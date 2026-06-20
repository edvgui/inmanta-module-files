"""
Copyright 2026 Guillaume Everarts de Velp

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Contact: edvgui@gmail.com
"""

import logging
import pathlib

import pytest
from pytest_inmanta.plugin import Project

import inmanta.plugins
from inmanta.agent.handler import PythonLogger

LOGGER = logging.getLogger()


def test_serialization(project: Project) -> None:
    from inmanta_plugins.std import create_environment_reference

    from inmanta_plugins.files import JinjaReference

    # Create a basic jinja ref
    env_a = create_environment_reference("A")
    tmpl_a = JinjaReference(template="{{a}}", references={"a": env_a})

    # Validate its serialized arguments
    assert tmpl_a.arguments == {
        "template": "{{a}}",
        "references": {"a": env_a},
    }

    # Create another identical reference, which is another object, but
    # should get the same name
    tmpl_aa = JinjaReference(template="{{a}}", references={"a": env_a})
    assert tmpl_a is not tmpl_aa
    assert tmpl_a == tmpl_aa


def test_basics(
    project: Project, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inmanta_plugins.std import EnvironmentReference

    from inmanta_plugins.files import JinjaReference

    # A reference emitted as a terminal value in a template is automatically
    # registered in the jinja reference context.
    template = """ENV={{ "TEST" | std.create_environment_reference() }}"""
    template_path = tmp_path / "test.j2"
    template_path.write_text(template)

    # Registering the reference explicitly produces the same result.
    other_template = (
        """ENV={{ "TEST" | std.create_environment_reference()"""
        """ | files.register_reference() }}"""
    )
    other_template_path = tmp_path / "test_2.j2"
    other_template_path.write_text(other_template)

    project.compile(
        f"""
        import std
        import files
        import files::host
        import mitogen

        host = std::Host(
            name="localhost",
            os=std::linux,
            via=mitogen::Local(),
        )

        files::TextFile(
            path="/a",
            content=files::jinja("file://{template_path}"),
            host=host,
        )

        files::TextFile(
            path="/b",
            content=files::jinja("file://{other_template_path}"),
            host=host,
        )
        """,
        no_dedent=False,
    )

    files = project.get_instances("files::TextFile")

    for path in ("/a", "/b"):
        file = [f for f in files if f.path == path].pop()
        file = inmanta.plugins.allow_reference_values(file)
        assert isinstance(file.content, JinjaReference)
        assert (
            file.content.template
            == 'ENV={{ references["3ad25aea-bd44-30d8-8da3-f4c5e58e3d1e"] }}'
        )
        assert file.content.references == {
            "3ad25aea-bd44-30d8-8da3-f4c5e58e3d1e": EnvironmentReference("TEST")
        }

        with monkeypatch.context() as ctx:
            ctx.setenv("TEST", "b")
            assert file.content.resolve(PythonLogger(LOGGER)) == "ENV=b"


def test_no_reference(project: Project, tmp_path: pathlib.Path) -> None:
    """
    When a template doesn't emit any reference, the jinja plugin resolves it
    directly and returns a plain string instead of a JinjaReference.
    """
    template = """Hello {{ name }}!"""
    template_path = tmp_path / "test.j2"
    template_path.write_text(template)

    project.compile(
        f"""
        import std
        import files
        import files::host
        import mitogen

        host = std::Host(
            name="localhost",
            os=std::linux,
            via=mitogen::Local(),
        )

        files::TextFile(
            path="/a",
            content=files::jinja("file://{template_path}", name="world"),
            host=host,
        )
        """,
        no_dedent=False,
    )

    file = project.get_instances("files::TextFile").pop()
    assert file.content == "Hello world!"


def test_one_pass_unset_discovery(project: Project, tmp_path: pathlib.Path) -> None:
    """
    A template that reads several model values which are not frozen yet is
    rendered once in "discovery" mode: all the unset values are collected in a
    single pass and waited for at once, instead of rescheduling the whole render
    once per miss.
    """
    import jinja2

    import inmanta_plugins.files as files_plugin

    template_dir = pathlib.Path(project._test_project_dir, "templates")
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / "multi.j2").write_text(
        "{{ config.a }}-{{ config.b }}-{{ config.c }}"
    )

    # Count full template renders, and record how many misses the collector held
    # at each collection during a discovery pass.
    render_count = 0
    original_render = jinja2.Template.render

    def counting_render(self: jinja2.Template, *args: object, **kwargs: object) -> str:
        nonlocal render_count
        render_count += 1
        return original_render(self, *args, **kwargs)

    pass_sizes: list[int] = []
    original_collect = files_plugin.collect_or_raise

    def spy_collect(exc: object) -> object:
        result = original_collect(exc)
        collector = files_plugin.JINJA_UNSET_COLLECTOR.get()
        if collector is not None:
            pass_sizes.append(len(collector))
        return result

    jinja2.Template.render = counting_render
    files_plugin.collect_or_raise = spy_collect
    try:
        project.compile(
            """
            import files
            import files::host
            import mitogen
            import std

            host = std::Host(
                name="localhost",
                os=std::linux,
                via=mitogen::Local(),
            )

            # The three attributes are only assigned in the implementation, so
            # they are all unset when the jinja plugin is first evaluated.
            entity Config:
                string a
                string b
                string c
            end
            implement Config using compute

            implementation compute for Config:
                self.a = "AAA"
                self.b = "BBB"
                self.c = "CCC"
            end

            config = Config()

            files::TextFile(
                path="/a",
                content=files::jinja("template:///multi.j2", config=config),
                host=host,
            )
            """,
            no_dedent=False,
        )
    finally:
        jinja2.Template.render = original_render
        files_plugin.collect_or_raise = original_collect

    # The template was rendered correctly, with all the deferred values.
    file = project.get_instances("files::TextFile").pop()
    assert file.content == "AAA-BBB-CCC"

    # A single discovery pass collected all three misses at once: the old
    # per-miss behaviour could never hold more than one miss in a single render.
    assert max(pass_sizes) == 3
    # One discovery render that collected the batch, then one clean render.
    assert render_count == 2

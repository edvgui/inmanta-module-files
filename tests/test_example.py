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

import pathlib

from pytest_inmanta.plugin import Project


def update_example(name: str, model: str) -> None:
    """
    Find the example with the given name in the readme, and make sure
    the model is the described one.
    """
    readme_file = pathlib.Path(__file__).parent.parent / "README.md"
    readme = readme_file.read_text()

    marker_start = f"<x-example-{name}>"
    start = readme.find(marker_start)
    if start == -1:
        raise RuntimeError(
            f"Can not find marker {marker_start} in readme {readme_file}"
        )

    marker_end = f"</x-example-{name}>"
    end = readme.find(marker_end, start)
    if end == -1:
        raise RuntimeError(f"Can not find marker {marker_end} in readme {readme_file}")

    current_model = readme[start : end + len(marker_end)]
    desired_model = marker_start + "\n\n```\n" + model + "\n```\n\n" + marker_end

    if current_model != desired_model:
        readme_file.write_text(
            readme[:start] + desired_model + readme[end + len(marker_end) :]
        )


def test_simple(
    project: Project,
    tmp_path: pathlib.Path,
) -> None:

    model = f"""
        import mitogen
        import files

        import std

        host = std::Host(
            name="localhost",
            os=std::linux,
            via=mitogen::Local(),
        )

        dir = files::Directory(
            host=host,
            path="{tmp_path}/a",
            # The directory that is managed is /tmp/test/a, but the resource
            # will also make sure that any of its parent directories exists as well
            create_parents=true,
        )

        file = files::TextFile(
            host=host,
            path=f"{{dir.path}}/file.txt",
            content="test",
            # No need to explicitly add the dependency to the parent, the
            # exporter takes care of adding it
            # requires=dir,
        )
    """

    project.compile(model, no_dedent=False)

    # Verify that the requirements are wired automatically
    dir = project.get_resource("files::Directory")
    assert dir is not None
    file = project.get_resource("files::TextFile")
    assert file is not None
    assert dir.id in file.requires

    tested_model = pathlib.Path(project._test_project_dir, "main.cf").read_text()
    tested_model = tested_model.replace(str(tmp_path), "/example/folder")
    update_example("simple", tested_model)

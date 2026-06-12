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

import grp
import os
import pathlib

from pytest_inmanta.plugin import Project


def write_source_file(project: Project, name: str, content: str) -> str:
    """
    Write a file in the files directory of the test project, and return the
    uri at which it can be referenced in the model.

    :param project: The project fixture, in which the file is created.
    :param name: The name of the file to create.
    :param content: The content of the file.
    """
    source_file = pathlib.Path(project._test_project_dir, "files", name)
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(content)
    return f"inmanta:///files/{name}"


def build_model(files: dict[str, str]) -> str:
    """
    Build a model deploying the given text files.

    :param files: A dict mapping the path of each file to deploy to the
        expression to use as its content.
    """
    user = os.getlogin()
    group = grp.getgrgid(os.getgid()).gr_name
    file_blocks = "\n".join(f"""
        files::TextFile(
            host=host,
            path={path!r},
            owner={user!r},
            group={group!r},
            content={content},
        )
        """ for path, content in files.items())
    return f"""
        import mitogen
        import files
        import std

        host = std::Host(
            name="localhost",
            os=std::linux,
            via=mitogen::Local(),
        )
        {file_blocks}
    """


def test_compile_without_server(project: Project, tmp_path: pathlib.Path) -> None:
    """
    A compile serializing file and text references must succeed without any
    server interaction: there is no server fixture in this test, any api call
    would fail.
    """
    source_uri = write_source_file(project, "source.txt", "test content")

    project.compile(
        build_model(
            {
                str(
                    tmp_path / "a.txt"
                ): f"files::create_text_file_content_reference({source_uri!r})",
                str(tmp_path / "b.txt"): 'files::create_text_reference("test content")',
            }
        ),
        no_dedent=False,
    )


def test_upload_deduplication(project: Project, tmp_path: pathlib.Path) -> None:
    """
    A file referenced by multiple references is registered for upload only once.
    """
    source_uri = write_source_file(project, "source.txt", "shared content")
    ref = f"files::create_text_file_content_reference({source_uri!r})"

    project.compile(
        build_model(
            {
                str(tmp_path / "a.txt"): ref,
                str(tmp_path / "b.txt"): ref,
            }
        ),
        no_dedent=False,
    )

    assert project._exporter is not None
    assert list(project._exporter._file_store.values()) == [b"shared content"]


def test_content_frozen_at_serialization(
    project: Project, tmp_path: pathlib.Path
) -> None:
    """
    Once a reference to a file has been serialized, the content associated to
    its hash is frozen: modifying the file afterwards doesn't change what is
    uploaded or deployed.
    """
    source_uri = write_source_file(project, "source.txt", "original content")

    target_file = tmp_path / "target.txt"
    project.compile(
        build_model(
            {
                str(
                    target_file
                ): f"files::create_text_file_content_reference({source_uri!r})",
            }
        ),
        no_dedent=False,
    )

    # Modify the source file after its reference has been serialized
    write_source_file(project, "source.txt", "modified content")

    assert project._exporter is not None
    assert list(project._exporter._file_store.values()) == [b"original content"]

    # The deployed file should hold the content snapshotted at serialization time
    project.deploy_resource("files::TextFile")
    assert target_file.read_text() == "original content"


def test_state_reset_between_compiles(project: Project, tmp_path: pathlib.Path) -> None:
    """
    The global snapshot collector is reset between two compiles in the same
    process: the second compile only sees its own files.
    """
    import inmanta_plugins.files.upload as upload

    first_uri = write_source_file(project, "first.txt", "first content")
    project.compile(
        build_model(
            {
                str(
                    tmp_path / "a.txt"
                ): f"files::create_text_file_content_reference({first_uri!r})",
            }
        ),
        no_dedent=False,
    )
    assert list(upload._snapshots.values()) == [b"first content"]

    second_uri = write_source_file(project, "second.txt", "second content")
    project.compile(
        build_model(
            {
                str(
                    tmp_path / "b.txt"
                ): f"files::create_text_file_content_reference({second_uri!r})",
            }
        ),
        no_dedent=False,
    )
    assert list(upload._snapshots.values()) == [b"second content"]


def test_serialized_value_is_content_based(
    project: Project, tmp_path: pathlib.Path
) -> None:
    """
    The serialized value of a reference depends on the content of the file,
    not on its path: identical content at different paths serializes to the
    same value.
    """
    import inmanta_plugins.files.upload as upload
    from inmanta_plugins.files import TextFileContentReference, TextReference

    file_a = tmp_path / "a" / "file.txt"
    file_b = tmp_path / "b" / "file.txt"
    file_a.parent.mkdir(parents=True)
    file_b.parent.mkdir(parents=True)
    file_a.write_text("identical content")
    file_b.write_text("identical content")

    try:
        ref_a = TextFileContentReference(str(file_a), None)
        ref_b = TextFileContentReference(str(file_b), None)
        assert ref_a.serialize() == ref_b.serialize()
        assert ref_a.serialize().id == ref_b.serialize().id

        text_a = TextReference("identical content", None)
        text_b = TextReference("identical content", None)
        assert text_a.serialize() == text_b.serialize()
        assert text_a.serialize().id == text_b.serialize().id
    finally:
        upload.reset()

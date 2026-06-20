"""
Copyright 2023 Guillaume Everarts de Velp

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

import asyncio
import functools
import grp
import os
import pathlib
import typing

from pytest_inmanta.plugin import Project

from inmanta.server.protocol import Server


async def off_main_thread[T](func: typing.Callable[[], T]) -> T:
    return await asyncio.get_event_loop().run_in_executor(None, func)


def test_text_reference_equality(project: Project) -> None:
    """
    Two text references point to the same content as soon as either their text
    or their hash matches.  This must hold even when some of the instances have
    already been serialized -- e.g. by the jinja plugin, which serializes the
    references it registers in its context.  Serializing a reference populates
    its hash without dropping its text, so a freshly created reference (text
    only), a serialized one (text + hash) and a hash-only one (e.g. coming from
    a deserialized resource) must all compare equal when they describe the same
    content.
    """
    from inmanta_plugins.files import TextReference

    # Fresh reference, only the text is set.
    fresh = TextReference("hello", None)

    # Same content, but serialized already: its hash is now populated while the
    # text is kept around.  This mimics a reference the jinja plugin registered.
    serialized = TextReference("hello", None)
    serialized.serialize_arguments()
    assert serialized.text == "hello"
    assert serialized.hash is not None

    # Hash-only reference, as if it came back from a serialized resource.
    hash_only = TextReference(None, serialized.hash)

    # The fresh and serialized instances match on their text.
    assert fresh == serialized
    # The serialized and hash-only instances match on their hash.
    assert serialized == hash_only
    # Equality is symmetric.
    assert serialized == fresh
    assert hash_only == serialized

    # A reference to a different content is never equal, regardless of how it
    # was created.
    other = TextReference("world", None)
    other_serialized = TextReference("world", None)
    other_serialized.serialize_arguments()
    assert fresh != other
    assert serialized != other_serialized
    assert hash_only != TextReference(None, other_serialized.hash)


def test_text_file_content_reference_equality(
    project: Project, tmp_path: pathlib.Path
) -> None:
    """
    Same as test_text_reference_equality, but for references to the content of
    a file: instances match on either their file path or their content hash,
    mixing freshly created, already-serialized and hash-only instances.
    """
    from inmanta_plugins.files import TextFileContentReference

    source = tmp_path / "content.txt"
    source.write_text("hello")

    # Fresh reference, only the file path is set.
    fresh = TextFileContentReference(str(source), None)

    # Same file, but serialized already: the content hash is populated while the
    # file path is kept around.
    serialized = TextFileContentReference(str(source), None)
    serialized.serialize_arguments()
    assert serialized.file_path == str(source)
    assert serialized.file_hash is not None

    # Hash-only reference, as if it came back from a serialized resource.
    hash_only = TextFileContentReference(None, serialized.file_hash)

    # The fresh and serialized instances match on their file path.
    assert fresh == serialized
    # The serialized and hash-only instances match on their hash.
    assert serialized == hash_only
    # Equality is symmetric.
    assert serialized == fresh
    assert hash_only == serialized

    # A reference to a file with a different content is never equal.
    other_source = tmp_path / "other.txt"
    other_source.write_text("world")
    other = TextFileContentReference(str(other_source), None)
    other_serialized = TextFileContentReference(str(other_source), None)
    other_serialized.serialize_arguments()
    assert fresh != other
    assert serialized != other_serialized
    assert hash_only != TextFileContentReference(None, other_serialized.file_hash)


def test_text_model(
    project: Project,
    dir_path: pathlib.Path = pathlib.Path("/tmp/example"),
    purged: bool = False,
    content: str = "test",
) -> None:
    user = os.getlogin()
    group = grp.getgrgid(os.getgid()).gr_name
    model = f"""
        import mitogen
        import files
        import files::host

        import std

        host = std::Host(
            name="localhost",
            os=std::linux,
            via=mitogen::Local(),
        )

        files::TextFile(
            host=host,
            path={repr(str(dir_path))},
            owner={repr(user)},
            group={repr(group)},
            purged={str(purged).lower()},
            content=files::create_text_reference({repr(content)}),
        )
    """

    project.compile(model.strip("\n"), no_dedent=False)


def test_text_file_content_model(
    project: Project,
    dir_path: pathlib.Path = pathlib.Path("/tmp/example"),
    purged: bool = False,
    content: str = "test",
) -> None:
    source_file = pathlib.Path(project._test_project_dir, "files/test.txt")
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(content)

    user = os.getlogin()
    group = grp.getgrgid(os.getgid()).gr_name
    model = f"""
        import mitogen
        import files
        import files::host

        import std

        host = std::Host(
            name="localhost",
            os=std::linux,
            via=mitogen::Local(),
        )

        files::TextFile(
            host=host,
            path={repr(str(dir_path))},
            owner={repr(user)},
            group={repr(group)},
            purged={str(purged).lower()},
            content=files::create_text_file_content_reference("inmanta:///files/test.txt"),
        )
    """

    project.compile(model.strip("\n"), no_dedent=False)


async def test_deploy(
    project: Project,
    tmp_path: pathlib.Path,
    server: Server,
) -> None:
    file = tmp_path / "test"

    def test():
        # Create the dir
        assert project.dryrun_resource("files::TextFile")
        project.deploy_resource("files::TextFile")
        assert file.is_file()
        assert file.read_text() == "test"
        assert not project.dryrun_resource("files::TextFile")

    await off_main_thread(
        functools.partial(test_text_model, project, file, purged=False, content="test")
    )
    await off_main_thread(test)

    file.unlink()

    await off_main_thread(
        functools.partial(
            test_text_file_content_model, project, file, purged=False, content="test"
        )
    )
    await off_main_thread(test)

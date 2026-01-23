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


async def test_text_model(
    project: Project,
    server: Server,
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

    await off_main_thread(
        functools.partial(project.compile, model.strip("\n"), no_dedent=False)
    )


async def test_text_file_content_model(
    project: Project,
    server: Server,
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

    await off_main_thread(
        functools.partial(project.compile, model.strip("\n"), no_dedent=False)
    )


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

    await test_text_model(project, server, file, purged=False, content="test")
    await off_main_thread(test)

    file.unlink()

    await test_text_file_content_model(
        project, server, file, purged=False, content="test"
    )
    await off_main_thread(test)

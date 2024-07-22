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

import grp
import os
import pathlib

import pytest_inmanta.plugin


def test_model(
    project: pytest_inmanta.plugin.Project,
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
            content={repr(content)},
        )
    """

    project.compile(model.strip("\n"), no_dedent=False)


def test_deploy(project: pytest_inmanta.plugin.Project, tmp_path: pathlib.Path) -> None:
    file = tmp_path / "test"

    # Create the dir
    test_model(project, file, purged=False, content="test")
    assert project.dryrun_resource("files::TextFile")
    project.deploy_resource("files::TextFile")
    assert file.is_file()
    assert file.read_text() == "test"
    assert not project.dryrun_resource("files::TextFile")

    # Update the file
    test_model(project, file, purged=False, content="testtest")
    assert project.dryrun_resource("files::TextFile")
    project.deploy_resource("files::TextFile")
    assert file.is_file()
    assert file.read_text() == "testtest"
    assert not project.dryrun_resource("files::TextFile")

    # Delete the file
    test_model(project, file, purged=True)
    assert project.dryrun_resource("files::TextFile")
    project.deploy_resource("files::TextFile")
    assert not file.exists()
    assert not project.dryrun_resource("files::TextFile")

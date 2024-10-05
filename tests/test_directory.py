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
import shutil

import pytest_inmanta.plugin


def test_model(
    project: pytest_inmanta.plugin.Project,
    dir_path: pathlib.Path = pathlib.Path("/tmp/example"),
    create_parents: bool = False,
    purged: bool = False,
) -> None:
    user = os.getlogin()
    group = grp.getgrgid(os.getgid()).gr_name
    model = f"""
        import mitogen
        import files

        import std

        host = std::Host(
            name="localhost",
            os=std::linux,
            via=mitogen::Local(),
        )

        files::Directory(
            host=host,
            path={repr(str(dir_path))},
            owner={repr(user)},
            group={repr(group)},
            purged={str(purged).lower()},
            create_parents={str(create_parents).lower()},
        )
    """

    project.compile(model.strip("\n"), no_dedent=False)


def test_deploy(project: pytest_inmanta.plugin.Project, tmp_path: pathlib.Path) -> None:
    dir = tmp_path / "dir"

    # Create the dir
    test_model(project, dir, purged=False, create_parents=False)
    assert project.dryrun_resource("files::Directory")
    project.deploy_resource("files::Directory")
    assert dir.is_dir()
    assert not project.dryrun_resource("files::Directory")

    # Create the dir (recursively)
    test_model(project, dir / "in/dir", purged=False, create_parents=True)
    assert not (dir / "in").exists()
    assert project.dryrun_resource("files::Directory")
    project.deploy_resource("files::Directory")
    assert (dir / "in/dir").is_dir()
    assert (dir / "in").owner() == dir.owner()
    assert (dir / "in").group() == dir.group()
    assert (dir / "in").stat().st_mode == dir.stat().st_mode
    assert not project.dryrun_resource("files::Directory")

    # Create another directory recursively in a folder that is owned
    # by root, but running the test as a non-root user
    other_tmp_path = pathlib.Path("/tmp/test")
    if other_tmp_path.exists():
        shutil.rmtree(str(other_tmp_path))

    test_model(project, other_tmp_path / "a", create_parents=True)
    project.deploy_resource("files::Directory")

    # Delete the dir
    test_model(project, dir, purged=True)
    assert project.dryrun_resource("files::Directory")
    project.deploy_resource("files::Directory")
    assert not dir.exists()
    assert not project.dryrun_resource("files::Directory")

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
    link_path: pathlib.Path = pathlib.Path("/tmp/example"),
    target: pathlib.Path = pathlib.Path("/tmp"),
    purged: bool = False,
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

        files::Symlink(
            host=host,
            path={repr(str(link_path))},
            target={repr(str(target))},
            owner={repr(user)},
            group={repr(group)},
            purged={str(purged).lower()},
        )
    """

    project.compile(model.strip("\n"), no_dedent=False)


def test_deploy(project: pytest_inmanta.plugin.Project, tmp_path: pathlib.Path) -> None:
    link = tmp_path / "link"
    target = tmp_path / "target"
    target.touch()

    # Create the link
    test_model(project, link, purged=False, target=target)
    assert project.dryrun_resource("files::Symlink")
    project.deploy_resource("files::Symlink")
    assert link.is_symlink()
    assert link.resolve() == target
    assert not project.dryrun_resource("files::Symlink")

    # Delete the symlink target and make sure the resource read doesn't fail
    target.unlink()
    assert not project.dryrun_resource("files::Symlink")

    # Delete the link
    test_model(project, link, purged=True)
    assert project.dryrun_resource("files::Symlink")
    project.deploy_resource("files::Symlink")
    assert not link.exists()
    assert not project.dryrun_resource("files::Symlink")

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

import pytest
import pytest_inmanta.plugin


@pytest.mark.parametrize(
    (
        "file_path",
        "purged",
    ),
    [
        (pathlib.Path("/tmp/example"), False),
    ],
)
def test_model(
    project: pytest_inmanta.plugin.Project, file_path: pathlib.Path, purged: bool
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

        files::SharedHostFile(
            host=host,
            path={repr(str(file_path))},
            owner={repr(user)},
            group={repr(group)},
            purged={str(purged).lower()},
            entries=[
                files::host::Entry(
                    hostname="example.com",
                    address4="192.168.10.10",
                    operation="replace",
                ),
            ],
        )

        files::SharedHostFile(
            host=host,
            path={repr(str(file_path))},
            resource_discriminator="example.be",
            purged={str(purged).lower()},
            entries=[
                files::host::Entry(
                    hostname="example.be",
                    operation="remove",
                ),
            ],
        )

        files::SharedHostFile(
            host=host,
            path={repr(str(file_path))},
            resource_discriminator="example.eu",
            purged={str(purged).lower()},
            entries=[
                files::host::Entry(
                    hostname="example.eu",
                    address4="192.168.10.10",
                    operation="merge",
                ),
            ],
        )
    """

    project.compile(model.strip("\n"), no_dedent=False)


def test_deploy(project: pytest_inmanta.plugin.Project, tmp_path: pathlib.Path) -> None:
    file = tmp_path / "host"

    # Create the file
    test_model(project, file, purged=False)
    assert project.dryrun_resource("files::SharedHostFile", uri=str(file))
    assert project.dryrun_resource("files::SharedHostFile", uri=f"{file}:example.be")
    assert project.dryrun_resource("files::SharedHostFile", uri=f"{file}:example.eu")
    project.deploy_resource("files::SharedHostFile", uri=str(file))
    project.deploy_resource("files::SharedHostFile", uri=f"{file}:example.be")
    project.deploy_resource("files::SharedHostFile", uri=f"{file}:example.eu")
    assert not project.dryrun_resource("files::SharedHostFile", uri=str(file))
    assert not project.dryrun_resource(
        "files::SharedHostFile", uri=f"{file}:example.be"
    )
    assert not project.dryrun_resource(
        "files::SharedHostFile", uri=f"{file}:example.eu"
    )

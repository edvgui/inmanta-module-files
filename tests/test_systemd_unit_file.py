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

EXAMPLE_UNIT = """
[Unit]
Description=Podman inmanta-orchestrator-net.service
Documentation=https://github.com/edvgui/inmanta-module-podman
RequiresMountsFor=%t/containers
Wants=network-online.target
Wants=inmanta-orchestrator-db.service
Wants=inmanta-orchestrator-server.service
Before=inmanta-orchestrator-db.service
Before=inmanta-orchestrator-server.service
After=network-online.target

[Service]
Environment="TEST=a\\na"
Restart=on-failure
TimeoutStopSec=70
ExecStartPre=/usr/bin/podman network create --ignore --subnet=172.42.0.0/24 inmanta-orchestrator-net
ExecStart=/usr/bin/bash -c "/usr/bin/sleep infinity & /usr/bin/podman network inspect inmanta-orchestrator-net"
ExecStopPost=/usr/bin/podman network rm -f inmanta-orchestrator-net
Type=forking

[Install]
WantedBy=default.target

""".lstrip(
    "\n"
)


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
        import files::systemd_unit

        import std

        host = std::Host(
            name="localhost",
            os=std::linux,
            via=mitogen::Local(),
        )

        files::SystemdUnitFile(
            host=host,
            path={repr(str(file_path))},
            owner={repr(user)},
            group={repr(group)},
            purged={str(purged).lower()},
            unit=Unit(
                description="Podman inmanta-orchestrator-net.service",
                documentation=["https://github.com/edvgui/inmanta-module-podman"],
                requires_mounts_for=["%t/containers"],
                wants=[
                    "network-online.target",
                    "inmanta-orchestrator-db.service",
                    "inmanta-orchestrator-server.service",
                ],
                before=[
                    "inmanta-orchestrator-db.service",
                    "inmanta-orchestrator-server.service",
                ],
                after=["network-online.target"],
            ),
            service=Service(
                environment={{\"TEST\": \"\"\"a
a\"\"\"}},
                restart="on-failure",
                timeout_stop_sec=70,
                exec_start_pre=["/usr/bin/podman network create --ignore --subnet=172.42.0.0/24 inmanta-orchestrator-net"],
                exec_start="/usr/bin/bash -c \\"/usr/bin/sleep infinity & /usr/bin/podman network inspect inmanta-orchestrator-net\\"",
                exec_stop_post=["/usr/bin/podman network rm -f inmanta-orchestrator-net"],
                type="forking",
            ),
            install=Install(
                wanted_by=["default.target"],
            ),
        )
    """  # noqa: E501

    project.compile(model.strip("\n"), no_dedent=False)


def test_deploy(project: pytest_inmanta.plugin.Project, tmp_path: pathlib.Path) -> None:
    file = tmp_path / "inmanta-orchestrator-net.service"

    # Create the file
    test_model(project, file, purged=False)
    assert project.dryrun_resource("files::SystemdUnitFile")
    project.deploy_resource("files::SystemdUnitFile")
    assert not project.dryrun_resource("files::SystemdUnitFile")

    # Check that the file content is the expected one
    assert file.read_text() == EXAMPLE_UNIT

    # Delete the file
    test_model(project, file, purged=True)
    assert project.dryrun_resource("files::SystemdUnitFile")
    project.deploy_resource("files::SystemdUnitFile")
    assert not project.dryrun_resource("files::SystemdUnitFile")
    assert not file.exists()

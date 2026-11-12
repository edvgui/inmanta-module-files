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
import logging
import os
import pathlib

import pytest
import pytest_inmanta.plugin

import inmanta.plugins
from inmanta.agent.handler import PythonLogger

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
SuccessExitStatus=143 SIGTERM

[Install]
WantedBy=default.target

""".lstrip("\n")


EXAMPLE_MOUNT_UNIT = """
[Unit]
Description=TrueNAS Immich NFS Mount
Wants=network-online.target
After=network-online.target

[Mount]
What=NAS_IP:/mnt/{POOL}/{DATASET}
Where=/mnt/some/folder
Type=nfs
Options=rw,_netdev,noatime,vers=4.2

[Install]
WantedBy=multi-user.target

""".lstrip("\n")


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
                success_exit_status=["143", "SIGTERM"],
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


@pytest.mark.parametrize(
    (
        "file_path",
        "purged",
    ),
    [
        (pathlib.Path("/tmp/example.mount"), False),
    ],
)
def test_mount_model(
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
                description="TrueNAS Immich NFS Mount",
                wants=["network-online.target"],
                after=["network-online.target"],
            ),
            mount=Mount(
                what="NAS_IP:/mnt/{{POOL}}/{{DATASET}}",
                where="/mnt/some/folder",
                type="nfs",
                options="rw,_netdev,noatime,vers=4.2",
            ),
            install=Install(
                wanted_by=["multi-user.target"],
            ),
        )
    """  # noqa: E501

    project.compile(model.strip("\n"), no_dedent=False)


def test_mount_deploy(
    project: pytest_inmanta.plugin.Project, tmp_path: pathlib.Path
) -> None:
    file = tmp_path / "mnt-some-folder.mount"

    # Create the file
    test_mount_model(project, file, purged=False)
    assert project.dryrun_resource("files::SystemdUnitFile")
    project.deploy_resource("files::SystemdUnitFile")
    assert not project.dryrun_resource("files::SystemdUnitFile")

    # Check that the file content is the expected one
    assert file.read_text() == EXAMPLE_MOUNT_UNIT

    # Delete the file
    test_mount_model(project, file, purged=True)
    assert project.dryrun_resource("files::SystemdUnitFile")
    project.deploy_resource("files::SystemdUnitFile")
    assert not project.dryrun_resource("files::SystemdUnitFile")
    assert not file.exists()


def render(project: pytest_inmanta.plugin.Project, attributes: str) -> str:
    """
    Compile a model with a single unit file, with the given attributes, and
    return the content of the unit file.
    """
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
            path="/tmp/example.service",
            owner="root",
            group="root",
            {attributes}
        )
    """
    project.compile(model.strip("\n"), no_dedent=False)
    return project.get_resource("files::SystemdUnitFile").content


@pytest.mark.parametrize(
    ("attributes", "error"),
    [
        (
            'unit=Unit(description="a\\nExecStartPre=/bin/false")',
            "Value of Description= can not contain a newline",
        ),
        (
            'service=Service(pid_file="/run/a.pid\\r")',
            "Value of PIDFile= can not contain a newline",
        ),
        (
            'unit=Unit(description="a\\\\", after=["b.target"])',
            "Value of Description= can not end with a backslash",
        ),
        (
            'unit=Unit(requires_mounts_for=["/mnt/a\\n"])',
            "Value of RequiresMountsFor= can not contain a newline",
        ),
        (
            'service=Service(extra={"User": ["a", "b\\n"]})',
            "Value of User= can not contain a newline",
        ),
        (
            'service=Service(extra={"User\\nGroup": "a"})',
            "Invalid systemd directive name",
        ),
        (
            'service=Service(environment={"A B": "c"})',
            "Invalid environment variable name",
        ),
    ],
)
def test_invalid_values(
    project: pytest_inmanta.plugin.Project, attributes: str, error: str
) -> None:
    with pytest.raises(Exception) as exc_info:
        render(project, attributes)

    # The error raised by the plugin is wrapped by the jinja plugin exception
    causes = []
    exc: BaseException | None = exc_info.value
    while exc is not None:
        causes.append(str(exc))
        exc = exc.__cause__
    assert any(error in cause for cause in causes), causes


def test_escaping(project: pytest_inmanta.plugin.Project) -> None:
    content = render(
        project,
        """
            unit=Unit(
                description=files::systemd_unit::escape_specifiers("100% %n"),
                requires_mounts_for=["%t/containers", "/mnt/my disk", "/mnt/a\\\\b"],
            ),
            service=Service(
                environment={"A": "%n", "B": 1, "C": true, "D": 'a"b'},
                exec_start="/bin/true",
            ),
        """,
    )
    assert content == """
[Unit]
Description=100%% %%n
RequiresMountsFor=%t/containers "/mnt/my disk" "/mnt/a\\\\b"

[Service]
Environment="A=%n"
Environment="B=1"
Environment="C=true"
Environment="D=a\\"b"
ExecStart=/bin/true

""".lstrip("\n")


def test_extra(project: pytest_inmanta.plugin.Project) -> None:
    content = render(
        project,
        """
            unit=Unit(
                conflicts=["a.service"],
                start_limit_interval_sec=10,
                start_limit_burst=3,
                extra={"Wants": ["b.target", "c.target"], "DefaultDependencies": false},
            ),
            service=Service(
                user="nobody",
                group="nobody",
                working_directory="/tmp",
                exec_start="/bin/true",
                exec_reload=["/bin/kill -HUP $MAINPID"],
                type="oneshot",
                remain_after_exit=true,
                kill_mode="mixed",
                extra={"LimitNOFILE": 65536, "Nice": null},
            ),
            timer=Timer(
                on_boot_sec="5min",
                on_unit_active_sec="1h",
                persistent=true,
                extra={"WakeSystem": true},
            ),
            socket=Socket(
                listen_datagram=["127.0.0.1:53"],
                accept=false,
                socket_mode="0600",
                extra={"Backlog": 10},
            ),
            mount=Mount(what="/dev/sda1", where="/mnt", extra={"Options": "ro"}),
            install=Install(wanted_by=["multi-user.target"], extra={"Also": "a.socket"}),
        """,
    )
    assert content == """
[Unit]
Conflicts=a.service
StartLimitIntervalSec=10
StartLimitBurst=3
Wants=b.target
Wants=c.target
DefaultDependencies=false

[Service]
User=nobody
Group=nobody
WorkingDirectory=/tmp
ExecStart=/bin/true
ExecReload=/bin/kill -HUP $MAINPID
Type=oneshot
RemainAfterExit=true
KillMode=mixed
LimitNOFILE=65536

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
Persistent=true
WakeSystem=true

[Socket]
ListenDatagram=127.0.0.1:53
Accept=false
SocketMode=0600
Backlog=10

[Mount]
What=/dev/sda1
Where=/mnt
Options=ro

[Install]
WantedBy=multi-user.target
Also=a.socket

""".lstrip("\n")


def test_references(
    project: pytest_inmanta.plugin.Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    render(
        project,
        """
            service=Service(
                exec_start=std::create_environment_reference("COMMAND"),
                exec_start_pre=[std::create_environment_reference("COMMAND")],
            ),
        """,
    )

    # References are not checked when compiling, they are resolved in the content
    unit_file = inmanta.plugins.allow_reference_values(
        project.get_instances("files::SystemdUnitFile")[0]
    )
    monkeypatch.setenv("COMMAND", "/bin/echo hi")
    assert unit_file.content.resolve(PythonLogger(logging.getLogger())) == """
[Service]
ExecStartPre=/bin/echo hi
ExecStart=/bin/echo hi

""".lstrip("\n")


def test_directive() -> None:
    from inmanta_plugins.files.systemd_unit import directive

    assert directive("After", None) == ""
    assert directive("After", []) == ""
    assert (
        directive("After", ["a.target", "b.target"])
        == "\nAfter=a.target\nAfter=b.target"
    )
    assert directive("TimeoutSec", 10) == "\nTimeoutSec=10"
    assert directive("Persistent", False) == "\nPersistent=false"
    assert (
        directive("Alias", ["a.service", "b.service"], joined=True)
        == "\nAlias=a.service b.service"
    )
    assert directive("Alias", [], joined=True) == "\nAlias="
    assert (
        directive(
            "RequiresMountsFor",
            ["%t/a", "/mnt/my disk", "/mnt/a\\"],
            joined=True,
            quoted=True,
        )
        == '\nRequiresMountsFor=%t/a "/mnt/my disk" "/mnt/a\\\\"'
    )

    with pytest.raises(Exception, match="Invalid systemd directive name"):
        directive("after", "a.target")

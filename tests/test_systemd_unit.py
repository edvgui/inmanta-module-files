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

import collections.abc
import getpass
import json
import pathlib
import subprocess
import typing
import uuid

import pytest
import pytest_inmanta.plugin

from inmanta import const
from inmanta.agent.handler import HandlerContext
from inmanta.resources import Resource
from inmanta_plugins.files.systemd import (
    SystemdDaemonReloadResource,
    SystemdUnitResource,
)
from inmanta_plugins.files.systemd_unit import SystemdUnitFileResource

UNIT = "files::SystemdUnit"
RELOAD = "files::SystemdDaemonReload"
FILE = "files::SystemdUnitFile"


def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True
    )


def show(name: str, property: str) -> str:
    return systemctl(
        "show", "--property", property, "--value", "--", name
    ).stdout.strip()


@pytest.fixture
def unit_name() -> collections.abc.Iterator[str]:
    """
    The name of a user unit, removed after the test.
    """
    if systemctl("is-system-running").returncode not in (0, 1):
        pytest.skip("No systemd user manager to test with")

    name = f"inmanta-files-test-{uuid.uuid4().hex[:8]}.service"
    path = pathlib.Path.home() / ".config/systemd/user" / name
    yield name
    systemctl("disable", "--now", "--", name)
    systemctl("reset-failed", "--", name)
    path.unlink(missing_ok=True)
    systemctl("daemon-reload")


def unit_model(
    name: str,
    *,
    exec_start: str = "/usr/bin/sleep infinity",
    service_type: str = "simple",
    file_purged: bool = False,
    unit_purged: bool = False,
    receive_events: bool = False,
) -> str:
    path = pathlib.Path.home() / ".config/systemd/user" / name
    return f"""
        import files
        import files::systemd_unit
        import mitogen
        import std

        host = std::Host(name="localhost", os=std::linux, via=mitogen::Local())

        files::Directory(
            host=host,
            path={json.dumps(str(path.parent))},
            permissions=null,
            create_parents=true,
        )

        files::SystemdUnitFile(
            host=host,
            path={json.dumps(str(path))},
            permissions=644,
            purged={json.dumps(file_purged)},
            service=files::systemd_unit::Service(
                exec_start={json.dumps(exec_start)},
                type={json.dumps(service_type)},
            ),
            install=files::systemd_unit::Install(wanted_by=["default.target"]),
        )

        files::SystemdUnit(
            host=host,
            user={json.dumps(getpass.getuser())},
            name={json.dumps(name)},
            enabled=true,
            active=true,
            purged={json.dumps(unit_purged)},
            receive_events={json.dumps(receive_events)},
        )
    """


def get_unit(project: pytest_inmanta.plugin.Project, unit: str) -> SystemdUnitResource:
    resource = project.get_resource(UNIT, unit=unit)
    assert resource is not None
    return typing.cast(SystemdUnitResource, resource)


def get_reload(
    project: pytest_inmanta.plugin.Project, manager: str
) -> SystemdDaemonReloadResource:
    resource = project.get_resource(RELOAD, manager=manager)
    assert resource is not None
    return typing.cast(SystemdDaemonReloadResource, resource)


def deploy_and_reload(
    project: pytest_inmanta.plugin.Project, resource: Resource
) -> HandlerContext:
    """
    Deploy the resource as if one of its dependencies changed: the agent then
    calls the reload of the handler after deploying it.
    """
    handler = project.get_handler(resource, False)
    ctx = HandlerContext(resource)
    try:
        handler.execute(ctx, resource)
        handler.do_reload(ctx, resource)
    finally:
        project.finalize_handler(handler)
    return ctx


def test_model(project: pytest_inmanta.plugin.Project) -> None:
    """
    All the units of a service manager share its reload, which happens after
    the unit files changed and before the units are started.  A purged unit is
    stopped before its file is removed.
    """
    project.compile("""
        import files
        import mitogen
        import std

        host = std::Host(name="localhost", os=std::linux, via=mitogen::Local())

        files::SystemdUnitFile(host=host, path="/etc/systemd/system/a.service")
        files::SystemdUnitFile(host=host, path="/etc/systemd/system/b.service", purged=true)
        files::SystemdUnitFile(host=host, path="/home/me/.config/systemd/user/a.service")

        files::SystemdUnit(host=host, name="a.service", active=true)
        files::SystemdUnit(host=host, name="b.service", purged=true)
        files::SystemdUnit(host=host, name="c.service", enabled=false)
        files::SystemdUnit(host=host, name="a.service", user="me", active=true)
    """)

    system_reload = get_reload(project, "system")
    user_reload = get_reload(project, "user/me")
    assert system_reload.systemctl_command == ["systemctl"]
    assert user_reload.systemctl_command == ["systemctl", "--user"]
    assert len([r for r in project.resources.values() if r.is_type(RELOAD)]) == 2

    files = {
        r.path: r
        for r in typing.cast(
            list[SystemdUnitFileResource],
            [r for r in project.resources.values() if r.is_type(FILE)],
        )
    }
    a_file = files["/etc/systemd/system/a.service"]
    b_file = files["/etc/systemd/system/b.service"]
    user_a_file = files["/home/me/.config/systemd/user/a.service"]

    # Every unit file of the host is loaded by the reloads
    for reload in [system_reload, user_reload]:
        assert {a_file.id, b_file.id, user_a_file.id} <= reload.requires

    a = get_unit(project, "system/a.service")
    b = get_unit(project, "system/b.service")
    c = get_unit(project, "system/c.service")
    user_a = get_unit(project, "user/me/a.service")

    # The units start once their manager is reloaded, and their file is written
    assert a.requires == {system_reload.id, a_file.id, user_a_file.id}
    assert c.requires == {system_reload.id}
    assert user_a.requires == {user_reload.id, a_file.id, user_a_file.id}
    assert (a.enabled, a.active) == (None, True)
    assert (c.enabled, c.active) == (False, None)

    # The purged unit is stopped and disabled before its file is removed
    assert b.requires == set()
    assert b.id in b_file.requires
    assert (b.enabled, b.active) == (False, False)


def test_deploy(project: pytest_inmanta.plugin.Project, unit_name: str) -> None:
    """
    The unit is skipped until it exists, then enabled and started, restarted
    on changes when it receives events, and stopped and disabled once purged.
    """
    project.compile(unit_model(unit_name))

    # The unit file doesn't exist yet
    project.deploy_resource(UNIT, status=const.ResourceState.skipped)

    project.deploy_resource("files::Directory")
    project.deploy_resource(FILE)
    reload = project.get_resource(RELOAD)
    assert reload is not None
    assert deploy_and_reload(project, reload).status == const.ResourceState.deployed

    # The unit is now known, it gets enabled and started
    project.deploy_resource(UNIT, change=const.Change.updated)
    assert show(unit_name, "ActiveState") == "active"
    assert show(unit_name, "UnitFileState") == "enabled"
    project.deploy_resource(UNIT, change=const.Change.nochange)

    # A unit that doesn't receive events is not restarted when a dependency changed
    pid = show(unit_name, "MainPID")
    unit = project.get_resource(UNIT)
    assert unit is not None
    ctx = deploy_and_reload(project, unit)
    assert ctx.status == const.ResourceState.deployed
    assert ctx.change == const.Change.nochange
    assert show(unit_name, "MainPID") == pid

    # A unit that receives events is restarted
    project.compile(unit_model(unit_name, receive_events=True))
    unit = project.get_resource(UNIT)
    assert unit is not None
    ctx = deploy_and_reload(project, unit)
    assert ctx.status == const.ResourceState.deployed
    assert ctx.change == const.Change.updated
    assert show(unit_name, "ActiveState") == "active"
    assert show(unit_name, "MainPID") != pid

    # The purged unit is stopped and disabled, its file is left untouched
    project.compile(unit_model(unit_name, unit_purged=True))
    project.deploy_resource(UNIT, change=const.Change.purged)
    assert show(unit_name, "ActiveState") == "inactive"
    assert show(unit_name, "UnitFileState") == "disabled"
    project.deploy_resource(UNIT, change=const.Change.nochange)

    # Once its file is removed and the manager reloaded, it is still purged
    project.compile(unit_model(unit_name, unit_purged=True, file_purged=True))
    project.deploy_resource(FILE, change=const.Change.purged)
    reload = project.get_resource(RELOAD)
    assert reload is not None
    assert deploy_and_reload(project, reload).status == const.ResourceState.deployed
    assert show(unit_name, "LoadState") == "not-found"
    project.deploy_resource(UNIT, change=const.Change.nochange)


def test_deploy_failure(project: pytest_inmanta.plugin.Project, unit_name: str) -> None:
    """
    When the unit fails to start, the error contains what it logged.
    """
    marker = uuid.uuid4().hex
    project.compile(
        unit_model(
            unit_name,
            exec_start=f"/usr/bin/sh -c 'echo {marker}; exit 3'",
            service_type="oneshot",
        )
    )
    project.deploy_resource("files::Directory")
    project.deploy_resource(FILE)
    reload = project.get_resource(RELOAD)
    assert reload is not None
    assert deploy_and_reload(project, reload).status == const.ResourceState.deployed

    project.deploy_resource(UNIT, status=const.ResourceState.failed)
    assert project.ctx is not None
    errors = "\n".join(str(log._data["kwargs"]) for log in project.ctx.logs)
    assert f"Journal of {unit_name}" in errors
    assert marker in errors

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
    restart_on_change: bool = False,
    enabled: bool = True,
    install: bool = True,
    config_file: pathlib.Path | None = None,
    config: str = "",
) -> str:
    path = pathlib.Path.home() / ".config/systemd/user" / name
    config_files = (
        f"""[
            files::TextFile(
                host=host,
                path={json.dumps(str(config_file))},
                content={json.dumps(config)},
                permissions=null,
            ),
        ]"""
        if config_file is not None
        else "[]"
    )
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
            install={'files::systemd_unit::Install(wanted_by=["default.target"])' if install else "null"},
        )

        files::SystemdUnit(
            host=host,
            user={json.dumps(getpass.getuser())},
            name={json.dumps(name)},
            enabled={json.dumps(enabled)},
            active=true,
            purged={json.dumps(unit_purged)},
            restart_on_change={json.dumps(restart_on_change)},
            config_files={config_files},
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


def deploy_unit_file(project: pytest_inmanta.plugin.Project) -> None:
    """
    Write the unit file, and reload the service manager, which only has to
    happen once.
    """
    project.deploy_resource("files::Directory")
    project.deploy_resource(FILE)
    project.deploy_resource(RELOAD, change=const.Change.updated)
    project.deploy_resource(RELOAD, change=const.Change.nochange)


def test_model(project: pytest_inmanta.plugin.Project) -> None:
    """
    All the units of a service manager share its reload, which happens after
    the unit files changed and before the units are started.  A purged unit is
    stopped before its file is removed.  The config files of a unit are
    deployed before it, unless they are removed: then the unit is stopped first.
    """
    project.compile("""
        import files
        import mitogen
        import std

        host = std::Host(name="localhost", os=std::linux, via=mitogen::Local())

        files::SystemdUnitFile(host=host, path="/etc/systemd/system/a.service")
        files::SystemdUnitFile(host=host, path="/etc/systemd/system/b.service", purged=true)
        files::SystemdUnitFile(host=host, path="/home/me/.config/systemd/user/a.service")

        config = files::TextFile(host=host, path="/etc/a.env", content="")
        files::SystemdUnit(host=host, name="a.service", active=true, config_files=[config])
        b_config = files::TextFile(host=host, path="/etc/b.env", content="", purged=true)
        files::SystemdUnit(host=host, name="b.service", purged=true, config_files=[b_config])
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

    # The units start once their manager is reloaded, and their files are written
    config = project.get_resource("files::TextFile", path="/etc/a.env")
    assert config is not None
    assert a.requires == {system_reload.id, a_file.id, user_a_file.id, config.id}
    assert a.config_files == ["/etc/a.env"]
    assert c.requires == {system_reload.id}
    assert user_a.requires == {user_reload.id, a_file.id, user_a_file.id}
    assert (a.enabled, a.active) == (None, True)
    assert (c.enabled, c.active) == (False, None)

    # The purged unit is stopped and disabled before its files are removed
    b_config = project.get_resource("files::TextFile", path="/etc/b.env")
    assert b_config is not None
    assert b.requires == set()
    assert b.id in b_file.requires
    assert b.id in b_config.requires
    assert (b.enabled, b.active) == (False, False)


def test_model_purged_unit_file(project: pytest_inmanta.plugin.Project) -> None:
    """
    A unit file can only be removed if its unit is purged: the unit is then
    stopped and disabled before the file is removed, even if the model says
    otherwise.  Otherwise the unit would be skipped once its file is removed,
    without ever being stopped.  This is only checked for the unit files
    which are surely loaded by the service manager of the unit.
    """
    model = """
        import files
        import mitogen
        import std

        host = std::Host(name="localhost", os=std::linux, via=mitogen::Local())

        # Both purged: the unit is stopped before the file is removed
        a_file = files::SystemdUnitFile(host=host, path="/etc/systemd/system/a.service", purged=true)
        files::SystemdUnit(host=host, name="a.service", purged=true, requires=[a_file])

        # Not loaded by the service manager of the unit, or not for sure
        files::SystemdUnitFile(host=host, path="/etc/systemd/system/b.service", purged=true)
        files::SystemdUnit(host=host, name="b.service", user="me", active=true)
        files::SystemdUnitFile(host=host, path="/home/me/.config/systemd/user/c.service", purged=true)
        files::SystemdUnit(host=host, name="c.service", user="me", active=true)
        files::SystemdUnitFile(host=host, path="/opt/app/d.service", purged=true)
        files::SystemdUnit(host=host, name="d.service", active=true)
    """
    project.compile(model)

    a = get_unit(project, "system/a.service")
    a_file = project.get_resource(FILE, path="/etc/systemd/system/a.service")
    assert a_file is not None
    assert a_file.id not in a.requires
    assert a.id in a_file.requires

    # The unit file of the system manager is removed, but its unit is not purged
    for path, user in [
        ("/etc/systemd/system/e.service", "null"),
        ("/usr/lib/systemd/user/e.service", '"me"'),
    ]:
        with pytest.raises(ValueError, match=r"e\.service\] is not purged"):
            project.compile(model + f"""
                files::SystemdUnitFile(host=host, path="{path}", purged=true)
                files::SystemdUnit(host=host, name="e.service", user={user}, active=false)
                """)


def test_deploy(
    project: pytest_inmanta.plugin.Project, unit_name: str, tmp_path: pathlib.Path
) -> None:
    """
    The unit is skipped until it exists, then enabled and started, restarted
    when its files change if it should, and stopped and disabled once purged.
    """
    config_file = tmp_path / "config"
    project.compile(unit_model(unit_name, config_file=config_file, config="a"))
    project.deploy_resource("files::TextFile", change=const.Change.created)

    # The unit file doesn't exist yet
    project.deploy_resource(UNIT, status=const.ResourceState.skipped)

    # The unit is now known, it gets enabled and started
    deploy_unit_file(project)
    project.deploy_resource(UNIT, change=const.Change.updated)
    assert show(unit_name, "ActiveState") == "active"
    assert show(unit_name, "UnitFileState") == "enabled"
    project.deploy_resource(UNIT, change=const.Change.nochange)

    # The unit file changes, the unit is not restarted unless it should
    pid = show(unit_name, "MainPID")
    project.compile(
        unit_model(
            unit_name,
            exec_start="/usr/bin/sleep 3600",
            config_file=config_file,
            config="a",
        )
    )
    deploy_unit_file(project)
    project.deploy_resource(UNIT, change=const.Change.nochange)
    assert show(unit_name, "MainPID") == pid

    project.compile(
        unit_model(
            unit_name,
            exec_start="/usr/bin/sleep 3600",
            config_file=config_file,
            config="a",
            restart_on_change=True,
        )
    )
    # The pending restart shows in a dry-run
    assert set(project.dryrun_resource(UNIT)) == {"up_to_date"}
    project.deploy_resource(UNIT, change=const.Change.updated)
    assert show(unit_name, "ActiveState") == "active"
    assert show(unit_name, "MainPID") != pid
    project.deploy_resource(UNIT, change=const.Change.nochange)

    # The config file changes, the unit is restarted
    pid = show(unit_name, "MainPID")
    project.compile(
        unit_model(
            unit_name,
            exec_start="/usr/bin/sleep 3600",
            config_file=config_file,
            config="b",
            restart_on_change=True,
        )
    )
    project.deploy_resource("files::TextFile", change=const.Change.updated)
    project.deploy_resource(UNIT, change=const.Change.updated)
    assert show(unit_name, "MainPID") != pid
    project.deploy_resource(UNIT, change=const.Change.nochange)

    # The purged unit is stopped and disabled, its file is left untouched
    project.compile(unit_model(unit_name, unit_purged=True))
    project.deploy_resource(UNIT, change=const.Change.purged)
    assert show(unit_name, "ActiveState") == "inactive"
    assert show(unit_name, "UnitFileState") == "disabled"
    project.deploy_resource(UNIT, change=const.Change.nochange)

    # Once its file is removed and the manager reloaded, it is still purged
    project.compile(unit_model(unit_name, unit_purged=True, file_purged=True))
    project.deploy_resource(FILE, change=const.Change.purged)
    project.deploy_resource(RELOAD, change=const.Change.updated)
    assert show(unit_name, "LoadState") == "not-found"
    project.deploy_resource(UNIT, change=const.Change.nochange)


def test_reload_requires(
    project: pytest_inmanta.plugin.Project, unit_name: str, tmp_path: pathlib.Path
) -> None:
    """
    The service manager is reloaded when the folder of a file it requires
    changed, even if it is not one of its unit paths (e.g. quadlet files).
    """
    project.compile(unit_model(unit_name) + f"""
        files::SystemdDaemonReload(
            host=host,
            user={json.dumps(getpass.getuser())},
            requires=[
                files::TextFile(
                    host=host,
                    path={json.dumps(str(tmp_path / "app.container"))},
                    content="",
                    permissions=null,
                ),
            ],
        )
        """)
    deploy_unit_file(project)

    project.deploy_resource("files::TextFile", change=const.Change.created)
    project.deploy_resource(RELOAD, change=const.Change.updated)
    project.deploy_resource(RELOAD, change=const.Change.nochange)


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
    deploy_unit_file(project)

    project.deploy_resource(UNIT, status=const.ResourceState.failed)
    assert project.ctx is not None
    errors = "\n".join(str(log._data["kwargs"]) for log in project.ctx.logs)
    assert f"Journal of {unit_name}" in errors
    assert marker in errors


def test_deploy_static(project: pytest_inmanta.plugin.Project, unit_name: str) -> None:
    """
    A unit without [Install] section can't be enabled or disabled, asking for
    it to be disabled doesn't report a change on every deploy.
    """
    project.compile(unit_model(unit_name, enabled=False, install=False))
    deploy_unit_file(project)

    project.deploy_resource(UNIT, change=const.Change.updated)
    assert show(unit_name, "ActiveState") == "active"
    assert show(unit_name, "UnitFileState") == "static"
    project.deploy_resource(UNIT, change=const.Change.nochange)

    project.compile(unit_model(unit_name, unit_purged=True, install=False))
    project.deploy_resource(UNIT, change=const.Change.purged)
    assert show(unit_name, "ActiveState") == "inactive"
    project.deploy_resource(UNIT, change=const.Change.nochange)

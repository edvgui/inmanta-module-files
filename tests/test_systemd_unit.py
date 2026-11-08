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
from inmanta.resources import Id
from inmanta_plugins.files.systemd import (
    SystemdDaemonReloadResource,
    SystemdUnitResource,
)

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


def test_model(
    project: pytest_inmanta.plugin.Project, caplog: pytest.LogCaptureFixture
) -> None:
    """
    All the units of a service manager share its reload, which happens after
    the unit files changed and before the units are deployed.  A purged unit is
    stopped before its removed files are, so it can't wait for the reload
    then, it doesn't need it either.  A unit file which is surely loaded by
    the service manager of a unit can only be removed if the unit is purged.
    The dependencies the model sets are kept, with a warning if they are wrong.
    """
    model = """
        import files
        import mitogen
        import std

        host = std::Host(name="localhost", os=std::linux, via=mitogen::Local())

        # Started after its files and the reload
        a_config = files::TextFile(host=host, path="/etc/a.env", content="")
        files::SystemdUnitFile(host=host, path="/etc/systemd/system/a.service")
        files::SystemdUnit(host=host, name="a.service", active=true, config_files=[a_config])
        files::SystemdUnitFile(host=host, path="/home/me/.config/systemd/user/u.service")
        files::SystemdUnit(host=host, name="u.service", user="me", active=true)

        # Stopped before its files are removed
        b_config = files::TextFile(host=host, path="/etc/b.env", content="", purged=true)
        files::SystemdUnitFile(host=host, path="/etc/systemd/system/b.service", purged=true)
        files::SystemdUnit(host=host, name="b.service", purged=true, config_files=[b_config])

        # The model says otherwise, it is kept
        g_config = files::TextFile(host=host, path="/etc/g.env", content="", purged=true)
        g_file = files::SystemdUnitFile(host=host, path="/etc/systemd/system/g.service", purged=true)
        files::SystemdUnit(
            host=host, name="g.service", purged=true, config_files=[g_config], requires=[g_file, g_config],
        )
        h = files::SystemdUnit(host=host, name="h.service", active=true)
        files::SystemdUnitFile(host=host, path="/etc/systemd/system/h.service", requires=[h])

        # Stopped after its file is written and loaded, it may not exist yet
        files::SystemdUnitFile(host=host, path="/etc/systemd/system/c.service")
        files::SystemdUnit(host=host, name="c.service", purged=true)

        # Not loaded by the service manager of the unit, or not for sure
        files::SystemdUnitFile(host=host, path="/etc/systemd/system/d.service", purged=true)
        files::SystemdUnit(host=host, name="d.service", user="me", active=true)
        files::SystemdUnitFile(host=host, path="/home/me/.config/systemd/user/e.service", purged=true)
        files::SystemdUnit(host=host, name="e.service", user="me", active=true)

        # A quadlet file, removed after its unit is stopped
        q = files::SystemdUnit(host=host, name="q.service", purged=true)
        files::SystemdDaemonReload(
            host=host,
            requires=[
                files::TextFile(
                    host=host,
                    path="/etc/containers/systemd/q.container",
                    content="",
                    purged=true,
                    requires=[q],
                ),
            ],
        )
    """
    project.compile(model)

    system_reload = get_reload(project, "system")
    user_reload = get_reload(project, "user/me")
    assert system_reload.systemctl_command == ["systemctl"]
    assert user_reload.systemctl_command == ["systemctl", "--user"]
    assert len([r for r in project.resources.values() if r.is_type(RELOAD)]) == 2

    def rid(entity: str, path: str) -> Id:
        resource = project.get_resource(entity, path=path)
        assert resource is not None
        return resource.id

    def unit_file(name: str) -> Id:
        return rid(FILE, f"/etc/systemd/system/{name}")

    def user_unit_file(name: str) -> Id:
        return rid(FILE, f"/home/me/.config/systemd/user/{name}")

    # Every unit file of the host is loaded by the reloads
    unit_files = {r.id for r in project.resources.values() if r.is_type(FILE)}
    assert unit_files <= system_reload.requires
    assert unit_files <= user_reload.requires

    a = get_unit(project, "system/a.service")
    assert a.requires == {
        system_reload.id,
        unit_file("a.service"),
        rid("files::TextFile", "/etc/a.env"),
    }
    assert a.config_files == ["/etc/a.env"]
    assert (a.enabled, a.active) == (None, True)
    u = get_unit(project, "user/me/u.service")
    assert u.requires == {user_reload.id, user_unit_file("u.service")}

    b = get_unit(project, "system/b.service")
    assert b.requires == set()
    assert (b.enabled, b.active) == (False, False)
    for removed in [unit_file("b.service"), rid("files::TextFile", "/etc/b.env")]:
        assert b.id in project.resources[removed].requires

    g = get_unit(project, "system/g.service")
    g_config = rid("files::TextFile", "/etc/g.env")
    assert g.requires == {system_reload.id, unit_file("g.service"), g_config}
    for removed in [unit_file("g.service"), g_config]:
        assert g.id not in project.resources[removed].requires
        assert f"{g.id} should be deployed before {removed}" in caplog.text

    # The unit file waits for the unit, which can't wait for the reload then
    h = get_unit(project, "system/h.service")
    assert h.requires == set()
    assert f"{unit_file('h.service')} should be deployed before {h.id}" in caplog.text

    c = get_unit(project, "system/c.service")
    assert c.requires == {system_reload.id, unit_file("c.service")}

    d = get_unit(project, "user/me/d.service")
    assert d.requires == {user_reload.id, unit_file("d.service")}
    e = get_unit(project, "user/me/e.service")
    assert e.requires == {user_reload.id, user_unit_file("e.service")}

    q = get_unit(project, "system/q.service")
    assert q.requires == set()

    # The unit file is removed, but its unit is not purged
    for path, user in [
        ("/etc/systemd/system/f.service", "null"),
        ("/usr/lib/systemd/user/f.service", '"me"'),
    ]:
        with pytest.raises(ValueError, match=r"f\.service\] is not purged"):
            project.compile(model + f"""
                files::SystemdUnitFile(host=host, path="{path}", purged=true)
                files::SystemdUnit(host=host, name="f.service", user={user}, active=false)
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

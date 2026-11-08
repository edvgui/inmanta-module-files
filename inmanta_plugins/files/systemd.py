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

import collections
import datetime
import os
import re
import typing
from collections.abc import Sequence

import inmanta_plugins.mitogen.abc

import inmanta.agent.handler
import inmanta.execute.proxy
import inmanta.export
import inmanta.resources
from inmanta.export import ModelDict, ResourceDict, dependency_manager

# The unit paths systemd writes to itself: generated units are rewritten on
# every reload, transient and control units are loaded as soon as they are
# written.  They never ask for a reload.
SYSTEMD_OWNED_PATH = re.compile(
    r"/systemd/(generator(\.early|\.late)?|transient)(/|$)|\.control(/|$)"
)


def manager_name(user: str | None) -> str:
    """
    Identify the systemd service manager a unit belongs to: the system one, or
    the one of the given user.
    """
    return "system" if user is None else f"user/{user}"


def parse_timestamp(value: str) -> float | None:
    """
    Parse a timestamp printed by systemctl show --timestamp=us+utc, e.g.
    "Sat 2026-11-07 20:54:24.753120 UTC", into seconds since the epoch.
    Return None for the timestamps of what never happened (empty or n/a).
    """
    try:
        # Drop the day of the week, it doesn't matter and depends on the locale
        _, date = value.split(" ", maxsplit=1)
        return (
            datetime.datetime.strptime(date, "%Y-%m-%d %H:%M:%S.%f UTC")
            .replace(tzinfo=datetime.UTC)
            .timestamp()
        )
    except ValueError:
        return None


class SystemctlResource(inmanta_plugins.mitogen.abc.ResourceABC):
    """
    Base class for all resources executing systemctl commands, against the
    service manager of the system or of a user.
    """

    fields = ("user", "systemctl_command")  # type: ignore[assignment]
    user: str | None
    systemctl_command: list[str]

    @classmethod
    def get_systemctl_command(
        cls,
        _: inmanta.export.Exporter,
        entity: inmanta.execute.proxy.DynamicProxy,
    ) -> list[str]:
        if entity.systemctl_command is not None:
            return list(entity.systemctl_command)
        return ["systemctl"] if entity.user is None else ["systemctl", "--user"]


R = typing.TypeVar("R", bound=SystemctlResource)


class SystemctlHandler(inmanta_plugins.mitogen.abc.HandlerABC[R]):
    """
    Base class for all handlers executing systemctl commands, it gives the
    journal of the units that fail.
    """

    def systemctl(self, resource: R, *args: str) -> tuple[str, str, int]:
        command = [*resource.systemctl_command, *args]
        return self.proxy.run(command[0], command[1:])

    def show(
        self, resource: R, *properties: str, unit: str | None = None
    ) -> dict[str, str]:
        """
        Get the given properties of the unit, or of the service manager when no
        unit is given.  Timestamps are printed in UTC, with microseconds.
        """
        args = ["show", "--timestamp=us+utc", "--property", ",".join(properties)]
        if unit is not None:
            args += ["--", unit]
        stdout, stderr, return_code = self.systemctl(resource, *args)
        if return_code != 0:
            raise RuntimeError(
                f"Failed to read {', '.join(properties)} of {unit or 'the service manager'} "
                f"(return code {return_code}): {stderr}"
            )
        return dict(
            line.split("=", maxsplit=1) for line in stdout.splitlines() if "=" in line
        )

    def last_modified(self, paths: Sequence[str], depth: int) -> float | None:
        """
        Get the most recent modification time (seconds since the epoch) of the
        given paths, and of what they contain, up to the given depth.  Return
        None if none of them exist.  The modification time of a directory
        changes when an entry is added to or removed from it.
        """
        if not paths:
            return None
        # Missing paths are reported on stderr, they simply have no modification time
        stdout, _, _ = self.proxy.run(
            "find", ["-P", *paths, "-maxdepth", str(depth), "-printf", "%T@\\n"]
        )
        return max(
            (float(line) for line in stdout.splitlines() if line.strip()), default=None
        )

    def host_time(self) -> str | None:
        """
        The current time on the host (seconds since the epoch), to read back
        what was logged after it, or None if it can not be read.
        """
        stdout, _, return_code = self.proxy.run("date", ["+%s"])
        return stdout.strip() if return_code == 0 else None

    def journal(self, resource: R, unit: str, since: str | None) -> str:
        """
        Get the lines the unit logged since the given host time, which hold the
        actual error of a unit that failed: systemctl only says to go and look
        at them.  When journalctl is not available or can not be used, fall
        back on the status of the unit, which may hold its last lines too.

        journalctl exits with 0 when it can't read the journal (e.g. the user
        is not in the systemd-journal group), it only says so on stderr, which
        is kept, so that an empty journal is never mistaken for a silent unit.
        """
        journalctl = ["--user"] if resource.user is not None else []
        journalctl += ["--unit", unit, "--lines", "50", "--no-pager"]
        if since is not None:
            journalctl += ["--since", f"@{since}"]

        try:
            stdout, stderr, return_code = self.proxy.run("journalctl", journalctl)
        except Exception as e:
            # journalctl is not installed on the host
            stdout, stderr, return_code = "", str(e), -1

        if return_code == 0:
            lines = stdout or "-- No entries --"
            if stderr:
                lines += f"\n(journalctl: {stderr})"
            return f"Journal of {unit}:\n{lines}"

        stdout, _, _ = self.systemctl(
            resource, "status", "--no-pager", "--full", "--", unit
        )
        return (
            f"journalctl failed ({stderr or f'return code {return_code}'}), "
            f"status of {unit}:\n{stdout}"
        )

    def run_systemctl(self, resource: R, *args: str, unit: str | None = None) -> None:
        """
        Run the systemctl command, and raise an error if it fails.  When it is
        about a unit, the error holds what the unit logged during the command,
        and the command also fails if it leaves the unit in a failed state: a
        unit whose process dies right after it started is started successfully.
        """
        since = self.host_time() if unit is not None else None
        stdout, stderr, return_code = self.systemctl(resource, *args)
        if return_code != 0:
            message = (
                f"Failed to run {' '.join(args)} (return code {return_code}): "
                f"{stderr or stdout}"
            )
        elif (
            unit is not None
            and self.systemctl(resource, "is-failed", "--quiet", "--", unit)[2] == 0
        ):
            message = f"The unit {unit} is in a failed state after {' '.join(args)}"
        else:
            return

        if unit is not None:
            message += "\n\n" + self.journal(resource, unit, since)
        raise RuntimeError(message)


@inmanta.resources.resource(
    name="files::SystemdDaemonReload",
    id_attribute="manager",
    agent="host.name",
)
class SystemdDaemonReloadResource(SystemctlResource):
    fields = ("manager", "paths", "loaded")  # type: ignore[assignment]
    manager: str
    paths: list[str]
    loaded: bool

    @classmethod
    def get_manager(
        cls,
        _: inmanta.export.Exporter,
        entity: inmanta.execute.proxy.DynamicProxy,
    ) -> str:
        return manager_name(entity.user)

    @classmethod
    def get_paths(
        cls,
        _: inmanta.export.Exporter,
        entity: inmanta.execute.proxy.DynamicProxy,
    ) -> list[str]:
        # Filled in by the dependency manager, with the folders of the unit files
        return []

    @classmethod
    def get_loaded(
        cls,
        _: inmanta.export.Exporter,
        entity: inmanta.execute.proxy.DynamicProxy,
    ) -> bool:
        return True


@inmanta.agent.handler.provider("files::SystemdDaemonReload", "")
class SystemdDaemonReloadHandler(SystemctlHandler[SystemdDaemonReloadResource]):
    """
    The service manager has loaded its units if none of the files it loads them
    from changed since it last did: the files in its unit paths (and their
    drop-in folders), and in the folders of the unit files managed on the host
    (e.g. the quadlet files, that a generator turns into units).
    """

    def read_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: SystemdDaemonReloadResource,
    ) -> None:
        manager = self.show(resource, "UnitPath", "UnitsLoadTimestamp")
        loaded_at = parse_timestamp(manager.get("UnitsLoadTimestamp", ""))
        paths = [
            p
            for p in manager.get("UnitPath", "").split()
            if not SYSTEMD_OWNED_PATH.search(p)
        ]
        modified_at = self.last_modified([*paths, *resource.paths], depth=2)
        ctx.debug(
            "Units loaded at %(loaded_at)s, unit files modified at %(modified_at)s",
            loaded_at=str(loaded_at),
            modified_at=str(modified_at),
        )
        resource.loaded = loaded_at is not None and (
            modified_at is None or modified_at <= loaded_at
        )

    def calculate_diff(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        current: SystemdDaemonReloadResource,
        desired: SystemdDaemonReloadResource,
    ) -> dict[str, dict[str, typing.Any]]:
        if desired.purged:
            # The service manager can't be removed, we simply stop reloading it
            return {}
        return super().calculate_diff(ctx, current, desired)

    def update_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        changes: dict[str, dict[str, object]],
        resource: SystemdDaemonReloadResource,
    ) -> None:
        self.run_systemctl(resource, "daemon-reload")
        ctx.set_updated()


@inmanta.resources.resource(
    name="files::SystemdUnit",
    id_attribute="unit",
    agent="host.name",
)
class SystemdUnitResource(SystemctlResource):
    fields = (  # type: ignore[assignment]
        "unit",
        "name",
        "enabled",
        "active",
        "config_files",
        "up_to_date",
    )
    unit: str
    name: str
    enabled: bool | None
    active: bool | None
    config_files: list[str]
    up_to_date: bool | None

    @classmethod
    def get_unit(
        cls,
        _: inmanta.export.Exporter,
        entity: inmanta.execute.proxy.DynamicProxy,
    ) -> str:
        return f"{manager_name(entity.user)}/{entity.name}"

    @classmethod
    def get_enabled(
        cls,
        _: inmanta.export.Exporter,
        entity: inmanta.execute.proxy.DynamicProxy,
    ) -> bool | None:
        return False if entity.purged else entity.enabled

    @classmethod
    def get_active(
        cls,
        _: inmanta.export.Exporter,
        entity: inmanta.execute.proxy.DynamicProxy,
    ) -> bool | None:
        return False if entity.purged else entity.active

    @classmethod
    def get_up_to_date(
        cls,
        _: inmanta.export.Exporter,
        entity: inmanta.execute.proxy.DynamicProxy,
    ) -> bool | None:
        return True if entity.restart_on_change and not entity.purged else None


@inmanta.agent.handler.provider("files::SystemdUnit", "")
class SystemdUnitHandler(SystemctlHandler[SystemdUnitResource]):
    """
    Enable/disable and start/stop a unit.  A unit is purged when it is neither
    enabled nor active: its unit file is not ours to remove.  A unit which
    doesn't exist (yet) is skipped, unless it is purged.

    A running unit is up to date when it started after any of its files was
    last modified: its unit file (or the file it is generated from, e.g. a
    quadlet file), its drop-ins and its config files.
    """

    def read_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: SystemdUnitResource,
    ) -> None:
        properties = self.show(
            resource,
            "LoadState",
            "ActiveState",
            "ActiveEnterTimestamp",
            "FragmentPath",
            "SourcePath",
            "DropInPaths",
            unit=resource.name,
        )
        ctx.debug("Current state of the unit", properties=properties)
        if properties.get("LoadState") == "not-found":
            raise inmanta.agent.handler.ResourcePurged()

        resource.active = properties.get("ActiveState") in ["active", "reloading"]
        stdout, _, return_code = self.systemctl(
            resource, "is-enabled", "--", resource.name
        )
        if stdout.strip() not in ("static", "generated", "transient"):
            # Units without an [Install] section can't be enabled or disabled
            resource.enabled = return_code == 0

        if resource.up_to_date is None:
            # We don't restart the unit on changes, don't bother checking
            return

        started_at = parse_timestamp(properties.get("ActiveEnterTimestamp", ""))
        if not resource.active or started_at is None:
            # Whatever changed will be picked up when the unit starts
            resource.up_to_date = True
            return

        files = [
            path
            for path in [
                properties.get("SourcePath") or properties.get("FragmentPath", ""),
                *properties.get("DropInPaths", "").split(),
            ]
            if path and not SYSTEMD_OWNED_PATH.search(path)
        ]
        modified_at = self.last_modified([*files, *resource.config_files], depth=0)
        ctx.debug(
            "Unit started at %(started_at)s, its files modified at %(modified_at)s",
            started_at=str(started_at),
            modified_at=str(modified_at),
            files=files,
        )
        resource.up_to_date = modified_at is None or modified_at <= started_at

    def calculate_diff(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        current: SystemdUnitResource,
        desired: SystemdUnitResource,
    ) -> dict[str, dict[str, typing.Any]]:
        changes = super().calculate_diff(ctx, current, desired)
        if desired.purged and not current.enabled and not current.active:
            # The unit is neither enabled nor active, that is as purged as it gets
            changes.pop("purged", None)
        return changes

    def create_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: SystemdUnitResource,
    ) -> None:
        # The unit is only reported as purged when it doesn't exist
        raise inmanta.agent.handler.SkipResource(
            f"The unit {resource.name} doesn't exist (yet)"
        )

    def update_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        changes: dict[str, dict[str, object]],
        resource: SystemdUnitResource,
    ) -> None:
        if "enabled" in changes and resource.enabled:
            self.run_systemctl(resource, "enable", "--", resource.name)
        if "active" in changes:
            if resource.active:
                self.run_systemctl(
                    resource, "start", "--", resource.name, unit=resource.name
                )
            else:
                self.run_systemctl(resource, "stop", "--", resource.name)
        elif "up_to_date" in changes:
            self.run_systemctl(
                resource, "restart", "--", resource.name, unit=resource.name
            )
        if "enabled" in changes and not resource.enabled:
            self.run_systemctl(resource, "disable", "--", resource.name)
        ctx.set_updated()

    def delete_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: SystemdUnitResource,
    ) -> None:
        self.run_systemctl(resource, "disable", "--now", "--", resource.name)
        ctx.set_purged()


@dependency_manager
def systemd_dependencies(model: ModelDict, resources: ResourceDict) -> None:
    """
    Order the unit files, the service manager reloads and the units of each
    host:
    - every unit file is written (or removed) before the service managers of
      its host are reloaded, which also look for changes in the folders of
      these files: the file doesn't tell which manager loads it, and it may
      not be in a unit path (e.g. a quadlet file, turned into a unit by a
      generator);
    - every unit is enabled and started once its service manager is reloaded,
      and once its own unit file is written;
    - every purged unit is stopped and disabled before its unit file is removed.
    """
    from inmanta_plugins.files.systemd_unit import SystemdUnitFileResource

    files: dict[str, list[SystemdUnitFileResource]] = collections.defaultdict(list)
    reloads: dict[tuple[str, str], SystemdDaemonReloadResource] = {}
    units: list[SystemdUnitResource] = []
    for resource in resources.values():
        match resource:
            case SystemdUnitFileResource():
                files[resource.id.agent_name].append(resource)
            case SystemdDaemonReloadResource():
                reloads[(resource.id.agent_name, resource.manager)] = resource
            case SystemdUnitResource():
                units.append(resource)
            case _:
                pass

    for (host, _), daemon_reload in reloads.items():
        daemon_reload.requires.update(f.id for f in files[host])
        daemon_reload.paths = sorted({os.path.dirname(f.path) for f in files[host]})

    for unit in units:
        host = unit.id.agent_name
        unit_files = [f for f in files[host] if os.path.basename(f.path) == unit.name]
        if unit.purged:
            for unit_file in unit_files:
                unit_file.requires.add(unit.id)
            continue

        unit.requires.update(f.id for f in unit_files)
        unit_reload = reloads.get((host, manager_name(unit.user)))
        if unit_reload is not None:
            unit.requires.add(unit_reload.id)

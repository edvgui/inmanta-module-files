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
import os
import typing

import inmanta_plugins.mitogen.abc

import inmanta.agent.handler
import inmanta.execute.proxy
import inmanta.export
import inmanta.resources
from inmanta import const
from inmanta.export import ModelDict, ResourceDict, dependency_manager


def manager_name(user: str | None) -> str:
    """
    Identify the systemd service manager a unit belongs to: the system one, or
    the one of the given user.
    """
    return "system" if user is None else f"user/{user}"


class SystemctlResource(inmanta_plugins.mitogen.abc.Resource):
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


class SystemctlHandler(inmanta_plugins.mitogen.abc.Handler[R]):
    """
    Base class for all handlers executing systemctl commands, it allows them to
    reload on dependency changes and gives the journal of the units that fail.
    """

    def systemctl(self, resource: R, *args: str) -> tuple[str, str, int]:
        command = [*resource.systemctl_command, *args]
        return self.proxy.run(command[0], command[1:])

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

    def can_reload(self) -> bool:
        return True

    def do_reload(self, ctx: inmanta.agent.handler.HandlerContext, resource: R) -> None:
        """
        Called after the resource has been deployed, when any of its
        dependencies changed since its last successful deployment.  The base
        handler takes care of the remote connection and of the errors, the
        actual reload is delegated to the reload method.
        """
        if ctx.status is not const.ResourceState.deployed:
            # The resource failed or was skipped, there is nothing to reload
            return

        try:
            self.pre(ctx, resource)
            self.reload(ctx, resource)
        except Exception as e:
            ctx.set_resource_state(const.HandlerResourceState.failed)
            ctx.exception(
                "An error occurred while reloading %(resource_id)s (exception: %(exception)s)",
                resource_id=resource.id.resource_str(),
                exception=f"{e.__class__.__name__}('{e}')",
            )
        finally:
            self.post(ctx, resource)

    def reload(self, ctx: inmanta.agent.handler.HandlerContext, resource: R) -> None:
        """
        Reload the resource, because one of its dependencies changed.
        """
        raise NotImplementedError()


@inmanta.resources.resource(
    name="files::SystemdDaemonReload",
    id_attribute="manager",
    agent="host.name",
)
class SystemdDaemonReloadResource(SystemctlResource):
    fields = ("manager",)  # type: ignore[assignment]
    manager: str

    @classmethod
    def get_manager(
        cls,
        _: inmanta.export.Exporter,
        entity: inmanta.execute.proxy.DynamicProxy,
    ) -> str:
        return manager_name(entity.user)


@inmanta.agent.handler.provider("files::SystemdDaemonReload", "")
class SystemdDaemonReloadHandler(
    SystemctlHandler[SystemdDaemonReloadResource],
    inmanta.agent.handler.ResourceHandler[SystemdDaemonReloadResource],
):
    """
    The resource has no state, it reloads the service manager when one of its
    dependencies changed.  The reload is not reported as a change: the units
    restarting on changes depend on their own files, they shouldn't restart
    because any unit file of the host changed.
    """

    def check_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: SystemdDaemonReloadResource,
    ) -> SystemdDaemonReloadResource:
        return resource

    def do_changes(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: SystemdDaemonReloadResource,
        changes: typing.Mapping[str, typing.Mapping[str, object]],
    ) -> None:
        pass

    def reload(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: SystemdDaemonReloadResource,
    ) -> None:
        self.run_systemctl(resource, "daemon-reload")
        ctx.info("Reloaded the %(manager)s service manager", manager=resource.manager)


@inmanta.resources.resource(
    name="files::SystemdUnit",
    id_attribute="unit",
    agent="host.name",
)
class SystemdUnitResource(SystemctlResource, inmanta_plugins.mitogen.abc.ResourceABC):
    fields = ("unit", "name", "enabled", "active")  # type: ignore[assignment]
    unit: str
    name: str
    enabled: bool | None
    active: bool | None
    purged: bool
    receive_events: bool

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


@inmanta.agent.handler.provider("files::SystemdUnit", "")
class SystemdUnitHandler(
    SystemctlHandler[SystemdUnitResource],
    inmanta.agent.handler.CRUDHandler[SystemdUnitResource],
):
    """
    Enable/disable and start/stop a unit.  A unit is purged when it is neither
    enabled nor active: its unit file is not ours to remove.  A unit which
    doesn't exist (yet) is skipped, unless it is purged.
    """

    def read_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: SystemdUnitResource,
    ) -> None:
        stdout, stderr, return_code = self.systemctl(
            resource, "show", "--property", "LoadState,ActiveState", "--", resource.name
        )
        if return_code != 0:
            raise RuntimeError(
                f"Failed to read the state of {resource.name} (return code {return_code}): {stderr}"
            )
        properties = dict(
            line.split("=", maxsplit=1) for line in stdout.splitlines() if "=" in line
        )
        ctx.debug("Current state of the unit", properties=properties)
        if properties.get("LoadState") == "not-found":
            raise inmanta.agent.handler.ResourcePurged()

        resource.active = properties.get("ActiveState") in ["active", "reloading"]
        resource.enabled = (
            self.systemctl(resource, "is-enabled", "--quiet", "--", resource.name)[2]
            == 0
        )

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

    def reload(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: SystemdUnitResource,
    ) -> None:
        if not resource.receive_events or resource.purged or "active" in ctx.changes:
            # The unit doesn't restart on changes, or it has just been
            # started or stopped
            return

        # Only restart the unit if it is running, a stopped unit will pick up
        # the change when it starts
        self.run_systemctl(
            resource, "try-restart", "--", resource.name, unit=resource.name
        )
        ctx.info("Restarted %(unit)s, if it was running", unit=resource.name)
        if ctx.change is const.Change.nochange:
            ctx.set_updated()


@dependency_manager
def systemd_dependencies(model: ModelDict, resources: ResourceDict) -> None:
    """
    Order the unit files, the service manager reloads and the units of each
    host:
    - every unit file is written (or removed) before the service managers of
      its host are reloaded: the file doesn't tell which manager loads it,
      reloading another one does no harm;
    - every unit is enabled and started once its service manager is reloaded,
      and once its own unit file is written, so that it restarts when its file
      changes (if it receives events);
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

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

import typing

import inmanta_plugins.mitogen.abc

import inmanta.agent.agent
import inmanta.agent.handler
import inmanta.const
import inmanta.execute.proxy
import inmanta.export
import inmanta.resources


class BaseFileResource(
    inmanta_plugins.mitogen.abc.ResourceABC, inmanta.resources.ManagedResource
):
    fields = ("path", "permissions", "owner", "group")
    path: str
    permissions: typing.Optional[int]
    owner: typing.Optional[str]
    group: typing.Optional[str]


X = typing.TypeVar("X", bound=BaseFileResource)


class BaseFileHandler(inmanta_plugins.mitogen.abc.HandlerABC[X]):
    def whoami(self) -> str:
        """
        Check which user is currently running the the commands on the proxy.
        The result is cached on the proxy object to avoid running the command
        more times than required.
        """
        if not hasattr(self.proxy, "_whoami"):
            stdout, stderr, code = self.proxy.run("whoami")
            if code != 0:
                raise RuntimeError(
                    f"Failed to check current user on the remote host: {stderr}"
                )

            # Cache the result
            setattr(self.proxy, "_whoami", stdout)

        return typing.cast(str, getattr(self.proxy, "_whoami"))

    def read_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: X
    ) -> None:
        if not self.proxy.file_exists(resource.path):
            raise inmanta.agent.handler.ResourcePurged()

        for key, value in self.proxy.file_stat(resource.path).items():
            if getattr(resource, key) is not None:
                setattr(resource, key, value)

    def create_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: X
    ) -> None:
        if resource.permissions is not None:
            self.proxy.chmod(resource.path, str(resource.permissions))

        if resource.owner is not None or resource.group is not None:
            self.proxy.chown(resource.path, resource.owner, resource.group)

        ctx.set_created()

    def update_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        changes: dict[str, dict[str, object]],
        resource: X,
    ) -> None:
        if "permissions" in changes:
            self.proxy.chmod(resource.path, str(resource.permissions))

        if "owner" in changes or "group" in changes:
            self.proxy.chown(resource.path, resource.owner, resource.group)

        ctx.set_updated()

    def delete_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: X
    ) -> None:
        self.proxy.remove(resource.path)
        ctx.set_purged()

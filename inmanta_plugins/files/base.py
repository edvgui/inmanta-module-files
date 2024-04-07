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

import inmanta.agent.agent
import inmanta.agent.handler
import inmanta.agent.io.local
import inmanta.const
import inmanta.execute.proxy
import inmanta.export
import inmanta.resources


class BaseFileResource(
    inmanta.resources.PurgeableResource, inmanta.resources.ManagedResource
):
    fields = ("path", "permissions", "owner", "group")
    path: str
    permissions: typing.Optional[int]
    owner: typing.Optional[str]
    group: typing.Optional[str]


X = typing.TypeVar("X", bound=BaseFileResource)


class BaseFileHandler(inmanta.agent.handler.CRUDHandlerGeneric[X]):
    _io: inmanta.agent.io.local.LocalIO

    def read_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: X
    ) -> None:
        if not self._io.file_exists(resource.path):
            raise inmanta.agent.handler.ResourcePurged()

        for key, value in self._io.file_stat(resource.path).items():
            if getattr(resource, key) is not None:
                setattr(resource, key, value)

    def create_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: X
    ) -> None:
        if resource.permissions is not None:
            self._io.chmod(resource.path, str(resource.permissions))

        if resource.owner is not None or resource.group is not None:
            self._io.chown(resource.path, resource.owner, resource.group)

        ctx.set_created()

    def update_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        changes: dict[str, dict[str, object]],
        resource: X,
    ) -> None:
        if "permissions" in changes:
            self._io.chmod(resource.path, str(resource.permissions))

        if "owner" in changes or "group" in changes:
            self._io.chown(resource.path, resource.owner, resource.group)

        ctx.set_updated()

    def delete_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: X
    ) -> None:
        self._io.remove(resource.path)
        ctx.set_purged()

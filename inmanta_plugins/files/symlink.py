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

import inmanta.agent.agent
import inmanta.agent.handler
import inmanta.agent.io.local
import inmanta.const
import inmanta.execute.proxy
import inmanta.export
import inmanta.resources
import inmanta_plugins.files.base
import inmanta_plugins.files.json


@inmanta.resources.resource(
    name="files::Symlink",
    id_attribute="path",
    agent="host.name",
)
class SymlinkResource(inmanta_plugins.files.base.BaseFileResource):
    fields = ("target",)
    target: str


@inmanta.agent.handler.provider("files::Symlink", "")
class SymlinkHandler(inmanta_plugins.files.base.BaseFileHandler[SymlinkResource]):
    _io: inmanta.agent.io.local.LocalIO

    def read_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: SymlinkResource
    ) -> None:
        super().read_resource(ctx, resource)

    def create_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: SymlinkResource
    ) -> None:
        # Call the basic io mkdir helper
        self._io.symlink(resource.target, resource.path)

        super().create_resource(ctx, resource)

    def update_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        changes: dict[str, dict[str, object]],
        resource: SymlinkResource,
    ) -> None:
        super().update_resource(ctx, changes, resource)

    def delete_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: SymlinkResource
    ) -> None:
        self._io.remove(resource.path)
        ctx.set_purged()

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

    def chown_symlink(self, path: str, owner: str, group: str) -> None:
        """
        Perform the chown operation on a symlink, it requires an extra argument
        to prevent chown from resolving the symlink itself and changing the
        permissions of the target file.
        """
        self._io.run(
            "chown",
            [
                "--no-dereference",
                f"{owner}:{group}",
                path,
            ],
        )

    def read_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: SymlinkResource
    ) -> None:
        super().read_resource(ctx, resource)

        if not self._io.is_symlink(resource.path):
            raise Exception(
                "The target of resource %s already exists but is not a symlink."
                % resource
            )

        resource.target = self._io.readlink(resource.path)

    def create_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: SymlinkResource
    ) -> None:
        # Call the basic io symlink helper
        self._io.symlink(resource.target, resource.path)

        if resource.owner is not None or resource.group is not None:
            self.chown_symlink(resource.path, resource.owner, resource.group)

        ctx.set_created()

    def update_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        changes: dict[str, dict[str, object]],
        resource: SymlinkResource,
    ) -> None:
        if "target" in changes:
            self._io.remove(resource.path)
            self._io.symlink(resource.target, resource.path)

        if "owner" in changes or "group" in changes:
            self.chown_symlink(resource.path, resource.owner, resource.group)

        ctx.set_updated()

    def delete_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: SymlinkResource
    ) -> None:
        self._io.remove(resource.path)
        ctx.set_purged()

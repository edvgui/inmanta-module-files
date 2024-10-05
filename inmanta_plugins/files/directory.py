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

import pathlib

import inmanta.agent.agent
import inmanta.agent.handler
import inmanta.const
import inmanta.execute.proxy
import inmanta.export
import inmanta.resources
import inmanta_plugins.files.base
import inmanta_plugins.files.json


@inmanta.resources.resource(
    name="files::Directory",
    id_attribute="path",
    agent="host.name",
)
class DirectoryResource(inmanta_plugins.files.base.BaseFileResource):
    fields = ("create_parents",)
    create_parents: bool


@inmanta.agent.handler.provider("files::Directory", "")
class DirectoryHandler(inmanta_plugins.files.base.BaseFileHandler[DirectoryResource]):
    def read_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: DirectoryResource
    ) -> None:
        super().read_resource(ctx, resource)

    def create_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: DirectoryResource
    ) -> None:
        if resource.create_parents:
            # Create all parent directories, with the owner, group and permissions of
            # their parents
            parent_owner = "root"
            parent_group = "root"
            parent_permissions = "555"
            for parent in reversed(pathlib.Path(resource.path).parents):
                if not self.proxy.file_exists(str(parent)):
                    # Create the parent directory, and make sure it has the
                    # right owner and permissions
                    self.proxy.mkdir(str(parent))

                    if self.whoami() == "root":
                        # We can only change the ownership if we are root
                        self.proxy.chown(str(parent), parent_owner, parent_group)

                    self.proxy.chmod(str(parent), parent_permissions)
                    continue

                # Read the existing folder permissions, and save it for the next child folder
                stat = self.proxy.file_stat(str(parent))
                parent_owner = stat["owner"]
                parent_group = stat["group"]
                parent_permissions = str(stat["permissions"])

        # Call the basic io mkdir helper
        self.proxy.mkdir(resource.path)

        super().create_resource(ctx, resource)

    def update_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        changes: dict[str, dict[str, object]],
        resource: DirectoryResource,
    ) -> None:
        super().update_resource(ctx, changes, resource)

    def delete_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: DirectoryResource
    ) -> None:
        self.proxy.rmdir(resource.path)
        ctx.set_purged()

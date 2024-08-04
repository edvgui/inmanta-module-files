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

import grp
import os
import pwd

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


def symlink_stat(path: str) -> dict[str, object]:
    """
    This method is similar to inmanta_mitogen.file_stat excepts that it doesn't
    try to resolve the symlink on the last element of the path (all other
    symlinks will be resolved).

    :param path: The path to stat.
    """
    # Resolve all the symlinks except the one at the end of the path
    parent_path = os.path.abspath(os.path.join(path, os.pardir))
    resolved_parent_path = os.path.realpath(parent_path)

    # Get the stat result for the symlink at the end of the path
    stat_result = os.stat(
        os.path.join(resolved_parent_path, os.path.basename(path)),
        follow_symlinks=False,
    )
    return dict(
        owner=pwd.getpwuid(stat_result.st_uid).pw_name,
        group=grp.getgrgid(stat_result.st_gid).gr_name,
        permissions=int(oct(stat_result.st_mode)[-4:]),
    )


@inmanta.agent.handler.provider("files::Symlink", "")
class SymlinkHandler(inmanta_plugins.files.base.BaseFileHandler[SymlinkResource]):
    def chown_symlink(self, path: str, owner: str, group: str) -> None:
        """
        Perform the chown operation on a symlink, it requires an extra argument
        to prevent chown from resolving the symlink itself and changing the
        permissions of the target file.
        """
        self.proxy.run(
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
        if not self.proxy.file_exists(resource.path):
            raise inmanta.agent.handler.ResourcePurged()

        if not self.proxy.is_symlink(resource.path):
            raise Exception(
                "The target of resource %s already exists but is not a symlink."
                % resource
            )

        for key, value in self.proxy.remote_call(
            symlink_stat,
            resource.path,
        ).items():
            if getattr(resource, key) is not None:
                setattr(resource, key, value)

        resource.target = self.proxy.readlink(resource.path)

    def create_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: SymlinkResource
    ) -> None:
        # Call the basic io symlink helper
        self.proxy.symlink(resource.target, resource.path)

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
            self.proxy.remove(resource.path)
            self.proxy.symlink(resource.target, resource.path)

        if "owner" in changes or "group" in changes:
            self.chown_symlink(resource.path, resource.owner, resource.group)

        ctx.set_updated()

    def delete_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: SymlinkResource
    ) -> None:
        self.proxy.remove(resource.path)
        ctx.set_purged()

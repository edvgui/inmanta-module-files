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

import collections
import os
import typing

import inmanta_plugins.mitogen.abc

import inmanta.agent.handler
import inmanta.resources
from inmanta.export import ModelDict, ResourceDict, dependency_manager


class BaseFileResource(
    inmanta_plugins.mitogen.abc.ResourceABC, inmanta.resources.ManagedResource
):
    fields = ("path", "permissions", "owner", "group")
    path: str
    permissions: int | None
    owner: str | None
    group: str | None


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


@dependency_manager
def dir_before_file(model: ModelDict, resources: ResourceDict):
    """
    If a file/symlink/directory is defined on a host, then make it depend on its parent directory
    cf. https://code.inmanta.com/solutions/modules/fs/-/blob/d5425be42af4ccd9f8be0316bcbd6fad47548fd8/inmanta_plugins/fs/resources.py#L367
    """
    from inmanta_plugins.files.directory import DirectoryResource
    from inmanta_plugins.files.symlink import SymlinkResource

    # Use plain strings with os.path instead of pathlib.Path objects.
    # The pathlib-based approach created millions of Path objects and used
    # `Path(dir) in path.parents` which iterates all parents with expensive
    # __eq__ comparisons involving string normalization on every call.
    # String prefix matching with os.path.normpath is O(1) per check.
    per_host: dict[str, list[tuple[str, BaseFileResource]]] = collections.defaultdict(
        list
    )
    per_host_dirs: dict[str, list[tuple[str, DirectoryResource]]] = (
        collections.defaultdict(list)
    )
    for resource in resources.values():
        match resource:
            case DirectoryResource():
                dir_path = os.path.normpath(resource.path)
                per_host_dirs[resource.model.host].append((dir_path + os.sep, resource))
                per_host[resource.model.host].append(
                    (os.path.normpath(resource.path), resource)
                )
            case SymlinkResource():
                per_host[resource.model.host].append(
                    (os.path.normpath(resource.path), resource)
                )
                per_host[resource.model.host].append(
                    (os.path.normpath(resource.target), resource)
                )
            case BaseFileResource():
                per_host[resource.model.host].append(
                    (os.path.normpath(resource.path), resource)
                )
            case _:
                pass

    # now add deps per host
    for host, files in per_host.items():
        for file_path, hfile in files:
            for dir_prefix, pdir in per_host_dirs[host]:
                if file_path.startswith(dir_prefix):
                    if pdir.purged:
                        if hfile.purged:
                            # The folder is purged, and so is the file, the file should be
                            # cleaned up first, then the folder can be.
                            # This is not required as the folder would have cleaned the file,
                            # but it is also not wrong
                            pdir.requires.add(hfile)
                        else:
                            # Trying to create a file in a purged folder, this can not work
                            raise RuntimeError(
                                f"Directory {pdir.id} is purged but a resource is trying to "
                                f"deploy something in it: {hfile.id}"
                            )
                    else:
                        # Make the File resource require the directory
                        hfile.requires.add(pdir)

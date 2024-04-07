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
    name="files::TextFile",
    id_attribute="path",
    agent="host.name",
)
class TextFileResource(inmanta_plugins.files.base.BaseFileResource):
    fields = ("content",)
    content: str


@inmanta.agent.handler.provider("files::TextFile", "")
class TextFileHandler(inmanta_plugins.files.base.BaseFileHandler[TextFileResource]):
    _io: inmanta.agent.io.local.LocalIO

    def read_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: TextFileResource
    ) -> None:
        super().read_resource(ctx, resource)

        # Load the content of the existing file
        resource.content = self._io.read_binary(resource.path).decode()
        ctx.debug("Reading existing file", raw_content=resource.content)

    def create_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: TextFileResource
    ) -> None:
        self._io.put(resource.path, resource.content.encode())
        super().create_resource(ctx, resource)

    def update_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        changes: dict[str, dict[str, object]],
        resource: TextFileResource,
    ) -> None:
        if "content" in changes:
            self._io.put(resource.path, resource.content.encode())

        super().update_resource(ctx, changes, resource)

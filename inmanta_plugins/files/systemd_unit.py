"""
Copyright 2024 Guillaume Everarts de Velp

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
import inmanta.const
import inmanta.execute.proxy
import inmanta.export
import inmanta.resources
import inmanta_plugins.files.base
from inmanta.plugins import plugin


@plugin
def quote(s: str) -> str:
    """
    Quote a string with double quotes and escape the following characters:
    - double-quotes
    - newline
    - carriage return
    - backslash

    cf. https://www.freedesktop.org/software/systemd/man/latest/systemd.syntax.html

    The returned string should be the equivalent value for that string in a systemd
    unit context where quoting is allowed.
    """
    escaped_chars = {
        "\\": "\\\\",
        "\n": "\\n",
        "\r": "\\r",
        '"': '\\"',
    }
    for k, v in escaped_chars.items():
        s = s.replace(k, v)

    return '"' + s + '"'


@inmanta.resources.resource(
    name="files::SystemdUnitFile",
    id_attribute="path",
    agent="host.name",
)
class SystemdUnitFileResource(inmanta_plugins.files.base.BaseFileResource):
    fields = ("content",)
    content: str


@inmanta.agent.handler.provider("files::SystemdUnitFile", "")
class SystemdUnitFileHandler(
    inmanta_plugins.files.base.BaseFileHandler[SystemdUnitFileResource]
):
    def read_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: SystemdUnitFileResource,
    ) -> None:
        super().read_resource(ctx, resource)

        # Load the content of the existing file
        resource.content = self.proxy.read_binary(resource.path).decode()
        ctx.debug("Reading existing file", content=resource.content)

    def create_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: SystemdUnitFileResource,
    ) -> None:
        self.proxy.put(resource.path, resource.content.encode())
        super().create_resource(ctx, resource)

    def update_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        changes: dict[str, dict[str, object]],
        resource: SystemdUnitFileResource,
    ) -> None:
        if "content" in changes:
            self.proxy.put(resource.path, resource.content.encode())

        super().update_resource(ctx, changes, resource)

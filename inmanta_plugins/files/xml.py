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
import copy
import json
import xml.etree.ElementTree

import inmanta.agent.agent
import inmanta.agent.handler
import inmanta.agent.io.local
import inmanta.const
import inmanta.execute.proxy
import inmanta.export
import inmanta.resources
import inmanta_plugins.files.base
from inmanta.util import dict_path
from inmanta_plugins.files.json import Operation


def update(
    config: xml.etree.ElementTree.Element,
    path: dict_path.DictPath,
    operation: Operation,
    desired: object,
) -> xml.etree.ElementTree.ElementTree:
    """
    Update the config config at the specified type, using given operation and desired value.

    :param config: The configuration to update
    :param path: The path pointing to an element of the config that should be modified
    :param operation: The type of operation to apply to the config element
    :param desired: The desired state to apply to the config element
    """
    if operation == Operation.REMOVE:
        path.remove(config)
        return config

    if operation == Operation.REPLACE:
        path.set_element(config, value=desired)
        return config

    if operation == Operation.MERGE:
        if not isinstance(desired, dict):
            raise ValueError(
                f"Merge operation is only supported for dicts, but got {type(desired)} "
                f"({desired})"
            )
        current = path.get_element(config, construct=True)
        if not isinstance(current, dict):
            raise ValueError(
                f"A dict can only me merged to a dict, current value at path {path} "
                f"is not a dict: {current} ({type(current)})"
            )
        current.update({k: v for k, v in desired.items() if v is not None})
        return config

    raise ValueError(f"Unsupported operation: {operation}")


@inmanta.resources.resource(
    name="files::XmlFile",
    id_attribute="path",
    agent="host.name",
)
class XmlFileResource(inmanta_plugins.files.base.BaseFileResource):
    fields = (
        "values",
    )
    values: list[dict]

    @classmethod
    def get_values(cls, _, entity: inmanta.execute.proxy.DynamicProxy) -> list[dict]:
        return [
            {
                "path": value.path,
                "operation": value.operation,
                "element": {
                    "name": value.element.name,
                    "attributes": value.element.attributes,
                    "text": value.element.text,
                },
            }
            for value in entity.values
        ]


@inmanta.agent.handler.provider("files::XmlFile", "")
class XmlFileHandler(inmanta_plugins.files.base.BaseFileHandler[XmlFileResource]):
    _io: inmanta.agent.io.local.LocalIO

    def read_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: XmlFileResource
    ) -> None:
        super().read_resource(ctx, resource)

        # Load the content of the existing file
        raw_content = self._io.read_binary(resource.path).decode()
        ctx.debug("Reading existing file", raw_content=raw_content)
        ctx.set("current_content", xml.etree.ElementTree.fromstring(raw_content))

    def calculate_diff(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        current: XmlFileResource,
        desired: XmlFileResource,
    ) -> dict[str, dict[str, object]]:
        # For file permissions and ownership, we delegate to the parent class
        changes = super().calculate_diff(ctx, current, desired)

        # To check if some change content needs to be applied, we perform a "stable" addition
        # operation: We apply our desired state to the current state, and check if we can then
        # see any difference.
        current_content = ctx.get("current_content")
        desired_content = copy.deepcopy(current_content)

        for value in desired.values:
            update(
                desired_content,
                dict_path.to_path(value["path"]),
                Operation(value["operation"]),
                value["value"],
            )

        if current_content != desired_content:
            changes["content"] = {
                "current": current_content,
                "desired": desired_content,
            }

        return changes

    def create_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: XmlFileResource
    ) -> None:
        # Build a config based on all the elements we want to manage
        content = xml.etree.ElementTree.fromstring("</>")
        for value in resource.values:
            update(
                content,
                dict_path.to_path(value["path"]),
                Operation(value["operation"]),
                value["value"],
            )

        raw_content = xml.etree.ElementTree.tostring(content)
        self._io.put(resource.path, raw_content.encode())
        super().create_resource(ctx, resource)

    def update_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        changes: dict[str, dict[str, object]],
        resource: XmlFileResource,
    ) -> None:
        if "content" in changes:
            raw_content = xml.etree.ElementTree.tostring(changes["content"]["desired"])
            self._io.put(resource.path, raw_content.encode())

        super().create_resource(ctx, resource)

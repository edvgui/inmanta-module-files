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
import enum
import json
import typing

import inmanta_plugins.std
import yaml

import inmanta.agent.handler
import inmanta.execute.proxy
import inmanta.execute.util
import inmanta.plugins
import inmanta.resources
import inmanta_plugins.files.base
from inmanta.util import dict_path


@inmanta.plugins.plugin()
def get_json_fact(
    context: inmanta.plugins.Context,
    resource: typing.Annotated[typing.Any, inmanta.plugins.ModelType["std::Resource"]],
    fact_name: str,
    *,
    default_value: object | None = None,
    soft_fail: bool = False,
) -> object:
    """
    Get a value from fact that is expected to be a json-serialized payload.
    Deserialize the value and return it.
    If soft_fail is True and the value is not a valid json, return Unknown instead.

    :param resource: The resource that should provide the fact
    :param fact_name: The name of the fact provided by the resource
    :param default_value: A default value to return if the fact is not set yet
    :param soft_fail: Whether to suppress json decoding error and return Unknown instead.
    """
    # Get the fact using std logic
    fact = inmanta_plugins.std.getfact(
        context,
        resource,
        fact_name,
    )

    # If the fact is unknown and we have a default, we return the default
    # instead
    if inmanta_plugins.std.is_unknown(fact) and default_value is not None:
        return default_value

    # If the fact is unknown, we return it as is
    if inmanta_plugins.std.is_unknown(fact):
        return fact

    # Try to decode the json
    try:
        return json.loads(fact)
    except json.JSONDecodeError:
        if soft_fail:
            # Return unknown instead
            return inmanta.execute.util.Unknown(source=resource)

        raise


class Operation(str, enum.Enum):
    REPLACE = "replace"
    REMOVE = "remove"
    MERGE = "merge"


def update(
    config: dict, path: dict_path.DictPath, operation: Operation, desired: object
) -> dict:
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
    name="files::JsonFile",
    id_attribute="path",
    agent="host.name",
)
class JsonFileResource(inmanta_plugins.files.base.BaseFileResource):
    fields = (
        "indent",
        "format",
        "values",
        "discovered_values",
        "named_list",
        "sort_keys",
    )
    values: list[dict]
    discovered_values: list[dict]
    format: typing.Literal["json", "yaml"]
    indent: int
    named_list: str | None
    sort_keys: bool

    @classmethod
    def get_values(cls, _, entity: inmanta.execute.proxy.DynamicProxy) -> list[dict]:
        path_prefix = (
            str(dict_path.InDict(entity.named_list))
            if entity.named_list is not None
            else None
        )

        def validate_path(path: str) -> str:
            if path_prefix is None:
                return path
            if path.startswith(path_prefix + "["):
                return path
            else:
                raise ValueError(
                    f"Unexpected path {path}.  The resource is a named list, "
                    f"all paths must start with {path_prefix}"
                )

        return [
            {
                "path": validate_path(value.path),
                "operation": value.operation,
                "value": value.value,
            }
            for value in entity.values
        ]

    @classmethod
    def get_discovered_values(
        cls, _, entity: inmanta.execute.proxy.DynamicProxy
    ) -> list[dict]:
        return [
            {
                "path": value.path,
            }
            for value in entity.discovered_values
        ]


@inmanta.resources.resource(
    name="files::SharedJsonFile",
    id_attribute="uri",
    agent="host.name",
)
class SharedJsonFileResource(JsonFileResource):
    fields = ("uri",)

    @classmethod
    def get_uri(cls, _, entity: inmanta.execute.proxy.DynamicProxy) -> str:
        """
        Compose a uri to identify the resource, and which allows multiple resources
        to manage the same file.
        """
        if entity.resource_discriminator:
            return f"{entity.path}:{entity.resource_discriminator}"
        return entity.path


@inmanta.agent.handler.provider("files::JsonFile", "")
@inmanta.agent.handler.provider("files::SharedJsonFile", "")
class JsonFileHandler(inmanta_plugins.files.base.BaseFileHandler[JsonFileResource]):
    def from_json(
        self,
        raw: str,
        *,
        format: typing.Literal["json", "yaml"],
        named_list: str | None = None,
    ) -> dict:
        """
        Convert a json-like raw string in the expected format to the corresponding
        python dict-like object.

        :param raw: The raw value, as read in the file.
        :param format: The format of the value.
        :param named_list: When this parameter is set, the json/yaml content
            is expected to be deserialized into a list.  The return object will
            then be a dict containing a single entry, with as key the value of this
            parameter and as value the deserialized json/yaml list.
        """
        if format == "json":
            data = json.loads(raw)
        elif format == "yaml":
            data = yaml.safe_load(raw)
        else:
            raise ValueError(f"Unsupported format: {format}")

        match (data, named_list):
            case dict(), None:
                return data
            case list(), str():
                return {named_list: data}
            case _:
                raise ValueError(f"Unsupported file content: {data}")

    def to_json(
        self,
        value: dict,
        *,
        format: typing.Literal["json", "yaml"],
        indent: typing.Optional[int] = None,
        sort_keys: bool | None = None,
        named_list: str | None = None,
    ) -> str:
        """
        Dump a dict-like structure into a json-like string.  The string can
        be in different formats, depending on the value specified.

        :param value: The dict-like value, to be written to file.
        :param format: The format of the value.
        :param indent: Whether any indentation should be applied to the
            value written to file.
        :param sort_keys: Whether the keys should be sorted when saving the file.
            Set to None to keep the underlying library's default behavior.
        :param named_list: When this parameter is set, the json/yaml content
            is expected to be serialized into a list.  The input object will
            then be a dict containing a single entry, with as key the value of this
            parameter and as value the json/yaml list to serialize.
        """
        if named_list is not None:
            value = value[named_list]

        if format == "json":
            sort_keys = False if sort_keys is None else sort_keys
            return json.dumps(value, indent=indent, sort_keys=sort_keys)
        if format == "yaml":
            sort_keys = True if sort_keys is None else sort_keys
            return yaml.safe_dump(value, indent=indent, sort_keys=sort_keys)
        raise ValueError(f"Unsupported format: {format}")

    def extract_facts(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: JsonFileResource,
        *,
        content: dict,
    ) -> dict[str, str]:
        # Read facts based on the content of the file
        return {
            str(path): json.dumps(
                {
                    str(k): dict_path.to_path(str(k)).get_element(content)
                    for k in path.resolve_wild_cards(content)
                }
            )
            for desired_value in resource.discovered_values
            if (path := dict_path.to_wild_path(desired_value["path"]))
        }

    def facts(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: JsonFileResource,
    ) -> dict[str, object]:
        try:
            # Delegate to read_resource to get the content of
            # the file
            self.read_resource(ctx, resource)
        except inmanta.agent.handler.ResourcePurged():
            return {}

        return self.extract_facts(
            ctx,
            resource,
            content=ctx.get("current_content"),
        )

    def read_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: JsonFileResource,
    ) -> None:
        super().read_resource(ctx, resource)

        # Load the content of the existing file
        raw_content = self.proxy.read_binary(resource.path).decode()
        ctx.debug("Reading existing file", raw_content=raw_content)
        current_content = self.from_json(
            raw_content, format=resource.format, named_list=resource.named_list
        )
        ctx.set("current_content", current_content)

    def calculate_diff(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        current: JsonFileResource,
        desired: JsonFileResource,
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

        # Set the facts now if it is a dryrun or if there is
        # no changes
        if not changes or ctx.is_dry_run():
            for k, v in self.extract_facts(
                ctx,
                desired,
                content=current_content,
            ).items():
                ctx.set_fact(k, v)

        return changes

    def create_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        resource: JsonFileResource,
    ) -> None:
        # Build a config based on all the elements we want to manage
        content = {}
        for value in resource.values:
            update(
                content,
                dict_path.to_path(value["path"]),
                Operation(value["operation"]),
                value["value"],
            )

        indent = resource.indent if resource.indent != 0 else None
        raw_content = self.to_json(
            content,
            format=resource.format,
            indent=indent,
            named_list=resource.named_list,
            sort_keys=resource.sort_keys,
        )
        self.proxy.put(resource.path, raw_content.encode())
        super().create_resource(ctx, resource)

        # Set the facts after creation
        for k, v in self.extract_facts(ctx, resource, content=content).items():
            ctx.set_fact(k, v)

    def update_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        changes: dict[str, dict[str, object]],
        resource: JsonFileResource,
    ) -> None:
        if "content" in changes:
            content = changes["content"]["desired"]
            indent = resource.indent if resource.indent != 0 else None
            raw_content = self.to_json(
                content,
                format=resource.format,
                indent=indent,
                named_list=resource.named_list,
                sort_keys=resource.sort_keys,
            )
            self.proxy.put(resource.path, raw_content.encode())

            # Set the facts after update
            for k, v in self.extract_facts(ctx, resource, content=content).items():
                ctx.set_fact(k, v)

        super().update_resource(ctx, changes, resource)

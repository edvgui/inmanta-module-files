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
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass

import inmanta_plugins.std
import yaml

import inmanta.agent.handler
import inmanta.execute.proxy
import inmanta.execute.util
import inmanta.plugins
import inmanta.resources
import inmanta_plugins.files.base
from inmanta.ast import OptionalValueException
from inmanta.ast.attribute import RelationAttribute
from inmanta.ast.entity import Entity
from inmanta.execute.proxy import SequenceProxy
from inmanta.util import dict_path

type Resource = typing.Annotated[
    inmanta.execute.proxy.DynamicProxy,
    inmanta.plugins.ModelType["std::Resource"],
]


class SerializableEntityProtocol(typing.Protocol):
    path: str
    managed: bool
    _operation: str
    mapping_overwrite: dict[str, str]
    parent: "SerializableEntityProtocol"
    root: "SerializableEntityProtocol"
    _resource: "JsonResourceProtocol"
    _type: typing.Callable[[], Entity]


type SerializableEntity = typing.Annotated[
    SerializableEntityProtocol,
    inmanta.plugins.ModelType["files::json::SerializableEntity"],
]


class JsonResourceProtocol(typing.Protocol):
    entities: Collection[SerializableEntityProtocol]
    serialized: Collection["SerializedEntity"]


type JsonResource = typing.Annotated[
    JsonResourceProtocol,
    inmanta.plugins.ModelType["files::json::JsonResource"],
]


PARENT_RELATION = "parent"
SERIALIZABLE_ENTITY_TYPE = "files::json::SerializableEntity"
SERIALIZABLE_ENTITY_ATTRIBUTES = [
    "path",
    "managed",
    "operation",
    "_operation",
    "mapping_overwrite",
]


def json_value(raw_value: object) -> object:
    """
    Convert an immutable value (i.e. coming from the inmanta DSL) into
    a mutable, json-like python object.  Sequences are converted into
    lists, and Mappings into dicts.  Any other value is kept as is.

    :param raw_value: The raw value that should be converted.
    """
    match raw_value:
        case str():
            return raw_value
        case Sequence() | SequenceProxy():
            return [json_value(item) for item in raw_value]
        case Mapping():
            return {k: json_value(v) for k, v in raw_value.items()}
        case _:
            return raw_value


def get_optional_relation(entity: object, name: str) -> object | None:
    """
    helper function to get the value of an optional relation, which will raise
    an OptionalValueException when the relation is not set.
    """
    try:
        return getattr(entity, name)
    except OptionalValueException:
        return None


@dataclass(frozen=True, kw_only=True)
class SerializedEntity:
    path: str
    operation: str
    value: dict | None


@inmanta.plugins.plugin()
def get_relation_from_parent(
    serializable_entity: SerializableEntity,
) -> str | None:
    """
    Figure out the relation that leads to a serializable entity, from its parent,
    if it has any.  If the entity has no parent, return None instead.

    :param serializable_entity: An instance of the serializable entity, for
        which we want to know the relation from the parent.
    """
    entity_type = serializable_entity._type()
    parent_relation = entity_type.get_attribute(PARENT_RELATION)

    match parent_relation:
        case None:
            # No relation named "parent"
            return None
        case RelationAttribute():
            if parent_relation.is_optional():
                # We always need a parent
                raise ValueError(
                    f"Parent relation on type {entity_type.type_string()} "
                    "is optional, this is not allowed."
                )

            if parent_relation.is_multi():
                # We always need a single parent
                raise ValueError(
                    f"Parent relation on type {entity_type.type_string()} "
                    "has an arity greater than 1, this is not allowed."
                )

            if parent_relation.end is None:
                # We need a reverse relation from the parent
                raise ValueError(
                    f"Parent relation on type {entity_type.type_string()} "
                    "is unidirectional, this is not allowed."
                )

            parent_type = parent_relation.end.entity
            if not isinstance(parent_type, Entity):
                raise RuntimeError(
                    f"Unexpected type for parent relation end's entity: {parent_type} ({type(parent_type)})"
                )

            if SERIALIZABLE_ENTITY_TYPE not in parent_type.get_all_parent_names():
                # The parent entity should also be a serializable entity
                raise ValueError(
                    f"Parent entity of {entity_type.type_string()} "
                    f"({parent_type.type_string()}) is not a subentity of "
                    f"{SERIALIZABLE_ENTITY_TYPE}."
                )

            return parent_relation.end.name
        case _:
            raise ValueError(
                f"Unexpected type for parent relation on type {entity_type.type_string()}"
            )


@inmanta.plugins.plugin()
def get_relative_path(serializable_entity: SerializableEntity) -> str | None:
    """
    Calculate the relative path from the parent entity.  If there is no parent
    entity, return None instead.  The relative path is derived from the index
    of this entity that contains the relation to the parent entity.

    :param serializable_entity: The instance for which we want to get the
        path from the parent, or None if the entity doesn't have a parent.
    """
    relation_from_parent = get_relation_from_parent(serializable_entity)
    if relation_from_parent is None:
        # No parent, no relative parent
        return None

    # If the relation from parent has been overwritten in the mapping of
    # the parent, we should adapt the relative path too.
    relation_from_parent = serializable_entity.parent.mapping_overwrite.get(
        relation_from_parent,
        relation_from_parent,
    )

    entity_type = serializable_entity._type()
    indices = entity_type.get_indices()

    for index in indices:
        if PARENT_RELATION not in index:
            # We only consider the index expression that contain the
            # parent relation, this allows to define additional index
            # related to the entity usage.
            continue

        index_attributes = [
            attr
            for attr_name in index
            if attr_name != PARENT_RELATION
            and (attr := entity_type.get_attribute(attr_name)) is not None
        ]

        # Make sure that none of the attributes used in the index are relations
        # as this wouldn't work for the dict_path expression we are constructing
        relation_attributes = [
            attr for attr in index_attributes if isinstance(attr, RelationAttribute)
        ]
        if relation_attributes:
            raise ValueError(
                f"Invalid index on type {entity_type.type_string()}. "
                f"Index {index} contains some relations: {[attr.name for attr in relation_attributes]}"
            )

        if not index_attributes:
            # Single instance, use InDict path
            return str(dict_path.InDict(relation_from_parent))
        else:
            # Get the keys and values from the model
            keys = {
                attr.name: getattr(serializable_entity, attr.name)
                for attr in index_attributes
            }

            # Normalize the keys, use the overwrite if it is defined, replace None value
            # by proper escape character
            return str(
                dict_path.KeyedList(
                    relation_from_parent,
                    [
                        (
                            serializable_entity.mapping_overwrite.get(key, key),
                            (
                                value
                                if value is not None
                                else dict_path.NullValue().escape()
                            ),
                        )
                        for key, value in keys.items()
                    ],
                )
            )

    # No valid index defined, impossible to derive a relative path.
    raise LookupError(
        f"Could not find any valid index on entity {entity_type.type_string()}."
    )


def get_instance_attributes(
    serializable_entity: SerializableEntity,
    *,
    serializable_entity_type: Entity,
) -> dict[str, object]:
    """
    Serialize a serializable entity into a dict containing its attributes.
    The method returns an empty dict if there are no attributes defined, or
    if the entity type is not a subclass of the serializable entity type.

    :param serializable_entity: The instance to serialize
    :param serializable_entity_type: The type of the entity to serialize
    """
    if serializable_entity_type.type_string() == SERIALIZABLE_ENTITY_TYPE:
        # The base entity doesn't have any attribute to serialize
        return {}

    if SERIALIZABLE_ENTITY_TYPE not in serializable_entity_type.get_all_parent_names():
        # This entity is not a subentity of the serializable entity
        # its attributes are not serializable
        return {}

    attributes: dict[str, object] = {}
    for super_entity in serializable_entity_type.parent_entities:
        # Let each parent entity extract the relevant attributes from the
        # model, if they have the right type
        attributes.update(
            get_instance_attributes(
                serializable_entity,
                serializable_entity_type=super_entity,
            )
        )

    for attr_name, attr in serializable_entity_type.attributes.items():
        if attr_name in SERIALIZABLE_ENTITY_ATTRIBUTES:
            # Make sure that any redefinition of the attributes of the base entity
            # stay ignored
            continue

        if isinstance(attr, RelationAttribute):
            # Only consider primitive attributes
            continue

        if (
            attr_name.startswith("_")
            and attr_name not in serializable_entity.mapping_overwrite
        ):
            # Private attributes should not be serialized
            continue

        # Add the serialized attribute to the dict of attributes
        serialized_name = serializable_entity.mapping_overwrite.get(
            attr_name, attr_name
        )
        attributes[serialized_name] = json_value(
            getattr(serializable_entity, attr_name)
        )

    return attributes


def get_child_instances(
    serializable_entity: SerializableEntity,
    *,
    serializable_entity_type: Entity,
) -> dict[str, list[SerializableEntity] | SerializableEntity]:
    """
    Get all the child serializable entities of a serializable entity.
    """
    if serializable_entity_type.type_string() == SERIALIZABLE_ENTITY_TYPE:
        # The base entity doesn't have any attribute to serialize
        return {}

    if SERIALIZABLE_ENTITY_TYPE not in serializable_entity_type.get_all_parent_names():
        # This entity is not a subentity of the serializable entity
        # its attributes are not serializable
        return {}

    attributes: dict[str, list[SerializableEntity] | SerializableEntity] = {}
    for super_entity in serializable_entity_type.parent_entities:
        # Let each parent entity extract the relevant attributes from the
        # model, if they have the right type
        attributes.update(
            get_child_instances(
                serializable_entity,
                serializable_entity_type=super_entity,
            )
        )

    for attr_name, attr in serializable_entity_type.attributes.items():
        if attr_name == PARENT_RELATION:
            # We only want the child entities
            continue

        if not isinstance(attr, RelationAttribute):
            # Only consider primitive attributes
            continue

        if attr.end is None:
            # Child instances will always be bi-directional
            continue

        child_type = attr.end.entity
        if not isinstance(child_type, Entity):
            raise RuntimeError(
                f"Unexpected type for child relation end's entity: {child_type} ({type(child_type)})"
            )

        if SERIALIZABLE_ENTITY_TYPE not in child_type.get_all_parent_names():
            # Not a relation towards a serializable entity
            continue

        if (
            attr_name.startswith("_")
            and attr_name not in serializable_entity.mapping_overwrite
        ):
            # Private attributes should not be serialized
            continue

        # Add the serialized attribute to the dict of attributes
        serialized_name = serializable_entity.mapping_overwrite.get(
            attr_name, attr_name
        )
        if attr.is_multi():
            attributes[serialized_name] = list(getattr(serializable_entity, attr_name))
        else:
            optional_entity = get_optional_relation(serializable_entity, attr_name)
            if optional_entity is not None:
                attributes[serialized_name] = optional_entity

    return attributes


def serialize(
    serializable_entity: SerializableEntity,
) -> SerializedEntity | None:
    """
    Serialize a serializable entity instance.  Return it as a dict containing
    the path leading to this value, the value, and the operation to use
    when updating the value.

    If the entity is not managed, None is returned instead.

    When the operation is "replace", the serialized value will also contain all the child instances.
    When the operation is "merge", the serialized value only contains the attributes of this instance.
    When the operation is "remove", the serialized value is None as we don't need to know its content.

    :param serializable_entity: An instance of a serializable entity.
    """
    if not serializable_entity.managed:
        # The entity is not managed, no need to serialize it
        return None

    current_operation = serializable_entity._operation

    if current_operation == Operation.REMOVE:
        # No need to serialize, we remove it anyway
        value = None
    elif current_operation == Operation.MERGE:
        # Merge, drop all optionals, these are value we don't care about
        value = {
            attr: value
            for attr, value in get_instance_attributes(
                serializable_entity,
                serializable_entity_type=serializable_entity._type(),
            ).items()
            if value is not None
        }
    elif current_operation == Operation.REPLACE:
        value = get_instance_attributes(
            serializable_entity,
            serializable_entity_type=serializable_entity._type(),
        )

        # This is a replace operation, we also need to fetch all the
        # child instances
        for attr_name, instances in get_child_instances(
            serializable_entity,
            serializable_entity_type=serializable_entity._type(),
        ).items():
            if isinstance(instances, list):
                value[attr_name] = [
                    serialized.value
                    for instance in instances
                    if (serialized := serialize(instance)) is not None
                ]
            else:
                serialized = serialize(instances)
                if serialized is not None:
                    value[attr_name] = serialized.value
    else:
        raise ValueError(f"Unexpected operation: {current_operation}")

    return SerializedEntity(
        path=serializable_entity.path,
        operation=current_operation,
        value=value,
    )


@inmanta.plugins.plugin("serialize")
def serialize_plugin(
    serializable_entity: SerializableEntity,
) -> dict | None:  # TODO: https://github.com/edvgui/inmanta-module-files/issues/136
    return asdict(serialize(serializable_entity))


def serialize_for_resource(
    serializable_entity: SerializableEntity,
    resource: JsonResource,
) -> list[SerializedEntity]:
    """
    Go through the serializable entity tree, and return a list of all
    the serialized entities which are attached to the given resource.

    :param serializable_entity: An entity tree that can be serialized.
    :param resource: The resource that might be attached to some elements
        of the tree.
    """
    current_resource = serializable_entity._resource
    current_operation = serializable_entity._operation

    if current_operation == Operation.REPLACE:
        # A replace tree is not shared, if the resource is not
        # attached to this entity, it won't be attached lower
        # in the tree either
        if current_resource == resource:
            serialized = serialize(serializable_entity)
            return [serialized] if serialized is not None else []
        else:
            return []

    if current_operation == Operation.REMOVE:
        if current_resource == resource:
            # This entity is deleted and attached to our resource, we don't
            # need to look further in the tree for other deleted elements
            serialized = serialize(serializable_entity)
            return [serialized] if serialized is not None else []
        else:
            # We still try to see if our resource is supposed to delete some
            # part of the config before this entity is deleted
            serialized: list[SerializedEntity | None] = []
            for _, instances in get_child_instances(
                serializable_entity,
                serializable_entity_type=serializable_entity._type(),
            ).items():
                if isinstance(instances, list):
                    for instance in instances:
                        serialized.extend(
                            serialize_for_resource(
                                instance,
                                resource,
                            ),
                        )
                else:
                    serialized.extend(
                        serialize_for_resource(
                            instances,
                            resource,
                        ),
                    )

            return [s for s in serialized if s is not None]

    if current_operation == Operation.MERGE:
        # For a merge operation, we might share a part of the tree with any
        # other resource, we take what we can at ever level and group them
        # in a list
        serialized: list[SerializedEntity | None] = []
        if current_resource == resource:
            serialized.append(serialize(serializable_entity))

        for _, instances in get_child_instances(
            serializable_entity,
            serializable_entity_type=serializable_entity._type(),
        ).items():
            if isinstance(instances, list):
                for instance in instances:
                    serialized.extend(
                        serialize_for_resource(
                            instance,
                            resource,
                        ),
                    )
            else:
                serialized.extend(
                    serialize_for_resource(
                        instances,
                        resource,
                    ),
                )

        return [s for s in serialized if s is not None]

    raise ValueError(f"Unexpected operation: {current_operation}")


@inmanta.plugins.plugin("serialize_for_resource")
def serialize_for_resource_plugin(
    serializable_entity: SerializableEntity,
    resource: JsonResource,
) -> list[dict]:  # TODO: https://github.com/edvgui/inmanta-module-files/issues/136
    return [asdict(s) for s in serialize_for_resource(serializable_entity, resource)]


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

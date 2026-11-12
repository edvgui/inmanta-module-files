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

import re

import inmanta.agent.handler
import inmanta.resources
import inmanta_plugins.files.base
from inmanta.plugins import PluginException, plugin
from inmanta.references import Reference

DIRECTIVE_NAME = re.compile(r"[A-Z][A-Za-z0-9]*")
ENVIRONMENT_VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _to_str(value: str | int | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _check_directive(directive: str) -> None:
    if not DIRECTIVE_NAME.fullmatch(directive):
        raise PluginException(f"Invalid systemd directive name: {directive!r}")


def _single_line(value: str, directive: str) -> str:
    if "\n" in value or "\r" in value:
        raise PluginException(
            f"Value of {directive}= can not contain a newline: {value!r}"
        )
    return value


@plugin
def raw(
    value: str | int | bool | Reference[str], directive: str
) -> str | Reference[str]:
    """
    Make sure the value can be written as is after the "<directive>=" key of a
    systemd unit file line, convert it to its string representation.  A value
    containing a newline or ending with a backslash can not be represented
    this way (the line would be broken or merged with the next one), it is
    rejected.  Specifiers (e.g. "%n") are left untouched, cf. escape_specifiers.

    References are returned as is, they can only be checked once resolved.

    :param value: The value to write in the unit file.
    :param directive: The name of the directive the value is assigned to.
    """
    _check_directive(directive)
    if isinstance(value, Reference):
        return value

    value = _single_line(_to_str(value), directive)
    if value.endswith("\\"):
        raise PluginException(
            f"Value of {directive}= can not end with a backslash: {value!r}"
        )
    return value


@plugin
def word(value: str, directive: str) -> str:
    """
    Same as raw, for an item of a space-separated list of a directive which
    supports quoting (e.g. RequiresMountsFor=).  The item is quoted if it
    contains whitespace, quotes or backslashes.

    :param value: The list item to write in the unit file.
    :param directive: The name of the directive the value is assigned to.
    """
    _check_directive(directive)
    value = _single_line(value, directive)
    if any(c.isspace() or c in "\"'\\" for c in value):
        return quote(value)
    return value


@plugin
def environment(name: str, value: str | int | bool) -> str:
    """
    Build the quoted value of an Environment= directive, assigning the
    given value to the given variable name.

    :param name: The name of the environment variable.
    :param value: The value of the environment variable.
    """
    if not ENVIRONMENT_VARIABLE_NAME.fullmatch(name):
        raise PluginException(f"Invalid environment variable name: {name!r}")
    return quote(name + "=" + _to_str(value))


@plugin
def escape_specifiers(value: str) -> str:
    """
    Escape the "%" characters of the value, so that systemd doesn't resolve
    them as specifiers (e.g. "%n", "%t") and uses the value literally.

    cf. https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html#Specifiers

    :param value: The value which should be used literally in a unit file.
    """
    return value.replace("%", "%%")


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
    unit context where quoting is allowed.  Specifiers (e.g. "%n") are still resolved
    by systemd, use escape_specifiers to use a "%" literally.
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

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
from collections.abc import Sequence

import inmanta.agent.handler
import inmanta.resources
import inmanta_plugins.files.base
from inmanta.plugins import PluginException, plugin
from inmanta.references import Reference, reference
from inmanta_plugins.files import register_reference

DIRECTIVE_NAME = re.compile(r"[A-Z][A-Za-z0-9]*")
ENVIRONMENT_VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _to_str(value: str | int | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _single_line(value: str, directive: str) -> str:
    if "\n" in value or "\r" in value:
        raise PluginException(
            f"Value of {directive}= can not contain a newline: {value!r}"
        )
    return value


def _raw(value: str, directive: str) -> str:
    if _single_line(value, directive).endswith("\\"):
        raise PluginException(
            f"Value of {directive}= can not end with a backslash: {value!r}"
        )
    return value


def _word(value: str, directive: str) -> str:
    if any(c.isspace() or c in "\"'\\" for c in _single_line(value, directive)):
        return quote(value)
    return value


@plugin
def directive(
    key: str,
    value: (
        str
        | int
        | bool
        | Reference[str]
        | Sequence[str | int | bool | Reference[str]]
        | None
    ),
    *,
    joined: bool = False,
    quoted: bool = False,
) -> str:
    """
    Build the "<key>=<value>" lines of a directive of a systemd unit file, to
    be used in a template, e.g. {{- "After" | files.systemd_unit.directive(unit.after) }}

    Each line is prefixed with a newline, so that the calls can be chained
    without leaving empty lines.  A none value or an empty list produces no line.

    Values are written as is, specifiers (e.g. "%n") are left untouched (cf.
    escape_specifiers).  A value containing a newline or ending with a
    backslash can not be represented in a unit file (the line would be broken
    or merged with the next one), it is rejected.

    Reference values are not checked, they are added to the template as
    references to resolve at deploy time, which requires the template to be
    rendered with the files::jinja plugin.

    :param key: The name of the directive.
    :param value: The value of the directive, or a list of values to write
        one line per value.
    :param joined: Write all the values of the list on a single line,
        separated by spaces, e.g. for Documentation=.
    :param quoted: For a joined directive which supports quoting (e.g.
        RequiresMountsFor=), quote the values that contain whitespace,
        quotes or backslashes.
    """
    if not DIRECTIVE_NAME.fullmatch(key):
        raise PluginException(f"Invalid systemd directive name: {key!r}")

    if value is None:
        return ""

    items = [value] if isinstance(value, str | int | bool | Reference) else list(value)
    check = _word if quoted else _raw
    lines = [
        (
            register_reference(item)
            if isinstance(item, Reference)
            else check(_to_str(item), key)
        )
        for item in items
    ]
    if joined:
        return f"\n{key}=" + " ".join(lines)
    return "".join(f"\n{key}={line}" for line in lines)


@plugin
def environment(name: str, value: str | int | bool | Reference[str]) -> str:
    """
    Build the quoted value of an Environment= directive, assigning the
    given value to the given variable name.

    A reference value is escaped once resolved, which requires the template
    to be rendered with the files::jinja plugin.

    :param name: The name of the environment variable.
    :param value: The value of the environment variable.
    """
    if not ENVIRONMENT_VARIABLE_NAME.fullmatch(name):
        raise PluginException(f"Invalid environment variable name: {name!r}")
    if isinstance(value, Reference):
        return '"' + _escape(name + "=") + _escape_reference(value) + '"'
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


def _escape(s: str) -> str:
    escaped_chars = {
        "\\": "\\\\",
        "\n": "\\n",
        "\r": "\\r",
        '"': '\\"',
    }
    for k, v in escaped_chars.items():
        s = s.replace(k, v)
    return s


@reference("files::SystemdEscapeReference")
class SystemdEscapeReference(Reference[str]):
    """
    Reference to a value escaped to be placed between double quotes in a
    systemd unit file, cf. quote.
    """

    def __init__(self, value: str | Reference[str]) -> None:
        super().__init__()
        self.value = value

    def resolve(self, logger: inmanta.agent.handler.LoggerABC) -> str:
        return _escape(self.resolve_other(self.value, logger))


def _escape_reference(value: Reference[str]) -> str:
    # Emits the escaped reference in the template being rendered by files::jinja
    return register_reference(SystemdEscapeReference(value))


@plugin
def quote(s: str | Reference[str]) -> str:
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

    A reference is escaped once resolved, which requires the template to be
    rendered with the files::jinja plugin.
    """
    if isinstance(s, Reference):
        return '"' + _escape_reference(s) + '"'
    return '"' + _escape(s) + '"'


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

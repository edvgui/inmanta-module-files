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
import copy
import ipaddress
import typing

import inmanta.agent.agent
import inmanta.agent.handler
import inmanta.agent.io.local
import inmanta.const
import inmanta.execute.proxy
import inmanta.export
import inmanta.resources
import inmanta_plugins.files.base
import inmanta_plugins.files.json
from inmanta.util import dict_path

PARSED_HOST_FILE = dict[str, dict[str, typing.Optional[str]]]
"""
Define an alias for the parsed host file format.  The parsed host file
is a dict which has as keys, hostnames, and as values, a dict containing
two keys: address4 and address6, respectively representing the ipv4 and
ipv6 address defined in the file for the given hostname.

And example of such dict is:

.. code-block::python

    {
        "localhost": {"address4": "127.0.0.1", "address6": "::1"},
        "ip6-loopback": {"address4": None, "address6": "::1"},
        "example.com": {"address4": "93.184.216.34", "address6": None},
    }

"""


def parse_host_file(raw_content: str) -> PARSED_HOST_FILE:
    """
    Parse the content of an host file, and return it as a dict having as
    keys the hostnames, and as values, the corresponding ips.
    """
    parsed_file: PARSED_HOST_FILE = collections.defaultdict(
        lambda: {
            "address4": None,
            "address6": None,
        }
    )

    for line in raw_content.splitlines():
        if not line:
            # No info in that line
            continue

        if line[0] not in "0123456789:":
            # This is not a valid entry, probably a comment
            continue

        parsed_line = line.split()

        # Parse the ip address
        address = ipaddress.ip_address(parsed_line[0])

        # For each hostname, add the ipaddress to the parsed file
        for hostname in parsed_line[1:]:
            parsed_file[hostname][f"address{address.version}"] = str(address)

    return dict(parsed_file)


def write_host_file(parsed_file: PARSED_HOST_FILE) -> str:
    """
    Generate the content of the hostfile in a single string, that
    can be written down into a file.
    """
    raw_content = ""

    for hostname, addresses in parsed_file.items():
        if addresses.get("address4") is not None:
            raw_content += f"{addresses['address4']} {hostname}\n"

        if addresses.get("address6") is not None:
            raw_content += f"{addresses['address6']} {hostname}\n"

    return raw_content


@inmanta.resources.resource(
    name="files::HostFile",
    id_attribute="path",
    agent="host.name",
)
class HostFileResource(inmanta_plugins.files.base.BaseFileResource):
    fields = ("values",)
    values: list[dict]

    @classmethod
    def get_values(
        cls,
        _: inmanta.export.Exporter,
        entity: inmanta.execute.proxy.DynamicProxy,
    ) -> list[dict]:
        return [
            {
                "path": str(dict_path.InDict(entry.hostname)),
                "operation": entry.operation,
                "value": {
                    "address4": entry.address4,
                    "address6": entry.address6,
                },
            }
            for entry in entity.entries
        ]


@inmanta.agent.handler.provider("files::HostFile", "")
class HostFileHandler(inmanta_plugins.files.base.BaseFileHandler[HostFileResource]):
    _io: inmanta.agent.io.local.LocalIO

    def read_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: HostFileResource
    ) -> None:
        super().read_resource(ctx, resource)

        # Load the content of the existing file
        raw_content = self._io.read_binary(resource.path).decode()
        ctx.debug("Reading existing file", raw_content=raw_content)
        ctx.set("current_content", parse_host_file(raw_content))

    def calculate_diff(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        current: HostFileResource,
        desired: HostFileResource,
    ) -> dict[str, dict[str, object]]:
        # For file permissions and ownership, we delegate to the parent class
        changes = super().calculate_diff(ctx, current, desired)

        # To check if some change content needs to be applied, we perform a "stable" addition
        # operation: We apply our desired state to the current state, and check if we can then
        # see any difference.
        current_content = ctx.get("current_content")
        desired_content = copy.deepcopy(current_content)

        for value in desired.values:
            inmanta_plugins.files.json.update(
                desired_content,
                dict_path.to_path(value["path"]),
                inmanta_plugins.files.json.Operation(value["operation"]),
                value["value"],
            )

        if current_content != desired_content:
            changes["content"] = {
                "current": current_content,
                "desired": desired_content,
            }

        return changes

    def create_resource(
        self, ctx: inmanta.agent.handler.HandlerContext, resource: HostFileResource
    ) -> None:
        # Build a config based on all the values we want to manage
        content = {}
        for value in resource.values:
            inmanta_plugins.files.json.update(
                content,
                dict_path.to_path(value["path"]),
                inmanta_plugins.files.json.Operation(value["operation"]),
                value["value"],
            )
        raw_content = write_host_file(content)
        self._io.put(resource.path, raw_content.encode())
        super().create_resource(ctx, resource)

    def update_resource(
        self,
        ctx: inmanta.agent.handler.HandlerContext,
        changes: dict[str, dict[str, object]],
        resource: HostFileResource,
    ) -> None:
        if "content" in changes:
            raw_content = write_host_file(changes["content"]["desired"])
            self._io.put(resource.path, raw_content.encode())

        super().update_resource(ctx, changes, resource)

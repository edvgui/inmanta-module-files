"""
Copyright 2026 Guillaume Everarts de Velp

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
import json
import os
import pathlib

import pytest
import pytest_inmanta.plugin

# A json file resource that also extends files::json::JsonResource: the attached
# SerializableEntity tree is serialized into the file managed by the resource.
# This is the same pattern as the gatus config file of the misty module.
TYPE_DEFINITION = """
import files
import files::json


entity ConfigFile extends files::json::JsonResource, files::SharedJsonFile:
    \"\"\"
    A configuration file whose desired state is the serialized version of the
    attached Config entity tree.  The exported resource is a files::SharedJsonFile.
    \"\"\"
end
ConfigFile.config [1] -- Config


entity Config extends files::json::SerializableEntity:
    string path = "."
end
Config.endpoints [0:] -- Endpoint.parent [1]


entity Endpoint extends files::json::SerializableEntity:
    string name
    string url
    string? group = null
end
index Endpoint(parent, name)


implementation config_file for ConfigFile:
    # Connect the root of the configuration tree, and attach it to this resource
    self.root = self.config
    self.root.resource = self
end


implement ConfigFile using config_file, parents
implement Config using parents
implement Endpoint using parents
"""


def test_model(
    project: pytest_inmanta.plugin.Project,
    file_path: pathlib.Path = pathlib.Path("/tmp/example"),
    purged: bool = False,
    serialize: bool = True,
) -> None:
    user = os.getlogin()
    group = grp.getgrgid(os.getgid()).gr_name

    model = TYPE_DEFINITION + f"""
        import mitogen
        import std

        host = std::Host(
            name="localhost",
            os=std::linux,
            via=mitogen::Local(),
        )

        ConfigFile(
            host=host,
            path={repr(str(file_path))},
            owner={repr(user)},
            group={repr(group)},
            purged={str(purged).lower()},
            serialize={str(serialize).lower()},
            config=Config(
                endpoints=[
                    Endpoint(name="bob", url="https://bob.example.com"),
                    Endpoint(
                        name="alice",
                        url="https://alice.example.com",
                        group="friends",
                    ),
                ],
            ),
        )
    """

    project.compile(model, no_dedent=False)

    # The serialized entities are only materialized in the model when the
    # serialization is requested to happen in-compile.
    resource = project.get_instances("__config__::ConfigFile")[0]
    if serialize:
        serialized = {s.path: dict(s.value) for s in resource.serialized}
        assert serialized == {
            ".": {},
            "endpoints[name=alice]": {
                "name": "alice",
                "url": "https://alice.example.com",
                "group": "friends",
            },
            "endpoints[name=bob]": {
                "name": "bob",
                "url": "https://bob.example.com",
            },
        }
    else:
        assert list(resource.serialized) == []


@pytest.mark.parametrize("serialize", [True, False])
def test_deploy(
    project: pytest_inmanta.plugin.Project,
    tmp_path: pathlib.Path,
    serialize: bool,
) -> None:
    file = tmp_path / "config.json"

    def read_endpoints() -> list[dict]:
        return json.loads(file.read_text())["endpoints"]

    expected = [
        {"name": "bob", "url": "https://bob.example.com"},
        {"name": "alice", "url": "https://alice.example.com", "group": "friends"},
    ]

    def assert_endpoints() -> None:
        endpoints = read_endpoints()
        assert sorted(endpoints, key=lambda e: e["name"]) == sorted(
            expected, key=lambda e: e["name"]
        )

    # Create the file out of the serialized config tree.  Whether the
    # serialization happens in-compile (serialize=true) or at export time
    # (serialize=false), the deployed content is identical.
    test_model(project, file, purged=False, serialize=serialize)
    assert project.dryrun_resource("__config__::ConfigFile", uri=str(file))
    project.deploy_resource("__config__::ConfigFile", uri=str(file))
    assert_endpoints()
    assert not project.dryrun_resource("__config__::ConfigFile", uri=str(file))

    # Drop a managed endpoint from the file and make sure we detect and fix it
    content = json.loads(file.read_text())
    content["endpoints"] = [e for e in content["endpoints"] if e["name"] != "alice"]
    file.write_text(json.dumps(content))
    assert project.dryrun_resource("__config__::ConfigFile", uri=str(file))
    project.deploy_resource("__config__::ConfigFile", uri=str(file))
    assert_endpoints()
    assert not project.dryrun_resource("__config__::ConfigFile", uri=str(file))

    # An unmanaged endpoint left in the file is not touched
    content = json.loads(file.read_text())
    content["endpoints"].append({"name": "eve", "url": "https://eve.example.com"})
    file.write_text(json.dumps(content))
    assert not project.dryrun_resource("__config__::ConfigFile", uri=str(file))

    # Delete the file
    test_model(project, file, purged=True, serialize=serialize)
    assert project.dryrun_resource("__config__::ConfigFile", uri=str(file))
    project.deploy_resource("__config__::ConfigFile", uri=str(file))
    assert not file.exists()
    assert not project.dryrun_resource("__config__::ConfigFile", uri=str(file))

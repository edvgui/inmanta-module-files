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

import grp
import json
import os
import pathlib

import pytest_inmanta.plugin


def test_model(
    project: pytest_inmanta.plugin.Project,
    file_path: pathlib.Path = pathlib.Path("/tmp/example"),
    purged: bool = False,
    format: str = "json",
) -> None:
    user = os.getlogin()
    group = grp.getgrgid(os.getgid()).gr_name
    model = f"""
        import files
        import files::json

        import std

        host = std::Host(
            name="localhost",
            os=std::linux,
        )

        files::JsonFile(
            host=host,
            path={repr(str(file_path))},
            owner={repr(user)},
            group={repr(group)},
            purged={str(purged).lower()},
            format={repr(format)},
            values=[
                files::json::Object(
                    path="people[name=bob]",
                    operation="replace",
                    value={{"name": "bob", "age": 20}},
                ),
                files::json::Object(
                    path="people[name=alice]",
                    operation="merge",
                    value={{"name": "alice", "age": 20}},
                ),
                files::json::Object(
                    path="people[name=eve]",
                    operation="remove",
                    value={{}},
                ),
            ],
        )
    """

    project.compile(model.strip("\n"), no_dedent=False)


def test_deploy(project: pytest_inmanta.plugin.Project, tmp_path: pathlib.Path) -> None:
    file = tmp_path / "friends.json"

    # Create the file
    test_model(project, file, purged=False)
    assert project.dryrun_resource("files::JsonFile")
    project.deploy_resource("files::JsonFile")
    assert not project.dryrun_resource("files::JsonFile")

    # Manually remove a managed line from the file and make sure we detect a change
    friends = json.loads(file.read_text())
    del friends["people"]
    file.write_text(json.dumps(friends))
    assert project.dryrun_resource("files::JsonFile")
    project.deploy_resource("files::JsonFile")
    assert not project.dryrun_resource("files::JsonFile")

    # Insert an extra entry in the file and me sure we don't detect any change as we don't
    # manage that entry
    friends = json.loads(file.read_text())
    friends["people"].append({"name": "chris"})
    file.write_text(json.dumps(friends))
    assert not project.dryrun_resource("files::JsonFile")

    # Add the entry that should not be there and make sure it is removed, the unmanaged
    # entry should remain untouched
    friends["people"].append({"name": "eve"})
    file.write_text(json.dumps(friends))
    assert project.dryrun_resource("files::JsonFile")
    project.deploy_resource("files::JsonFile")
    assert not project.dryrun_resource("files::JsonFile")
    friends = json.loads(file.read_text())
    assert friends["people"][2] == {"name": "chris"}
    assert len(friends["people"]) == 3

    # Delete the file
    test_model(project, file, purged=True)
    assert project.dryrun_resource("files::JsonFile")
    project.deploy_resource("files::JsonFile")
    assert not project.dryrun_resource("files::JsonFile")
    assert not file.exists()

    # Create the file as a yaml
    test_model(project, file, purged=False, format="yaml")
    assert project.dryrun_resource("files::JsonFile")
    project.deploy_resource("files::JsonFile")
    assert not project.dryrun_resource("files::JsonFile")

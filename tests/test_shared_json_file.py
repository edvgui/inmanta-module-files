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
    people_fact: str | None = None,
) -> None:
    user = os.getlogin()
    group = grp.getgrgid(os.getgid()).gr_name

    model = f"""
        import mitogen
        import files
        import files::json

        import std

        host = std::Host(
            name="localhost",
            os=std::linux,
            via=mitogen::Local(),
        )

        files::SharedJsonFile(
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
            ],
            discovered_values=[
                files::json::DiscoveredValue(
                    path="people[name=*]",
                ),
            ],
        )

        files::SharedJsonFile(
            host=host,
            path={repr(str(file_path))},
            resource_discriminator="alice",
            purged={str(purged).lower()},
            format={repr(format)},
            values=[
                files::json::Object(
                    path="people[name=alice]",
                    operation="merge",
                    value={{"name": "alice", "age": 20}},
                ),
            ],
            discovered_values=[
                files::json::DiscoveredValue(
                    path="people[name=*]",
                ),
            ],
        )

        files::SharedJsonFile(
            host=host,
            path={repr(str(file_path))},
            resource_discriminator="eve",
            purged={str(purged).lower()},
            format={repr(format)},
            values=[
                files::json::Object(
                    path="people[name=eve]",
                    operation="remove",
                    value={{}},
                ),
            ],
            discovered_values=[
                files::json::DiscoveredValue(
                    path="people[name=*]",
                ),
            ],
        )
    """

    if people_fact is not None:
        # If the fact is set for the people, we add it to the project
        project.add_fact(
            f"files::SharedJsonFile[localhost,uri={file_path}]",
            "people[name=*]",
            people_fact,
        )
        project.add_fact(
            f"files::SharedJsonFile[localhost,uri={file_path}:alice]",
            "people[name=*]",
            people_fact,
        )
        project.add_fact(
            f"files::SharedJsonFile[localhost,uri={file_path}:eve]",
            "people[name=*]",
            people_fact,
        )

    project.compile(model.strip("\n"), no_dedent=False)

    if people_fact is not None:
        # Check that the fact has been assigned in the model
        discovered = project.get_instances("files::json::DiscoveredValue")
        assert len(discovered) == 3
        for discovered_value in discovered:
            assert dict(discovered_value.values) == json.loads(people_fact)


def test_deploy(project: pytest_inmanta.plugin.Project, tmp_path: pathlib.Path) -> None:
    file = tmp_path / "friends.json"
    res_id = f"files::SharedJsonFile[localhost,uri={file}]"
    res_alice_id = f"files::SharedJsonFile[localhost,uri={file}:alice]"
    res_eve_id = f"files::SharedJsonFile[localhost,uri={file}:eve]"

    def get_people_fact_value(resource_id: str) -> dict | None:
        if project.ctx is None:
            # No handler has run, the fact can't be set
            return None

        for fact in project.ctx.facts:
            if fact["resource_id"] != resource_id:
                continue

            if fact["id"] != "people[name=*]":
                continue

            return json.loads(fact["value"])

        # No fact matched
        return None

    # Create the file
    test_model(project, file, purged=False)
    assert project.dryrun_resource("files::SharedJsonFile", uri=str(file))
    assert get_people_fact_value(res_id) is None
    assert project.dryrun_resource("files::SharedJsonFile", uri=f"{file}:alice")
    assert get_people_fact_value(res_alice_id) is None
    assert project.dryrun_resource("files::SharedJsonFile", uri=f"{file}:eve")
    assert get_people_fact_value(res_eve_id) is None

    project.deploy_resource("files::SharedJsonFile", uri=str(file))
    assert get_people_fact_value(res_id) == {
        "people[name=bob]": {"name": "bob", "age": 20},
    }
    project.deploy_resource("files::SharedJsonFile", uri=f"{file}:alice")
    assert get_people_fact_value(res_alice_id) == {
        "people[name=bob]": {"name": "bob", "age": 20},
        "people[name=alice]": {"name": "alice", "age": 20},
    }
    assert not project.dryrun_resource("files::SharedJsonFile", uri=f"{file}:eve")
    assert get_people_fact_value(res_eve_id) == {
        "people[name=bob]": {"name": "bob", "age": 20},
        "people[name=alice]": {"name": "alice", "age": 20},
    }

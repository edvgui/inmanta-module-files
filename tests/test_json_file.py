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

import pytest
import pytest_inmanta.plugin


def test_model(
    project: pytest_inmanta.plugin.Project,
    file_path: pathlib.Path = pathlib.Path("/tmp/example"),
    purged: bool = False,
    format: str = "json",
    people_fact: str | None = None,
    named_list: bool = False,
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

        files::JsonFile(
            host=host,
            path={repr(str(file_path))},
            owner={repr(user)},
            group={repr(group)},
            purged={str(purged).lower()},
            format={repr(format)},
            named_list={repr("people") if named_list else "null"},
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
            f"files::JsonFile[localhost,path={file_path}]",
            "people[name=*]",
            people_fact,
        )

    project.compile(model.strip("\n"), no_dedent=False)

    if people_fact is not None:
        # Check that the fact has been assigned in the model
        discovered = project.get_instances("files::json::DiscoveredValue")
        assert len(discovered) == 1
        assert dict(discovered[0].values) == json.loads(people_fact)


@pytest.mark.parametrize("named_list", [True, False])
def test_deploy(
    project: pytest_inmanta.plugin.Project, tmp_path: pathlib.Path, named_list: bool
) -> None:
    file = tmp_path / "friends.json"
    res_id = f"files::JsonFile[localhost,path={file}]"

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
    test_model(project, file, purged=False, named_list=named_list)
    assert project.dryrun_resource("files::JsonFile")
    assert get_people_fact_value(res_id) is None
    project.deploy_resource("files::JsonFile")
    assert get_people_fact_value(res_id) == {
        "people[name=bob]": {"name": "bob", "age": 20},
        "people[name=alice]": {"name": "alice", "age": 20},
    }
    assert not project.dryrun_resource("files::JsonFile")
    assert get_people_fact_value(res_id) == {
        "people[name=bob]": {"name": "bob", "age": 20},
        "people[name=alice]": {"name": "alice", "age": 20},
    }

    # Manually remove a managed line from the file and make sure we detect a change
    friends = json.loads(file.read_text())
    if named_list:
        friends = []
    else:
        del friends["people"]
    file.write_text(json.dumps(friends))
    assert project.dryrun_resource("files::JsonFile")
    assert get_people_fact_value(res_id) == {}
    project.deploy_resource("files::JsonFile")
    assert get_people_fact_value(res_id) == {
        "people[name=bob]": {"name": "bob", "age": 20},
        "people[name=alice]": {"name": "alice", "age": 20},
    }
    assert not project.dryrun_resource("files::JsonFile")
    assert get_people_fact_value(res_id) == {
        "people[name=bob]": {"name": "bob", "age": 20},
        "people[name=alice]": {"name": "alice", "age": 20},
    }

    # Insert an extra entry in the file and me sure we don't detect any change as we don't
    # manage that entry
    friends = json.loads(file.read_text())
    people = friends if named_list else friends["people"]
    people.append({"name": "chris"})
    file.write_text(json.dumps(friends))
    assert not project.dryrun_resource("files::JsonFile")
    assert get_people_fact_value(res_id) == {
        "people[name=bob]": {"name": "bob", "age": 20},
        "people[name=alice]": {"name": "alice", "age": 20},
        "people[name=chris]": {"name": "chris"},
    }

    # Add the entry that should not be there and make sure it is removed, the unmanaged
    # entry should remain untouched
    people.append({"name": "eve"})
    file.write_text(json.dumps(friends))
    assert project.dryrun_resource("files::JsonFile")
    assert get_people_fact_value(res_id) == {
        "people[name=bob]": {"name": "bob", "age": 20},
        "people[name=alice]": {"name": "alice", "age": 20},
        "people[name=chris]": {"name": "chris"},
        "people[name=eve]": {"name": "eve"},
    }
    project.deploy_resource("files::JsonFile")
    assert get_people_fact_value(res_id) == {
        "people[name=bob]": {"name": "bob", "age": 20},
        "people[name=alice]": {"name": "alice", "age": 20},
        "people[name=chris]": {"name": "chris"},
    }
    assert not project.dryrun_resource("files::JsonFile")
    friends = json.loads(file.read_text())
    people = friends if named_list else friends["people"]
    assert people[2] == {"name": "chris"}
    assert len(people) == 3

    # Delete the file
    test_model(
        project,
        file,
        purged=True,
        people_fact=json.dumps(
            {
                "people[name=bob]": {"name": "bob", "age": 20},
                "people[name=alice]": {"name": "alice", "age": 20},
                "people[name=chris]": {"name": "chris"},
            },
        ),
        named_list=named_list,
    )
    assert project.dryrun_resource("files::JsonFile")
    assert get_people_fact_value(res_id) == {
        "people[name=bob]": {"name": "bob", "age": 20},
        "people[name=alice]": {"name": "alice", "age": 20},
        "people[name=chris]": {"name": "chris"},
    }
    project.deploy_resource("files::JsonFile")
    assert get_people_fact_value(res_id) is None
    assert not project.dryrun_resource("files::JsonFile")
    assert not file.exists()

    # Create the file as a yaml
    test_model(project, file, purged=False, format="yaml", named_list=named_list)
    assert project.dryrun_resource("files::JsonFile")
    project.deploy_resource("files::JsonFile")
    assert not project.dryrun_resource("files::JsonFile")

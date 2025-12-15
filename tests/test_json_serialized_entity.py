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

import pytest_inmanta.plugin

from inmanta_plugins.files.json import (
    Operation,
    SerializedEntity,
    serialize,
    serialize_for_resource,
)

TYPE_DEFINITION = """
import files
import files::json

entity Test extends files::json::SerializableEntity:
    string name
    int? count = 0
    bool? flag = false
    dict? attr = {}
    files::operation_t? operation = "merge"
end
Test.optional [0:1] -- OptionalEmbeddedTest.parent [1]
Test.required [1] -- RequiredEmbeddedTest.parent [1]
Test.many [0:] -- ManyEmbeddedTest.parent [1]

entity EmbeddedTestABC extends files::json::SerializableEntity:
    string? name
    int? count = 0
    bool? flag = false
    dict? attr = {}
end
EmbeddedTestABC.recursive [0:] -- RecursiveEmbeddedTest.parent [1]

entity RecursiveEmbeddedTest extends EmbeddedTestABC:
end

index RecursiveEmbeddedTest(parent, name)

entity OptionalEmbeddedTest extends EmbeddedTestABC:
end

index OptionalEmbeddedTest(parent)

entity RequiredEmbeddedTest extends EmbeddedTestABC:
end

index RequiredEmbeddedTest(parent)

entity ManyEmbeddedTest extends EmbeddedTestABC:
end

index ManyEmbeddedTest(parent, name)

implement Test using parents
implement EmbeddedTestABC using parents
implement RecursiveEmbeddedTest using parents
implement OptionalEmbeddedTest using parents
implement RequiredEmbeddedTest using parents
implement ManyEmbeddedTest using parents
"""


def test_replace(
    project: pytest_inmanta.plugin.Project,
) -> None:
    model = """
a = Test(
    name="test",
    required=RequiredEmbeddedTest(
        name="required",
        recursive=[
            RecursiveEmbeddedTest(
                name="a",
                recursive=RecursiveEmbeddedTest(
                    name="a",
                ),
            ),
        ],
    ),
    optional=OptionalEmbeddedTest(
        name="optional",
    ),
    many=[
        ManyEmbeddedTest(
            name="a",
        ),
        ManyEmbeddedTest(
            name="b",
        ),
    ],
    path=".",
    operation=files::replace,
    resource=files::json::JsonResource(),
)
"""

    project.compile(TYPE_DEFINITION + model)

    instance = project.get_instances("__config__::Test")[0]
    assert serialize(instance) == SerializedEntity(
        path=".",
        operation=Operation.REPLACE,
        value={
            "name": "test",
            "count": 0,
            "flag": False,
            "attr": {},
            "required": {
                "name": "required",
                "count": 0,
                "flag": False,
                "attr": {},
                "recursive": [
                    {
                        "name": "a",
                        "count": 0,
                        "flag": False,
                        "attr": {},
                        "recursive": [
                            {
                                "name": "a",
                                "count": 0,
                                "flag": False,
                                "attr": {},
                                "recursive": [],
                            },
                        ],
                    },
                ],
            },
            "optional": {
                "name": "optional",
                "count": 0,
                "flag": False,
                "attr": {},
                "recursive": [],
            },
            "many": [
                {
                    "name": "a",
                    "count": 0,
                    "flag": False,
                    "attr": {},
                    "recursive": [],
                },
                {
                    "name": "b",
                    "count": 0,
                    "flag": False,
                    "attr": {},
                    "recursive": [],
                },
            ],
        },
    )


def test_merge(project: pytest_inmanta.plugin.Project) -> None:
    model = """
res_a = files::json::JsonResource()
res_b = files::json::JsonResource()

a = Test(
    name="test",
    required=RequiredEmbeddedTest(
        name="required",
        count=null,
        recursive=[
            RecursiveEmbeddedTest(
                name="a",
                recursive=RecursiveEmbeddedTest(
                    name="a",
                ),
            ),
        ],
        resource=res_a,
        operation=files::replace,
    ),
    optional=OptionalEmbeddedTest(
        name="optional",
        flag=null,
    ),
    many=[
        ManyEmbeddedTest(
            name="a",
            attr=null,
            resource=res_a,
        ),
        ManyEmbeddedTest(
            name="b",
        ),
    ],
    path=".",
    operation=files::merge,
    resource=res_b,
)
"""

    project.compile(TYPE_DEFINITION + model)

    instance = project.get_instances("__config__::Test")[0]
    res_a = instance.required.resource
    res_b = instance.resource

    assert serialize_for_resource(
        instance,
        res_a,
    ) == [
        SerializedEntity(
            operation=Operation.REPLACE,
            path="required",
            value={
                "attr": {},
                "count": None,
                "flag": False,
                "name": "required",
                "recursive": [
                    {
                        "attr": {},
                        "count": 0,
                        "flag": False,
                        "name": "a",
                        "recursive": [
                            {
                                "attr": {},
                                "count": 0,
                                "flag": False,
                                "name": "a",
                                "recursive": [],
                            },
                        ],
                    },
                ],
            },
        ),
        SerializedEntity(
            operation=Operation.MERGE,
            path="many[name=a]",
            value={
                "count": 0,
                "flag": False,
                "name": "a",
            },
        ),
    ]

    assert serialize_for_resource(
        instance,
        res_b,
    ) == [
        SerializedEntity(
            operation=Operation.MERGE,
            path=".",
            value={
                "attr": {},
                "count": 0,
                "flag": False,
                "name": "test",
            },
        ),
        SerializedEntity(
            operation=Operation.MERGE,
            path="optional",
            value={
                "attr": {},
                "count": 0,
                "name": "optional",
            },
        ),
        SerializedEntity(
            operation=Operation.MERGE,
            path="many[name=b]",
            value={
                "attr": {},
                "count": 0,
                "flag": False,
                "name": "b",
            },
        ),
    ]


def test_remove(project: pytest_inmanta.plugin.Project) -> None:
    model = """
res_a = files::json::JsonResource()
res_b = files::json::JsonResource()

a = Test(
    name="test",
    required=RequiredEmbeddedTest(
        name="required",
        count=null,
        recursive=[
            RecursiveEmbeddedTest(
                name="a",
                recursive=RecursiveEmbeddedTest(
                    name="a",
                ),
            ),
        ],
        resource=res_a,
        operation=files::remove,
    ),
    optional=OptionalEmbeddedTest(
        name="optional",
        flag=null,
        recursive=[
            RecursiveEmbeddedTest(
                name=null,
                recursive=RecursiveEmbeddedTest(
                    name="a",
                    resource=res_b,
                ),
                resource=res_a,
                operation=files::remove,
            ),
        ],
    ),
    many=[
        ManyEmbeddedTest(
            name="a",
            attr=null,
            resource=res_a,
        ),
        ManyEmbeddedTest(
            name="b",
        ),
    ],
    path=".",
    operation=files::merge,
    resource=res_b,
)
"""

    project.compile(TYPE_DEFINITION + model)

    instance = project.get_instances("__config__::Test")[0]
    res_a = instance.required.resource
    res_b = instance.resource

    assert serialize_for_resource(
        instance,
        res_a,
    ) == [
        SerializedEntity(
            operation=Operation.REMOVE,
            path=r"optional.recursive[name=\0]",
            value=None,
        ),
        SerializedEntity(
            operation=Operation.REMOVE,
            path="required",
            value=None,
        ),
        SerializedEntity(
            operation=Operation.MERGE,
            path="many[name=a]",
            value={
                "count": 0,
                "flag": False,
                "name": "a",
            },
        ),
    ]

    assert serialize_for_resource(
        instance,
        res_b,
    ) == [
        SerializedEntity(
            operation=Operation.MERGE,
            path=".",
            value={
                "attr": {},
                "count": 0,
                "flag": False,
                "name": "test",
            },
        ),
        SerializedEntity(
            operation=Operation.MERGE,
            path="optional",
            value={
                "attr": {},
                "count": 0,
                "name": "optional",
            },
        ),
        SerializedEntity(
            operation=Operation.REMOVE,
            path=r"optional.recursive[name=\0].recursive[name=a]",
            value=None,
        ),
        SerializedEntity(
            operation=Operation.MERGE,
            path="many[name=b]",
            value={
                "attr": {},
                "count": 0,
                "flag": False,
                "name": "b",
            },
        ),
    ]

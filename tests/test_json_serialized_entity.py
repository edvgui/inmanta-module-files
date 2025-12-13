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
from inmanta_plugins.files.json import serialize


TYPE_DEFINITION = """
import files::json

entity Test extends files::json::SerializableEntity:
    string name
    int count = 0
    bool flag = false
    dict attr = {}
end
Test.optional [0:1] -- OptionalEmbeddedTest.parent [1]
Test.required [1] -- RequiredEmbeddedTest.parent [1]
Test.many [0:] -- ManyEmbeddedTest.parent [1]

entity EmbeddedTestABC extends files::json::SerializableEntity:
    string name
    int count = 0
    bool flag = false
    dict attr = {}
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
    operation="replace",
    resource=std::Resource(),
)

implement std::Resource using std::none
    """

    project.compile(TYPE_DEFINITION + model)

    instance = project.get_instances("__config__::Test")[0]
    assert serialize(instance) == {
        "path": ".",
        "operation": "replace",
        "value": {
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
            ]
        },
    }

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

from pytest_inmanta.plugin import Project

import inmanta_plugins.files


def test_basics(project: Project) -> None:
    project.compile("import files")


def test_path_join() -> None:
    assert inmanta_plugins.files.path_join("/test", "a", "b") == "/test/a/b"
    assert inmanta_plugins.files.path_join("/test", "/a", "b") == "/a/b"
    assert inmanta_plugins.files.path_join("test", "a", "b") == "test/a/b"
    assert inmanta_plugins.files.path_join("test/", "a", "b") == "test/a/b"

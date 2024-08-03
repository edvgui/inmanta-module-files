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

import pathlib

import inmanta.plugins


@inmanta.plugins.plugin()
def path_join(base_path: "string", *extra: "string") -> "string":  # type: ignore
    """
    Join together the base_path and all of the extra parts after it.  If any extra
    item specified an absolute path (starts with a '/') it will overwrite all the
    elements of the path before it.

    :param base_path: The base path, a directory, to which should be
        appended all the extra items.
    :param *extra: A set of extra parts to add to the path.
    """
    return str(pathlib.Path(base_path, *extra))

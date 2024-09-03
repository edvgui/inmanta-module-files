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

This module contains some code that will be executed on a remote host
using mitogen.
"""

import grp
import os
import pwd


def symlink_stat(path: str) -> dict[str, object]:
    """
    This method is similar to inmanta_mitogen.file_stat excepts that it doesn't
    try to resolve the symlink on the last element of the path (all other
    symlinks will be resolved).

    :param path: The path to stat.
    """
    # Resolve all the symlinks except the one at the end of the path
    parent_path = os.path.abspath(os.path.join(path, os.pardir))
    resolved_parent_path = os.path.realpath(parent_path)

    # Get the stat result for the symlink at the end of the path
    stat_result = os.stat(
        os.path.join(resolved_parent_path, os.path.basename(path)),
        follow_symlinks=False,
    )
    return dict(
        owner=pwd.getpwuid(stat_result.st_uid).pw_name,
        group=grp.getgrgid(stat_result.st_gid).gr_name,
        permissions=int(oct(stat_result.st_mode)[-4:]),
    )

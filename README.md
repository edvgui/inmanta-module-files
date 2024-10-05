# inmanta-module-files

[![pypi version](https://img.shields.io/pypi/v/inmanta-module-files.svg)](https://pypi.python.org/pypi/inmanta-module-files/)
[![build status](https://img.shields.io/github/actions/workflow/status/edvgui/inmanta-module-files/continuous-integration.yml)](https://github.com/edvgui/inmanta-module-files/actions)

This package is an adapter that is meant to be used with the inmanta orchestrator: https://docs.inmanta.com

## Features

This module allows to manage files, on a unix host.  It contains the following resources:
1. `files::Directory`: to manage a directory, its existence, permissions and ownership.
2. `files::TextFile`: to manage a simple text file, its existence, content, permissions and ownership.  This should not be used for big files, as the content of the file is embedded in the resource itself.
3. `files::HostFile`: to manage hosts file entries (i.e. `/etc/hosts`), but allowing the file to be managed by other tools.  The resource makes sure to only modify the entries defined in its desired state and leave the rest untouched.
4. `files::JsonFile` and `files::SharedJsonFile`: to manage json file entries.  Similarly to `files::HostFile`, only change in the file what is present in the desired state.  The file can then still be modified by other tools.
5. `files::SystemdUnitFile`: an entity representing a unit file, which exposes it most useful properties directly in the model.  After being exported, this resource becomes nothing more than a text file.
6. `files::Symlink`: to manage a symlink, its existence and ownership.

## Example

The following example makes sure that the directory `/tmp/test/a` exists, and creates a text file in it.
```
import mitogen
import files

import std

host = std::Host(
    name="localhost",
    os=std::linux,
    via=mitogen::Local(),
)

dir = files::Directory(
    host=host,
    path="/tmp/test/a",
    # The directory that is managed is /tmp/test/a, but the resource
    # will also make sure that any of its parent directories exists as well
    create_parents=true,
)

file = files::TextFile(
    host=host,
    path=f"{dir.path}/file.txt",
    content="test",
    # The file requires the directory to be created first
    requires=[dir],
)
```

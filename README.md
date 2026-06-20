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

It also exposes the `files::jinja` plugin, to render a jinja template at compile time while keeping any [reference](https://docs.inmanta.com/community/dev/lsm/reference/references.html) it embeds (e.g. a secret) unresolved.  Such references are then resolved by the agent, on the host, when the resource is deployed.

## Example

The following example makes sure that the directory `/example/folder/a` exists, and creates a text file in it.

<x-example-simple>

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
    path="/example/folder/a",
    # The directory that is managed is /example/folder/a, but the resource
    # will also make sure that any of its parent directories exists as well
    create_parents=true,
)

file = files::TextFile(
    host=host,
    path=f"{dir.path}/file.txt",
    content="test",
    # No need to explicitly add the dependency to the parent, the
    # exporter takes care of adding it
    # requires=dir,
)

```

</x-example-simple>

### Jinja templates with references

The `files::jinja` plugin renders a template stored in the project's `templates/` folder.  Inputs that are [references](https://docs.inmanta.com/community/dev/lsm/reference/references.html), such as secrets pulled from the environment, are not resolved at compile time: they are kept in the rendered content and resolved by the agent, on the host, when the resource is deployed.

Given the following template, stored in `templates/app.conf.j2`:

```jinja
[database]
password = {{ "APP_PASSWORD" | std.create_environment_reference() }}
```

The following example renders it into a text file, while keeping the password as an environment reference resolved at deploy time:

<x-example-jinja>

```
import mitogen
import files

import std

host = std::Host(
    name="localhost",
    os=std::linux,
    via=mitogen::Local(),
)

file = files::TextFile(
    host=host,
    path="/example/folder/app.conf",
    # The template is rendered at compile time, but the password, which
    # is an environment reference, is left untouched.  It is resolved by
    # the agent, on the host, when the resource is deployed.
    content=files::jinja("template:///app.conf.j2"),
)

```

</x-example-jinja>

Find more examples in the ´tests` folder of this module!

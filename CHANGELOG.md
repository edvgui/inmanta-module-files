# Changelog

## v2.6.0 - ?

- Add files::json::SerializableEntity for easy DSL entities serialization

## v2.5.0 - 2025-08-17

- Support OnFailure directive in unit file
- Automatically escape values in environment variables definition in unit files

## v2.4.0 - 2025-07-06

- Allow to control key sorting when saving a json/yaml file to disk

## v2.3.0 - 2025-05-12

- Allow to manage json/yaml files containing a list instead of an object

## v2.2.1 - 2025-04-11

- Fix deployment on new executor

## v2.2.0 - 2025-04-10

- Use python type annotations in plugins

## v2.1.1 - 2024-12-21

- Fix typo in unit file template

## v2.1.0 - 2024-12-18

- Add files::SharedHostFile resource

## v2.0.2 - 2024-12-16

- Re-release for dependency version bump

## v2.0.1 - 2024-12-13

- Add PartOf directive support to unit files

## v2.0.0 - 2024-11-30

- Fix attributes types for various unit entities
- Add support for systemd socket unit file

## v1.1.2 - 2024-10-15

- Widen dependencies constraints

## v1.1.1 - 2024-10-05

- Fix recursive creation of directory for unprivileged access user. (#50)

## v1.1.0 - 2024-09-30

- Add files::SharedJsonFile resource

## v1.0.2 - 2024-09-26

- Stop importing unused io module

## v1.0.1 - 2024-09-03

- Fix symlink stat operation, move helper to non-namespace package

## v1.0.0 - 2024-08-04

- Make sure that the symlink resource can be used to target a non-existing file
- Use mitogen for handler io
- Add default null value to file owner and group
- Add files::path_join plugin

## v0.6.1 - 2024-07-22

- Fix JsonFile facts serialization

## v0.6.0 - 2024-07-17

- Add discovery values to JsonFile resource

## v0.5.2 - 2024-07-07

- Re-release of 0.5.0

## v0.5.1 - 2024-07-07

- Re-release of 0.5.0

## v0.5.0 - 2024-07-07

- Add files::Symlink resource

## v0.4.0 - 2024-05-25

- Add support for timer unit files
- Add exec systemd unit service type

## v0.3.0 - 2024-04-28

- Add support for managing yaml files

## v0.2.1 - 2024-04-21

- Default send_event=true for all resources
- Improve recursive dict creation, keep new folder's parents stats

## v0.2.0 - 2024-04-07

- Add files::Directory and files::TextFile resources
- Fixed UnmanagedResource support

## v0.1.1 - 2024-03-10

- Fixed release of 0.1.0

## v0.1.0 - 2024-03-10

- Add support for host files
- Add support for json files
- Add support for systemd unit files

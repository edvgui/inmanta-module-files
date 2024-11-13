# Changelog

## v1.2.0 - ?

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

"""
Copyright 2026 Guillaume Everarts de Velp

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

import inmanta.compiler
import inmanta.execute.proxy
import inmanta.export
import inmanta.resources

# Snapshots of the content of every file that has been serialized as part of a
# file/text reference during this compile, keyed by content hash.  The content
# is frozen at serialization time, so that later modifications of the source
# file within the same compile don't affect what is uploaded to the server.
_snapshots: dict[str, bytes] = {}

# The exporter of the ongoing export, captured by the FileUploadTriggerResource.
# Once it is set, newly collected snapshots are registered with the exporter
# directly, as the trigger resource may have been serialized before some of the
# references.
_exporter: "inmanta.export.Exporter | None" = None


def collect_snapshot(content: bytes) -> str:
    """
    Freeze the given file content and register it for upload to the server.
    The actual upload is batched and deferred to the moment the resources are
    committed to the server (inmanta.export.Exporter.commit_resources), so no
    server is required during the compile itself.

    :param content: The content of the file, as it should be uploaded.
    :return: The hash of the content, which can be used to retrieve it.
    """
    file_hash = inmanta.export.hash_file(content)
    _snapshots[file_hash] = content
    if _exporter is not None:
        _exporter.upload_file(content)
    return file_hash


def get_snapshot(file_hash: str) -> bytes | None:
    """
    Get the snapshot of a file collected during this compile, if there is any.

    :param file_hash: The hash of the content, as returned by collect_snapshot.
    """
    return _snapshots.get(file_hash)


def reset() -> None:
    """
    Reset the state accumulated during the compile and export, so that
    consecutive compiles in the same process don't leak state.
    """
    global _exporter
    _snapshots.clear()
    _exporter = None


inmanta.compiler.finalizer(reset)


@inmanta.resources.resource(
    name="files::FileUploadTrigger",
    id_attribute="name",
    agent="name",
)
class FileUploadTriggerResource(inmanta.resources.Resource):
    """
    Internal resource backing the files::FileUploadTrigger entity.  It is never
    sent to the orchestrator: its only purpose is to register, at export time,
    the file snapshots collected during reference serialization with the
    exporter, which uploads them (batched) when the resources are committed to
    the server.
    """

    fields = ("files_to_upload",)

    @classmethod
    def get_files_to_upload(
        cls,
        exporter: "inmanta.export.Exporter | None",
        entity: "inmanta.execute.proxy.DynamicProxy",
    ) -> None:
        """
        Register all the file snapshots collected so far with the exporter, and
        capture the exporter so that snapshots collected later during this
        export are registered as well.  Then drop this resource from the
        export, it doesn't represent anything that should be deployed.
        """
        global _exporter
        if exporter is not None:
            _exporter = exporter
            for content in _snapshots.values():
                exporter.upload_file(content)
        raise inmanta.resources.IgnoreResourceException()

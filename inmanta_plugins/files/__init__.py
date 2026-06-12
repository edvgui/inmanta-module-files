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

import base64
import functools
import logging
import pathlib
import uuid

from inmanta_plugins.config import resolve_path

import inmanta_plugins.files.upload
from inmanta.agent.handler import LoggerABC, PythonLogger
from inmanta.plugins import plugin
from inmanta.protocol.endpoints import SyncClient
from inmanta.references import ArgumentTypes, Reference, reference

LOGGER = logging.getLogger(__name__)


@plugin
def path_join(base_path: str, *extra: str) -> str:
    """
    Join together the base_path and all of the extra parts after it.  If any extra
    item specified an absolute path (starts with a '/') it will overwrite all the
    elements of the path before it.

    :param base_path: The base path, a directory, to which should be
        appended all the extra items.
    :param *extra: A set of extra parts to add to the path.
    """
    return str(pathlib.Path(base_path, *extra))


@reference("files::TextReference")
class TextReference(Reference[str]):
    def __init__(
        self,
        text: str | Reference[str] | None,
        hash: str | Reference[str] | None,
    ):
        super().__init__()
        self.text = text
        self.hash = hash

    def resolve(self, logger: LoggerABC) -> str:
        if self.hash is not None:
            hash = self.resolve_other(self.hash, logger)

            # Resolve from the snapshot collected during this compile, if the
            # text hasn't been uploaded to the server yet
            snapshot = inmanta_plugins.files.upload.get_snapshot(hash)
            if snapshot is not None:
                return snapshot.decode()

            # Resolve from file api
            return get_file(hash).decode()

        if self.text is not None:
            # Resolve from local file system
            return self.resolve_other(self.text, logger)

        raise ValueError(
            "Invalid reference, either the file_path or the file_hash should be provided"
        )

    def serialize_arguments(self) -> tuple[uuid.UUID, list[ArgumentTypes]]:
        """
        Override parent implementation, to register the text for upload to the
        server and return a hash-based reference instead.
        """
        if self.hash is None:
            # Snapshot the text and register it for upload, then save its hash
            # into this reference attributes
            if self.text is None:
                raise ValueError("The text must be provided when the hash is not set")

            text = self.resolve_other(self.text, PythonLogger(LOGGER))

            # The text will be uploaded to the server when the resources are
            # exported, next time this reference is resolved, it should do it
            # using the hash
            self.hash = inmanta_plugins.files.upload.collect_snapshot(text.encode())
            self.text = None

        # Now that the text is registered for upload, delegate serialization
        # to the parent class
        return super().serialize_arguments()


@plugin
def create_text_reference(
    text: str | Reference[str],
) -> TextReference:
    """
    Create a reference to a text, preferably long, which can be consumed either
    by the agent or the compiler.  To share this long text with the agent, the
    reference uses the file api instead of storing the full text in the
    serialized reference.  During reference serialization, the text is
    registered for upload, it is uploaded to the server when the resources are
    exported.  When the reference is resolved on the agent side the text is
    pulled from the files api.

    :param text: The textual content should be accessed when the reference is resolved.
    """
    return TextReference(text, None)


@functools.lru_cache
def get_file(hash: str) -> bytes:
    """
    Get the file with the given hash from the api.  The result of the
    function is cached so that multiple calls with the same hash don't
    need to reach the server.

    :param hash: The hash of the file we want to retrieve
    """
    response = SyncClient("agent").get_file(hash)
    if response.code != 200 or not response.result:
        raise RuntimeError(
            f"Failed to get file from server ({response.code}): {response.result}"
        )

    return base64.b64decode(response.result["content"])


@reference("files::TextFileContentReference")
class TextFileContentReference(Reference[str]):
    def __init__(
        self,
        file_path: str | Reference[str] | None,
        file_hash: str | Reference[str] | None,
    ):
        super().__init__()
        self.file_path = file_path
        self.file_hash = file_hash

    def resolve(self, logger: LoggerABC) -> str:
        if self.file_hash is not None:
            file_hash = self.resolve_other(self.file_hash, logger)

            # Resolve from the snapshot collected during this compile, if the
            # file hasn't been uploaded to the server yet
            snapshot = inmanta_plugins.files.upload.get_snapshot(file_hash)
            if snapshot is not None:
                return snapshot.decode()

            # Resolve from file api
            return get_file(file_hash).decode()

        if self.file_path is not None:
            # Resolve from local file system, in case the reference is
            # resolved in the context of a compile and the file hasn't
            # been uploaded yet
            file_path = self.resolve_other(self.file_path, logger)
            return pathlib.Path(file_path).read_text()

        raise ValueError(
            "Invalid reference, either the file_path or the file_hash should be provided"
        )

    def serialize_arguments(self) -> tuple[uuid.UUID, list[ArgumentTypes]]:
        """
        Override parent implementation, to register the file content for upload
        to the server and return a hash-based reference instead.
        """
        if self.file_hash is None:
            # Snapshot the file content and register it for upload, then save
            # its hash into this reference attributes
            if self.file_path is None:
                raise ValueError(
                    "The file_path must be provided when the file_hash is not set"
                )

            file_path = self.resolve_other(self.file_path, PythonLogger(LOGGER))
            file_content = pathlib.Path(file_path).read_text()

            # The file will be uploaded to the server when the resources are
            # exported, next time this reference is resolved, it should do it
            # using the hash.  The file path is dropped so that the serialized
            # reference only depends on the content of the file.
            self.file_hash = inmanta_plugins.files.upload.collect_snapshot(
                file_content.encode()
            )
            self.file_path = None

        # Now that the file is registered for upload, delegate serialization
        # to the parent class
        return super().serialize_arguments()


@plugin
def create_text_file_content_reference(
    file_path: str | Reference[str],
) -> TextFileContentReference:
    """
    Create a reference to the content of a file, which can be consumed either
    by the agent or the compiler.  To share the content of the file with the
    agent, the reference uses the file api.  During reference serialization,
    the file content is read and registered for upload, it is uploaded to the
    server when the resources are exported.  When the reference is resolved on
    the agent side the file is pulled from the files api.

    :param file_path: The file to the path in the current filesystem whose content
        should be accessed when the reference is resolved.
    """
    return TextFileContentReference(resolve_path(file_path), None)

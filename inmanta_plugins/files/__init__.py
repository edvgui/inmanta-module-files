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
import logging
import pathlib
import uuid

from inmanta_plugins.config import resolve_path

from inmanta.agent.handler import LoggerABC, PythonLogger
from inmanta.export import hash_file
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
            # Resolve from file api
            file_hash = self.resolve_other(self.file_hash, logger)
            response = SyncClient("agent").get_file(file_hash)
            if response.code != 200 or not response.result:
                raise RuntimeError(
                    f"Failed to get file from server ({response.code}): {response.result}"
                )

            return base64.b64decode(response.result["content"]).decode()

        if self.file_path is not None:
            # Resolve from local file system
            file_path = self.resolve_other(self.file_path, logger)
            return pathlib.Path(file_path).read_text()

        raise ValueError(
            "Invalid reference, either the file_path or the file_hash should be provided"
        )

    def serialize_arguments(self) -> tuple[uuid.UUID, list[ArgumentTypes]]:
        """
        Override parent implementation, to upload file content to the server
        and return a hash-based reference instead.
        """
        if self.file_hash is None:
            # Upload the file to the api, then save its hash into this reference
            # attributes
            if self.file_path is None:
                raise ValueError(
                    "The file_path must be provided when the file_hash is not set"
                )

            file_path = self.resolve_other(self.file_path, PythonLogger(LOGGER))
            file_content = pathlib.Path(file_path).read_text()
            file_hash = hash_file(file_content.encode())

            client = SyncClient("compiler")
            stats_result = client.stat_files(files=[file_hash])
            if stats_result.code != 200:
                raise RuntimeError(
                    f"Unable to check status of files at server ({stats_result.code}): {stats_result.result}"
                )

            missing = file_hash in stats_result.result["files"]
            if missing:
                upload_result = client.upload_file(
                    id=file_hash,
                    content=base64.b64encode(file_content.encode()).decode("ascii"),
                )
                if upload_result.code != 200:
                    raise RuntimeError(
                        f"Unable to upload file to the server ({upload_result.code}): {upload_result.result}"
                    )

            # The file exists on the server, next time this reference is resolved, it
            # should do it using the server
            self.file_hash = file_hash
            self.file_path = None

        # Now that we have the file available in the api, delegate serialization
        # to the parent class
        return super().serialize_arguments()


@plugin
def create_text_file_content_reference(
    file_path: str | Reference[str],
) -> TextFileContentReference:
    """
    Create a reference to the content of a file, which can be consumed either
    by the agent or the compiler.  To share the content of the file with the
    agent, the reference uses the file api.  During reference serialization (
    during resource export), the file content is read, and uploaded to the
    server.  When the reference is resolved on the server side the file is pulled
    from the files api.

    :param file_path: The file to the path in the current filesystem whose content
        should be accessed when the reference is resolved.
    """
    return TextFileContentReference(resolve_path(file_path), None)

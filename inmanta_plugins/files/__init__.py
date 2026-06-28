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

import base64
import contextlib
import contextvars
import functools
import logging
import pathlib
import typing
import uuid
from collections.abc import Mapping

import jinja2
from inmanta_plugins.config import resolve_path
from inmanta_plugins.std import FactReference, JinjaDynamicProxy

import inmanta.ast
import inmanta_plugins.files.upload
from inmanta.agent.handler import LoggerABC, PythonLogger
from inmanta.plugins import CheckedArgs, Context, Plugin, plugin
from inmanta.protocol.endpoints import SyncClient
from inmanta.references import ArgumentTypes, Reference, reference
from inmanta_plugins.files.monkeypatch import (
    allow_references_in_templates,
    collect_unset_values,
)

JINJA_DEFERRED_CONTEXT: contextvars.ContextVar[dict[str, Reference[str]]] = (
    contextvars.ContextVar("JINJA_DEFERRED_CONTEXT")
)
JINJA_FILE: contextvars.ContextVar[str] = contextvars.ContextVar("JINJA_FILE")
JINJA_ENV: jinja2.Environment | None = None
# Compiled jinja templates, keyed by resolved template path.  Compiling a
# template from source (parse + optimize + bytecode) is expensive and the same
# handful of templates are rendered thousands of times per compile, so we cache
# the compiled Template objects.  Cleared in inmanta_reset_state alongside the
# environment they are bound to.
JINJA_TEMPLATE_CACHE: dict[str, "jinja2.Template"] = dict()
REFERENCES: list[Reference] = list()
# Reference classes whose __str__ should be temporarily overwritten during a
# jinja render so that they register themselves in the current jinja context.
# Populated by auto_register_reference.
REFERENCE_CLASSES: list[type[Reference]] = list()
LOGGER = logging.getLogger(__name__)


# --- One-pass dependency discovery -------------------------------------------
#
# Rendering a template reads many model values.  When the first one that is not
# yet frozen raises UnsetException, the whole render aborts and the compiler
# reschedules and fully re-runs the template.  A template that reads K still-unset
# values is therefore rendered up to K+1 times.
#
# Instead we render once in "discovery" mode: each unset value encountered is
# collected and replaced by a chaining-undefined so the render keeps going and
# reaches the other (independent) unset values.  If anything was collected, the
# render output is discarded and a single MultiUnsetException is raised for the
# whole batch, so the compiler waits for all of them at once and re-invokes us.
# Only a pass that observes zero misses used real values throughout, so only its
# output is ever returned -- which keeps the result identical to the old code.
JINJA_UNSET_COLLECTOR: contextvars.ContextVar[set[object] | None] = (
    contextvars.ContextVar("files_jinja_unset_collector", default=None)
)


def inmanta_reset_state() -> None:
    global JINJA_ENV
    JINJA_ENV = None
    JINJA_TEMPLATE_CACHE.clear()


def collect_or_raise(exc: inmanta.ast.UnsetException) -> object:
    """
    During a discovery render, record the unset value and return a
    chaining-undefined so rendering can continue.  Outside a discovery render
    (no active collector) propagate the exception, preserving the original
    per-miss rescheduling behaviour.
    """
    collector = JINJA_UNSET_COLLECTOR.get()
    variable = exc.get_result_variable()
    if collector is None or variable is None:
        raise exc
    collector.add(variable)
    return jinja2.ChainableUndefined(hint=exc.msg)


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
        text: str | Reference[str] | None = None,
        hash: str | Reference[str] | None = None,
    ):
        super().__init__()
        self._text = text
        self.hash = hash

    @property
    def text(self) -> str | Reference[str] | None:
        # This text can be set in the compiler but should never be set in the
        # exported resource.  To ensure this while preserving equality check
        # in the compiler, we create this hidden attributes, which we use in
        # the compiler and ignore in the deserialized resource
        return self._text

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

        # Now that the text is registered for upload, delegate serialization
        # to the parent class
        return super().serialize_arguments()

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return False

        # References are the same if the text is equal or the hash
        # is equal, only one needs to be True (both might not be True
        # depending on how the reference is created).
        if self.hash is not None and other.hash is not None:
            return self.hash == other.hash

        if self.text is not None and other.text is not None:
            return self.text == other.text

        return False


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
        file_path: str | Reference[str] | None = None,
        file_hash: str | Reference[str] | None = None,
    ):
        super().__init__()
        self._file_path = file_path
        self.file_hash = file_hash

    @property
    def file_path(self) -> str | Reference[str] | None:
        # This path can be set in the compiler but should never be set in the
        # exported resource.  To ensure this while preserving equality check
        # in the compiler, we create this hidden attributes, which we use in
        # the compiler and ignore in the deserialized resource
        return self._file_path

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

        # Now that the file is registered for upload, delegate serialization
        # to the parent class
        return super().serialize_arguments()

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return False

        # References are the same if the file is equal or the hash
        # is equal, only one needs to be True (both might not be True
        # depending on how the reference is created).
        if self.file_hash is not None and other.file_hash is not None:
            return self.file_hash == other.file_hash

        if self.file_path is not None and other.file_path is not None:
            return self.file_path == other.file_path

        return False


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


@reference("files::JinjaReference")
class JinjaReference(Reference[str]):
    """
    Reference to resolve a basic jinja template on the handler side.  The template
    may only expect simple strings, which should all be provided in the context
    dict.  These strings may be references themselves.
    """

    def __init__(
        self,
        template: str | Reference[str],
        references: Mapping[str, str | Reference[str]],
    ):
        super().__init__()
        self.template = template
        self.references = references

    def resolve(self, logger: LoggerABC) -> str:
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        tmpl = env.from_string(self.resolve_other(self.template, logger))
        return tmpl.render(
            references={
                k: self.resolve_other(v, logger) for k, v in self.references.items()
            },
        )


@contextlib.contextmanager
def jinja_deferred_context(file: str) -> typing.Iterator[dict[str, Reference[str]]]:
    # Setup a new dict and give away the context
    context: dict[str, Reference[str]] = {}
    context_token = JINJA_DEFERRED_CONTEXT.set(context)
    file_token = JINJA_FILE.set(file)
    try:
        yield context
    finally:
        # Always restore the context vars, even when the template rendering
        # raises (e.g. when the compiler reschedules the plugin call because
        # of an unset value).  Without this, JINJA_FILE and
        # JINJA_DEFERRED_CONTEXT leak past the current render, polluting later
        # renders (and even later tests running in the same thread) with a
        # stale context dict, which causes references to be registered into the
        # wrong dict and lost.
        JINJA_FILE.reset(file_token)
        JINJA_DEFERRED_CONTEXT.reset(context_token)


@plugin
def resolve_reference(
    ref: str | Reference[str], *, soft: bool = True
) -> str | Reference[str]:
    """
    Try to resolve the input reference, if it is one, return its resolved value.
    If the reference can not be resolved, or the input value is a string, the
    input value is returned as is.

    :param ref: The input value or reference that we should try to resolve
    :param soft: When set to False, raise an exception if the input is a reference
        and can't be resolved.
    """
    match ref:
        case str():
            return ref
        case Reference():
            try:
                return ref.resolve(PythonLogger(LOGGER))
            except Exception:
                if soft:
                    return ref
                else:
                    raise
        case _:
            typing.assert_never(ref)


@plugin
def register_reference(value: str | Reference[str], *, resolve: bool = False) -> str:
    """
    This plugin can be called in a jinja template that is being
    resolved by the jinja plugin.

    :param value: The value that may or may not be a reference.  If it is a reference, it
        is registered into the current context, if it is a str, it is returned directly.
    :param name: The name to assign to the reference when saving it into the context.
    :param resolve: Whether we should try to resolve the reference directly, and return its
        value if the resolving succeeds.  If the resolving fails, return the reference.
    """
    if isinstance(value, str):
        # Passthrough
        return value

    if resolve:
        with contextlib.suppress(Exception):
            # Try to resolve the reference now
            return value.resolve(PythonLogger(LOGGER))

    context = JINJA_DEFERRED_CONTEXT.get()
    ref_id, _ = value.serialize_arguments()  # TODO: optimize this
    context[str(ref_id)] = value
    return f'{{% endraw %}}{{{{ references["{str(ref_id)}"] }}}}{{% raw %}}'


def auto_register_reference[R: Reference](ref_cls: type[R]) -> type[R]:
    """
    Decorate a reference class, to indicate that the presence of one of its instances
    in a template as a terminal value should automatically register said instance
    in the current jinja reference context.

    The actual __str__ patching is done temporarily, for the duration of a jinja
    render only, by ``allow_references_in_templates``.  Here we only collect the
    classes that should be patched.
    """
    REFERENCE_CLASSES.append(ref_cls)
    return ref_cls


auto_register_reference(Reference)
auto_register_reference(FactReference)


def get_jinja_env(ctx: Context) -> jinja2.Environment:
    """
    Helper to construct a jinja environment that can be used with the inmanta
    dsl.  It loads all the plugins as filters and wraps the dynamic proxy
    objects into jinja specific proxies.
    """
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)

    # Registering all plugins as filters
    def curywrapper(func: Plugin) -> typing.Callable:
        def safewrapper(*args, **kwargs) -> typing.Any:

            # Make sure that a plugin with a Context argument can be called
            # inside a template
            if func._context != -1:
                new_args = list(args)
                new_args.insert(func._context, ctx)
                args = tuple(new_args)

            # Execute the plugin
            value = func.call_in_context(
                processed_args=CheckedArgs(
                    args=list(args),
                    kwargs=kwargs,
                    unknowns=False,
                ),
                resolver=ctx.resolver,
                queue=ctx.queue,
                location=inmanta.ast.Range(JINJA_FILE.get(), 0, 0, 0, 0),
            )

            # If we get a dynamic proxy, make sure to wrap it in case it
            # contains unset attributes.
            return JinjaDynamicProxy.return_value(value)

        return safewrapper

    for name, cls in ctx.get_compiler().get_plugins().items():
        env.filters[name.replace("::", ".")] = curywrapper(cls)

    return env


@plugin
def jinja(
    ctx: Context,
    template_path: str,
    **kwargs: object,
) -> JinjaReference | str:
    """
    Resolve a jinja template located at the given path, with all the keyword arguments
    as input.  If any reference is emitted and not converted to a primitive, the plugin
    returns a reference to finish the evaluation of the template in a context
    where the reference is known.

    :param template_path: The path to the template in the project
    :param **kwargs: Input to the template
    """
    # Resolve the full path of the template
    template_path = resolve_path(template_path)

    # Setting up the jinja2 environment
    global JINJA_ENV
    if JINJA_ENV is None:
        JINJA_ENV = get_jinja_env(ctx)

    # Reading the template string and building the template object.  Compiling
    # the template is expensive, so reuse the compiled object across the many
    # renders of the same template within a single compile.
    template = JINJA_TEMPLATE_CACHE.get(template_path)
    if template is None:
        template_string = pathlib.Path(template_path).read_text()
        template = JINJA_ENV.from_string(template_string)
        JINJA_TEMPLATE_CACHE[template_path] = template

    # Wrap kwargs so that optional inmanta relations behave as Jinja Undefined
    # rather than raising OptionalValueException at attribute access time.
    wrapped_kwargs = {
        key: JinjaDynamicProxy.return_value(value) for key, value in kwargs.items()
    }

    # Render once in discovery mode: collect every unset model value instead of
    # aborting at the first one (see collect_or_raise).  If any were missing,
    # wait for the whole batch at once rather than rescheduling per miss.
    #
    # In order to use references in templates, we also need to monkeypatch core
    # and std.  This is done only for the duration of the render, so the patches
    # never leak to code running outside of a jinja render.
    collector: set[object] = set()
    batch_unset = inmanta.ast.MultiUnsetException(
        f"Template {template_path} accessed values that were not set yet",
        # filled in below once the discovery pass has run
        [],
    )
    token = JINJA_UNSET_COLLECTOR.set(collector)
    try:
        with allow_references_in_templates(REFERENCE_CLASSES, register_reference):
            with collect_unset_values(collect_or_raise):
                with jinja_deferred_context(template_path) as context:
                    rendered = template.render(wrapped_kwargs)
    except (inmanta.ast.UnsetException, inmanta.ast.MultiUnsetException):
        # Raised by a nested template/plugin: propagate unchanged.
        raise
    except Exception as e:
        # A collected unset value can cascade into a downstream error during the
        # discovery render (e.g. a chaining-undefined reaching a filter).  If we
        # collected any misses, that error is presumed to be a consequence of
        # them: wait for the batch and retry.  A genuine error surfaces
        # unchanged on the final pass, where nothing is collected.
        if collector:
            batch_unset.result_variables = list(collector)
            raise batch_unset from None
        if isinstance(e, jinja2.exceptions.UndefinedError):
            raise inmanta.ast.NotFoundException(ctx.owner, "", e.message)
        raise
    finally:
        JINJA_UNSET_COLLECTOR.reset(token)

    if collector:
        batch_unset.result_variables = list(collector)
        raise batch_unset

    if len(context) == 0:
        # No reference to resolve later on
        return rendered
    else:
        return JinjaReference(
            template=create_text_reference("{% raw %}" + rendered + "{% endraw %}"),
            references=context,
        )

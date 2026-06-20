"""
Copyright 2024 Guillaume Everarts de Velp

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

import contextlib
import typing
from collections.abc import Collection, Iterator

from inmanta_plugins.std import JinjaDynamicProxy

from inmanta.execute.proxy import DynamicProxy, ProxyContext
from inmanta.references import Reference


@contextlib.contextmanager
def overwrite_reference_str(
    ref_cls: type[Reference],
    register_reference: typing.Callable[[Reference], str],
) -> Iterator[None]:
    """
    Overwrite the __str__ method of a reference class, to call the register_reference
    method instead.  This is used by the jinja template to automatically catch
    references which are about to be serialized by jinja, and register these
    references for the jinja reference context.

    The patch is only active for the duration of the context manager, so a
    reference being serialized outside of a jinja render (e.g. by the compiler
    when logging a rescheduled plugin call) keeps its normal string
    representation.
    """
    original_str = ref_cls.__str__

    def __str__(self: object) -> str:
        if not isinstance(self, Reference):
            raise ValueError(f"Invalid type for {type(self)} it is not a Reference")

        return register_reference(self)

    # Overwrite the reference class's str method with our own special one
    ref_cls.__str__ = __str__

    try:
        yield None
    finally:
        # Make sure that this method is not patched outside of the context of
        # this function
        ref_cls.__str__ = original_str


@contextlib.contextmanager
def allow_references_in_templates(
    ref_classes: Collection[type[Reference]],
    register_reference: typing.Callable[[Reference], str],
) -> Iterator[None]:
    """
    This contextmanager temporarily allows the usage of references in jinja
    templates.  It monkeypatches core and std so that references aren't blocked
    when wrapped in a dynamic proxy, and overwrites the __str__ method of the
    known reference classes so that they register themselves in the current
    jinja reference context when serialized.

    All patches are reverted when the context manager is exited (even on error),
    so they never leak to code running outside of a jinja render.
    """
    # Register original implementations so they can be restored when the
    # contextmanager is exited
    original_jinja_dynamic_proxy_return_value = JinjaDynamicProxy.return_value
    original_dynamic_proxy_return_value = DynamicProxy._return_value

    def return_value(
        cls: type[JinjaDynamicProxy],
        value: object,
        *,
        context: ProxyContext | None = None,
    ) -> object:
        """
        Alternative implementation of JinjaDynamicProxy.return_value which doesn't
        block the usage of References in templates.
        """
        context = (
            context
            if context is not None
            else ProxyContext(path=object.__repr__(value), validated=False)
        )

        return cls.wrap(
            super(JinjaDynamicProxy, cls).return_value(value, context=context)
        )

    # Overwrite the return_value method of JinjaDynamicProxy to allow usage of
    # references in templates.
    JinjaDynamicProxy.return_value = classmethod(return_value)

    def _return_value(
        self: DynamicProxy, value: object, *, relative_path: str
    ) -> object:
        """
        Alternative implementation of DynamicProxy._return_value which doesn't
        block the usage of References in templates.
        """
        context: ProxyContext = self._get_context()
        value_context: ProxyContext = context.nested(relative_path=relative_path)

        return DynamicProxy.return_value(value, context=value_context)

    # Overwrite the _return_value method of DynamicProxy to allow usage of
    # references in templates.
    DynamicProxy._return_value = _return_value

    with contextlib.ExitStack() as stack:
        # Patch known reference classes so they can be registered automatically
        for ref_cls in ref_classes:
            stack.enter_context(overwrite_reference_str(ref_cls, register_reference))

        try:
            yield None
        finally:
            # Make sure that these functions are not patched outside of the
            # context of this function
            JinjaDynamicProxy.return_value = original_jinja_dynamic_proxy_return_value
            DynamicProxy._return_value = original_dynamic_proxy_return_value

"""Trusted dynamic Python router loading.

This module is intentionally not imported by the gospace-only hot path. A
single-file router is compiled as an ordinary module and must expose the same
ROUTES mapping accepted by pyspace-minimal and bundled Python routers.
"""
from __future__ import annotations

import sys
import types
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LoadedPythonApplication:
    name: str
    module_name: str
    module: types.ModuleType
    routes: Mapping[str, object]


def load_source(name: str, source: bytes, *, filename: str | None = None) -> LoadedPythonApplication:
    """Compile and execute one trusted .py router entirely in memory.

    Imports resolve normally against packages already installed in the Cloud
    Function. Multi-file/package loading is deliberately a separate concern.
    """
    if not name:
        raise ValueError("application name is required")
    if not isinstance(source, bytes):
        raise TypeError("Python router source must be bytes")

    module_name = f"_pyspace_dynamic_{_module_component(name)}"
    origin = filename or f"pyspace://{name}/router.py"
    code = compile(source, origin, "exec", dont_inherit=True)
    module = types.ModuleType(module_name)
    module.__file__ = origin
    module.__package__ = ""

    # Publish while executing so normal self-references through sys.modules work
    # like an imported module. Roll back on failure rather than leaving a
    # partially initialized module visible.
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        exec(code, module.__dict__)
        routes = getattr(module, "ROUTES", None)
        if not isinstance(routes, Mapping):
            raise TypeError("dynamic Python router must expose a ROUTES mapping")
    except BaseException:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise

    return LoadedPythonApplication(name, module_name, module, routes)


def _module_component(name: str) -> str:
    # The application name is an identity, not Python source. Keep generated
    # module names deterministic without allowing punctuation to affect import
    # syntax or module hierarchy.
    component = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
    return component or "router"

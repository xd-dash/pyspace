"""Trusted dynamic Python router loading.

This module is intentionally not imported by the gospace-only hot path. A
single-file router is compiled as an ordinary module and must expose the same
ROUTES mapping accepted by pyspace-minimal and bundled Python routers.
"""
from __future__ import annotations

import hashlib
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
    """Compile and execute one trusted single-file router entirely in memory."""
    if not name:
        raise ValueError("application name is required")
    if not isinstance(source, bytes):
        raise TypeError("Python router source must be bytes")

    module_name = _module_name(name)
    origin = filename or f"pyspace://{name}/router.py"
    code = compile(source, origin, "exec", dont_inherit=True)
    module = types.ModuleType(module_name)
    module.__file__ = origin
    module.__package__ = ""

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

def _module_name(name: str) -> str:
    component = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name) or "router"
    # Sanitization alone aliases names such as foo-v1 and foo_v1. Preserve a
    # readable prefix but make the actual module identity injective for normal
    # application names without tying identity to source digests.
    suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return f"_pyspace_dynamic_{component}_{suffix}"

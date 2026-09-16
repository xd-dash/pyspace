"""Trusted dynamic Python router loading without mutating Flask's URL map."""
from __future__ import annotations

import hashlib
import hmac
import sys
import types
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PythonApplication:
    name: str
    identity: str
    module: types.ModuleType
    routes: Mapping[str, object]


def load_source(name: str, source: bytes, *, expected_sha256: str | None = None) -> PythonApplication:
    digest = hashlib.sha256(source).hexdigest()
    if expected_sha256:
        expected = expected_sha256.removeprefix("sha256:").lower()
        if not hmac.compare_digest(digest, expected):
            raise ValueError("Python module sha256 mismatch")
    identity = f"sha256:{digest}"
    module_name = f"pyspace_dynamic_{digest}"
    module = types.ModuleType(module_name)
    module.__file__ = f"pyspace://{name}/{digest}/router.py"
    module.__package__ = ""
    code = compile(source, module.__file__, "exec", dont_inherit=True, optimize=2)
    sys.modules[module_name] = module
    try:
        exec(code, module.__dict__)
        routes = getattr(module, "ROUTES", None)
        if not isinstance(routes, Mapping):
            raise TypeError("dynamic Python module must expose a ROUTES mapping")
        return PythonApplication(name=name, identity=identity, module=module, routes=routes)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise

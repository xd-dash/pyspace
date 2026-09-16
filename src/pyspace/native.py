"""Lazy bridge to an embedded gospace native module.

The Python host deliberately knows only this compact bytes ABI. The native
module owns Go/native/WASM routing and lifecycle; importing pyspace alone does
not import or initialize the Go runtime.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from threading import Lock
from typing import Any

from flask import Response

_INTERNAL_HEADERS = {
    "x-pyspace-app",
    "x-pyspace-control-token",
    "x-pyspace-module",
    "x-pyspace-module-sha256",
}


@dataclass(frozen=True, slots=True)
class NativeResponse:
    status: int
    headers: object
    body: bytes


def _header_block(headers: Iterable[tuple[str, str]]) -> bytes:
    # Length-prefixed pairs avoid escaping/parsing ambiguity while keeping one
    # coarse Python -> native crossing for all headers.
    out = bytearray()
    for key, value in headers:
        kb = key.encode("latin-1")
        vb = value.encode("latin-1")
        out += len(kb).to_bytes(4, "little")
        out += len(vb).to_bytes(4, "little")
        out += kb
        out += vb
    return bytes(out)


def _decode_header_block(block: bytes) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    view = memoryview(block)
    pos = 0
    size = len(view)
    while pos < size:
        if size - pos < 8:
            raise RuntimeError("invalid gospace response header block")
        klen = int.from_bytes(view[pos : pos + 4], "little")
        vlen = int.from_bytes(view[pos + 4 : pos + 8], "little")
        pos += 8
        end = pos + klen + vlen
        if end > size:
            raise RuntimeError("invalid gospace response header block")
        key = bytes(view[pos : pos + klen]).decode("latin-1")
        pos += klen
        value = bytes(view[pos : pos + vlen]).decode("latin-1")
        pos += vlen
        result.append((key, value))
    return result


class NativeGospace:
    """Lazy, process-local embedded gospace backend.

    The extension contract is intentionally tiny:

        dispatch(method: bytes, uri: bytes, headers: bytes, body: bytes)

    It may return ``(status, headers, body)``, an object with those attributes,
    or a mapping. ``headers`` may be the compact block above or ordinary pairs.
    """

    def __init__(self, module_name: str | None = None) -> None:
        self.module_name = module_name or os.environ.get(
            "PYSPACE_GOSPACE_MODULE", "gospace_native"
        )
        self._module: Any | None = None
        self._load_lock = Lock()

    @property
    def loaded(self) -> bool:
        return self._module is not None

    def _load(self):
        module = self._module
        if module is not None:
            return module
        with self._load_lock:
            module = self._module
            if module is None:
                module = importlib.import_module(self.module_name)
                if not callable(getattr(module, "dispatch", None)):
                    raise TypeError(
                        f"native module {self.module_name!r} does not expose dispatch()"
                    )
                self._module = module
        return module

    def __call__(self, request):
        module = self._load()
        method = request.method.encode("ascii")
        uri = request.full_path
        if uri.endswith("?"):
            uri = uri[:-1]
        uri_bytes = uri.encode("latin-1")
        headers = _header_block(
            (key, value)
            for key, value in request.headers.items()
            if key.lower() not in _INTERNAL_HEADERS
        )
        body = request.get_data(cache=False)
        raw = module.dispatch(method, uri_bytes, headers, body)
        native = self._normalize(raw)
        response_headers = native.headers
        if isinstance(response_headers, (bytes, bytearray, memoryview)):
            response_headers = _decode_header_block(bytes(response_headers))
        return Response(native.body, status=native.status, headers=response_headers)

    @staticmethod
    def _normalize(raw: object) -> NativeResponse:
        if isinstance(raw, NativeResponse):
            return raw
        if isinstance(raw, tuple) and len(raw) == 3:
            status, headers, body = raw
        elif isinstance(raw, dict):
            status, headers, body = raw["status"], raw.get("headers", ()), raw.get("body", b"")
        else:
            status = getattr(raw, "status")
            headers = getattr(raw, "headers", ())
            body = getattr(raw, "body", b"")
        if isinstance(body, str):
            body = body.encode()
        return NativeResponse(int(status), headers, bytes(body))

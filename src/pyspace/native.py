"""Zero-copy-ish ctypes bridge to gospace's stable C ABI."""
from __future__ import annotations

import ctypes
import os
from collections.abc import Iterable

from flask import Response

ABI_VERSION = 1
_HOP = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"})
_INTERNAL = frozenset({"x-pyspace-app", "x-pyspace-control-token", "x-pyspace-module", "x-pyspace-python-source", "x-pyspace-python-sha256"})


class _Response(ctypes.Structure):
    _fields_ = [("status", ctypes.c_uint32), ("headers", ctypes.POINTER(ctypes.c_uint8)), ("headers_len", ctypes.c_size_t), ("body", ctypes.POINTER(ctypes.c_uint8)), ("body_len", ctypes.c_size_t)]


def _span(data: bytes):
    if not data:
        return None, 0, None
    buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
    return ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint8)), len(data), buf


def _headers(request) -> bytes:
    out = bytearray()
    for key, value in request.headers.items():
        lower = key.lower()
        if lower in _HOP or lower in _INTERNAL or lower == "content-length":
            continue
        out.extend(key.encode("latin-1")); out.append(58); out.extend(value.encode("latin-1")); out.append(10)
    return bytes(out)


def _decode_headers(raw: bytes) -> list[tuple[str, str]]:
    result = []
    for line in raw.splitlines():
        name, sep, value = line.partition(b":")
        if sep and name.decode("latin-1").lower() not in _HOP and name.decode("latin-1").lower() != "content-length":
            result.append((name.decode("latin-1"), value.lstrip().decode("latin-1")))
    return result


class GospaceNative:
    """One in-process gospace runtime loaded once for the life of the instance."""
    __slots__ = ("_lib", "path")

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.environ.get("PYSPACE_GOSPACE_LIBRARY", "libgospace.so")
        lib = ctypes.CDLL(self.path)
        lib.gs_abi_version.argtypes = []
        lib.gs_abi_version.restype = ctypes.c_uint32
        if lib.gs_abi_version() != ABI_VERSION:
            raise RuntimeError(f"unsupported gospace ABI: {lib.gs_abi_version()}")
        p = ctypes.POINTER(ctypes.c_uint8)
        lib.gs_dispatch.argtypes = [p, ctypes.c_size_t, p, ctypes.c_size_t, p, ctypes.c_size_t, p, ctypes.c_size_t, ctypes.POINTER(_Response)]
        lib.gs_dispatch.restype = ctypes.c_int
        lib.gs_free_response.argtypes = [ctypes.POINTER(_Response)]
        lib.gs_free_response.restype = None
        self._lib = lib

    def dispatch(self, request):
        method = request.method.encode("ascii")
        uri = request.full_path.rstrip("?").encode("latin-1")
        headers = _headers(request)
        body = request.get_data(cache=True)
        mp, ml, mb = _span(method); up, ul, ub = _span(uri); hp, hl, hb = _span(headers); bp, bl, bb = _span(body)
        out = _Response()
        rc = self._lib.gs_dispatch(mp, ml, up, ul, hp, hl, bp, bl, ctypes.byref(out))
        # Keep borrowed input buffers live until the native call returns.
        _ = (mb, ub, hb, bb)
        if rc != 0:
            raise RuntimeError(f"gospace dispatch failed: {rc}")
        try:
            response_headers = ctypes.string_at(out.headers, out.headers_len) if out.headers_len else b""
            response_body = ctypes.string_at(out.body, out.body_len) if out.body_len else b""
            return Response(response_body, status=int(out.status), headers=_decode_headers(response_headers))
        finally:
            self._lib.gs_free_response(ctypes.byref(out))

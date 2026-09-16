"""In-process gospace bridge.

The preferred path is the tiny CPython extension (`_gospace_native`). A ctypes
fallback keeps source deployments usable without compiling the extension; both
speak the same gospace C ABI and retain no request pointers after dispatch.
"""
from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NativeResponse:
    status: int
    headers: bytes
    body: bytes


class _CResponse(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_uint32),
        ("headers", ctypes.POINTER(ctypes.c_uint8)),
        ("headers_len", ctypes.c_size_t),
        ("body", ctypes.POINTER(ctypes.c_uint8)),
        ("body_len", ctypes.c_size_t),
    ]


class NativeGospace:
    def __init__(self, library: str) -> None:
        self.library = os.path.abspath(library)
        try:
            import _gospace_native as extension
        except ImportError:
            extension = None
        self._extension = extension
        if extension is not None:
            extension.open(self.library)
            if extension.abi_version() != 1:
                raise RuntimeError("unsupported gospace ABI")
            self._lib = None
            return

        lib = ctypes.CDLL(self.library)
        lib.gs_abi_version.argtypes = []
        lib.gs_abi_version.restype = ctypes.c_uint32
        if lib.gs_abi_version() != 1:
            raise RuntimeError("unsupported gospace ABI")
        p = ctypes.POINTER(ctypes.c_uint8)
        lib.gs_dispatch.argtypes = [p, ctypes.c_size_t, p, ctypes.c_size_t, p, ctypes.c_size_t, p, ctypes.c_size_t, ctypes.POINTER(_CResponse)]
        lib.gs_dispatch.restype = ctypes.c_int
        lib.gs_free_response.argtypes = [ctypes.POINTER(_CResponse)]
        lib.gs_free_response.restype = None
        self._lib = lib

    @staticmethod
    def _ptr(value: bytes):
        if not value:
            return None, None
        buf = (ctypes.c_uint8 * len(value)).from_buffer_copy(value)
        return ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint8)), buf

    def dispatch(self, method: bytes, uri: bytes, headers: bytes, body: bytes) -> NativeResponse:
        if self._extension is not None:
            status, response_headers, response_body = self._extension.dispatch(method, uri, headers, body)
            return NativeResponse(status, response_headers, response_body)

        values = (method, uri, headers, body)
        pointers = [self._ptr(value) for value in values]
        out = _CResponse()
        rc = self._lib.gs_dispatch(
            pointers[0][0], len(method), pointers[1][0], len(uri),
            pointers[2][0], len(headers), pointers[3][0], len(body), ctypes.byref(out),
        )
        if rc != 0:
            raise RuntimeError(f"gospace dispatch failed: {rc}")
        try:
            response_headers = ctypes.string_at(out.headers, out.headers_len) if out.headers_len else b""
            response_body = ctypes.string_at(out.body, out.body_len) if out.body_len else b""
            return NativeResponse(int(out.status), response_headers, response_body)
        finally:
            self._lib.gs_free_response(ctypes.byref(out))

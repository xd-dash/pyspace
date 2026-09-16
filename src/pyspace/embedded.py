"""Flask request adapter for the in-process gospace ABI."""
from __future__ import annotations

from .native import NativeGospace


class EmbeddedGospaceBackend:
    __slots__ = ("runtime",)

    def __init__(self, library: str) -> None:
        self.runtime = NativeGospace(library)

    @staticmethod
    def _headers(request) -> bytes:
        # latin-1 is the lossless WSGI header byte mapping. Keep this wire form
        # deliberately trivial so Go parses lines without JSON or HTTP framing.
        return b"".join(
            key.encode("latin-1") + b":" + value.encode("latin-1") + b"\n"
            for key, value in request.headers.items()
        )

    @staticmethod
    def _response_headers(raw: bytes):
        result = []
        for line in raw.splitlines():
            key, sep, value = line.partition(b":")
            if sep and key:
                result.append((key.decode("latin-1"), value.decode("latin-1")))
        return result

    def __call__(self, request):
        status, response_headers, body = self.runtime.dispatch_raw(
            request.method.encode("ascii"),
            request.full_path.rstrip("?").encode("latin-1"),
            self._headers(request),
            request.get_data(cache=False),
        )
        return body, status, self._response_headers(response_headers)

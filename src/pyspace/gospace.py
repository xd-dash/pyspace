"""Lazy gospace subprocess backend over a Unix-domain HTTP socket."""

from __future__ import annotations

import hashlib
import http.client
import os
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from flask import Response

from .supervisor import ProcessSpec, ProcessSupervisor, file_sha256


_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_INTERNAL_HEADERS = {
    "x-pyspace-app",
    "x-pyspace-control-token",
    "x-pyspace-module",
    "x-pyspace-gospace-binary",
}


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 10.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self.sock = sock


@dataclass(frozen=True)
class GospaceConfig:
    binary: str
    binary_sha256: str
    socket_path: str
    control_token: str
    request_timeout: float = 10.0
    ready_timeout: float = 5.0

    @classmethod
    def from_binary(
        cls,
        binary: str,
        *,
        name: str = "gospace",
        socket_dir: str = "/tmp/pyspace",
        request_timeout: float = 10.0,
        ready_timeout: float = 5.0,
    ) -> "GospaceConfig":
        if not os.path.isfile(binary):
            raise ValueError(f"gospace binary does not exist: {binary!r}")
        if not os.access(binary, os.X_OK):
            raise ValueError(f"gospace binary is not executable: {binary!r}")

        readable = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name)[:40]
        suffix = hashlib.sha256(name.encode()).hexdigest()[:12]
        safe_name = f"{readable or 'gospace'}-{suffix}"
        # If the deployment did not configure a public/runtime-load control
        # token, still give pyspace a private per-instance token for the local
        # non-executing route resolver.
        control_token = os.environ.get("GOSPACE_CONTROL_TOKEN") or secrets.token_urlsafe(32)
        return cls(
            binary=binary,
            binary_sha256=file_sha256(binary),
            socket_path=str(Path(socket_dir) / f"{safe_name}.sock"),
            control_token=control_token,
            request_timeout=request_timeout,
            ready_timeout=ready_timeout,
        )


class GospaceBackend:
    """A pyspace application that lazily ensures one gospace child exists."""

    def __init__(
        self,
        supervisor: ProcessSupervisor,
        name: str,
        config: GospaceConfig,
    ) -> None:
        self.supervisor = supervisor
        self.name = name
        self.config = config

    @property
    def process_key(self) -> str:
        return f"gospace:{self.name}"

    def _spec(self) -> ProcessSpec:
        return ProcessSpec.from_argv(
            (self.config.binary, "--unix-socket", self.config.socket_path),
            socket_path=self.config.socket_path,
            executable_sha256=self.config.binary_sha256,
            env={"GOSPACE_CONTROL_TOKEN": self.config.control_token},
            ready_timeout=self.config.ready_timeout,
        )

    def _ensure(self) -> None:
        self.supervisor.acquire(self.process_key, self._spec())

    def matches(self, request) -> bool:
        """Ask gospace's metadata index whether this request has an owner."""
        self._ensure()
        query = urlencode({"method": request.method, "path": request.full_path.rstrip("?")})
        conn = UnixHTTPConnection(self.config.socket_path, timeout=self.config.request_timeout)
        try:
            conn.request(
                "GET",
                f"/_gospace/resolve?{query}",
                headers={"X-Gospace-Control-Token": self.config.control_token},
            )
            upstream = conn.getresponse()
            upstream.read()
            if upstream.status == 200:
                return True
            if upstream.status == 404:
                return False
            raise RuntimeError(f"gospace route resolver returned HTTP {upstream.status}")
        finally:
            conn.close()

    def __call__(self, request):
        self._ensure()
        return self._proxy(request)

    def _proxy(self, request):
        body = request.get_data(cache=True)
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in _HOP_BY_HOP
            and key.lower() not in _INTERNAL_HEADERS
            and key.lower() != "content-length"
        }

        target = request.full_path
        if target.endswith("?"):
            target = target[:-1]

        conn = UnixHTTPConnection(
            self.config.socket_path,
            timeout=self.config.request_timeout,
        )
        try:
            conn.request(request.method, target, body=body, headers=headers)
            upstream = conn.getresponse()
            response_body = upstream.read()
            response_headers = [
                (key, value)
                for key, value in upstream.getheaders()
                if key.lower() not in _HOP_BY_HOP and key.lower() != "content-length"
            ]
            return Response(
                response_body,
                status=upstream.status,
                headers=response_headers,
            )
        finally:
            conn.close()

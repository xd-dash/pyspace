"""Small request-driven subprocess supervisor.

This is intentionally not a daemon supervisor. A child is cached while the
Cloud Function instance is warm and reconciled lazily by the next request that
needs it. No background monitor or restart loop is required.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ProcessSpec:
    argv: tuple[str, ...]
    socket_path: str | None = None
    executable_sha256: str | None = None
    env: Mapping[str, str] | None = None
    cwd: str | None = None
    ready_timeout: float = 5.0

    @classmethod
    def from_argv(
        cls,
        argv: Sequence[str],
        *,
        socket_path: str | None = None,
        executable_sha256: str | None = None,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        ready_timeout: float = 5.0,
    ) -> "ProcessSpec":
        if not argv:
            raise ValueError("argv is required")
        return cls(
            tuple(argv),
            socket_path,
            executable_sha256,
            env,
            cwd,
            ready_timeout,
        )


@dataclass
class ProcessHandle:
    spec: ProcessSpec
    process: subprocess.Popen[bytes]

    @property
    def alive(self) -> bool:
        return self.process.poll() is None


@dataclass
class _Slot:
    lock: threading.Lock = field(default_factory=threading.Lock)
    handle: ProcessHandle | None = None


class ProcessSupervisor:
    """Per-instance lazy process cache with per-key spawn serialization."""

    def __init__(self) -> None:
        self._slots_lock = threading.Lock()
        self._slots: dict[str, _Slot] = {}
        atexit.register(self.shutdown)

    def _slot(self, key: str) -> _Slot:
        with self._slots_lock:
            return self._slots.setdefault(key, _Slot())

    def acquire(self, key: str, spec: ProcessSpec) -> ProcessHandle:
        slot = self._slot(key)
        with slot.lock:
            handle = slot.handle
            if handle is not None and handle.alive and handle.spec == spec:
                if self._ready(handle):
                    return handle
                self._stop_handle(handle)
                slot.handle = None
            elif handle is not None:
                self._stop_handle(handle)
                slot.handle = None

            handle = self._spawn(spec)
            try:
                self._wait_ready(handle)
            except Exception:
                self._stop_handle(handle)
                raise
            slot.handle = handle
            return handle

    def get(self, key: str) -> ProcessHandle | None:
        slot = self._slot(key)
        with slot.lock:
            if slot.handle is None or not slot.handle.alive:
                return None
            return slot.handle

    def stop(self, key: str) -> None:
        slot = self._slot(key)
        with slot.lock:
            if slot.handle is not None:
                self._stop_handle(slot.handle)
                slot.handle = None

    def shutdown(self) -> None:
        with self._slots_lock:
            slots = tuple(self._slots.values())
        for slot in slots:
            with slot.lock:
                if slot.handle is not None:
                    self._stop_handle(slot.handle)
                    slot.handle = None

    def _spawn(self, spec: ProcessSpec) -> ProcessHandle:
        if spec.executable_sha256 is not None:
            actual = file_sha256(spec.argv[0])
            if actual != spec.executable_sha256:
                raise RuntimeError(
                    f"executable digest changed for {spec.argv[0]!r}: "
                    f"got {actual}, want {spec.executable_sha256}"
                )

        if spec.socket_path:
            path = Path(spec.socket_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                path.unlink()
            except FileNotFoundError:
                pass

        env = os.environ.copy()
        if spec.env:
            env.update(spec.env)

        process = subprocess.Popen(
            spec.argv,
            cwd=spec.cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            # Inherit stdout/stderr so Cloud Functions captures child logs.
            stdout=None,
            stderr=None,
            start_new_session=True,
            close_fds=True,
        )
        return ProcessHandle(spec=spec, process=process)

    def _wait_ready(self, handle: ProcessHandle) -> None:
        if not handle.spec.socket_path:
            return

        deadline = time.monotonic() + handle.spec.ready_timeout
        while time.monotonic() < deadline:
            if not handle.alive:
                raise RuntimeError(
                    f"child exited before readiness with status "
                    f"{handle.process.returncode}"
                )
            if self._ready(handle):
                return
            time.sleep(0.01)
        raise TimeoutError(
            f"child did not become ready at {handle.spec.socket_path!r} "
            f"within {handle.spec.ready_timeout}s"
        )

    @staticmethod
    def _ready(handle: ProcessHandle) -> bool:
        path = handle.spec.socket_path
        if path is None:
            return handle.alive
        if not handle.alive or not os.path.exists(path):
            return False
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.1)
        try:
            probe.connect(path)
            return True
        except OSError:
            return False
        finally:
            probe.close()

    @staticmethod
    def _stop_handle(handle: ProcessHandle, grace: float = 1.0) -> None:
        process = handle.process
        if process.poll() is not None:
            return

        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

        try:
            process.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            pass

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            # There is nothing useful for the request path to do beyond this.
            pass


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

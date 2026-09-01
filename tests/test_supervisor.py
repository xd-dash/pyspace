import os
import sys
import tempfile

import pytest

from pyspace.supervisor import ProcessSpec, ProcessSupervisor, file_sha256


def test_acquire_reuses_live_ready_process():
    supervisor = ProcessSupervisor()
    with tempfile.TemporaryDirectory() as tmp:
        socket_path = os.path.join(tmp, "child.sock")
        code = (
            "import socket,time; "
            f"p={socket_path!r}; "
            "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); "
            "s.bind(p); s.listen(); time.sleep(30)"
        )
        spec = ProcessSpec.from_argv(
            (sys.executable, "-c", code),
            socket_path=socket_path,
            ready_timeout=2.0,
        )
        first = supervisor.acquire("child", spec)
        second = supervisor.acquire("child", spec)
        try:
            assert first.process.pid == second.process.pid
            assert first.alive
        finally:
            supervisor.stop("child")


def test_spawn_rejects_executable_changed_after_composition():
    supervisor = ProcessSupervisor()
    with tempfile.TemporaryDirectory() as tmp:
        executable = os.path.join(tmp, "child.sh")
        with open(executable, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nsleep 30\n")
        os.chmod(executable, 0o700)
        digest = file_sha256(executable)

        spec = ProcessSpec.from_argv(
            (executable,),
            executable_sha256=digest,
        )

        with open(executable, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 0\n")

        with pytest.raises(RuntimeError, match="executable digest changed"):
            supervisor.acquire("child", spec)

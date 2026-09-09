"""Own the child process group and bound its lifetime and captured output."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from contextvars import ContextVar
from pathlib import Path
from threading import Event

execution_cancelled: ContextVar[Event | None] = ContextVar("execution_cancelled", default=None)


def _signal_group(proc: subprocess.Popen, sig: signal.Signals) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Reap a leader that exited since the last poll before retrying.
        # Persistent permission failures, including live children, remain errors.
        if proc.poll() is None:
            raise
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass


def run_process(
    args: list[str],
    *,
    prompt: str = "",
    cwd: Path | None = None,
    timeout: float = 600,
    max_bytes: int = 1048576,
) -> str:
    if timeout <= 0 or max_bytes < 1:
        raise ValueError("Positive process limits required")
    encoded = prompt.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError("Prompt exceeds process input limit")
    cancel = execution_cancelled.get()
    with (
        tempfile.TemporaryFile() as stdin,
        tempfile.TemporaryFile() as stdout,
        tempfile.TemporaryFile() as stderr,
    ):
        stdin.write(encoded)
        stdin.seek(0)
        proc = subprocess.Popen(
            args, stdin=stdin, stdout=stdout, stderr=stderr, cwd=cwd, start_new_session=True
        )
        started = time.monotonic()
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("Execution lease lost or task cancelled")
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Agent process deadline exceeded")
                if (
                    os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size
                    > max_bytes
                ):
                    raise ValueError("Agent process output exceeds byte limit")
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            if proc.returncode:
                raise RuntimeError(f"Agent process exited with code {proc.returncode}")
            stdout.seek(0)
            return stdout.read(max_bytes + 1).decode("utf-8")
        finally:
            _signal_group(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            _signal_group(proc, signal.SIGKILL)
            proc.wait()

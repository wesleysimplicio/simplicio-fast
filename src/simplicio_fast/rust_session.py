"""Resident client for the read-only Rust core session protocol."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading
from typing import Any, Mapping


SESSION_SCHEMA = "simplicio.fast.engine-session/v1"
MAX_FRAME_BYTES = 1 * 1024 * 1024


class RustSessionError(RuntimeError):
    """A Rust session failed without retrying or fabricating a response."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class RustCoreSession:
    """One verified Rust child reused for read-only stats/query/context calls."""

    def __init__(self, executable: str | Path) -> None:
        self.executable = str(executable)
        self._lock = threading.Lock()
        try:
            self._process = subprocess.Popen(
                [self.executable, "--session"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            handshake = self._readline()
        except (OSError, ValueError, RustSessionError) as error:
            self.close()
            raise RustSessionError(f"session_start_failed:{type(error).__name__}") from error
        if (
            handshake.get("schema") != SESSION_SCHEMA
            or handshake.get("engine") != "rust"
            or handshake.get("status") != "ready"
        ):
            self.close()
            raise RustSessionError("session_handshake_invalid")
        capabilities = handshake.get("capabilities")
        if not isinstance(capabilities, list) or not {
            "stats",
            "query",
            "context",
        }.issubset(capabilities):
            self.close()
            raise RustSessionError("session_capabilities_invalid")
        self.handshake = handshake

    def _readline(self) -> dict[str, Any]:
        line = self._process.stdout.readline() if self._process.stdout else ""
        if not line or len(line.encode("utf-8")) > MAX_FRAME_BYTES:
            raise RustSessionError("session_frame_invalid")
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RustSessionError("session_frame_invalid") from error
        if not isinstance(value, dict):
            raise RustSessionError("session_frame_invalid")
        return value

    def call(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        frame = _canonical({"operation": operation, "payload": dict(payload)})
        if len(frame.encode("utf-8")) > MAX_FRAME_BYTES:
            raise RustSessionError("session_frame_too_large")
        with self._lock:
            if self._process.poll() is not None:
                raise RustSessionError("session_crashed")
            try:
                if self._process.stdin is None:
                    raise RustSessionError("session_stdin_missing")
                self._process.stdin.write(frame + "\n")
                self._process.stdin.flush()
                response = self._readline()
            except (BrokenPipeError, OSError, RustSessionError) as error:
                raise RustSessionError("session_crashed") from error
        if response.get("ok") is not True:
            raise RustSessionError(str(response.get("reason") or "session_request_failed"))
        result = response.get("result")
        if not isinstance(result, dict):
            raise RustSessionError("session_result_invalid")
        return result

    def close(self) -> None:
        process = getattr(self, "_process", None)
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=2)

    def __enter__(self) -> "RustCoreSession":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["MAX_FRAME_BYTES", "RustCoreSession", "RustSessionError", "SESSION_SCHEMA"]

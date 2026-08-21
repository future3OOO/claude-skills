"""Command execution and bounded reporting shared by workflow recorders."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile

from .repo_identity import RepoIdentity
from .state_store import utc_timestamp

MAX_CAPTURE = 16000
KILL_WAIT = 0.2


def run(
    command: list[str], identity: RepoIdentity, timeout: float
) -> tuple[bytes, int, bool]:
    """Run one command without letting inherited output handles extend timeout."""
    with tempfile.TemporaryFile() as capture:
        process = subprocess.Popen(
            command,
            cwd=str(identity.root),
            stdout=capture,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
        try:
            process.wait(timeout=timeout)
            exit_code, timed_out = int(process.returncode), False
        except subprocess.TimeoutExpired:
            _terminate(process)
            try:
                process.wait(timeout=KILL_WAIT)
            except subprocess.TimeoutExpired:
                pass
            # The parent may exit on TERM while a same-group child ignores it.
            # Always kill the owned group before returning, then bound reaping.
            _kill(process)
            try:
                process.wait(timeout=KILL_WAIT)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=KILL_WAIT)
                except subprocess.TimeoutExpired:
                    pass
            exit_code, timed_out = 124, True
        capture.seek(0)
        raw = capture.read()
    return raw, exit_code, timed_out


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        _signal_group(process.pid, signal.SIGTERM)
    else:
        process.terminate()


def _kill(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        _signal_group(process.pid, signal.SIGKILL)
    else:
        process.kill()


def _signal_group(pid: int, value: signal.Signals) -> None:
    try:
        os.killpg(pid, value)
    except ProcessLookupError:
        pass


def run_entry(
    raw: bytes, exit_code: int, timed_out: bool, **fields: object
) -> dict[str, object]:
    return {
        **fields,
        "exitCode": exit_code,
        "timedOut": timed_out,
        "outputTail": raw[-MAX_CAPTURE:].decode("utf-8", errors="replace"),
        "at": utc_timestamp(),
    }


def emit_json(value: object) -> None:
    try:
        print(json.dumps(value, sort_keys=True), flush=True)
    except OSError:
        mute_stdout()


def mute_stdout() -> None:
    descriptor = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(descriptor, sys.stdout.fileno())
    finally:
        os.close(descriptor)


def print_output(raw: bytes) -> None:
    output = raw[-MAX_CAPTURE:].decode("utf-8", errors="replace")
    if output:
        try:
            print(output, end="" if output.endswith("\n") else "\n")
        except OSError:
            mute_stdout()

"""Command execution and bounded reporting shared by workflow recorders."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

from .repo_identity import RepoIdentity
from .state_store import utc_timestamp

MAX_CAPTURE = 16000


def run(command: list[str], identity: RepoIdentity, timeout: int) -> tuple[bytes, int, bool]:
    process = subprocess.Popen(
        command,
        cwd=str(identity.root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name == "posix",
    )
    try:
        raw, _ = process.communicate(timeout=timeout)
        return raw or b"", process.returncode, False
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        raw, _ = process.communicate()
        return raw or b"", 124, True


def run_entry(raw: bytes, exit_code: int, timed_out: bool, **fields: object) -> dict[str, object]:
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

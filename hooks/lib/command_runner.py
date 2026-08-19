"""Bounded command execution and output reporting shared by the TDD and
verification recorders."""
from __future__ import annotations

import os
import subprocess
import sys

from .repo_identity import RepoIdentity
from .state_store import utc_timestamp

MAX_CAPTURE = 16000


def run(command: list[str], identity: RepoIdentity, timeout: int) -> tuple[bytes, int, bool]:
    try:
        result = subprocess.run(
            command,
            cwd=str(identity.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return result.stdout or b"", result.returncode, False
    except subprocess.TimeoutExpired as exc:
        return (exc.stdout or b"") + (exc.stderr or b""), 124, True


def run_entry(raw: bytes, exit_code: int, timed_out: bool, **fields: object) -> dict[str, object]:
    return {
        **fields,
        "exitCode": exit_code,
        "timedOut": timed_out,
        "outputTail": raw[-MAX_CAPTURE:].decode("utf-8", errors="replace"),
        "at": utc_timestamp(),
    }


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
            # A successful run is already committed by the time it reports, so a
            # lost reporting channel must not be re-labelled as a refusal; a run
            # that genuinely failed still returns 2 through the caller's branch.
            mute_stdout()



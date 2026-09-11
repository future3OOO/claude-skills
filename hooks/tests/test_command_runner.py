#!/usr/bin/env python3
"""Public contract for bounded command output: real child processes through command_runner."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.command_runner import MAX_CAPTURE, run, run_entry
from hooks.lib.repo_identity import resolve_repo_identity

WIDTHS = (("é", 2), ("€", 3), ("😀", 4))


def run_program(program: str, timeout: float = 30) -> tuple[bytes, int, bool, str]:
    """Run a real child program and return its output, status and recorded tail."""
    raw, exit_code, timed_out = run([sys.executable, "-c", program], resolve_repo_identity(ROOT), timeout)
    return raw, exit_code, timed_out, str(run_entry(raw, exit_code, timed_out)["outputTail"])


def emitting(payload: bytes) -> str:
    return f"import sys; sys.stdout.buffer.write({payload!r})"


def printing(payload: bytes, stdout: int = subprocess.PIPE) -> subprocess.CompletedProcess[bytes]:
    program = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
        f"from hooks.lib.command_runner import print_output; print_output({payload!r})"
    )
    return subprocess.run([sys.executable, "-c", program], stdout=stdout, stderr=subprocess.PIPE, check=False)


def split_cuts() -> list[tuple[bytes, str]]:
    """Every width and cut offset inside a code point, with the expected whole-character tail."""
    cases = []
    for char, width in WIDTHS:
        for inside in range(1, width):
            suffix = "B" * ((MAX_CAPTURE - inside) % width)
            text = char * (MAX_CAPTURE // width + 10) + suffix
            cases.append((text.encode(), char * ((MAX_CAPTURE - len(suffix)) // width) + suffix))
    return cases


class RecordedTail(unittest.TestCase):
    def test_cut_inside_code_point_starts_at_next_whole_character(self) -> None:
        for payload, expected in split_cuts():
            tail = run_program(emitting(payload))[3]
            self.assertEqual(tail, expected, "TAIL_CUT_MANUFACTURED_REPLACEMENT")

    def test_invalid_bytes_inside_tail_still_render_replacement(self) -> None:
        tail = run_program(emitting(b"x" * 17000 + b"\xff\xfe" + b"y"))[3]
        self.assertTrue(tail.endswith("��y"), "INVALID_BYTES_NOT_REPLACED")

    def test_tail_is_bounded_suffix_of_emitted_output(self) -> None:
        raw, _, _, tail = run_program(emitting(("A" + "€" * 6000).encode()))
        observed = len(tail.encode())
        message = f"TAIL_EXCEEDS_BYTE_BOUND scale={len(raw)} limit={MAX_CAPTURE} observed={observed}"
        self.assertLessEqual(observed, MAX_CAPTURE, message)
        self.assertTrue(raw.endswith(tail.encode()), message)

    def test_short_output_is_unchanged(self) -> None:
        text = "héllo €"
        self.assertEqual(run_program(emitting(text.encode()))[3], text, "SHORT_OUTPUT_CHANGED")

    def test_failed_and_timed_out_runs_keep_their_tail(self) -> None:
        failed = run_program(emitting(b"z" * 20000) + "; sys.stdout.flush(); sys.exit(3)")
        interrupted = run_program(emitting(b"z" * 20000) + "; sys.stdout.flush(); import time; time.sleep(30)", timeout=1)
        self.assertEqual(failed[1:], (3, False, "z" * MAX_CAPTURE), "FAILED_RUN_TAIL_CHANGED")
        self.assertEqual(interrupted[1:], (124, True, "z" * MAX_CAPTURE), "FAILED_RUN_TAIL_CHANGED")

    def test_run_returns_every_emitted_byte(self) -> None:
        payload = b"PROOF_MARKER\n" + "€".encode() * 6000
        self.assertEqual(run_program(emitting(payload))[0], payload, "RAW_OUTPUT_TRUNCATED")


class PrintedTail(unittest.TestCase):
    def test_cut_inside_code_point_prints_next_whole_character(self) -> None:
        for payload, expected in split_cuts():
            printed = printing(payload).stdout.decode()
            self.assertEqual(printed, expected + "\n", "PRINTED_CUT_MANUFACTURED_REPLACEMENT")

    def test_closed_reader_is_silent(self) -> None:
        reader, writer = os.pipe()
        os.close(reader)
        try:
            result = printing(("€" * 6000).encode(), stdout=writer)
        finally:
            os.close(writer)
        self.assertEqual((result.returncode, result.stderr), (0, b""), "PRINT_CLOSED_READER_FAILED")


class BoundaryInvalidByte(unittest.TestCase):
    def test_invalid_byte_at_cut_still_renders_replacement(self) -> None:
        for prefix in (b"\x80", b"\xe2\x80", b"\xc0\x80", b"\xed\xa0\x80"):
            payload = b"a" * 100 + prefix + b"b" * (MAX_CAPTURE - 1)
            expected = "�" + "b" * (MAX_CAPTURE - 1)
            self.assertEqual(run_program(emitting(payload))[3], expected, "BOUNDARY_INVALID_BYTE_NOT_REPLACED")
            self.assertEqual(printing(payload).stdout.decode(), expected + "\n", "BOUNDARY_INVALID_BYTE_NOT_REPLACED")


if __name__ == "__main__":
    unittest.main()

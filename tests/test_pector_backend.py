"""
FILE: test_pector_backend.py
Purpose: Verifies the optional external pector backend without requiring the real GPL binary.
Depends on: unittest, tempfile, app_paths.py, clap_detector.py, and pector_backend.py
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import numpy as np

from app_paths import AppPaths
from clap_detector import ClapDetectorConfig
from pector_backend import PectorDetector


class FakePipe:
    """Collects bytes written by the detector into the subprocess stdin."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    def flush(self) -> None:
        pass


class FakeProcess:
    """Tiny fake subprocess that can be scripted to exit after a few chunks."""

    def __init__(self, exit_after_writes: int | None = None) -> None:
        self.stdin = FakePipe()
        self._exit_after_writes = exit_after_writes
        self._writes = 0
        self._returncode = None

    def poll(self):
        if self._exit_after_writes is not None and len(self.stdin.buffer) > self._writes:
            self._writes = len(self.stdin.buffer)
            self._returncode = 0
        return self._returncode

    def terminate(self) -> None:
        self._returncode = -15

    def wait(self, timeout: float | None = None) -> None:
        return None

    def kill(self) -> None:
        self._returncode = -9


class PectorDetectorTests(unittest.TestCase):
    """Unit tests for the external process-backed clap detector."""

    def make_paths(self, temp_dir: str) -> AppPaths:
        return AppPaths.from_home(Path(temp_dir))

    def test_reports_missing_backend_when_binary_is_unconfigured(self) -> None:
        """Selecting pector without an installed binary should surface a backend-specific status."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.make_paths(temp_dir)
            detector = PectorDetector(paths, ClapDetectorConfig(backend="pector"))

            update = detector.process_chunk(np.zeros(512, dtype=np.float32), timestamp=1.0)

        self.assertEqual(update.status, "missing-backend")
        self.assertEqual(update.rejection_reason, "missing backend")

    def test_triggers_when_external_process_exits_zero(self) -> None:
        """A clean pector subprocess exit should map to one triggered double clap."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.make_paths(temp_dir)
            binary_path = paths.app_support_dir / "vendor" / "pector" / "bin" / "pector_c"
            binary_path.parent.mkdir(parents=True, exist_ok=True)
            binary_path.write_text("", encoding="utf-8")

            detector = PectorDetector(
                paths,
                ClapDetectorConfig(backend="pector", pector_binary_path=str(binary_path), warmup_seconds=0.0),
                popen=lambda *args, **kwargs: FakeProcess(exit_after_writes=1),
            )

            update = detector.process_chunk(np.ones(512, dtype=np.float32) * 0.2, timestamp=1.0)

        self.assertTrue(update.triggered)
        self.assertEqual(update.status, "triggered")
        self.assertTrue(update.is_impulse)


if __name__ == "__main__":
    unittest.main()

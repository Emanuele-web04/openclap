"""
FILE: test_daemon_voice.py
Purpose: Verifies the daemon's shared trigger bookkeeping stays stable while
voice wake remains outside the shipped runtime path.
Depends on: unittest, tempfile, daemon_service.py, and app_paths.py.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app_paths import AppPaths
from daemon_service import ClapDaemonService


class FakeControlServer:
    """Thread-free control server stub for daemon unit tests."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class FakeActionDispatcher:
    """Action dispatcher stub that captures enqueued trigger reasons."""

    def __init__(self, *args, **kwargs) -> None:
        self.jobs: list[str] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def update_settings(self, _settings) -> None:
        pass

    def enqueue_trigger(self, reason: str) -> None:
        self.jobs.append(reason)

    def pending_jobs(self) -> int:
        return len(self.jobs)


class FakeClapDetector:
    """Minimal clap detector stub used only so the daemon can initialize."""

    def __init__(self, config, sensitivity_preset: str) -> None:
        self.config = config

    def reset_runtime_state(self) -> None:
        pass


class DaemonVoiceTests(unittest.TestCase):
    """Unit tests for daemon-side trigger bookkeeping."""

    def test_dispatch_trigger_tracks_trigger_source_without_voice_fields(self) -> None:
        """Manual trigger bookkeeping should not expose dormant voice runtime state."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = AppPaths.from_home(Path(temp_dir))
            with (
                patch("daemon_service.ensure_audio_dependencies", lambda: None),
                patch("daemon_service.ControlServer", FakeControlServer),
                patch("daemon_service.ActionDispatcher", FakeActionDispatcher),
                patch("daemon_service.ClapDetector", FakeClapDetector),
            ):
                service = ClapDaemonService(paths)

            service._dispatch_trigger("manual-test", "manual-test")
            payload = service._serialize_status()

            self.assertEqual(service.status.last_trigger_source, "manual-test")
            self.assertEqual(service._action_dispatcher.jobs, ["manual-test"])
            self.assertEqual(payload["last_trigger_source"], "manual-test")
            self.assertIn("recent_detection_history", payload)
            self.assertIn("environment_summary", payload)
            self.assertNotIn("voice_enabled", payload)
            self.assertNotIn("voice_status", payload)
            self.assertNotIn("voice", payload)


if __name__ == "__main__":
    unittest.main()

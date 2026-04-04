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
from clap_detector import ClapUpdate
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


class FakeVoiceDetector:
    """Wake-word stub that lets tests control confirmation hits deterministically."""

    def __init__(self, detections: list[bool] | None = None) -> None:
        self.detections = list(detections or [])
        self.reset_calls = 0
        self.audio_chunks = 0

    def process_chunk(self, _chunk, timestamp: float | None = None) -> bool:
        self.audio_chunks += 1
        if self.detections:
            return self.detections.pop(0)
        return False

    def reset_for_listening(self) -> None:
        self.reset_calls += 1

    def close(self) -> None:
        pass

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "engine": "local",
            "status": "ready",
            "last_error": "",
            "last_heard_text": "",
            "last_heard_at": None,
            "last_matched_variant": "",
            "recent_text_window": [],
            "cooldown_seconds": 2.0,
        }


class DaemonVoiceTests(unittest.TestCase):
    """Unit tests for daemon-side trigger bookkeeping."""

    def test_dispatch_trigger_tracks_trigger_source_with_voice_debug_fields(self) -> None:
        """Manual trigger bookkeeping should still serialize the daemon voice-debug payload safely."""

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
            self.assertIn("voice_status", payload)
            self.assertIn("voice_debug", payload)

    def test_double_clap_can_arm_voice_confirmation_without_dispatching(self) -> None:
        """When voice confirmation is enabled, a clap should open the wake-word window first."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = AppPaths.from_home(Path(temp_dir))
            with (
                patch("daemon_service.ensure_audio_dependencies", lambda: None),
                patch("daemon_service.ControlServer", FakeControlServer),
                patch("daemon_service.ActionDispatcher", FakeActionDispatcher),
                patch("daemon_service.ClapDetector", FakeClapDetector),
            ):
                service = ClapDaemonService(paths)

            service.config.voice.enabled = True
            service.config.voice.wake_phrase = "jarvis"
            service.config.voice.confirmation_window_seconds = 2.5
            service._voice_detector = FakeVoiceDetector()

            service._arm_voice_confirmation(10.0)

            self.assertEqual(service._action_dispatcher.jobs, [])
            self.assertEqual(service._voice_detector.reset_calls, 1)
            self.assertEqual(service.status.detector_status, "awaiting 'jarvis'")
            self.assertAlmostEqual(service._voice_confirmation_deadline or 0.0, 12.5)

    def test_wake_word_after_double_clap_dispatches_actions(self) -> None:
        """A wake-word hit inside the armed window should complete the trigger."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = AppPaths.from_home(Path(temp_dir))
            with (
                patch("daemon_service.ensure_audio_dependencies", lambda: None),
                patch("daemon_service.ControlServer", FakeControlServer),
                patch("daemon_service.ActionDispatcher", FakeActionDispatcher),
                patch("daemon_service.ClapDetector", FakeClapDetector),
            ):
                service = ClapDaemonService(paths)

            service.config.voice.enabled = True
            service.config.voice.wake_phrase = "jarvis"
            service.config.voice.confirmation_window_seconds = 2.5
            service._voice_detector = FakeVoiceDetector(detections=[True])

            service._arm_voice_confirmation(10.0)
            detected = service._voice_detector.process_chunk([], timestamp=10.5)
            if detected:
                service._voice_confirmation_deadline = None
                service.status.detector_status = "triggered"
                service._dispatch_trigger("double-clap+voice", "double-clap+voice")

            self.assertEqual(service._action_dispatcher.jobs, ["double-clap+voice"])
            self.assertEqual(service.status.last_trigger_source, "double-clap+voice")

    def test_voice_confirmation_timeout_clears_pending_window(self) -> None:
        """The voice gate should expire cleanly if no wake word arrives in time."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = AppPaths.from_home(Path(temp_dir))
            with (
                patch("daemon_service.ensure_audio_dependencies", lambda: None),
                patch("daemon_service.ControlServer", FakeControlServer),
                patch("daemon_service.ActionDispatcher", FakeActionDispatcher),
                patch("daemon_service.ClapDetector", FakeClapDetector),
            ):
                service = ClapDaemonService(paths)

            service.config.voice.enabled = True
            service.config.voice.wake_phrase = "jarvis"
            service.config.voice.confirmation_window_seconds = 2.5
            service._voice_detector = FakeVoiceDetector()

            service._arm_voice_confirmation(10.0)
            service._expire_voice_confirmation_if_needed(13.0)

            self.assertIsNone(service._voice_confirmation_deadline)
            self.assertEqual(service.status.last_rejection_reason, "wake-word timeout")
            self.assertEqual(service._action_dispatcher.jobs, [])

    def test_soft_clap_candidates_can_arm_voice_confirmation(self) -> None:
        """Two strong near-miss clap events should still open the voice gate."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = AppPaths.from_home(Path(temp_dir))
            with (
                patch("daemon_service.ensure_audio_dependencies", lambda: None),
                patch("daemon_service.ControlServer", FakeControlServer),
                patch("daemon_service.ActionDispatcher", FakeActionDispatcher),
                patch("daemon_service.ClapDetector", FakeClapDetector),
            ):
                service = ClapDaemonService(paths)

            service.config.voice.enabled = True
            first = ClapUpdate(
                status="listening",
                clap_count=0,
                triggered=False,
                is_impulse=False,
                peak=0.20,
                rms=0.02,
                transient=0.04,
                crest_factor=3.0,
                band_ratio=1.4,
                high_band_share=0.3,
                spectral_flatness=0.2,
                zero_crossing_rate=0.3,
                spectral_centroid=2500.0,
                clap_score=8.0,
                confidence=0.90,
                rejection_reason="low confidence",
            )
            second = ClapUpdate(
                status="listening",
                clap_count=0,
                triggered=False,
                is_impulse=False,
                peak=0.18,
                rms=0.02,
                transient=0.035,
                crest_factor=2.8,
                band_ratio=1.3,
                high_band_share=0.28,
                spectral_flatness=0.2,
                zero_crossing_rate=0.3,
                spectral_centroid=2400.0,
                clap_score=7.6,
                confidence=0.88,
                rejection_reason="low confidence",
            )

            armed_first = service._consider_soft_clap_voice_arm(first, 10.0)
            armed_second = service._consider_soft_clap_voice_arm(second, 10.35)

            self.assertFalse(armed_first)
            self.assertTrue(armed_second)

    def test_real_first_clap_plus_soft_second_clap_can_arm_voice_confirmation(self) -> None:
        """One confirmed clap followed by one near-miss clap should still open the voice gate."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = AppPaths.from_home(Path(temp_dir))
            with (
                patch("daemon_service.ensure_audio_dependencies", lambda: None),
                patch("daemon_service.ControlServer", FakeControlServer),
                patch("daemon_service.ActionDispatcher", FakeActionDispatcher),
                patch("daemon_service.ClapDetector", FakeClapDetector),
            ):
                service = ClapDaemonService(paths)

            service.config.voice.enabled = True
            first = ClapUpdate(
                status="clap 1/2",
                clap_count=1,
                triggered=False,
                is_impulse=True,
                peak=0.24,
                rms=0.03,
                transient=0.05,
                crest_factor=3.2,
                band_ratio=1.5,
                high_band_share=0.32,
                spectral_flatness=0.21,
                zero_crossing_rate=0.31,
                spectral_centroid=2600.0,
                clap_score=9.0,
                confidence=0.95,
                rejection_reason="",
            )
            second = ClapUpdate(
                status="listening",
                clap_count=1,
                triggered=False,
                is_impulse=False,
                peak=0.18,
                rms=0.02,
                transient=0.035,
                crest_factor=2.8,
                band_ratio=1.3,
                high_band_share=0.28,
                spectral_flatness=0.2,
                zero_crossing_rate=0.3,
                spectral_centroid=2400.0,
                clap_score=7.6,
                confidence=0.75,
                rejection_reason="timing mismatch",
            )

            armed_first = service._consider_soft_clap_voice_arm(first, 10.0)
            armed_second = service._consider_soft_clap_voice_arm(second, 10.12)

            self.assertFalse(armed_first)
            self.assertTrue(armed_second)

    def test_prepare_voice_chunk_resamples_44100_input_to_16000(self) -> None:
        """The wake-word path should receive correctly scaled 16 kHz audio even on 44.1 kHz clap backends."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = AppPaths.from_home(Path(temp_dir))
            with (
                patch("daemon_service.ensure_audio_dependencies", lambda: None),
                patch("daemon_service.ControlServer", FakeControlServer),
                patch("daemon_service.ActionDispatcher", FakeActionDispatcher),
                patch("daemon_service.ClapDetector", FakeClapDetector),
            ):
                service = ClapDaemonService(paths)

            source = [0.0] * 441
            resampled = service._prepare_voice_chunk(source, 44_100)

            self.assertEqual(len(resampled), 160)


if __name__ == "__main__":
    unittest.main()

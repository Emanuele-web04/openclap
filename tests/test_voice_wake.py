"""
FILE: test_voice_wake.py
Purpose: Verifies the wake-word audio adapter, lazy engine setup, and cooldown
behavior without requiring a live microphone or native Porcupine runtime.
Depends on: unittest, numpy, and voice_wake.py.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from voice_wake import VoiceWakeDetector, VoiceWakeSettings


class FakePorcupineEngine:
    """Small fake wake-word engine that records frames and returns scripted hits."""

    def __init__(self, detections: list[int] | None = None) -> None:
        self.frame_length = 4
        self.sample_rate = 16_000
        self.detections = list(detections or [])
        self.frames: list[list[int]] = []
        self.deleted = False

    def process(self, pcm: list[int]) -> int:
        self.frames.append(list(pcm))
        if self.detections:
            return self.detections.pop(0)
        return -1

    def delete(self) -> None:
        self.deleted = True


class VoiceWakeDetectorTests(unittest.TestCase):
    """Unit tests for the optional wake-word detector wrapper."""

    def test_requires_access_key_before_engine_start(self) -> None:
        """Enabled voice wake should report a missing key before touching audio."""

        with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
            detector = VoiceWakeDetector(
                VoiceWakeSettings(enabled=True),
                access_key_loader=lambda: None,
            )

            triggered = detector.process_chunk(np.array([0.1, 0.2], dtype=np.float32), timestamp=1.0)

        self.assertFalse(triggered)
        self.assertEqual(detector.status, "missing-key")

    def test_buffers_audio_until_one_full_frame_is_available(self) -> None:
        """PCM conversion should accumulate partial chunks before calling Porcupine."""

        engine = FakePorcupineEngine()
        with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
            detector = VoiceWakeDetector(
                VoiceWakeSettings(enabled=True),
                access_key_loader=lambda: "test-key",
                engine_factory=lambda **_: engine,
            )

            detector.process_chunk(np.array([0.10, 0.20], dtype=np.float32), timestamp=1.0)
            detector.process_chunk(np.array([0.30, 0.40], dtype=np.float32), timestamp=1.0)

        self.assertEqual(len(engine.frames), 1)
        self.assertEqual(
            engine.frames[0],
            [3277, 6553, 9830, 13107],
        )
        self.assertEqual(detector.status, "ready")

    def test_enforces_voice_cooldown_between_repeated_detections(self) -> None:
        """Back-to-back detections should only trigger once until the cooldown expires."""

        engine = FakePorcupineEngine(detections=[0, 0, 0])
        with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
            detector = VoiceWakeDetector(
                VoiceWakeSettings(enabled=True, cooldown_seconds=2.0),
                access_key_loader=lambda: "test-key",
                engine_factory=lambda **_: engine,
            )
            frame = np.array([0.2, 0.2, 0.2, 0.2], dtype=np.float32)

            first = detector.process_chunk(frame, timestamp=10.0)
            second = detector.process_chunk(frame, timestamp=11.0)
            third = detector.process_chunk(frame, timestamp=13.1)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(third)

    def test_close_releases_engine_resources(self) -> None:
        """Shutting the detector down should release the native engine handle."""

        engine = FakePorcupineEngine()
        with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
            detector = VoiceWakeDetector(
                VoiceWakeSettings(enabled=True),
                access_key_loader=lambda: "test-key",
                engine_factory=lambda **_: engine,
            )
            detector.process_chunk(np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32), timestamp=1.0)

            detector.close()

        self.assertTrue(engine.deleted)


if __name__ == "__main__":
    unittest.main()

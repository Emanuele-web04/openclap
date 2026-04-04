"""
FILE: test_voice_wake.py
Purpose: Verifies the local offline wake-word adapter plus the optional
Porcupine fallback without requiring a live microphone or native runtime.
Depends on: unittest, numpy, and voice_wake.py.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from voice_wake import LOCAL_ENGINE, PORCUPINE_ENGINE, VoiceWakeDetector, VoiceWakeSettings


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


class FakeLocalModel:
    """Tiny marker object used by the fake local recognizer factory."""


class FakeLocalRecognizer:
    """Small Vosk-like recognizer stub with scripted partial/final payloads."""

    def __init__(self, partials: list[str] | None = None, finals: list[str] | None = None) -> None:
        self.partials = list(partials or [])
        self.finals = list(finals or [])
        self.accept_calls = 0
        self.reset_calls = 0
        self.last_partial = ""
        self.last_final = ""

    def AcceptWaveform(self, pcm_bytes: bytes) -> bool:
        self.accept_calls += 1
        if self.finals:
            self.last_final = self.finals.pop(0)
            return True
        if self.partials:
            self.last_partial = self.partials.pop(0)
        return False

    def PartialResult(self) -> str:
        return self.last_partial

    def Result(self) -> str:
        return self.last_final

    def Reset(self) -> None:
        self.reset_calls += 1
        self.last_partial = ""
        self.last_final = ""


class VoiceWakeDetectorTests(unittest.TestCase):
    """Unit tests for the local and Porcupine wake-word detector paths."""

    def test_requires_access_key_before_porcupine_start(self) -> None:
        """Porcupine mode should report a missing key before touching audio."""

        with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
            detector = VoiceWakeDetector(
                VoiceWakeSettings(enabled=True, engine=PORCUPINE_ENGINE),
                access_key_loader=lambda: None,
            )

            triggered = detector.process_chunk(np.array([0.1, 0.2], dtype=np.float32), timestamp=1.0)

        self.assertFalse(triggered)
        self.assertEqual(detector.status, "missing-key")

    def test_builtin_phrase_uses_keyword_name_without_custom_file(self) -> None:
        """Built-in Porcupine keywords should still initialize without a custom .ppn path."""

        recorded_kwargs: dict[str, object] = {}

        def build_engine(**kwargs):
            recorded_kwargs.update(kwargs)
            return FakePorcupineEngine()

        with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
            detector = VoiceWakeDetector(
                VoiceWakeSettings(enabled=True, wake_phrase="jarvis", engine=PORCUPINE_ENGINE),
                access_key_loader=lambda: "test-key",
                engine_factory=build_engine,
            )

            detector.process_chunk(np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32), timestamp=1.0)

        self.assertEqual(recorded_kwargs["keywords"], ["jarvis"])
        self.assertNotIn("keyword_paths", recorded_kwargs)

    def test_custom_phrase_requires_keyword_file_in_porcupine_mode(self) -> None:
        """A phrase like 'wake up' should fail clearly in Porcupine mode without a custom .ppn."""

        with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
            detector = VoiceWakeDetector(
                VoiceWakeSettings(enabled=True, wake_phrase="wake up", engine=PORCUPINE_ENGINE),
                access_key_loader=lambda: "test-key",
            )

            triggered = detector.process_chunk(np.array([0.1, 0.2], dtype=np.float32), timestamp=1.0)

        self.assertFalse(triggered)
        self.assertEqual(detector.status, "missing-keyword")
        self.assertIn("wake up", detector.last_error)

    def test_custom_keyword_path_is_forwarded_to_porcupine(self) -> None:
        """Custom phrases should initialize Porcupine with the configured .ppn path."""

        recorded_kwargs: dict[str, object] = {}

        def build_engine(**kwargs):
            recorded_kwargs.update(kwargs)
            return FakePorcupineEngine()

        with tempfile.TemporaryDirectory() as temp_dir:
            keyword_path = Path(temp_dir) / "wake-up.ppn"
            keyword_path.write_bytes(b"test")

            with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
                detector = VoiceWakeDetector(
                    VoiceWakeSettings(
                        enabled=True,
                        wake_phrase="wake up",
                        keyword_path=str(keyword_path),
                        engine=PORCUPINE_ENGINE,
                    ),
                    access_key_loader=lambda: "test-key",
                    engine_factory=build_engine,
                )

                detector.process_chunk(np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32), timestamp=1.0)

        self.assertEqual(recorded_kwargs["keyword_paths"], [str(keyword_path)])
        self.assertNotIn("keywords", recorded_kwargs)

    def test_porcupine_buffers_audio_until_one_full_frame_is_available(self) -> None:
        """PCM conversion should accumulate partial chunks before calling Porcupine."""

        engine = FakePorcupineEngine()
        with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
            detector = VoiceWakeDetector(
                VoiceWakeSettings(enabled=True, wake_phrase="jarvis", engine=PORCUPINE_ENGINE),
                access_key_loader=lambda: "test-key",
                engine_factory=lambda **_: engine,
            )

            detector.process_chunk(np.array([0.10, 0.20], dtype=np.float32), timestamp=1.0)
            detector.process_chunk(np.array([0.30, 0.40], dtype=np.float32), timestamp=1.0)

        self.assertEqual(len(engine.frames), 1)
        self.assertEqual(engine.frames[0], [3277, 6553, 9830, 13107])
        self.assertEqual(detector.status, "ready")

    def test_porcupine_enforces_voice_cooldown_between_repeated_detections(self) -> None:
        """Back-to-back Porcupine detections should only trigger once until cooldown expires."""

        engine = FakePorcupineEngine(detections=[0, 0, 0])
        with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
            detector = VoiceWakeDetector(
                VoiceWakeSettings(
                    enabled=True,
                    wake_phrase="jarvis",
                    engine=PORCUPINE_ENGINE,
                    cooldown_seconds=2.0,
                ),
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

    def test_close_releases_porcupine_resources(self) -> None:
        """Shutting the detector down should release the native Porcupine handle."""

        engine = FakePorcupineEngine()
        with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
            detector = VoiceWakeDetector(
                VoiceWakeSettings(enabled=True, wake_phrase="jarvis", engine=PORCUPINE_ENGINE),
                access_key_loader=lambda: "test-key",
                engine_factory=lambda **_: engine,
            )
            detector.process_chunk(np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32), timestamp=1.0)

            detector.close()

        self.assertTrue(engine.deleted)

    def test_reset_for_listening_clears_partial_porcupine_pcm_buffer(self) -> None:
        """Starting a fresh confirmation window should drop leftover PCM for Porcupine."""

        engine = FakePorcupineEngine()
        with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
            detector = VoiceWakeDetector(
                VoiceWakeSettings(enabled=True, wake_phrase="jarvis", engine=PORCUPINE_ENGINE),
                access_key_loader=lambda: "test-key",
                engine_factory=lambda **_: engine,
            )

            detector.process_chunk(np.array([0.10, 0.20], dtype=np.float32), timestamp=1.0)
            detector.reset_for_listening()
            detector.process_chunk(np.array([0.30, 0.40], dtype=np.float32), timestamp=1.0)

        self.assertEqual(engine.frames, [])

    def test_local_engine_starts_without_access_key(self) -> None:
        """The local offline engine should not require any external key or keyword file."""

        recognizer = FakeLocalRecognizer()
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "vosk-model"
            model_path.mkdir()
            with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
                detector = VoiceWakeDetector(
                    VoiceWakeSettings(
                        enabled=True,
                        wake_phrase="wake up",
                        engine=LOCAL_ENGINE,
                        model_path=str(model_path),
                    ),
                    local_model_loader=lambda path: FakeLocalModel(),
                    local_recognizer_factory=lambda model, sample_rate, phrase: recognizer,
                )

                triggered = detector.process_chunk(np.array([0.1, 0.2], dtype=np.float32), timestamp=1.0)

        self.assertFalse(triggered)
        self.assertEqual(detector.status, "ready")
        self.assertEqual(detector.last_error, "")
        self.assertEqual(recognizer.accept_calls, 1)

    def test_local_engine_reports_detection_from_partial_result(self) -> None:
        """A partial transcript containing the wake phrase should trigger the local engine."""

        recognizer = FakeLocalRecognizer(partials=['{"partial": "wake up"}'])
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "vosk-model"
            model_path.mkdir()
            with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
                detector = VoiceWakeDetector(
                    VoiceWakeSettings(
                        enabled=True,
                        wake_phrase="wake up",
                        engine=LOCAL_ENGINE,
                        model_path=str(model_path),
                    ),
                    local_model_loader=lambda path: FakeLocalModel(),
                    local_recognizer_factory=lambda model, sample_rate, phrase: recognizer,
                )

                triggered = detector.process_chunk(np.array([0.1], dtype=np.float32), timestamp=1.2)

        self.assertTrue(triggered)

    def test_local_engine_accepts_common_wake_up_variant(self) -> None:
        """The local matcher should accept common ASR variants like 'wake app' for 'wake up'."""

        recognizer = FakeLocalRecognizer(partials=['{"partial": "wake app"}'])
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "vosk-model"
            model_path.mkdir()
            with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
                detector = VoiceWakeDetector(
                    VoiceWakeSettings(
                        enabled=True,
                        wake_phrase="wake up",
                        engine=LOCAL_ENGINE,
                        model_path=str(model_path),
                    ),
                    local_model_loader=lambda path: FakeLocalModel(),
                    local_recognizer_factory=lambda model, sample_rate, phrase: recognizer,
                )

                triggered = detector.process_chunk(np.array([0.1], dtype=np.float32), timestamp=1.2)

        self.assertTrue(triggered)

    def test_local_engine_accepts_common_jarvis_variant(self) -> None:
        """The local matcher should accept common offline-ASR slips like 'jervis' for 'jarvis'."""

        recognizer = FakeLocalRecognizer(partials=['{"partial": "jervis"}'])
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "vosk-model"
            model_path.mkdir()
            with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
                detector = VoiceWakeDetector(
                    VoiceWakeSettings(
                        enabled=True,
                        wake_phrase="jarvis",
                        engine=LOCAL_ENGINE,
                        model_path=str(model_path),
                    ),
                    local_model_loader=lambda path: FakeLocalModel(),
                    local_recognizer_factory=lambda model, sample_rate, phrase: recognizer,
                )

                triggered = detector.process_chunk(np.array([0.1], dtype=np.float32), timestamp=1.2)

        self.assertTrue(triggered)

    def test_local_engine_accepts_split_recent_phrase_context(self) -> None:
        """Two short partials should still combine into one wake phrase inside the rolling text window."""

        recognizer = FakeLocalRecognizer(partials=['{"partial": "wake"}', '{"partial": "up"}'])
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "vosk-model"
            model_path.mkdir()
            with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
                detector = VoiceWakeDetector(
                    VoiceWakeSettings(
                        enabled=True,
                        wake_phrase="wake up",
                        engine=LOCAL_ENGINE,
                        model_path=str(model_path),
                    ),
                    local_model_loader=lambda path: FakeLocalModel(),
                    local_recognizer_factory=lambda model, sample_rate, phrase: recognizer,
                )

                first = detector.process_chunk(np.array([0.1], dtype=np.float32), timestamp=1.0)
                second = detector.process_chunk(np.array([0.1], dtype=np.float32), timestamp=1.3)

        self.assertFalse(first)
        self.assertTrue(second)

    def test_local_engine_reset_calls_recognizer_reset(self) -> None:
        """Starting a fresh confirmation window should reset the local decoder state."""

        recognizer = FakeLocalRecognizer(partials=['{"partial": "wake up"}', '{"partial": ""}'])
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "vosk-model"
            model_path.mkdir()
            with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
                detector = VoiceWakeDetector(
                    VoiceWakeSettings(
                        enabled=True,
                        wake_phrase="wake up",
                        engine=LOCAL_ENGINE,
                        model_path=str(model_path),
                    ),
                    local_model_loader=lambda path: FakeLocalModel(),
                    local_recognizer_factory=lambda model, sample_rate, phrase: recognizer,
                )

                detector.process_chunk(np.array([0.1], dtype=np.float32), timestamp=1.0)
                detector.reset_for_listening()
                triggered = detector.process_chunk(np.array([0.1], dtype=np.float32), timestamp=1.2)

        self.assertFalse(triggered)
        self.assertEqual(recognizer.reset_calls, 1)

    def test_local_engine_missing_model_surfaces_clear_error(self) -> None:
        """Local mode should explain when the offline speech model is not installed yet."""

        with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
            detector = VoiceWakeDetector(
                VoiceWakeSettings(
                    enabled=True,
                    wake_phrase="wake up",
                    engine=LOCAL_ENGINE,
                    model_path="/tmp/definitely-missing-openclap-model",
                ),
            )

            triggered = detector.process_chunk(np.array([0.1], dtype=np.float32), timestamp=1.0)

        self.assertFalse(triggered)
        self.assertEqual(detector.status, "missing-model")
        self.assertIn("Local speech model is missing", detector.last_error)

    def test_local_engine_surfaces_model_loader_failure(self) -> None:
        """Local model loader problems should propagate as a clear missing-dependency state."""

        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "vosk-model"
            model_path.mkdir()
            with patch("voice_wake.VOICE_WAKE_EXPERIMENTAL", True):
                detector = VoiceWakeDetector(
                    VoiceWakeSettings(
                        enabled=True,
                        wake_phrase="wake up",
                        engine=LOCAL_ENGINE,
                        model_path=str(model_path),
                    ),
                    local_model_loader=lambda path: (_ for _ in ()).throw(RuntimeError("local model unavailable")),
                    local_recognizer_factory=lambda model, sample_rate, phrase: FakeLocalRecognizer(),
                )

                triggered = detector.process_chunk(np.array([0.1], dtype=np.float32), timestamp=1.0)

        self.assertFalse(triggered)
        self.assertEqual(detector.status, "missing-dependency")
        self.assertIn("local model unavailable", detector.last_error)


if __name__ == "__main__":
    unittest.main()

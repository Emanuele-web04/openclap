"""
FILE: test_clap_detector.py
Purpose: Verifies the clap detector with synthetic clap and non-clap waveforms
so we can harden the scoring pipeline without using a real microphone.
Depends on: unittest, numpy, clap_detector.py
"""

from __future__ import annotations

import unittest

import numpy as np

from clap_detector import ClapCalibrationProfile, ClapDetector, ClapDetectorConfig


class ClapDetectorTests(unittest.TestCase):
    """Unit tests for the clap-specific audio detector."""

    def make_detector(
        self,
        clap_window_seconds: float = 1.2,
        cooldown_seconds: float = 5.0,
        sensitivity_preset: str = "balanced",
    ) -> ClapDetector:
        """Builds a deterministic detector tuned for synthetic fixtures."""

        return ClapDetector(
            ClapDetectorConfig(
                sample_rate=16_000,
                block_duration=0.025,
                event_window_seconds=0.050,
                warmup_seconds=0.0,
                target_claps=2,
                clap_window_seconds=clap_window_seconds,
                cooldown_seconds=cooldown_seconds,
                min_clap_gap_seconds=0.16,
                refractory_seconds=0.10,
                min_peak=0.06,
                min_rms=0.006,
                min_transient=0.010,
                energy_ratio_threshold=1.7,
                transient_ratio_threshold=1.6,
                min_crest_factor=2.2,
                min_band_ratio=1.18,
                min_high_band_share=0.18,
                min_spectral_flatness=0.15,
                min_clap_score=4.0,
            ),
            sensitivity_preset=sensitivity_preset,
        )

    # --- Synthetic fixtures ---------------------------------------------

    def silence_chunk(self, length: int = 400) -> np.ndarray:
        """Stable quiet chunk used to let one clap event decay and confirm."""

        return np.zeros(length, dtype=np.float32)

    def clap_chunk(self, amplitude: float = 1.0, seed: int = 42) -> np.ndarray:
        """Broadband decaying burst that more closely matches a real clap."""

        rng = np.random.default_rng(seed)
        noise = rng.normal(0.0, 1.0, 400).astype(np.float32)
        envelope = np.exp(-np.linspace(0.0, 4.0, 400)).astype(np.float32)
        burst = amplitude * 0.30 * noise * envelope
        burst[:18] += amplitude * 0.22 * np.hanning(18).astype(np.float32)
        return burst.astype(np.float32)

    def desk_hit_chunk(self) -> np.ndarray:
        """Low-frequency knock that should not pass the clap spectral filters."""

        sample_rate = 16_000
        duration = 0.025
        t = np.linspace(0.0, duration, int(sample_rate * duration), endpoint=False)
        envelope = np.exp(-45.0 * t)
        signal = np.sin(2.0 * np.pi * 220.0 * t)
        return (0.9 * envelope * signal).astype(np.float32)

    def finger_snap_chunk(self) -> np.ndarray:
        """Short narrow-band burst sharper than speech but less clap-like overall."""

        rng = np.random.default_rng(7)
        noise = rng.normal(0.0, 1.0, 400).astype(np.float32)
        envelope = np.exp(-np.linspace(0.0, 8.0, 400)).astype(np.float32)
        tone = np.sin(np.linspace(0.0, 24.0, 400)).astype(np.float32)
        return (0.08 * noise * envelope + 0.03 * tone * envelope).astype(np.float32)

    def speech_burst_chunk(self) -> np.ndarray:
        """Voiced low-mid burst that should stay below the clap score."""

        sample_rate = 16_000
        duration = 0.025
        t = np.linspace(0.0, duration, int(sample_rate * duration), endpoint=False)
        envelope = 0.5 + 0.5 * np.sin(2.0 * np.pi * 18.0 * t)
        signal = np.sin(2.0 * np.pi * 260.0 * t) + 0.5 * np.sin(2.0 * np.pi * 520.0 * t)
        return (0.35 * envelope * signal).astype(np.float32)

    def typing_chunk(self) -> np.ndarray:
        """Small sharp tick used to simulate keyboard typing noise."""

        tick = np.zeros(400, dtype=np.float32)
        tick[100:105] = np.array([0.0, 0.18, -0.12, 0.08, 0.0], dtype=np.float32)
        return tick

    def emit_clap(self, detector: ClapDetector, timestamp: float, amplitude: float = 1.0):
        """Feeds one clap plus a quiet tail so the event state machine can confirm it."""

        detector.process_chunk(self.clap_chunk(amplitude=amplitude), timestamp=timestamp)
        detector.process_chunk(self.silence_chunk(), timestamp=timestamp + 0.03)
        return detector.process_chunk(self.silence_chunk(), timestamp=timestamp + 0.06)

    # --- Core clap behavior ---------------------------------------------

    def test_single_clap_counts_once(self) -> None:
        """One clap event should advance the clap counter without triggering."""

        detector = self.make_detector()
        update = self.emit_clap(detector, timestamp=0.0)

        self.assertTrue(update.is_impulse)
        self.assertEqual(update.clap_count, 1)
        self.assertFalse(update.triggered)
        self.assertEqual(update.status, "clap 1/2")

    def test_double_clap_triggers(self) -> None:
        """Two clap events inside the timing window should trigger once."""

        detector = self.make_detector()
        self.emit_clap(detector, timestamp=0.0)
        update = self.emit_clap(detector, timestamp=0.34)

        self.assertTrue(update.triggered)
        self.assertEqual(update.status, "triggered")
        self.assertEqual(update.clap_count, 0)

    def test_repeated_clap_inside_cooldown_is_ignored(self) -> None:
        """New impulses during cooldown must not retrigger the action path."""

        detector = self.make_detector(cooldown_seconds=2.0)
        self.emit_clap(detector, timestamp=0.0)
        self.emit_clap(detector, timestamp=0.34)
        update = self.emit_clap(detector, timestamp=0.68)

        self.assertFalse(update.triggered)
        self.assertEqual(update.status, "cooldown")

    def test_boundary_clap_is_captured_by_overlap_window(self) -> None:
        """A clap split across chunk boundaries should still confirm once."""

        detector = self.make_detector(sensitivity_preset="sensitive")
        full = self.clap_chunk()
        detector.process_chunk(full[:200], timestamp=0.0)
        detector.process_chunk(full[200:], timestamp=0.0125)
        detector.process_chunk(self.silence_chunk(length=200), timestamp=0.0375)
        update = detector.process_chunk(self.silence_chunk(length=200), timestamp=0.0625)

        self.assertTrue(update.is_impulse)
        self.assertEqual(update.clap_count, 1)

    def test_sustained_event_does_not_count_twice(self) -> None:
        """A long noisy burst should remain one clap event, not two separate impulses."""

        detector = self.make_detector()
        first = detector.process_chunk(self.clap_chunk(amplitude=1.0, seed=11), timestamp=0.0)
        second = detector.process_chunk(self.clap_chunk(amplitude=0.55, seed=11), timestamp=0.05)
        third = detector.process_chunk(self.silence_chunk(), timestamp=0.08)
        fourth = detector.process_chunk(self.silence_chunk(), timestamp=0.11)

        impulses = [update.is_impulse for update in [first, second, third, fourth]]
        self.assertEqual(sum(bool(value) for value in impulses), 1)
        self.assertLessEqual(fourth.clap_count, 1)

    # --- False positive guards ------------------------------------------

    def test_desk_hit_is_not_classified_as_clap(self) -> None:
        """A desk knock should fail the clap score and stay idle."""

        detector = self.make_detector()
        update = detector.process_chunk(self.desk_hit_chunk(), timestamp=0.0)

        self.assertFalse(update.is_impulse)
        self.assertNotEqual(update.status, "triggered")

    def test_finger_snap_is_not_classified_as_clap(self) -> None:
        """A narrow-band snap should not pass the broadband clap filters."""

        detector = self.make_detector()
        update = detector.process_chunk(self.finger_snap_chunk(), timestamp=0.0)

        self.assertFalse(update.is_impulse)

    def test_speech_burst_is_not_classified_as_clap(self) -> None:
        """A voiced speech-like burst should stay below the clap score."""

        detector = self.make_detector()
        update = detector.process_chunk(self.speech_burst_chunk(), timestamp=0.0)

        self.assertFalse(update.is_impulse)

    def test_keyboard_typing_is_not_classified_as_clap(self) -> None:
        """Small typing ticks should stay below the absolute thresholds."""

        detector = self.make_detector()
        update = detector.process_chunk(self.typing_chunk(), timestamp=0.0)

        self.assertFalse(update.is_impulse)

    def test_calibration_profile_lowers_thresholds_for_weaker_claps(self) -> None:
        """A saved profile should let the detector accept softer real-world clap energy."""

        base_detector = ClapDetector(
            ClapDetectorConfig(
                sample_rate=16_000,
                block_duration=0.025,
                event_window_seconds=0.050,
                warmup_seconds=0.0,
            ),
            sensitivity_preset="balanced",
        )
        weak_update = self.emit_clap(base_detector, timestamp=0.0, amplitude=0.04)
        self.assertFalse(weak_update.is_impulse)

        profile = ClapCalibrationProfile(
            captured_claps=6,
            calibrated_at=123.0,
            noise_rms=0.003,
            noise_transient=0.006,
            clap_peak=0.07,
            clap_rms=0.007,
            clap_transient=0.013,
            clap_score=4.3,
            crest_factor=2.5,
            band_ratio=1.2,
            high_band_share=0.20,
            spectral_flatness=0.18,
            observed_gap_seconds=0.28,
            peak_min=0.05,
            peak_median=0.07,
            peak_max=0.13,
            rms_min=0.005,
            rms_median=0.008,
            rms_max=0.014,
            transient_min=0.011,
            transient_median=0.014,
            transient_max=0.022,
        )
        tuned_detector = ClapDetector(
            ClapDetectorConfig(
                sample_rate=16_000,
                block_duration=0.025,
                event_window_seconds=0.050,
                warmup_seconds=0.0,
                calibration_profile=profile,
            ),
            sensitivity_preset="balanced",
        )
        tuned_update = self.emit_clap(tuned_detector, timestamp=0.0, amplitude=0.04)
        self.assertTrue(tuned_update.is_impulse)


if __name__ == "__main__":
    unittest.main()

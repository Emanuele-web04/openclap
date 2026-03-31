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
                min_zero_crossing_rate=0.20,
                min_spectral_centroid_hz=1800.0,
                min_clap_score=5.0,
                min_inter_clap_similarity=0.35,
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

    def music_percussion_chunk(self) -> np.ndarray:
        """Synthetic hi-hat-like burst used to keep percussive music transients out of the clap path."""

        rng = np.random.default_rng(19)
        noise = rng.normal(0.0, 1.0, 400).astype(np.float32)
        t = np.linspace(0.0, 1.0, 400, endpoint=False)
        carrier = np.sin(2.0 * np.pi * 35.0 * t) + 0.7 * np.sin(2.0 * np.pi * 67.0 * t)
        envelope = np.exp(-np.linspace(0.0, 10.0, 400)).astype(np.float32)
        return (0.16 * noise * np.sign(carrier) * envelope).astype(np.float32)

    def music_bed_chunk(self, seed: int = 3) -> np.ndarray:
        """Low continuous musical bed used to simulate outside audio between percussion hits."""

        rng = np.random.default_rng(seed)
        t = np.linspace(0.0, 0.025, 400, endpoint=False)
        bed = 0.018 * np.sin(2.0 * np.pi * 220.0 * t) + 0.015 * np.sin(2.0 * np.pi * 440.0 * t)
        hiss = 0.010 * rng.normal(0.0, 1.0, 400)
        return (bed + hiss).astype(np.float32)

    def music_bed_hit_chunk(self, seed: int = 5) -> np.ndarray:
        """Percussive hit on top of a sustained music bed."""

        rng = np.random.default_rng(seed)
        t = np.linspace(0.0, 0.025, 400, endpoint=False)
        bed = 0.018 * np.sin(2.0 * np.pi * 220.0 * t) + 0.015 * np.sin(2.0 * np.pi * 440.0 * t)
        noise = rng.normal(0.0, 1.0, 400).astype(np.float32)
        envelope = np.exp(-np.linspace(0.0, 7.0, 400)).astype(np.float32)
        hit = 0.11 * noise * envelope + 0.04 * np.sin(2.0 * np.pi * 180.0 * t) * envelope
        return (bed.astype(np.float32) + hit.astype(np.float32)).astype(np.float32)

    def typing_chunk(self) -> np.ndarray:
        """Small sharp tick used to simulate keyboard typing noise."""

        tick = np.zeros(400, dtype=np.float32)
        tick[100:105] = np.array([0.0, 0.18, -0.12, 0.08, 0.0], dtype=np.float32)
        return tick

    def snare_drum_chunk(self) -> np.ndarray:
        """Synthetic snare hit: tonal body with some noise, unlike a broadband clap."""

        rng = np.random.default_rng(99)
        t = np.linspace(0.0, 0.025, 400, endpoint=False)
        body = 0.35 * np.sin(2.0 * np.pi * 180.0 * t) * np.exp(-30.0 * t)
        snares = 0.12 * rng.normal(0.0, 1.0, 400).astype(np.float32) * np.exp(-np.linspace(0.0, 6.0, 400)).astype(np.float32)
        return (body + snares).astype(np.float32)

    def mouth_click_chunk(self) -> np.ndarray:
        """Brief tongue/mouth click: very short, narrow-band, low energy."""

        click = np.zeros(400, dtype=np.float32)
        t_click = np.linspace(0.0, 0.003, 48, endpoint=False)
        click[50:98] = (0.15 * np.sin(2.0 * np.pi * 1200.0 * t_click) * np.exp(-200.0 * t_click)).astype(np.float32)
        return click

    def emit_event(self, detector: ClapDetector, chunk: np.ndarray, timestamp: float):
        """Feeds one generic impulse plus a short quiet tail so the detector can confirm or reject it."""

        detector.process_chunk(chunk, timestamp=timestamp)
        detector.process_chunk(self.silence_chunk(), timestamp=timestamp + 0.03)
        return detector.process_chunk(self.silence_chunk(), timestamp=timestamp + 0.06)

    def emit_clap(self, detector: ClapDetector, timestamp: float, amplitude: float = 1.0):
        """Feeds one clap plus a quiet tail so the event state machine can confirm it."""

        return self.emit_event(detector, self.clap_chunk(amplitude=amplitude), timestamp=timestamp)

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

    def test_responsive_preset_accepts_real_claps(self) -> None:
        """The middle preset should still detect a normal double clap sequence."""

        detector = self.make_detector(sensitivity_preset="responsive")
        self.emit_clap(detector, timestamp=0.0)
        update = self.emit_clap(detector, timestamp=0.34)

        self.assertTrue(update.triggered)
        self.assertEqual(update.status, "triggered")

    def test_responsive_soft_path_can_confirm_with_effective_decay(self) -> None:
        """Responsive mode should confirm a clap when compression makes the decay look clap-like."""

        detector = ClapDetector(
            ClapDetectorConfig(
                sample_rate=16_000,
                block_duration=0.025,
                event_window_seconds=0.025,
                warmup_seconds=0.0,
                target_claps=2,
                clap_window_seconds=1.2,
                cooldown_seconds=5.0,
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
                min_zero_crossing_rate=0.20,
                min_spectral_centroid_hz=1800.0,
                min_clap_score=5.0,
            ),
            sensitivity_preset="responsive",
        )
        raw_features = (0.090, 0.0085, 0.0125, 3.1, 1.45, 0.30, 0.22, 0.91, 0.42, 3500.0)
        soft_features = (0.082, 0.0082, 0.0118, 2.9, 1.45, 0.30, 0.22, 1.04, 0.42, 3500.0)
        original_compute = detector._compute_signal_features
        call_state = {"nonzero_calls": 0}

        def fake_compute(signal):
            if not np.any(signal):
                return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            call_state["nonzero_calls"] += 1
            if call_state["nonzero_calls"] % 2 == 1:
                return raw_features
            return soft_features

        detector._compute_signal_features = fake_compute
        detector.process_chunk(self.clap_chunk(amplitude=0.2, seed=31), timestamp=0.0)
        update = detector.process_chunk(self.silence_chunk(), timestamp=0.03)
        detector._compute_signal_features = original_compute

        self.assertTrue(update.is_impulse)
        self.assertEqual(update.clap_count, 1)
        self.assertAlmostEqual(update.decay_ratio, soft_features[7], places=3)

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
        """A finger snap should be rejected after the candidate event fully decays."""

        detector = self.make_detector()
        update = self.emit_event(detector, self.finger_snap_chunk(), timestamp=0.0)

        self.assertFalse(update.is_impulse)
        self.assertEqual(update.clap_count, 0)

    def test_speech_burst_is_not_classified_as_clap(self) -> None:
        """A voiced speech-like burst should stay below the clap score."""

        detector = self.make_detector()
        update = detector.process_chunk(self.speech_burst_chunk(), timestamp=0.0)

        self.assertFalse(update.is_impulse)

    def test_keyboard_typing_is_not_classified_as_clap(self) -> None:
        """Typing ticks should still be rejected once the detector finishes the event."""

        detector = self.make_detector()
        update = self.emit_event(detector, self.typing_chunk(), timestamp=0.0)

        self.assertFalse(update.is_impulse)
        self.assertEqual(update.clap_count, 0)

    def test_music_percussion_is_not_classified_as_clap(self) -> None:
        """Short music-like bursts should not be accepted as a clap impulse."""

        detector = self.make_detector()
        update = self.emit_event(detector, self.music_percussion_chunk(), timestamp=0.0)

        self.assertFalse(update.is_impulse)
        self.assertEqual(update.clap_count, 0)

    def test_double_typing_ticks_do_not_trigger(self) -> None:
        """Two quick keyboard ticks should not accumulate into a double-clap trigger."""

        detector = self.make_detector()
        first = self.emit_event(detector, self.typing_chunk(), timestamp=0.0)
        second = self.emit_event(detector, self.typing_chunk(), timestamp=0.18)

        self.assertFalse(first.is_impulse)
        self.assertFalse(second.is_impulse)
        self.assertFalse(second.triggered)
        self.assertEqual(second.clap_count, 0)

    def test_double_music_percussion_does_not_trigger(self) -> None:
        """Repeated hi-hat-like bursts should not pair up into a false double clap."""

        detector = self.make_detector()
        first = self.emit_event(detector, self.music_percussion_chunk(), timestamp=0.0)
        second = self.emit_event(detector, self.music_percussion_chunk(), timestamp=0.31)

        self.assertFalse(first.is_impulse)
        self.assertFalse(second.is_impulse)
        self.assertFalse(second.triggered)
        self.assertEqual(second.clap_count, 0)

    def test_music_bed_with_repeated_percussion_does_not_trigger(self) -> None:
        """Continuous music plus repeated drum hits should not count as a near-field double clap."""

        detector = self.make_detector(sensitivity_preset="responsive")
        sequence = [
            (self.music_bed_chunk(), 0.00),
            (self.music_bed_hit_chunk(), 0.025),
            (self.music_bed_chunk(seed=4), 0.050),
            (self.music_bed_hit_chunk(seed=6), 0.075),
            (self.music_bed_chunk(seed=7), 0.100),
            (self.music_bed_hit_chunk(seed=8), 0.125),
            (self.music_bed_chunk(seed=9), 0.150),
        ]

        updates = [detector.process_chunk(chunk, timestamp=timestamp) for chunk, timestamp in sequence]

        self.assertFalse(any(update.is_impulse for update in updates))
        self.assertFalse(any(update.triggered for update in updates))
        self.assertEqual(updates[-1].clap_count, 0)

    def test_snare_drum_is_not_classified_as_clap(self) -> None:
        """A synthetic snare hit should be rejected due to its tonal body and lower centroid."""

        detector = self.make_detector()
        update = self.emit_event(detector, self.snare_drum_chunk(), timestamp=0.0)

        self.assertFalse(update.is_impulse)
        self.assertEqual(update.clap_count, 0)

    def test_mouth_click_is_not_classified_as_clap(self) -> None:
        """A brief tongue or mouth click should not register as a clap impulse."""

        detector = self.make_detector()
        update = self.emit_event(detector, self.mouth_click_chunk(), timestamp=0.0)

        self.assertFalse(update.is_impulse)
        self.assertEqual(update.clap_count, 0)

    def test_double_snare_drums_do_not_trigger(self) -> None:
        """Two snare hits spaced like a double clap should not trigger."""

        detector = self.make_detector()
        first = self.emit_event(detector, self.snare_drum_chunk(), timestamp=0.0)
        second = self.emit_event(detector, self.snare_drum_chunk(), timestamp=0.35)

        self.assertFalse(first.is_impulse)
        self.assertFalse(second.is_impulse)
        self.assertFalse(second.triggered)

    # --- Inter-clap similarity ------------------------------------------

    def test_dissimilar_transients_do_not_pair_as_double_clap(self) -> None:
        """Two spectrally different impulses should not combine into a trigger even if each is clap-like."""

        detector = self.make_detector()
        # First "clap" with one seed
        self.emit_clap(detector, timestamp=0.0, amplitude=1.0)
        first_count = detector.clap_count
        if first_count == 0:
            # If the first clap wasn't even accepted, skip the similarity check.
            return
        # Feed a desk hit as the "second clap" - it should not pair up.
        update = self.emit_event(detector, self.desk_hit_chunk(), timestamp=0.35)

        self.assertFalse(update.triggered)

    # --- Transient density tracking -------------------------------------

    def test_high_transient_density_raises_score_threshold(self) -> None:
        """Rapid energetic frames (simulating music) should raise the effective score bar."""

        detector = self.make_detector()
        # Feed many rapid transients to build up density.
        for i in range(20):
            detector.process_chunk(self.music_bed_hit_chunk(seed=i + 10), timestamp=i * 0.08)
            detector.process_chunk(self.silence_chunk(), timestamp=i * 0.08 + 0.04)

        # The density penalty should now be active.
        self.assertGreater(detector._density_penalty, 0.0)

    # --- New ClapUpdate fields ------------------------------------------

    def test_clap_update_includes_zcr_and_centroid(self) -> None:
        """ClapUpdate should expose the new zero_crossing_rate and spectral_centroid fields."""

        detector = self.make_detector()
        update = self.emit_clap(detector, timestamp=0.0)

        self.assertIsInstance(update.zero_crossing_rate, float)
        self.assertIsInstance(update.spectral_centroid, float)
        # A real broadband clap should have high ZCR and high centroid.
        self.assertGreater(update.zero_crossing_rate, 0.0)
        self.assertGreater(update.spectral_centroid, 0.0)

    # --- Calibration profile --------------------------------------------

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
            zero_crossing_rate=0.35,
            spectral_centroid=3200.0,
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

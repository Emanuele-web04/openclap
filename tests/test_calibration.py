"""
FILE: test_calibration.py
Purpose: Verifies the guided calibration flow and profile building helpers.
Depends on: unittest, calibration.py, clap_detector.py
"""

from __future__ import annotations

import unittest

from calibration import CalibrationSession, build_calibration_profile
from clap_detector import ClapUpdate


def make_update(
    *,
    is_impulse: bool = False,
    peak: float = 0.02,
    rms: float = 0.003,
    transient: float = 0.006,
    crest_factor: float = 2.0,
    band_ratio: float = 1.0,
    high_band_share: float = 0.12,
    spectral_flatness: float = 0.12,
    clap_score: float = 2.0,
) -> ClapUpdate:
    """Builds one stable update fixture for calibration-focused tests."""

    return ClapUpdate(
        status="listening",
        clap_count=0,
        triggered=False,
        is_impulse=is_impulse,
        peak=peak,
        rms=rms,
        transient=transient,
        crest_factor=crest_factor,
        band_ratio=band_ratio,
        high_band_share=high_band_share,
        spectral_flatness=spectral_flatness,
        clap_score=clap_score,
        noise_floor=0.003,
        transient_floor=0.006,
        cooldown_remaining=0.0,
        warmup_remaining=0.0,
        decay_ratio=1.25,
        event_state="idle",
    )


class CalibrationTests(unittest.TestCase):
    """Unit tests for the guided calibration flow."""

    def test_build_calibration_profile(self) -> None:
        """A silence baseline plus confirmed claps should produce one profile."""

        silence = [make_update() for _ in range(8)]
        claps = [
            make_update(
                is_impulse=True,
                peak=0.11 + index * 0.01,
                rms=0.012 + index * 0.001,
                transient=0.024 + index * 0.002,
                crest_factor=2.8 + index * 0.05,
                band_ratio=1.3 + index * 0.03,
                high_band_share=0.26 + index * 0.01,
                spectral_flatness=0.20 + index * 0.01,
                clap_score=4.8 + index * 0.2,
            )
            for index in range(6)
        ]
        profile = build_calibration_profile(silence, claps, [0.0, 0.28, 1.0, 1.29, 2.0, 2.27])

        self.assertEqual(profile.captured_claps, 6)
        self.assertGreater(profile.clap_peak, profile.noise_rms)
        self.assertGreater(profile.observed_gap_seconds, 0.2)
        self.assertGreater(profile.peak_max, profile.peak_min)
        self.assertGreater(profile.rms_max, profile.rms_min)
        self.assertGreater(profile.transient_max, profile.transient_min)

    def test_calibration_session_completes_after_silence_and_six_claps(self) -> None:
        """The guided session should move from silence to capture and then complete."""

        session = CalibrationSession(silence_seconds=0.1, target_claps=6, max_capture_seconds=5.0)
        session.start(0.0)
        session.observe(make_update(), 0.0)
        session.observe(make_update(), 0.12)
        self.assertEqual(session.progress.state, "claps")

        timestamps = [0.20, 0.45, 0.90, 1.15, 1.60, 1.84]
        for index, timestamp in enumerate(timestamps):
            session.observe(
                make_update(
                    is_impulse=True,
                    peak=0.12 + index * 0.01,
                    rms=0.013 + index * 0.001,
                    transient=0.026 + index * 0.001,
                    crest_factor=3.0,
                    band_ratio=1.4,
                    high_band_share=0.28,
                    spectral_flatness=0.22,
                    clap_score=5.0,
                ),
                timestamp,
            )

        self.assertEqual(session.progress.state, "complete")
        self.assertIsNotNone(session.profile)


if __name__ == "__main__":
    unittest.main()

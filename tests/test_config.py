"""
FILE: test_config.py
Purpose: Verifies config defaults, persistence, and small update helpers.
Depends on: unittest, tempfile, app_paths.py, config.py
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app_paths import AppPaths
from clap_detector import ClapCalibrationProfile
from config import load_config, save_config, set_armed, set_input_device, set_sensitivity_preset


class ConfigTests(unittest.TestCase):
    """Unit tests for persistent config helpers."""

    def make_paths(self, temp_dir: str) -> AppPaths:
        """Builds isolated app paths inside a temporary fake home folder."""

        return AppPaths.from_home(Path(temp_dir))

    def test_missing_config_creates_defaults(self) -> None:
        """Loading config the first time should create a usable default file."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.make_paths(temp_dir)
            config = load_config(paths)

            self.assertTrue(paths.config_path.exists())
            self.assertTrue(config.service.armed)
            self.assertEqual(config.actions.codex_url, "codex://")

    def test_save_and_reload_round_trip(self) -> None:
        """Persisted values should survive a save/load cycle."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.make_paths(temp_dir)
            config = load_config(paths)
            config.actions.local_audio_file = "/tmp/test-song.mp3"
            config.service.input_device_name = "MacBook Pro Microphone"
            config.service.sensitivity_preset = "strict"
            config.detector.calibration_profile = ClapCalibrationProfile(
                captured_claps=6,
                calibrated_at=1234.5,
                noise_rms=0.004,
                noise_transient=0.009,
                clap_peak=0.14,
                clap_rms=0.018,
                clap_transient=0.031,
                clap_score=5.5,
                crest_factor=3.2,
                band_ratio=1.5,
                high_band_share=0.34,
                spectral_flatness=0.26,
                observed_gap_seconds=0.29,
            )
            save_config(paths, config)

            reloaded = load_config(paths)
            self.assertEqual(reloaded.actions.local_audio_file, "/tmp/test-song.mp3")
            self.assertEqual(reloaded.service.input_device_name, "MacBook Pro Microphone")
            self.assertEqual(reloaded.service.sensitivity_preset, "strict")
            self.assertIsNotNone(reloaded.detector.calibration_profile)
            self.assertEqual(reloaded.detector.calibration_profile.captured_claps, 6)

    def test_small_update_helpers_persist_changes(self) -> None:
        """Convenience setters should save armed state and input device choice."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.make_paths(temp_dir)
            set_armed(paths, False)
            set_input_device(paths, "USB Mic")
            set_sensitivity_preset(paths, "sensitive")

            updated = load_config(paths)
            self.assertFalse(updated.service.armed)
            self.assertEqual(updated.service.input_device_name, "USB Mic")
            self.assertEqual(updated.service.sensitivity_preset, "sensitive")


if __name__ == "__main__":
    unittest.main()

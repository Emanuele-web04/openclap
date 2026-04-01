"""
FILE: test_config.py
Purpose: Verifies config defaults, persistence, and small update helpers.
Depends on: unittest, tempfile, app_paths.py, config.py
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app_paths import AppPaths
from clap_detector import ClapCalibrationProfile
from config import (
    clear_target_app,
    load_config,
    save_config,
    set_armed,
    set_armed_on_launch,
    set_diagnostics_enabled,
    set_input_device,
    set_launch_at_login,
    set_sensitivity_preset,
    set_target_app,
    set_voice_enabled,
)


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
            self.assertTrue(config.service.armed_on_launch)
            self.assertTrue(config.app.launch_at_login)
            self.assertTrue(config.app.diagnostics_enabled)
            self.assertEqual(config.actions.target_app_path, "")
            self.assertEqual(config.actions.target_app_name, "")
            self.assertFalse(config.voice.enabled)
            self.assertEqual(config.voice.wake_phrase, "jarvis")
            self.assertEqual(config.voice.engine, "porcupine")

    def test_save_and_reload_round_trip(self) -> None:
        """Persisted values should survive a save/load cycle."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.make_paths(temp_dir)
            config = load_config(paths)
            config.actions.target_app_path = "/Applications/Notion.app"
            config.actions.target_app_name = "Notion"
            config.actions.local_audio_file = "/tmp/test-song.mp3"
            config.service.input_device_name = "MacBook Pro Microphone"
            config.service.sensitivity_preset = "strict"
            config.service.armed_on_launch = False
            config.app.launch_at_login = False
            config.app.diagnostics_enabled = False
            config.voice.enabled = True
            config.voice.sensitivity = 0.7
            config.voice.cooldown_seconds = 3.0
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
            self.assertEqual(reloaded.actions.target_app_path, "/Applications/Notion.app")
            self.assertEqual(reloaded.actions.target_app_name, "Notion")
            self.assertEqual(reloaded.actions.local_audio_file, "/tmp/test-song.mp3")
            self.assertEqual(reloaded.service.input_device_name, "MacBook Pro Microphone")
            self.assertEqual(reloaded.service.sensitivity_preset, "strict")
            self.assertFalse(reloaded.service.armed_on_launch)
            self.assertFalse(reloaded.app.launch_at_login)
            self.assertFalse(reloaded.app.diagnostics_enabled)
            self.assertTrue(reloaded.voice.enabled)
            self.assertEqual(reloaded.voice.wake_phrase, "jarvis")
            self.assertAlmostEqual(reloaded.voice.sensitivity, 0.7)
            self.assertAlmostEqual(reloaded.voice.cooldown_seconds, 3.0)
            self.assertIsNotNone(reloaded.detector.calibration_profile)
            self.assertEqual(reloaded.detector.calibration_profile.captured_claps, 6)

    def test_small_update_helpers_persist_changes(self) -> None:
        """Convenience setters should save armed state and input device choice."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.make_paths(temp_dir)
            set_armed(paths, False)
            set_armed_on_launch(paths, False)
            set_launch_at_login(paths, False)
            set_diagnostics_enabled(paths, False)
            set_input_device(paths, "USB Mic")
            set_sensitivity_preset(paths, "responsive")
            set_voice_enabled(paths, True)
            set_target_app(paths, "/Applications/Finder.app")
            clear_target_app(paths)

            updated = load_config(paths)
            self.assertFalse(updated.service.armed)
            self.assertFalse(updated.service.armed_on_launch)
            self.assertFalse(updated.app.launch_at_login)
            self.assertFalse(updated.app.diagnostics_enabled)
            self.assertEqual(updated.service.input_device_name, "USB Mic")
            self.assertEqual(updated.service.sensitivity_preset, "responsive")
            self.assertTrue(updated.voice.enabled)
            self.assertEqual(updated.actions.target_app_path, "")
            self.assertEqual(updated.actions.target_app_name, "")

    def test_legacy_codex_config_migrates_to_target_app(self) -> None:
        """Older configs without target_app fields should seed Codex when available."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.make_paths(temp_dir)
            paths.config_path.parent.mkdir(parents=True, exist_ok=True)
            paths.config_path.write_text(
                json.dumps(
                    {
                        "service": {"armed": True},
                        "detector": {},
                        "actions": {"local_audio_file": ""},
                    }
                ),
                encoding="utf-8",
            )

            legacy_codex_app = Path(temp_dir) / "Codex.app"
            legacy_codex_app.mkdir()
            with patch("config.LEGACY_DEFAULT_TARGET_APP_PATH", legacy_codex_app):
                migrated = load_config(paths)

            self.assertEqual(migrated.actions.target_app_path, str(legacy_codex_app))
            self.assertEqual(migrated.actions.target_app_name, "Codex")


if __name__ == "__main__":
    unittest.main()

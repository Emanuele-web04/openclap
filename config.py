"""
FILE: config.py
Purpose: Loads, validates, and persists user configuration for the clap helper.
Depends on: app_paths.py and clap_detector.py for shared defaults.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from app_paths import AppPaths, ensure_runtime_directories
from clap_detector import ClapCalibrationProfile, ClapDetectorConfig


@dataclass
class ServiceSettings:
    """User-facing runtime settings for the background detector service."""

    armed: bool = True
    input_device_name: Optional[str] = None
    debug_logging: bool = False
    sensitivity_preset: str = "balanced"


@dataclass
class ActionSettings:
    """Trigger targets launched after a successful double clap."""

    codex_url: str = "codex://"
    local_audio_file: str = ""
    fallback_media_url: str = ""


@dataclass
class AppConfig:
    """Top-level persisted configuration for the clap helper."""

    service: ServiceSettings = field(default_factory=ServiceSettings)
    detector: ClapDetectorConfig = field(default_factory=ClapDetectorConfig)
    actions: ActionSettings = field(default_factory=ActionSettings)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the config into JSON-friendly nested dictionaries."""

        return {
            "service": asdict(self.service),
            "detector": asdict(self.detector),
            "actions": asdict(self.actions),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "AppConfig":
        """Builds a config from partially filled JSON while keeping defaults."""

        service = ServiceSettings(**_filter_known_fields(ServiceSettings, raw.get("service", {})))
        if service.sensitivity_preset not in {"balanced", "sensitive", "strict"}:
            service.sensitivity_preset = "balanced"

        detector_raw = dict(raw.get("detector", {}))
        profile_raw = detector_raw.get("calibration_profile")
        calibration_profile = None
        if isinstance(profile_raw, dict):
            calibration_profile = ClapCalibrationProfile(
                **_filter_known_fields(ClapCalibrationProfile, profile_raw)
            )
        detector_values = _filter_known_fields(ClapDetectorConfig, detector_raw)
        detector_values["calibration_profile"] = calibration_profile
        detector = ClapDetectorConfig(**detector_values)
        actions = ActionSettings(**_filter_known_fields(ActionSettings, raw.get("actions", {})))
        return cls(service=service, detector=detector, actions=actions)


def _filter_known_fields(dataclass_type, raw_values: Dict[str, Any]) -> Dict[str, Any]:
    """Drops unknown keys so forward-compatible config files do not break loading."""

    known_fields = dataclass_type.__dataclass_fields__.keys()
    return {key: value for key, value in raw_values.items() if key in known_fields}


def load_config(paths: AppPaths) -> AppConfig:
    """Loads the config from disk, creating a default file the first time."""

    ensure_runtime_directories(paths)
    if not paths.config_path.exists():
        config = AppConfig()
        save_config(paths, config)
        return config

    try:
        raw = json.loads(paths.config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppConfig()

    return AppConfig.from_dict(raw)


def save_config(paths: AppPaths, config: AppConfig) -> None:
    """Persists the config atomically to avoid partial writes."""

    ensure_runtime_directories(paths)
    temp_path = paths.config_path.with_suffix(".tmp")
    payload = json.dumps(config.to_dict(), indent=2, sort_keys=True)
    temp_path.write_text(payload + "\n", encoding="utf-8")
    os.replace(temp_path, paths.config_path)


def set_armed(paths: AppPaths, armed: bool) -> AppConfig:
    """Updates the persisted armed/disarmed state and returns the new config."""

    config = load_config(paths)
    config.service.armed = armed
    save_config(paths, config)
    return config


def set_input_device(paths: AppPaths, input_device_name: Optional[str]) -> AppConfig:
    """Stores the preferred microphone name and returns the updated config."""

    config = load_config(paths)
    config.service.input_device_name = input_device_name
    save_config(paths, config)
    return config


def set_sensitivity_preset(paths: AppPaths, sensitivity_preset: str) -> AppConfig:
    """Stores the preferred clap sensitivity preset and returns the updated config."""

    config = load_config(paths)
    config.service.sensitivity_preset = sensitivity_preset if sensitivity_preset in {
        "balanced",
        "sensitive",
        "strict",
    } else "balanced"
    save_config(paths, config)
    return config


def set_audio_file(paths: AppPaths, local_audio_file: str) -> AppConfig:
    """Stores the preferred local audio file used after a successful trigger."""

    config = load_config(paths)
    config.actions.local_audio_file = str(Path(local_audio_file).expanduser()) if local_audio_file else ""
    save_config(paths, config)
    return config

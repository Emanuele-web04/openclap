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

LEGACY_DEFAULT_TARGET_APP_PATH = Path("/Applications/Codex.app")


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

    target_app_path: str = ""
    target_app_name: str = ""
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
        if service.sensitivity_preset not in {"balanced", "responsive", "sensitive", "strict"}:
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
        if actions.target_app_path and not actions.target_app_name:
            actions.target_app_name = derive_app_name(actions.target_app_path)
        return cls(service=service, detector=detector, actions=actions)


def _filter_known_fields(dataclass_type, raw_values: Dict[str, Any]) -> Dict[str, Any]:
    """Drops unknown keys so forward-compatible config files do not break loading."""

    known_fields = dataclass_type.__dataclass_fields__.keys()
    return {key: value for key, value in raw_values.items() if key in known_fields}


def derive_app_name(app_bundle_path: str) -> str:
    """Builds a display-friendly app name from a saved .app bundle path."""

    if not app_bundle_path:
        return ""
    return Path(app_bundle_path).expanduser().stem


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

    config = AppConfig.from_dict(raw)
    if _migrate_legacy_target_app(raw, config):
        save_config(paths, config)
    return config


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
        "responsive",
        "sensitive",
        "strict",
    } else "balanced"
    save_config(paths, config)
    return config


def set_target_app(paths: AppPaths, target_app_path: str) -> AppConfig:
    """Stores the chosen target app bundle path and a cached display name."""

    config = load_config(paths)
    normalized_path = str(Path(target_app_path).expanduser()) if target_app_path else ""
    config.actions.target_app_path = normalized_path
    config.actions.target_app_name = derive_app_name(normalized_path)
    save_config(paths, config)
    return config


def clear_target_app(paths: AppPaths) -> AppConfig:
    """Clears any previously selected target app from the saved config."""

    return set_target_app(paths, "")


def set_audio_file(paths: AppPaths, local_audio_file: str) -> AppConfig:
    """Stores the preferred local audio file used after a successful trigger."""

    config = load_config(paths)
    config.actions.local_audio_file = str(Path(local_audio_file).expanduser()) if local_audio_file else ""
    save_config(paths, config)
    return config


def _migrate_legacy_target_app(raw: Dict[str, Any], config: AppConfig) -> bool:
    """Seeds Codex for older configs that predate the generic target-app setting."""

    raw_actions = raw.get("actions", {})
    if not isinstance(raw_actions, dict):
        return False
    if "target_app_path" in raw_actions:
        return False
    if config.actions.target_app_path:
        return False
    if not LEGACY_DEFAULT_TARGET_APP_PATH.exists():
        return False

    config.actions.target_app_path = str(LEGACY_DEFAULT_TARGET_APP_PATH)
    config.actions.target_app_name = derive_app_name(config.actions.target_app_path)
    return True

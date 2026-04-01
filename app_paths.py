"""
FILE: app_paths.py
Purpose: Centralizes filesystem locations plus shared app metadata such as the
bundle id, version, and LaunchAgent labels used across runtime and packaging.
Depends on: Python stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
import shutil
from pathlib import Path


APP_NAME = "OpenClap"
APP_VERSION = "0.1.0"
APP_BUNDLE_ID = "com.emanuele.openclap"
LEGACY_APP_NAME = "ClapTrigger"
SERVICE_LABEL = "com.emanuele.clapd"
MENU_LABEL = "com.emanuele.clapmenu"


@dataclass(frozen=True)
class AppPaths:
    """Resolved filesystem locations for runtime state and install artifacts."""

    app_support_dir: Path
    logs_dir: Path
    socket_path: Path
    config_path: Path
    launch_agents_dir: Path
    daemon_plist_path: Path
    menu_plist_path: Path

    @classmethod
    def from_home(cls, home: Path | None = None) -> "AppPaths":
        """Builds the default macOS paths under the current user's Library."""

        user_home = home or Path.home()
        library_dir = user_home / "Library"
        app_support_dir = library_dir / "Application Support" / APP_NAME
        logs_dir = library_dir / "Logs" / APP_NAME
        launch_agents_dir = library_dir / "LaunchAgents"
        return cls(
            app_support_dir=app_support_dir,
            logs_dir=logs_dir,
            socket_path=app_support_dir / "ctl.sock",
            config_path=app_support_dir / "config.json",
            launch_agents_dir=launch_agents_dir,
            daemon_plist_path=launch_agents_dir / f"{SERVICE_LABEL}.plist",
            menu_plist_path=launch_agents_dir / f"{MENU_LABEL}.plist",
        )


def ensure_runtime_directories(paths: AppPaths) -> None:
    """Creates the folders needed by the service, menu bar app, and installer."""

    _migrate_legacy_runtime_paths(paths)
    paths.app_support_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    paths.launch_agents_dir.mkdir(parents=True, exist_ok=True)


def _migrate_legacy_runtime_paths(paths: AppPaths) -> None:
    """Moves ClapTrigger support folders to the OpenClap names the first time they are needed."""

    if APP_NAME == LEGACY_APP_NAME:
        return

    library_dir = paths.app_support_dir.parent.parent
    legacy_support_dir = library_dir / "Application Support" / LEGACY_APP_NAME
    legacy_logs_dir = library_dir / "Logs" / LEGACY_APP_NAME

    if legacy_support_dir.exists() and not paths.app_support_dir.exists():
        paths.app_support_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_support_dir), str(paths.app_support_dir))

    if legacy_logs_dir.exists() and not paths.logs_dir.exists():
        paths.logs_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_logs_dir), str(paths.logs_dir))

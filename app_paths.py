"""
FILE: app_paths.py
Purpose: Centralizes filesystem locations for config, logs, sockets, and
LaunchAgents used by the always-on clap helper.
Depends on: Python stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


APP_NAME = "ClapTrigger"
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

    paths.app_support_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    paths.launch_agents_dir.mkdir(parents=True, exist_ok=True)

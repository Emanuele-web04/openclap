"""
FILE: launch_agents.py
Purpose: Generates and installs LaunchAgent plists for the daemon and menu bar
runtime so the helper starts automatically at login from either source or a
packaged .app bundle.
Depends on: app_paths.py for labels and destinations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from typing import Dict, List

from app_paths import AppPaths, MENU_LABEL, SERVICE_LABEL, ensure_runtime_directories
from runtime_env import RuntimeEnvironment


@dataclass(frozen=True)
class LaunchTarget:
    """Base executable information shared by both daemon and menu LaunchAgents."""

    base_program_arguments: List[str]
    working_directory: Path
    environment_variables: Dict[str, str] = field(
        default_factory=lambda: {
            "PYTHONUNBUFFERED": "1",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        }
    )

    def command_arguments(self, command: str) -> List[str]:
        """Builds one ProgramArguments payload for a named subcommand."""

        return [*self.base_program_arguments, command]


def build_launch_agent_plist(
    label: str,
    program_arguments: List[str],
    working_directory: str,
    stdout_path: str,
    stderr_path: str,
    keep_alive: object,
    environment_variables: Dict[str, str] | None = None,
) -> Dict[str, object]:
    """Builds one LaunchAgent plist payload."""

    payload = {
        "Label": label,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": working_directory,
        "RunAtLoad": True,
        "KeepAlive": keep_alive,
        "StandardOutPath": stdout_path,
        "StandardErrorPath": stderr_path,
    }
    if environment_variables:
        payload["EnvironmentVariables"] = environment_variables
    return payload


def resolve_launch_target(runtime: RuntimeEnvironment) -> LaunchTarget:
    """Chooses launchd arguments for either source mode or bundled app mode."""

    if runtime.is_bundled_app:
        return LaunchTarget(
            base_program_arguments=[str(runtime.executable_path)],
            working_directory=runtime.working_directory,
        )

    bundled_python = runtime.project_root / ".venv" / "bin" / "python"
    python_executable = str(bundled_python) if bundled_python.exists() else sys.executable
    return LaunchTarget(
        base_program_arguments=[python_executable, str(runtime.project_root / "main.py")],
        working_directory=runtime.working_directory,
    )


def install_launch_agents(paths: AppPaths, runtime: RuntimeEnvironment, dry_run: bool = False) -> Dict[str, str]:
    """Writes and optionally loads the daemon and menu bar LaunchAgents."""

    ensure_runtime_directories(paths)
    launch_target = resolve_launch_target(runtime)

    daemon_plist = build_launch_agent_plist(
        label=SERVICE_LABEL,
        program_arguments=launch_target.command_arguments("daemon"),
        working_directory=str(launch_target.working_directory),
        stdout_path=str(paths.logs_dir / "clapd.launchd.out.log"),
        stderr_path=str(paths.logs_dir / "clapd.launchd.err.log"),
        keep_alive=True,
        environment_variables=launch_target.environment_variables,
    )
    menu_plist = build_launch_agent_plist(
        label=MENU_LABEL,
        program_arguments=launch_target.command_arguments("menubar"),
        working_directory=str(launch_target.working_directory),
        stdout_path=str(paths.logs_dir / "clapmenu.launchd.out.log"),
        stderr_path=str(paths.logs_dir / "clapmenu.launchd.err.log"),
        keep_alive={"SuccessfulExit": False},
        environment_variables=launch_target.environment_variables,
    )

    if not dry_run:
        paths.daemon_plist_path.write_bytes(plistlib.dumps(daemon_plist))
        paths.menu_plist_path.write_bytes(plistlib.dumps(menu_plist))
        _reload_agent(paths.daemon_plist_path)
        _reload_agent(paths.menu_plist_path)

    return {
        "daemon_plist": str(paths.daemon_plist_path),
        "menu_plist": str(paths.menu_plist_path),
    }


def uninstall_launch_agents(paths: AppPaths, dry_run: bool = False) -> Dict[str, str]:
    """Unloads and removes the daemon and menu bar LaunchAgent plists."""

    removed = {}
    for plist_path in [paths.daemon_plist_path, paths.menu_plist_path]:
        if not dry_run:
            _bootout_agent(plist_path)
            try:
                plist_path.unlink()
            except FileNotFoundError:
                pass
        removed[plist_path.stem] = str(plist_path)
    return removed


def kickstart_agent(label: str) -> None:
    """Restarts a user LaunchAgent immediately."""

    subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def bootout_agent(plist_path: Path) -> None:
    """Stops one LaunchAgent without deleting its plist from disk."""

    _bootout_agent(plist_path)


def _reload_agent(plist_path: Path) -> None:
    """Boots out any existing agent and loads the fresh plist."""

    _bootout_agent(plist_path)
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _bootout_agent(plist_path: Path) -> None:
    """Silently unloads one LaunchAgent if it is currently loaded."""

    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

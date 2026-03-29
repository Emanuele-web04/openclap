"""
FILE: main.py
Purpose: Exposes CLI entrypoints for the daemon, menu bar app, LaunchAgent
installer, diagnostics, and local control commands.
Depends on: the shared runtime modules in this project plus sounddevice/rumps.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import time
from typing import Sequence

from app_paths import AppPaths, MENU_LABEL, SERVICE_LABEL
from config import load_config, set_sensitivity_preset
from control import send_control_command
from daemon_service import ClapDaemonService, list_input_devices, resolve_input_device
from launch_agents import install_launch_agents, uninstall_launch_agents
from menubar import run_menu_bar


def build_parser() -> argparse.ArgumentParser:
    """Builds the subcommand-based CLI for the always-on clap helper."""

    parser = argparse.ArgumentParser(description="Always-on clap helper for macOS.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("daemon", help="Run the background clap detector service.")
    subparsers.add_parser("menubar", help="Run the menu bar controller app.")
    install_parser = subparsers.add_parser("install", help="Install LaunchAgents and start them.")
    install_parser.add_argument("--dry-run", action="store_true", help="Only print what would be installed.")
    uninstall_parser = subparsers.add_parser(
        "uninstall", help="Unload and remove the daemon and menu bar LaunchAgents."
    )
    uninstall_parser.add_argument("--dry-run", action="store_true", help="Only print what would be removed.")
    subparsers.add_parser("list-devices", help="Print available audio input devices.")
    subparsers.add_parser("doctor", help="Run basic environment and service diagnostics.")
    subparsers.add_parser("test-trigger", help="Ask the daemon to run the configured trigger actions.")
    subparsers.add_parser("calibrate", help="Start the guided clap calibration on the running daemon.")
    sensitivity_parser = subparsers.add_parser("set-sensitivity", help="Update the detector sensitivity preset.")
    sensitivity_parser.add_argument("preset", choices=["balanced", "sensitive", "strict"])
    return parser


def cmd_list_devices() -> int:
    """Prints all microphone-capable devices in a stable CLI-friendly format."""

    print("Available input devices:")
    for device in list_input_devices():
        print(f"[{device['index']}] {device['name']}")
    return 0


def cmd_doctor(paths: AppPaths) -> int:
    """Prints a compact health report covering config, binaries, devices, and socket state."""

    config = load_config(paths)
    checks = []
    checks.append(("config", "ok", str(paths.config_path)))
    checks.append(("socket", "ok" if paths.socket_path.exists() else "missing", str(paths.socket_path)))
    checks.append(("codex", "ok", config.actions.codex_url))
    checks.append(("afplay", "ok" if shutil.which("afplay") else "missing", "afplay"))
    checks.append(("launchd", "ok" if shutil.which("launchctl") else "missing", "launchctl"))
    checks.append(("rumps", "ok" if _module_available("rumps") else "missing", "rumps"))
    checks.append(("service plist", "ok" if paths.daemon_plist_path.exists() else "missing", str(paths.daemon_plist_path)))
    checks.append(("menu plist", "ok" if paths.menu_plist_path.exists() else "missing", str(paths.menu_plist_path)))
    checks.append(("sensitivity", "ok", config.service.sensitivity_preset))
    if config.detector.calibration_profile is not None:
        checks.append(("calibration", "ok", f"saved at {config.detector.calibration_profile.calibrated_at:.0f}"))
    else:
        checks.append(("calibration", "unset", "No calibration profile saved"))

    try:
        configured_name, device_info = resolve_input_device(config.service.input_device_name)
        device_name = configured_name or str(device_info["name"])
        checks.append(("input device", "ok", device_name))
    except Exception as exc:  # pragma: no cover - depends on local hardware
        checks.append(("input device", "error", str(exc)))

    if config.actions.local_audio_file:
        audio_path = Path(config.actions.local_audio_file).expanduser()
        checks.append(("audio file", "ok" if audio_path.exists() else "missing", str(audio_path)))
    else:
        checks.append(("audio file", "unset", "No local audio file configured"))

    try:
        response = send_control_command(paths, "status")
        checks.append(("daemon", "ok" if response.get("ok") else "error", "socket reachable"))
    except Exception as exc:
        checks.append(("daemon", "offline", str(exc)))

    for name, status, detail in checks:
        print(f"{name:14} {status:8} {detail}")
    return 0


def cmd_test_trigger(paths: AppPaths) -> int:
    """Requests a manual trigger from the daemon through the private control socket."""

    try:
        response = send_control_command(paths, "test-trigger")
    except FileNotFoundError as exc:
        raise SystemExit("Daemon is not running. Start it with `python main.py daemon` or install the LaunchAgents.") from exc
    except OSError as exc:
        raise SystemExit(f"Unable to reach daemon control socket: {exc}") from exc
    if not response.get("ok"):
        raise SystemExit(response.get("error", "test-trigger failed"))
    print("Trigger dispatched.")
    return 0


def cmd_calibrate(paths: AppPaths) -> int:
    """Starts the guided calibration and prints progress until it completes or times out."""

    try:
        baseline = send_control_command(paths, "status")
        baseline_status = baseline.get("status", {}) if baseline.get("ok") else {}
        previous_calibrated_at = baseline_status.get("last_calibrated_at")
        response = send_control_command(paths, "start-calibration")
    except FileNotFoundError as exc:
        raise SystemExit("Daemon is not running. Start it first, then run `python main.py calibrate`.") from exc
    except OSError as exc:
        raise SystemExit(f"Unable to reach daemon control socket: {exc}") from exc

    if not response.get("ok"):
        raise SystemExit(response.get("error", "Unable to start calibration"))

    print("Calibration started. Stay quiet briefly, then do 2 soft claps, 2 normal claps, and 2 loud claps.")
    last_message = ""
    deadline = time.time() + 24.0
    while time.time() < deadline:
        status_response = send_control_command(paths, "status")
        status = status_response.get("status", {}) if status_response.get("ok") else {}
        calibration_state = str(status.get("calibration_state", "idle"))
        last_calibrated_at = status.get("last_calibrated_at")
        if calibration_state.startswith("failed:"):
            raise SystemExit(calibration_state)
        if calibration_state != last_message:
            print(calibration_state)
            last_message = calibration_state
        if calibration_state == "idle" and last_calibrated_at and last_calibrated_at != previous_calibrated_at:
            print("Calibration complete.")
            return 0
        time.sleep(0.25)

    raise SystemExit("Calibration did not finish in time. You can retry with `python main.py calibrate`.")


def cmd_set_sensitivity(paths: AppPaths, preset: str) -> int:
    """Persists a new sensitivity preset and updates the daemon when reachable."""

    set_sensitivity_preset(paths, preset)
    try:
        response = send_control_command(paths, "set-sensitivity", {"preset": preset})
    except OSError:
        print(f"Sensitivity saved as `{preset}`. Restart or reload the daemon to apply it.")
        return 0
    if not response.get("ok"):
        raise SystemExit(response.get("error", "Unable to set sensitivity"))
    print(f"Sensitivity set to `{preset}`.")
    return 0


def _module_available(module_name: str) -> bool:
    """Checks whether a module can be imported without importing it eagerly."""

    import importlib.util

    return importlib.util.find_spec(module_name) is not None


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatches one CLI subcommand for the clap helper."""

    args = build_parser().parse_args(argv)
    paths = AppPaths.from_home()
    project_root = Path(__file__).resolve().parent

    if args.command == "daemon":
        return ClapDaemonService(paths).run()
    if args.command == "menubar":
        return run_menu_bar(paths)
    if args.command == "install":
        result = install_launch_agents(paths, project_root, dry_run=args.dry_run)
        for name, plist_path in result.items():
            print(f"{name}: {plist_path}")
        return 0
    if args.command == "uninstall":
        result = uninstall_launch_agents(paths, dry_run=args.dry_run)
        for name, plist_path in result.items():
            print(f"{name}: {plist_path}")
        return 0
    if args.command == "list-devices":
        return cmd_list_devices()
    if args.command == "doctor":
        return cmd_doctor(paths)
    if args.command == "test-trigger":
        return cmd_test_trigger(paths)
    if args.command == "calibrate":
        return cmd_calibrate(paths)
    if args.command == "set-sensitivity":
        return cmd_set_sensitivity(paths, args.preset)

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())

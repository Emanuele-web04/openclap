"""
FILE: main.py
Purpose: Exposes CLI entrypoints for the daemon, menu bar app, LaunchAgent
installer, diagnostics, packaging-aware first-launch bootstrap, and local
control commands.
Depends on: the shared runtime modules in this project plus sounddevice/rumps.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Sequence

from app_paths import APP_NAME, APP_VERSION, AppPaths
from config import (
    clear_target_app,
    load_config,
    save_config,
    set_armed_on_launch,
    set_detector_backend,
    set_diagnostics_enabled,
    set_input_device,
    set_launch_at_login,
    set_pector_binary_path,
    set_sensitivity_preset,
    set_target_app,
    set_voice_confirmation_window,
    set_voice_enabled,
    set_voice_engine,
    set_voice_keyword_path,
    set_voice_model_path,
    set_wake_phrase,
)
from control import send_control_command
from daemon_service import ClapDaemonService, list_input_devices, resolve_input_device
from launch_agents import install_launch_agents, uninstall_launch_agents
from menubar import run_menu_bar
from pector_backend import PECTOR_LICENSE, install_pector_checkout
from runtime_env import RuntimeEnvironment
from voice_wake import delete_access_key, install_local_model, store_access_key


def build_parser() -> argparse.ArgumentParser:
    """Builds the subcommand-based CLI for the always-on clap helper."""

    parser = argparse.ArgumentParser(description="Always-on clap helper for macOS.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("daemon", help="Run the background clap detector service.")
    subparsers.add_parser("menubar", help="Run the menu bar controller app.")
    install_parser = subparsers.add_parser("install", help="Install LaunchAgents and start them.")
    install_parser.add_argument("--dry-run", action="store_true", help="Only print what would be installed.")
    install_parser.add_argument(
        "--companion-app",
        help="Optional path to the native OpenClap.app bundle used for login-time UI startup.",
    )
    install_parser.add_argument(
        "--skip-menu-bootstrap",
        action="store_true",
        help="Write the menu LaunchAgent without starting a second UI instance immediately.",
    )
    uninstall_parser = subparsers.add_parser(
        "uninstall", help="Unload and remove the daemon and menu bar LaunchAgents."
    )
    uninstall_parser.add_argument("--dry-run", action="store_true", help="Only print what would be removed.")
    list_devices_parser = subparsers.add_parser("list-devices", help="Print available audio input devices.")
    list_devices_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    subparsers.add_parser("doctor", help="Run basic environment and service diagnostics.")
    subparsers.add_parser("test-trigger", help="Ask the daemon to run the configured trigger actions.")
    config_parser = subparsers.add_parser("config", help="Print the persisted config.")
    config_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    enable_clap_wake_parser = subparsers.add_parser(
        "enable-clap-wake",
        help="Enable the clap-clap plus wake-word flow in one step.",
    )
    enable_clap_wake_parser.add_argument(
        "--phrase",
        default="jarvis",
        help="Wake phrase label to expect after the double clap. Default: jarvis",
    )
    enable_clap_wake_parser.add_argument(
        "--keyword",
        default="",
        help="Optional Porcupine .ppn keyword file. Not needed for the default local engine.",
    )
    enable_clap_wake_parser.add_argument(
        "--window",
        type=float,
        default=5.0,
        help="Seconds to wait for the wake word after the double clap. Default: 5.0",
    )
    enable_clap_wake_parser.add_argument(
        "--engine",
        choices=["local", "porcupine"],
        default="local",
        help="Wake-word backend to use. Default: local",
    )
    install_pector_parser = subparsers.add_parser(
        "install-pector",
        help="Clone/build the optional external pector backend and switch the detector to it.",
    )
    install_pector_parser.add_argument(
        "--keep-backend",
        action="store_true",
        help="Install pector without switching the active backend immediately.",
    )
    subparsers.add_parser("arm", help="Arm the live detector through the daemon socket.")
    subparsers.add_parser("disarm", help="Pause the live detector through the daemon socket.")
    subparsers.add_parser("reload-config", help="Ask the daemon to reload config from disk.")
    status_parser = subparsers.add_parser("status", help="Print the current daemon status.")
    status_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    status_parser.add_argument("--verbose", action="store_true", help="Show recent clap/voice diagnostics.")
    status_parser.add_argument("--watch", action="store_true", help="Refresh status continuously until Ctrl+C.")
    status_parser.add_argument("--interval", type=float, default=1.0, help="Seconds between watch refreshes. Default: 1.0")
    subparsers.add_parser("start-calibration", help="Start calibration without following interactive progress.")
    bootstrap_parser = subparsers.add_parser(
        "bootstrap-native-shell",
        help="Install/start the helper from the native SwiftUI shell when no daemon is reachable yet.",
    )
    bootstrap_parser.add_argument(
        "--companion-app",
        help="Path to the native OpenClap.app bundle so login-time UI startup points at the app shell.",
    )
    subparsers.add_parser("calibrate", help="Start the guided clap calibration on the running daemon.")
    subparsers.add_parser("version", help="Print the bundled app version.")
    target_app_parser = subparsers.add_parser("set-target-app", help="Persist one target .app bundle path.")
    target_app_parser.add_argument("path")
    subparsers.add_parser("clear-target-app", help="Clear the configured target app.")
    detector_backend_parser = subparsers.add_parser("set-detector-backend", help="Choose the active clap backend.")
    detector_backend_parser.add_argument("backend", choices=["native", "pector"])
    pector_binary_parser = subparsers.add_parser("set-pector-binary", help="Persist a custom external pector binary path.")
    pector_binary_parser.add_argument("path")
    input_device_parser = subparsers.add_parser("set-input-device", help="Persist the preferred microphone name.")
    input_device_parser.add_argument("name")
    launch_toggle_parser = subparsers.add_parser("set-launch-at-login", help="Persist auto-start and sync LaunchAgents.")
    launch_toggle_parser.add_argument("value", choices=["true", "false"])
    launch_toggle_parser.add_argument(
        "--companion-app",
        help="Optional path to the native OpenClap.app bundle used when enabling login-time startup.",
    )
    armed_on_launch_parser = subparsers.add_parser("set-armed-on-launch", help="Persist the startup armed state.")
    armed_on_launch_parser.add_argument("value", choices=["true", "false"])
    diagnostics_parser = subparsers.add_parser("set-diagnostics-enabled", help="Persist detection history collection.")
    diagnostics_parser.add_argument("value", choices=["true", "false"])
    sensitivity_parser = subparsers.add_parser("set-sensitivity", help="Update the detector sensitivity preset.")
    sensitivity_parser.add_argument("preset", choices=["balanced", "responsive", "sensitive", "strict"])
    voice_enabled_parser = subparsers.add_parser(
        "set-voice-enabled",
        help="Require a wake word after a valid double clap before actions run.",
    )
    voice_enabled_parser.add_argument("value", choices=["true", "false"])
    wake_phrase_parser = subparsers.add_parser(
        "set-wake-phrase",
        help="Persist the wake phrase label used after the double clap confirmation window opens.",
    )
    wake_phrase_parser.add_argument("phrase")
    wake_keyword_parser = subparsers.add_parser(
        "set-wake-keyword-path",
        help="Persist a Porcupine custom keyword file (.ppn) for non-built-in wake phrases.",
    )
    wake_keyword_parser.add_argument("path")
    subparsers.add_parser("clear-wake-keyword-path", help="Remove the saved custom wake-word keyword file.")
    wake_window_parser = subparsers.add_parser(
        "set-wake-window",
        help="Persist how long the daemon should wait for the wake word after a double clap.",
    )
    wake_window_parser.add_argument("seconds", type=float)
    voice_key_parser = subparsers.add_parser(
        "set-voice-access-key",
        help="Save the Porcupine access key used by wake-word detection.",
    )
    voice_key_parser.add_argument("key")
    subparsers.add_parser("clear-voice-access-key", help="Remove the saved Porcupine access key.")
    subparsers.add_parser(
        "install-local-voice",
        help="Install the offline local speech runtime and download the managed wake-word model.",
    )
    return parser


def cmd_list_devices(as_json: bool = False) -> int:
    """Prints all microphone-capable devices in a stable CLI-friendly format."""

    devices = list_input_devices()
    if as_json:
        print(json.dumps({"devices": devices}, indent=2))
        return 0

    print("Available input devices:")
    for device in devices:
        print(f"[{device['index']}] {device['name']}")
    return 0


def _send_simple_command(paths: AppPaths, command: str) -> dict:
    """Sends one daemon command and converts socket failures into clear CLI exits."""

    try:
        response = send_control_command(paths, command)
    except FileNotFoundError as exc:
        raise SystemExit("Daemon is not running. Start it first or install the LaunchAgents.") from exc
    except OSError as exc:
        raise SystemExit(f"Unable to reach daemon control socket: {exc}") from exc
    if not response.get("ok"):
        raise SystemExit(response.get("error", f"{command} failed"))
    return response


def _render_status(status: dict, *, verbose: bool = False) -> None:
    """Prints one status payload in a compact human-readable layout."""

    print(f"armed:            {status.get('armed')}")
    print(f"backend:          {status.get('detector_backend', 'native')}")
    print(f"signal quality:   {status.get('signal_quality')}")
    print(f"environment:      {status.get('environment_quality')}")
    print(f"sensitivity:      {status.get('sensitivity_preset')}")
    print(f"last trigger:     {status.get('last_trigger_at') or 'never'}")
    print(f"last error:       {status.get('last_error') or 'none'}")
    if verbose:
        voice_debug = status.get("voice_debug", {}) if isinstance(status.get("voice_debug"), dict) else {}
        print(f"voice status:     {status.get('voice_status') or voice_debug.get('status') or 'unknown'}")
        print(f"voice heard:      {status.get('last_voice_heard') or voice_debug.get('last_heard_text') or 'none'}")
        print(f"voice matched:    {status.get('last_voice_match') or voice_debug.get('last_matched_variant') or 'none'}")
        print(f"voice window:     {voice_debug.get('confirmation_remaining_seconds', 0.0):.1f}s remaining")
        print(f"last rejection:   {status.get('last_rejection_reason') or 'none'}")
        history = status.get("recent_detection_history", [])
        if isinstance(history, list) and history:
            print("recent events:")
            for event in history[:8]:
                if not isinstance(event, dict):
                    continue
                print(
                    "  - "
                    f"{event.get('outcome', 'unknown')}"
                    f" | {event.get('source', 'unknown')}"
                    f" | {event.get('reason', 'n/a')}"
                    f" | conf={event.get('confidence', 0.0)}"
                    f" | score={event.get('clap_score', 0.0)}"
                )


def cmd_status(
    paths: AppPaths,
    as_json: bool = False,
    verbose: bool = False,
    watch: bool = False,
    interval: float = 1.0,
) -> int:
    """Prints the current daemon state for either humans or the native app shell."""

    if watch and as_json:
        raise SystemExit("`status --watch` cannot be combined with `--json`.")

    interval = max(0.2, float(interval))
    if not watch:
        response = _send_simple_command(paths, "status")
        status = response.get("status", {})
        if as_json:
            print(json.dumps(status, indent=2, sort_keys=True))
            return 0
        if not isinstance(status, dict):
            raise SystemExit("Daemon returned an invalid status payload")
        _render_status(status, verbose=verbose)
        return 0

    try:
        while True:
            response = _send_simple_command(paths, "status")
            status = response.get("status", {})
            if not isinstance(status, dict):
                raise SystemExit("Daemon returned an invalid status payload")
            print("\033[2J\033[H", end="")
            _render_status(status, verbose=verbose)
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print()
        return 0


def cmd_config(paths: AppPaths, as_json: bool = False) -> int:
    """Prints the persisted config payload used by both helper and native UI."""

    config = load_config(paths)
    if as_json:
        print(json.dumps(config.to_dict(), indent=2, sort_keys=True))
        return 0

    print(f"launch at login:  {config.app.launch_at_login}")
    print(f"armed on launch:  {config.service.armed_on_launch}")
    print(f"diagnostics:      {config.app.diagnostics_enabled}")
    print(f"backend:          {config.detector.backend}")
    print(f"pector binary:    {config.detector.pector_binary_path or 'not configured'}")
    print(f"input device:     {config.service.input_device_name or 'default'}")
    print(f"sensitivity:      {config.service.sensitivity_preset}")
    print(f"voice engine:     {config.voice.engine}")
    print(f"voice confirm:    {config.voice.enabled}")
    print(f"wake phrase:      {config.voice.wake_phrase}")
    print(f"wake keyword:     {config.voice.keyword_path or 'not configured'}")
    print(f"voice model:      {config.voice.model_path or 'managed default'}")
    print(f"wake window:      {config.voice.confirmation_window_seconds:.1f}s")
    print(f"target app:       {config.actions.target_app_name or config.actions.target_app_path or 'not selected'}")
    return 0


def cmd_doctor(paths: AppPaths, runtime: RuntimeEnvironment) -> int:
    """Prints a compact health report covering config, binaries, devices, and socket state."""

    config = load_config(paths)
    checks = []
    runtime_label = "bundle" if runtime.is_bundled_app else "source"
    checks.append(("runtime", "ok", runtime_label))
    checks.append(("version", "ok", APP_VERSION))
    if runtime.is_bundled_app and runtime.bundle_path is not None:
        install_status = "ok" if runtime.is_installed_in_applications() else "warning"
        checks.append(("bundle path", install_status, str(runtime.bundle_path)))
    checks.append(("config", "ok", str(paths.config_path)))
    checks.append(("socket", "ok" if paths.socket_path.exists() else "missing", str(paths.socket_path)))
    checks.append(("afplay", "ok" if shutil.which("afplay") else "missing", "afplay"))
    checks.append(("launchd", "ok" if shutil.which("launchctl") else "missing", "launchctl"))
    pector_path = Path(config.detector.pector_binary_path).expanduser() if config.detector.pector_binary_path else None
    checks.append(("detector backend", "ok", config.detector.backend))
    checks.append(("pector license", "notice", PECTOR_LICENSE))
    checks.append(
        (
            "pector binary",
            "ok" if pector_path and pector_path.exists() else "unset",
            str(pector_path) if pector_path else "Not configured",
        )
    )
    checks.append(("rumps", "ok" if _module_available("rumps") else "missing", "rumps"))
    checks.append(("service plist", "ok" if paths.daemon_plist_path.exists() else "missing", str(paths.daemon_plist_path)))
    checks.append(("menu plist", "ok" if paths.menu_plist_path.exists() else "missing", str(paths.menu_plist_path)))
    checks.append(("auto start", "ok" if config.app.launch_at_login else "disabled", str(config.app.launch_at_login)))
    checks.append(("armed on launch", "ok" if config.service.armed_on_launch else "disabled", str(config.service.armed_on_launch)))
    checks.append(("diagnostics", "ok" if config.app.diagnostics_enabled else "disabled", str(config.app.diagnostics_enabled)))
    checks.append(("sensitivity", "ok", config.service.sensitivity_preset))
    checks.append(("voice engine", "ok", config.voice.engine))
    checks.append(("voice confirm", "ok" if config.voice.enabled else "disabled", str(config.voice.enabled)))
    checks.append(("wake phrase", "ok", config.voice.wake_phrase))
    wake_keyword = Path(config.voice.keyword_path).expanduser() if config.voice.keyword_path else None
    wake_model = Path(config.voice.model_path).expanduser() if config.voice.model_path else None
    checks.append(
        (
            "wake keyword",
            "ok" if wake_keyword and wake_keyword.exists() else "unset",
            str(wake_keyword) if wake_keyword else "Not configured",
        )
    )
    checks.append(
        (
            "voice model",
            "ok" if wake_model and wake_model.exists() else "managed",
            str(wake_model) if wake_model else "Managed default",
        )
    )
    checks.append(("wake window", "ok", f"{config.voice.confirmation_window_seconds:.1f}s"))
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

    if config.actions.target_app_path:
        target_app_path = Path(config.actions.target_app_path).expanduser()
        target_name = config.actions.target_app_name or target_app_path.stem
        checks.append(("target app", "ok" if target_app_path.exists() else "missing", f"{target_name} ({target_app_path})"))
    else:
        checks.append(("target app", "unset", "No target app selected"))

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


def cmd_start_calibration(paths: AppPaths) -> int:
    """Starts calibration and returns immediately for native UI callers."""

    _send_simple_command(paths, "start-calibration")
    print("Calibration started.")
    return 0


def cmd_bootstrap_native_shell(paths: AppPaths, runtime: RuntimeEnvironment, companion_app: str | None) -> int:
    """Installs and starts the helper runtime when the native app launches before launchd is ready."""

    config = load_config(paths)
    companion_bundle = Path(companion_app).expanduser() if companion_app else None

    if config.app.launch_at_login:
        install_launch_agents(
            paths,
            runtime,
            dry_run=False,
            companion_app_bundle_path=companion_bundle,
            skip_menu_bootstrap=True,
        )
        print("LaunchAgents installed and daemon started.")
        return 0

    executable_path = runtime.executable_path
    subprocess.Popen(
        [str(executable_path), "daemon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print("Daemon started.")
    return 0


def _parse_bool_flag(value: str) -> bool:
    """Parses one CLI boolean choice shared by the native shell bridge."""

    return value.strip().lower() == "true"


def cmd_install_pector(paths: AppPaths, keep_backend: bool = False) -> int:
    """Installs the optional external pector checkout and persists its binary path."""

    binary_path = install_pector_checkout(paths)
    set_pector_binary_path(paths, str(binary_path))
    if not keep_backend:
        set_detector_backend(paths, "pector")
    try:
        _send_simple_command(paths, "reload-config")
    except SystemExit:
        pass
    print(f"Installed pector at {binary_path}")
    if not keep_backend:
        print("Detector backend switched to `pector`.")
    return 0


def cmd_install_local_voice(paths: AppPaths) -> int:
    """Installs the offline local speech runtime and managed recognition model."""

    subprocess.run([sys.executable, "-m", "pip", "install", "vosk"], check=True)
    model_path = install_local_model()
    set_voice_engine(paths, "local")
    set_voice_model_path(paths, str(model_path))
    try:
        _send_simple_command(paths, "reload-config")
    except SystemExit:
        pass
    print(f"Installed local voice model at {model_path}")
    return 0


def cmd_enable_clap_wake(paths: AppPaths, phrase: str, keyword: str, window: float, engine: str) -> int:
    """Configures the clap-plus-wake flow in one step and reloads the daemon when reachable."""

    normalized_phrase = phrase.strip() or "jarvis"
    normalized_keyword = keyword.strip()

    if engine == "porcupine" and normalized_phrase.lower() not in {"jarvis"} and not normalized_keyword:
        raise SystemExit(
            "For a custom wake phrase, pass the Porcupine keyword file too: "
            "`python main.py enable-clap-wake --engine porcupine --keyword /absolute/path/to/wake-up.ppn`"
        )

    set_voice_engine(paths, engine)
    set_wake_phrase(paths, normalized_phrase)
    set_voice_keyword_path(paths, normalized_keyword if engine == "porcupine" else "")
    set_voice_confirmation_window(paths, window)
    set_voice_enabled(paths, True)
    try:
        _send_simple_command(paths, "reload-config")
        reload_note = "Daemon updated."
    except SystemExit:
        reload_note = "Config saved. Start the daemon to use it."

    print(reload_note)
    print(f"Say this after clap clap: {normalized_phrase}")
    return 0


def _prompt_move_to_applications(bundle_path: Path) -> None:
    """Explains that the app must live in Applications before background install."""

    message = (
        f"Move {APP_NAME}.app into Applications before enabling the always-on background helper. "
        "After moving it, open the app again and it will install itself in the menu bar."
    )
    applescript = (
        f"display dialog {json.dumps(message)} buttons {{\"OK\"}} "
        f"default button \"OK\" with title {json.dumps(APP_NAME)}"
    )
    subprocess.run(["osascript", "-e", applescript], check=False)
    subprocess.run(["open", "/Applications"], check=False)
    subprocess.run(["open", "-R", str(bundle_path)], check=False)


def _notify_app_ready() -> None:
    """Sends a lightweight macOS notification after bundled first launch finishes."""

    applescript = (
        f"display notification {json.dumps('OpenClap is running in your menu bar.')} "
        f"with title {json.dumps(APP_NAME)}"
    )
    subprocess.run(["osascript", "-e", applescript], check=False)


def _handle_bundle_launch(paths: AppPaths, runtime: RuntimeEnvironment) -> int:
    """Bootstraps the packaged app on first launch without exposing CLI steps."""

    if runtime.bundle_path is None:
        raise SystemExit("Bundled launch requested without a valid .app bundle path.")
    if not runtime.is_installed_in_applications():
        _prompt_move_to_applications(runtime.bundle_path)
        return 0

    install_launch_agents(paths, runtime, dry_run=False)
    _notify_app_ready()
    return 0


def _module_available(module_name: str) -> bool:
    """Checks whether a module can be imported without importing it eagerly."""

    import importlib.util

    return importlib.util.find_spec(module_name) is not None


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatches one CLI subcommand for the clap helper."""

    runtime = RuntimeEnvironment.current(__file__)
    args = build_parser().parse_args(argv)
    paths = AppPaths.from_home()

    # Packaged .app launches have no CLI subcommand; bootstrap launchd and exit.
    if args.command is None:
        if runtime.is_bundled_app:
            return _handle_bundle_launch(paths, runtime)
        build_parser().print_help()
        return 0

    if args.command == "daemon":
        return ClapDaemonService(paths).run()
    if args.command == "menubar":
        return run_menu_bar(paths)
    if args.command == "install":
        companion_app = Path(args.companion_app).expanduser() if getattr(args, "companion_app", None) else None
        result = install_launch_agents(
            paths,
            runtime,
            dry_run=args.dry_run,
            companion_app_bundle_path=companion_app,
            skip_menu_bootstrap=bool(getattr(args, "skip_menu_bootstrap", False)),
        )
        for name, plist_path in result.items():
            print(f"{name}: {plist_path}")
        return 0
    if args.command == "uninstall":
        result = uninstall_launch_agents(paths, dry_run=args.dry_run)
        for name, plist_path in result.items():
            print(f"{name}: {plist_path}")
        return 0
    if args.command == "list-devices":
        return cmd_list_devices(as_json=bool(getattr(args, "json", False)))
    if args.command == "status":
        return cmd_status(
            paths,
            as_json=bool(getattr(args, "json", False)),
            verbose=bool(getattr(args, "verbose", False)),
            watch=bool(getattr(args, "watch", False)),
            interval=float(getattr(args, "interval", 1.0)),
        )
    if args.command == "doctor":
        return cmd_doctor(paths, runtime)
    if args.command == "config":
        return cmd_config(paths, as_json=bool(getattr(args, "json", False)))
    if args.command == "test-trigger":
        return cmd_test_trigger(paths)
    if args.command == "enable-clap-wake":
        return cmd_enable_clap_wake(
            paths,
            phrase=args.phrase,
            keyword=args.keyword,
            window=args.window,
            engine=args.engine,
        )
    if args.command == "install-pector":
        return cmd_install_pector(paths, keep_backend=bool(getattr(args, "keep_backend", False)))
    if args.command == "install-local-voice":
        return cmd_install_local_voice(paths)
    if args.command == "arm":
        _send_simple_command(paths, "arm")
        print("Detector armed.")
        return 0
    if args.command == "disarm":
        _send_simple_command(paths, "disarm")
        print("Detector paused.")
        return 0
    if args.command == "reload-config":
        _send_simple_command(paths, "reload-config")
        print("Config reloaded.")
        return 0
    if args.command == "start-calibration":
        return cmd_start_calibration(paths)
    if args.command == "bootstrap-native-shell":
        return cmd_bootstrap_native_shell(paths, runtime, getattr(args, "companion_app", None))
    if args.command == "set-target-app":
        set_target_app(paths, args.path)
        try:
            _send_simple_command(paths, "reload-config")
        except SystemExit:
            pass
        print("Target app saved.")
        return 0
    if args.command == "clear-target-app":
        clear_target_app(paths)
        try:
            _send_simple_command(paths, "reload-config")
        except SystemExit:
            pass
        print("Target app cleared.")
        return 0
    if args.command == "set-detector-backend":
        set_detector_backend(paths, args.backend)
        try:
            _send_simple_command(paths, "reload-config")
        except SystemExit:
            pass
        print(f"Detector backend saved as `{args.backend}`.")
        return 0
    if args.command == "set-pector-binary":
        set_pector_binary_path(paths, args.path)
        try:
            _send_simple_command(paths, "reload-config")
        except SystemExit:
            pass
        print("pector binary path saved.")
        return 0
    if args.command == "set-input-device":
        set_input_device(paths, args.name)
        try:
            _send_simple_command(paths, "reload-config")
        except SystemExit:
            pass
        print("Input device saved.")
        return 0
    if args.command == "set-launch-at-login":
        launch_at_login = _parse_bool_flag(args.value)
        set_launch_at_login(paths, launch_at_login)
        companion_app = Path(args.companion_app).expanduser() if getattr(args, "companion_app", None) else None
        if launch_at_login:
            result = install_launch_agents(paths, runtime, dry_run=False, companion_app_bundle_path=companion_app)
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            result = uninstall_launch_agents(paths, dry_run=False)
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "set-armed-on-launch":
        set_armed_on_launch(paths, _parse_bool_flag(args.value))
        print("Startup armed state saved.")
        return 0
    if args.command == "set-diagnostics-enabled":
        set_diagnostics_enabled(paths, _parse_bool_flag(args.value))
        try:
            _send_simple_command(paths, "reload-config")
        except SystemExit:
            pass
        print("Diagnostics setting saved.")
        return 0
    if args.command == "set-voice-enabled":
        set_voice_enabled(paths, _parse_bool_flag(args.value))
        try:
            _send_simple_command(paths, "reload-config")
        except SystemExit:
            pass
        print("Voice confirmation setting saved.")
        return 0
    if args.command == "set-wake-phrase":
        set_wake_phrase(paths, args.phrase)
        try:
            _send_simple_command(paths, "reload-config")
        except SystemExit:
            pass
        print(f"Wake phrase saved as `{args.phrase}`.")
        return 0
    if args.command == "set-wake-keyword-path":
        set_voice_keyword_path(paths, args.path)
        try:
            _send_simple_command(paths, "reload-config")
        except SystemExit:
            pass
        print("Wake keyword path saved.")
        return 0
    if args.command == "clear-wake-keyword-path":
        set_voice_keyword_path(paths, "")
        try:
            _send_simple_command(paths, "reload-config")
        except SystemExit:
            pass
        print("Wake keyword path cleared.")
        return 0
    if args.command == "set-wake-window":
        set_voice_confirmation_window(paths, args.seconds)
        try:
            _send_simple_command(paths, "reload-config")
        except SystemExit:
            pass
        print(f"Wake confirmation window saved as `{args.seconds}` seconds.")
        return 0
    if args.command == "set-voice-access-key":
        if not store_access_key(args.key):
            raise SystemExit("Unable to save the Porcupine access key.")
        try:
            _send_simple_command(paths, "reload-config")
        except SystemExit:
            pass
        print("Voice access key saved.")
        return 0
    if args.command == "clear-voice-access-key":
        delete_access_key()
        try:
            _send_simple_command(paths, "reload-config")
        except SystemExit:
            pass
        print("Voice access key cleared.")
        return 0
    if args.command == "calibrate":
        return cmd_calibrate(paths)
    if args.command == "version":
        print(APP_VERSION)
        return 0
    if args.command == "set-sensitivity":
        return cmd_set_sensitivity(paths, args.preset)

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())

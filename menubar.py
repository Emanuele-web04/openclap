"""
FILE: menubar.py
Purpose: Provides a lightweight macOS menu bar controller for the clap daemon.
Depends on: rumps for the UI plus the local control socket and config helpers.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
from typing import Dict

from app_paths import AppPaths, SERVICE_LABEL
from config import clear_target_app, load_config, set_input_device, set_target_app
from control import send_control_command
from daemon_service import list_input_devices
from launch_agents import bootout_agent, kickstart_agent


def _choose_target_app_path() -> str | None:
    """Opens the native macOS app picker and returns one chosen .app bundle path."""

    result = subprocess.run(
        [
            "osascript",
            "-e",
            'POSIX path of (choose application with prompt "Choose the app Clap should open.")',
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        return None
    selected_path = result.stdout.strip()
    return selected_path or None


def run_menu_bar(paths: AppPaths) -> int:
    """Starts the macOS menu bar app and hands control to the Cocoa event loop."""

    try:
        import rumps
    except ImportError as exc:  # pragma: no cover - depends on local GUI deps
        raise SystemExit(
            "Menu bar mode requires `rumps`. Install dependencies with `python -m pip install -r requirements.txt`."
        ) from exc
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    except ImportError:
        NSApplication = None
        NSApplicationActivationPolicyAccessory = None

    # Tell macOS this process is a menu bar accessory so Python.app stays out of the Dock.
    if NSApplication is not None and NSApplicationActivationPolicyAccessory is not None:
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    class ClapMenuApp(rumps.App):
        """Small menu bar surface for arming, testing, and reconfiguring the daemon."""

        def __init__(self) -> None:
            super().__init__("👏")
            self.paths = paths
            self.status_item = rumps.MenuItem("Detector: starting...")
            self.mode_item = rumps.MenuItem("State: starting...")
            self.sensitivity_item = rumps.MenuItem("Sensitivity: starting...")
            self.signal_item = rumps.MenuItem("Signal: starting...")
            self.calibration_item = rumps.MenuItem("Calibration: idle")
            self.calibrated_at_item = rumps.MenuItem("Last calibrated: never")
            self.performance_item = rumps.MenuItem("Performance: starting...")
            self.cpu_item = rumps.MenuItem("CPU: --")
            self.memory_item = rumps.MenuItem("RAM: --")
            self.queue_item = rumps.MenuItem("Queue: --")
            self.overflow_item = rumps.MenuItem("Audio overflows: --")
            self.device_item = rumps.MenuItem("Device: starting...")
            self.target_app_item = rumps.MenuItem("Target App: starting...")
            self.trigger_item = rumps.MenuItem("Last trigger: never")
            self.error_item = rumps.MenuItem("Last error: none")
            self.toggle_item = rumps.MenuItem("Pause Detection")
            self.calibrate_item = rumps.MenuItem("Calibrate Clap")
            self.test_item = rumps.MenuItem("Test Trigger")
            self.restart_item = rumps.MenuItem("Restart Helper")
            self.logs_item = rumps.MenuItem("Open Logs Folder")
            self.config_item = rumps.MenuItem("Open Config Folder")
            self.device_menu = rumps.MenuItem("Choose Device")
            self.sensitivity_menu = rumps.MenuItem("Set Sensitivity")
            self.choose_app_item = rumps.MenuItem("Choose App")
            self.clear_app_item = rumps.MenuItem("Clear Selected App")
            self.stop_helper_item = rumps.MenuItem("Stop Background Helper")
            self.quit_item = rumps.MenuItem("Quit Menu Bar")

            self.toggle_item.set_callback(self.on_toggle_detection)
            self.calibrate_item.set_callback(self.on_start_calibration)
            self.test_item.set_callback(self.on_test_trigger)
            self.restart_item.set_callback(self.on_restart_service)
            self.logs_item.set_callback(self.on_open_logs_folder)
            self.config_item.set_callback(self.on_open_config_folder)
            self.choose_app_item.set_callback(self.on_choose_app)
            self.clear_app_item.set_callback(self.on_clear_app)
            self.stop_helper_item.set_callback(self.on_stop_helper)
            self.quit_item.set_callback(self.on_quit)

            self.menu = [
                self.status_item,
                self.mode_item,
                self.sensitivity_item,
                self.signal_item,
                self.calibration_item,
                self.calibrated_at_item,
                self.performance_item,
                self.cpu_item,
                self.memory_item,
                self.queue_item,
                self.overflow_item,
                self.device_item,
                self.target_app_item,
                self.trigger_item,
                self.error_item,
                None,
                self.toggle_item,
                self.calibrate_item,
                self.test_item,
                self.choose_app_item,
                self.clear_app_item,
                self.sensitivity_menu,
                self.device_menu,
                self.logs_item,
                self.config_item,
                self.restart_item,
                self.stop_helper_item,
                None,
                self.quit_item,
            ]
            self.refresh_sensitivity_menu()
            self.refresh_device_menu()
            self.refresh_status()

        @rumps.timer(2)
        def refresh_timer(self, _sender) -> None:
            self.refresh_status()

        # --- Menu callbacks ----------------------------------------------

        def on_toggle_detection(self, _sender) -> None:
            status = self._status_payload()
            if status and bool(status.get("armed")):
                self._send_command("disarm")
            else:
                self._send_command("arm")

        def on_test_trigger(self, _sender) -> None:
            self._send_command("test-trigger")

        def on_start_calibration(self, _sender) -> None:
            self._send_command("start-calibration")
            rumps.notification(
                "ClapTrigger",
                "Calibration started",
                "Stay quiet briefly, then do 2 soft claps, 2 normal claps, and 2 loud claps.",
            )

        def on_restart_service(self, _sender) -> None:
            kickstart_agent(SERVICE_LABEL)
            self.refresh_status()

        def on_open_config_folder(self, _sender) -> None:
            subprocess.run(["open", str(self.paths.app_support_dir)], check=False)

        def on_open_logs_folder(self, _sender) -> None:
            subprocess.run(["open", str(self.paths.logs_dir)], check=False)

        def on_choose_app(self, _sender) -> None:
            selected_path = _choose_target_app_path()
            if not selected_path:
                return
            set_target_app(self.paths, selected_path)
            self._send_command("reload-config")

        def on_clear_app(self, _sender) -> None:
            clear_target_app(self.paths)
            self._send_command("reload-config")

        def on_stop_helper(self, _sender) -> None:
            bootout_agent(self.paths.daemon_plist_path)
            bootout_agent(self.paths.menu_plist_path)
            rumps.quit_application()

        def on_quit(self, _sender) -> None:
            rumps.quit_application()

        # --- UI refresh helpers -----------------------------------------

        def refresh_status(self) -> None:
            status = self._status_payload()
            if not status:
                self.title = "👏?"
                self.status_item.title = "Detector: offline"
                self.mode_item.title = "State: daemon offline"
                self.sensitivity_item.title = "Sensitivity: unknown"
                self.signal_item.title = "Signal: unavailable"
                self.calibration_item.title = "Calibration: daemon offline"
                self.calibrated_at_item.title = "Last calibrated: unknown"
                self.performance_item.title = "Performance: unknown"
                self.cpu_item.title = "CPU: --"
                self.memory_item.title = "RAM: --"
                self.queue_item.title = "Queue: --"
                self.overflow_item.title = "Audio overflows: --"
                self.device_item.title = "Device: unavailable"
                self.target_app_item.title = "Target App: unavailable"
                self.trigger_item.title = "Last trigger: unknown"
                self.error_item.title = "Last error: daemon offline"
                self.toggle_item.title = "Resume Detection"
                return

            armed = bool(status.get("armed"))
            performance_issue = str(status.get("performance_issue", "unknown"))
            if performance_issue == "error":
                self.title = "👏!"
            elif not armed:
                self.title = "👏·"
            elif performance_issue == "attention":
                self.title = "👏!"
            else:
                self.title = "👏"

            detector_status = str(status.get("detector_status", "unknown"))
            calibration_state = str(status.get("calibration_state", "idle"))
            self.status_item.title = f"Detector: {detector_status}"
            self.mode_item.title = f"State: {'armed' if armed else 'paused'}"
            self.sensitivity_item.title = f"Sensitivity: {status.get('sensitivity_preset', 'balanced')}"
            self.signal_item.title = f"Signal: {status.get('signal_quality', 'unknown')}"
            self.calibration_item.title = f"Calibration: {calibration_state}"
            last_calibrated_at = status.get("last_calibrated_at")
            if last_calibrated_at:
                calibration_stamp = datetime.fromtimestamp(float(last_calibrated_at)).strftime("%H:%M:%S")
                self.calibrated_at_item.title = f"Last calibrated: {calibration_stamp}"
            else:
                self.calibrated_at_item.title = "Last calibrated: never"
            self.performance_item.title = f"Performance: {performance_issue}"
            self.cpu_item.title = f"CPU: {float(status.get('cpu_percent', 0.0)):.1f}%"
            self.memory_item.title = f"RAM: {float(status.get('memory_mb', 0.0)):.1f} MB"
            self.queue_item.title = f"Queue: {int(status.get('queue_depth', 0))}"
            self.overflow_item.title = f"Audio overflows: {int(status.get('overflow_count', 0))}"
            self.device_item.title = f"Device: {status.get('device_name', 'unknown')}"
            self.target_app_item.title = self._target_app_title(status)
            last_trigger_at = status.get("last_trigger_at")
            if last_trigger_at:
                timestamp = datetime.fromtimestamp(float(last_trigger_at)).strftime("%H:%M:%S")
                self.trigger_item.title = f"Last trigger: {timestamp}"
            else:
                self.trigger_item.title = "Last trigger: never"
            last_error = str(status.get("last_error", "") or "none")
            self.error_item.title = f"Last error: {last_error[:80]}"
            self.toggle_item.title = "Pause Detection" if armed else "Resume Detection"
            self.calibrate_item.title = "Calibrate Clap" if calibration_state == "idle" else "Calibration Running..."

        def refresh_sensitivity_menu(self) -> None:
            if getattr(self.sensitivity_menu, "_menu", None) is not None:
                self.sensitivity_menu.clear()
            for preset in ["balanced", "responsive", "sensitive", "strict"]:
                item = rumps.MenuItem(preset.title())
                item.set_callback(self._make_sensitivity_callback(preset))
                self.sensitivity_menu.add(item)

        def refresh_device_menu(self) -> None:
            if getattr(self.device_menu, "_menu", None) is not None:
                self.device_menu.clear()
            config = load_config(self.paths)
            current_device = config.service.input_device_name
            current_default = None
            for device in list_input_devices():
                title = device["name"]
                if title == current_device:
                    title = f"{title} (selected)"
                elif current_device is None and current_default is None:
                    current_default = title
                    title = f"{title} (default)"
                item = rumps.MenuItem(title)
                item.set_callback(self._make_device_callback(str(device["name"])))
                self.device_menu.add(item)

        def _make_device_callback(self, device_name: str):
            """Builds one callback that persists a device choice and reloads the daemon."""

            def callback(_sender) -> None:
                set_input_device(self.paths, device_name)
                self._send_command("reload-config")
                self.refresh_device_menu()
                self.refresh_status()

            return callback

        def _make_sensitivity_callback(self, preset: str):
            """Builds one callback that persists a new sensitivity preset through the daemon."""

            def callback(_sender) -> None:
                try:
                    send_control_command(self.paths, "set-sensitivity", {"preset": preset})
                except Exception:
                    pass
                self.refresh_status()

            return callback

        def _target_app_title(self, status: Dict[str, object]) -> str:
            """Builds the menu bar target-app label from the latest daemon status."""

            actions = status.get("actions", {})
            if not isinstance(actions, dict):
                return "Target App: unknown"

            target_app_path = str(actions.get("target_app_path", "") or "")
            target_app_name = str(actions.get("target_app_name", "") or "")
            if not target_app_path:
                return "Target App: not selected"

            display_name = target_app_name or Path(target_app_path).stem
            if not Path(target_app_path).exists():
                return f"Target App: {display_name} (missing)"
            return f"Target App: {display_name}"

        def _send_command(self, command: str) -> None:
            try:
                send_control_command(self.paths, command)
            except Exception:
                pass
            self.refresh_status()

        def _status_payload(self) -> Dict[str, object] | None:
            try:
                response = send_control_command(self.paths, "status")
            except Exception:
                return None
            if not response.get("ok"):
                return None
            return response.get("status") if isinstance(response.get("status"), dict) else None

    app = ClapMenuApp()
    app.run()
    return 0

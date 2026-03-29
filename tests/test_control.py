"""
FILE: test_control.py
Purpose: Verifies the local Unix socket protocol used between the daemon,
menu bar app, and CLI commands.
Depends on: unittest, tempfile, app_paths.py, control.py
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from app_paths import AppPaths
from control import ControlServer, send_control_command


class ControlServerTests(unittest.TestCase):
    """Integration-style tests for the private control socket."""

    def make_paths(self, temp_dir: str) -> AppPaths:
        """Builds isolated app paths inside a temporary fake home folder."""

        root = Path(temp_dir)
        return AppPaths(
            app_support_dir=root / "app",
            logs_dir=root / "logs",
            socket_path=root / "ctl.sock",
            config_path=root / "app" / "config.json",
            launch_agents_dir=root / "launchd",
            daemon_plist_path=root / "launchd" / "daemon.plist",
            menu_plist_path=root / "launchd" / "menu.plist",
        )

    def test_status_command_round_trip(self) -> None:
        """A client should be able to send and receive one JSON command."""

        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            paths = self.make_paths(temp_dir)
            server = ControlServer(
                paths=paths,
                logger=__import__("logging").getLogger("test-control"),
                handler=lambda request: {"ok": True, "echo": request.get("command")},
            )
            server.start()
            time.sleep(0.1)
            try:
                response = send_control_command(paths, "status")
            finally:
                server.stop()

            self.assertTrue(response["ok"])
            self.assertEqual(response["echo"], "status")


if __name__ == "__main__":
    unittest.main()

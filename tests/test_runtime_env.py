"""
FILE: test_runtime_env.py
Purpose: Verifies bundle detection helpers so the packaged app can safely decide
when to install LaunchAgents and when to ask the user to move it first.
Depends on: unittest and runtime_env.py
"""

from __future__ import annotations

from pathlib import Path
import unittest

from runtime_env import RuntimeEnvironment


class RuntimeEnvironmentTests(unittest.TestCase):
    """Unit tests for bundled-versus-source runtime detection helpers."""

    def test_bundled_app_reports_applications_install(self) -> None:
        """A bundled app inside /Applications should be treated as install-ready."""

        runtime = RuntimeEnvironment(
            project_root=Path("/Applications"),
            executable_path=Path("/Applications/ClapTrigger.app/Contents/MacOS/ClapTrigger"),
            bundle_path=Path("/Applications/ClapTrigger.app"),
            frozen=True,
        )

        self.assertTrue(runtime.is_bundled_app)
        self.assertTrue(runtime.is_installed_in_applications())
        self.assertEqual(runtime.working_directory, Path("/Applications"))

    def test_bundled_app_outside_applications_is_not_install_ready(self) -> None:
        """A copied app in Downloads should not auto-install background helpers."""

        runtime = RuntimeEnvironment(
            project_root=Path("/Users/tester/Downloads"),
            executable_path=Path("/Users/tester/Downloads/ClapTrigger.app/Contents/MacOS/ClapTrigger"),
            bundle_path=Path("/Users/tester/Downloads/ClapTrigger.app"),
            frozen=True,
        )

        self.assertFalse(runtime.is_installed_in_applications())


if __name__ == "__main__":
    unittest.main()

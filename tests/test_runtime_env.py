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
            executable_path=Path("/Applications/OpenClap.app/Contents/MacOS/OpenClap"),
            bundle_path=Path("/Applications/OpenClap.app"),
            frozen=True,
        )

        self.assertTrue(runtime.is_bundled_app)
        self.assertTrue(runtime.is_installed_in_applications())
        self.assertEqual(runtime.working_directory, Path("/Applications"))

    def test_bundled_app_outside_applications_is_not_install_ready(self) -> None:
        """A copied app in Downloads should not auto-install background helpers."""

        runtime = RuntimeEnvironment(
            project_root=Path("/Users/tester/Downloads"),
            executable_path=Path("/Users/tester/Downloads/OpenClap.app/Contents/MacOS/OpenClap"),
            bundle_path=Path("/Users/tester/Downloads/OpenClap.app"),
            frozen=True,
        )

        self.assertFalse(runtime.is_installed_in_applications())

    def test_embedded_frozen_helper_still_counts_as_frozen_launch_target(self) -> None:
        """The helper binary inside Resources should still be launchd-safe even without a .app bundle path."""

        runtime = RuntimeEnvironment(
            project_root=Path("/Applications/OpenClap.app/Contents/Resources/Helper/OpenClapHelper"),
            executable_path=Path("/Applications/OpenClap.app/Contents/Resources/Helper/OpenClapHelper/OpenClapHelper"),
            bundle_path=None,
            frozen=True,
        )

        self.assertFalse(runtime.is_bundled_app)
        self.assertTrue(runtime.launches_from_frozen_binary)
        self.assertEqual(runtime.working_directory, runtime.project_root)


if __name__ == "__main__":
    unittest.main()

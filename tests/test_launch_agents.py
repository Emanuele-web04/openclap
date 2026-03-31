"""
FILE: test_launch_agents.py
Purpose: Verifies LaunchAgent plist generation without touching the live user
LaunchAgents directory.
Depends on: unittest plus source/bundle target builders in launch_agents.py
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from launch_agents import build_launch_agent_plist, resolve_launch_target
from runtime_env import RuntimeEnvironment


class LaunchAgentTests(unittest.TestCase):
    """Unit tests for LaunchAgent plist payloads."""

    def test_build_launch_agent_plist(self) -> None:
        """Generated plists should include the expected launchd keys."""

        payload = build_launch_agent_plist(
            label="com.example.test",
            program_arguments=["/usr/bin/python3", "/tmp/main.py", "daemon"],
            working_directory="/tmp",
            stdout_path="/tmp/stdout.log",
            stderr_path="/tmp/stderr.log",
            keep_alive=True,
        )

        self.assertEqual(payload["Label"], "com.example.test")
        self.assertTrue(payload["RunAtLoad"])
        self.assertTrue(payload["KeepAlive"])
        self.assertEqual(payload["ProgramArguments"][2], "daemon")

    def test_resolve_source_launch_target_prefers_local_virtualenv(self) -> None:
        """Source installs should keep launchd pinned to the project virtualenv."""

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            virtualenv_python = project_root / ".venv" / "bin" / "python"
            virtualenv_python.parent.mkdir(parents=True, exist_ok=True)
            virtualenv_python.write_text("", encoding="utf-8")

            runtime = RuntimeEnvironment(
                project_root=project_root,
                executable_path=Path("/usr/bin/python3"),
                bundle_path=None,
                frozen=False,
            )
            target = resolve_launch_target(runtime)

        self.assertEqual(target.base_program_arguments[0], str(virtualenv_python))
        self.assertEqual(target.base_program_arguments[1], str(project_root / "main.py"))

    def test_resolve_bundled_launch_target_uses_bundle_binary(self) -> None:
        """Bundled installs should point LaunchAgents at the packaged executable."""

        runtime = RuntimeEnvironment(
            project_root=Path("/Applications"),
            executable_path=Path("/Applications/ClapTrigger.app/Contents/MacOS/ClapTrigger"),
            bundle_path=Path("/Applications/ClapTrigger.app"),
            frozen=True,
        )
        target = resolve_launch_target(runtime)

        self.assertEqual(
            target.base_program_arguments,
            ["/Applications/ClapTrigger.app/Contents/MacOS/ClapTrigger"],
        )
        self.assertEqual(target.working_directory, Path("/Applications"))


if __name__ == "__main__":
    unittest.main()

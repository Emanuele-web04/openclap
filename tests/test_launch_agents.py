"""
FILE: test_launch_agents.py
Purpose: Verifies LaunchAgent plist generation without touching the live user
LaunchAgents directory.
Depends on: unittest, plist builders in launch_agents.py
"""

from __future__ import annotations

import unittest

from launch_agents import build_launch_agent_plist


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


if __name__ == "__main__":
    unittest.main()

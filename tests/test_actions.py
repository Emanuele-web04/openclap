"""
FILE: test_actions.py
Purpose: Verifies the async action dispatcher launches the expected commands
without blocking the detector loop.
Depends on: unittest, threading helpers, actions.py, config.py
"""

from __future__ import annotations

import logging
import tempfile
import time
import unittest

from actions import ActionDispatcher
from config import ActionSettings


class ActionDispatcherTests(unittest.TestCase):
    """Unit tests for asynchronous trigger action dispatch."""

    def test_target_app_and_audio_file_are_dispatched(self) -> None:
        """A trigger job should launch the selected app and local playback on the worker thread."""

        captured_commands = []

        def runner(command):
            captured_commands.append(list(command))

        with tempfile.TemporaryDirectory() as temp_dir, tempfile.NamedTemporaryFile(suffix=".mp3") as temp_file:
            target_app = tempfile.mkdtemp(suffix=".app", dir=temp_dir)
            dispatcher = ActionDispatcher(
                logger=logging.getLogger("test-actions"),
                action_settings=ActionSettings(
                    target_app_path=target_app,
                    target_app_name="Test App",
                    local_audio_file=temp_file.name,
                    fallback_media_url="https://example.com/fallback",
                ),
                runner=runner,
            )
            dispatcher.start()
            dispatcher.enqueue_trigger("unit-test")
            time.sleep(0.2)
            dispatcher.stop()

        self.assertEqual(captured_commands[0], ["open", "-a", target_app])
        self.assertEqual(captured_commands[1], ["afplay", temp_file.name])
        self.assertEqual(len(captured_commands), 2)

    def test_missing_audio_file_falls_back_to_url(self) -> None:
        """If the local audio file is missing, the fallback URL should open instead."""

        captured_commands = []

        def runner(command):
            captured_commands.append(list(command))

        with tempfile.TemporaryDirectory() as temp_dir:
            target_app = tempfile.mkdtemp(suffix=".app", dir=temp_dir)
            dispatcher = ActionDispatcher(
                logger=logging.getLogger("test-actions"),
                action_settings=ActionSettings(
                    target_app_path=target_app,
                    target_app_name="Test App",
                    local_audio_file="/tmp/does-not-exist.mp3",
                    fallback_media_url="https://example.com/fallback",
                ),
                runner=runner,
            )
            dispatcher.start()
            dispatcher.enqueue_trigger("unit-test")
            time.sleep(0.2)
            dispatcher.stop()

        self.assertEqual(captured_commands[0], ["open", "-a", target_app])
        self.assertEqual(captured_commands[1], ["open", "https://example.com/fallback"])

    def test_missing_target_app_is_a_safe_no_op(self) -> None:
        """If no target app is selected, the dispatcher should avoid launching anything."""

        captured_commands = []
        reported_statuses = []

        dispatcher = ActionDispatcher(
            logger=logging.getLogger("test-actions"),
            action_settings=ActionSettings(),
            runner=lambda command: captured_commands.append(list(command)),
            status_reporter=reported_statuses.append,
        )
        dispatcher.start()
        dispatcher.enqueue_trigger("unit-test")
        time.sleep(0.2)
        dispatcher.stop()

        self.assertEqual(captured_commands, [])
        self.assertEqual(reported_statuses[-1], "No target app selected. Choose one from the menu bar.")

    def test_deleted_target_app_is_reported_without_crashing(self) -> None:
        """If the saved app path no longer exists, the dispatcher should report it and stop."""

        captured_commands = []
        reported_statuses = []

        dispatcher = ActionDispatcher(
            logger=logging.getLogger("test-actions"),
            action_settings=ActionSettings(
                target_app_path="/tmp/Missing.app",
                target_app_name="Missing",
            ),
            runner=lambda command: captured_commands.append(list(command)),
            status_reporter=reported_statuses.append,
        )
        dispatcher.start()
        dispatcher.enqueue_trigger("unit-test")
        time.sleep(0.2)
        dispatcher.stop()

        self.assertEqual(captured_commands, [])
        self.assertEqual(reported_statuses[-1], "Selected app is missing: /tmp/Missing.app")


if __name__ == "__main__":
    unittest.main()

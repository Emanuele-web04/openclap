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

    def test_codex_and_audio_file_are_dispatched(self) -> None:
        """A trigger job should launch Codex and local playback on the worker thread."""

        captured_commands = []

        def runner(command):
            captured_commands.append(list(command))

        with tempfile.NamedTemporaryFile(suffix=".mp3") as temp_file:
            dispatcher = ActionDispatcher(
                logger=logging.getLogger("test-actions"),
                action_settings=ActionSettings(
                    codex_url="codex://",
                    local_audio_file=temp_file.name,
                    fallback_media_url="https://example.com/fallback",
                ),
                runner=runner,
            )
            dispatcher.start()
            dispatcher.enqueue_trigger("unit-test")
            time.sleep(0.2)
            dispatcher.stop()

        self.assertEqual(captured_commands[0], ["open", "-a", "/Applications/Codex.app"])
        self.assertEqual(
            captured_commands[1],
            [
                "osascript",
                "-e",
                'tell application id "com.openai.codex" to reopen',
                "-e",
                'tell application id "com.openai.codex" to activate',
            ],
        )
        self.assertEqual(captured_commands[2], ["afplay", temp_file.name])
        self.assertEqual(len(captured_commands), 3)

    def test_missing_audio_file_falls_back_to_url(self) -> None:
        """If the local audio file is missing, the fallback URL should open instead."""

        captured_commands = []

        def runner(command):
            captured_commands.append(list(command))

        dispatcher = ActionDispatcher(
            logger=logging.getLogger("test-actions"),
            action_settings=ActionSettings(
                codex_url="codex://",
                local_audio_file="/tmp/does-not-exist.mp3",
                fallback_media_url="https://example.com/fallback",
            ),
            runner=runner,
        )
        dispatcher.start()
        dispatcher.enqueue_trigger("unit-test")
        time.sleep(0.2)
        dispatcher.stop()

        self.assertEqual(captured_commands[0], ["open", "-a", "/Applications/Codex.app"])
        self.assertEqual(
            captured_commands[1],
            [
                "osascript",
                "-e",
                'tell application id "com.openai.codex" to reopen',
                "-e",
                'tell application id "com.openai.codex" to activate',
            ],
        )
        self.assertEqual(captured_commands[2], ["open", "https://example.com/fallback"])


if __name__ == "__main__":
    unittest.main()

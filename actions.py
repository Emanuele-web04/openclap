"""
FILE: actions.py
Purpose: Executes trigger actions off the hot audio loop through a small worker
queue so Codex launch and media playback never block clap detection.
Depends on: config.py for action settings plus Python stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import queue
import subprocess
import threading
from typing import Callable, Sequence

from config import ActionSettings


Runner = Callable[[Sequence[str]], None]
CODEX_APP_PATH = "/Applications/Codex.app"
CODEX_BUNDLE_ID = "com.openai.codex"


@dataclass
class TriggerJob:
    """One action payload emitted by the detector or a manual test trigger."""

    reason: str


class ActionDispatcher:
    """Runs trigger actions on a worker thread to keep audio processing responsive."""

    def __init__(
        self,
        logger: logging.Logger,
        action_settings: ActionSettings,
        runner: Runner | None = None,
    ) -> None:
        self.logger = logger
        self._action_settings = action_settings
        self._runner = runner or self._default_runner
        self._queue: "queue.Queue[TriggerJob | None]" = queue.Queue()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._worker_loop, name="action-dispatcher", daemon=True)
        self._started = False

    def start(self) -> None:
        """Starts the action worker once for the service lifetime."""

        if not self._started:
            self._thread.start()
            self._started = True

    def stop(self) -> None:
        """Stops the worker thread and waits briefly for shutdown."""

        self._queue.put(None)
        if self._started:
            self._thread.join(timeout=2.0)

    def update_settings(self, action_settings: ActionSettings) -> None:
        """Refreshes trigger targets after a config reload."""

        with self._lock:
            self._action_settings = action_settings

    def enqueue_trigger(self, reason: str) -> None:
        """Queues one trigger job without blocking the detector loop."""

        self._queue.put_nowait(TriggerJob(reason=reason))

    def pending_jobs(self) -> int:
        """Returns the approximate number of queued trigger jobs."""

        return self._queue.qsize()

    # --- Worker internals -------------------------------------------------

    def _snapshot_settings(self) -> ActionSettings:
        """Returns a thread-safe copy of the current action settings."""

        with self._lock:
            return ActionSettings(**self._action_settings.__dict__)

    def _worker_loop(self) -> None:
        """Executes queued jobs until the service shuts down."""

        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                self._run_job(job)
            except Exception:  # pragma: no cover - logged and kept alive in production
                self.logger.exception("Action dispatch failed for trigger job")

    def _run_job(self, job: TriggerJob) -> None:
        """Starts the configured trigger targets for one event."""

        settings = self._snapshot_settings()
        self.logger.info("Executing trigger actions for %s", job.reason)

        if settings.codex_url:
            self._bring_codex_to_front(settings.codex_url)

        audio_file = Path(settings.local_audio_file).expanduser() if settings.local_audio_file else None
        if audio_file and audio_file.exists():
            self._runner(["afplay", str(audio_file)])
            return

        if audio_file and settings.local_audio_file:
            self.logger.warning("Configured audio file does not exist: %s", audio_file)

        if settings.fallback_media_url:
            self._runner(["open", settings.fallback_media_url])

    def _bring_codex_to_front(self, codex_url: str) -> None:
        """Launches or reactivates Codex and forces it to the front on macOS."""

        self._runner(["open", "-a", CODEX_APP_PATH])
        if codex_url and codex_url != "codex://":
            self._runner(["open", codex_url])
        self._runner(
            [
                "osascript",
                "-e",
                f'tell application id "{CODEX_BUNDLE_ID}" to reopen',
                "-e",
                f'tell application id "{CODEX_BUNDLE_ID}" to activate',
            ]
        )

    def _default_runner(self, command: Sequence[str]) -> None:
        """Starts one macOS action command without waiting for completion."""

        subprocess.Popen(
            list(command),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

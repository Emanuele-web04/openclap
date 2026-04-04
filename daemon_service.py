"""
FILE: daemon_service.py
Purpose: Runs the always-on clap detector service, exposes local control
commands, and dispatches trigger actions without blocking audio capture.
Depends on: sounddevice, numpy, and the shared runtime/config modules.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, replace
import logging
import os
from pathlib import Path
import resource
import threading
import time
from typing import Dict, List, Optional
import sys

import numpy as np

from actions import ActionDispatcher
from app_paths import AppPaths, ensure_runtime_directories
from calibration import CalibrationSession
from clap_detector import ClapDetector, ClapUpdate
from config import load_config, save_config
from control import ControlServer
from logging_utils import setup_logger
from pector_backend import PectorDetector
from voice_wake import VoiceWakeDetector, VoiceWakeSettings

try:
    import sounddevice as sd
except ImportError as exc:  # pragma: no cover - exercised in production runtime
    sd = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


@dataclass
class RuntimeStatus:
    """Mutable daemon status snapshot served to the menu bar and CLI."""

    armed: bool = True
    device_name: str = "Unavailable"
    detector_status: str = "starting"
    last_trigger_at: float | None = None
    last_error: str = ""
    overflow_count: int = 0
    daemon_pid: int = 0
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    queue_depth: int = 0
    uptime_seconds: float = 0.0
    performance_issue: str = "unknown"
    detector_backend: str = "native"
    sensitivity_preset: str = "balanced"
    signal_quality: str = "unknown"
    environment_quality: str = "unknown"
    calibration_state: str = "idle"
    last_calibrated_at: float | None = None
    last_trigger_source: str = ""
    last_detection_confidence: float = 0.0
    last_rejection_reason: str = ""
    voice_status: str = "disabled"
    last_voice_heard: str = ""
    last_voice_match: str = ""


@dataclass
class DetectionEvent:
    """Compact detection history row exposed to the menu bar and native app UI."""

    timestamp: float
    outcome: str
    reason: str
    confidence: float
    clap_score: float
    signal_quality: str
    environment_quality: str
    source: str
    status: str


def ensure_audio_dependencies() -> None:
    """Stops the daemon early when sounddevice is missing from the environment."""

    if IMPORT_ERROR is not None:
        raise SystemExit(
            "Missing runtime dependency: "
            f"{IMPORT_ERROR}. Install dependencies with `python -m pip install -r requirements.txt`."
        )


def list_input_devices() -> List[Dict[str, object]]:
    """Returns all microphone-capable devices as serializable dictionaries."""

    ensure_audio_dependencies()
    devices = []
    for index, device in enumerate(sd.query_devices()):
        if int(device["max_input_channels"]) <= 0:
            continue
        devices.append({"index": index, "name": str(device["name"])})
    return devices


def resolve_input_device(preferred_name: Optional[str]) -> tuple[Optional[str], Dict[str, object]]:
    """Resolves the configured device name to a sounddevice input device."""

    ensure_audio_dependencies()
    if preferred_name:
        for device in list_input_devices():
            if device["name"] == preferred_name:
                return str(device["name"]), sd.query_devices(device["name"], "input")

    default_input, _default_output = sd.default.device
    device_info = sd.query_devices(default_input, "input")
    return None, device_info


class ClapDaemonService:
    """Owns the microphone, detector, action queue, calibration flow, and control socket."""

    def __init__(self, paths: AppPaths) -> None:
        ensure_audio_dependencies()
        ensure_runtime_directories(paths)
        initial_config = load_config(paths)
        if initial_config.service.armed != initial_config.service.armed_on_launch:
            initial_config.service.armed = initial_config.service.armed_on_launch
            save_config(paths, initial_config)
        self.paths = paths
        self.logger = setup_logger("clapd", paths, debug=initial_config.service.debug_logging)
        self.config = initial_config
        self.status = RuntimeStatus(
            armed=initial_config.service.armed,
            daemon_pid=os.getpid(),
            detector_backend=initial_config.detector.backend,
            sensitivity_preset=initial_config.service.sensitivity_preset,
            last_calibrated_at=(
                initial_config.detector.calibration_profile.calibrated_at
                if initial_config.detector.calibration_profile is not None
                else None
            ),
        )
        self._stop_requested = False
        self._reload_requested = False
        self._state_lock = threading.Lock()
        self._started_at = time.monotonic()
        self._last_perf_wall = self._started_at
        self._last_perf_cpu = self._current_cpu_time()
        self._last_overflow_at = float("-inf")
        self._control_server = ControlServer(paths=paths, logger=self.logger, handler=self.handle_control_command)
        self._action_dispatcher = ActionDispatcher(
            logger=self.logger,
            action_settings=initial_config.actions,
            status_reporter=self._set_action_error,
        )
        self._detector = self._build_detector(initial_config)
        self._voice_detector = self._build_voice_detector(initial_config)
        self._calibration_session: CalibrationSession | None = None
        self._detection_history: deque[DetectionEvent] = deque(maxlen=24)
        self._voice_confirmation_deadline: float | None = None
        self._soft_clap_count = 0
        self._first_soft_clap_at: float | None = None
        self._last_soft_clap_at: float | None = None

    # --- Lifecycle ------------------------------------------------------

    def run(self) -> int:
        """Starts the control server and keeps the audio loop alive until shutdown."""

        self.logger.info("Starting clap daemon service")
        self._action_dispatcher.start()
        self._control_server.start()

        try:
            while not self._stop_requested:
                self._reload_requested = False
                self._reload_runtime_config()

                try:
                    self._run_audio_session()
                except Exception as exc:  # pragma: no cover - hardware/runtime dependent
                    self.status.last_error = str(exc)
                    self.logger.exception("Audio session failed")
                    time.sleep(1.0)
        finally:
            self.logger.info("Stopping clap daemon service")
            self._close_detector_if_needed()
            self._control_server.stop()
            self._action_dispatcher.stop()
        return 0

    def _reload_runtime_config(self) -> None:
        """Loads config from disk and rebuilds the live detector/action runtime."""

        new_config = load_config(self.paths)
        with self._state_lock:
            self._close_detector_if_needed()
            self.config = new_config
            self._action_dispatcher.update_settings(self.config.actions)
            self._detector = self._build_detector(self.config)
            self._voice_detector = self._build_voice_detector(self.config)
            self._voice_confirmation_deadline = None
            self._reset_soft_clap_sequence()
            self.status.armed = self.config.service.armed
            self.status.detector_backend = self.config.detector.backend
            self.status.sensitivity_preset = self.config.service.sensitivity_preset
            self.status.last_calibrated_at = (
                self.config.detector.calibration_profile.calibrated_at
                if self.config.detector.calibration_profile is not None
                else None
            )
            self.status.environment_quality = "unknown"
            self._last_overflow_at = float("-inf")
        self.logger = setup_logger("clapd", self.paths, debug=new_config.service.debug_logging)

    def _build_detector(self, config) -> object:
        """Builds the configured clap backend while keeping the daemon loop backend-agnostic."""

        if config.detector.backend == "pector":
            return PectorDetector(self.paths, config.detector)
        return ClapDetector(
            config.detector,
            sensitivity_preset=config.service.sensitivity_preset,
        )

    def _build_voice_detector(self, config) -> VoiceWakeDetector:
        """Builds the optional wake-word confirmer used after a confirmed double clap."""

        return VoiceWakeDetector(
            VoiceWakeSettings(
                enabled=config.voice.enabled,
                wake_phrase=config.voice.wake_phrase,
                keyword_path=config.voice.keyword_path,
                engine=config.voice.engine,
                sensitivity=config.voice.sensitivity,
                cooldown_seconds=config.voice.cooldown_seconds,
                confirmation_window_seconds=config.voice.confirmation_window_seconds,
            )
        )

    def _close_detector_if_needed(self) -> None:
        """Releases detector resources for backends that own subprocesses or native handles."""

        close = getattr(self._detector, "close", None)
        if callable(close):
            close()
        voice_close = getattr(self, "_voice_detector", None)
        if voice_close is not None:
            voice_close.close()

    def _run_audio_session(self) -> None:
        """Opens one microphone stream and processes it until reload or shutdown."""

        with self._state_lock:
            configured_name, device_info = resolve_input_device(self.config.service.input_device_name)
            detector = self._detector
            calibrating = self._calibration_session is not None and self._calibration_session.is_active()
        device_name = configured_name or str(device_info["name"])
        sample_rate = detector.config.sample_rate
        blocksize = max(1, int(sample_rate * detector.config.block_duration))

        self.status.device_name = device_name
        self.status.last_error = ""
        if calibrating:
            self.status.calibration_state = "Calibration starting..."
        self.logger.info("Listening on input device '%s' at %s Hz", device_name, sample_rate)

        with sd.InputStream(
            device=device_name,
            channels=1,
            samplerate=sample_rate,
            dtype="float32",
            blocksize=blocksize,
        ) as stream:
            while not self._stop_requested and not self._reload_requested:
                chunk, overflowed = stream.read(blocksize)
                now = time.monotonic()
                if overflowed:
                    self.status.overflow_count += 1
                    self._last_overflow_at = now
                    self.logger.warning("Audio input overflow detected")

                with self._state_lock:
                    armed = self.config.service.armed
                    detector = self._detector
                    calibration_session = self._calibration_session
                calibrating = calibration_session is not None and calibration_session.is_active()
                if not armed and not calibrating:
                    self._update_performance_metrics()
                    continue

                update = detector.process_chunk(chunk[:, 0], timestamp=now)
                self._update_status(update)
                if calibrating:
                    self._advance_calibration(update, now)
                    self._update_performance_metrics()
                    continue

                self._expire_voice_confirmation_if_needed(now)
                self._update_performance_metrics()
                if update.triggered:
                    if self._voice_confirmation_required():
                        self.logger.info(
                            "Double clap detected; waiting for wake word '%s'",
                            self.config.voice.wake_phrase,
                        )
                        self._arm_voice_confirmation(now)
                        continue
                    self.logger.info("Double clap detected; dispatching actions")
                    self._dispatch_trigger("double-clap", "double-clap")
                elif self._voice_confirmation_required() and self._consider_soft_clap_voice_arm(update, now):
                    self.logger.info(
                        "Near-miss double clap accepted for voice confirmation; waiting for wake word '%s'",
                        self.config.voice.wake_phrase,
                    )
                    self._arm_voice_confirmation(now)
                    continue

                if self._voice_confirmation_is_active(now):
                    voice_chunk = self._prepare_voice_chunk(chunk[:, 0], sample_rate)
                    if self._voice_detector.process_chunk(voice_chunk, timestamp=now):
                        self.logger.info(
                            "Wake word '%s' detected after double clap; dispatching actions",
                            self.config.voice.wake_phrase,
                        )
                        self._voice_confirmation_deadline = None
                        self.status.detector_status = "triggered"
                        self.status.last_error = ""
                        self._dispatch_trigger("double-clap+voice", "double-clap+voice")
                    elif getattr(self._voice_detector, "status", "") in {
                        "missing-key",
                        "missing-keyword",
                        "missing-dependency",
                        "error",
                    }:
                        self.status.last_error = getattr(self._voice_detector, "last_error", "")
                self._update_voice_status(now)

    # --- Status helpers -------------------------------------------------

    def _update_status(self, update: ClapUpdate) -> None:
        """Publishes the latest detector status for the menu bar and doctor output."""

        self.status.detector_status = update.status
        self.status.signal_quality = self._classify_signal_quality(update)
        self.status.environment_quality = self._classify_environment_quality(update)
        self.status.last_detection_confidence = update.confidence
        self.status.last_rejection_reason = update.rejection_reason
        if update.status == "missing-backend":
            self.status.last_error = "pector backend is selected but no binary is installed"
        elif update.status == "error" and not self.status.last_error:
            self.status.last_error = "detector backend reported an error"
        self._record_detection_event(update)
        if update.status == "cooldown" or update.status == "triggered":
            self._update_performance_metrics(force=True)

    def _update_voice_status(self, timestamp: float) -> None:
        """Mirrors the current voice-confirmation state into shared status fields."""

        snapshot = self._voice_debug_snapshot()
        self.status.voice_status = str(snapshot.get("status", "disabled") or "disabled")
        self.status.last_voice_heard = str(snapshot.get("last_heard_text", "") or "")
        self.status.last_voice_match = str(snapshot.get("last_matched_variant", "") or "")
        if self._voice_confirmation_deadline is not None and timestamp > self._voice_confirmation_deadline:
            self.status.voice_status = "timed-out"

    def _voice_debug_snapshot(self) -> Dict[str, object]:
        """Returns a defensive voice-debug payload even when tests use a tiny stub detector."""

        debug_snapshot = getattr(self._voice_detector, "debug_snapshot", None)
        if callable(debug_snapshot):
            snapshot = debug_snapshot()
            if isinstance(snapshot, dict):
                return snapshot
        return {
            "engine": getattr(getattr(self._voice_detector, "settings", None), "engine", ""),
            "status": getattr(self._voice_detector, "status", ""),
            "last_error": getattr(self._voice_detector, "last_error", ""),
            "last_heard_text": "",
            "last_heard_at": None,
            "last_matched_variant": "",
            "recent_text_window": [],
            "cooldown_seconds": getattr(getattr(self._voice_detector, "settings", None), "cooldown_seconds", 0.0),
        }

    def _prepare_voice_chunk(self, audio_chunk, input_sample_rate: int) -> np.ndarray:
        """Resamples shared mic audio into the 16 kHz stream expected by the wake-word engines."""

        normalized = np.asarray(audio_chunk, dtype=np.float32).reshape(-1)
        if normalized.size == 0 or input_sample_rate == 16_000:
            return normalized
        target_size = max(1, int(round(normalized.size * (16_000.0 / max(float(input_sample_rate), 1.0)))))
        source_positions = np.linspace(0.0, normalized.size - 1, num=normalized.size, dtype=np.float32)
        target_positions = np.linspace(0.0, normalized.size - 1, num=target_size, dtype=np.float32)
        return np.interp(target_positions, source_positions, normalized).astype(np.float32)

    def _classify_signal_quality(self, update: ClapUpdate) -> str:
        """Maps recent audio conditions into a compact quality label."""

        if self._calibration_session is not None and self._calibration_session.is_active():
            return "calibrating"
        if update.peak >= 0.98:
            return "clipping"
        if update.noise_floor >= self._detector.config.min_rms * 0.9:
            return "noisy"
        if update.noise_floor >= self._detector.config.min_rms * 0.55:
            return "fair"
        if self._clock_since_last_overflow() < 4.0:
            return "unstable"
        return "good"

    def _classify_environment_quality(self, update: ClapUpdate) -> str:
        """Maps ambience and transient density into a coarse environment label."""

        if update.transient_density > self._detector.config.max_recent_transient_rate * 1.15:
            return "music-like"
        if self.status.signal_quality in {"clipping", "unstable"}:
            return self.status.signal_quality
        if update.noise_floor >= self._detector.config.min_rms * 1.1:
            return "noisy"
        if update.noise_floor >= self._detector.config.min_rms * 0.65:
            return "busy"
        return "stable"

    def _record_detection_event(self, update: ClapUpdate) -> None:
        """Captures high-signal trigger and rejection events for diagnostics surfaces."""

        if not self.config.app.diagnostics_enabled:
            self._detection_history.clear()
            return

        if update.triggered:
            outcome = "triggered"
            reason = "double-clap"
            source = "double-clap"
        elif update.is_impulse:
            outcome = "partial"
            reason = f"clap {update.clap_count}/{self._detector.config.target_claps}"
            source = "double-clap"
        elif update.rejection_reason:
            outcome = "rejected"
            reason = update.rejection_reason
            source = "double-clap"
        else:
            return

        self._detection_history.appendleft(
            DetectionEvent(
                timestamp=time.time(),
                outcome=outcome,
                reason=reason,
                confidence=round(float(update.confidence), 3),
                clap_score=round(float(update.clap_score), 3),
                signal_quality=self.status.signal_quality,
                environment_quality=self.status.environment_quality,
                source=source,
                status=update.status,
            )
        )

    def _voice_confirmation_required(self) -> bool:
        """Returns whether a spoken confirmation should gate the final trigger."""

        return bool(self.config.voice.enabled)

    def _reset_soft_clap_sequence(self) -> None:
        """Clears the permissive near-miss clap bookkeeping used before the voice gate."""

        self._soft_clap_count = 0
        self._first_soft_clap_at = None
        self._last_soft_clap_at = None

    def _consider_soft_clap_voice_arm(self, update: ClapUpdate, timestamp: float) -> bool:
        """Lets two strong near-miss claps arm the voice gate when hard clap scoring is too strict."""

        if self._voice_confirmation_is_active(timestamp):
            return False
        clap_like_event = update.is_impulse or self._is_soft_clap_candidate(update)
        if not clap_like_event:
            if self._first_soft_clap_at is not None:
                window = max(self._detector.config.clap_window_seconds, 1.4)
                if timestamp - self._first_soft_clap_at > window:
                    self._reset_soft_clap_sequence()
            return False

        min_gap = min(getattr(self._detector.config, "min_clap_gap_seconds", 0.08), 0.08)
        window = max(getattr(self._detector.config, "clap_window_seconds", 1.2), 1.4)
        if self._first_soft_clap_at is None or timestamp - self._first_soft_clap_at > window:
            self._soft_clap_count = 1
            self._first_soft_clap_at = timestamp
            self._last_soft_clap_at = timestamp
            return False

        if self._last_soft_clap_at is not None and timestamp - self._last_soft_clap_at < min_gap:
            return False

        self._soft_clap_count += 1
        self._last_soft_clap_at = timestamp
        if self._soft_clap_count >= 2:
            self._reset_soft_clap_sequence()
            return True
        return False

    def _is_soft_clap_candidate(self, update: ClapUpdate) -> bool:
        """Treats strong near-miss clap events as good enough when a wake word still gates the action."""

        detector_config = getattr(self._detector, "config", None)
        if detector_config is None:
            return False
        if update.triggered:
            return False
        if update.rejection_reason not in {"", "low confidence", "music-like pattern", "timing mismatch"}:
            return False
        if update.confidence < 0.60:
            return False
        if update.clap_score < max(detector_config.min_clap_score * 0.78, 4.4):
            return False
        if update.peak < detector_config.min_peak * 0.82:
            return False
        if update.transient < detector_config.min_transient * 0.82:
            return False
        if self.status.signal_quality in {"clipping"}:
            return False
        return True

    def _voice_confirmation_is_active(self, timestamp: float) -> bool:
        """Returns whether the daemon is currently waiting for the wake word."""

        return self._voice_confirmation_deadline is not None and timestamp <= self._voice_confirmation_deadline

    def _arm_voice_confirmation(self, timestamp: float) -> None:
        """Starts a short wake-word window after a valid double clap."""

        self._voice_confirmation_deadline = timestamp + self.config.voice.confirmation_window_seconds
        self._reset_soft_clap_sequence()
        self._voice_detector.reset_for_listening()
        self.status.detector_status = f"awaiting '{self.config.voice.wake_phrase}'"
        self.status.last_rejection_reason = ""
        self.status.last_error = getattr(self._voice_detector, "last_error", "") or ""
        self._update_voice_status(timestamp)
        if self.config.app.diagnostics_enabled:
            self._detection_history.appendleft(
                DetectionEvent(
                    timestamp=time.time(),
                    outcome="partial",
                    reason=f"awaiting wake word: {self.config.voice.wake_phrase}",
                    confidence=1.0,
                    clap_score=0.0,
                    signal_quality=self.status.signal_quality,
                    environment_quality=self.status.environment_quality,
                    source="voice-confirmation",
                    status="awaiting-voice",
                )
            )

    def _expire_voice_confirmation_if_needed(self, timestamp: float) -> None:
        """Ends stale wake-word windows so accidental double claps do not linger."""

        if self._voice_confirmation_deadline is None or timestamp <= self._voice_confirmation_deadline:
            return
        self._voice_confirmation_deadline = None
        self._reset_soft_clap_sequence()
        self.status.detector_status = "listening"
        self.status.last_rejection_reason = "wake-word timeout"
        self._update_voice_status(timestamp)
        if self.config.app.diagnostics_enabled:
            heard_suffix = f" (heard: {self.status.last_voice_heard})" if self.status.last_voice_heard else ""
            self._detection_history.appendleft(
                DetectionEvent(
                    timestamp=time.time(),
                    outcome="rejected",
                    reason=f"wake-word timeout{heard_suffix}",
                    confidence=0.0,
                    clap_score=0.0,
                    signal_quality=self.status.signal_quality,
                    environment_quality=self.status.environment_quality,
                    source="voice-confirmation",
                    status="timeout",
                )
            )

    def _current_cpu_time(self) -> float:
        """Returns total user+system CPU time consumed by the daemon process."""

        usage = resource.getrusage(resource.RUSAGE_SELF)
        return usage.ru_utime + usage.ru_stime

    def _clock_since_last_overflow(self) -> float:
        """Returns how many monotonic seconds have passed since the last input overflow."""

        return time.monotonic() - self._last_overflow_at

    def _current_memory_mb(self) -> float:
        """Returns resident memory in megabytes, adjusted for macOS units."""

        usage = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == "darwin":
            return usage.ru_maxrss / (1024.0 * 1024.0)
        return usage.ru_maxrss / 1024.0

    def _update_performance_metrics(self, force: bool = False) -> None:
        """Samples lightweight runtime metrics used by doctor and the menu bar."""

        now = time.monotonic()
        if not force and now - self._last_perf_wall < 2.0:
            return

        cpu_time = self._current_cpu_time()
        wall_delta = max(now - self._last_perf_wall, 1e-6)
        cpu_delta = max(cpu_time - self._last_perf_cpu, 0.0)
        cpu_percent = (cpu_delta / wall_delta) * 100.0

        self.status.cpu_percent = cpu_percent
        self.status.memory_mb = self._current_memory_mb()
        self.status.queue_depth = self._action_dispatcher.pending_jobs()
        self.status.uptime_seconds = now - self._started_at
        if self.status.last_error:
            self.status.performance_issue = "error"
        elif self.status.queue_depth > 0 or cpu_percent > 12.0:
            self.status.performance_issue = "attention"
        else:
            self.status.performance_issue = "ok"

        self._last_perf_wall = now
        self._last_perf_cpu = cpu_time

    def _set_action_error(self, message: str | None) -> None:
        """Publishes recoverable action-launch errors for the menu bar and doctor output."""

        with self._state_lock:
            self.status.last_error = message or ""

    def _dispatch_trigger(self, trigger_source: str, reason: str) -> None:
        """Updates shared status fields before enqueueing a trigger action."""

        self.status.last_trigger_at = time.time()
        self.status.last_trigger_source = trigger_source
        if self.config.app.diagnostics_enabled and trigger_source != "double-clap":
            self._detection_history.appendleft(
                DetectionEvent(
                    timestamp=self.status.last_trigger_at,
                    outcome="triggered",
                    reason=reason,
                    confidence=1.0,
                    clap_score=0.0,
                    signal_quality=self.status.signal_quality,
                    environment_quality=self.status.environment_quality,
                    source=trigger_source,
                    status="triggered",
                )
            )
        self._action_dispatcher.enqueue_trigger(reason)

    # --- Calibration ----------------------------------------------------

    def _advance_calibration(self, update: ClapUpdate, timestamp: float) -> None:
        """Feeds detector updates into the guided calibration session and persists the result."""

        session = self._calibration_session
        if session is None:
            return

        session.observe(update, timestamp)
        self.status.calibration_state = session.progress.instruction

        if session.progress.state == "complete" and session.profile is not None:
            self.logger.info("Calibration completed from %s captured claps", session.progress.captured_claps)
            with self._state_lock:
                self.config.detector.calibration_profile = session.profile
                save_config(self.paths, self.config)
                self._close_detector_if_needed()
                self._detector = self._build_detector(self.config)
                self.status.last_calibrated_at = session.profile.calibrated_at
            self.status.calibration_state = "idle"
            self._calibration_session = None
            return

        if session.progress.state == "failed":
            self.logger.warning("Calibration failed: %s", session.progress.result_message)
            with self._state_lock:
                self._close_detector_if_needed()
                self._detector = self._build_detector(self.config)
            self.status.calibration_state = f"failed: {session.progress.result_message}"
            self._calibration_session = None

    def _start_calibration(self) -> Dict[str, object]:
        """Arms a new guided calibration session inside the live daemon."""

        with self._state_lock:
            if self._calibration_session is not None and self._calibration_session.is_active():
                return {"ok": False, "error": "Calibration already in progress"}
            # Calibration must not reuse the normal double-clap cooldown logic, otherwise it misses samples.
            calibration_config = replace(
                self.config.detector,
                backend="native",
                warmup_seconds=0.0,
                target_claps=99,
                clap_window_seconds=30.0,
                cooldown_seconds=0.0,
                min_clap_gap_seconds=max(0.08, self.config.detector.min_clap_gap_seconds * 0.75),
                refractory_seconds=max(0.07, self.config.detector.refractory_seconds * 0.75),
            )
            self._detector = ClapDetector(calibration_config, sensitivity_preset="sensitive")
            self._detector.reset_runtime_state()
            self._calibration_session = CalibrationSession()
            self._calibration_session.start(time.monotonic())
            self.status.calibration_state = self._calibration_session.progress.instruction
        self.logger.info("Calibration started")
        return {"ok": True, "status": self._serialize_status()}

    def _set_sensitivity(self, preset: str) -> Dict[str, object]:
        """Persists a new sensitivity preset and swaps in a detector built with it."""

        if preset not in {"balanced", "responsive", "sensitive", "strict"}:
            return {"ok": False, "error": f"Unknown sensitivity preset: {preset}"}

        with self._state_lock:
            self.config.service.sensitivity_preset = preset
            save_config(self.paths, self.config)
            self._close_detector_if_needed()
            self._detector = self._build_detector(self.config)
            self.status.sensitivity_preset = preset
        self.logger.info("Sensitivity preset changed to %s", preset)
        return {"ok": True, "status": self._serialize_status()}

    # --- Control commands -----------------------------------------------

    def handle_control_command(self, request: Dict[str, object]) -> Dict[str, object]:
        """Handles one JSON control command from the CLI or menu bar app."""

        command = str(request.get("command", ""))
        if command == "status":
            return {"ok": True, "status": self._serialize_status()}
        if command == "arm":
            with self._state_lock:
                self.config.service.armed = True
                save_config(self.paths, self.config)
                self._detector.reset_runtime_state()
                self.status.armed = True
            return {"ok": True, "status": self._serialize_status()}
        if command == "disarm":
            with self._state_lock:
                self.config.service.armed = False
                save_config(self.paths, self.config)
                self._detector.reset_runtime_state()
                self.status.armed = False
            return {"ok": True, "status": self._serialize_status()}
        if command == "reload-config":
            self._reload_requested = True
            return {"ok": True, "status": self._serialize_status()}
        if command == "start-calibration":
            return self._start_calibration()
        if command == "set-sensitivity":
            return self._set_sensitivity(str(request.get("preset", "")))
        if command == "test-trigger":
            self._dispatch_trigger("manual-test", "manual-test")
            return {"ok": True, "status": self._serialize_status()}
        if command == "quit-service":
            self._stop_requested = True
            return {"ok": True}
        return {"ok": False, "error": f"Unknown command: {command}"}

    def _serialize_status(self) -> Dict[str, object]:
        """Converts runtime state into a stable JSON payload for the control socket."""

        self._update_performance_metrics(force=True)
        config = load_config(self.paths)
        calibration_profile = config.detector.calibration_profile
        return {
            **asdict(self.status),
            "running": not self._stop_requested,
            "config_path": str(self.paths.config_path),
            "socket_path": str(self.paths.socket_path),
            "launch_at_login": config.app.launch_at_login,
            "diagnostics_enabled": config.app.diagnostics_enabled,
            "armed_on_launch": config.service.armed_on_launch,
            "detector_backend": config.detector.backend,
            "pector_binary_path": config.detector.pector_binary_path,
            "input_device_name": config.service.input_device_name,
            "sensitivity_preset": config.service.sensitivity_preset,
            "calibration_state": self.status.calibration_state,
            "last_calibrated_at": (
                calibration_profile.calibrated_at if calibration_profile is not None else None
            ),
            "calibration_available": calibration_profile is not None,
            "last_trigger_source": self.status.last_trigger_source,
            "recent_detection_history": [asdict(event) for event in self._detection_history],
            "voice_debug": {
                **self._voice_debug_snapshot(),
                "confirmation_active": self._voice_confirmation_deadline is not None,
                "confirmation_remaining_seconds": (
                    max(0.0, self._voice_confirmation_deadline - time.monotonic())
                    if self._voice_confirmation_deadline is not None
                    else 0.0
                ),
                "configured_phrase": config.voice.wake_phrase,
            },
            "environment_summary": {
                "signal_quality": self.status.signal_quality,
                "environment_quality": self.status.environment_quality,
                "last_detection_confidence": self.status.last_detection_confidence,
                "last_rejection_reason": self.status.last_rejection_reason,
                "overflow_count": self.status.overflow_count,
            },
            "actions": {
                "target_app_path": config.actions.target_app_path,
                "target_app_name": config.actions.target_app_name,
                "local_audio_file": config.actions.local_audio_file,
                "fallback_media_url": config.actions.fallback_media_url,
            },
        }

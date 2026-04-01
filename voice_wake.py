"""
FILE: voice_wake.py
Purpose: Wraps the optional Porcupine wake-word runtime plus macOS Keychain
storage so the daemon can listen for "jarvis" without owning a second mic path.
Depends on: numpy, subprocess, app_paths.py metadata, and pvporcupine when installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import time
from typing import Callable, Protocol

import numpy as np

from app_paths import APP_BUNDLE_ID, APP_NAME

try:
    import pvporcupine
except ImportError:  # pragma: no cover - depends on optional runtime dependency
    pvporcupine = None


KEYCHAIN_SERVICE = APP_BUNDLE_ID
KEYCHAIN_ACCOUNT = "porcupine-access-key"
SUPPORTED_WAKE_PHRASE = "jarvis"
SUPPORTED_ENGINE = "porcupine"
# Voice wake stays out of the main product flow until we have a zero-setup UX.
VOICE_WAKE_EXPERIMENTAL = False


class PorcupineEngine(Protocol):
    """Minimal Porcupine surface used by the daemon and tests."""

    frame_length: int
    sample_rate: int

    def process(self, pcm: list[int]) -> int:
        """Returns a keyword index >= 0 when the wake phrase is detected."""

    def delete(self) -> None:
        """Releases any native resources owned by the engine."""


@dataclass
class VoiceWakeSettings:
    """Serializable runtime knobs for wake-word detection."""

    enabled: bool = False
    wake_phrase: str = SUPPORTED_WAKE_PHRASE
    engine: str = SUPPORTED_ENGINE
    sensitivity: float = 0.5
    cooldown_seconds: float = 2.0


def load_access_key() -> str | None:
    """Reads the Porcupine access key from the user's macOS Keychain."""

    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-w",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            KEYCHAIN_ACCOUNT,
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        return None
    access_key = result.stdout.strip()
    return access_key or None


def store_access_key(access_key: str) -> bool:
    """Creates or replaces the Porcupine access key in the user's Keychain."""

    normalized_key = access_key.strip()
    if not normalized_key:
        return False

    result = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            KEYCHAIN_ACCOUNT,
            "-l",
            f"{APP_NAME} Porcupine Access Key",
            "-w",
            normalized_key,
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.returncode == 0


def delete_access_key() -> bool:
    """Removes the stored access key so the daemon stops using voice wake."""

    result = subprocess.run(
        [
            "security",
            "delete-generic-password",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            KEYCHAIN_ACCOUNT,
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.returncode == 0


class VoiceWakeDetector:
    """Streams shared microphone audio into Porcupine and reports wake events."""

    def __init__(
        self,
        settings: VoiceWakeSettings,
        *,
        access_key_loader: Callable[[], str | None] | None = None,
        engine_factory: Callable[..., PorcupineEngine] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings
        self._feature_enabled = VOICE_WAKE_EXPERIMENTAL and settings.enabled
        self.status = "disabled" if not self._feature_enabled else "starting"
        self.last_error = ""
        self._access_key_loader = access_key_loader or load_access_key
        self._engine_factory = engine_factory or self._default_engine_factory
        self._clock = clock or time.monotonic
        self._engine: PorcupineEngine | None = None
        self._pcm_buffer = np.empty(0, dtype=np.int16)
        self._last_detection_at = float("-inf")

    # --- Lifecycle --------------------------------------------------

    def close(self) -> None:
        """Releases native engine resources and clears any buffered audio."""

        if self._engine is not None:
            self._engine.delete()
        self._engine = None
        self._pcm_buffer = np.empty(0, dtype=np.int16)

    # --- Streaming --------------------------------------------------

    def process_chunk(self, audio_chunk: np.ndarray, timestamp: float | None = None) -> bool:
        """Consumes one float32 audio chunk and returns True on wake detection."""

        if not self._feature_enabled:
            self.status = "disabled"
            self.last_error = ""
            self._pcm_buffer = np.empty(0, dtype=np.int16)
            return False

        if self.settings.engine != SUPPORTED_ENGINE:
            self.status = "error"
            self.last_error = f"Unsupported voice engine: {self.settings.engine}"
            return False

        if self.settings.wake_phrase.strip().lower() != SUPPORTED_WAKE_PHRASE:
            self.status = "error"
            self.last_error = f"Unsupported wake phrase: {self.settings.wake_phrase}"
            return False

        if not self._ensure_engine():
            return False

        timestamp = timestamp if timestamp is not None else self._clock()
        pcm_chunk = self._float_chunk_to_pcm(audio_chunk)
        if pcm_chunk.size == 0:
            return False

        self._pcm_buffer = np.concatenate((self._pcm_buffer, pcm_chunk))
        triggered = False
        frame_length = self.frame_length

        while self._pcm_buffer.size >= frame_length:
            frame = self._pcm_buffer[:frame_length]
            self._pcm_buffer = self._pcm_buffer[frame_length:]
            try:
                keyword_index = self._engine.process(frame.tolist()) if self._engine is not None else -1
            except Exception as exc:  # pragma: no cover - native runtime failures depend on host env
                self.status = "error"
                self.last_error = str(exc)
                self.close()
                return False

            if keyword_index >= 0 and timestamp - self._last_detection_at >= self.settings.cooldown_seconds:
                self._last_detection_at = timestamp
                triggered = True

        return triggered

    @property
    def frame_length(self) -> int:
        """Returns the engine's required PCM frame length when available."""

        return int(self._engine.frame_length) if self._engine is not None else 0

    # --- Internal helpers ------------------------------------------

    def _ensure_engine(self) -> bool:
        """Creates the Porcupine engine lazily once voice wake is actually enabled."""

        if self._engine is not None:
            self.status = "ready"
            return True

        access_key = self._access_key_loader()
        if not access_key:
            self.status = "missing-key"
            self.last_error = "Wake-word access key is not configured"
            return False

        try:
            self._engine = self._engine_factory(
                access_key=access_key,
                keywords=[SUPPORTED_WAKE_PHRASE],
                sensitivities=[self.settings.sensitivity],
            )
        except RuntimeError as exc:
            self.status = "missing-dependency"
            self.last_error = str(exc)
            return False
        except Exception as exc:  # pragma: no cover - native failures depend on host env
            self.status = "error"
            self.last_error = str(exc)
            return False

        if int(self._engine.sample_rate) != 16_000:
            self.status = "error"
            self.last_error = f"Unsupported Porcupine sample rate: {self._engine.sample_rate}"
            self.close()
            return False

        self.status = "ready"
        self.last_error = ""
        return True

    def _default_engine_factory(self, **kwargs) -> PorcupineEngine:
        """Builds the default Porcupine engine when the dependency is installed."""

        if pvporcupine is None:
            raise RuntimeError("Missing runtime dependency: pvporcupine")
        return pvporcupine.create(**kwargs)

    def _float_chunk_to_pcm(self, audio_chunk: np.ndarray) -> np.ndarray:
        """Converts the shared float32 input stream into Porcupine's int16 PCM format."""

        normalized = np.asarray(audio_chunk, dtype=np.float32).reshape(-1)
        if normalized.size == 0:
            return np.empty(0, dtype=np.int16)
        clipped = np.clip(normalized, -1.0, 1.0)
        return np.rint(clipped * 32767.0).astype(np.int16)

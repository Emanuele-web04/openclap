"""
FILE: voice_wake.py
Purpose: Provides a fully local wake-word detector on the shared audio stream
using an offline Vosk model, plus an optional Porcupine fallback.
Depends on: numpy, subprocess, urllib/zipfile, and optional vosk/pvporcupine runtimes.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Callable, Protocol
from urllib.request import urlopen
import zipfile

import numpy as np

from app_paths import APP_BUNDLE_ID, APP_NAME

try:
    import pvporcupine
except ImportError:  # pragma: no cover - depends on optional runtime dependency
    pvporcupine = None

try:
    import vosk
except ImportError:  # pragma: no cover - depends on optional runtime dependency
    vosk = None


KEYCHAIN_SERVICE = APP_BUNDLE_ID
KEYCHAIN_ACCOUNT = "porcupine-access-key"
LOCAL_ENGINE = "local"
PORCUPINE_ENGINE = "porcupine"
BUILTIN_WAKE_PHRASES = {"jarvis"}
LOCAL_MODEL_NAME = "vosk-model-small-en-us-0.15"
LOCAL_MODEL_URL = f"https://alphacephei.com/vosk/models/{LOCAL_MODEL_NAME}.zip"
# Voice wake is now an opt-in runtime path controlled by config.
VOICE_WAKE_EXPERIMENTAL = True


class PorcupineEngine(Protocol):
    """Minimal Porcupine surface used by the daemon and tests."""

    frame_length: int
    sample_rate: int

    def process(self, pcm: list[int]) -> int:
        """Returns a keyword index >= 0 when the wake phrase is detected."""

    def delete(self) -> None:
        """Releases any native resources owned by the engine."""


class LocalRecognizer(Protocol):
    """Small surface used by the local Vosk engine and unit tests."""

    def AcceptWaveform(self, pcm_bytes: bytes) -> bool:
        """Feeds PCM bytes and returns True when a final result is available."""

    def PartialResult(self) -> str:
        """Returns one JSON payload containing the current partial transcript."""

    def Result(self) -> str:
        """Returns one JSON payload containing the final transcript."""

    def Reset(self) -> None:
        """Clears local decoder state for a new wake-word window."""


@dataclass
class VoiceWakeSettings:
    """Serializable runtime knobs for wake-word detection."""

    enabled: bool = False
    wake_phrase: str = "jarvis"
    keyword_path: str = ""
    model_path: str = ""
    engine: str = LOCAL_ENGINE
    sensitivity: float = 0.5
    cooldown_seconds: float = 2.0
    confirmation_window_seconds: float = 5.0


def managed_local_model_dir() -> Path:
    """Returns the managed app-support location for the offline speech model."""

    return Path.home() / "Library" / "Application Support" / APP_NAME / "models"


def default_local_model_path() -> Path:
    """Returns the default managed model path used by the local engine."""

    return managed_local_model_dir() / LOCAL_MODEL_NAME


def install_local_model(model_url: str = LOCAL_MODEL_URL) -> Path:
    """Downloads and extracts the small offline Vosk model into app support."""

    target_dir = managed_local_model_dir()
    model_path = default_local_model_path()
    if model_path.exists():
        return model_path

    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openclap-vosk-") as temp_dir:
        temp_root = Path(temp_dir)
        zip_path = temp_root / "model.zip"
        with urlopen(model_url) as response, zip_path.open("wb") as output:  # nosec B310
            shutil.copyfileobj(response, output)

        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(temp_root)

        extracted_root = temp_root / LOCAL_MODEL_NAME
        if not extracted_root.exists():
            directories = [item for item in temp_root.iterdir() if item.is_dir()]
            if len(directories) != 1:
                raise RuntimeError("Downloaded local speech model archive had an unexpected layout.")
            extracted_root = directories[0]

        if model_path.exists():
            shutil.rmtree(model_path)
        shutil.move(str(extracted_root), str(model_path))
    return model_path


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
    """Consumes shared audio chunks and reports wake detections from local or Porcupine engines."""

    def __init__(
        self,
        settings: VoiceWakeSettings,
        *,
        access_key_loader: Callable[[], str | None] | None = None,
        engine_factory: Callable[..., PorcupineEngine] | None = None,
        local_model_loader: Callable[[str], object] | None = None,
        local_recognizer_factory: Callable[[object, int, str], LocalRecognizer] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings
        self._feature_enabled = VOICE_WAKE_EXPERIMENTAL and settings.enabled
        self.status = "disabled" if not self._feature_enabled else "starting"
        self.last_error = ""
        self._access_key_loader = access_key_loader or load_access_key
        self._engine_factory = engine_factory or self._default_engine_factory
        self._local_model_loader = local_model_loader or self._default_local_model_loader
        self._local_recognizer_factory = local_recognizer_factory or self._default_local_recognizer_factory
        self._uses_default_local_model_loader = local_model_loader is None
        self._uses_default_local_recognizer_factory = local_recognizer_factory is None
        self._clock = clock or time.monotonic
        self._engine: PorcupineEngine | None = None
        self._local_model: object | None = None
        self._local_recognizer: LocalRecognizer | None = None
        self._pcm_buffer = np.empty(0, dtype=np.int16)
        self._last_detection_at = float("-inf")
        self._recent_local_texts: list[str] = []
        self._last_heard_text = ""
        self._last_matched_variant = ""
        self._last_heard_at: float | None = None

    # --- Lifecycle --------------------------------------------------

    def close(self) -> None:
        """Releases any native runtime resources and clears buffered state."""

        if self._engine is not None:
            self._engine.delete()
        self._engine = None
        self._local_model = None
        self._local_recognizer = None
        self._pcm_buffer = np.empty(0, dtype=np.int16)
        self._recent_local_texts = []
        self._last_heard_text = ""
        self._last_matched_variant = ""
        self._last_heard_at = None

    def reset_for_listening(self) -> None:
        """Drops buffered audio and resets the local decoder for a new wake window."""

        self._pcm_buffer = np.empty(0, dtype=np.int16)
        if self._local_recognizer is not None:
            self._local_recognizer.Reset()
        self._recent_local_texts = []
        self._last_heard_text = ""
        self._last_matched_variant = ""
        self._last_heard_at = None

    def debug_snapshot(self) -> dict[str, object]:
        """Returns one small serializable snapshot used by CLI/UI diagnostics."""

        return {
            "engine": self.settings.engine,
            "status": self.status,
            "last_error": self.last_error,
            "last_heard_text": self._last_heard_text,
            "last_heard_at": self._last_heard_at,
            "last_matched_variant": self._last_matched_variant,
            "recent_text_window": list(self._recent_local_texts),
            "cooldown_seconds": self.settings.cooldown_seconds,
        }

    # --- Streaming --------------------------------------------------

    def process_chunk(self, audio_chunk: np.ndarray, timestamp: float | None = None) -> bool:
        """Consumes one audio chunk and returns True on wake detection."""

        if not self._feature_enabled:
            self.status = "disabled"
            self.last_error = ""
            self._pcm_buffer = np.empty(0, dtype=np.int16)
            return False

        timestamp = timestamp if timestamp is not None else self._clock()
        if self.settings.engine == LOCAL_ENGINE:
            return self._process_local_detection(audio_chunk, timestamp)
        if self.settings.engine == PORCUPINE_ENGINE:
            return self._process_porcupine_detection(audio_chunk, timestamp)

        self.status = "error"
        self.last_error = f"Unsupported voice engine: {self.settings.engine}"
        return False

    @property
    def frame_length(self) -> int:
        """Returns the Porcupine frame length when that backend is active."""

        return int(self._engine.frame_length) if self._engine is not None else 0

    # --- Local offline path ---------------------------------------

    def _process_local_detection(self, audio_chunk: np.ndarray, timestamp: float) -> bool:
        """Runs one chunk through the local Vosk recognizer and matches the configured phrase."""

        if not self._ensure_local_recognizer():
            return False
        recognizer = self._local_recognizer
        if recognizer is None:
            return False

        pcm_chunk = self._float_chunk_to_pcm(audio_chunk)
        if pcm_chunk.size == 0:
            return False

        detected_text = ""
        if recognizer.AcceptWaveform(pcm_chunk.tobytes()):
            detected_text = self._extract_text(recognizer.Result())
        else:
            detected_text = self._extract_text(recognizer.PartialResult())

        if not detected_text:
            self.status = "ready"
            self.last_error = ""
            return False
        self._last_heard_text = detected_text
        self._last_heard_at = timestamp
        self._remember_local_text(detected_text)
        matched_variant = self._matches_wake_phrase(detected_text)
        if not matched_variant:
            self._last_matched_variant = ""
            self.status = "ready"
            self.last_error = ""
            return False
        self._last_matched_variant = matched_variant
        if timestamp - self._last_detection_at < self.settings.cooldown_seconds:
            return False

        self._last_detection_at = timestamp
        self.status = "ready"
        self.last_error = ""
        return True

    def _ensure_local_recognizer(self) -> bool:
        """Loads the offline model and recognizer lazily once local voice wake is used."""

        if self._local_recognizer is not None:
            self.status = "ready"
            self.last_error = ""
            return True

        model_path = Path(self.settings.model_path).expanduser() if self.settings.model_path else default_local_model_path()
        if not model_path.exists():
            self.status = "missing-model"
            self.last_error = f"Local speech model is missing: {model_path}"
            return False
        if vosk is None and (self._uses_default_local_model_loader or self._uses_default_local_recognizer_factory):
            self.status = "missing-dependency"
            self.last_error = "Missing local speech runtime: vosk"
            return False

        try:
            self._local_model = self._local_model_loader(str(model_path))
            self._local_recognizer = self._local_recognizer_factory(
                self._local_model,
                16_000,
                self.settings.wake_phrase,
            )
        except RuntimeError as exc:
            self.status = "missing-dependency"
            self.last_error = str(exc)
            return False
        except Exception as exc:  # pragma: no cover - native/runtime failures depend on host env
            self.status = "error"
            self.last_error = str(exc)
            return False

        self.status = "ready"
        self.last_error = ""
        return True

    def _default_local_model_loader(self, model_path: str) -> object:
        """Loads one offline Vosk model from disk."""

        if vosk is None:
            raise RuntimeError("Missing local speech runtime: vosk")
        vosk.SetLogLevel(-1)
        return vosk.Model(model_path)

    def _default_local_recognizer_factory(self, model: object, sample_rate: int, phrase: str) -> LocalRecognizer:
        """Builds one grammar-constrained local recognizer around the configured phrase."""

        if vosk is None:
            raise RuntimeError("Missing local speech runtime: vosk")
        grammar = json.dumps([*self._wake_phrase_variants(phrase), "[unk]"])
        return vosk.KaldiRecognizer(model, sample_rate, grammar)

    def _extract_text(self, payload: str) -> str:
        """Parses Vosk JSON payloads and normalizes any recognized transcript."""

        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return ""
        return str(parsed.get("text") or parsed.get("partial") or "").strip().lower()

    def _matches_wake_phrase(self, detected_text: str) -> str | None:
        """Accepts close phrase variants and short split transcripts for the configured wake phrase."""

        normalized_detected = self._normalize_text(detected_text)
        compressed_detected = normalized_detected.replace(" ", "")
        recent_detected = " ".join(self._recent_local_texts[-3:])
        normalized_recent = self._normalize_text(recent_detected)
        compressed_recent = normalized_recent.replace(" ", "")

        for variant in self._wake_phrase_variants(self.settings.wake_phrase):
            normalized_variant = self._normalize_text(variant)
            compressed_variant = normalized_variant.replace(" ", "")
            if not normalized_variant:
                continue
            if normalized_variant in normalized_detected or normalized_variant in normalized_recent:
                return normalized_variant
            if compressed_variant and (
                compressed_variant in compressed_detected or compressed_variant in compressed_recent
            ):
                return normalized_variant
            if " " not in normalized_variant and self._contains_close_single_word_match(
                normalized_variant,
                normalized_detected,
                normalized_recent,
            ):
                return normalized_variant
        return None

    def _remember_local_text(self, detected_text: str) -> None:
        """Keeps a tiny rolling transcript window so split partials still count as one phrase."""

        normalized = self._normalize_text(detected_text)
        if not normalized:
            return
        if not self._recent_local_texts or self._recent_local_texts[-1] != normalized:
            self._recent_local_texts.append(normalized)
            self._recent_local_texts = self._recent_local_texts[-3:]

    def _wake_phrase_variants(self, phrase: str) -> list[str]:
        """Builds a few robust aliases for the configured phrase without going too broad."""

        normalized = self._normalize_text(phrase)
        if not normalized:
            return []

        variants = {normalized, normalized.replace(" ", "")}
        if normalized == "wake up":
            variants.update({"wake-up", "wakeup", "wake app", "wakeapp"})
        elif normalized == "jarvis":
            variants.update({"jar vis", "jervis", "jarviss", "jarvish", "jarves"})
        return [variant for variant in variants if variant]

    def _normalize_text(self, text: str) -> str:
        """Normalizes transcript text into one lowercase space-separated phrase."""

        lowered = text.strip().lower()
        collapsed = re.sub(r"[^a-z0-9]+", " ", lowered)
        return " ".join(collapsed.split())

    def _contains_close_single_word_match(self, phrase: str, *texts: str) -> bool:
        """Allows one near-miss token for short offline-ASR slips like 'jervis'."""

        if not phrase or " " in phrase:
            return False
        for text in texts:
            for token in text.split():
                if self._is_close_single_word_match(phrase, token):
                    return True
        return False

    def _is_close_single_word_match(self, phrase: str, token: str) -> bool:
        """Accepts one-edit variations while keeping unrelated words out."""

        if not token or token == phrase:
            return False
        if len(phrase) < 5 or abs(len(token) - len(phrase)) > 1:
            return False
        if token[0] != phrase[0]:
            return False
        return self._edit_distance_at_most_one(phrase, token)

    def _edit_distance_at_most_one(self, left: str, right: str) -> bool:
        """Cheap bounded edit-distance check used for single-token wake words."""

        if left == right:
            return True
        if abs(len(left) - len(right)) > 1:
            return False

        if len(left) > len(right):
            left, right = right, left

        index_left = 0
        index_right = 0
        mismatches = 0
        while index_left < len(left) and index_right < len(right):
            if left[index_left] == right[index_right]:
                index_left += 1
                index_right += 1
                continue
            mismatches += 1
            if mismatches > 1:
                return False
            if len(left) == len(right):
                index_left += 1
                index_right += 1
            else:
                index_right += 1

        if index_left < len(left) or index_right < len(right):
            mismatches += 1
        return mismatches <= 1

    # --- Porcupine fallback ---------------------------------------

    def _process_porcupine_detection(self, audio_chunk: np.ndarray, timestamp: float) -> bool:
        """Consumes float32 chunks through Porcupine for users who opt into that backend."""

        if not self._ensure_porcupine_engine():
            return False

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

    def _ensure_porcupine_engine(self) -> bool:
        """Creates the Porcupine engine lazily once that fallback backend is requested."""

        if self._engine is not None:
            self.status = "ready"
            return True

        access_key = self._access_key_loader()
        if not access_key:
            self.status = "missing-key"
            self.last_error = "Wake-word access key is not configured"
            return False

        wake_phrase = self.settings.wake_phrase.strip().lower()
        engine_kwargs: dict[str, object]
        if self.settings.keyword_path:
            keyword_path = Path(self.settings.keyword_path).expanduser()
            if not keyword_path.exists():
                self.status = "missing-keyword"
                self.last_error = f"Wake-word keyword file is missing: {keyword_path}"
                return False
            engine_kwargs = {"keyword_paths": [str(keyword_path)]}
        elif wake_phrase in BUILTIN_WAKE_PHRASES:
            engine_kwargs = {"keywords": [wake_phrase]}
        else:
            self.status = "missing-keyword"
            self.last_error = (
                f"Wake phrase '{self.settings.wake_phrase}' needs a Porcupine keyword file"
            )
            return False

        try:
            self._engine = self._engine_factory(
                access_key=access_key,
                sensitivities=[self.settings.sensitivity],
                **engine_kwargs,
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

    # --- Shared helpers -------------------------------------------

    def _float_chunk_to_pcm(self, audio_chunk: np.ndarray) -> np.ndarray:
        """Converts the shared float32 input stream into int16 PCM."""

        normalized = np.asarray(audio_chunk, dtype=np.float32).reshape(-1)
        if normalized.size == 0:
            return np.empty(0, dtype=np.int16)
        clipped = np.clip(normalized, -1.0, 1.0)
        return np.rint(clipped * 32767.0).astype(np.int16)

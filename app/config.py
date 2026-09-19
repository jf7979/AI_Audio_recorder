"""Loads and validates config.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLOUD_SYNC_MARKERS = (
    "com~apple~cloudDocs".lower(),
    "icloud",
    "onedrive",
    "dropbox",
    "google drive",
    "googledrive",
)


@dataclass
class AudioConfig:
    device: str | None
    sample_rate: int


@dataclass
class VadConfig:
    silence_ms_to_close: int
    min_segment_ms: int
    max_segment_seconds: int
    session_gap_minutes: int
    speech_pad_ms: int
    threshold: float


@dataclass
class TranscriptionConfig:
    model_size: str
    compute_type: str
    model_dir: str | None


@dataclass
class KeywordsConfig:
    triggers: list[str]


@dataclass
class LlmBackend:
    base_url: str
    model: str


@dataclass
class SummarizationConfig:
    active_backend: str
    backends: dict[str, LlmBackend]

    def resolve(self, name: str | None = None) -> LlmBackend:
        key = name or self.active_backend
        try:
            return self.backends[key]
        except KeyError:
            raise ValueError(
                f"Unknown summarization backend '{key}'. "
                f"Known backends: {', '.join(sorted(self.backends))}"
            ) from None


@dataclass
class StorageConfig:
    data_dir: Path

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db.sqlite3"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


@dataclass
class WebConfig:
    host: str
    port: int
    password_hash: str
    secret_key: str


@dataclass
class Config:
    audio: AudioConfig
    vad: VadConfig
    transcription: TranscriptionConfig
    keywords: KeywordsConfig
    summarization: SummarizationConfig
    storage: StorageConfig
    web: WebConfig
    path: Path = field(repr=False, default=Path("config.yaml"))


def default_config_path() -> Path:
    env_path = os.environ.get("AUDIOLOG_CONFIG")
    if env_path:
        return Path(env_path)
    return PROJECT_ROOT / "config.yaml"


def web_auth_path(config_path: Path) -> Path:
    return config_path.parent / "web_auth.yaml"


def _load_web_auth(config_path: Path) -> dict:
    auth_path = web_auth_path(config_path)
    if not auth_path.exists():
        return {}
    with open(auth_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _warn_if_cloud_synced(data_dir: Path) -> None:
    lowered = str(data_dir.resolve()).lower()
    for marker in CLOUD_SYNC_MARKERS:
        if marker in lowered:
            raise ValueError(
                f"storage.data_dir ({data_dir}) looks like it's inside a cloud-synced "
                "folder (iCloud/OneDrive/Dropbox/Google Drive). This will corrupt the "
                "SQLite database - point data_dir at a plain local disk path instead."
            )


def load_config(path: Path | str | None = None) -> Config:
    config_path = Path(path) if path else default_config_path()
    if not config_path.exists():
        example = config_path.parent / "config.example.yaml"
        raise FileNotFoundError(
            f"No config file at {config_path}. Copy {example.name} to "
            f"{config_path.name} and edit it for your machine."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    data_dir = Path(raw["storage"]["data_dir"]).expanduser()
    if not data_dir.is_absolute():
        data_dir = (config_path.parent / data_dir).resolve()
    _warn_if_cloud_synced(data_dir)

    sample_rate = int(raw["audio"]["sample_rate"])
    if sample_rate != 16000:
        # capture.py's block size (512 samples) and Silero VAD's streaming
        # windows are only valid at 16kHz; faster-whisper also expects 16kHz
        # input. Nothing in this pipeline resamples, so anything else silently
        # misbehaves rather than erroring where it happens.
        raise ValueError(
            f"audio.sample_rate must be 16000 (got {sample_rate}) - the VAD "
            "and transcription pipeline are hardcoded to it."
        )

    backends = {
        name: LlmBackend(base_url=b["base_url"], model=b["model"])
        for name, b in raw["summarization"]["backends"].items()
    }

    web_auth = _load_web_auth(config_path)

    return Config(
        audio=AudioConfig(
            device=raw["audio"].get("device"),
            sample_rate=sample_rate,
        ),
        vad=VadConfig(
            silence_ms_to_close=int(raw["vad"]["silence_ms_to_close"]),
            min_segment_ms=int(raw["vad"]["min_segment_ms"]),
            max_segment_seconds=int(raw["vad"]["max_segment_seconds"]),
            session_gap_minutes=int(raw["vad"]["session_gap_minutes"]),
            speech_pad_ms=int(raw["vad"]["speech_pad_ms"]),
            threshold=float(raw["vad"]["threshold"]),
        ),
        transcription=TranscriptionConfig(
            model_size=raw["transcription"]["model_size"],
            compute_type=raw["transcription"]["compute_type"],
            model_dir=raw["transcription"].get("model_dir"),
        ),
        keywords=KeywordsConfig(
            triggers=list(raw["keywords"]["triggers"]),
        ),
        summarization=SummarizationConfig(
            active_backend=raw["summarization"]["active_backend"],
            backends=backends,
        ),
        storage=StorageConfig(data_dir=data_dir),
        web=WebConfig(
            host=raw["web"]["host"],
            port=int(raw["web"]["port"]),
            password_hash=web_auth.get("password_hash", ""),
            secret_key=web_auth.get("secret_key", ""),
        ),
        path=config_path,
    )


def ensure_data_dirs(config: Config) -> None:
    config.storage.audio_dir.mkdir(parents=True, exist_ok=True)
    config.storage.logs_dir.mkdir(parents=True, exist_ok=True)

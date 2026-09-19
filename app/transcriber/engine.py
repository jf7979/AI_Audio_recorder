"""Wraps faster-whisper for CPU transcription with word-level timestamps."""
from __future__ import annotations

from pathlib import Path

from faster_whisper import WhisperModel


class TranscriptionEngine:
    def __init__(self, model_size: str, compute_type: str, model_dir: str | None = None):
        # model_dir pins the download/cache location explicitly rather than
        # the default (a per-user profile cache dir). Important if this runs
        # as a Windows service under a dedicated/LocalSystem account whose
        # profile differs from the account used for initial manual testing -
        # without this, the service may try (and fail) to re-download the
        # model under an account with no usable profile/network access.
        self.model = WhisperModel(
            model_size, device="cpu", compute_type=compute_type, download_root=model_dir
        )

    def transcribe(self, audio_path: Path) -> tuple[str, list[dict]]:
        """Returns (full_text, words) where words is a list of
        {"word": str, "start": float, "end": float} dicts."""
        segments, _info = self.model.transcribe(str(audio_path), word_timestamps=True)

        text_parts: list[str] = []
        words: list[dict] = []
        for segment in segments:
            text_parts.append(segment.text)
            for w in segment.words or []:
                words.append({"word": w.word, "start": w.start, "end": w.end})

        return "".join(text_parts).strip(), words

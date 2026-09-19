"""Thin wrapper around Silero VAD's streaming API.

silero-vad's VADIterator reports speech start/end as a running seconds-offset
since the iterator was created (or last reset), NOT wall-clock time - the
segmenter is responsible for converting that offset into an actual timestamp.
Torch is required here even though we run the ONNX compute backend: the
package's VADIterator/OnnxWrapper interface passes torch tensors regardless
of which backend does the underlying inference.
"""
from __future__ import annotations

import numpy as np
import torch
from silero_vad import VADIterator, load_silero_vad


class VoiceActivityDetector:
    def __init__(
        self,
        sample_rate: int,
        silence_ms_to_close: int,
        speech_pad_ms: int,
        threshold: float,
    ):
        self.sample_rate = sample_rate
        model = load_silero_vad(onnx=True)
        self._iterator = VADIterator(
            model,
            sampling_rate=sample_rate,
            threshold=threshold,
            min_silence_duration_ms=silence_ms_to_close,
            speech_pad_ms=speech_pad_ms,
        )

    def process(self, chunk: np.ndarray) -> dict | None:
        """chunk: int16 mono numpy array of exactly one block (see capture.BLOCK_SIZE).
        Returns {'start': seconds} or {'end': seconds} on a state transition, else None."""
        float_chunk = torch.from_numpy(chunk.astype(np.float32) / 32768.0)
        return self._iterator(float_chunk, return_seconds=True)

    def reset(self) -> None:
        self._iterator.reset_states()

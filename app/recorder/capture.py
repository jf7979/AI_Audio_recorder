"""Microphone capture: pushes fixed-size int16 mono blocks onto a queue.

The block size (512 samples) is not arbitrary - it's what Silero VAD's
streaming API expects per call at 16kHz (32ms windows). Keep this in sync
with app.recorder.vad.
"""
from __future__ import annotations

import logging
import queue

import numpy as np
import sounddevice as sd

BLOCK_SIZE = 512
# ~96s of buffering at 32ms/block. Blocks are tiny (1KB each), so this isn't
# about memory size - it's a bound so a stalled consumer (e.g. a slow disk
# write in finalize_segment) can't grow the queue forever; PortAudio keeps
# calling back in real time regardless of whether the consumer keeps up.
QUEUE_MAXSIZE = 3000

logger = logging.getLogger("recorder")


def resolve_device(name_substring: str | None) -> int | None:
    """Returns a sounddevice input device index matching name_substring, or
    None to use the system default input device."""
    if not name_substring:
        return None
    for index, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] > 0 and name_substring.lower() in info["name"].lower():
            return index
    raise RuntimeError(
        f"No input device matching '{name_substring}' found. "
        "Run `python -m app.recorder.list_devices` to see available devices."
    )


class AudioCapture:
    """Opens the mic and pushes int16 mono blocks onto self.frames."""

    def __init__(self, sample_rate: int, device: str | None = None):
        self.sample_rate = sample_rate
        self.device_index = resolve_device(device)
        self.frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=QUEUE_MAXSIZE)
        self._stream: sd.InputStream | None = None

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            logger.warning("Audio input status: %s", status)
        if len(indata) != BLOCK_SIZE:
            # Silero's model requires exactly this window size, and the
            # Segmenter's chunk-index-to-timestamp math assumes every chunk is
            # the same length. PortAudio should always honour blocksize, so
            # this is a guard against a platform quirk turning into either a
            # crash loop in the VAD or silently skewed timestamps.
            logger.warning("Ignoring audio block of unexpected size %d", len(indata))
            return
        try:
            self.frames.put_nowait(indata[:, 0].copy())
        except queue.Full:
            # Consumer has been stuck for ~96s. Drop this block rather than
            # growing without bound; the periodic clock resync in Segmenter
            # bounds how long the resulting timestamp gap can skew things.
            logger.warning("Audio queue full (consumer stalled?) - dropping a block")

    def start(self) -> None:
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=BLOCK_SIZE,
            device=self.device_index,
            callback=self._callback,
        )
        self._stream.start()
        logger.info(
            "Audio capture started (device=%s, sample_rate=%d)",
            self.device_index if self.device_index is not None else "default",
            self.sample_rate,
        )

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            logger.info("Audio capture stopped")

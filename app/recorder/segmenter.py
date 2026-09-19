"""Turns a stream of fixed-size audio blocks + VAD start/end events into
finalized speech segments.

VADIterator reports start/end as seconds elapsed since it was created, not
wall-clock time, and it only recognizes speech has started a little *after*
it actually began (needs a few chunks of confidence). So we keep a short
rolling pre-buffer of recent raw chunks, keyed by a per-chunk sequence
number, and when a 'start' event arrives we splice in whatever pre-roll
audio corresponds to its reported offset.
"""
from __future__ import annotations

import logging
import queue
from collections import deque
from datetime import datetime, timedelta, timezone

import numpy as np

from app.config import Config
from app.recorder.capture import BLOCK_SIZE, AudioCapture
from app.recorder.storage import finalize_segment
from app.recorder.vad import VoiceActivityDetector

logger = logging.getLogger("recorder")

# Long enough to cover typical VAD detection latency + speech_pad_ms.
PRE_ROLL_SECONDS = 3.0

# How often (in seconds of idle, non-speech time) to re-anchor chunk-counted
# timestamps against the real wall clock. Bounds drift from audio-hardware
# clock error - this process runs 24/7, so an unbounded single anchor set at
# startup would let error accumulate for as long as the process stays up.
CLOCK_RESYNC_SECONDS = 30.0

# PortAudio calls back roughly every 32ms even during total silence in the
# room (silence is still audio data). If nothing arrives for this long, the
# input stream itself has died (mic unplugged, driver crash) - fail loudly
# so the process exits and a service manager (NSSM) restarts it, rather than
# sitting "alive" forever on a blocked queue.get() while recording nothing.
FRAME_TIMEOUT_SECONDS = 10.0


class Segmenter:
    def __init__(self, config: Config, conn):
        self.config = config
        self.conn = conn
        self.sample_rate = config.audio.sample_rate
        self.vad = VoiceActivityDetector(
            sample_rate=self.sample_rate,
            silence_ms_to_close=config.vad.silence_ms_to_close,
            speech_pad_ms=config.vad.speech_pad_ms,
            threshold=config.vad.threshold,
        )
        ring_len = int(PRE_ROLL_SECONDS * self.sample_rate / BLOCK_SIZE)
        self._ring: deque[tuple[int, np.ndarray]] = deque(maxlen=ring_len)
        self._chunk_index = 0
        self._stream_start = datetime.now(timezone.utc)
        self._last_resync_chunk_index = 0
        self._resync_interval_chunks = max(1, int(CLOCK_RESYNC_SECONDS * self.sample_rate / BLOCK_SIZE))
        self._max_segment_chunks = max(1, int(config.vad.max_segment_seconds * self.sample_rate / BLOCK_SIZE))
        self._in_segment = False
        self._segment_chunks: list[np.ndarray] = []
        self._segment_start_chunk_index = 0

    def _chunk_index_for_offset(self, seconds: float) -> int:
        return round(seconds * self.sample_rate / BLOCK_SIZE)

    def _time_for_chunk_index(self, chunk_index: int) -> datetime:
        return self._stream_start + timedelta(seconds=chunk_index * BLOCK_SIZE / self.sample_rate)

    def _maybe_resync_clock(self, index: int) -> None:
        # Only safe to re-anchor while idle: a segment's started_at is computed
        # lazily (at close time) from _stream_start, so moving the anchor while
        # one is open would retroactively shift that segment's own start time.
        if self._in_segment:
            return
        if index - self._last_resync_chunk_index < self._resync_interval_chunks:
            return
        now = datetime.now(timezone.utc)
        elapsed = timedelta(seconds=index * BLOCK_SIZE / self.sample_rate)
        self._stream_start = now - elapsed
        self._last_resync_chunk_index = index

    def feed(self, chunk: np.ndarray) -> None:
        index = self._chunk_index
        self._chunk_index += 1
        self._ring.append((index, chunk))
        self._maybe_resync_clock(index)

        event = self.vad.process(chunk)

        if event and "start" in event and not self._in_segment:
            start_index = self._chunk_index_for_offset(event["start"])
            self._segment_chunks = [d for (i, d) in self._ring if i >= start_index]
            self._segment_start_chunk_index = start_index
            self._in_segment = True
        elif self._in_segment:
            self._segment_chunks.append(chunk)

        if event and "end" in event and self._in_segment:
            # event["end"] is the offset where speech actually stopped (plus
            # speech_pad_ms) - NOT "now". VADIterator only reports it once
            # silence_ms_to_close has elapsed to confirm the end, by which
            # point we've already buffered that trailing silence into
            # _segment_chunks. Trim back to the real end instead of keeping it.
            end_index = self._chunk_index_for_offset(event["end"])
            self._close_segment(end_chunk_index=end_index)
        elif self._in_segment and len(self._segment_chunks) >= self._max_segment_chunks:
            # Continuous "speech" with no silence gap (TV/music left on, a
            # false-positive VAD state) would otherwise grow this buffer
            # forever. Force-split into a fresh recording and keep listening -
            # the VAD's own triggered/silence state is untouched, so a later
            # real 'end' event still closes normally.
            self._close_segment(end_chunk_index=index + 1, reopen=True)

    def _close_segment(self, end_chunk_index: int, reopen: bool = False) -> None:
        keep = max(1, end_chunk_index - self._segment_start_chunk_index)
        trimmed_chunks = self._segment_chunks[:keep]
        samples = np.concatenate(trimmed_chunks) if trimmed_chunks else np.array([], dtype=np.int16)
        started_at = self._time_for_chunk_index(self._segment_start_chunk_index)
        ended_at = self._time_for_chunk_index(end_chunk_index)

        if reopen:
            self._segment_chunks = self._segment_chunks[keep:]
            self._segment_start_chunk_index = end_chunk_index
            self._in_segment = True
        else:
            self._segment_chunks = []
            self._in_segment = False

        try:
            finalize_segment(self.config, self.conn, samples, self.sample_rate, started_at, ended_at)
        except Exception:
            logger.exception("Failed to finalize segment starting at %s", started_at)
            try:
                self.conn.rollback()
            except Exception:
                logger.exception("Rollback after failed finalize also failed")

    def close(self) -> None:
        """Force-finalizes whatever segment is currently open, so a graceful
        shutdown (KeyboardInterrupt/SIGTERM) doesn't silently drop the last
        thing that was being recorded."""
        if self._in_segment:
            logger.info("Finalizing in-progress segment before shutdown")
            self._close_segment(end_chunk_index=self._chunk_index)

    def run_forever(self, capture: AudioCapture) -> None:
        logger.info("Segmenter listening (silence_ms_to_close=%d, min_segment_ms=%d)",
                    self.config.vad.silence_ms_to_close, self.config.vad.min_segment_ms)
        while True:
            try:
                chunk = capture.frames.get(timeout=FRAME_TIMEOUT_SECONDS)
            except queue.Empty:
                raise RuntimeError(
                    f"No audio received for {FRAME_TIMEOUT_SECONDS}s - the input stream "
                    "appears to have died (mic unplugged? driver crash?)."
                )
            self.feed(chunk)

import numpy as np
import pytest

import app.recorder.segmenter as segmenter_module
from app.config import (
    AudioConfig,
    Config,
    KeywordsConfig,
    LlmBackend,
    StorageConfig,
    SummarizationConfig,
    TranscriptionConfig,
    VadConfig,
    WebConfig,
)
from app.recorder.capture import BLOCK_SIZE
from app.recorder.segmenter import Segmenter


class FakeVad:
    """Stands in for VoiceActivityDetector: replays a scripted sequence of
    events (one slot per chunk fed), so tests don't need the real Silero
    model/torch/onnxruntime installed."""

    def __init__(self, *args, **kwargs):
        self.events: list[dict | None] = []
        self._i = 0

    def process(self, chunk):
        event = self.events[self._i] if self._i < len(self.events) else None
        self._i += 1
        return event


def _make_config(tmp_path):
    return Config(
        audio=AudioConfig(device=None, sample_rate=16000),
        vad=VadConfig(silence_ms_to_close=1800, min_segment_ms=0, max_segment_seconds=300,
                      session_gap_minutes=5, speech_pad_ms=300, threshold=0.5),
        transcription=TranscriptionConfig(model_size="small.en", compute_type="int8", model_dir=None),
        keywords=KeywordsConfig(triggers=["flag this"]),
        summarization=SummarizationConfig(
            active_backend="local",
            backends={"local": LlmBackend(base_url="http://example.invalid", model="test-model")},
        ),
        storage=StorageConfig(data_dir=tmp_path / "data"),
        web=WebConfig(host="0.0.0.0", port=8420, password_hash="", secret_key="x"),
    )


def _make_segmenter(monkeypatch, config):
    monkeypatch.setattr(segmenter_module, "VoiceActivityDetector", FakeVad)
    calls = []
    monkeypatch.setattr(
        segmenter_module,
        "finalize_segment",
        lambda cfg, conn, samples, sample_rate, started_at, ended_at: calls.append(
            (samples, sample_rate, started_at, ended_at)
        ),
    )
    return Segmenter(config, conn=None), calls


def test_segment_trims_trailing_silence_to_reported_end(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    seg, calls = _make_segmenter(monkeypatch, config)

    chunks = [np.full(BLOCK_SIZE, i, dtype=np.int16) for i in range(20)]

    # 'start' fires exactly at chunk 5's own offset. Speech actually ends at
    # chunk 10's offset, but (as with the real Silero VADIterator) the 'end'
    # event only arrives once silence_ms_to_close has elapsed to confirm it -
    # simulated here as 5 chunks later, at chunk 15.
    seg.vad.events = [None] * 20
    seg.vad.events[5] = {"start": 5 * BLOCK_SIZE / seg.sample_rate}
    seg.vad.events[15] = {"end": 10 * BLOCK_SIZE / seg.sample_rate}

    for chunk in chunks:
        seg.feed(chunk)

    assert len(calls) == 1
    samples, sample_rate, started_at, ended_at = calls[0]

    # Must keep only chunks 5-9 (the actual speech), not chunks 5-15 (which
    # would bake in ~5 chunks of trailing silence that only existed because
    # the VAD needed it to *confirm* the segment had ended).
    assert len(samples) == 5 * BLOCK_SIZE
    assert (ended_at - started_at).total_seconds() == pytest.approx(5 * BLOCK_SIZE / seg.sample_rate)


def test_segment_start_pulls_preroll_from_ring_buffer(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    seg, calls = _make_segmenter(monkeypatch, config)

    chunks = [np.full(BLOCK_SIZE, i, dtype=np.int16) for i in range(10)]

    # VAD only reports 'start' at chunk 4, but says speech actually began
    # back at chunk 2's offset - the segment must include that pre-roll.
    seg.vad.events = [None] * 10
    seg.vad.events[4] = {"start": 2 * BLOCK_SIZE / seg.sample_rate}
    seg.vad.events[8] = {"end": 7 * BLOCK_SIZE / seg.sample_rate}

    for chunk in chunks:
        seg.feed(chunk)

    assert len(calls) == 1
    samples, _sample_rate, _started_at, _ended_at = calls[0]
    assert len(samples) == 5 * BLOCK_SIZE  # chunks 2,3,4,5,6


def test_segment_force_splits_after_max_duration(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    seg, calls = _make_segmenter(monkeypatch, config)
    seg._max_segment_chunks = 5  # force a split well before any real 'end' event

    # Continuous "speech" with no silence gap at all (e.g. TV/music left on)
    # - never gets an 'end' event in this test.
    chunks = [np.full(BLOCK_SIZE, i, dtype=np.int16) for i in range(8)]
    seg.vad.events = [None] * 8
    seg.vad.events[0] = {"start": 0.0}

    for chunk in chunks:
        seg.feed(chunk)

    assert len(calls) == 1
    samples, _sample_rate, started_at, ended_at = calls[0]
    assert len(samples) == 5 * BLOCK_SIZE
    assert (ended_at - started_at).total_seconds() == pytest.approx(5 * BLOCK_SIZE / seg.sample_rate)

    # Kept listening as a continuation rather than dropping the ongoing speech.
    assert seg._in_segment is True
    assert len(seg._segment_chunks) == 3  # chunks 5,6,7 accumulated since the split


def test_close_finalizes_in_progress_segment(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    seg, calls = _make_segmenter(monkeypatch, config)

    chunks = [np.full(BLOCK_SIZE, i, dtype=np.int16) for i in range(5)]
    seg.vad.events = [None] * 5
    seg.vad.events[0] = {"start": 0.0}

    for chunk in chunks:
        seg.feed(chunk)

    assert len(calls) == 0  # no 'end' event yet - segment is still open
    assert seg._in_segment is True

    seg.close()

    assert len(calls) == 1
    samples, _sample_rate, started_at, ended_at = calls[0]
    assert len(samples) == 5 * BLOCK_SIZE
    assert (ended_at - started_at).total_seconds() == pytest.approx(5 * BLOCK_SIZE / seg.sample_rate)


def test_close_is_a_noop_when_no_segment_is_open(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    seg, calls = _make_segmenter(monkeypatch, config)

    seg.close()

    assert len(calls) == 0

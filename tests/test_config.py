import textwrap

import pytest

from app.config import load_config


def _write_config(tmp_path, data_dir="./data", sample_rate=16000):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            f"""\
            audio:
              device: null
              sample_rate: {sample_rate}
            vad:
              silence_ms_to_close: 1800
              min_segment_ms: 800
              max_segment_seconds: 300
              session_gap_minutes: 5
              speech_pad_ms: 300
              threshold: 0.5
            transcription:
              model_size: small.en
              compute_type: int8
            keywords:
              triggers: ["flag this"]
            summarization:
              active_backend: local
              backends:
                local:
                  base_url: "http://localhost:11434/v1"
                  model: "test-model"
            storage:
              data_dir: "{data_dir}"
            web:
              host: "0.0.0.0"
              port: 8420
            """
        )
    )
    return config_path


def test_load_config_rejects_non_16k_sample_rate(tmp_path):
    config_path = _write_config(tmp_path, sample_rate=44100)
    with pytest.raises(ValueError, match="16000"):
        load_config(config_path)


def test_load_config_accepts_16k_sample_rate(tmp_path):
    config_path = _write_config(tmp_path, sample_rate=16000)
    config = load_config(config_path)
    assert config.audio.sample_rate == 16000


def test_load_config_rejects_cloud_synced_data_dir(tmp_path):
    # Simulate an iCloud-style path by nesting the data dir under a
    # com~apple~CloudDocs-looking directory component, like this project's
    # own working directory.
    cloud_data_dir = tmp_path / "com~apple~CloudDocs" / "Documents" / "data"
    config_path = _write_config(tmp_path, data_dir=str(cloud_data_dir))
    with pytest.raises(ValueError, match="cloud-synced"):
        load_config(config_path)

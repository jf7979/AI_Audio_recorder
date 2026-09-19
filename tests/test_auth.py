from werkzeug.security import generate_password_hash

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
from app.db import init_db
from app.web.app import create_app
from app.web.auth import _failed_attempts

PASSWORD = "correct horse battery staple"


def _make_config(tmp_path):
    return Config(
        audio=AudioConfig(device=None, sample_rate=16000),
        vad=VadConfig(silence_ms_to_close=1800, min_segment_ms=800, max_segment_seconds=300,
                      session_gap_minutes=5, speech_pad_ms=300, threshold=0.5),
        transcription=TranscriptionConfig(model_size="small.en", compute_type="int8", model_dir=None),
        keywords=KeywordsConfig(triggers=["flag this"]),
        summarization=SummarizationConfig(
            active_backend="local",
            backends={"local": LlmBackend(base_url="http://example.invalid", model="test-model")},
        ),
        storage=StorageConfig(data_dir=tmp_path / "data"),
        web=WebConfig(
            host="127.0.0.1", port=8420,
            password_hash=generate_password_hash(PASSWORD),
            secret_key="test-secret",
        ),
    )


def _make_client(tmp_path):
    _failed_attempts.clear()
    config = _make_config(tmp_path)
    config.storage.data_dir.mkdir(parents=True, exist_ok=True)
    init_db(config.storage.db_path)
    app = create_app(config)
    app.testing = True
    return app.test_client()


def test_create_app_refuses_to_start_without_auth_configured(tmp_path):
    config = _make_config(tmp_path)
    config.web.password_hash = ""
    try:
        create_app(config)
        assert False, "expected create_app to raise"
    except RuntimeError as e:
        assert "setup_password.py" in str(e)


def test_login_succeeds_with_correct_password(tmp_path):
    client = _make_client(tmp_path)
    response = client.post("/login", data={"password": PASSWORD})
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess["logged_in"] is True


def test_login_fails_with_wrong_password(tmp_path):
    client = _make_client(tmp_path)
    response = client.post("/login", data={"password": "wrong"})
    assert response.status_code == 200
    assert b"Incorrect password" in response.data


def test_login_rate_limits_after_repeated_failures(tmp_path):
    client = _make_client(tmp_path)
    for _ in range(5):
        client.post("/login", data={"password": "wrong"})

    # Even the CORRECT password is rejected once locked out.
    response = client.post("/login", data={"password": PASSWORD})
    assert b"Too many failed attempts" in response.data


def test_login_rejects_unsafe_next_redirect(tmp_path):
    client = _make_client(tmp_path)
    response = client.post("/login?next=https://evil.example.com", data={"password": PASSWORD})
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_login_allows_safe_next_redirect(tmp_path):
    client = _make_client(tmp_path)
    response = client.post("/login?next=/flags", data={"password": PASSWORD})
    assert response.status_code == 302
    assert response.headers["Location"] == "/flags"

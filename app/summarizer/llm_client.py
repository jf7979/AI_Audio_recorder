"""Minimal client for any OpenAI-compatible /v1/chat/completions server -
works unchanged against Ollama (local mini PC) or LM Studio (Mac Studio on
the LAN), since both implement that same API surface."""
from __future__ import annotations

import requests


CONNECT_TIMEOUT_SECONDS = 10


class LlmClient:
    def __init__(self, base_url: str, model: str, timeout: int = 600):
        self.base_url = base_url.rstrip("/")
        self.model = model
        # Separate connect vs read timeouts: generation legitimately takes
        # minutes on CPU, but failing to *reach* the backend at all (LAN box
        # powered off, LM Studio server not started) should give up quickly
        # rather than hanging a web request for the full read timeout.
        self.timeout = (CONNECT_TIMEOUT_SECONDS, timeout)

    def chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        response = requests.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "messages": messages, "temperature": temperature},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

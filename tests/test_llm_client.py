"""Tests del fallback del cliente LLM."""
from __future__ import annotations

from app.chat.llm_client import GEMINI_LITE_FALLBACK_MODEL, LLMClient, SYSTEM_PROMPT


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_system_prompt_forbids_explaining_external_topics():
    assert "No expliques ni respondas preguntas sobre temas ajenos" in SYSTEM_PROMPT
    assert "No describas, definas ni menciones datos sobre el tema externo" in SYSTEM_PROMPT


def test_gemini_uses_lite_fallback_after_503(monkeypatch):
    client = LLMClient("gemini")
    models = []

    monkeypatch.setattr(
        "app.chat.llm_client.secrets.get_secret", lambda _name: "test-key"
    )

    def fake_post(_url, **kwargs):
        models.append(kwargs["json"]["model"])
        if len(models) == 1:
            return FakeResponse(503, text="high demand")
        return FakeResponse(
            200,
            {"choices": [{"message": {"content": " Respuesta lite "}}]},
        )

    monkeypatch.setattr("app.chat.llm_client.requests.post", fake_post)

    assert client.ask("Que es el tempo?") == "Respuesta lite"
    assert models == ["gemini-flash-latest", GEMINI_LITE_FALLBACK_MODEL]


def test_gemini_does_not_fallback_for_non_transient_error(monkeypatch):
    client = LLMClient("gemini")
    models = []

    monkeypatch.setattr(
        "app.chat.llm_client.secrets.get_secret", lambda _name: "test-key"
    )

    def fake_post(_url, **kwargs):
        models.append(kwargs["json"]["model"])
        return FakeResponse(400, text="bad request")

    monkeypatch.setattr("app.chat.llm_client.requests.post", fake_post)

    assert client.ask("pregunta") is None
    assert models == ["gemini-flash-latest"]

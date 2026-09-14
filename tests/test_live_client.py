"""Tests del cliente local de Live Client Data API."""
from __future__ import annotations

from app.riot.live_client import LiveClient


def test_live_client_normalizes_base_url():
    client = LiveClient("https://127.0.0.1:2999")
    assert client.base_url == "https://127.0.0.1:2999/liveclientdata"


def test_live_client_diagnostics_reports_last_error(monkeypatch):
    client = LiveClient("https://127.0.0.1:2999/liveclientdata")

    def fake_get(path):
        client.last_error = {
            "url": f"{client.base_url}{path}",
            "kind": "ConnectionError",
            "message": "connection refused",
        }
        return None

    monkeypatch.setattr(client, "_get", fake_get)
    diagnostics = client.diagnostics()

    assert diagnostics["available"] is False
    assert diagnostics["kind"] == "ConnectionError"
    assert "connection refused" in diagnostics["message"]
    assert "127.0.0.1:2999" in diagnostics["url"]

from __future__ import annotations

from dataclasses import replace
import hashlib
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import joblib
import pytest

from app.cloud.assets import allowed_asset, extract_assets
from app.config import load_settings
from app.container import ServiceContainer
from app.data.database import Database
from app.data.repositories import MatchRepository
from app.ml.champion_registry import ChampionModelRegistry
from app.security import secrets
from scripts.export_demo_assets import export_assets


@pytest.mark.parametrize("name", ["../escape", "/app/.env", "storage/../.env", "storage\\file.json", "app/config.py", "models/key.pem"])
def test_bundle_rejects_unexpected_paths(name):
    assert not allowed_asset(name)


def test_bundle_validates_hash_and_all_paths_before_extracting(tmp_path):
    archive = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("demo.json", "{}")
        package.writestr("../escape", "unwanted")
    destination = tmp_path / "out"
    destination.mkdir()
    with pytest.raises(ValueError, match="coincide"):
        extract_assets(archive, destination, "0" * 64)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="Ruta"):
        extract_assets(archive, destination, digest)
    assert list(destination.iterdir()) == []


def test_cloud_does_not_load_dotenv_or_riot_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test-only")
    local_env = Mock(side_effect=AssertionError("No cargar .env en la demo"))
    monkeypatch.setattr("app.config.load_dotenv", local_env)
    settings = load_settings(data_root=tmp_path)
    assert settings.public_demo
    assert settings.database_path == tmp_path / "storage/lol_recommender.db"
    assert secrets.has_riot_api_key() is False
    local_env.assert_not_called()


def test_model_cache_reuses_loaded_object_and_picks_new_version(tmp_path, monkeypatch):
    registry = ChampionModelRegistry(tmp_path)
    folder = registry.champion_dir("Diana", "GOLD")
    for suffix, value in (("001", 1),):
        joblib.dump({"value": value}, folder / f"live_model_{suffix}.joblib")
        (folder / f"live_model_{suffix}.json").write_text('{"patch": "16.13"}')
    original_load = joblib.load
    loader = Mock(wraps=original_load)
    monkeypatch.setattr("app.ml.champion_registry.joblib.load", loader)
    first, _ = registry.latest("live", "Diana", "GOLD")
    assert registry.latest("live", "Diana", "GOLD")[0] is first
    assert loader.call_count == 1
    joblib.dump({"value": 2}, folder / "live_model_002.joblib")
    (folder / "live_model_002.json").write_text('{"patch": "16.14"}')
    assert registry.latest("live", "Diana", "GOLD")[0]["value"] == 2
    assert registry.latest("live", "Diana", "GOLD", "16.13")[0] is first


def test_demo_never_polls_live_client_or_rebuilds_for_force(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    c = ServiceContainer(load_settings(data_root=tmp_path))
    try:
        c.live_client.diagnostics = Mock(side_effect=AssertionError("No loopback in cloud"))
        c.refresh_live_state = Mock(side_effect=AssertionError("No rebuilding static demo"))
        c._live_cache.update(ts=1, snapshot={"simulated": "replay"}, recommendations={"ok": True})
        assert c.live_state(max_age=0)[0]["simulated"] == "replay"
        assert c.live_client_diagnostics()["available"] is False
        assert c.live_cache_age_seconds() == 0
        c.engine.build = Mock(return_value={"items": []})
        c.demo_recommendations({}, "agresivo")
        c.demo_recommendations({}, "agresivo")
        c.engine.build.assert_called_once()
    finally:
        c.close()


def test_public_api_blocks_training_limits_chat_and_warms_once(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.server import create_app

    monkeypatch.setenv("PUBLIC_DEMO", "true")
    container = SimpleNamespace(
        settings=load_settings(data_root=tmp_path),
        refresh_live_state=Mock(), close=Mock(),
        chat=SimpleNamespace(answer=Mock(return_value={
            "answer": "Respuesta local", "intent": "general", "data_available": True,
        })),
    )
    with TestClient(create_app(container)) as client:
        assert client.get("/api/health").json()["public_demo"] is True
        for path in ("/api/coach-ai/jobs", "/api/coach-ai/jobs/", "/api/coach-ai/estimate"):
            assert client.post(path, json={"champion": "Diana"}).status_code == 403
        for _ in range(30):
            assert client.post("/api/chat", json={"question": "Hola"}).status_code == 200
        denied = client.post("/api/chat", json={"question": "Hola"}, headers={"X-Forwarded-For": "1.2.3.4"})
        assert denied.status_code == 429
        assert denied.headers["Retry-After"] == "60"
        assert client.get("/api/health").status_code == 200
        assert container.chat.answer.call_count == 30
    container.refresh_live_state.assert_called_once()
    container.close.assert_called_once()


def test_local_api_still_allows_training(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.server import create_app

    monkeypatch.setenv("PUBLIC_DEMO", "false")
    settings = replace(load_settings(data_root=tmp_path), public_demo=False)
    app = create_app(SimpleNamespace(settings=settings, refresh_live_state=Mock()))
    with TestClient(app, raise_server_exceptions=True) as client:
        # Avoid unrelated local background IO in this minimal fake container.
        response = client.post("/api/coach-ai/estimate", json={"champion": "Diana"})
        assert response.status_code == 200


def test_export_roundtrip_is_small_and_does_not_change_original(tmp_path, sample_participants):
    root = tmp_path / "project"
    (root / "storage").mkdir(parents=True)
    db = Database(root / "storage/lol_recommender.db")
    repo = MatchRepository(db)
    participants = sample_participants.copy()
    matches = participants.drop_duplicates("matchId")[["matchId", "gameCreation", "gameDuration", "gameVersion", "queueId"]]
    repo.upsert_bundle({"matches": matches, "participants": participants})
    db.close()
    cache = root / "storage/ddragon"
    cache.mkdir()
    (cache / "versions.json").write_text('["16.13.1"]')
    for name in ("champion", "item"):
        (cache / f"{name}_es_MX_16.13.1.json").write_text('{"data": {}}')
    folder = root / "models/champions/Ahri/GOLD"
    folder.mkdir(parents=True)
    for kind in ("item", "live"):
        joblib.dump({"model": "test"}, folder / f"{kind}_model_001.joblib")
        (folder / f"{kind}_model_001.json").write_text('{"trained_for": "private-name", "patch": "16.13"}')
    original = hashlib.sha256((root / "storage/lol_recommender.db").read_bytes()).hexdigest()
    output = tmp_path / "assets.zip"
    digest = export_assets(root, output, "Ahri", "GOLD", limit=2)
    assert hashlib.sha256((root / "storage/lol_recommender.db").read_bytes()).hexdigest() == original
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    manifest = extract_assets(output, unpacked, digest)
    assert manifest["match_count"] == 2
    assert manifest["puuid"].startswith("demo-")
    conn = sqlite3.connect(unpacked / "storage/lol_recommender.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM participants WHERE riotIdTagline != 'DEMO'").fetchone()[0] == 0
    finally:
        conn.close()
    with pytest.raises(FileExistsError):
        export_assets(root, output, "Ahri", "GOLD")

"""Prueba local de la factory cloud con un ZIP, sin llamadas a Riot ni al LLM."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    os.environ["PUBLIC_DEMO"] = "true"
    os.environ["LLM_PROVIDER"] = ""
    os.environ["DEMO_ASSETS_PATH"] = str(args.archive.resolve())
    with args.archive.open("rb") as source:
        os.environ["DEMO_ASSETS_SHA256"] = hashlib.file_digest(source, "sha256").hexdigest()
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = "1"
    from fastapi.testclient import TestClient
    from app.cloud.runtime import create_cloud_app

    started = time.perf_counter()
    with patch("requests.sessions.Session.request", side_effect=AssertionError("La demo debe funcionar offline")):
        app = create_cloud_app()
        try:
            with TestClient(app) as client:
                print(f"Arranque local: {time.perf_counter() - started:.2f}s", flush=True)
                for path in ("/", "/api/health", "/api/player/profile", "/api/player/history",
                             "/api/live/status", "/api/live/game", "/api/review/matches", "/api/overlay/state"):
                    response = client.get(path)
                    response.raise_for_status()
                    print(f"OK {path}", flush=True)
                started_batch = time.perf_counter()
                with ThreadPoolExecutor(max_workers=5) as pool:
                    responses = list(pool.map(lambda _: client.get("/api/live/recommendations"), range(5)))
                for response in responses:
                    response.raise_for_status()
                    assert "modelo_live" in response.json()["win_probability"]["method"]
                print(f"Cinco solicitudes concurrentes: {time.perf_counter() - started_batch:.2f}s", flush=True)
                for style in ("agresivo", "defensivo", "conservador"):
                    client.get("/api/live/recommendations", params={"item_style": style}).raise_for_status()
                client.post("/api/chat", json={"question": "que item debo comprar ahora?"}).raise_for_status()
                assert client.post("/api/coach-ai/jobs", json={"champion": "Diana"}).status_code == 403
                print("Modelos reales, frontend y rutas de demo: OK", flush=True)
        finally:
            app.state.demo_runtime.cleanup()


if __name__ == "__main__":
    main()

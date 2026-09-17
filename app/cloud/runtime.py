"""Factory ASGI: python -m app.cloud.runtime; no ingesta ni entrenamiento."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import tempfile

from app.cloud.assets import download_assets, extract_assets


def create_cloud_app():
    # Incluso una ejecucion local de esta factory nunca carga .env ni Riot.
    os.environ["PUBLIC_DEMO"] = "true"
    from app.config import load_settings
    from app.container import ServiceContainer
    from app.api.server import create_app
    from app.logging_config import setup_logging
    from app.pipelines.simulate_live import snapshot_from_replay

    runtime = tempfile.TemporaryDirectory(prefix="lol-demo-")
    root = Path(runtime.name)
    container = None
    try:
        local_archive = os.getenv("DEMO_ASSETS_PATH")
        archive = Path(local_archive) if local_archive else root / "assets.zip"
        if not local_archive:
            download_assets(os.getenv("DEMO_ASSETS_URI", ""), archive)
        manifest = extract_assets(archive, root, os.getenv("DEMO_ASSETS_SHA256", ""))
        if not local_archive:
            archive.unlink()  # Solo el ZIP temporal que acaba de descargar esta factory.
        settings = replace(
            load_settings(data_root=root),
            game_name=manifest["game_name"], tag_line=manifest["tag_line"],
            champion=manifest["champion"], target_tier=manifest["tier"],
            language=manifest["language"], host="0.0.0.0",
            port=int(os.getenv("PORT", "8080")), public_demo=True,
        )
        setup_logging(settings.log_level)
        if not (settings.frontend_dist_dir / "index.html").is_file():
            raise RuntimeError("Falta frontend/dist: compila el frontend antes de iniciar la demo.")
        container = ServiceContainer(settings)
        snapshot = snapshot_from_replay(
            container.repo, container.ddragon, my_puuid=manifest["puuid"],
            match_id=manifest["match_id"], minute=15,
        )
        if snapshot is None:
            raise RuntimeError("No se pudo preparar la partida de demostracion.")
        container.snapshot_provider = lambda: snapshot
        for kind in ("item", "live"):
            if container.champion_registry.latest(kind, settings.champion, settings.target_tier) is None:
                raise RuntimeError(f"El modelo {kind} no es compatible con este entorno.")
        app = create_app(container)
        # Mantiene vivos los archivos durante toda la vida del proceso.
        app.state.demo_runtime = runtime
        return app
    except Exception:
        if container is not None:
            container.close()
        runtime.cleanup()
        raise


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.cloud.runtime:create_cloud_app", factory=True,
        host="0.0.0.0", port=int(os.getenv("PORT", "8080")), workers=1,
    )

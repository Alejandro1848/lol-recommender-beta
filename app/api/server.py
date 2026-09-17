"""Servidor FastAPI: monta rutas, middleware, frontend estatico y la
tarea de inferencia periodica en vivo."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import (
    routes_chat,
    routes_coach_settings,
    routes_live,
    routes_overlay,
    routes_player,
    routes_qr,
    routes_recommendations,
    routes_review,
)
from app.container import ServiceContainer
from app.security.auth_stub import AuthStubMiddleware

logger = logging.getLogger(__name__)


def create_app(container: ServiceContainer) -> FastAPI:
    settings = container.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.public_demo:
            # La preparacion termina antes de aceptar trafico. Cloud Run puede
            # suspender CPU fuera de solicitudes; no usar hilos periodicos aqui.
            try:
                await asyncio.to_thread(container.refresh_live_state)
                yield
            finally:
                container.close()
            return
        # Inferencia periodica: refresca snapshot+recomendaciones mientras
        # haya partida activa, cada REFRESH_SECONDS.
        stop_event = asyncio.Event()

        async def periodic_refresh():
            loop = asyncio.get_running_loop()
            while not stop_event.is_set():
                try:
                    await loop.run_in_executor(None, container.refresh_live_state)
                except Exception:
                    logger.exception("Fallo el refresh periodico en vivo.")
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=settings.refresh_seconds
                    )
                except asyncio.TimeoutError:
                    pass

        task = asyncio.create_task(periodic_refresh())
        yield
        stop_event.set()
        task.cancel()

    app = FastAPI(
        title="LoL Recommender",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.container = container

    app.add_middleware(AuthStubMiddleware)
    if settings.public_demo:
        from app.cloud.demo_guard import DemoGuardMiddleware

        app.add_middleware(DemoGuardMiddleware)
    app.add_middleware(
        CORSMiddleware,
        # Solo origenes locales: la app no esta pensada para exponerse a la red.
        allow_origins=[
            f"http://{settings.host}:{settings.port}",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "public_demo": settings.public_demo}

    app.include_router(routes_player.router, prefix="/api")
    app.include_router(routes_live.router, prefix="/api")
    app.include_router(routes_recommendations.router, prefix="/api")
    app.include_router(routes_chat.router, prefix="/api")
    app.include_router(routes_coach_settings.router, prefix="/api")
    app.include_router(routes_review.router, prefix="/api")
    app.include_router(routes_overlay.router, prefix="/api")
    app.include_router(routes_qr.router, prefix="/api")

    # Pagina del overlay fuera de /api (usable en navegador u OBS); debe
    # declararse ANTES del mount del frontend para que no la tape.
    @app.get("/overlay", response_class=HTMLResponse, include_in_schema=False)
    def overlay_page():
        return routes_overlay.OVERLAY_HTML

    # Atajo corto para los QR de acceso desde el movil.
    @app.get("/qr", response_class=HTMLResponse, include_in_schema=False)
    def qr_shortcut(request: Request):
        return routes_qr.qr_page(request)

    # Frontend: si existe el build de Vite, se sirve en /
    dist = settings.frontend_dist_dir
    if dist.exists() and (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
        logger.info("Frontend servido desde %s", dist)
    else:
        @app.get("/", response_class=HTMLResponse)
        def frontend_placeholder():
            return (
                "<html><body style='font-family:sans-serif;background:#0d1117;"
                "color:#e6edf3;padding:40px'>"
                "<h1>LoL Recommender API activa</h1>"
                "<p>El frontend aun no esta compilado. Ejecuta:</p>"
                "<pre>cd frontend\nnpm install\nnpm run build</pre>"
                "<p>o en desarrollo: <code>npm run dev</code> "
                "(http://localhost:5173).</p>"
                "<p>Documentacion de la API: <a style='color:#58a6ff' "
                "href='/api/docs'>/api/docs</a></p>"
                "</body></html>"
            )

    return app

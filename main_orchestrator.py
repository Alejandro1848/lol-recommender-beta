"""Orquestador principal de LoL Recommender.

Coordina servicios y flujos; la logica pesada vive en app/. Modos:

  python main_orchestrator.py --mode app             # flujo completo + servidor
  python main_orchestrator.py --mode ingest-history  # descarga/carga historial
  python main_orchestrator.py --mode ingest-ladder   # muestra grande por liga
  python main_orchestrator.py --mode train-champion  # modelo por campeon (CHAMPION en .env)
  python main_orchestrator.py --mode ingest-timelines # timelines para el modelo in-game
  python main_orchestrator.py --mode live-check      # diagnostico de partida en vivo
  python main_orchestrator.py --mode overlay         # overlay in-game always-on-top
"""
from __future__ import annotations

import argparse
import logging
import sys
import webbrowser

import pandas as pd

from app.config import get_settings
from app.container import ServiceContainer
from app.logging_config import setup_logging
from app.ml.evaluation import InsufficientDataError
from app.pipelines.ingest_history import ensure_seed_loaded, ingest_history
from app.pipelines.update_static_data import update_static_data
from app.riot.riot_client import RiotApiError
from app.security import secrets
from app.security.secrets import MissingSecretError

logger = logging.getLogger("orchestrator")


# ----------------------------------------------------------------- modos

def mode_ingest_history(container: ServiceContainer) -> int:
    try:
        summary = ingest_history(container.settings, container.repo, container.riot_client)
    except (RiotApiError, MissingSecretError, ValueError) as exc:
        logger.error("Ingesta fallida: %s", exc)
        seeded = ensure_seed_loaded(container.repo, container.settings)
        if seeded:
            logger.info("Se cargaron %s partidas desde los CSV seed.", seeded)
        return 1
    logger.info("Ingesta completa: %s", summary)
    return 0


def mode_ingest_ladder(
    container: ServiceContainer,
    target_matches: int | None = None,
    target_records: int = 100_000,
    per_player: int = 20,
    max_players: int = 1_200,
    target_tier: str | None = None,
    days: int = 30,
) -> int:
    from app.pipelines.ingest_ladder import ingest_ladder_matches

    try:
        summary = ingest_ladder_matches(
            container.settings,
            container.repo,
            container.riot_client,
            target_matches=target_matches,
            target_records=target_records,
            per_player=per_player,
            max_players=max_players,
            target_tier=target_tier,
            days=days,
        )
    except (RiotApiError, MissingSecretError, ValueError) as exc:
        logger.error("Ingesta masiva por liga fallida: %s", exc)
        return 1
    logger.info("Ingesta masiva por liga completa: %s", summary)
    return 0


def mode_train_champion(
    container: ServiceContainer,
    champion: str | None = None,
    target_tier: str | None = None,
    force: bool = False,
) -> int:
    """Entrena/reutiliza los modelos del campeon indicado (o CHAMPION en .env).

    Pensado para correrse antes de cada partida: si ya existe un modelo del
    campeon para la liga y el parche actual (caso OTP), NO se reentrena;
    solo un parche nuevo o --force-retrain disparan otro entrenamiento.
    """
    from app.ml.train_champion import train_champion_models
    from app.pipelines.build_training_dataset import RANKED_QUEUE_IDS

    settings = container.settings
    champion = (champion or settings.champion).strip()
    if not champion:
        logger.error("Indica el campeon con --champion o CHAMPION en .env.")
        return 1
    tier = (target_tier or settings.target_tier or "GOLD").upper()

    ensure_seed_loaded(container.repo, settings)
    update_static_data(container.ddragon)

    participants = container.repo.participants_df(enriched=True)
    if participants.empty:
        logger.error("No hay partidas en la DB. Ejecuta primero --mode ingest-ladder.")
        return 1
    if "queueId" in participants.columns:
        queue_ids = pd.to_numeric(participants["queueId"], errors="coerce")
        participants = participants[queue_ids.isin(RANKED_QUEUE_IDS) | queue_ids.isna()]

    # Normalizar el nombre al usado por la API (MissFortune, Kaisa, ...).
    known = participants["championName"].dropna().unique()
    canonical = {str(name).lower().replace(" ", "").replace("'", ""): str(name) for name in known}
    champion = canonical.get(champion.lower().replace(" ", "").replace("'", ""), champion)

    try:
        result = train_champion_models(
            participants,
            champion,
            tier,
            container.champion_registry,
            container.ddragon,
            max_matches=settings.champion_history_matches,
            force=force,
        )
    except InsufficientDataError as exc:
        logger.error("Entrenamiento por campeon cancelado: %s", exc)
        return 1

    logger.info(
        "Campeon %s | liga %s | parche %s | %s partidas en el historico.",
        result["champion"], result["tier"], result["patch"], result["n_samples"],
    )
    if "item" in result["skipped"]:
        logger.info("Modelo de items vigente para este parche: se reutiliza sin reentrenar.")
    else:
        meta = result["item"]
        logger.info(
            "Modelo de items entrenado: %s | validacion=%s | test=%s",
            meta["algorithm"], meta["validation_metrics"], meta["metrics"],
        )
        for name, path in (meta.get("plots") or {}).items():
            logger.info("Plot %s de items: %s", name, path)
    # Modelo in-game (si hay timelines descargadas): mismo criterio de
    # vigencia por parche que el modelo de items. Es EL modelo de
    # probabilidad de victoria: la partida arranca en 50% fijo y este
    # construye el numero con el estado real (ver app/ml/inference.py).
    try:
        from app.ml.train_live_model import train_live_win_model

        if force or container.champion_registry.needs_training(
            "live", result["champion"], tier, result["patch"]
        ):
            live_meta = train_live_win_model(
                container.repo.timeline_minutes_df(),
                result["champion"], tier, result["patch"],
                container.champion_registry,
            )
            logger.info(
                "Modelo in-game entrenado: %s | test=%s | AUC por minuto=%s",
                live_meta["algorithm"], live_meta["metrics"], live_meta["auc_by_minute"],
            )
            for name, path in (live_meta.get("plots") or {}).items():
                logger.info("Plot %s in-game: %s", name, path)
        else:
            logger.info("Modelo in-game vigente para este parche: se reutiliza.")
    except InsufficientDataError as exc:
        logger.warning("Modelo in-game omitido: %s", exc)

    status = container.champion_registry.status(result["champion"], result["tier"], result["patch"])
    logger.info("Registry del campeon: %s", status)
    return 0


def mode_ingest_timelines(
    container: ServiceContainer,
    champion: str | None = None,
    max_matches: int | None = None,
) -> int:
    """Descarga timelines de las partidas del campeon (1 llamada c/u) para
    entrenar el modelo in-game. Reanudable."""
    from app.pipelines.ingest_timelines import ingest_timelines

    settings = container.settings
    champion = (champion or settings.champion).strip()
    if not champion:
        logger.error("Indica el campeon con --champion o CHAMPION en .env.")
        return 1
    try:
        summary = ingest_timelines(
            settings, container.repo, container.riot_client, champion,
            max_matches=max_matches or settings.champion_history_matches,
        )
    except (RiotApiError, MissingSecretError, ValueError) as exc:
        logger.error("Ingesta de timelines fallida: %s", exc)
        return 1
    logger.info("Ingesta de timelines completa: %s", summary)
    return 0


def mode_live_check(container: ServiceContainer) -> int:
    live_ok = container.live_client.is_available()
    logger.info("Live Client Data API: %s", "DISPONIBLE" if live_ok else "no disponible")
    if live_ok:
        snapshot, _ = container.live_state()
        if snapshot:
            me = snapshot.get("me", {})
            rival = snapshot.get("direct_rival")
            logger.info("Jugando: %s con %s (%s)", me.get("riot_id"), me.get("champion"), me.get("position"))
            logger.info("Rival directo: %s", f"{rival.get('champion')} ({rival.get('riot_id')})" if rival else "no identificado")
            logger.info("Tiempo de juego: %.0fs", snapshot.get("game_time_seconds") or 0)
    if secrets.has_riot_api_key():
        account = container.resolve_account()
        if account:
            try:
                active = container.riot_client.get_active_game(account["puuid"])
                logger.info("Spectator-V5: %s", "partida activa" if active else "sin partida activa")
            except RiotApiError as exc:
                logger.warning("Spectator-V5 no disponible: %s", exc)
        else:
            logger.warning("Cuenta no resuelta: %s", container.account_error)
    else:
        logger.warning("Sin RIOT_API_KEY: se omite el chequeo via Spectator-V5.")
    return 0


def mode_app(container: ServiceContainer) -> int:
    """Flujo completo: datos -> servidor -> frontend.

    Los modelos por campeon (item + live) se cargan bajo demanda desde el
    registry; el antiguo modelo pregame se elimino (rendia como una moneda).
    """
    settings = container.settings

    # 1-2. Config y servicios ya inicializados por el container.
    # 3-4. Resolver cuenta (si hay key). 5-7. Historial + cache.
    if secrets.has_riot_api_key():
        try:
            summary = ingest_history(settings, container.repo, container.riot_client)
            logger.info("Historial listo: %s partidas en DB.", summary["total_matches_in_db"])
        except (RiotApiError, ValueError, MissingSecretError) as exc:
            logger.warning("Ingesta omitida (%s). Se usara el historial local.", exc)
            ensure_seed_loaded(container.repo, settings)
    else:
        logger.warning("Sin RIOT_API_KEY: la app funcionara solo con datos locales y Live Client.")
        ensure_seed_loaded(container.repo, settings)

    update_static_data(container.ddragon)

    # 8-10. Servidor FastAPI + frontend + inferencia periodica (lifespan).
    import uvicorn
    from app.api.server import create_app

    app = create_app(container)
    # 0.0.0.0 escucha en todas las interfaces pero no es navegable: para
    # abrir/mostrar se usa loopback, y se anuncia la IP LAN para el movil.
    display_host = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
    url = f"http://{display_host}:{settings.port}"
    logger.info("=" * 60)
    logger.info("LoL Recommender corriendo en: %s", url)
    logger.info("API docs: %s/api/docs", url)
    if display_host != settings.host:
        from app.api.routes_qr import lan_ip

        logger.info("Desde el movil (misma Wi-Fi): http://%s:%s", lan_ip(), settings.port)
        logger.info("Codigos QR para el movil: %s/qr", url)
    logger.info("=" * 60)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    return 0


def mode_overlay(container: ServiceContainer) -> int:
    """Overlay in-game: levanta el servidor local (si no corre ya) y abre la
    ventana always-on-top con probabilidad + accion + compra.

    Pensado para usarse ANTES de entrar a partida. Si --mode app ya esta
    corriendo en otra consola, este modo solo abre la ventana.
    """
    import threading
    import urllib.request

    from overlay_window import OverlayWindow, acquire_single_instance

    if not acquire_single_instance():
        logger.warning("Ya hay un overlay abierto en esta sesion: no se abre otro.")
        return 0

    settings = container.settings
    display_host = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
    url = f"http://{display_host}:{settings.port}"

    def server_running() -> bool:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=2) as response:
                return response.status == 200
        except Exception:
            return False

    if server_running():
        logger.info("Servidor ya corriendo en %s: solo se abre el overlay.", url)
    else:
        ensure_seed_loaded(container.repo, settings)
        try:
            update_static_data(container.ddragon)
        except Exception as exc:
            logger.warning("Data Dragon sin actualizar (%s); se usa el cache local.", exc)
        import uvicorn
        from app.api.server import create_app

        config = uvicorn.Config(
            create_app(container), host=settings.host, port=settings.port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        threading.Thread(target=server.run, daemon=True).start()
        logger.info("Servidor local del overlay en %s", url)

    logger.info("Overlay abierto: arrastra para mover, doble clic compacta, cierra con la X.")
    logger.info("El juego debe estar en 'Pantalla completa (sin bordes)' o 'Ventana'.")

    OverlayWindow(url).run()
    return 0


# ------------------------------------------------------------------ main

MODES = {
    "app": mode_app,
    "ingest-history": mode_ingest_history,
    "ingest-ladder": mode_ingest_ladder,
    "train-champion": mode_train_champion,
    "ingest-timelines": mode_ingest_timelines,
    "live-check": mode_live_check,
    "overlay": mode_overlay,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Orquestador de LoL Recommender")
    parser.add_argument("--mode", choices=MODES.keys(), default="app")
    parser.add_argument(
        "--target-records", type=int, default=100_000,
        help="Filas/participantes objetivo para --mode ingest-ladder.",
    )
    parser.add_argument(
        "--target-matches", type=int, default=None,
        help="Partidas nuevas objetivo para --mode ingest-ladder; si se omite se deriva de --target-records.",
    )
    parser.add_argument(
        "--per-player", type=int, default=20,
        help="Partidas ranked recientes a pedir por jugador semilla en --mode ingest-ladder.",
    )
    parser.add_argument(
        "--max-players", type=int, default=1_200,
        help="Maximo de jugadores de liga a muestrear en --mode ingest-ladder.",
    )
    parser.add_argument(
        "--target-tier", default=None,
        help="Tier/liga: a muestrear en ingest-ladder o del modelo en train-champion "
             "(ej. GOLD, EMERALD, MASTER). Por defecto TARGET_TIER del .env.",
    )
    parser.add_argument(
        "--champion", default=None,
        help="Campeon para --mode train-champion; por defecto CHAMPION del .env.",
    )
    parser.add_argument(
        "--force-retrain", action="store_true",
        help="Reentrena el modelo del campeon aunque el parche actual ya este cubierto.",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Ventana temporal en dias para --mode ingest-ladder.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info("Modo: %s | Jugador: %s#%s | Plataforma: %s/%s",
                args.mode, settings.game_name or "?", settings.tag_line or "?",
                settings.platform_routing, settings.regional_routing)

    container = ServiceContainer(settings)
    try:
        if args.mode == "train-champion":
            return mode_train_champion(
                container,
                champion=args.champion,
                target_tier=args.target_tier,
                force=args.force_retrain,
            )
        if args.mode == "ingest-timelines":
            return mode_ingest_timelines(
                container,
                champion=args.champion,
                max_matches=args.target_matches,
            )
        if args.mode == "ingest-ladder":
            return mode_ingest_ladder(
                container,
                target_matches=max(1, args.target_matches) if args.target_matches else None,
                target_records=max(10, args.target_records),
                per_player=max(1, min(args.per_player, 100)),
                max_players=max(1, args.max_players),
                target_tier=args.target_tier,
                days=max(1, args.days),
            )
        return MODES[args.mode](container)
    finally:
        # app: uvicorn es dueno del ciclo de vida. overlay: el servidor vive
        # en un hilo daemon; cerrar la DB aqui provocaria un traceback de
        # "closed database" en el refresh en curso (el proceso muere igual).
        if args.mode not in ("app", "overlay"):
            container.close()


if __name__ == "__main__":
    sys.exit(main())

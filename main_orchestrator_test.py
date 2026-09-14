"""Orquestador de PRUEBAS: ejercita toda la app SIN el cliente de LoL abierto.

Pensado para validar el sistema con cualquier cuenta (via Riot API) antes
de usarlo con una cuenta personal. Nunca toca al Live Client real; inyecta
snapshots simulados claramente etiquetados como MODO PRUEBA.

Fuentes de simulacion:
  spectator  Partida ACTIVA real del jugador configurado (Spectator-V5).
             Composicion real; sin kills/oro/items (Riot no los expone
             fuera del cliente); roles inferidos heuristicamente.
  replay     Re-simula una partida historica de la DB local al minuto N.
             100% offline una vez ingestado el historial.

Uso:
  python main_orchestrator_test.py                          # auto: spectator si hay partida, si no replay
  python main_orchestrator_test.py --source spectator
  python main_orchestrator_test.py --source replay --minute 22
  python main_orchestrator_test.py --source replay --match-id EUN1_1234567890
  python main_orchestrator_test.py --serve                  # dashboard completo en el navegador
  python main_orchestrator_test.py --serve --port 8001
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import webbrowser


logger = logging.getLogger("orchestrator.test")


# ------------------------------------------------------------- providers

def make_spectator_provider(container):
    """Provider que refetchea la partida activa en cada refresh."""
    from app.pipelines.simulate_live import snapshot_from_spectator
    from app.riot.riot_client import RiotApiError

    account = container.resolve_account()
    if account is None:
        logger.error("No se pudo resolver la cuenta: %s", container.account_error)
        return None
    try:
        active = container.riot_client.get_active_game(account["puuid"])
    except RiotApiError as exc:
        logger.error("Spectator-V5 fallo: %s", exc)
        return None
    if active is None:
        logger.warning("El jugador %s no esta en partida ahora mismo.", container.my_riot_id())
        return None

    def provider():
        try:
            game = container.riot_client.get_active_game(account["puuid"])
        except RiotApiError:
            game = None
        if game is None:
            return None
        return snapshot_from_spectator(game, container.repo, container.ddragon, account["puuid"])

    return provider


def make_replay_provider(container, match_id: str | None, minute: float):
    from app.pipelines.simulate_live import snapshot_from_replay

    puuid = container.my_puuid()
    snapshot = snapshot_from_replay(
        container.repo, container.ddragon, my_puuid=puuid, match_id=match_id, minute=minute
    )
    if snapshot is None:
        return None
    logger.info(
        "Replay: partida %s al minuto %.0f (jugando como %s con %s).",
        snapshot.get("replay_match_id"), minute,
        snapshot["me"]["riot_id"], snapshot["me"]["champion"],
    )
    return lambda: snapshot


# --------------------------------------------------------------- reporte

def print_report(container) -> None:
    """Reporte de una pasada por consola: snapshot, ML y recomendaciones."""
    snapshot, recommendations = container.refresh_live_state()
    if snapshot is None:
        print("\nNo se pudo construir un snapshot simulado.")
        return

    line = "=" * 70
    print(f"\n{line}\nMODO PRUEBA - snapshot simulado ({snapshot['simulated']})\n{line}")
    for warning in snapshot.get("warnings", []):
        print(f"  [!] {warning}")

    me, rival = snapshot["me"], snapshot.get("direct_rival")
    minutes = (snapshot.get("game_time_seconds") or 0) / 60
    print(f"\nMinuto {minutes:.0f} | {me['riot_id']} con {me['champion']} ({me.get('position') or 'rol ?'})")
    print(f"Rival directo: {rival['champion'] + ' (' + rival['riot_id'] + ')' if rival else 'no identificado'}")
    mix = snapshot.get("enemy_damage_mix")
    if mix:
        print(f"Danio enemigo estimado: {mix['physical']:.0%} fisico / {mix['magic']:.0%} magico")

    print("\n-- Equipos --")
    for label, group in (("Aliados", [me] + snapshot["allies"]), ("Enemigos", snapshot["enemies"])):
        rows = ", ".join(
            f"{p['champion']}[{(p.get('position') or '?')[:3]}]"
            + (f" {p['kills']}/{p['deaths']}/{p['assists']}" if snapshot["live_signals_available"] else "")
            for p in group
        )
        print(f"  {label}: {rows}")

    wp = (recommendations or {}).get("win_probability") or {}
    print("\n-- Probabilidad de victoria --")
    if wp.get("probability") is not None:
        print(f"  {wp['probability']:.0%} (confianza {wp.get('confidence')}, {wp.get('method')})")
        for factor in wp.get("top_factors", [])[:4]:
            name = factor.get("name") or factor.get("feature")
            print(f"    - {name}: {factor.get('contribution'):+.3f}")
    else:
        print("  no disponible")

    similarity = (recommendations or {}).get("similarity") or {}
    if similarity.get("sample_size"):
        print(
            f"\n-- Similitud historica --\n  {similarity['sample_size']} partidas "
            f"(nivel {similarity['level']}: {similarity['level_label']}, "
            f"confianza {similarity['confidence']})"
        )

    for section, title in (("items", "Items"), ("ganks", "Lineas para gank"), ("objectives", "Objetivos")):
        recs = (recommendations or {}).get(section, [])
        print(f"\n-- {title} --")
        for rec in recs:
            print(f"  * {rec['title']} [{rec['confidence']}]")
            print(f"    {rec['explanation']}")

    print("\n-- Chatbot (preguntas de ejemplo) --")
    for question in (
        "que item debo comprar ahora?",
        "que linea debo priorizar para gank?",
        "cual es mi probabilidad de ganar?",
    ):
        answer = container.chat.answer(question)
        first_line = answer["answer"].split("\n")[0]
        print(f"  Q: {question}\n  A: {first_line}")
    print(f"\n{line}\nFin del reporte de prueba. Nada de esto usa el Live Client.\n{line}")


# ------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Orquestador de pruebas (sin cliente de LoL)")
    parser.add_argument("--source", choices=["auto", "spectator", "replay"], default="auto")
    parser.add_argument("--match-id", default=None, help="Partida a re-simular (replay)")
    parser.add_argument("--minute", type=float, default=15.0, help="Minuto simulado (replay)")
    parser.add_argument("--serve", action="store_true", help="Levantar el dashboard web con el snapshot simulado")
    parser.add_argument("--port", type=int, default=None, help="Puerto para --serve")
    args = parser.parse_args(argv)

    if args.port:
        os.environ["APP_PORT"] = str(args.port)

    from app.config import get_settings
    from app.container import ServiceContainer
    from app.logging_config import setup_logging
    from app.pipelines.ingest_history import ensure_seed_loaded
    from app.pipelines.update_static_data import update_static_data

    settings = get_settings()
    setup_logging(settings.log_level)
    container = ServiceContainer(settings)
    ensure_seed_loaded(container.repo, settings)
    update_static_data(container.ddragon)

    provider = None
    if args.source in ("auto", "spectator"):
        provider = make_spectator_provider(container)
        if provider is None and args.source == "spectator":
            logger.error("Sin partida activa via Spectator-V5. Prueba --source replay.")
            return 1
    if provider is None:
        logger.info("Usando replay de una partida historica local.")
        provider = make_replay_provider(container, args.match_id, args.minute)
    if provider is None:
        logger.error(
            "No hay datos para simular: ingesta historial primero "
            "(python main_orchestrator.py --mode ingest-history)."
        )
        return 1

    container.snapshot_provider = provider

    if args.serve:
        import uvicorn
        from app.api.server import create_app

        app = create_app(container)
        url = f"http://{settings.host}:{settings.port}"
        logger.info("=" * 60)
        logger.info("DASHBOARD EN MODO PRUEBA: %s (snapshot simulado)", url)
        logger.info("=" * 60)
        try:
            webbrowser.open(url)
        except Exception:
            pass
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
        return 0

    print_report(container)
    container.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

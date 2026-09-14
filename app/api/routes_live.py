"""Rutas de partida en vivo: estado y datos del juego actual."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.container import ServiceContainer
from app.data.schemas import LiveGame, LivePlayer, LiveStatus
from app.riot.riot_client import RiotApiError
from app.security import secrets

logger = logging.getLogger(__name__)
router = APIRouter(tags=["live"])


def _container(request: Request) -> ServiceContainer:
    return request.app.state.container


@router.get("/live/status", response_model=LiveStatus)
def live_status(request: Request, force: bool = False) -> LiveStatus:
    c = _container(request)
    snapshot, _ = c.live_state(max_age=0 if force else None)
    diagnostics = c.live_client_diagnostics()
    simulated_source = (snapshot or {}).get("simulated")
    snapshot_available = snapshot is not None
    live_available = snapshot_available and simulated_source is None

    spectator_active = simulated_source == "spectator"
    if not snapshot_available and secrets.has_riot_api_key():
        # Distinguir "estas en partida pero el Live Client aun no responde"
        # de "no estas en partida": Spectator-V5.
        account = c.resolve_account()
        if account:
            try:
                spectator_active = c.riot_client.get_active_game(account["puuid"]) is not None
            except RiotApiError as exc:
                logger.warning("Spectator no disponible: %s", exc)

    if simulated_source:
        data_source = (snapshot or {}).get("data_source", "historico")
        if simulated_source == "replay":
            message = (
                "Modo prueba: replay historico simulado. No se esta usando "
                "Live Client Data API."
            )
        else:
            diag_message = (diagnostics or {}).get("message")
            message = (
                "Partida activa detectada via Spectator-V5 (juego en otra "
                "maquina, juego cargando o puerto local 2999 no accesible). "
                "Composicion real, sin senales detalladas del Live Client."
            )
            if diag_message:
                message = f"{message} Diagnostico local: {diag_message}"
    elif live_available:
        data_source = "live_client"
        message = "Partida en vivo detectada via Live Client Data API."
    elif spectator_active:
        data_source = "partida_activa"
        diag_message = (diagnostics or {}).get("message")
        message = (
            "Riot reporta una partida activa (Spectator-V5), pero el Live Client "
            "local aun no responde. Se activara al cargar el juego."
        )
        if diag_message:
            message = f"{message} Diagnostico local: {diag_message}"
    else:
        data_source = "partida_activa"
        if diagnostics and not diagnostics.get("available"):
            message = f"No hay partida activa local. Diagnostico Live Client: {diagnostics.get('message')}"
        else:
            message = "No hay partida activa."

    return LiveStatus(
        live_client_available=live_available,
        spectator_active_game=spectator_active,
        in_game=snapshot_available or spectator_active,
        game_time_seconds=(snapshot or {}).get("game_time_seconds"),
        game_mode=(snapshot or {}).get("game_mode"),
        refresh_seconds=c.settings.refresh_seconds,
        message=message,
        data_source=data_source,
        live_client_diagnostics=diagnostics,
    )


def _to_live_player(raw: dict | None) -> LivePlayer | None:
    if not raw:
        return None
    return LivePlayer(
        riot_id=raw.get("riot_id", "?"),
        champion=raw.get("champion", "?"),
        champion_image_url=raw.get("champion_image_url"),
        team=raw.get("team", "?"),
        position=raw.get("position"),
        level=raw.get("level"),
        kills=raw.get("kills", 0),
        deaths=raw.get("deaths", 0),
        assists=raw.get("assists", 0),
        creep_score=raw.get("creep_score"),
        is_dead=raw.get("is_dead", False),
        respawn_timer=raw.get("respawn_timer"),
        items=raw.get("items", []),
        runes=raw.get("runes"),
        damage_profile=raw.get("damage_profile"),
    )


@router.get("/live/game", response_model=LiveGame)
def live_game(request: Request) -> LiveGame:
    c = _container(request)
    snapshot, _ = c.live_state()
    diagnostics = c.live_client_diagnostics()
    if snapshot is None:
        return LiveGame(
            in_game=False,
            message=(
                "Live Client Data API no disponible. Solo funciona con el juego "
                "abierto en esta maquina."
            ),
            live_client_diagnostics=diagnostics,
        )
    return LiveGame(
        in_game=True,
        game_time_seconds=snapshot.get("game_time_seconds"),
        me=_to_live_player(snapshot.get("me")),
        direct_rival=_to_live_player(snapshot.get("direct_rival")),
        allies=[_to_live_player(p) for p in snapshot.get("allies", [])],
        enemies=[_to_live_player(p) for p in snapshot.get("enemies", [])],
        enemy_damage_mix=snapshot.get("enemy_damage_mix"),
        events_summary=snapshot.get("events_summary"),
        data_source=snapshot.get("data_source", "live_client"),
        live_signals_available=snapshot.get("live_signals_available", False),
        active_player=snapshot.get("active_player"),
        live_client_diagnostics=snapshot.get("live_client_diagnostics") or diagnostics,
        message=None,
    )

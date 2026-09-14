"""Rutas del jugador: perfil e historial."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.analytics.player_profile import build_local_profile
from app.container import ServiceContainer
from app.data.schemas import MatchHistoryItem, PlayerAnalytics, PlayerProfile, RankedEntry
from app.riot.riot_client import RiotApiError
from app.security import secrets

logger = logging.getLogger(__name__)
router = APIRouter(tags=["player"])


def _container(request: Request) -> ServiceContainer:
    return request.app.state.container


@router.get("/player/profile", response_model=PlayerProfile)
def player_profile(request: Request) -> PlayerProfile:
    c = _container(request)
    warnings: list[str] = []
    account = c.resolve_account()
    puuid = c.my_puuid()

    summoner_level = None
    icon_url = None
    ranked_entries: list[RankedEntry] = []

    if account and secrets.has_riot_api_key():
        try:
            summoner = c.riot_client.get_summoner_by_puuid(account["puuid"]) or {}
            summoner_level = summoner.get("summonerLevel")
            if summoner.get("profileIconId") is not None:
                icon_url = c.ddragon.profile_icon_url(summoner["profileIconId"])
            entries = c.riot_client.get_league_entries_by_puuid(account["puuid"])
            ranked_entries = [
                RankedEntry(
                    queue_type=e.get("queueType", "?"),
                    tier=e.get("tier"),
                    rank=e.get("rank"),
                    league_points=e.get("leaguePoints", 0),
                    wins=e.get("wins", 0),
                    losses=e.get("losses", 0),
                )
                for e in entries
            ]
        except RiotApiError as exc:
            warnings.append(f"Perfil ranked no disponible: {exc}")
    else:
        warnings.append(
            c.account_error or "Sin API key: mostrando solo datos historicos locales."
        )

    local = {}
    if puuid:
        player_df = c.repo.player_participants_df(puuid)
        local = build_local_profile(player_df, c.ddragon)
        warnings.extend(local.get("warnings", []))
    else:
        warnings.append(
            "No se encontro tu PUUID (ni via API ni en el historial local). "
            "Revisa GAME_NAME/TAG_LINE y ejecuta la ingesta."
        )

    recent = local.get("recent_form", {}) or {}
    return PlayerProfile(
        riot_id=c.my_riot_id(),
        puuid=puuid,
        summoner_level=summoner_level,
        profile_icon_url=icon_url,
        ranked_entries=ranked_entries,
        top_champions=local.get("top_champions", []),
        main_role=local.get("main_role_label"),
        recent_winrate=recent.get("winrate"),
        recent_games=recent.get("games", 0),
        style=local.get("style"),
        data_source="mixto" if account else "historico",
        warnings=warnings,
    )


@router.get("/player/lookup", response_model=PlayerAnalytics)
def player_lookup(request: Request, riot_id: str) -> PlayerAnalytics:
    """Analiticas de cualquier jugador por Riot ID (estilo porofessor).

    No requiere que el jugador este jugando en esta maquina: con API key
    descarga sus partidas recientes de la Riot API; sin key, busca lo que
    haya de el en el historial local.
    """
    c = _container(request)
    riot_id = riot_id.strip()
    if "#" not in riot_id:
        return PlayerAnalytics(
            available=False,
            reason="Formato esperado: Nombre#TAG (ej. Faker#KR1).",
        )
    game_name, tag_line = (part.strip() for part in riot_id.split("#", 1))

    if secrets.has_riot_api_key():
        profile = c.scout.profile(game_name, tag_line, with_league=True)
        if not profile.get("available") and not profile.get("puuid"):
            return PlayerAnalytics(
                available=False,
                reason=profile.get("reason", "Jugador no encontrado."),
            )
        return PlayerAnalytics(
            available=profile.get("available", False),
            reason=None if profile.get("available") else (
                "Cuenta encontrada"
                + (f" en {profile['platform']}" if profile.get("platform") else "")
                + ", pero sin partidas recientes descargables en ninguna cola "
                "(ranked, flex o normales)."
            ),
            riot_id=profile.get("riot_id"),
            puuid=profile.get("puuid"),
            summoner_level=profile.get("summoner_level"),
            profile_icon_url=profile.get("profile_icon_url"),
            ranked_entries=profile.get("ranked_entries", []),
            games=profile.get("games", 0),
            top_champions=profile.get("top_champions", []),
            main_role=profile.get("main_role_label"),
            recent_form=profile.get("recent_form"),
            style=profile.get("style"),
            data_source="mixto",
            warnings=profile.get("warnings", []),
        )

    # Sin API key: solo lo que exista en el historial local.
    puuid = c.puuid_from_local_history(game_name, tag_line)
    if not puuid:
        return PlayerAnalytics(
            available=False,
            reason=(
                "Sin RIOT_API_KEY solo puedo buscar en el historial local, y "
                f"{riot_id} no aparece en el. Configura la key en .env para "
                "consultar cualquier jugador."
            ),
        )
    local = build_local_profile(c.repo.player_participants_df(puuid), c.ddragon)
    return PlayerAnalytics(
        available=local.get("games", 0) > 0,
        riot_id=riot_id,
        puuid=puuid,
        games=local.get("games", 0),
        top_champions=local.get("top_champions", []),
        main_role=local.get("main_role_label"),
        recent_form=local.get("recent_form"),
        style=local.get("style"),
        data_source="historico",
        warnings=local.get("warnings", [])
        + ["Sin API key: datos limitados al historial local."],
    )


@router.get("/player/history", response_model=list[MatchHistoryItem])
def player_history(request: Request, limit: int = 20) -> list[MatchHistoryItem]:
    c = _container(request)
    puuid = c.my_puuid()
    if not puuid:
        return []
    from app.analytics.matchup_analysis import with_opponents

    player_df = c.repo.player_participants_df(puuid)
    if player_df.empty:
        return []
    full = with_opponents(c.repo.participants_df())
    mine = full[full["puuid"] == puuid].sort_values("gameCreation", ascending=False).head(limit)

    items = []
    for _, row in mine.iterrows():
        items.append(MatchHistoryItem(
            match_id=row["matchId"],
            champion=row["championName"],
            champion_image_url=c.ddragon.champion_image_url(row["championName"]),
            role=row.get("teamPosition"),
            win=bool(row["win"]),
            kills=int(row["kills"] or 0),
            deaths=int(row["deaths"] or 0),
            assists=int(row["assists"] or 0),
            kda=float(row["kda"] or 0),
            cs_per_min=_safe_float(row.get("csPerMin")),
            gold_per_min=_safe_float(row.get("goldPerMin")),
            damage_per_min=_safe_float(row.get("damagePerMin")),
            vision_per_min=_safe_float(row.get("visionPerMin")),
            kill_participation=_safe_float(row.get("killParticipation")),
            duration_min=_duration_min(row),
            game_creation=int(row["gameCreation"]) if row.get("gameCreation") else None,
            patch=row.get("patch"),
            opponent_champion=row.get("opponentChampionName"),
        ))
    return items


def _duration_min(row) -> float | None:
    # gameDurationSeconds ya viene normalizado (ms -> s) por enrich_participants;
    # gameDuration crudo queda como respaldo para DataFrames no enriquecidos.
    seconds = _safe_float(row.get("gameDurationSeconds"))
    if seconds is None:
        seconds = _safe_float(row.get("gameDuration"))
        if seconds is not None and seconds > 20000:
            seconds /= 1000
    return round(seconds / 60, 1) if seconds else None


def _safe_float(value) -> float | None:
    try:
        import math
        f = float(value)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None

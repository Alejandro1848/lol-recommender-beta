"""Ingesta de timelines (Match-V5 /timeline) para el modelo in-game.

Descarga la linea de tiempo minuto a minuto de las partidas del campeon y
la reduce a una fila por minuto con SOLO las senales que tambien estan
disponibles en vivo via Live Client Data API (kills, niveles, CS, torres,
dragones, barones por equipo). Asi el modelo in-game se entrena con
exactamente las mismas features que tendra al predecir en una partida real
(el oro enemigo, por ejemplo, NO se usa porque el Live Client no lo expone).

1 llamada por partida; reanudable (salta partidas que ya tienen timeline).
Usar via main_orchestrator.py --mode ingest-timelines.
"""
from __future__ import annotations

import logging

import pandas as pd

from app.config import Settings
from app.data.repositories import MatchRepository
from app.riot.riot_client import RiotApiError, RiotClient

logger = logging.getLogger(__name__)

FLUSH_EVERY_MATCHES = 50
TOWER_BUILDING = "TOWER_BUILDING"


def timeline_to_minute_rows(match_id: str, timeline: dict) -> list[dict]:
    """Reduce el JSON crudo del timeline a una fila acumulada por minuto."""
    frames = ((timeline or {}).get("info") or {}).get("frames") or []
    if not frames:
        return []
    kills = {100: 0, 200: 0}
    turrets = {100: 0, 200: 0}
    dragons = {100: 0, 200: 0}
    barons = {100: 0, 200: 0}
    rows = []
    for frame in frames:
        for event in frame.get("events") or []:
            etype = event.get("type")
            if etype == "CHAMPION_KILL":
                victim = int(event.get("victimId") or 0)
                if 1 <= victim <= 5:
                    kills[200] += 1
                elif 6 <= victim <= 10:
                    kills[100] += 1
            elif etype == "BUILDING_KILL" and event.get("buildingType") == TOWER_BUILDING:
                lost_by = int(event.get("teamId") or 0)  # equipo que PIERDE la torre
                if lost_by == 100:
                    turrets[200] += 1
                elif lost_by == 200:
                    turrets[100] += 1
            elif etype == "ELITE_MONSTER_KILL":
                team = int(event.get("killerTeamId") or 0)
                if team not in (100, 200):
                    continue
                monster = str(event.get("monsterType") or "")
                if monster == "DRAGON":
                    dragons[team] += 1
                elif monster == "BARON_NASHOR":
                    barons[team] += 1

        level = {100: 0, 200: 0}
        cs = {100: 0, 200: 0}
        for pid_str, pframe in (frame.get("participantFrames") or {}).items():
            try:
                pid = int(pid_str)
            except (TypeError, ValueError):
                continue
            team = 100 if pid <= 5 else 200
            level[team] += int(pframe.get("level") or 0)
            cs[team] += int(pframe.get("minionsKilled") or 0) + int(pframe.get("jungleMinionsKilled") or 0)

        minute = int(round((frame.get("timestamp") or 0) / 60000))
        rows.append({
            "matchId": match_id, "minute": minute,
            "kills_100": kills[100], "kills_200": kills[200],
            "level_100": level[100], "level_200": level[200],
            "cs_100": cs[100], "cs_200": cs[200],
            "turrets_100": turrets[100], "turrets_200": turrets[200],
            "dragons_100": dragons[100], "dragons_200": dragons[200],
            "barons_100": barons[100], "barons_200": barons[200],
        })
    return rows


def ingest_timelines(
    settings: Settings,
    repo: MatchRepository,
    client: RiotClient,
    champion: str,
    max_matches: int = 3000,
) -> dict:
    """Descarga timelines de las partidas mas recientes del campeon."""
    participants = repo.participants_df(enriched=False)
    if participants.empty:
        raise ValueError("No hay partidas en la DB; corre primero ingest-ladder.")
    champ_rows = participants[participants["championName"] == champion]
    if champ_rows.empty:
        raise ValueError(f"No hay filas de {champion} en la DB.")
    ordered = (
        champ_rows[["matchId", "gameCreation"]]
        .drop_duplicates("matchId")
        .sort_values("gameCreation", ascending=False)
        .head(max(1, int(max_matches)))
    )
    done = repo.matches_with_timeline()
    todo = [m for m in ordered["matchId"] if m not in done]

    stats = {
        "champion": champion,
        "matches_target": int(len(ordered)),
        "matches_already_done": int(len(ordered)) - len(todo),
        "matches_downloaded": 0,
        "matches_failed": 0,
        "api_calls": 0,
    }
    pending: list[dict] = []

    def flush() -> None:
        nonlocal pending
        if pending:
            repo.upsert_timeline_minutes(pd.DataFrame(pending))
            pending = []

    for index, match_id in enumerate(todo, start=1):
        try:
            timeline = client.get_match_timeline(match_id)
            stats["api_calls"] += 1
        except RiotApiError as exc:
            if getattr(exc, "status_code", None) in {401, 403}:
                stats["aborted"] = str(exc)
                logger.error("Ingesta de timelines abortada: %s", exc)
                break
            stats["matches_failed"] += 1
            logger.warning("Timeline de %s omitida: %s", match_id, exc)
            continue
        rows = timeline_to_minute_rows(match_id, timeline) if timeline else []
        if rows:
            pending.extend(rows)
            stats["matches_downloaded"] += 1
        else:
            stats["matches_failed"] += 1
        if index % FLUSH_EVERY_MATCHES == 0:
            flush()
            logger.info(
                "Progreso timelines: %s/%s descargadas (%s fallidas).",
                stats["matches_downloaded"], len(todo), stats["matches_failed"],
            )

    flush()
    stats["matches_with_timeline_in_db"] = len(repo.matches_with_timeline())
    return stats

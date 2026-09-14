"""Ingesta masiva desde jugadores de una liga similar.

El objetivo es ampliar el dataset local con partidas ranked de jugadores del
mismo tier que la cuenta principal. Esto mejora dos cosas:
- el modelo de probabilidad tiene muchas mas composiciones historicas;
- las builds pueden aprender de buenos desempenos, incluso en derrotas.

No se ejecuta en el arranque normal porque 10,000 partidas implican muchas
llamadas a Riot API. Usar via main_orchestrator.py --mode ingest-ladder.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil

from app.config import Settings
from app.data.normalizers import flatten_matches
from app.data.repositories import MatchRepository
from app.pipelines.ingest_history import resolve_account
from app.riot.riot_client import RiotApiError, RiotClient

logger = logging.getLogger(__name__)

QUEUE_BY_ID = {
    420: "RANKED_SOLO_5x5",
    440: "RANKED_FLEX_SR",
}

TIER_ORDER = [
    "IRON",
    "BRONZE",
    "SILVER",
    "GOLD",
    "PLATINUM",
    "EMERALD",
    "DIAMOND",
    "MASTER",
    "GRANDMASTER",
    "CHALLENGER",
]
DIVISIONS = ["I", "II", "III", "IV"]
APEX_TIERS = {"MASTER", "GRANDMASTER", "CHALLENGER"}


@dataclass
class LadderSeed:
    puuid: str
    tier: str
    division: str | None
    wins: int
    losses: int
    league_points: int

    @property
    def games(self) -> int:
        return self.wins + self.losses

    @property
    def winrate(self) -> float:
        return self.wins / self.games if self.games else 0.0

    @property
    def score(self) -> float:
        games_bonus = min(self.games, 200) / 200
        return self.winrate * 100 + self.league_points * 0.05 + games_bonus * 10


def _queue_type(queue_id: int) -> str:
    return QUEUE_BY_ID.get(queue_id, "RANKED_SOLO_5x5")


def _entry_tier_for_player(client: RiotClient, settings: Settings, platform: str | None) -> str:
    account = resolve_account(client, settings)
    entries = client.get_league_entries_by_puuid(account["puuid"], platform=platform)
    queue = _queue_type(settings.queue_id)
    ranked = [e for e in entries if e.get("queueType") == queue]
    if not ranked:
        logger.warning(
            "La cuenta principal no tiene entrada %s; se usara GOLD como punto de partida.",
            queue,
        )
        return "GOLD"
    ranked.sort(
        key=lambda e: TIER_ORDER.index(str(e.get("tier") or "IRON").upper())
        if str(e.get("tier") or "IRON").upper() in TIER_ORDER else 0,
        reverse=True,
    )
    return str(ranked[0].get("tier") or "GOLD").upper()


def _tier_window(tier: str) -> list[str]:
    if tier not in TIER_ORDER:
        return ["GOLD"]
    idx = TIER_ORDER.index(tier)
    tiers = [tier]
    if idx + 1 < len(TIER_ORDER):
        tiers.append(TIER_ORDER[idx + 1])
    if idx - 1 >= 0:
        tiers.append(TIER_ORDER[idx - 1])
    return tiers


def _entry_puuid(client: RiotClient, entry: dict, platform: str | None) -> str | None:
    puuid = entry.get("puuid")
    if puuid:
        return puuid
    summoner_id = entry.get("summonerId")
    if not summoner_id:
        return None
    summoner = client.get_summoner_by_id(summoner_id, platform=platform)
    return (summoner or {}).get("puuid")


def _seed_from_entry(client: RiotClient, entry: dict, tier: str, platform: str | None) -> LadderSeed | None:
    puuid = _entry_puuid(client, entry, platform)
    if not puuid:
        return None
    return LadderSeed(
        puuid=puuid,
        tier=tier,
        division=entry.get("rank"),
        wins=int(entry.get("wins") or 0),
        losses=int(entry.get("losses") or 0),
        league_points=int(entry.get("leaguePoints") or 0),
    )


def _collect_apex_seeds(
    client: RiotClient,
    tier: str,
    queue: str,
    platform: str | None,
    max_players: int,
) -> list[LadderSeed]:
    payload = client.get_apex_league(tier, queue=queue, platform=platform) or {}
    seeds = []
    for entry in payload.get("entries") or []:
        seed = _seed_from_entry(client, entry, tier, platform)
        if seed:
            seeds.append(seed)
    seeds.sort(key=lambda s: s.score, reverse=True)
    return seeds[:max_players]


def _collect_division_seeds(
    client: RiotClient,
    tier: str,
    queue: str,
    platform: str | None,
    max_players: int,
) -> list[LadderSeed]:
    seeds: list[LadderSeed] = []
    for division in DIVISIONS:
        page = 1
        while len(seeds) < max_players:
            entries = client.get_league_entries(
                tier, division, queue=queue, page=page, platform=platform
            )
            if not entries:
                break
            for entry in entries:
                seed = _seed_from_entry(client, entry, tier, platform)
                if seed:
                    seeds.append(seed)
            page += 1
    seeds.sort(key=lambda s: s.score, reverse=True)
    return seeds[:max_players]


def collect_ladder_seeds(
    client: RiotClient,
    settings: Settings,
    target_tier: str | None = None,
    platform: str | None = None,
    max_players: int = 700,
) -> list[LadderSeed]:
    queue = _queue_type(settings.queue_id)
    tier = (target_tier or _entry_tier_for_player(client, settings, platform)).upper()
    seeds: list[LadderSeed] = []
    seen: set[str] = set()
    for current_tier in _tier_window(tier):
        remaining = max_players - len(seeds)
        if remaining <= 0:
            break
        if current_tier in APEX_TIERS:
            batch = _collect_apex_seeds(client, current_tier, queue, platform, remaining)
        else:
            batch = _collect_division_seeds(client, current_tier, queue, platform, remaining)
        for seed in batch:
            if seed.puuid in seen:
                continue
            seen.add(seed.puuid)
            seeds.append(seed)
        logger.info(
            "Semillas de liga %s: %s acumuladas para cola %s.",
            current_tier, len(seeds), queue,
        )
    seeds.sort(key=lambda s: s.score, reverse=True)
    return seeds[:max_players]


def ingest_ladder_matches(
    settings: Settings,
    repo: MatchRepository,
    client: RiotClient,
    target_matches: int | None = None,
    target_records: int = 100_000,
    per_player: int = 20,
    max_players: int = 1_200,
    target_tier: str | None = None,
    days: int = 30,
) -> dict:
    """Descarga partidas ranked de jugadores de liga similar.

    target_records cuenta filas de participante objetivo. Como cada partida
    completa aporta 10 participantes (5 win / 5 loss), se transforma a partidas
    objetivo para mantener balance natural entre clases.

    Cada jugador semilla debe aportar exactamente `per_player` partidas ranked
    unicas dentro de los ultimos `days` dias; si no llega, se salta y se prueba
    el siguiente jugador de forma iterativa.
    """
    target_records = max(10, int(target_records))
    per_player = max(1, min(int(per_player), 100))
    if target_matches is None:
        target_matches = ceil(target_records / 10)
    target_matches = max(1, int(target_matches))
    target_matches = ceil(target_matches / per_player) * per_player
    days = max(1, int(days))
    now = datetime.now(timezone.utc)
    start_time = int((now - timedelta(days=days)).timestamp())
    end_time = int(now.timestamp())

    account = resolve_account(client, settings)
    platform = client.autoconfigure_routing_for(account["puuid"]) or client.current_platform
    seeds = collect_ladder_seeds(
        client,
        settings,
        target_tier=target_tier,
        platform=platform,
        max_players=max_players,
    )
    if not seeds:
        raise RiotApiError("No se encontraron jugadores de liga para muestrear.")

    known = repo.known_match_ids()
    selected_ids: list[str] = []
    seen_ids = set(known)
    players_used = 0
    players_skipped_insufficient = 0
    players_skipped_duplicates = 0
    for seed in seeds:
        if len(selected_ids) >= target_matches:
            break
        try:
            match_ids = client.get_recent_ranked_match_ids(
                seed.puuid,
                count=100,
                queue_id=settings.queue_id,
                start_time=start_time,
                end_time=end_time,
                platform=platform,
            )
        except RiotApiError as exc:
            logger.warning("Se omite jugador %s/%s: %s", seed.tier, seed.division, exc)
            continue

        if len(match_ids) < per_player:
            players_skipped_insufficient += 1
            continue

        unique_new_ids = []
        for match_id in match_ids:
            if match_id not in seen_ids:
                unique_new_ids.append(match_id)
            if len(unique_new_ids) >= per_player:
                break
        if len(unique_new_ids) < per_player:
            players_skipped_duplicates += 1
            continue

        players_used += 1
        for match_id in unique_new_ids[:per_player]:
            seen_ids.add(match_id)
            selected_ids.append(match_id)
        if players_used % 25 == 0:
            logger.info(
                "Progreso homogeneo: %s partidas desde %s jugadores validos "
                "(%s sin %s partidas/%sd, %s duplicados).",
                len(selected_ids), players_used, players_skipped_insufficient,
                per_player, days, players_skipped_duplicates,
            )

    raw_matches = []
    for index, match_id in enumerate(selected_ids, start=1):
        match = client.get_match(match_id, platform=platform)
        if match is not None:
            raw_matches.append(match)
        if index % 100 == 0:
            logger.info("Descargadas %s/%s partidas nuevas.", index, len(selected_ids))

    counts = {"matches": 0, "participants": 0, "teams": 0, "bans": 0}
    win_rows = 0
    loss_rows = 0
    if raw_matches:
        tables = flatten_matches(raw_matches)
        participants = tables.get("participants")
        if participants is not None and not participants.empty:
            win_rows = int(participants["win"].astype(bool).sum())
            loss_rows = int(len(participants) - win_rows)
        counts = repo.upsert_bundle(tables)

    tiers = sorted({seed.tier for seed in seeds})
    return {
        "target_records": target_records,
        "target_matches": target_matches,
        "days_window": days,
        "per_player_required_matches": per_player,
        "selected_match_ids": len(selected_ids),
        "estimated_selected_records": len(selected_ids) * 10,
        "downloaded_matches": len(raw_matches),
        "inserted_matches": counts.get("matches", 0),
        "inserted_participants": counts.get("participants", 0),
        "inserted_win_rows": win_rows,
        "inserted_loss_rows": loss_rows,
        "players_used": players_used,
        "players_skipped_insufficient_recent_matches": players_skipped_insufficient,
        "players_skipped_duplicate_overlap": players_skipped_duplicates,
        "players_available": len(seeds),
        "tiers_sampled": tiers,
        "start_time_utc": datetime.fromtimestamp(start_time, timezone.utc).isoformat(),
        "end_time_utc": datetime.fromtimestamp(end_time, timezone.utc).isoformat(),
        "total_matches_in_db": repo.match_count(),
    }

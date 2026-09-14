"""Pipeline de ingesta de historial (Match-V5) + arranque en frio con CSVs seed.

Idempotente: solo descarga partidas que no esten ya en la base local.
"""
from __future__ import annotations

import logging

from app.config import Settings
from app.data.loaders import load_seed_csvs
from app.data.normalizers import flatten_matches
from app.data.repositories import MatchRepository
from app.riot.riot_client import RiotApiError, RiotClient

logger = logging.getLogger(__name__)


def ensure_seed_loaded(repo: MatchRepository, settings: Settings) -> int:
    """Si la DB esta vacia, carga los CSV seed del proyecto Learning."""
    if repo.match_count() > 0:
        return 0
    tables = load_seed_csvs(settings.seed_data_dir)
    if not tables:
        logger.info("Sin CSVs seed disponibles; la DB inicia vacia.")
        return 0
    counts = repo.upsert_bundle(tables)
    return counts.get("matches", 0)


def resolve_account(client: RiotClient, settings: Settings) -> dict:
    if not settings.game_name or not settings.tag_line:
        raise ValueError(
            "GAME_NAME y TAG_LINE no estan configurados. Editalos en tu .env."
        )
    account = client.get_account_by_riot_id(settings.game_name, settings.tag_line)
    if account is None:
        raise RiotApiError(
            f"Riot ID {settings.game_name}#{settings.tag_line} no encontrado en "
            f"{settings.regional_routing}. Revisa GAME_NAME/TAG_LINE/REGIONAL_ROUTING."
        )
    # Ajusta el routing a la region real de la cuenta (puede diferir del .env).
    client.autoconfigure_routing_for(account["puuid"])
    return account


def ingest_history(
    settings: Settings, repo: MatchRepository, client: RiotClient
) -> dict:
    """Descarga las ultimas MATCH_COUNT partidas ranked y las guarda en la DB."""
    seeded = ensure_seed_loaded(repo, settings)

    account = resolve_account(client, settings)
    puuid = account["puuid"]

    match_ids = client.get_recent_ranked_match_ids(
        puuid, count=settings.match_count, queue_id=settings.queue_id
    )
    known = repo.known_match_ids()
    new_ids = [mid for mid in match_ids if mid not in known]
    logger.info(
        "Historial: %s ids obtenidos, %s nuevos por descargar.", len(match_ids), len(new_ids)
    )

    raw_matches = []
    for match_id in new_ids:
        match = client.get_match(match_id)
        if match is not None:
            raw_matches.append(match)

    counts = {"matches": 0}
    if raw_matches:
        counts = repo.upsert_bundle(flatten_matches(raw_matches))

    return {
        "riot_id": f"{account.get('gameName')}#{account.get('tagLine')}",
        "puuid": puuid,
        "seeded_matches": seeded,
        "fetched_ids": len(match_ids),
        "new_matches": counts.get("matches", 0),
        "total_matches_in_db": repo.match_count(),
    }

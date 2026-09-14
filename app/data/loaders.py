"""Carga de los CSV seed existentes (Learning/lan_ranked_match_sample)
hacia la base de datos local. Se usan como arranque en frio del historial."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

SEED_FILES = {
    "matches": "matches.csv",
    "participants": "match_participants.csv",
    "teams": "match_teams.csv",
    "bans": "match_team_bans.csv",
}


def load_seed_csvs(seed_dir: Path) -> dict[str, pd.DataFrame] | None:
    """Lee los CSV seed. Devuelve None si el directorio o archivos no existen."""
    seed_dir = Path(seed_dir)
    if not seed_dir.exists():
        logger.info("No hay directorio seed en %s; se omite.", seed_dir)
        return None

    tables: dict[str, pd.DataFrame] = {}
    for key, filename in SEED_FILES.items():
        path = seed_dir / filename
        if not path.exists():
            logger.warning("Seed incompleto: falta %s", path)
            return None
        try:
            tables[key] = pd.read_csv(path, low_memory=False)
        except (pd.errors.ParserError, OSError) as exc:
            logger.warning("No se pudo leer %s: %s", path, exc)
            return None

    logger.info(
        "Seed cargado: %s partidas, %s participantes.",
        len(tables["matches"]), len(tables["participants"]),
    )
    return tables

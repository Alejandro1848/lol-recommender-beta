"""Analisis de matchups (campeon vs campeon por rol) sobre el historial local."""
from __future__ import annotations

import pandas as pd

from app.data.normalizers import attach_direct_opponent


def with_opponents(participants: pd.DataFrame) -> pd.DataFrame:
    """Participantes con su rival directo adjunto (cacheable por el caller)."""
    if participants.empty:
        return participants
    return attach_direct_opponent(participants)


def matchup_winrate(
    participants_with_opp: pd.DataFrame,
    own_champion: str,
    opponent_champion: str | None = None,
    role: str | None = None,
    puuid: str | None = None,
) -> dict:
    """Winrate historico del matchup con el nivel de filtro pedido.

    Devuelve sample_size explicito: si es chico, las capas superiores
    deben degradar la confianza y decirlo.
    """
    df = participants_with_opp
    if df.empty:
        return {"games": 0, "wins": 0, "winrate": None}
    mask = df["championName"].str.lower() == own_champion.lower()
    if puuid:
        mask &= df["puuid"] == puuid
    if role:
        mask &= df["teamPosition"] == role
    if opponent_champion:
        mask &= df["opponentChampionName"].fillna("").str.lower() == opponent_champion.lower()
    subset = df[mask]
    games = int(len(subset))
    wins = int(subset["win"].sum()) if games else 0
    return {
        "games": games,
        "wins": wins,
        "winrate": round(wins / games, 3) if games else None,
    }

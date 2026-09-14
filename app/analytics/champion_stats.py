"""Estadisticas agregadas por campeon a partir del historial local."""
from __future__ import annotations

import pandas as pd


def champion_aggregates(participants: pd.DataFrame) -> pd.DataFrame:
    """Agrega winrate/KDA/metricas por campeon sobre el df dado."""
    if participants.empty:
        return pd.DataFrame()
    grouped = participants.groupby("championName").agg(
        games=("matchId", "nunique"),
        wins=("win", "sum"),
        kda=("kda", "mean"),
        cs_per_min=("csPerMin", "mean"),
        gold_per_min=("goldPerMin", "mean"),
        damage_per_min=("damagePerMin", "mean"),
        damage_share=("damageShare", "mean"),
        kill_participation=("killParticipation", "mean"),
    ).reset_index()
    grouped["winrate"] = (grouped["wins"] / grouped["games"]).round(3)
    return grouped.sort_values(["games", "winrate"], ascending=False)


def top_champions(participants: pd.DataFrame, puuid: str | None = None, n: int = 3) -> pd.DataFrame:
    df = participants
    if puuid:
        df = df[df["puuid"] == puuid]
    aggregates = champion_aggregates(df)
    return aggregates.head(n) if not aggregates.empty else aggregates


def games_with_champion(participants: pd.DataFrame, puuid: str, champion: str) -> dict:
    """Cuantas partidas (en la muestra local) jugo `puuid` con `champion`."""
    df = participants[(participants["puuid"] == puuid)]
    subset = df[df["championName"].str.lower() == champion.lower()] if not df.empty else df
    return {
        "games": int(len(subset)),
        "wins": int(subset["win"].sum()) if not subset.empty else 0,
        "sample_total": int(len(df)),
    }

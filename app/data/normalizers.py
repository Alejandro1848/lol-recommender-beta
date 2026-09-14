"""Normalizacion de datos crudos de Match-V5 a tablas planas y tipadas.

Refactor de flatten_matches() del script original, con:
- subconjunto curado de columnas de participante (lo que usan analytics y ML),
- derivadas consistentes (kda, cs/min, gold/min, dmg/min, shares),
- normalizacion de roles a los 5 canonicos.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ROLES = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]

ROLE_LABELS_ES = {
    "TOP": "Top",
    "JUNGLE": "Jungla",
    "MIDDLE": "Mid",
    "BOTTOM": "ADC",
    "UTILITY": "Soporte",
}

PARTICIPANT_COLUMNS = [
    "matchId", "puuid", "riotIdGameName", "riotIdTagline", "summonerName",
    "championId", "championName", "teamId", "teamPosition", "individualPosition",
    "win", "kills", "deaths", "assists", "champLevel",
    "goldEarned", "goldSpent", "totalMinionsKilled", "neutralMinionsKilled",
    "totalDamageDealtToChampions", "physicalDamageDealtToChampions",
    "magicDamageDealtToChampions", "trueDamageDealtToChampions",
    "totalDamageTaken", "damageDealtToObjectives", "visionScore", "wardsPlaced",
    "turretTakedowns", "dragonKills", "baronKills", "killingSprees",
    "largestMultiKill", "totalTimeSpentDead",
    "item0", "item1", "item2", "item3", "item4", "item5", "item6",
    "gameCreation", "gameDuration", "queueId", "platformId", "gameVersion",
]

MATCH_COLUMNS = [
    "matchId", "dataVersion", "gameId", "gameCreation", "gameStartTimestamp",
    "gameEndTimestamp", "gameDuration", "gameMode", "gameType", "gameVersion",
    "mapId", "queueId", "platformId",
]

TEAM_COLUMNS = [
    "matchId", "teamId", "win", "baron_kills", "champion_kills", "dragon_kills",
    "horde_kills", "inhibitor_kills", "riftHerald_kills", "tower_kills",
]

BAN_COLUMNS = ["matchId", "teamId", "championId", "pickTurn"]


def normalize_role(value) -> str | None:
    """Mapea teamPosition/individualPosition/Live Client position a rol canonico."""
    if not isinstance(value, str) or not value.strip():
        return None
    role = value.strip().upper()
    aliases = {
        "MID": "MIDDLE", "BOT": "BOTTOM", "ADC": "BOTTOM",
        "SUPPORT": "UTILITY", "SUP": "UTILITY", "NONE": None, "": None,
    }
    role = aliases.get(role, role)
    return role if role in ROLES else None


def flatten_matches(matches: list[dict]) -> dict[str, pd.DataFrame]:
    """Convierte JSON crudos de Match-V5 en 4 DataFrames planos."""
    match_rows, participant_rows, team_rows, ban_rows = [], [], [], []

    for match in matches:
        if not match:
            continue
        metadata = match.get("metadata", {})
        info = match.get("info", {})
        match_id = metadata.get("matchId")

        match_rows.append({
            "matchId": match_id,
            "dataVersion": metadata.get("dataVersion"),
            **{col: info.get(col) for col in MATCH_COLUMNS if col not in ("matchId", "dataVersion")},
        })

        for participant in info.get("participants", []):
            row = {col: participant.get(col) for col in PARTICIPANT_COLUMNS}
            row["matchId"] = match_id
            row["gameCreation"] = info.get("gameCreation")
            row["gameDuration"] = info.get("gameDuration")
            row["queueId"] = info.get("queueId")
            row["platformId"] = info.get("platformId")
            row["gameVersion"] = info.get("gameVersion")
            participant_rows.append(row)

        for team in info.get("teams", []):
            objectives = team.get("objectives", {})
            team_rows.append({
                "matchId": match_id,
                "teamId": team.get("teamId"),
                "win": team.get("win"),
                "baron_kills": objectives.get("baron", {}).get("kills"),
                "champion_kills": objectives.get("champion", {}).get("kills"),
                "dragon_kills": objectives.get("dragon", {}).get("kills"),
                "horde_kills": (
                    objectives.get("horde", {}).get("kills")
                    if objectives.get("horde") is not None
                    else objectives.get("voidGrub", {}).get("kills")
                ),
                "inhibitor_kills": objectives.get("inhibitor", {}).get("kills"),
                "riftHerald_kills": objectives.get("riftHerald", {}).get("kills"),
                "tower_kills": objectives.get("tower", {}).get("kills"),
            })
            for ban in team.get("bans", []):
                ban_rows.append({
                    "matchId": match_id,
                    "teamId": team.get("teamId"),
                    "championId": ban.get("championId"),
                    "pickTurn": ban.get("pickTurn"),
                })

    return {
        "matches": pd.DataFrame(match_rows, columns=MATCH_COLUMNS),
        "participants": pd.DataFrame(participant_rows, columns=PARTICIPANT_COLUMNS),
        "teams": pd.DataFrame(team_rows, columns=TEAM_COLUMNS),
        "bans": pd.DataFrame(ban_rows, columns=BAN_COLUMNS),
    }


def coerce_participants(df: pd.DataFrame) -> pd.DataFrame:
    """Alinea un DataFrame externo (CSV seed o flatten) al esquema curado."""
    out = df.copy()
    for col in PARTICIPANT_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    out = out[PARTICIPANT_COLUMNS]
    if not pd.api.types.is_numeric_dtype(out["win"]) and not pd.api.types.is_bool_dtype(out["win"]):
        out["win"] = (
            out["win"].astype(str).str.strip().str.lower().map({"true": 1, "false": 0, "1": 1, "0": 0})
        )
    out["win"] = pd.to_numeric(out["win"], errors="coerce")
    numeric_cols = [
        c for c in PARTICIPANT_COLUMNS
        if c not in ("matchId", "puuid", "riotIdGameName", "riotIdTagline", "summonerName",
                     "championName", "teamPosition", "individualPosition", "platformId", "gameVersion")
    ]
    for col in numeric_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["teamPosition"] = out["teamPosition"].map(normalize_role)
    return out


def enrich_participants(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega metricas derivadas estandar. Espera un df ya coercionado."""
    out = df.copy()
    duration = pd.to_numeric(out["gameDuration"], errors="coerce")
    # Match-V5 reporta gameDuration en MILISEGUNDOS en partidas antiguas
    # (las que no traen gameEndTimestamp). Ninguna partida real supera los
    # 20000 s (5.5 h): por encima de eso el valor es ms y se normaliza.
    duration = duration.where(duration <= 20000, duration / 1000)
    out["gameDurationSeconds"] = duration
    minutes = (duration.fillna(0) / 60).replace(0, np.nan)
    out["minutes"] = minutes
    # Remakes y rendiciones <5 min: los ratios por minuto salen absurdos
    # (p. ej. 2 CS en 3 min de base) y contaminan medias y modelos.
    rate_minutes = minutes.where(minutes >= 5)
    out["csTotal"] = out["totalMinionsKilled"].fillna(0) + out["neutralMinionsKilled"].fillna(0)
    out["csPerMin"] = (out["csTotal"] / rate_minutes).round(2)
    out["goldPerMin"] = (out["goldEarned"] / rate_minutes).round(1)
    out["damagePerMin"] = (out["totalDamageDealtToChampions"] / rate_minutes).round(1)
    out["visionPerMin"] = (out["visionScore"] / rate_minutes).round(2)
    out["kda"] = (
        (out["kills"].fillna(0) + out["assists"].fillna(0))
        / out["deaths"].fillna(0).replace(0, 1)
    ).round(2)
    out["patch"] = out["gameVersion"].astype(str).str.extract(r"^(\d+\.\d+)")[0]

    # Shares por equipo (danio infligido/recibido y participacion en kills)
    grouped = out.groupby(["matchId", "teamId"])
    team_damage = grouped["totalDamageDealtToChampions"].transform("sum").replace(0, np.nan)
    team_taken = grouped["totalDamageTaken"].transform("sum").replace(0, np.nan)
    team_kills = grouped["kills"].transform("sum").replace(0, np.nan)
    out["damageShare"] = (out["totalDamageDealtToChampions"] / team_damage).round(3)
    out["damageTakenShare"] = (out["totalDamageTaken"] / team_taken).round(3)
    out["killParticipation"] = (
        (out["kills"].fillna(0) + out["assists"].fillna(0)) / team_kills
    ).clip(upper=1.5).round(3)
    return out


def attach_direct_opponent(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega el rival directo (mismo matchId, mismo rol, otro equipo)."""
    out = df.copy()
    rivals = out[["matchId", "teamId", "teamPosition", "championName", "championId", "puuid"]].rename(
        columns={
            "championName": "opponentChampionName",
            "championId": "opponentChampionId",
            "puuid": "opponentPuuid",
        }
    )
    merged = out.merge(rivals, on=["matchId", "teamPosition"], suffixes=("", "_rival"))
    merged = merged[merged["teamId"] != merged["teamId_rival"]].drop(columns=["teamId_rival"])
    merged = merged.drop_duplicates(subset=["matchId", "puuid"], keep="first")
    missing = out[~out.set_index(["matchId", "puuid"]).index.isin(
        merged.set_index(["matchId", "puuid"]).index
    )].copy()
    missing["opponentChampionName"] = None
    missing["opponentChampionId"] = np.nan
    missing["opponentPuuid"] = None
    return pd.concat([merged, missing], ignore_index=True)

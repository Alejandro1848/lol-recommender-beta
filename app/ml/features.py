"""Feature engineering para el modelo de probabilidad de victoria.

Decision de disenio anti-leakage: el modelo entrenable usa SOLO features
disponibles ANTES de la partida (campeones, rol, composiciones), porque
son las unicas comparables 1:1 entre historial e inferencia en vivo.
Las senales del estado en vivo (oro, kills, torres) se incorporan aparte
via el baseline heuristico (ml/baseline.py) y se mezclan en inference.py.
Asi nunca se usan estadisticas finales de partida para predecir decisiones
tempranas.
"""
from __future__ import annotations

import pandas as pd

from app.data.normalizers import ROLES, attach_direct_opponent
from app.riot.data_dragon import DataDragon

FEATURE_COLUMNS = (
    [f"role_{role}" for role in ROLES]
    + ["own_attack", "own_defense", "own_magic", "own_difficulty"]
    + ["opp_attack", "opp_defense", "opp_magic", "opp_difficulty"]
    + ["ally_magic_share", "enemy_magic_share"]
)

META_COLUMNS = ["matchId", "puuid", "gameCreation", "championName", "opponentChampionName", "teamPosition", "patch"]


def _champ_features(champion: str | None, prefix: str, ddragon: DataDragon) -> dict:
    info = ddragon.champion_info(champion or "")
    return {
        f"{prefix}_attack": info["attack"],
        f"{prefix}_defense": info["defense"],
        f"{prefix}_magic": info["magic"],
        f"{prefix}_difficulty": info["difficulty"],
    }


def _team_magic_share(champions: list[str], ddragon: DataDragon) -> float:
    if not champions:
        return 0.5
    shares = [ddragon.champion_damage_profile(c)["magic"] for c in champions]
    return round(sum(shares) / len(shares), 3)


def _role_onehot(role: str | None) -> dict:
    return {f"role_{r}": 1.0 if role == r else 0.0 for r in ROLES}


def build_feature_row(
    own_champion: str,
    opponent_champion: str | None,
    role: str | None,
    ally_champions: list[str],
    enemy_champions: list[str],
    ddragon: DataDragon,
) -> dict:
    """Fila de features para una situacion (historica o en vivo)."""
    row: dict = {}
    row.update(_role_onehot(role))
    row.update(_champ_features(own_champion, "own", ddragon))
    row.update(_champ_features(opponent_champion, "opp", ddragon))
    row["ally_magic_share"] = _team_magic_share(ally_champions, ddragon)
    row["enemy_magic_share"] = _team_magic_share(enemy_champions, ddragon)
    return row


def build_training_frame(participants: pd.DataFrame, ddragon: DataDragon) -> pd.DataFrame:
    """Dataset: una fila por participante, features pregame + target `win`."""
    if participants.empty:
        return pd.DataFrame()

    df = attach_direct_opponent(participants)

    # Composiciones por (matchId, teamId)
    comps = (
        df.groupby(["matchId", "teamId"])["championName"]
        .apply(list)
        .to_dict()
    )

    rows = []
    for _, part in df.iterrows():
        match_id, team_id = part["matchId"], part["teamId"]
        ally_comp = [c for c in comps.get((match_id, team_id), []) if c != part["championName"]]
        enemy_team = next(
            (tid for (mid, tid) in comps if mid == match_id and tid != team_id), None
        )
        enemy_comp = comps.get((match_id, enemy_team), []) if enemy_team is not None else []

        row = build_feature_row(
            part["championName"],
            part.get("opponentChampionName"),
            part.get("teamPosition"),
            ally_comp,
            enemy_comp,
            ddragon,
        )
        row["win"] = int(part["win"]) if pd.notna(part["win"]) else 0
        for meta in META_COLUMNS:
            row[meta] = part.get(meta)
        rows.append(row)

    return pd.DataFrame(rows)


def split_features_target(dataset: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    X = dataset[FEATURE_COLUMNS].astype(float).fillna(0.0)
    y = dataset["win"].astype(int)
    return X, y


def temporal_split(dataset: pd.DataFrame, test_fraction: float = 0.2):
    """Split temporal por gameCreation: entrena con lo viejo, evalua con lo
    reciente. Evita que el modelo 'vea el futuro'."""
    ordered = dataset.sort_values("gameCreation", kind="stable").reset_index(drop=True)
    cut = max(1, int(len(ordered) * (1 - test_fraction)))
    return ordered.iloc[:cut], ordered.iloc[cut:]

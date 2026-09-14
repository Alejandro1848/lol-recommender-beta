"""Modelo ligero para priorizar objetivos neutrales.

El historico local no contiene intentos fallidos ni timers exactos; contiene
objetivos tomados por equipo y resultado. Por eso modelamos una pregunta
honesta: "si mi equipo consigue ventaja en este objetivo, que probabilidad
historica de victoria se observa, ajustando por kills/torres?".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
from sklearn.linear_model import LogisticRegression


OBJECTIVES = {
    "dragon": {
        "column": "dragon_kills",
        "label": "Dragon",
        "window": "desde 5:00",
        "availability": lambda minute: minute >= 5,
    },
    "herald": {
        "column": "riftHerald_kills",
        "label": "Heraldo",
        "window": "medio juego",
        "availability": lambda minute: 14 <= minute < 20,
    },
    "baron": {
        "column": "baron_kills",
        "label": "Baron",
        "window": "desde 20:00",
        "availability": lambda minute: minute >= 20,
    },
    "grubs": {
        "column": "horde_kills",
        "label": "Larvas del Vacio",
        "window": "early topside antes de Heraldo",
        "availability": lambda minute: 5 <= minute < 14,
    },
}


@dataclass
class ObjectiveModel:
    objective: str
    model: LogisticRegression | None
    sample_size: int
    baseline_winrate: float


def _safe_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0
    return int(value)


def _known(value: Any) -> bool:
    return value is not None and not pd.isna(value)


def _paired_rows(teams: pd.DataFrame) -> list[dict[str, Any]]:
    if teams.empty:
        return []
    rows = []
    for match_id, match in teams.groupby("matchId"):
        if len(match) != 2:
            continue
        for _, team in match.iterrows():
            enemy = match[match["teamId"] != team["teamId"]].iloc[0]
            row = {
                "matchId": match_id,
                "teamId": team["teamId"],
                "win": _safe_int(team.get("win")),
                "kill_diff": _safe_int(team.get("champion_kills")) - _safe_int(enemy.get("champion_kills")),
                "tower_diff": _safe_int(team.get("tower_kills")) - _safe_int(enemy.get("tower_kills")),
            }
            for objective, meta in OBJECTIVES.items():
                column = meta["column"]
                if column not in match.columns:
                    continue
                row[f"{objective}_count"] = _safe_int(team.get(column))
                row[f"{objective}_diff"] = _safe_int(team.get(column)) - _safe_int(enemy.get(column))
                row[f"{objective}_known"] = _known(team.get(column)) and _known(enemy.get(column))
            rows.append(row)
    return rows


def train_objective_models(teams: pd.DataFrame) -> dict[str, ObjectiveModel]:
    rows = _paired_rows(teams)
    if not rows:
        return {}
    df = pd.DataFrame(rows)
    models: dict[str, ObjectiveModel] = {}
    for objective in OBJECTIVES:
        feature_cols = [f"{objective}_count", f"{objective}_diff", "kill_diff", "tower_diff"]
        if any(col not in df.columns for col in feature_cols):
            models[objective] = ObjectiveModel(objective, None, 0, 0.5)
            continue
        known_col = f"{objective}_known"
        if known_col in df.columns:
            objective_df = df[df[known_col].astype(bool)]
        else:
            objective_df = df
        train = objective_df[feature_cols + ["win"]].dropna()
        baseline = float(train["win"].mean()) if not train.empty else 0.5
        has_objective_signal = (
            train[f"{objective}_count"].sum() > 0
            or train[f"{objective}_diff"].abs().sum() > 0
        )
        if len(train) < 30 or train["win"].nunique() < 2 or not has_objective_signal:
            models[objective] = ObjectiveModel(objective, None, int(len(train)), baseline)
            continue
        model = LogisticRegression(max_iter=1000, C=0.8)
        model.fit(train[feature_cols].astype(float), train["win"].astype(int))
        models[objective] = ObjectiveModel(objective, model, int(len(train)), baseline)
    return models


def _live_team_sums(snapshot: dict | None) -> dict[str, float]:
    if not snapshot:
        return {"kill_diff": 0, "level_diff": 0, "cs_diff": 0}
    me = snapshot.get("me") or {}
    allies = snapshot.get("allies", []) + ([me] if me else [])
    enemies = snapshot.get("enemies", [])

    def team_sum(players, key):
        return sum(p.get(key, 0) or 0 for p in players)

    return {
        "kill_diff": team_sum(allies, "kills") - team_sum(enemies, "kills"),
        "level_diff": team_sum(allies, "level") - team_sum(enemies, "level"),
        "cs_diff": team_sum(allies, "creep_score") - team_sum(enemies, "creep_score"),
    }


def _event_counts(snapshot: dict | None) -> tuple[dict, dict]:
    if not snapshot:
        return {}, {}
    my_team = snapshot.get("my_team")
    enemy_key = "CHAOS" if my_team == "ORDER" else "ORDER"
    events = snapshot.get("events_summary") or {}
    return events.get(my_team, {}) if my_team else {}, events.get(enemy_key, {})


def _predict_with_objective(
    objective: str,
    model: ObjectiveModel | None,
    snapshot: dict | None,
) -> tuple[float, list[str]]:
    mine, theirs = _event_counts(snapshot)
    live = _live_team_sums(snapshot)
    reasons = []

    column = OBJECTIVES[objective]["column"]
    current = int(mine.get(column.replace("_kills", "s"), 0) or 0) if column else 0
    enemy_current = int(theirs.get(column.replace("_kills", "s"), 0) or 0) if column else 0
    # events_summary usa plural simplificado.
    key = {"dragon": "dragons", "herald": "heralds", "baron": "barons", "grubs": "grubs"}[objective]
    current = int(mine.get(key, 0) or 0)
    enemy_current = int(theirs.get(key, 0) or 0)
    count_after = current + 1
    diff_after = count_after - enemy_current
    if model and model.model:
        X = pd.DataFrame([{
            f"{objective}_count": count_after,
            f"{objective}_diff": diff_after,
            "kill_diff": live["kill_diff"],
            "tower_diff": int(mine.get("turrets", 0) or 0) - int(theirs.get("turrets", 0) or 0),
        }])
        probability = float(model.model.predict_proba(X)[:, 1][0])
        reasons.append(f"modelo logistico entrenado con {model.sample_size} equipos historicos")
    else:
        if objective == "grubs":
            pressure = live["kill_diff"] * 0.015 + live["level_diff"] * 0.01
            probability = 0.5 + min(0.15, max(-0.15, pressure))
            reasons.append("sin horde_kills historicos suficientes; se usa proxy por ventaja temprana y presion topside")
        else:
            probability = model.baseline_winrate if model else 0.5
            reasons.append("muestra historica insuficiente; se usa tasa base")
    if live["kill_diff"] >= 3:
        probability += 0.04
        reasons.append("tu equipo tiene ventaja de kills")
    elif live["kill_diff"] <= -3:
        probability -= 0.05
        reasons.append("tu equipo va detras en kills: evita coin flip")
    if live["level_diff"] >= 2:
        probability += 0.03
        reasons.append("ventaja visible de niveles")
    return round(max(0.05, min(0.95, probability)), 3), reasons


def objective_priority(snapshot: dict | None, teams: pd.DataFrame) -> list[dict[str, Any]]:
    minute = ((snapshot or {}).get("game_time_seconds") or 0) / 60
    models = train_objective_models(teams)
    rows = []
    for objective, meta in OBJECTIVES.items():
        available = bool(meta["availability"](minute))
        probability, reasons = _predict_with_objective(objective, models.get(objective), snapshot)
        if not available:
            probability -= 0.12
            reasons.append(f"fuera de ventana principal ({meta['window']})")
        rows.append({
            "objective": objective,
            "label": meta["label"],
            "success_probability": round(max(0.01, min(0.99, probability)), 3),
            "available_now": available,
            "window": meta["window"],
            "reasons": reasons,
            "sample_size": models.get(objective).sample_size if models.get(objective) else 0,
            "model": (
                "logistic_regression"
                if models.get(objective) and models[objective].model is not None
                else ("historical_proxy" if objective == "grubs" else "baseline")
            ),
        })
    rows.sort(key=lambda row: (row["available_now"], row["success_probability"]), reverse=True)
    return rows

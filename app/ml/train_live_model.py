"""Modelo IN-GAME de probabilidad de victoria, entrenado con timelines.

A diferencia del modelo pregame (que topa en ~0.55 de AUC porque el draft y
los jugadores predicen poco), el estado de la partida SI predice: con las
diferencias de kills/niveles/CS/torres/dragones/barones minuto a minuto el
AUC sube conforme avanza el juego. Este modelo reemplaza al heuristico de
pesos a mano de ml/baseline.py en la mezcla en vivo.

Decisiones:
- SOLO features disponibles en el Live Client (paridad entrenamiento vs
  inferencia): nada de oro enemigo.
- Cada partida aporta una fila por minuto (4..30) en AMBAS perspectivas
  (equipo azul y rojo, con el signo volteado), lo que centra el modelo.
- El split temporal es POR PARTIDA (todas las filas de una partida caen en
  el mismo split) para no evaluar sobre minutos de partidas ya vistas.
- Se reporta AUC por tramo de minutos: la metrica honesta es "que tan bien
  predice al minuto X", no el promedio global.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from app.ml.champion_registry import ChampionModelRegistry
from app.ml.evaluation import (
    InsufficientDataError,
    compare_to_baseline,
    evaluate_probabilities,
)
from app.ml.training_plots import plot_auc_by_minute, plot_metric_comparison

logger = logging.getLogger(__name__)

LIVE_FEATURE_COLUMNS = [
    "minute", "kill_diff", "level_diff", "cs_diff",
    "turret_diff", "dragon_diff", "baron_diff",
]
MIN_MINUTE = 4
MAX_MINUTE = 30
MIN_TIMELINE_MATCHES = 200
MINUTE_BUCKETS = [(4, 10), (10, 15), (15, 20), (20, 25), (25, 31)]

LIVE_METRICS = [("roc_auc", True), ("accuracy", True), ("brier_score", False)]


def _candidates() -> dict[str, Pipeline]:
    return {
        "logistic_regression": Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=2000, C=1.0)),
        ]),
        "lightgbm": Pipeline([
            ("clf", LGBMClassifier(
                n_estimators=300, learning_rate=0.05, num_leaves=31,
                min_child_samples=40, subsample=0.9, colsample_bytree=0.9,
                random_state=42, verbose=-1,
            )),
        ]),
        "xgboost": Pipeline([
            ("clf", XGBClassifier(
                n_estimators=300, learning_rate=0.05, max_depth=5,
                subsample=0.9, colsample_bytree=0.9, min_child_weight=10,
                random_state=42, eval_metric="logloss", tree_method="hist",
            )),
        ]),
    }


def build_live_training_frame(timeline_df: pd.DataFrame) -> pd.DataFrame:
    """Una fila por (partida, minuto, perspectiva) con diffs y target win."""
    if timeline_df.empty:
        return pd.DataFrame()
    df = timeline_df[
        (timeline_df["minute"] >= MIN_MINUTE) & (timeline_df["minute"] <= MAX_MINUTE)
    ].copy()
    if df.empty:
        return pd.DataFrame()
    diffs = {
        "kill_diff": df["kills_100"] - df["kills_200"],
        "level_diff": df["level_100"] - df["level_200"],
        "cs_diff": df["cs_100"] - df["cs_200"],
        "turret_diff": df["turrets_100"] - df["turrets_200"],
        "dragon_diff": df["dragons_100"] - df["dragons_200"],
        "baron_diff": df["barons_100"] - df["barons_200"],
    }
    win_100 = df["win_100"].astype(int)
    blue = pd.DataFrame({
        "matchId": df["matchId"], "gameCreation": df["gameCreation"],
        "minute": df["minute"], **diffs, "win": win_100,
    })
    red = pd.DataFrame({
        "matchId": df["matchId"], "gameCreation": df["gameCreation"],
        "minute": df["minute"], **{k: -v for k, v in diffs.items()}, "win": 1 - win_100,
    })
    return pd.concat([blue, red], ignore_index=True)


def _split_by_match(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """60/20/20 temporal POR PARTIDA (una partida nunca cruza splits)."""
    matches = (
        frame[["matchId", "gameCreation"]]
        .drop_duplicates("matchId")
        .sort_values("gameCreation", kind="stable")["matchId"]
        .tolist()
    )
    train_cut = max(1, int(len(matches) * 0.6))
    validation_cut = max(train_cut + 1, int(len(matches) * 0.8))
    train_ids = set(matches[:train_cut])
    validation_ids = set(matches[train_cut:validation_cut])
    test_ids = set(matches[validation_cut:])
    return (
        frame[frame["matchId"].isin(train_ids)],
        frame[frame["matchId"].isin(validation_ids)],
        frame[frame["matchId"].isin(test_ids)],
    )


def _auc_by_bucket(model, test_df: pd.DataFrame) -> dict[str, float]:
    out = {}
    for start, end in MINUTE_BUCKETS:
        subset = test_df[(test_df["minute"] >= start) & (test_df["minute"] < end)]
        if len(subset) < 50 or subset["win"].nunique() < 2:
            continue
        probs = model.predict_proba(
            subset[LIVE_FEATURE_COLUMNS].astype(float).fillna(0.0)
        )[:, 1]
        metrics = evaluate_probabilities(subset["win"].astype(int), probs)
        out[f"{start}-{end - 1}"] = metrics.get("roc_auc", 0.5)
    return out


def train_live_win_model(
    timeline_df: pd.DataFrame,
    champion: str,
    tier: str,
    patch: str | None,
    registry: ChampionModelRegistry,
) -> dict:
    """Compara LR/LightGBM/XGBoost sobre el estado por minuto y registra el
    mejor modelo in-game (calibrado)."""
    frame = build_live_training_frame(timeline_df)
    n_matches = frame["matchId"].nunique() if not frame.empty else 0
    if n_matches < MIN_TIMELINE_MATCHES:
        raise InsufficientDataError(
            f"Solo hay {n_matches} partidas con timeline; se requieren al menos "
            f"{MIN_TIMELINE_MATCHES}. Corre --mode ingest-timelines."
        )
    train_df, validation_df, test_df = _split_by_match(frame)
    X_train = train_df[LIVE_FEATURE_COLUMNS].astype(float).fillna(0.0)
    y_train = train_df["win"].astype(int)
    X_validation = validation_df[LIVE_FEATURE_COLUMNS].astype(float).fillna(0.0)
    y_validation = validation_df["win"].astype(int)
    X_test = test_df[LIVE_FEATURE_COLUMNS].astype(float).fillna(0.0)
    y_test = test_df["win"].astype(int)

    rows, fitted, buckets = [], {}, {}
    for name, pipeline in _candidates().items():
        pipeline.fit(X_train, y_train)
        validation_cmp = compare_to_baseline(y_validation, pipeline.predict_proba(X_validation)[:, 1])
        test_cmp = compare_to_baseline(y_test, pipeline.predict_proba(X_test)[:, 1])
        rows.append({
            "algorithm": name, "round": "live", "selected": False,
            "validation": validation_cmp["model"], "test": test_cmp["model"],
            "baseline_test": test_cmp["baseline"],
        })
        fitted[name] = pipeline
        buckets[name] = _auc_by_bucket(pipeline, test_df)
        logger.info(
            "[live] %s | validation=%s | test=%s | AUC por minuto=%s",
            name, validation_cmp["model"], test_cmp["model"], buckets[name],
        )

    best = max(rows, key=lambda r: (r["validation"].get("roc_auc", -1.0),
                                    -r["validation"].get("brier_score", 1.0)))
    best["selected"] = True
    selected = fitted[best["algorithm"]]
    calibrated = CalibratedClassifierCV(
        FrozenEstimator(selected), method="sigmoid"
    ).fit(X_validation, y_validation)
    calibrated_test = compare_to_baseline(y_test, calibrated.predict_proba(X_test)[:, 1])

    payload = {
        "model": calibrated,
        "algorithm": best["algorithm"],
        "feature_columns": LIVE_FEATURE_COLUMNS,
        "calibration": "sigmoid_on_validation",
        "champion": champion,
        "tier": tier.upper(),
    }
    directory = registry.champion_dir(champion, tier)
    plots = {
        "auc_by_minute": str(plot_auc_by_minute(
            buckets, directory / "live_auc_by_minute.png",
            f"{champion} - modelo in-game: AUC segun el minuto ({tier.upper()})",
        )),
        "metrics": str(plot_metric_comparison(
            rows, LIVE_METRICS, directory / "live_metric_comparison.png",
            f"{champion} - comparacion de modelos in-game ({tier.upper()})",
        )),
    }
    meta = {
        "kind": "live",
        "algorithm": best["algorithm"],
        "patch": patch,
        "n_samples": int(len(frame)),
        "n_matches": int(n_matches),
        "feature_columns": LIVE_FEATURE_COLUMNS,
        "calibration": "sigmoid_on_validation",
        "metrics": calibrated_test["model"],
        "metrics_uncalibrated": best["test"],
        "validation_metrics": best["validation"],
        "auc_by_minute": buckets.get(best["algorithm"], {}),
        "model_comparison": rows,
        "plots": plots,
    }
    return registry.save("live", champion, tier, payload, meta)


def live_features_from_snapshot(snapshot: dict) -> dict | None:
    """Extrae las features del modelo in-game desde el snapshot en vivo.

    Usa exactamente las mismas senales que el heuristico de baseline.py
    (kills/niveles/CS por jugador, torres/dragones/barones por lado)."""
    me = snapshot.get("me") or {}
    my_team = snapshot.get("my_team")
    allies = (snapshot.get("allies") or []) + ([me] if me else [])
    enemies = snapshot.get("enemies") or []
    if not allies or not enemies:
        return None

    def team_sum(players, key):
        return sum(p.get(key, 0) or 0 for p in players)

    events = snapshot.get("events_summary") or {}
    my_side = events.get(my_team, {}) if my_team else {}
    enemy_side = events.get("CHAOS" if my_team == "ORDER" else "ORDER", {})
    minute = (snapshot.get("game_time_seconds") or 0) / 60.0
    return {
        "minute": round(min(float(MAX_MINUTE), minute), 2),
        "kill_diff": team_sum(allies, "kills") - team_sum(enemies, "kills"),
        "level_diff": team_sum(allies, "level") - team_sum(enemies, "level"),
        "cs_diff": team_sum(allies, "creep_score") - team_sum(enemies, "creep_score"),
        "turret_diff": (my_side.get("turrets") or 0) - (enemy_side.get("turrets") or 0),
        "dragon_diff": (my_side.get("dragons") or 0) - (enemy_side.get("dragons") or 0),
        "baron_diff": (my_side.get("barons") or 0) - (enemy_side.get("barons") or 0),
    }

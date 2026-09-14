"""Comparacion de modelos para sugerencia inicial de items.

Predice presencia de items finales a partir de features pregame. Es una
evaluacion offline de ranking de items; en vivo se combina con reglas de
estado actual para evitar sugerir compras desconectadas de la partida.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.analytics.item_analysis import ITEM_SLOTS, NON_BUILD_ITEM_IDS
from app.data.normalizers import attach_direct_opponent
from app.ml.features import FEATURE_COLUMNS, build_feature_row
from app.riot.data_dragon import DataDragon

TOP_ITEM_LABELS = 30


def _valid_item(value: Any, ddragon: DataDragon) -> int | None:
    try:
        item_id = int(value)
    except (TypeError, ValueError):
        return None
    if item_id <= 0 or item_id in NON_BUILD_ITEM_IDS:
        return None
    is_final = ddragon.item_is_final(item_id) if hasattr(ddragon, "item_is_final") else None
    if is_final is False:
        return None
    return item_id


def _temporal_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ordered = df.sort_values("gameCreation", kind="stable").reset_index(drop=True)
    train_cut = max(1, int(len(ordered) * 0.6))
    validation_cut = max(train_cut + 1, int(len(ordered) * 0.8))
    return ordered.iloc[:train_cut], ordered.iloc[train_cut:validation_cut], ordered.iloc[validation_cut:]


def build_item_training_frame(participants: pd.DataFrame, ddragon: DataDragon) -> tuple[pd.DataFrame, list[int]]:
    if participants.empty:
        return pd.DataFrame(), []
    df = attach_direct_opponent(participants)
    comps = df.groupby(["matchId", "teamId"])["championName"].apply(list).to_dict()
    item_counter: Counter[int] = Counter()
    rows = []
    for _, part in df.iterrows():
        items = [
            item_id
            for item_id in (_valid_item(part.get(slot), ddragon) for slot in ITEM_SLOTS)
            if item_id is not None
        ]
        if not items:
            continue
        item_counter.update(set(items))
        match_id, team_id = part["matchId"], part["teamId"]
        ally_comp = [c for c in comps.get((match_id, team_id), []) if c != part["championName"]]
        enemy_team = next((tid for (mid, tid) in comps if mid == match_id and tid != team_id), None)
        enemy_comp = comps.get((match_id, enemy_team), []) if enemy_team is not None else []
        row = build_feature_row(
            part["championName"],
            part.get("opponentChampionName"),
            part.get("teamPosition"),
            ally_comp,
            enemy_comp,
            ddragon,
        )
        row["matchId"] = part.get("matchId")
        row["puuid"] = part.get("puuid")
        row["gameCreation"] = part.get("gameCreation")
        row["items"] = items
        rows.append(row)

    top_items = [item_id for item_id, _ in item_counter.most_common(TOP_ITEM_LABELS)]
    for row in rows:
        item_set = set(row["items"])
        for item_id in top_items:
            row[f"item_{item_id}"] = 1 if item_id in item_set else 0
    return pd.DataFrame(rows), top_items


def _models() -> dict[str, object]:
    return {
        "one_vs_rest_logistic": Pipeline([
            ("scaler", StandardScaler()),
            ("clf", OneVsRestClassifier(LogisticRegression(max_iter=1200, C=0.7))),
        ]),
        "random_forest_multilabel": RandomForestClassifier(
            n_estimators=160, max_depth=8, min_samples_leaf=3, random_state=42
        ),
        "one_vs_rest_gradient_boosting": OneVsRestClassifier(
            GradientBoostingClassifier(n_estimators=70, learning_rate=0.06, max_depth=2, random_state=42)
        ),
    }


def _positive_probabilities(model, X: pd.DataFrame) -> np.ndarray:
    raw = model.predict_proba(X)
    if isinstance(raw, list):
        cols = []
        for probs in raw:
            cols.append(probs[:, 1] if probs.shape[1] > 1 else np.zeros(len(X)))
        return np.vstack(cols).T
    return np.asarray(raw)


def _evaluate(model, X: pd.DataFrame, y: pd.DataFrame, k: int = 3) -> dict[str, float]:
    if X.empty or y.empty:
        return {"n": 0, "hit_rate_at_3": 0, "precision_at_3": 0, "recall_at_3": 0}
    probs = _positive_probabilities(model, X)
    hits, precision, recall = [], [], []
    actual = y.to_numpy()
    for i in range(len(X)):
        actual_ids = set(np.flatnonzero(actual[i] > 0))
        if not actual_ids:
            continue
        top = set(np.argsort(probs[i])[-k:])
        hit_count = len(top & actual_ids)
        hits.append(1 if hit_count else 0)
        precision.append(hit_count / k)
        recall.append(hit_count / len(actual_ids))
    return {
        "n": int(len(hits)),
        "hit_rate_at_3": round(float(np.mean(hits)) if hits else 0.0, 3),
        "precision_at_3": round(float(np.mean(precision)) if precision else 0.0, 3),
        "recall_at_3": round(float(np.mean(recall)) if recall else 0.0, 3),
    }


def _write_report(models_dir: Path, stem: str, rows: list[dict]) -> dict[str, str]:
    json_path = models_dir / f"{stem}_comparison.json"
    csv_path = models_dir / f"{stem}_comparison.csv"
    html_path = models_dir / f"{stem}_comparison.html"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "algorithm", "selected",
        "validation_hit_rate_at_3", "validation_precision_at_3", "validation_recall_at_3",
        "test_hit_rate_at_3", "test_precision_at_3", "test_recall_at_3",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "algorithm": row["algorithm"],
                "selected": row["selected"],
                "validation_hit_rate_at_3": row["validation"]["hit_rate_at_3"],
                "validation_precision_at_3": row["validation"]["precision_at_3"],
                "validation_recall_at_3": row["validation"]["recall_at_3"],
                "test_hit_rate_at_3": row["test"]["hit_rate_at_3"],
                "test_precision_at_3": row["test"]["precision_at_3"],
                "test_recall_at_3": row["test"]["recall_at_3"],
            })
    max_hit = max((row["test"]["hit_rate_at_3"] for row in rows), default=1) or 1
    bars = []
    for row in rows:
        hit = row["test"]["hit_rate_at_3"]
        bars.append(
            f"<div class='row {'selected' if row['selected'] else ''}'><b>{row['algorithm']}</b>"
            f"<span><i style='width:{max(4, int(hit / max_hit * 100))}%'></i></span>"
            f"<em>Hit@3 {hit:.3f} | P@3 {row['test']['precision_at_3']:.3f}</em></div>"
        )
    html_path.write_text(
        "<html><head><meta charset='utf-8'><style>"
        "body{font-family:Segoe UI,Arial;background:#0b0e14;color:#e8edf6;padding:24px}"
        ".row{display:grid;grid-template-columns:230px 1fr 220px;gap:12px;margin:12px 0;align-items:center}"
        ".selected b{color:#3ecf8e}span{height:18px;background:#151a26;border:1px solid #232b3d;border-radius:6px;overflow:hidden}"
        "i{display:block;height:100%;background:linear-gradient(90deg,#4fa3ff,#3ecf8e)}em{color:#9aa7bd;font-style:normal}"
        "</style></head><body><h1>Comparacion item recommender</h1>"
        + "".join(bars) + "</body></html>",
        encoding="utf-8",
    )
    return {"json": str(json_path), "csv": str(csv_path), "html": str(html_path)}


def train_item_recommender_comparison(
    participants: pd.DataFrame,
    ddragon: DataDragon,
    models_dir: Path,
    focus_riot_id: str | None = None,
) -> dict:
    models_dir = Path(models_dir)
    frame, item_ids = build_item_training_frame(participants, ddragon)
    if frame.empty or len(item_ids) < 3:
        raise ValueError("No hay suficientes items finales para entrenar comparador de items.")
    train_df, validation_df, test_df = _temporal_split(frame)
    label_cols = [f"item_{item_id}" for item_id in item_ids]
    usable_cols = [
        col for col in label_cols
        if train_df[col].sum() > 0 and train_df[col].sum() < len(train_df)
    ]
    item_ids = [int(col.replace("item_", "")) for col in usable_cols]
    X_train = train_df[list(FEATURE_COLUMNS)].astype(float).fillna(0)
    X_validation = validation_df[list(FEATURE_COLUMNS)].astype(float).fillna(0)
    X_test = test_df[list(FEATURE_COLUMNS)].astype(float).fillna(0)
    y_train = train_df[usable_cols].astype(int)
    y_validation = validation_df[usable_cols].astype(int)
    y_test = test_df[usable_cols].astype(int)

    rows = []
    fitted = {}
    for name, model in _models().items():
        model.fit(X_train, y_train)
        validation = _evaluate(model, X_validation, y_validation)
        test = _evaluate(model, X_test, y_test)
        rows.append({"algorithm": name, "selected": False, "validation": validation, "test": test})
        fitted[name] = model
    best = max(rows, key=lambda row: (row["validation"]["hit_rate_at_3"], row["validation"]["precision_at_3"]))
    best["selected"] = True

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"item_model_{timestamp}_n{len(frame)}"
    joblib.dump(
        {
            "model": fitted[best["algorithm"]],
            "algorithm": best["algorithm"],
            "item_ids": item_ids,
            "feature_columns": list(FEATURE_COLUMNS),
            "trained_for": focus_riot_id,
        },
        models_dir / f"{stem}.joblib",
    )
    files = _write_report(models_dir, stem, rows)
    meta = {
        "version": stem,
        "algorithm": best["algorithm"],
        "trained_for": focus_riot_id,
        "n_samples": int(len(frame)),
        "n_items": int(len(item_ids)),
        "metrics": best["test"],
        "validation_metrics": best["validation"],
        "model_comparison": rows,
        "model_comparison_files": files,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    (models_dir / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta

"""Entrenamiento del modelo de ITEMS por campeon, con registry propio.

Nota de arquitectura: aqui vivia tambien un modelo PREGAME de probabilidad
de victoria (draft + senales de jugadores). Se elimino tras medirlo: su
ROC-AUC quedaba en ~0.53 (casi una moneda), asi que la probabilidad en
vivo ahora arranca en 50% fijo y depende del modelo IN-GAME entrenado con
timelines (ver app/ml/train_live_model.py e inference.py).

Flujo pensado para el caso OTP: antes de cada partida se consulta el
registry (models/champions/<Campeon>/<TIER>/); si ya existe un modelo del
parche actual se reutiliza tal cual y NO se reentrena. Solo un parche
nuevo (o --force-retrain) dispara un nuevo entrenamiento.

El historico se limita a las N partidas mas recientes del campeon
(CHAMPION_HISTORY_MATCHES, 3000 por defecto) de jugadores de la misma
region/liga ya ingestados en la DB (--mode ingest-ladder).

Comparacion de algoritmos: LogisticRegression -> LightGBM -> XGBoost
(en OneVsRest, porque predecir items es multi-etiqueta). Cada
entrenamiento deja metricas comparativas (json/csv) y un plot (png).
"""
from __future__ import annotations

import csv
import json
import logging
from collections import Counter
from pathlib import Path

import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from app.analytics.item_analysis import ITEM_SLOTS
from app.data.normalizers import attach_direct_opponent
from app.ml.champion_registry import ChampionModelRegistry
from app.ml.evaluation import InsufficientDataError
from app.ml.features import FEATURE_COLUMNS, build_feature_row
from app.ml.train_item_recommender import _evaluate as evaluate_item_ranking
from app.ml.train_item_recommender import _valid_item
from app.ml.training_plots import plot_metric_comparison
from app.riot.data_dragon import DataDragon

logger = logging.getLogger(__name__)

MIN_CHAMPION_ROWS = 120
TOP_ITEM_LABELS = 20

ITEM_METRICS = [("hit_rate_at_3", True), ("precision_at_3", True), ("recall_at_3", True)]


# ------------------------------------------------------------------ dataset


def build_champion_frame(
    participants: pd.DataFrame,
    champion: str,
    ddragon: DataDragon,
    max_matches: int = 3000,
) -> pd.DataFrame:
    """Una fila por aparicion del campeon: features pregame + items finales.

    Solo se construyen filas del campeon (el resto de participantes aporta
    las composiciones); se conservan las `max_matches` partidas mas recientes.
    """
    if participants.empty:
        return pd.DataFrame()
    df = attach_direct_opponent(participants)
    champ_rows = df[df["championName"] == champion]
    if champ_rows.empty:
        return pd.DataFrame()

    recent_matches = (
        champ_rows[["matchId", "gameCreation"]]
        .drop_duplicates("matchId")
        .sort_values("gameCreation", ascending=False)
        .head(max(1, int(max_matches)))["matchId"]
    )
    match_ids = set(recent_matches)
    df = df[df["matchId"].isin(match_ids)]
    comps = df.groupby(["matchId", "teamId"])["championName"].apply(list).to_dict()
    teams_by_match: dict = {}
    for match_id, team_id in comps:
        teams_by_match.setdefault(match_id, []).append(team_id)

    rows = []
    for _, part in df[df["championName"] == champion].iterrows():
        match_id, team_id = part["matchId"], part["teamId"]
        ally_comp = [c for c in comps.get((match_id, team_id), []) if c != champion]
        enemy_team = next(
            (tid for tid in teams_by_match.get(match_id, []) if tid != team_id), None
        )
        enemy_comp = comps.get((match_id, enemy_team), []) if enemy_team is not None else []
        row = build_feature_row(
            champion,
            part.get("opponentChampionName"),
            part.get("teamPosition"),
            ally_comp,
            enemy_comp,
            ddragon,
        )
        row["matchId"] = match_id
        row["puuid"] = part.get("puuid")
        row["gameCreation"] = part.get("gameCreation")
        row["patch"] = part.get("patch")
        row["items"] = [
            item_id
            for item_id in (_valid_item(part.get(slot), ddragon) for slot in ITEM_SLOTS)
            if item_id is not None
        ]
        rows.append(row)
    return pd.DataFrame(rows)


def _temporal_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ordered = frame.sort_values("gameCreation", kind="stable").reset_index(drop=True)
    train_cut = max(1, int(len(ordered) * 0.6))
    validation_cut = max(train_cut + 1, int(len(ordered) * 0.8))
    validation_cut = min(validation_cut, len(ordered))
    return ordered.iloc[:train_cut], ordered.iloc[train_cut:validation_cut], ordered.iloc[validation_cut:]


# ----------------------------------------------------------------- candidatos


def _item_candidates() -> dict[str, object]:
    return {
        "logistic_regression": Pipeline([
            ("scaler", StandardScaler()),
            ("clf", OneVsRestClassifier(LogisticRegression(max_iter=1500, C=0.7))),
        ]),
        "lightgbm": OneVsRestClassifier(LGBMClassifier(
            n_estimators=150, learning_rate=0.06, num_leaves=15,
            min_child_samples=10, random_state=42, verbose=-1,
        )),
        "xgboost": OneVsRestClassifier(XGBClassifier(
            n_estimators=150, learning_rate=0.06, max_depth=3,
            min_child_weight=2, random_state=42, eval_metric="logloss",
            tree_method="hist",
        )),
    }


def _write_comparison_report(directory: Path, stem: str, rows: list[dict], fields: list[str]) -> dict:
    json_path = directory / f"{stem}_comparison.json"
    csv_path = directory / f"{stem}_comparison.csv"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["algorithm", "selected"] + fields)
        writer.writeheader()
        for row in rows:
            flat = {"algorithm": row["algorithm"], "selected": row["selected"]}
            for field in fields:
                split, metric = field.split("_", 1)
                flat[field] = (row.get(split) or {}).get(metric)
            writer.writerow(flat)
    return {"json": str(json_path), "csv": str(csv_path)}


# -------------------------------------------------------------- entrenamiento


def train_champion_item_model(
    frame: pd.DataFrame,
    champion: str,
    tier: str,
    patch: str | None,
    registry: ChampionModelRegistry,
) -> dict:
    """Comparacion LR/LightGBM/XGBoost para ranking de items del campeon."""
    item_frame = frame[frame["items"].map(bool)].reset_index(drop=True)
    counter: Counter[int] = Counter()
    for items in item_frame["items"]:
        counter.update(set(items))
    top_items = [item_id for item_id, _ in counter.most_common(TOP_ITEM_LABELS)]
    if len(item_frame) < MIN_CHAMPION_ROWS // 2 or len(top_items) < 5:
        raise InsufficientDataError(
            f"No hay suficientes builds de {champion} para entrenar el modelo de items "
            f"({len(item_frame)} filas, {len(top_items)} items distintos)."
        )
    for item_id in top_items:
        item_frame[f"item_{item_id}"] = item_frame["items"].map(
            lambda items, i=item_id: 1 if i in set(items) else 0
        )

    train_df, validation_df, test_df = _temporal_split(item_frame)
    label_cols = [
        f"item_{item_id}" for item_id in top_items
        if 0 < train_df[f"item_{item_id}"].sum() < len(train_df)
    ]
    item_ids = [int(col.replace("item_", "")) for col in label_cols]
    if len(item_ids) < 3:
        raise InsufficientDataError(
            f"Las builds de {champion} en entrenamiento no tienen variedad de items."
        )
    feature_columns = list(FEATURE_COLUMNS)
    X_train = train_df[feature_columns].astype(float).fillna(0.0)
    X_validation = validation_df[feature_columns].astype(float).fillna(0.0)
    X_test = test_df[feature_columns].astype(float).fillna(0.0)
    y_train = train_df[label_cols].astype(int)
    y_validation = validation_df[label_cols].astype(int)
    y_test = test_df[label_cols].astype(int)

    rows, fitted = [], {}
    for name, model in _item_candidates().items():
        model.fit(X_train, y_train)
        rows.append({
            "algorithm": name,
            "selected": False,
            "validation": evaluate_item_ranking(model, X_validation, y_validation),
            "test": evaluate_item_ranking(model, X_test, y_test),
        })
        fitted[name] = model
        logger.info("[items] %s | validation=%s | test=%s", name, rows[-1]["validation"], rows[-1]["test"])

    best = max(rows, key=lambda r: (r["validation"]["hit_rate_at_3"], r["validation"]["precision_at_3"]))
    best["selected"] = True
    payload = {
        "model": fitted[best["algorithm"]],
        "algorithm": best["algorithm"],
        "item_ids": item_ids,
        "feature_columns": feature_columns,
        "champion": champion,
        "tier": tier.upper(),
    }

    directory = registry.champion_dir(champion, tier)
    plots = {
        "metrics": str(plot_metric_comparison(
            rows, ITEM_METRICS, directory / "item_metric_comparison.png",
            f"{champion} - comparacion de modelos de items ({tier.upper()})",
        )),
    }
    report_fields = [
        "validation_hit_rate_at_3", "validation_precision_at_3", "validation_recall_at_3",
        "test_hit_rate_at_3", "test_precision_at_3", "test_recall_at_3",
    ]
    files = _write_comparison_report(directory, "item_model", rows, report_fields)
    meta = {
        "kind": "item",
        "algorithm": best["algorithm"],
        "patch": patch,
        "n_samples": int(len(item_frame)),
        "n_items": int(len(item_ids)),
        "item_ids": item_ids,
        "metrics": best["test"],
        "validation_metrics": best["validation"],
        "model_comparison": rows,
        "model_comparison_files": files,
        "plots": plots,
    }
    return registry.save("item", champion, tier, payload, meta)


# --------------------------------------------------------------- entry point


def current_patch(ddragon: DataDragon, frame: pd.DataFrame | None = None) -> str | None:
    """Parche vigente segun Data Dragon; si no hay red, el mayor del dataset."""
    version = ddragon.version()
    if version:
        parts = str(version).split(".")
        if len(parts) >= 2:
            return f"{parts[0]}.{parts[1]}"
    if frame is not None and "patch" in frame.columns and frame["patch"].notna().any():
        patches = frame["patch"].dropna().astype(str).unique()
        try:
            return max(patches, key=lambda p: tuple(int(x) for x in p.split(".")))
        except ValueError:
            return None
    return None


def train_champion_models(
    participants: pd.DataFrame,
    champion: str,
    tier: str,
    registry: ChampionModelRegistry,
    ddragon: DataDragon,
    max_matches: int = 3000,
    force: bool = False,
) -> dict:
    """Entrena (si hace falta) el modelo de items del campeon.

    Devuelve {"item": meta|None, "skipped": [...], "patch": p}. Con un
    modelo vigente para el parche actual y force=False, se reutiliza (caso
    OTP) y no se reentrena. El modelo in-game se entrena aparte en el
    orquestador (necesita timelines, no este frame).
    """
    frame = build_champion_frame(participants, champion, ddragon, max_matches=max_matches)
    if len(frame) < MIN_CHAMPION_ROWS:
        raise InsufficientDataError(
            f"Solo hay {len(frame)} partidas de {champion} en el historico local; "
            f"se requieren al menos {MIN_CHAMPION_ROWS}. Ingesta mas partidas de la "
            f"liga con --mode ingest-ladder --target-tier {tier.upper()}."
        )
    patch = current_patch(ddragon, frame)
    result: dict = {"champion": champion, "tier": tier.upper(), "patch": patch,
                    "n_samples": int(len(frame)), "skipped": [], "item": None}

    if not force and not registry.needs_training("item", champion, tier, patch):
        result["skipped"].append("item")
        logger.info(
            "Modelo de items de %s (%s) vigente para el parche %s: se reutiliza.",
            champion, tier.upper(), patch,
        )
    else:
        result["item"] = train_champion_item_model(frame, champion, tier, patch, registry)
    return result

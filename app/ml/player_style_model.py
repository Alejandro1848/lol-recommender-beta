"""Clasificacion de estilo de jugador: agresivo / defensivo / neutral.

Con muestra suficiente (>=15 filas) usa KMeans (k=3) sobre metricas de
agresion normalizadas y etiqueta los clusters por su centroide. Con
muestra chica cae a un heuristico transparente por umbrales. En ambos
casos devuelve la evidencia usada.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STYLE_FEATURES = ["aggression", "risk", "damage_share"]
MIN_KMEANS_SAMPLE = 15


def _style_frame(df: pd.DataFrame) -> pd.DataFrame:
    minutes = df["minutes"].replace(0, np.nan)
    out = pd.DataFrame(index=df.index)
    out["aggression"] = (df["kills"].fillna(0) + df["assists"].fillna(0)) / minutes
    out["risk"] = df["deaths"].fillna(0) / minutes
    out["damage_share"] = df["damageShare"].fillna(df["damageShare"].median())
    return out.dropna()


def classify_style(player_df: pd.DataFrame) -> dict:
    """Estilo del jugador con evidencia. Devuelve 'sin datos' si no hay muestra."""
    if player_df is None or player_df.empty:
        return {"style": None, "label": "Sin datos suficientes", "games": 0, "method": None}

    recent = player_df.sort_values("gameCreation", ascending=False).head(20)
    styles = _style_frame(recent)
    if styles.empty:
        return {"style": None, "label": "Sin datos suficientes", "games": 0, "method": None}

    aggression = float(styles["aggression"].mean())
    risk = float(styles["risk"].mean())
    damage_share = float(styles["damage_share"].mean())

    method = "heuristico"
    if len(styles) >= MIN_KMEANS_SAMPLE:
        try:
            from sklearn.cluster import KMeans
            from sklearn.preprocessing import StandardScaler

            X = StandardScaler().fit_transform(styles[STYLE_FEATURES])
            kmeans = KMeans(n_clusters=3, n_init=10, random_state=42).fit(X)
            centroid_aggr = {
                label: X[kmeans.labels_ == label][:, 0].mean() for label in set(kmeans.labels_)
            }
            ranked = sorted(centroid_aggr, key=centroid_aggr.get)
            cluster_names = {ranked[0]: "defensivo", ranked[1]: "neutral", ranked[2]: "agresivo"}
            counts = pd.Series(kmeans.labels_).value_counts()
            style = cluster_names[counts.idxmax()]
            method = "kmeans"
        except Exception:
            style = _threshold_style(aggression, risk)
    else:
        style = _threshold_style(aggression, risk)

    labels = {"agresivo": "Agresivo", "defensivo": "Defensivo", "neutral": "Neutral"}
    return {
        "style": style,
        "label": labels[style],
        "games": int(len(styles)),
        "method": method,
        "evidence": {
            "kills_assists_por_min": round(aggression, 2),
            "muertes_por_min": round(risk, 2),
            "damage_share": round(damage_share, 3),
        },
    }


def _threshold_style(aggression: float, risk: float) -> str:
    # Umbrales tipicos: ~0.55 K+A/min y ~0.20 muertes/min separan perfiles.
    if aggression >= 0.55 and risk >= 0.18:
        return "agresivo"
    if aggression < 0.40 and risk < 0.15:
        return "defensivo"
    if risk >= 0.25:
        return "agresivo"
    return "neutral"

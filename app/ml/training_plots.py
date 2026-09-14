"""Plots de validacion para los entrenamientos por campeon (matplotlib).

Genera PNGs estaticos que documentan la comparacion de algoritmos:
- barras comparativas de ranking de items (hit@3 / precision@3 / recall@3);
- barras comparativas del modelo in-game y su AUC por tramo de minutos.

El color identifica al algoritmo de forma fija (nunca por posicion) y los
valores van etiquetados directamente sobre las barras.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

# Paleta categorica validada (CVD ΔE adyacente >= 21): un color por algoritmo.
ALGO_COLORS = {
    "logistic_regression": "#2a78d6",
    "lightgbm": "#1baf7a",
    "xgboost": "#eda100",
}
FALLBACK_COLOR = "#4a3aa7"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"

ALGO_LABELS = {
    "logistic_regression": "Regresion logistica",
    "lightgbm": "LightGBM",
    "xgboost": "XGBoost",
}

ROUND_LABELS = {
    "live": "estado de partida",
}


def _algo_color(algorithm: str) -> str:
    return ALGO_COLORS.get(algorithm, FALLBACK_COLOR)


def _algo_label(algorithm: str) -> str:
    return ALGO_LABELS.get(algorithm, algorithm)


def _style_axis(ax) -> None:
    ax.set_facecolor(SURFACE)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(BASELINE)
        ax.spines[spine].set_linewidth(1)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color=GRIDLINE, linewidth=1)
    ax.set_axisbelow(True)


def _metric_bars(ax, entries: list[dict], metric: str, higher_is_better: bool) -> None:
    """Barras de una metrica, agrupadas por ronda; el color fijo identifica al
    algoritmo (la leyenda va a nivel figura)."""
    _style_axis(ax)
    rounds = list(dict.fromkeys(entry.get("round", "") for entry in entries))
    algorithms = list(dict.fromkeys(entry["algorithm"] for entry in entries))
    values = {
        (entry.get("round", ""), entry["algorithm"]): entry["metrics"].get(metric)
        for entry in entries
    }
    plotted = [v for v in values.values() if v is not None]
    if not plotted:
        ax.set_axis_off()
        return
    width = 0.26
    for g, round_name in enumerate(rounds):
        for i, algorithm in enumerate(algorithms):
            value = values.get((round_name, algorithm))
            if value is None:
                continue
            x = g + (i - (len(algorithms) - 1) / 2) * (width + 0.04)
            ax.bar(x, float(value), width=width, color=_algo_color(algorithm), edgecolor="none")
            ax.annotate(
                f"{float(value):.3f}", (x, float(value)), textcoords="offset points",
                xytext=(0, 4), ha="center", fontsize=8.5, color=INK,
            )
    ax.set_xticks(range(len(rounds)))
    ax.set_xticklabels(
        [ROUND_LABELS.get(r, r) or "base" for r in rounds],
        fontsize=9.5, color=SECONDARY,
    )
    ax.set_xlim(-0.6, len(rounds) - 0.4)
    ax.set_ylim(0, max(0.05, max(plotted)) * 1.2)
    arrow = "mas alto mejor" if higher_is_better else "mas bajo mejor"
    ax.set_title(f"{metric} ({arrow})", color=SECONDARY, fontsize=10.5, pad=8)


def plot_auc_by_minute(
    buckets: dict[str, dict[str, float]],
    path: Path,
    title: str,
) -> Path:
    """AUC de test por tramo de minuto para cada algoritmo del modelo in-game.

    buckets: {algoritmo: {etiqueta_tramo: auc}}
    """
    fig, ax = plt.subplots(figsize=(8.4, 5.2), facecolor=SURFACE)
    _style_axis(ax)
    labels = None
    for algorithm, series in buckets.items():
        labels = list(series.keys())
        values = [series[k] for k in labels]
        x = np.arange(len(labels))
        ax.plot(
            x, values, color=_algo_color(algorithm), linewidth=2, marker="o",
            markersize=6, markeredgecolor=SURFACE, markeredgewidth=2,
            solid_joinstyle="round", solid_capstyle="round",
            label=_algo_label(algorithm),
        )
    if labels:
        ax.set_xticks(np.arange(len(labels)))
        ax.set_xticklabels(labels, fontsize=9.5, color=SECONDARY)
    ax.axhline(0.5, color=BASELINE, linewidth=1)
    ax.set_ylim(0.45, 1.0)
    ax.set_xlabel("Minuto de la partida", color=SECONDARY, fontsize=10)
    ax.set_ylabel("ROC-AUC (test)", color=SECONDARY, fontsize=10)
    ax.legend(loc="lower right", frameon=False, fontsize=9.5, labelcolor=SECONDARY)
    fig.suptitle(title, color=INK, fontsize=13, fontweight="bold", x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=144, facecolor=SURFACE)
    plt.close(fig)
    return path


def plot_metric_comparison(
    rows: list[dict],
    metrics: list[tuple[str, bool]],
    path: Path,
    title: str,
) -> Path:
    """Grid de barras: filas = split (validacion/test), columnas = metrica.

    rows: [{algorithm, round, validation: {...}, test: {...}}, ...]
    metrics: [(nombre_metrica, mas_alto_es_mejor), ...]
    """
    splits = [("validation", "Validacion"), ("test", "Test")]
    fig, axes = plt.subplots(
        len(splits), len(metrics),
        figsize=(4.4 * len(metrics), 3.6 * len(splits)),
        facecolor=SURFACE, squeeze=False,
    )
    for i, (split_key, split_label) in enumerate(splits):
        for j, (metric, higher_is_better) in enumerate(metrics):
            entries = [
                {
                    "algorithm": row["algorithm"],
                    "round": row.get("round", ""),
                    "metrics": row.get(split_key) or {},
                }
                for row in rows
            ]
            _metric_bars(axes[i][j], entries, metric, higher_is_better)
            if j == 0:
                axes[i][j].set_ylabel(split_label, color=INK, fontsize=11)
    algorithms = list(dict.fromkeys(row["algorithm"] for row in rows))
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=_algo_color(a), label=_algo_label(a))
        for a in algorithms
    ]
    fig.legend(
        handles=handles, loc="upper right", ncol=len(handles), frameon=False,
        fontsize=9.5, labelcolor=SECONDARY, bbox_to_anchor=(0.99, 0.99),
    )
    fig.suptitle(title, color=INK, fontsize=13, fontweight="bold", x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=144, facecolor=SURFACE)
    plt.close(fig)
    return path

"""Evaluacion de modelos: metricas estandar + comparacion contra baseline."""
from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    confusion_matrix,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)


class InsufficientDataError(RuntimeError):
    """No hay datos suficientes para entrenar con garantias minimas."""


def evaluate_probabilities(y_true, y_prob) -> dict:
    """Metricas de un vector de probabilidades. Robusto a clases unicas."""
    y_true = np.asarray(y_true, dtype=int)
    y_prob = np.asarray(y_prob, dtype=float).clip(1e-6, 1 - 1e-6)
    y_pred = (y_prob >= 0.5).astype(int)

    metrics = {
        "n": int(len(y_true)),
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 3),
        "brier_score": round(float(brier_score_loss(y_true, y_prob)), 4),
    }
    if len(np.unique(y_true)) > 1:
        metrics["precision"] = round(float(precision_score(y_true, y_pred, zero_division=0)), 3)
        metrics["recall"] = round(float(recall_score(y_true, y_pred, zero_division=0)), 3)
        metrics["roc_auc"] = round(float(roc_auc_score(y_true, y_prob)), 3)
        metrics["log_loss"] = round(float(log_loss(y_true, y_prob)), 4)
        metrics["confusion_matrix"] = confusion_matrix(y_true, y_pred).tolist()
    else:
        metrics["warning"] = "El set de prueba tiene una sola clase; AUC/logloss no aplican."
    return metrics


def compare_to_baseline(y_true, model_prob, baseline_prob: float = 0.5) -> dict:
    """Compara el modelo contra el baseline heuristico (50% pregame)."""
    baseline = np.full(len(np.asarray(y_true)), baseline_prob)
    return {
        "model": evaluate_probabilities(y_true, model_prob),
        "baseline": evaluate_probabilities(y_true, baseline),
    }

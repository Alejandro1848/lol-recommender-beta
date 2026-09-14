"""Servicio de inferencia: probabilidad de victoria en vivo.

Diseno (decidido tras medir): la prediccion PREGAME en soloQ ronda
AUC ~0.53 — practicamente una moneda — asi que NO se usa ningun modelo
pregame. La partida arranca con un 50% fijo y honesto, y el numero lo
construye el MODELO IN-GAME entrenado con timelines (AUC 0.66 al minuto
4-9, 0.88 al 25-30) conforme llegan senales reales: kills, niveles, CS,
torres, dragones y barones.

Mezcla: P = (1 - w) * 0.5  +  w * P_in_game
donde w crece con el minuto hasta MAX_LIVE_WEIGHT. El 50% fijo actua de
regularizador en el early (cuando el estado aun dice poco) y desaparece
casi por completo en el mid-late game.

Fallback: sin modelo in-game entrenado se usa el heuristico explicable
de ml/baseline.py; sin senales en vivo, la probabilidad es 50% y se
avisa en warnings.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from app.ml.baseline import live_state_probability
from app.ml.champion_registry import ChampionModelRegistry

logger = logging.getLogger(__name__)

# El 50% inicial: sin senales de la partida no se promete nada.
PREGAME_PROBABILITY = 0.5

# El modelo in-game domina rapido porque es el unico con poder predictivo
# real: peso maximo 90% alcanzado al minuto 20.
LIVE_WEIGHT_FULL_AT_SECONDS = 1200  # 20 min
MAX_LIVE_WEIGHT = 0.90


class InferenceService:
    def __init__(
        self,
        champion_registry: ChampionModelRegistry | None = None,
        tier: str | None = None,
    ):
        self.champion_registry = champion_registry
        self.tier = tier

    def _live_probability(self, snapshot: dict) -> dict[str, Any] | None:
        """Probabilidad segun el ESTADO de la partida con el modelo in-game
        entrenado con timelines; None si no hay modelo (se usa el heuristico)."""
        if self.champion_registry is None or not self.tier:
            return None
        champion = (snapshot.get("me") or {}).get("champion")
        if not champion:
            return None
        loaded = self.champion_registry.latest("live", champion, self.tier)
        if loaded is None:
            return None
        payload, meta = loaded
        if not isinstance(payload, dict) or "model" not in payload:
            return None
        from app.ml.train_live_model import (
            LIVE_FEATURE_COLUMNS,
            live_features_from_snapshot,
        )

        features = live_features_from_snapshot(snapshot)
        if features is None:
            return None
        try:
            columns = payload.get("feature_columns") or LIVE_FEATURE_COLUMNS
            X = pd.DataFrame([features])[columns].astype(float).fillna(0.0)
            probability = float(payload["model"].predict_proba(X)[:, 1][0])
        except Exception as exc:
            logger.warning("Modelo in-game no utilizable: %s", exc)
            return None
        factors = [
            {"name": name.replace("_", " "), "value": round(float(value), 2)}
            for name, value in features.items()
            if name != "minute" and abs(value) > 0
        ]
        return {
            "probability": round(probability, 3),
            "factors": factors[:4],
            "method": f"modelo_live_{meta.get('algorithm', 'desconocido')}",
            "meta": meta,
        }

    def win_probability(self, snapshot: dict | None) -> dict[str, Any]:
        """Probabilidad combinada + confianza + advertencias."""
        warnings: list[str] = []
        if snapshot is None:
            return {
                "probability": None,
                "confidence": "baja",
                "method": "sin_datos",
                "top_factors": [],
                "warnings": ["No hay partida activa detectada por el Live Client."],
                "data_source": "mixto",
            }

        live = self._live_probability(snapshot)
        trained_live = live is not None
        if live is None:
            live = live_state_probability(snapshot)
            warnings.append(
                "Sin modelo in-game entrenado para este campeon/liga: se usa "
                "el heuristico. Ejecuta --mode ingest-timelines y --mode train-champion."
            )

        game_time = snapshot.get("game_time_seconds") or 0
        live_weight = min(
            MAX_LIVE_WEIGHT, (game_time / LIVE_WEIGHT_FULL_AT_SECONDS) * MAX_LIVE_WEIGHT
        )
        # Snapshots sin senales en vivo (p. ej. Spectator-V5 no expone
        # kills/oro): mezclar con un estado "todo en cero" seria mentir.
        if not snapshot.get("live_signals_available", True):
            live_weight = 0.0
            warnings.append(
                "Sin senales en vivo (kills/torres no disponibles fuera del cliente): "
                "la probabilidad se mantiene en 50% hasta tener datos reales."
            )
        combined = (1 - live_weight) * PREGAME_PROBABILITY + live_weight * live["probability"]

        # Confianza: depende de cuanta informacion real hay, no de promesas.
        if live_weight == 0.0:
            confidence = "baja"
        elif not trained_live:
            confidence = "baja"
        elif live_weight < 0.45:
            confidence = "media"
        else:
            confidence = "alta"

        factors = [{"source": "estado_en_vivo", **f} for f in live.get("factors", [])]

        return {
            "probability": round(float(combined), 3),
            "confidence": confidence,
            "method": f"baseline_50 + {live.get('method', 'baseline_live')} (peso live {live_weight:.0%})",
            "top_factors": factors[:6],
            "warnings": warnings,
            "data_source": "mixto",
        }

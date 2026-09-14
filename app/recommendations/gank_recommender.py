"""Recomendador de lineas para gank: fusiona senal en vivo + historica."""
from __future__ import annotations

import pandas as pd

from app.analytics.lane_priority import (
    GANKABLE_ROLES,
    historical_lane_signals,
    live_lane_signals,
)
from app.data.normalizers import ROLE_LABELS_ES
from app.recommendations import explanation_builder as xp


def recommend_gank_lanes(
    snapshot: dict | None, participants_with_opp: pd.DataFrame
) -> list[dict]:
    if snapshot is None:
        return [{
            "kind": "gank",
            "title": "Sin datos en vivo",
            "detail": "Live Client no disponible",
            "explanation": (
                "Para priorizar lineas de gank se necesita la partida en vivo "
                "(Live Client Data API). Abre una partida y vuelve a consultar."
            ),
            "confidence": "baja",
            "data_source": "live_client",
            "extra": {},
        }]

    live = live_lane_signals(snapshot)
    historical = historical_lane_signals(participants_with_opp, snapshot)

    scored = []
    for role in GANKABLE_ROLES:
        live_signal = live.get(role)
        hist_signal = historical.get(role)
        if not live_signal and not hist_signal:
            continue
        score = (live_signal or {}).get("score", 0) + (hist_signal or {}).get("score", 0)
        reasons = list((live_signal or {}).get("reasons", []))
        if hist_signal:
            reasons.append(hist_signal["reason"])
        scored.append({
            "role": role,
            "score": round(score, 2),
            "reasons": reasons,
            "ally": (live_signal or {}).get("ally_champion"),
            "enemy": (live_signal or {}).get("enemy_champion"),
            "target_note": (live_signal or {}).get("target_note"),
            "hist_games": (hist_signal or {}).get("games", 0),
        })

    if not scored:
        return [{
            "kind": "gank",
            "title": "Sin lineas evaluables",
            "detail": "No se pudieron mapear las posiciones",
            "explanation": (
                "La Live Client API no reporto posiciones utilizables para "
                "comparar carriles (comun en colas no ranked). No hay "
                "recomendacion fundamentada."
            ),
            "confidence": "baja",
            "data_source": "live_client",
            "extra": {},
        }]

    scored.sort(key=lambda lane: lane["score"], reverse=True)
    output = []
    for rank, lane in enumerate(scored, start=1):
        label = ROLE_LABELS_ES.get(lane["role"], lane["role"])
        reasons_text = "; ".join(lane["reasons"]) if lane["reasons"] else "sin senales fuertes"
        target_bit = f" - objetivo: {lane['enemy']}" if lane.get("enemy") else ""
        output.append({
            "kind": "gank",
            "title": f"{rank}. Gank hacia {label}",
            "detail": (
                f"{lane['ally'] or '?'} vs {lane['enemy'] or '?'} "
                f"(score {lane['score']:+.1f}){target_bit}"
            ),
            "explanation": xp.compose(
                f"Prioridad {rank} para gankear {label} porque {reasons_text}",
                xp.small_sample_warning(lane["hist_games"]) if lane["hist_games"] else None,
                "Es una sugerencia de prioridad, no una orden: evalua vision y estado del mapa",
            ),
            "confidence": "media" if lane["score"] >= 1 else "baja",
            "sample_size": lane["hist_games"] or None,
            "data_source": "mixto",
            "extra": {
                "role": lane["role"],
                "score": lane["score"],
                "ally": lane.get("ally"),
                "enemy": lane.get("enemy"),
                "target_note": lane.get("target_note"),
            },
        })
    return output[:3]

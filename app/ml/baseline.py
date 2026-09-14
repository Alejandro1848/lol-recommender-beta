"""Baselines heuristicos, explicables y sin entrenamiento.

Dos usos:
1. Comparador en evaluacion (todo modelo debe ganarle al baseline).
2. Senal de estado en vivo: la Live Client API no expone oro enemigo,
   asi que se usan diferencias observables (kills, torres, dragones,
   niveles) pasadas por una logistica calibrada a mano y conservadora.
"""
from __future__ import annotations

import math
from typing import Any


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def pregame_baseline_probability() -> float:
    """Sin informacion previa, 50%. Honesto por definicion."""
    return 0.5


def live_state_probability(snapshot: dict) -> dict[str, Any]:
    """P(victoria) heuristica desde el snapshot en vivo normalizado.

    Pesos conservadores: una ventaja de 5 kills mueve ~12 puntos, no 40.
    Devuelve tambien los factores para poder explicar el numero.
    """
    me = snapshot.get("me") or {}
    my_team = snapshot.get("my_team")
    allies = snapshot.get("allies", []) + ([me] if me else [])
    enemies = snapshot.get("enemies", [])
    events = snapshot.get("events_summary") or {}

    def team_sum(players, key):
        return sum(p.get(key, 0) or 0 for p in players)

    kill_diff = team_sum(allies, "kills") - team_sum(enemies, "kills")
    level_diff = team_sum(allies, "level") - team_sum(enemies, "level")
    cs_diff = (team_sum(allies, "creep_score") or 0) - (team_sum(enemies, "creep_score") or 0)

    my_side = events.get(my_team, {}) if my_team else {}
    enemy_side_key = "CHAOS" if my_team == "ORDER" else "ORDER"
    enemy_side = events.get(enemy_side_key, {})
    turret_diff = my_side.get("turrets", 0) - enemy_side.get("turrets", 0)
    dragon_diff = my_side.get("dragons", 0) - enemy_side.get("dragons", 0)
    baron_diff = my_side.get("barons", 0) - enemy_side.get("barons", 0)

    factors = [
        ("diferencia de kills", kill_diff, 0.05),
        ("diferencia de niveles", level_diff, 0.03),
        ("diferencia de CS", cs_diff / 50.0, 0.04),
        ("diferencia de torres", turret_diff, 0.12),
        ("diferencia de dragones", dragon_diff, 0.10),
        ("diferencia de barones", baron_diff, 0.25),
    ]
    score = sum(value * weight for _, value, weight in factors)
    probability = _sigmoid(score)

    return {
        "probability": round(probability, 3),
        "factors": [
            {"name": name, "value": round(float(value), 2), "contribution": round(value * weight, 3)}
            for name, value, weight in factors
            if abs(value * weight) > 0.001
        ],
        "method": "baseline_live_heuristico",
    }

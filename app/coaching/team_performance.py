"""Desempeno in-game por jugador con las senales que el Live Client SI expone.

Para cada jugador (aliado o enemigo) se calcula un indice de desempeno a
partir de kills/deaths/assists, CS por minuto contra lo esperado para su
rol y nivel contra el promedio de la partida. El indice alimenta al coach:
quien es la amenaza enemiga, quien es la win condition aliada y como va
cada linea.

Tambien estima la diferencia de oro entre equipos a partir de senales
observables (kills, CS, torres, dragones): el oro enemigo real NO esta
disponible fuera del cliente y por eso el resultado se etiqueta siempre
como estimacion.
"""
from __future__ import annotations

from typing import Any

from app.data.normalizers import ROLE_LABELS_ES

# CS/min tipico por rol en soloQ (referencia conservadora).
EXPECTED_CS_PER_MIN = {
    "TOP": 6.5, "MIDDLE": 7.0, "BOTTOM": 7.5, "JUNGLE": 5.5, "UTILITY": 1.2,
}

# Valores medios de oro por senal observable (aproximaciones publicas del juego).
GOLD_PER_KILL = 300
GOLD_PER_CS = 21
GOLD_PER_TURRET = 550     # placas + torre + oro global, promediado
GOLD_PER_DRAGON = 25      # oro directo bajo; el valor real es el buff
GOLD_PER_GRUB = 75

FED_THRESHOLD = 1.5
STRUGGLING_THRESHOLD = -1.5


def player_performance(player: dict, minute: float) -> dict[str, Any]:
    """Indice de desempeno de un jugador: >0 por encima de lo esperado.

    Componentes (todos observables en el Live Client):
    - kills y assists netos de muertes (peso mayor a kills),
    - CS/min relativo a lo esperado para su rol,
    - los primeros minutos amortiguan el indice (poca informacion).
    """
    kills = player.get("kills") or 0
    deaths = player.get("deaths") or 0
    assists = player.get("assists") or 0
    cs = player.get("creep_score") or 0
    role = player.get("position")

    kda_component = kills * 0.9 + assists * 0.35 - deaths * 0.8

    cs_component = 0.0
    expected = EXPECTED_CS_PER_MIN.get(role or "", None)
    if expected and minute >= 4:
        cs_ratio = (cs / minute) / expected if minute > 0 else 0
        cs_component = max(-1.5, min(1.5, (cs_ratio - 1.0) * 3.0))

    # Antes del minuto 4 casi todo es ruido: se amortigua.
    damping = min(1.0, minute / 8.0)
    score = (kda_component * 0.6 + cs_component) * damping

    return {
        "riot_id": player.get("riot_id"),
        "champion": player.get("champion"),
        "position": role,
        "position_label": ROLE_LABELS_ES.get(role or "", role),
        "kda": f"{kills}/{deaths}/{assists}",
        "score": round(score, 2),
        "fed": score >= FED_THRESHOLD,
        "struggling": score <= STRUGGLING_THRESHOLD,
    }


def team_performance_summary(snapshot: dict) -> dict[str, Any]:
    """Resumen de desempeno de ambos equipos para el coach.

    Devuelve:
      allies / enemies: lista ordenada (mejor primero) de indices por jugador,
      top_threat: enemigo con mejor desempeno (None si nadie destaca),
      win_condition: aliado con mejor desempeno (incluyendote),
      lane_status: por rol, diferencia aliado - enemigo del mismo carril.
    """
    minute = (snapshot.get("game_time_seconds") or 0) / 60.0
    me = snapshot.get("me") or {}
    allies = list(snapshot.get("allies") or []) + ([me] if me else [])
    enemies = list(snapshot.get("enemies") or [])

    ally_perf = sorted(
        (player_performance(p, minute) for p in allies),
        key=lambda x: x["score"], reverse=True,
    )
    enemy_perf = sorted(
        (player_performance(p, minute) for p in enemies),
        key=lambda x: x["score"], reverse=True,
    )

    top_threat = enemy_perf[0] if enemy_perf and enemy_perf[0]["score"] >= 1.0 else None
    win_condition = ally_perf[0] if ally_perf and ally_perf[0]["score"] >= 1.0 else None

    enemy_by_role = {p["position"]: p for p in enemy_perf if p.get("position")}
    lane_status = {}
    for perf in ally_perf:
        role = perf.get("position")
        if not role:
            continue
        rival = enemy_by_role.get(role)
        diff = perf["score"] - (rival["score"] if rival else 0.0)
        lane_status[role] = {
            "ally": perf["champion"],
            "enemy": rival["champion"] if rival else None,
            "diff": round(diff, 2),
        }

    return {
        "allies": ally_perf,
        "enemies": enemy_perf,
        "top_threat": top_threat,
        "win_condition": win_condition,
        "lane_status": lane_status,
    }


def estimate_gold_diff(snapshot: dict) -> dict[str, Any] | None:
    """Diferencia de oro estimada mi equipo - enemigo desde senales visibles.

    El Live Client no expone el oro del rival: esto es una ESTIMACION a
    partir de kills, CS, torres y objetivos, y se etiqueta como tal.
    """
    me = snapshot.get("me") or {}
    allies = list(snapshot.get("allies") or []) + ([me] if me else [])
    enemies = list(snapshot.get("enemies") or [])
    if not allies or not enemies:
        return None

    def team_sum(players, key):
        return sum(p.get(key, 0) or 0 for p in players)

    events = snapshot.get("events_summary") or {}
    my_team = snapshot.get("my_team")
    my_side = events.get(my_team, {}) if my_team else {}
    enemy_side = events.get("CHAOS" if my_team == "ORDER" else "ORDER", {})

    diff = (
        (team_sum(allies, "kills") - team_sum(enemies, "kills")) * GOLD_PER_KILL
        + (team_sum(allies, "creep_score") - team_sum(enemies, "creep_score")) * GOLD_PER_CS
        + ((my_side.get("turrets") or 0) - (enemy_side.get("turrets") or 0)) * GOLD_PER_TURRET
        + ((my_side.get("dragons") or 0) - (enemy_side.get("dragons") or 0)) * GOLD_PER_DRAGON
        + ((my_side.get("grubs") or 0) - (enemy_side.get("grubs") or 0)) * GOLD_PER_GRUB
    )
    return {
        "gold_diff": int(round(diff)),
        "label": _gold_label(diff),
        "is_estimate": True,
        "basis": "kills, CS, torres y objetivos observados (el oro enemigo real no es visible)",
    }


def _gold_label(diff: float) -> str:
    magnitude = abs(diff)
    if magnitude < 800:
        return "partida pareja"
    sign = "+" if diff > 0 else "-"
    if magnitude >= 1000:
        return f"{sign}{magnitude / 1000:.1f}k de oro estimado"
    return f"{sign}{int(magnitude)} de oro estimado"

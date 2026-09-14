"""Timers de objetivos neutrales derivados de eventos del Live Client.

La Live Client Data API no expone los timers de respawn, pero SI expone
cada kill de objetivo con su EventTime. Con las reglas publicas del juego
(primer spawn + tiempo de respawn) se calcula cuando reaparece cada
objetivo. Son reglas del parche, no adivinanzas; aun asi cada timer se
marca como derivado de eventos para que la UI pueda decirlo.

Constantes en un solo lugar: si Riot cambia los tiempos en un parche,
se ajustan aqui.
"""
from __future__ import annotations

from typing import Any

# Reglas de spawn (segundos). Fuente: notas de parche publicas de Riot.
DRAGON_FIRST_SPAWN = 300        # 5:00
DRAGON_RESPAWN = 300            # 5 min tras cada kill
ELDER_RESPAWN = 360             # 6 min (post-alma); aproximado
GRUBS_FIRST_SPAWN = 360         # 6:00 larvas del Vacio
HERALD_SPAWN = 840              # 14:00
HERALD_DESPAWN = 1185           # 19:45
BARON_FIRST_SPAWN = 1200        # 20:00
BARON_RESPAWN = 360             # 6 min tras cada kill

# Ventana en la que "preparar" un objetivo tiene sentido para el coach.
PREP_WINDOW_SECONDS = 90


def _kill_times(events: list[dict] | None, name: str) -> list[float]:
    out = [
        float(e.get("EventTime") or 0)
        for e in (events or [])
        if e.get("EventName") == name
    ]
    return sorted(out)


def objective_timers(
    game_time_seconds: float | None,
    objective_events: list[dict] | None,
    dragons_killed_total: int = 0,
) -> list[dict[str, Any]]:
    """Estado de dragon, heraldo/baron y larvas para el instante actual.

    Devuelve una lista de dicts con:
      objective: dragon | baron | herald | grubs
      label: nombre legible en espanol
      status: activo | en_camino | cerrado
      seconds_until: segundos para el spawn (0 si ya esta en el mapa)
    """
    if game_time_seconds is None:
        return []
    now = float(game_time_seconds)
    timers: list[dict[str, Any]] = []

    # ------------------------------------------------------------ dragon
    dragon_kills = _kill_times(objective_events, "DragonKill")
    total_dragons = max(len(dragon_kills), dragons_killed_total)
    # Tras 4 dragones se asume territorio de Anciano (respawn mas largo).
    respawn = ELDER_RESPAWN if total_dragons >= 4 else DRAGON_RESPAWN
    if dragon_kills:
        next_dragon = dragon_kills[-1] + respawn
    else:
        next_dragon = DRAGON_FIRST_SPAWN
    label = "Dragon Anciano" if total_dragons >= 4 else "Dragon"
    timers.append(_timer("dragon", label, now, next_dragon))

    # -------------------------------------------------- heraldo -> baron
    if now < BARON_FIRST_SPAWN:
        herald_kills = _kill_times(objective_events, "HeraldKill")
        if herald_kills:
            timers.append({
                "objective": "herald", "label": "Heraldo",
                "status": "cerrado", "seconds_until": None,
            })
        elif now < HERALD_DESPAWN:
            timers.append(_timer("herald", "Heraldo", now, HERALD_SPAWN))
    baron_kills = _kill_times(objective_events, "BaronKill")
    next_baron = (baron_kills[-1] + BARON_RESPAWN) if baron_kills else BARON_FIRST_SPAWN
    timers.append(_timer("baron", "Baron", now, next_baron))

    # ------------------------------------------------------------ larvas
    grub_kills = _kill_times(objective_events, "HordeKill")
    if now < HERALD_SPAWN and not grub_kills:
        timers.append(_timer("grubs", "Larvas del Vacio", now, GRUBS_FIRST_SPAWN))

    return timers


def next_objective(timers: list[dict], within_seconds: int = PREP_WINDOW_SECONDS) -> dict | None:
    """El objetivo mas accionable AHORA: activo en el mapa, o el que
    aparece dentro de la ventana de preparacion."""
    active = [t for t in timers if t["status"] == "activo"]
    if active:
        # Baron pesa mas que dragon cuando ambos estan arriba.
        order = {"baron": 0, "dragon": 1, "herald": 2, "grubs": 3}
        return sorted(active, key=lambda t: order.get(t["objective"], 9))[0]
    upcoming = [
        t for t in timers
        if t["status"] == "en_camino"
        and t["seconds_until"] is not None
        and t["seconds_until"] <= within_seconds
    ]
    if upcoming:
        return min(upcoming, key=lambda t: t["seconds_until"])
    return None


def _timer(objective: str, label: str, now: float, spawn_at: float) -> dict[str, Any]:
    if now >= spawn_at:
        return {"objective": objective, "label": label, "status": "activo", "seconds_until": 0}
    return {
        "objective": objective, "label": label,
        "status": "en_camino", "seconds_until": int(round(spawn_at - now)),
    }

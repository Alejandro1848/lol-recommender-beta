"""Recomendador de objetivos neutrales segun tiempo de juego y estado.

Usa ventanas del juego (dragones y larvas desde juego temprano, heraldo en
medio juego, baron desde 20:00) + el marcador observado en eventos del
Live Client. No conoce timers exactos de respawn (la API no los expone):
lo dice en la explicacion.
"""
from __future__ import annotations

import pandas as pd

from app.analytics.objective_priority import objective_priority
from app.recommendations import explanation_builder as xp


def recommend_objectives(snapshot: dict | None, teams: pd.DataFrame | None = None) -> list[dict]:
    if snapshot is None:
        return [{
            "kind": "objetivo",
            "title": "Sin partida activa",
            "detail": "Live Client no disponible",
            "explanation": "Los objetivos se recomiendan con la partida en vivo abierta.",
            "confidence": "baja",
            "data_source": "live_client",
            "extra": {},
        }]

    priorities = objective_priority(snapshot, teams if teams is not None else pd.DataFrame())
    if priorities:
        recs = []
        for rank, row in enumerate(priorities[:3], start=1):
            confidence = "alta" if row["success_probability"] >= 0.62 else (
                "media" if row["success_probability"] >= 0.52 else "baja"
            )
            recs.append({
                "kind": "objetivo",
                "title": f"{rank}. Prioriza {row['label']}",
                "detail": f"Exito estimado {row['success_probability']:.0%} - {row['window']}",
                "explanation": xp.compose(
                    f"{row['label']} sale como opcion #{rank} por probabilidad historica ajustada al estado actual",
                    "; ".join(row["reasons"][:3]),
                    "Confirma vision, prioridad de lineas y posicion del jungla antes de iniciar",
                ),
                "confidence": confidence,
                "sample_size": row.get("sample_size") or None,
                "data_source": "mixto",
                "extra": row,
            })
        return recs

    game_time = snapshot.get("game_time_seconds") or 0
    minutes = game_time / 60
    events = snapshot.get("events_summary") or {}
    my_team = snapshot.get("my_team")
    enemy_key = "CHAOS" if my_team == "ORDER" else "ORDER"
    mine = events.get(my_team, {}) if my_team else {}
    theirs = events.get(enemy_key, {})

    my_dragons = mine.get("dragons", 0)
    their_dragons = theirs.get("dragons", 0)

    recs = []

    if minutes < 5:
        recs.append(_rec(
            "Farmea y controla vision temprana",
            "Aun no hay objetivos neutrales mayores",
            "Antes del minuto 5 no hay dragon; prioriza CS, nivel 2/3 antes que el rival y vision de rio.",
            "media",
        ))
    if 5 <= minutes:
        if minutes < 14:
            recs.append(_rec(
                "Ventana de larvas del Vacio",
                "Objetivo temprano del lado superior",
                "Las larvas compiten con dragon por tempo temprano. Si top/mid pueden rotar y "
                "el jungla rival esta lejos o sin prioridad, suelen ser una buena inversion para "
                "presionar torres despues.",
                "media",
            ))
        if my_dragons >= 3:
            recs.append(_rec(
                "Prepara el alma de dragon",
                f"Llevas {my_dragons} dragones",
                f"Tu equipo lleva {my_dragons} dragones: el siguiente puede dar punto de alma. "
                "Coloca vision con antelacion y llega con prioridad de mid/bot.",
                "media",
            ))
        elif their_dragons >= 3:
            recs.append(_rec(
                "Niega el alma enemiga",
                f"El rival lleva {their_dragons} dragones",
                f"El equipo enemigo lleva {their_dragons} dragones y amenaza alma: "
                "disputa o intercambia por heraldo/torres en el lado opuesto.",
                "media",
            ))
        else:
            recs.append(_rec(
                "Dragon disponible por ventana de tiempo",
                f"Minuto {minutes:.0f}: los dragones ya estan activos",
                "Los dragones aparecen desde el minuto 5. La Live Client API no expone el timer "
                "exacto de respawn, asi que confirma en el mapa antes de comprometerte.",
                "baja",
            ))
    if 14 <= minutes < 20:
        recs.append(_rec(
            "Ventana de Heraldo",
            "El Heraldo esta activo entre 14:00 y 19:45",
            "Un heraldo bien usado acelera torres y oro de plates. Buena opcion si tienes "
            "prioridad en top o mid.",
            "media",
        ))
    if minutes >= 20:
        advantage = (mine.get("turrets", 0) - theirs.get("turrets", 0)) + (
            mine.get("barons", 0) - theirs.get("barons", 0)
        ) * 2
        if advantage >= 1:
            recs.append(_rec(
                "Presiona con Baron",
                "Vas por delante en objetivos",
                xp.compose(
                    f"Desde el minuto 20 el Baron esta activo y tu equipo lleva ventaja de "
                    f"torres ({mine.get('turrets', 0)} vs {theirs.get('turrets', 0)})",
                    "Fuerza vision alrededor del Baron y castiga si el rival encara",
                ),
                "media",
            ))
        else:
            recs.append(_rec(
                "Baron solo con pick o superioridad numerica",
                "La partida esta cerrada o vas detras",
                "El Baron esta activo pero no llevas ventaja clara: buscarlo a ciegas es "
                "arriesgado. Espera un pick o una superioridad numerica visible.",
                "media",
            ))

    return recs[:3]


def _rec(title: str, detail: str, explanation: str, confidence: str) -> dict:
    return {
        "kind": "objetivo",
        "title": title,
        "detail": detail,
        "explanation": explanation,
        "confidence": confidence,
        "data_source": "live_client",
        "extra": {},
    }

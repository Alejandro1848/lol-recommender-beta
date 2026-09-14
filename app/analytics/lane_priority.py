"""Prioridad de lineas para gank: senales historicas y en vivo por carril.

Devuelve puntuaciones con evidencia separada por fuente. Sin Live Client
solo hay senal historica y se reporta como tal.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.analytics.matchup_analysis import matchup_winrate

GANKABLE_ROLES = ["TOP", "MIDDLE", "BOTTOM"]

# Campeones de linea con escape/dash fiable: el gank exige CC aliado o
# llegar por la espalda. Data Dragon no expone movilidad estructurada, por
# eso esta lista conservadora complementa las clases oficiales.
HIGH_MOBILITY_CHAMPIONS = {
    "Ahri", "Akali", "Akshan", "Camille", "Corki", "Ezreal", "Fiora",
    "Fizz", "Gragas", "Gwen", "Irelia", "Kaisa", "Kassadin", "Katarina",
    "LeBlanc", "Lucian", "Nidalee", "Riven", "Sylas", "Talon", "Tristana",
    "Tryndamere", "Vayne", "Yasuo", "Yone", "Zed", "Zeri",
}

SQUISHY_CLASSES = {"Marksman", "Mage", "Support"}

# Un laner que farmea claramente por encima del ritmo neutro (~7 CS/min en
# linea) esta empujando la oleada y, por tanto, expuesto al gank.
AGGRESSIVE_CS_PER_MIN = 7.5
CS_LEAD_THRESHOLD = 15


def _target_profile(enemy: dict) -> tuple[float, list[str]]:
    """Que tan susceptible al gank es el campeon rival por su tipo."""
    champion = enemy.get("champion") or "el rival"
    tags = set((enemy.get("damage_profile") or {}).get("tags") or [])
    score = 0.0
    reasons: list[str] = []
    if champion in HIGH_MOBILITY_CHAMPIONS:
        score -= 0.6
        reasons.append(
            f"{champion} tiene escapes fiables: gankea solo con CC aliado o cerrando por detras"
        )
    elif tags & SQUISHY_CLASSES:
        label = "tirador" if "Marksman" in tags else ("mago" if "Mage" in tags else "soporte")
        score += 0.5
        reasons.append(f"{champion} es {label} sin gran movilidad: objetivo blando de gank")
    if "Tank" in tags and not tags & {"Marksman", "Mage"}:
        score -= 0.3
        reasons.append(f"{champion} es tanque: matarlo cuesta mucho y rinde poco")
    return score, reasons


def _aggression_signals(
    ally: dict, enemy: dict, minute: float
) -> tuple[float, list[str]]:
    """Agresividad del rival medida con su farmeo (CS): un laner adelantado
    en minions empuja la oleada y queda sobre-extendido para el gank."""
    score = 0.0
    reasons: list[str] = []
    ally_cs, enemy_cs = ally.get("creep_score"), enemy.get("creep_score")
    if enemy_cs is None:
        return score, reasons
    if ally_cs is not None:
        cs_lead = enemy_cs - ally_cs
        if cs_lead >= CS_LEAD_THRESHOLD:
            score += min(0.9, 0.3 + cs_lead / 60.0)
            reasons.append(
                f"su rival lleva +{int(cs_lead)} CS: juega adelantado y suele estar sobre-extendido"
            )
    if minute >= 4:
        cs_per_min = enemy_cs / minute
        if cs_per_min >= AGGRESSIVE_CS_PER_MIN:
            score += 0.3
            reasons.append(
                f"farmea a {cs_per_min:.1f} CS/min: presiona la oleada lejos de su torre"
            )
    return score, reasons


def live_lane_signals(snapshot: dict) -> dict[str, dict[str, Any]]:
    """Senales por carril desde el snapshot en vivo (nivel, muertes, estado,
    farmeo del rival y tipo de campeon objetivo)."""
    signals: dict[str, dict[str, Any]] = {}
    allies = {p.get("position"): p for p in snapshot.get("allies", []) + [snapshot.get("me", {})] if p}
    enemies = {p.get("position"): p for p in snapshot.get("enemies", [])}
    minute = max((snapshot.get("game_time_seconds") or 0) / 60.0, 1.0)

    for role in GANKABLE_ROLES:
        ally, enemy = allies.get(role), enemies.get(role)
        if not ally or not enemy:
            continue
        level_diff = (ally.get("level") or 0) - (enemy.get("level") or 0)
        enemy_deaths = enemy.get("deaths", 0)
        enemy_kills = enemy.get("kills", 0)
        score = 0.0
        reasons = []
        if enemy.get("is_dead"):
            score += 1.5
            reasons.append("el rival de la linea esta muerto ahora mismo (ventana para plates/torre)")
        if level_diff >= 1:
            score += 0.8 * level_diff
            reasons.append(f"tu aliado tiene +{level_diff} nivel(es) sobre su rival")
        elif level_diff <= -1:
            score -= 0.5 * abs(level_diff)
            reasons.append(f"tu aliado va {abs(level_diff)} nivel(es) abajo (gank defensivo, con cuidado)")
        if enemy_kills - enemy_deaths >= 2:
            score += 0.7
            reasons.append("el enemigo de esa linea esta fed: cortarlo tiene alto impacto")
        if enemy_deaths - enemy_kills >= 2:
            score += 0.4
            reasons.append("el enemigo de esa linea ya va perdiendo: es presa facil")

        aggression_score, aggression_reasons = _aggression_signals(ally, enemy, minute)
        score += aggression_score
        reasons.extend(aggression_reasons)

        target_score, target_reasons = _target_profile(enemy)
        score += target_score
        reasons.extend(target_reasons)

        signals[role] = {
            "score": round(score, 2),
            "reasons": reasons,
            "ally_champion": ally.get("champion"),
            "enemy_champion": enemy.get("champion"),
            # Nota corta sobre el objetivo (tipo de campeon o su agresividad)
            # para que el overlay nombre a quien cazar y por que.
            "target_note": (target_reasons + aggression_reasons or [None])[0],
        }
    return signals


def historical_lane_signals(
    participants_with_opp: pd.DataFrame, snapshot: dict | None
) -> dict[str, dict[str, Any]]:
    """Senal historica: winrate del matchup de cada carril en la muestra local."""
    signals: dict[str, dict[str, Any]] = {}
    if snapshot is None or participants_with_opp.empty:
        return signals
    allies = {p.get("position"): p for p in snapshot.get("allies", []) + [snapshot.get("me", {})] if p}
    enemies = {p.get("position"): p for p in snapshot.get("enemies", [])}
    for role in GANKABLE_ROLES:
        ally, enemy = allies.get(role), enemies.get(role)
        if not ally or not enemy:
            continue
        stats = matchup_winrate(
            participants_with_opp, ally.get("champion", ""), enemy.get("champion"), role
        )
        if stats["games"] >= 2 and stats["winrate"] is not None:
            delta = stats["winrate"] - 0.5
            signals[role] = {
                "score": round(delta * 2.0, 2),
                "games": stats["games"],
                "winrate": stats["winrate"],
                "reason": (
                    f"en la muestra local, {ally.get('champion')} vs {enemy.get('champion')} "
                    f"en {role} tiene winrate {stats['winrate']:.0%} ({stats['games']} partidas)"
                ),
            }
    return signals

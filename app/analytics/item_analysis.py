"""Analisis de items: desempeno individual e items defensivos.

Las builds no deben aprender solo de victorias: una derrota con buen KDA,
participacion, dano y economia puede aportar mas senal que una victoria
arrastrada por el equipo. Por eso las frecuencias historicas ponderan partidas
similares con buen desempeno individual, ganadas o perdidas.
"""
from __future__ import annotations

from collections import Counter

import pandas as pd

from app.riot.data_dragon import DataDragon

ITEM_SLOTS = ["item0", "item1", "item2", "item3", "item4", "item5"]

# Ids estables de items defensivos base; los nombres/imagenes se resuelven
# via Data Dragon en runtime (no se hardcodean stats).
MR_ITEM_IDS = [3111, 3065, 3156, 3102, 4401]      # Mercurial, Visage, Maw, Banshee, Force of Nature
ARMOR_ITEM_IDS = [3047, 3143, 3075, 3742, 3026]   # Steelcaps, Randuin, Thornmail, Dead Man's, GA

# Linea de mision de soporte: solo el item inicial se compra al empezar;
# el resto evoluciona gratis al completar la mision. Data Dragon marca las
# evoluciones FINALES como purchasable=True (dato enganoso), asi que se
# excluyen por id explicito: nunca son una compra recomendable.
SUPPORT_QUEST_ITEM_IDS = {3865, 3866, 3867, 3869, 3870, 3871, 3876, 3877}

# Consumibles, trinkets y wards: nunca son una "compra recomendable".
# Complementa al filtro dinamico via Data Dragon (util cuando no hay cache).
NON_BUILD_ITEM_IDS = {
    2003, 2031, 2033, 2052, 2055, 2056, 2138, 2139, 2140, 2150, 2151, 2152,
    3340, 3363, 3364, 3330, 2049, 2010,
} | SUPPORT_QUEST_ITEM_IDS

# Decaimiento de recencia: cada RECENCY_HALF_LIFE partidas hacia atras,
# el peso del registro se reduce a la mitad.
RECENCY_HALF_LIFE = 8


def _num(row: pd.Series, column: str, default: float = 0.0) -> float:
    value = row.get(column, default)
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _performance_score(row: pd.Series) -> float:
    """Score 0-1 aproximado de desempeno individual post-partida.

    Se usa solo para aprender builds historicas, no para predecir winrate en
    vivo. Combina senales disponibles en Match-V5 y evita depender del resultado
    final para que una buena derrota siga contando.
    """
    kda = min(_num(row, "kda") / 6.0, 1.0)
    kp = min(_num(row, "killParticipation"), 1.0)
    damage_share = min(_num(row, "damageShare") / 0.35, 1.0)
    vision = min(_num(row, "visionPerMin") / 2.0, 1.0)
    cs = min(_num(row, "csPerMin") / 8.0, 1.0)
    gold = min(_num(row, "goldPerMin") / 450.0, 1.0)
    role = str(row.get("teamPosition") or "").upper()
    economy = vision if role == "UTILITY" else (cs * 0.65 + gold * 0.35)
    return round(kda * 0.28 + kp * 0.27 + damage_share * 0.25 + economy * 0.20, 4)


def performance_item_frequencies(sample: pd.DataFrame, min_count: int = 2) -> list[dict]:
    """Items frecuentes en partidas similares con buen desempeno individual.

    Ademas de la tasa de presencia simple (`rate`, reportable al usuario),
    calcula un `score` ponderado por recencia y desempeno: las partidas recientes
    y mejor jugadas pesan mas, sin exigir que hayan sido victoria.
    """
    if sample.empty:
        return []
    played = sample.copy()
    played["performance_score"] = played.apply(_performance_score, axis=1)
    if len(played) >= 5:
        threshold = max(0.35, float(played["performance_score"].quantile(0.55)))
        played = played[played["performance_score"] >= threshold]
    if played.empty:
        return []

    # Orden por fecha descendente para asignar pesos de recencia por partida.
    if "gameCreation" in played.columns:
        played = played.sort_values("gameCreation", ascending=False)
    recency_weights = [0.5 ** (i / RECENCY_HALF_LIFE) for i in range(len(played))]

    counter: Counter = Counter()
    weighted: Counter = Counter()
    wins_by_item: Counter = Counter()
    losses_by_item: Counter = Counter()
    for (_, row), recency_weight in zip(played.iterrows(), recency_weights):
        perf_weight = max(0.2, float(row.get("performance_score") or 0.0))
        weight = recency_weight * perf_weight
        for slot in ITEM_SLOTS:
            value = row.get(slot)
            if pd.isna(value):
                continue
            item_id = int(value)
            if item_id <= 0 or item_id in NON_BUILD_ITEM_IDS:
                continue
            counter[item_id] += 1
            weighted[item_id] += weight
            if int(row.get("win") or 0) == 1:
                wins_by_item[item_id] += 1
            else:
                losses_by_item[item_id] += 1

    total_games = len(played)
    total_weight = sum(
        recency * max(0.2, float(row.get("performance_score") or 0.0))
        for recency, (_, row) in zip(recency_weights, played.iterrows())
    ) or 1.0
    results = [
        {
            "item_id": item_id,
            "count": count,
            "rate": round(count / total_games, 2),
            "score": round(weighted[item_id] / total_weight, 3),
            "wins": wins_by_item[item_id],
            "losses": losses_by_item[item_id],
            "performance_sample": total_games,
        }
        for item_id, count in counter.items()
        if count >= min_count
    ]
    results.sort(key=lambda r: (r["score"], r["rate"]), reverse=True)
    return results[:8]


def winning_item_frequencies(sample: pd.DataFrame, min_count: int = 2) -> list[dict]:
    """Compatibilidad: ahora usa buen desempeno en victorias y derrotas."""
    return performance_item_frequencies(sample, min_count=min_count)


def defensive_candidates(enemy_damage_mix: dict | None, ddragon: DataDragon) -> dict:
    """Sugerencias defensivas segun la mezcla de danio enemiga.

    enemy_damage_mix = {"physical": 0.6, "magic": 0.4} (derivado de Data Dragon).
    """
    if not enemy_damage_mix:
        return {"type": None, "items": [], "mix": None}
    magic_share = enemy_damage_mix.get("magic", 0.5)
    if magic_share >= 0.55:
        ids, dmg_type = MR_ITEM_IDS, "magico"
    elif magic_share <= 0.45:
        ids, dmg_type = ARMOR_ITEM_IDS, "fisico"
    else:
        ids, dmg_type = [ARMOR_ITEM_IDS[0], MR_ITEM_IDS[0], 3026], "mixto"
    return {
        "type": dmg_type,
        "mix": enemy_damage_mix,
        "items": [
            {
                "item_id": item_id,
                "name": ddragon.item_name(item_id),
                "image_url": ddragon.item_image_url(item_id),
                "gold": ddragon.item_gold(item_id) if hasattr(ddragon, "item_gold") else None,
            }
            for item_id in ids
        ],
    }

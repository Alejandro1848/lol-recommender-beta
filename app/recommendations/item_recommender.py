"""Recomendador de items dinamico.

La recomendacion mezcla fuentes verificables:
- arbol de compra de Data Dragon (`from`/`into`) para subir componentes,
- composicion y tipo de danio enemigo,
- items actuales de aliados/enemigos observados por Live Client,
- oro/fase de partida,
- historial local de victorias similares,
- estilo elegido por el jugador.

No intenta prometer una build perfecta. Ordena compras utiles y explica por
que entran en la lista.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.analytics.item_analysis import (
    ARMOR_ITEM_IDS,
    ITEM_SLOTS,
    MR_ITEM_IDS,
    NON_BUILD_ITEM_IDS,
    defensive_candidates,
    performance_item_frequencies,
)
from app.recommendations import explanation_builder as xp
from app.recommendations.similarity_engine import SimilarityResult
from app.riot.data_dragon import DataDragon

EARLY_PHASE_END_SECONDS = 840
MID_PHASE_END_SECONDS = 1500
EARLY_MAX_ITEM_GOLD = 1600

STYLE_LABELS = {
    "defensivo": "defensivo",
    "conservador": "conservador",
    "neutral": "neutral",
    "agresivo": "agresivo",
}

PHASE_LABELS = {
    "temprana": "fase temprana (antes del min 14)",
    "media": "fase media (min 14-25)",
    "tardia": "fase tardia (min 25+)",
}

TRUE_DAMAGE_THREATS = {
    "Vayne", "Fiora", "Camille", "Gwen", "MasterYi", "Darius", "Garen",
    "ChoGath", "Velkoz", "Olaf", "Smolder", "Sett", "Yone",
}

# Mejoras de botas comprables. La eleccion se trata aparte del resto de
# objetos para garantizar que la interfaz nunca recomiende dos pares.
BOOT_UPGRADE_IDS = (3006, 3020, 3047, 3111, 3158, 3117)

# Campeones cuya identidad incluye control duro frecuente o especialmente
# peligroso. Data Dragon no expone una etiqueta estructurada de CC, por eso
# esta lista conservadora complementa (no sustituye) la mezcla de dano viva.
TENACITY_THREATS = {
    "Ahri", "Alistar", "Amumu", "Anivia", "Annie", "Ashe", "Bard",
    "Blitzcrank", "Braum", "Cassiopeia", "ChoGath", "Elise", "Fiddlesticks",
    "Galio", "Gragas", "Hwei", "Ivern", "Janna", "JarvanIV", "Kennen",
    "Leona", "Lissandra", "Lulu", "Lux", "Malphite", "Maokai", "Morgana",
    "Nami", "Nautilus", "Neeko", "Ornn", "Pantheon", "Poppy", "Rakan",
    "Rell", "Renata", "Sejuani", "Seraphine", "Shen", "Skarner", "Sona",
    "Swain", "Syndra", "TahmKench", "Taric", "Thresh", "TwistedFate",
    "Varus", "Veigar", "Velkoz", "Vex", "Vi", "Volibear", "Warwick",
    "Xerath", "XinZhao", "Zac", "Zyra",
}

ARMOR_PEN_ITEMS = [3036, 3033, 6694]
MAGIC_PEN_ITEMS = [3135, 4629, 3089]
ANTI_HEAL_ITEMS = [3033, 3165, 3076, 8001]
ANTI_TRUE_DAMAGE_ITEMS = [3083, 3053, 3065, 3748]


def _phase(game_time_seconds: float | None) -> str | None:
    if game_time_seconds is None:
        return None
    if game_time_seconds < EARLY_PHASE_END_SECONDS:
        return "temprana"
    if game_time_seconds < MID_PHASE_END_SECONDS:
        return "media"
    return "tardia"


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, "", 0, "0"):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _owned_ids(snapshot: dict | None) -> set[int]:
    if not snapshot or not snapshot.get("me"):
        return set()
    return {
        item_id
        for item_id in (_safe_int(item.get("id")) for item in snapshot["me"].get("items", []))
        if item_id is not None
    }


def _item_gold(ddragon: DataDragon, item_id: int) -> int | None:
    return ddragon.item_gold(item_id) if hasattr(ddragon, "item_gold") else None


def _owned_id_counts(snapshot: dict | None) -> dict[int, int]:
    counts: dict[int, int] = {}
    if not snapshot or not snapshot.get("me"):
        return counts
    for item in snapshot["me"].get("items", []):
        item_id = _safe_int(item.get("id"))
        if item_id is None:
            continue
        counts[item_id] = counts.get(item_id, 0) + int(item.get("count") or 1)
    return counts


def _effective_cost(
    ddragon: DataDragon,
    item_id: int,
    owned_counts: dict[int, int],
    depth: int = 0,
) -> int | None:
    """Costo REAL en tienda descontando los componentes que ya llevas,
    igual que el precio que muestra el juego. None si faltan datos.

    Muta owned_counts: cada componente del inventario se descuenta una vez.
    """
    if not (hasattr(ddragon, "item_data") and hasattr(ddragon, "item_from")):
        return None
    if depth > 6:
        return None
    data = ddragon.item_data(item_id)
    if data is None:
        return None
    base = (data.get("gold") or {}).get("base")
    if base is None:
        return None
    total = int(base)
    for component in ddragon.item_from(item_id):
        if owned_counts.get(component, 0) > 0:
            owned_counts[component] -= 1
            continue
        sub = _effective_cost(ddragon, component, owned_counts, depth + 1)
        if sub is None:
            sub = _item_gold(ddragon, component)
            if sub is None:
                return None
        total += int(sub)
    return total


def live_buy_cost(
    ddragon: DataDragon, item_id: int, owned_counts: dict[int, int]
) -> int | None:
    """Costo real en tienda para un inventario dado.

    API publica para recalcular el oro faltante fuera del ciclo de
    recomendacion (p. ej. el overlay con datos frescos del Live Client).
    """
    return _effective_cost(ddragon, item_id, dict(owned_counts))


def _item_is_final(ddragon: DataDragon, item_id: int) -> bool | None:
    return ddragon.item_is_final(item_id) if hasattr(ddragon, "item_is_final") else None


def _fits_phase(phase: str | None, cost: int | None, is_final: bool | None) -> bool:
    if phase is None or cost is None:
        return True
    if phase == "temprana":
        return cost <= EARLY_MAX_ITEM_GOLD or is_final is False
    if phase == "tardia" and is_final is not None:
        return is_final or cost > EARLY_MAX_ITEM_GOLD
    return True


def _gold_clause(
    buy_cost: int | None, total_cost: int | None, current_gold: float | None
) -> str | None:
    if buy_cost is None or current_gold is None:
        return None
    components_bit = (
        f" (total {total_cost}, ya llevas componentes)"
        if total_cost is not None and buy_cost < total_cost
        else ""
    )
    if current_gold >= buy_cost:
        return f"Ya te alcanza el oro ({int(current_gold)} disponibles de {buy_cost}{components_bit})"
    return f"Cuesta {buy_cost} de oro{components_bit}; te faltan {int(buy_cost - current_gold)}"


def _item_stats(ddragon: DataDragon, item_id: int) -> dict[str, float]:
    stats = ddragon.item_stats(item_id) if hasattr(ddragon, "item_stats") else {}
    tags = set(ddragon.item_tags(item_id) if hasattr(ddragon, "item_tags") else [])
    return {
        "armor": float(stats.get("FlatArmorMod") or 0),
        "mr": float(stats.get("FlatSpellBlockMod") or 0),
        "health": float(stats.get("FlatHPPoolMod") or 0),
        "ad": float(stats.get("FlatPhysicalDamageMod") or 0),
        "ap": float(stats.get("FlatMagicDamageMod") or 0),
        "attack_speed": float(stats.get("PercentAttackSpeedMod") or 0),
        "crit": float(stats.get("FlatCritChanceMod") or 0),
        "lifesteal": float(stats.get("PercentLifeStealMod") or 0),
        "haste": 1.0 if ("AbilityHaste" in tags or "CooldownReduction" in tags) else 0.0,
    }


def _item_fit_for_champion(
    stats: dict[str, float],
    me_profile: dict | None,
    item_tags: list[str] | None = None,
    sources: set[str] | None = None,
) -> tuple[bool, str | None]:
    """Evita recomendaciones que contradicen al campeon.

    Data Dragon no expone una build ideal por campeon, pero si un perfil de
    dano aproximado y las clases oficiales (Marksman, Mage, ...). No bloquea
    items defensivos/hibridos; descarta ofensivos AP para perfiles fisicos,
    ofensivos AD/critico/vel. de ataque para perfiles magicos, items de pura
    utilidad sin sinergia (vida chica + movimiento, p. ej. Placa Lunar) y
    refuerza con la clase oficial cuando el perfil numerico queda al borde.
    """
    if not me_profile:
        return True, None
    physical_share = float(me_profile.get("physical") or 0.5)
    magic_share = float(me_profile.get("magic") or 0.5)
    champion_classes = set(me_profile.get("tags") or [])
    tags = set(item_tags or [])
    sources = sources or set()
    defensive_value = stats["armor"] + stats["mr"] + stats["health"] / 20
    physical_offense = stats["ad"] + stats["crit"] * 80 + stats["attack_speed"] * 80
    magic_offense = stats["ap"]
    is_defensive = defensive_value >= 12

    if not is_defensive:
        if physical_share >= 0.6 and magic_offense >= 40 and physical_offense < 15:
            return False, "filtrado: item ofensivo AP para campeon de perfil fisico"
        if magic_share >= 0.6 and magic_offense < 20 and (
            physical_offense >= 25 or stats["crit"] > 0 or stats["attack_speed"] >= 0.15
        ):
            return False, "filtrado: item ofensivo AD/critico para campeon de perfil magico"
        if "Marksman" in champion_classes and magic_offense >= 40 and physical_offense < 15:
            return False, "filtrado: item AP puro para un tirador"
        if champion_classes & {"Mage", "Support"} and physical_offense >= 40 and magic_offense < 20:
            return False, "filtrado: item AD puro para un mago/soporte"

    # Items de pura utilidad (sin ofensa, defensa marginal, sin aceleracion):
    # no aportan al plan de compra salvo que sean parte de una ruta que ya
    # llevas (upgrade) o que el modelo del campeon los respalde. Solo se
    # juzga cuando HAY stats conocidos (sin datos no se filtra).
    has_stats = any(value for value in stats.values())
    utility_only = (
        has_stats
        and physical_offense + magic_offense < 10
        and defensive_value < 12
        and stats["haste"] == 0
        and "Boots" not in tags
    )
    if utility_only and not ({"upgrade", "modelo"} & sources):
        return False, "filtrado: item de utilidad sin sinergia clara con tu campeon"
    return True, None


def _stat_summary(stats: dict[str, float], item_tags: list[str] | None) -> str | None:
    """Descripcion corta de que mejora el item (para el overlay)."""
    tags = set(item_tags or [])
    bits: list[str] = []
    if stats["ap"]:
        bits.append(f"+{stats['ap']:.0f} AP")
    if stats["ad"]:
        bits.append(f"+{stats['ad']:.0f} AD")
    if stats["attack_speed"]:
        bits.append(f"+{stats['attack_speed'] * 100:.0f}% vel. ataque")
    if stats["crit"]:
        bits.append(f"+{stats['crit'] * 100:.0f}% critico")
    if stats["armor"]:
        bits.append(f"+{stats['armor']:.0f} armadura")
    if stats["mr"]:
        bits.append(f"+{stats['mr']:.0f} RM")
    if stats["health"]:
        bits.append(f"+{stats['health']:.0f} vida")
    if stats["lifesteal"]:
        bits.append(f"+{stats['lifesteal'] * 100:.0f}% robo de vida")
    if stats["haste"]:
        bits.append("aceleracion de habilidad")
    if "MagicPenetration" in tags:
        bits.append("pen. magica")
    if "ArmorPenetration" in tags:
        bits.append("pen. de armadura")
    if "Boots" in tags or "NonbootsMovement" in tags:
        bits.append("velocidad de movimiento")
    return " · ".join(bits[:4]) if bits else None


def _owned_boots(owned: set[int], ddragon: DataDragon) -> set[int]:
    if not hasattr(ddragon, "item_tags"):
        return set()
    return {
        item_id for item_id in owned
        if "Boots" in (ddragon.item_tags(item_id) or [])
    }


def _team_item_profile(players: list[dict], ddragon: DataDragon) -> dict[str, float]:
    profile = defaultdict(float)
    for player in players:
        for item in player.get("items", []) or []:
            item_id = _safe_int(item.get("id"))
            if item_id is None:
                continue
            for key, value in _item_stats(ddragon, item_id).items():
                profile[key] += value
    return dict(profile)


def _damage_context(snapshot: dict | None, ddragon: DataDragon) -> dict[str, Any]:
    enemies = (snapshot or {}).get("enemies", []) or []
    allies = (snapshot or {}).get("allies", []) or []
    me = (snapshot or {}).get("me")
    if me:
        allies = allies + [me]
    enemy_mix = (snapshot or {}).get("enemy_damage_mix")
    true_threats = [
        p.get("champion") for p in enemies if p.get("champion") in TRUE_DAMAGE_THREATS
    ]
    tenacity_threats = [
        p.get("champion") for p in enemies if p.get("champion") in TENACITY_THREATS
    ]
    basic_attack_threats = []
    for player in enemies:
        champion = player.get("champion")
        tags = set((player.get("damage_profile") or {}).get("tags") or [])
        if not tags and champion and hasattr(ddragon, "champion_info"):
            tags = set((ddragon.champion_info(champion) or {}).get("tags") or [])
        if "Marksman" in tags:
            basic_attack_threats.append(champion)
    ally_profiles = [p.get("damage_profile") for p in allies if p.get("damage_profile")]
    ally_magic = (
        sum(p["magic"] for p in ally_profiles) / len(ally_profiles)
        if ally_profiles else 0.5
    )
    return {
        "enemy_mix": enemy_mix,
        "true_damage_threats": true_threats,
        "tenacity_threats": tenacity_threats,
        "basic_attack_threats": basic_attack_threats,
        "enemy_items": _team_item_profile(enemies, ddragon),
        "ally_items": _team_item_profile(allies, ddragon),
        "ally_magic_share": round(ally_magic, 3),
    }


def _winning_boot_history(sample, ddragon: DataDragon) -> dict[int, dict[str, float]]:
    """Presencia de botas en victorias similares, ponderada por recencia.

    Esta senal representa exactamente el 70% de la decision de botas. Las
    derrotas no entran aqui; siguen siendo utiles para los objetos principales.
    """
    if sample is None or sample.empty:
        return {}
    won = sample[sample["win"].fillna(0).astype(int) == 1].copy()
    if won.empty:
        return {}
    if "gameCreation" in won.columns:
        won = won.sort_values("gameCreation", ascending=False)
    weights = [0.5 ** (i / 12) for i in range(len(won))]
    weighted: dict[int, float] = defaultdict(float)
    counts: dict[int, int] = defaultdict(int)
    for (_, row), weight in zip(won.iterrows(), weights):
        seen: set[int] = set()
        for slot in ITEM_SLOTS:
            item_id = _safe_int(row.get(slot))
            if item_id is None or item_id in seen:
                continue
            tags = ddragon.item_tags(item_id) if hasattr(ddragon, "item_tags") else []
            if "Boots" not in (tags or []):
                continue
            seen.add(item_id)
            weighted[item_id] += weight
            counts[item_id] += 1
    total_weight = sum(weights) or 1.0
    return {
        item_id: {
            "rate": round(value / total_weight, 4),
            "wins": counts[item_id],
            "winning_sample": len(won),
        }
        for item_id, value in weighted.items()
    }


def _boot_live_scores(
    context: dict[str, Any], me_profile: dict | None
) -> dict[int, tuple[float, str]]:
    """30% contextual: dano rival, control y autoataques de la composicion."""
    mix = context.get("enemy_mix") or {}
    physical = float(mix.get("physical", 0.5))
    magic = float(mix.get("magic", 0.5))
    cc = min(len(context.get("tenacity_threats") or []) / 3.0, 1.0)
    autos = min(len(context.get("basic_attack_threats") or []) / 2.0, 1.0)
    my_magic = float((me_profile or {}).get("magic", 0.5))
    my_physical = float((me_profile or {}).get("physical", 0.5))
    my_tags = set((me_profile or {}).get("tags") or [])

    return {
        3111: (
            min(1.0, magic * 0.75 + cc * 0.25),
            f"{magic:.0%} dano magico y {len(context.get('tenacity_threats') or [])} amenaza(s) de control",
        ),
        3047: (
            min(1.0, physical * 0.75 + autos * 0.25),
            f"{physical:.0%} dano fisico y {len(context.get('basic_attack_threats') or [])} amenaza(s) de autoataques",
        ),
        3020: (
            min(1.0, my_magic * 0.55 + (1.0 - magic) * 0.15),
            "prioridad ofensiva para un perfil de dano magico",
        ),
        3006: (
            min(1.0, my_physical * 0.5 + (0.25 if "Marksman" in my_tags else 0.0)),
            "prioridad de velocidad de ataque para un perfil fisico",
        ),
        3158: (0.55 if my_tags & {"Mage", "Support", "Fighter"} else 0.35,
               "aceleracion de habilidad y enfriamiento de hechizos"),
        3117: (0.2, "movilidad fuera de combate"),
    }


def _apply_boot_policy(
    scored: list[dict], similarity: SimilarityResult, context: dict[str, Any],
    me_profile: dict | None, ddragon: DataDragon,
) -> dict | None:
    """Elige exactamente una bota con 70% victorias historicas + 30% vivo."""
    boots = [item for item in scored if item.get("is_boots")]
    if not boots:
        return None
    history = _winning_boot_history(similarity.sample, ddragon)
    max_history = max((entry["rate"] for entry in history.values()), default=0.0)
    live = _boot_live_scores(context, me_profile)
    for item in boots:
        item_id = item["item_id"]
        hist = history.get(item_id, {})
        history_score = (hist.get("rate", 0.0) / max_history) if max_history else 0.0
        live_score, live_reason = live.get(item_id, (0.25, "ajuste general a la composicion"))
        item["boot_history_score"] = round(history_score, 3)
        item["boot_live_score"] = round(live_score, 3)
        item["boot_priority_score"] = round(0.70 * history_score + 0.30 * live_score, 3)
        item["boot_history"] = hist
        item["boot_live_reason"] = live_reason
    return max(boots, key=lambda item: (item["boot_priority_score"], item["score"]))


def _style_bonus(style: str, stats: dict[str, float], cost: int | None, current_gold: float | None) -> float:
    defensive_value = stats["armor"] + stats["mr"] + stats["health"] / 20
    offensive_value = stats["ad"] + stats["ap"] + stats["crit"] * 80 + stats["attack_speed"] * 80
    affordable = 1.0 if cost is not None and current_gold is not None and current_gold >= cost else 0.0
    if style == "defensivo":
        return defensive_value * 0.018 + affordable * 0.2
    if style == "conservador":
        return defensive_value * 0.012 + affordable * 0.45
    if style == "agresivo":
        return offensive_value * 0.018
    return defensive_value * 0.008 + offensive_value * 0.008 + affordable * 0.25


# Sinergia runas-items. Cada perfil: aliases del keystone (cliente en
# espanol o ingles, sin acentos), etiqueta para la explicacion, pesos por
# stat del item y tags de penetracion que refuerzan el plan de la runa.
RUNE_SYNERGY_PROFILES: list[dict[str, Any]] = [
    {
        "aliases": ("conqueror", "conquistador", "lethal tempo", "compas letal",
                    "fleet footwork", "juego de pies"),
        "label": "pelea extendida/sustain",
        "weights": {"ad": 0.010, "ap": 0.006, "attack_speed": 1.2,
                    "lifesteal": 4.0, "health": 0.0015, "haste": 0.15},
        "pen_tags": set(),
    },
    {
        "aliases": ("electrocute", "electrocutar", "dark harvest", "cosecha oscura",
                    "hail of blades", "rafaga de golpes", "first strike", "golpe inicial"),
        "label": "burst/asesinato",
        "weights": {"ad": 0.012, "ap": 0.012, "haste": 0.25},
        "pen_tags": {"ArmorPenetration", "MagicPenetration"},
    },
    {
        "aliases": ("arcane comet", "cometa arcano", "summon aery", "invoca a aery",
                    "invocacion de aery", "phase rush", "irrupcion de fase"),
        "label": "poke/utilidad",
        "weights": {"ap": 0.012, "haste": 0.35, "ad": 0.005},
        "pen_tags": {"MagicPenetration"},
    },
    {
        "aliases": ("grasp of the undying", "garra del inmortal", "aftershock",
                    "conmocion", "guardian", "guardian"),
        "label": "aguante/frontline",
        "weights": {"armor": 0.012, "mr": 0.012, "health": 0.0018, "haste": 0.15},
        "pen_tags": set(),
    },
    {
        "aliases": ("press the attack", "ataque intensificado"),
        "label": "dps con autoataques",
        "weights": {"attack_speed": 1.5, "ad": 0.010, "crit": 1.2, "lifesteal": 2.0},
        "pen_tags": set(),
    },
]

# Sin keystone reconocible, el arbol primario da una senal mas debil.
RUNE_TREE_FALLBACK = {
    "precision": 4,
    "domination": 1, "dominacion": 1,
    "sorcery": 2, "brujeria": 2,
    "resolve": 3, "valor": 3,
}

RUNE_BONUS_CAP = 1.2


def _fold_accents(text: str) -> str:
    import unicodedata

    return "".join(
        ch for ch in unicodedata.normalize("NFD", str(text).lower())
        if unicodedata.category(ch) != "Mn"
    )


def _rune_clause_and_bonus(
    snapshot: dict | None,
    stats: dict[str, float],
    item_tags: list[str] | None = None,
) -> tuple[str | None, float]:
    """Bono de score por sinergia entre el item y las runas elegidas.

    El bono se acota a RUNE_BONUS_CAP para que las runas orienten la
    eleccion (peso comparable a un counter situacional) sin aplastar al
    modelo entrenado ni al historial.
    """
    runes = ((snapshot or {}).get("me") or {}).get("runes") or {}
    keystone = _fold_accents(runes.get("keystone") or "")
    profile = None
    source = runes.get("keystone")
    if keystone:
        profile = next(
            (p for p in RUNE_SYNERGY_PROFILES
             if any(alias in keystone for alias in p["aliases"])),
            None,
        )
    scale = 1.0
    if profile is None:
        source = runes.get("primary_tree")
        tree = _fold_accents(runes.get("primary_tree") or "")
        if not tree:
            return None, 0.0
        index = next(
            (i for name, i in RUNE_TREE_FALLBACK.items() if name in tree),
            None,
        )
        if index is None:
            return None, 0.0
        profile = RUNE_SYNERGY_PROFILES[index]
        scale = 0.5  # el arbol sin keystone es evidencia mas debil

    bonus = sum(
        stats.get(stat, 0.0) * weight for stat, weight in profile["weights"].items()
    )
    if profile["pen_tags"] & set(item_tags or []):
        bonus += 0.5
    bonus = min(bonus * scale, RUNE_BONUS_CAP)
    if bonus < 0.15:
        return None, 0.0
    return (
        f"Sinergia con tus runas de {profile['label']} ({source or 'tus runas'})",
        round(bonus, 3),
    )


def _upgrade_candidates(owned: set[int], ddragon: DataDragon) -> list[dict]:
    out = []
    for owned_id in owned:
        next_items = ddragon.item_into(owned_id) if hasattr(ddragon, "item_into") else []
        for item_id in next_items:
            out.append({
                "item_id": item_id,
                "source": "upgrade",
                "base_score": 2.4,
                "reasons": [f"mejora directa de {ddragon.item_name(owned_id)}"],
                "builds_from": owned_id,
            })
            # Un paso mas para que se vea hacia donde escala el componente.
            for later_id in (ddragon.item_into(item_id) if hasattr(ddragon, "item_into") else []):
                out.append({
                    "item_id": later_id,
                    "source": "upgrade",
                    "base_score": 1.8,
                    "reasons": [
                        f"ruta de compra desde {ddragon.item_name(owned_id)}",
                        f"pasa por {ddragon.item_name(item_id)}",
                    ],
                    "builds_from": owned_id,
                })
    return out


def _strategic_candidates(context: dict[str, Any], me_profile: dict | None) -> list[dict]:
    candidates = []
    enemy_mix = context.get("enemy_mix") or {}
    if enemy_mix.get("magic", 0.5) >= 0.55:
        candidates.extend({"item_id": item_id, "source": "counter", "base_score": 2.0,
                           "reasons": ["el equipo enemigo carga mas danio magico"]}
                          for item_id in MR_ITEM_IDS)
    if enemy_mix.get("physical", 0.5) >= 0.55:
        candidates.extend({"item_id": item_id, "source": "counter", "base_score": 2.0,
                           "reasons": ["el equipo enemigo carga mas danio fisico"]}
                          for item_id in ARMOR_ITEM_IDS)
    if context.get("true_damage_threats"):
        candidates.extend({"item_id": item_id, "source": "counter", "base_score": 1.6,
                           "reasons": [
                               "hay amenazas de danio verdadero: vida/escudos suelen rendir mejor que solo resistencias"
                           ]}
                          for item_id in ANTI_TRUE_DAMAGE_ITEMS)

    enemy_items = context.get("enemy_items") or {}
    my_physical = (me_profile or {}).get("physical", 0.5)
    my_magic = (me_profile or {}).get("magic", 0.5)
    if enemy_items.get("armor", 0) >= 120 and my_physical >= my_magic:
        candidates.extend({"item_id": item_id, "source": "counter", "base_score": 1.8,
                           "reasons": ["los enemigos ya estan comprando armadura"]}
                          for item_id in ARMOR_PEN_ITEMS)
    if enemy_items.get("mr", 0) >= 100 and my_magic > my_physical:
        candidates.extend({"item_id": item_id, "source": "counter", "base_score": 1.8,
                           "reasons": ["los enemigos ya estan comprando resistencia magica"]}
                          for item_id in MAGIC_PEN_ITEMS)
    if enemy_items.get("health", 0) >= 1800 or enemy_items.get("lifesteal", 0) > 0.2:
        candidates.extend({"item_id": item_id, "source": "counter", "base_score": 1.4,
                           "reasons": ["el inventario enemigo muestra vida/curacion alta"]}
                          for item_id in ANTI_HEAL_ITEMS)
    return candidates


def _model_probabilities(model, X) -> list[float] | None:
    """predict_proba multilabel normalizado a una prob por item (columna)."""
    import numpy as np

    raw = model.predict_proba(X)
    if isinstance(raw, list):
        # RandomForest multilabel: lista de arrays (n, 2) por etiqueta.
        return [
            float(probs[0, 1]) if probs.shape[1] > 1 else 0.0 for probs in raw
        ]
    arr = np.asarray(raw)
    return [float(v) for v in arr[0]] if arr.ndim == 2 else None


def _model_candidates(
    snapshot: dict | None,
    registry,
    tier: str | None,
    ddragon: DataDragon,
    top_n: int = 6,
    min_probability: float = 0.2,
) -> list[dict]:
    """Candidatos del modelo de items ENTRENADO para este campeon/liga.

    El modelo (train_champion_item_model) aprende que items finales aparecen
    en las builds del campeon en la liga objetivo; sus features (rol,
    atributos propios/rival, mezcla magica de composiciones) se calculan
    igual en historico y en vivo. Es la fuente con mas peso: ancla las
    sugerencias a builds reales del campeon, no a reglas genericas.
    """
    if not snapshot or registry is None or not tier:
        return []
    me = snapshot.get("me") or {}
    champion = me.get("champion")
    if not champion:
        return []
    try:
        loaded = registry.latest("item", champion, tier)
    except Exception:
        return []
    if loaded is None:
        return []
    payload, meta = loaded
    if not isinstance(payload, dict):
        return []
    model = payload.get("model")
    item_ids = payload.get("item_ids") or []
    if model is None or not item_ids:
        return []

    import pandas as pd

    from app.ml.features import FEATURE_COLUMNS, build_feature_row

    rival = snapshot.get("direct_rival") or {}
    allies = [p.get("champion") for p in snapshot.get("allies") or [] if p.get("champion")]
    enemies = [p.get("champion") for p in snapshot.get("enemies") or [] if p.get("champion")]
    row = build_feature_row(
        champion, rival.get("champion"), me.get("position"), allies, enemies, ddragon
    )
    columns = payload.get("feature_columns") or list(FEATURE_COLUMNS)
    try:
        X = pd.DataFrame([row]).reindex(columns=columns, fill_value=0.0).astype(float)
        probabilities = _model_probabilities(model, X)
    except Exception:
        return []
    if probabilities is None or len(probabilities) != len(item_ids):
        return []

    n_samples = (meta or {}).get("n_samples")
    evidence = f" (muestra: {n_samples} builds)" if n_samples else ""
    ranked = sorted(zip(item_ids, probabilities), key=lambda x: x[1], reverse=True)
    return [
        {
            "item_id": int(item_id),
            "source": "modelo",
            "base_score": 1.6 + 2.4 * probability,
            "reasons": [
                f"el modelo de {champion} lo predice en el {probability:.0%} de "
                f"builds similares en {tier}{evidence}"
            ],
            "model_probability": round(probability, 3),
        }
        for item_id, probability in ranked[:top_n]
        if probability >= min_probability
    ]


def _historical_candidates(similarity: SimilarityResult) -> list[dict]:
    return [
        {
            "item_id": freq["item_id"],
            "source": "historico",
            "base_score": 1.2 + freq["score"],
            "reasons": [
                f"aparecio en {freq['rate']:.0%} de buenas partidas similares",
                "incluye victorias y derrotas con buen desempeno individual",
                "ponderado por recencia y desempeno",
            ],
            "freq": freq,
        }
        for freq in performance_item_frequencies(similarity.sample)
    ]


def _merge_candidates(candidates: list[dict]) -> list[dict]:
    merged: dict[int, dict] = {}
    for candidate in candidates:
        item_id = candidate["item_id"]
        if item_id not in merged:
            merged[item_id] = {**candidate, "reasons": []}
        merged[item_id]["base_score"] = merged[item_id].get("base_score", 0) + candidate.get("base_score", 0)
        merged[item_id].setdefault("sources", set()).add(candidate.get("source"))
        merged[item_id]["reasons"].extend(r for r in candidate.get("reasons", []) if r)
        if candidate.get("freq"):
            merged[item_id]["freq"] = candidate["freq"]
        if candidate.get("builds_from"):
            merged[item_id]["builds_from"] = candidate["builds_from"]
    for item in merged.values():
        item["sources"] = sorted(s for s in item.get("sources", set()) if s)
        item["reasons"] = list(dict.fromkeys(item["reasons"]))
    return list(merged.values())


def recommend_items(
    snapshot: dict | None,
    similarity: SimilarityResult,
    ddragon: DataDragon,
    max_items: int = 5,
    style: str = "neutral",
    champion_registry=None,
    tier: str | None = None,
) -> list[dict]:
    style = style if style in STYLE_LABELS else "neutral"
    recommendations: list[dict] = []
    phase = _phase((snapshot or {}).get("game_time_seconds"))
    current_gold = ((snapshot or {}).get("active_player") or {}).get("current_gold")
    owned = _owned_ids(snapshot)
    owned_counts = _owned_id_counts(snapshot)
    me_profile = (((snapshot or {}).get("me") or {}).get("damage_profile"))
    context = _damage_context(snapshot, ddragon)
    live_signals = bool(snapshot and snapshot.get("live_signals_available", True))

    defensive = defensive_candidates(context.get("enemy_mix"), ddragon)
    raw_candidates = _model_candidates(snapshot, champion_registry, tier, ddragon)
    raw_candidates += _upgrade_candidates(owned, ddragon)
    raw_candidates += _strategic_candidates(context, me_profile)
    raw_candidates += _historical_candidates(similarity)
    # Todas las mejoras de botas compiten en una clasificacion independiente
    # 70/30. Si Data Dragon no conoce una del parche, se filtra mas abajo.
    raw_candidates += [
        {
            "item_id": item_id,
            "source": "politica_botas",
            "base_score": 0.0,
            "reasons": ["candidata para la seleccion unica de botas"],
        }
        for item_id in BOOT_UPGRADE_IDS
    ]
    raw_candidates += [
        {"item_id": item["item_id"], "source": "counter", "base_score": 1.5,
         "reasons": [f"defensa contra danio {defensive['type']}"]}
        for item in defensive.get("items", [])
    ]

    scored = []
    for candidate in _merge_candidates(raw_candidates):
        item_id = _safe_int(candidate.get("item_id"))
        if item_id is None or item_id in owned:
            continue
        # Nunca recomendables como compra: consumibles/wards y toda la
        # linea de mision de soporte (evoluciona gratis; ddragon marca las
        # evoluciones finales como comprables y ese dato es enganoso).
        if item_id in NON_BUILD_ITEM_IDS:
            continue
        if hasattr(ddragon, "item_purchasable") and ddragon.item_purchasable(item_id) is False:
            continue
        # Solo un par de botas por jugador: si ya tienes botas, la unica
        # bota recomendable es la mejora directa de las que llevas.
        candidate_tags = ddragon.item_tags(item_id) if hasattr(ddragon, "item_tags") else []
        is_boots = "Boots" in (candidate_tags or []) or item_id in BOOT_UPGRADE_IDS
        if is_boots:
            boots = _owned_boots(owned, ddragon)
            if boots:
                builds_from = set(ddragon.item_from(item_id)) if hasattr(ddragon, "item_from") else set()
                if not (builds_from & boots):
                    continue
        cost = _item_gold(ddragon, item_id)
        is_final = _item_is_final(ddragon, item_id)
        core_source = bool({"modelo", "historico"} & set(candidate.get("sources") or []))
        # Los dos objetos principales deben verse desde el inicio aunque aun
        # no se puedan comprar completos. Los componentes/situacionales si
        # siguen respetando la fase y el oro.
        if not _fits_phase(phase, cost, is_final) and not (core_source and is_final is True):
            continue
        # Costo real en tienda: descuenta componentes que ya llevas (la
        # copia evita que un candidato "gaste" el inventario de otro).
        effective = _effective_cost(ddragon, item_id, dict(owned_counts))
        buy_cost = effective if effective is not None else cost
        if hasattr(ddragon, "item_data") and ddragon.item_data(item_id) is None:
            continue
        stats = _item_stats(ddragon, item_id)
        fits_champion, fit_note = _item_fit_for_champion(
            stats, me_profile,
            item_tags=candidate_tags,
            sources=set(candidate.get("sources") or []),
        )
        if not fits_champion and "upgrade" not in (candidate.get("sources") or []):
            continue
        if fit_note:
            candidate.setdefault("reasons", []).append(fit_note)
        rune_clause, rune_bonus = _rune_clause_and_bonus(snapshot, stats, candidate_tags)
        affordable = buy_cost is not None and current_gold is not None and current_gold >= buy_cost
        remaining = (
            int(buy_cost - current_gold)
            if buy_cost is not None and current_gold is not None
            else None
        )
        if remaining is not None and remaining < 0:
            remaining = 0
        score = (
            candidate.get("base_score", 0)
            + _style_bonus(style, stats, buy_cost, current_gold)
            + rune_bonus
            + (0.35 if affordable else 0)
            + (0.25 if candidate.get("source") == "upgrade" else 0)
        )
        scored.append({
            **candidate,
            "item_id": item_id,
            "cost": cost,
            "buy_cost": buy_cost,
            "is_final": is_final,
            "score": round(score, 3),
            "remaining_gold": remaining,
            "rune_clause": rune_clause,
            "rune_bonus": rune_bonus,
            "is_boots": is_boots,
            "stat_summary": _stat_summary(stats, candidate_tags),
        })

    # Relevancia primero (el item nucleo gana aunque aun no alcance el oro);
    # ser comprable ya suma dentro del score, no manda sobre el.
    scored.sort(
        key=lambda item: (
            -item["score"],
            item["remaining_gold"] if item["remaining_gold"] is not None else 99999,
            item["cost"] if item["cost"] is not None else 99999,
        )
    )

    # Estructura estable para la UI: una sola bota, dos objetos principales
    # historicos/modelados y despues (si caben) compras situacionales.
    selected: list[dict] = []
    finished_boots_owned = {
        item_id for item_id in _owned_boots(owned, ddragon) if item_id != 1001
    }
    chosen_boot = None if finished_boots_owned else _apply_boot_policy(
        scored, similarity, context, me_profile, ddragon
    )
    if chosen_boot is not None:
        chosen_boot["recommendation_role"] = "botas"
        chosen_boot["priority"] = 0
        boot_hist = chosen_boot.get("boot_history") or {}
        chosen_boot.setdefault("reasons", []).insert(
            0,
            "70% historial de victorias + 30% composicion: "
            f"{chosen_boot.get('boot_live_reason')}"
            + (
                f"; {boot_hist.get('wins', 0)}/{boot_hist.get('winning_sample', 0)} victorias similares"
                if boot_hist else ""
            ),
        )
        selected.append(chosen_boot)

    non_boots = [item for item in scored if not item.get("is_boots")]
    historical_core = [
        item for item in non_boots
        if item.get("is_final") is True
        and {"modelo", "historico"} & set(item.get("sources") or [])
    ]
    # El score ya combina el modelo entrenado, frecuencia historica, ajuste a
    # composicion y estilo. Mantener este orden convierte la primera posicion
    # en la prioridad real de la partida.
    core_items = historical_core[:2]
    if len(core_items) < 2:
        core_items.extend(
            item for item in non_boots
            if item not in core_items and item.get("is_final") is True
        )
        core_items = core_items[:2]
    for index, item in enumerate(core_items, start=1):
        item["recommendation_role"] = "principal"
        item["priority"] = index
        selected.append(item)

    selected_ids = {item["item_id"] for item in selected}
    for item in non_boots:
        if len(selected) >= max_items:
            break
        if item["item_id"] in selected_ids:
            continue
        item["recommendation_role"] = "situacional"
        item["priority"] = len(selected) + 1
        selected.append(item)
        selected_ids.add(item["item_id"])

    for item in selected[:max_items]:
        item_id = item["item_id"]
        name = ddragon.item_name(item_id)
        reasons = item.get("reasons") or []
        freq = item.get("freq") or {}
        source_label = " + ".join(item.get("sources") or [item.get("source", "mixto")])
        detail_bits = [f"estilo {STYLE_LABELS[style]}"]
        if phase:
            detail_bits.append(PHASE_LABELS[phase])
        if item.get("remaining_gold") == 0:
            detail_bits.append("comprable ahora")
        elif item.get("remaining_gold") is not None:
            detail_bits.append(f"faltan {item['remaining_gold']} oro")

        role = item.get("recommendation_role")
        if role == "botas":
            hist = item.get("boot_history") or {}
            winning_evidence = (
                f"aparecio en {hist.get('wins', 0)} de {hist.get('winning_sample', 0)} victorias similares"
                if hist else "sin victorias similares suficientes; se usa el contexto vivo"
            )
            lead = (
                f"Eleccion unica de botas: 70% historial ({winning_evidence}) y "
                f"30% composicion rival ({item.get('boot_live_reason')})"
            )
        elif role == "principal":
            lead = (
                f"Objeto principal #{item.get('priority')}: {name} entra por "
                f"{', '.join(reasons[:3])}"
            ) if reasons else f"Objeto principal #{item.get('priority')} segun el historico"
        else:
            lead = f"{name} entra por {', '.join(reasons[:3])}" if reasons else f"{name} encaja con el estado actual"
        explanation = xp.compose(
            lead,
            _gold_clause(item.get("buy_cost"), item.get("cost"), current_gold),
            item.get("rune_clause"),
            (
                f"Tu equipo tiene {context['ally_magic_share']:.0%} de perfil magico; "
                "la sugerencia busca complementar sin duplicar demasiado la composicion"
                if context.get("ally_magic_share") is not None else None
            ),
            xp.small_sample_warning(similarity.size) if freq else None,
        )
        historical_evidence = None
        if freq:
            historical_evidence = (
                f"evidencia: {freq.get('wins', 0)} victorias y "
                f"{freq.get('losses', 0)} derrotas con buen desempeno"
            )
        recommendations.append({
            "kind": "item",
            "title": f"{name}",
            "detail": " - ".join(detail_bits),
            "explanation": explanation,
            "confidence": "alta" if item["score"] >= 3.2 else ("media" if item["score"] >= 2.0 else "baja"),
            "sample_size": similarity.size if freq else None,
            "similarity_level": similarity.level if freq else None,
            "similarity_label": similarity.label if freq else None,
            "image_url": ddragon.item_image_url(item_id),
            "data_source": "mixto" if live_signals and snapshot else "historico",
            "extra": {
                "item_id": item_id,
                "is_boots": item.get("is_boots", False),
                "stat_summary": item.get("stat_summary"),
                "gold_cost": item.get("cost"),
                "buy_cost": item.get("buy_cost"),
                "current_gold": current_gold,
                "remaining_gold": item.get("remaining_gold"),
                "phase": phase,
                "style": style,
                "score": item["score"],
                "rune_synergy": item.get("rune_clause"),
                "rune_bonus": item.get("rune_bonus"),
                "recommendation_role": role,
                "priority": item.get("priority"),
                "boot_weights": (
                    {
                        "historical": 0.70,
                        "live_composition": 0.30,
                        "historical_score": item.get("boot_history_score"),
                        "live_score": item.get("boot_live_score"),
                        "combined_score": item.get("boot_priority_score"),
                    }
                    if role == "botas" else None
                ),
                "sources": item.get("sources") or [item.get("source")],
                "builds_from": item.get("builds_from"),
                "reasons": reasons,
                "win_rate_presence": freq.get("rate"),
                "recency_score": freq.get("score"),
                "performance_sample": freq.get("performance_sample"),
                "performance_evidence": historical_evidence,
            },
        })

    if not recommendations:
        recommendations.append({
            "kind": "item",
            "title": "Sin recomendacion de items",
            "detail": "Datos insuficientes",
            "explanation": (
                "No hay partida activa ni suficientes partidas similares en tu "
                "historial local para recomendar items con fundamento."
            ),
            "confidence": "baja",
            "sample_size": similarity.size,
            "data_source": "historico",
            "extra": {"style": style},
        })
    return recommendations[:max_items]

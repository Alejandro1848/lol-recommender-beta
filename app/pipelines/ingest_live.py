"""Ingesta de estado en vivo desde la Live Client Data API.

Convierte el JSON crudo de /allgamedata en un snapshot normalizado con:
- jugador propio, aliados, enemigos y rival directo por posicion,
- mezcla de tipo de danio del equipo enemigo (via Data Dragon),
- resumen de eventos (torres, dragones, barones, heraldos por equipo).

Todo lo que la Live Client API no expone (vida de enemigos, oro de
enemigos) se marca como no disponible; NO se estima ni se inventa.
"""
from __future__ import annotations

import logging
from typing import Any

from app.data.normalizers import normalize_role
from app.riot.data_dragon import DataDragon
from app.riot.live_client import LiveClient

logger = logging.getLogger(__name__)


def _normalize_item(item: Any, ddragon: DataDragon) -> dict[str, Any] | None:
    if not item:
        return None
    if isinstance(item, dict):
        item_id = item.get("itemID") or item.get("id")
        name = item.get("displayName") or item.get("name")
        count = item.get("count", 1)
    else:
        item_id = item
        name = None
        count = 1
    if item_id in (None, "", 0, "0"):
        return None
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        pass
    return {
        "id": item_id,
        "name": name or ddragon.item_name(item_id),
        "image_url": ddragon.item_image_url(item_id),
        "count": count,
    }


def _normalize_runes(raw: dict) -> dict[str, Any] | None:
    runes = raw.get("runes") or raw.get("fullRunes")
    if not isinstance(runes, dict):
        return None
    general = runes.get("generalRunes")
    first_rune = general[0] if isinstance(general, list) and general else {}
    return {
        "keystone": runes.get("keystone") or first_rune.get("displayName"),
        "primary_tree": runes.get("primaryRuneTree", {}).get("displayName")
        if isinstance(runes.get("primaryRuneTree"), dict)
        else runes.get("primaryRuneTree"),
        "secondary_tree": runes.get("secondaryRuneTree", {}).get("displayName")
        if isinstance(runes.get("secondaryRuneTree"), dict)
        else runes.get("secondaryRuneTree"),
        "raw": runes,
    }


def _normalize_player(raw: dict, ddragon: DataDragon) -> dict[str, Any]:
    champion = raw.get("championName") or ""
    scores = raw.get("scores", {}) or {}
    items = [
        normalized
        for normalized in (_normalize_item(item, ddragon) for item in (raw.get("items") or []))
        if normalized is not None
    ]
    return {
        "riot_id": raw.get("riotId") or raw.get("summonerName") or "?",
        "champion": champion,
        "champion_image_url": ddragon.champion_image_url(champion),
        "team": raw.get("team"),  # ORDER | CHAOS
        "position": normalize_role(raw.get("position")),
        "level": raw.get("level"),
        "kills": scores.get("kills", 0),
        "deaths": scores.get("deaths", 0),
        "assists": scores.get("assists", 0),
        "creep_score": scores.get("creepScore"),
        "ward_score": scores.get("wardScore"),
        "is_dead": bool(raw.get("isDead", False)),
        "respawn_timer": raw.get("respawnTimer"),
        "items": items,
        "runes": _normalize_runes(raw),
        "damage_profile": ddragon.champion_damage_profile(champion) if champion else None,
    }


def _events_summary(events: list[dict], players: list[dict]) -> dict[str, Any]:
    """Cuenta objetivos por equipo a partir de /eventdata.

    Los eventos traen KillerName (riot id); se mapea a equipo con allPlayers.
    Las torres derriban con nombres internos Turret_T1/T2 (T1=ORDER, T2=CHAOS).
    """
    name_to_team = {}
    for player in players:
        name_to_team[player["riot_id"]] = player["team"]
        # riotId puede venir "Nombre#TAG" y KillerName solo "Nombre"
        name_to_team[player["riot_id"].split("#")[0]] = player["team"]

    summary = {
        "ORDER": {"dragons": 0, "barons": 0, "heralds": 0, "turrets": 0, "grubs": 0},
        "CHAOS": {"dragons": 0, "barons": 0, "heralds": 0, "turrets": 0, "grubs": 0},
        "first_blood": None,
    }

    def killer_team(event: dict) -> str | None:
        return name_to_team.get(event.get("KillerName", ""))

    for event in events or []:
        name = event.get("EventName")
        if name == "DragonKill":
            team = killer_team(event)
            if team:
                summary[team]["dragons"] += 1
        elif name == "BaronKill":
            team = killer_team(event)
            if team:
                summary[team]["barons"] += 1
        elif name == "HeraldKill":
            team = killer_team(event)
            if team:
                summary[team]["heralds"] += 1
        elif name == "HordeKill":  # void grubs
            team = killer_team(event)
            if team:
                summary[team]["grubs"] += 1
        elif name == "TurretKilled":
            turret = event.get("TurretKilled", "")
            # Turret_T1_* pertenece a ORDER => la derribo CHAOS y viceversa
            if "_T1_" in turret:
                summary["CHAOS"]["turrets"] += 1
            elif "_T2_" in turret:
                summary["ORDER"]["turrets"] += 1
        elif name == "FirstBlood":
            summary["first_blood"] = event.get("Recipient")
    return summary


def build_live_snapshot(
    live_client: LiveClient, ddragon: DataDragon, my_riot_id: str | None = None
) -> dict[str, Any] | None:
    """Snapshot normalizado del juego en vivo, o None si no hay juego local."""
    data = live_client.get_all_game_data()
    if not data or not data.get("allPlayers"):
        return None

    active_raw = data.get("activePlayer", {}) or {}
    active_name = active_raw.get("riotId") or active_raw.get("summonerName") or my_riot_id
    players = [_normalize_player(p, ddragon) for p in data["allPlayers"]]

    me = next((p for p in players if p["riot_id"] == active_name), None)
    if me is None and active_name:
        short = active_name.split("#")[0]
        me = next((p for p in players if p["riot_id"].split("#")[0] == short), None)
    if me is None:
        me = players[0]

    my_team = me["team"]
    allies = [p for p in players if p["team"] == my_team and p is not me]
    enemies = [p for p in players if p["team"] != my_team]

    rival = None
    if me.get("position"):
        rival = next((p for p in enemies if p.get("position") == me["position"]), None)

    enemy_profiles = [p["damage_profile"] for p in enemies if p.get("damage_profile")]
    enemy_mix = None
    if enemy_profiles:
        physical = sum(pr["physical"] for pr in enemy_profiles) / len(enemy_profiles)
        enemy_mix = {"physical": round(physical, 3), "magic": round(1 - physical, 3)}

    events = (data.get("events") or {}).get("Events", [])
    game_data = data.get("gameData", {}) or {}

    # Eventos de objetivos con su EventTime: permiten derivar timers de
    # respawn (dragon/baron) con las reglas publicas del juego.
    objective_events = [
        {
            "EventName": e.get("EventName"),
            "EventTime": e.get("EventTime"),
            "KillerName": e.get("KillerName"),
        }
        for e in events
        if e.get("EventName") in ("DragonKill", "BaronKill", "HeraldKill", "HordeKill")
    ]

    return {
        "game_time_seconds": game_data.get("gameTime"),
        "game_mode": game_data.get("gameMode"),
        "me": me,
        "my_team": my_team,
        "active_player": {
            "current_gold": active_raw.get("currentGold"),
            "level": active_raw.get("level"),
            "champion_stats": active_raw.get("championStats", {}),
        },
        "allies": allies,
        "enemies": enemies,
        "direct_rival": rival,
        "enemy_damage_mix": enemy_mix,
        "events_summary": _events_summary(events, players),
        "objective_events": objective_events,
        "data_source": "live_client",
        "live_signals_available": True,
    }

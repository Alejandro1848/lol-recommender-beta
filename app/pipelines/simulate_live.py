"""Snapshots de partida SIMULADOS para pruebas sin el cliente de LoL abierto.

Dos fuentes, ambas etiquetadas explicitamente como simulacion:

- spectator: usa la partida ACTIVA real via Spectator-V5. Composiciones,
  campeones y equipos son reales; kills/oro/items/niveles NO estan
  disponibles fuera del cliente (Riot no los expone) y los roles se
  infieren heuristicamente (Smite => JUNGLE; despues historial local y
  tags de Data Dragon). Nada de eso se presenta como dato real.

- replay: re-simula una partida historica de la DB "como si fuera en vivo"
  al minuto N, escalando las estadisticas finales. 100% offline; ideal
  para probar la app con cualquier cuenta antes de usarla con la propia.

Todo snapshot simulado lleva:
  simulated: "spectator" | "replay"
  live_signals_available: bool  (False => la inferencia no mezcla estado en vivo)
  warnings: lista de advertencias que la UI/consola deben mostrar.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.data.repositories import MatchRepository
from app.riot.data_dragon import DataDragon

logger = logging.getLogger(__name__)

SMITE_SPELL_ID = 11
ROLES = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]

# Afinidad tag de Data Dragon -> rol, usada solo como ultimo recurso.
TAG_ROLE_SCORES = {
    "Marksman": {"BOTTOM": 3, "MIDDLE": 1},
    "Support": {"UTILITY": 3},
    "Mage": {"MIDDLE": 2, "UTILITY": 1},
    "Assassin": {"MIDDLE": 2, "JUNGLE": 1},
    "Tank": {"TOP": 2, "UTILITY": 1, "JUNGLE": 1},
    "Fighter": {"TOP": 2, "JUNGLE": 1},
}


# --------------------------------------------------------------- helpers

def _historical_role(repo: MatchRepository, champion: str) -> str | None:
    """Rol mas frecuente del campeon en la muestra local, si existe."""
    df = repo.participants_df(enriched=False)
    if df.empty:
        return None
    subset = df[(df["championName"] == champion) & df["teamPosition"].notna()]
    if subset.empty:
        return None
    return subset["teamPosition"].mode().iloc[0]


def _infer_roles(
    team: list[dict], repo: MatchRepository, ddragon: DataDragon
) -> dict[int, str]:
    """Asigna los 5 roles a un equipo de spectator. Heuristica, no dato real.

    Prioridad: Smite => JUNGLE; luego rol historico del campeon en la DB
    local; luego tags de Data Dragon; el resto rellena los roles libres.
    Devuelve {indice_en_team: rol}.
    """
    assigned: dict[int, str] = {}
    free_roles = set(ROLES)

    # 1) Smite delata al jungla
    for i, player in enumerate(team):
        spells = {player.get("spell1Id"), player.get("spell2Id")}
        if SMITE_SPELL_ID in spells and "JUNGLE" in free_roles:
            assigned[i] = "JUNGLE"
            free_roles.discard("JUNGLE")
            break

    # 2) Puntuar candidatos (historial local pesa mas que los tags)
    candidates: list[tuple[float, int, str]] = []
    for i, player in enumerate(team):
        if i in assigned:
            continue
        champ = player["championName"]
        hist = _historical_role(repo, champ)
        for role in ROLES:
            score = 0.0
            if hist == role:
                score += 5.0
            for tag in ddragon.champion_info(champ).get("tags", []):
                score += TAG_ROLE_SCORES.get(tag, {}).get(role, 0)
            candidates.append((score, i, role))

    # 3) Asignacion greedy de mayor a menor afinidad
    for score, i, role in sorted(candidates, reverse=True):
        if i in assigned or role not in free_roles:
            continue
        assigned[i] = role
        free_roles.discard(role)

    # 4) Relleno de sobrantes
    for i, _ in enumerate(team):
        if i not in assigned:
            assigned[i] = free_roles.pop() if free_roles else None
    return assigned


def _base_player(riot_id: str, champion: str, team: str, position: str | None,
                 ddragon: DataDragon) -> dict[str, Any]:
    return {
        "riot_id": riot_id,
        "champion": champion,
        "champion_image_url": ddragon.champion_image_url(champion),
        "team": team,
        "position": position,
        "level": None,
        "kills": 0, "deaths": 0, "assists": 0,
        "creep_score": None, "ward_score": None,
        "is_dead": False, "respawn_timer": None,
        "items": [],
        "damage_profile": ddragon.champion_damage_profile(champion) if champion else None,
    }


def _finish_snapshot(snapshot: dict, ddragon: DataDragon) -> dict:
    enemies = snapshot["enemies"]
    profiles = [p["damage_profile"] for p in enemies if p.get("damage_profile")]
    if profiles:
        physical = sum(pr["physical"] for pr in profiles) / len(profiles)
        snapshot["enemy_damage_mix"] = {
            "physical": round(physical, 3), "magic": round(1 - physical, 3)
        }
    else:
        snapshot["enemy_damage_mix"] = None
    me = snapshot["me"]
    if me and me.get("position"):
        snapshot["direct_rival"] = next(
            (p for p in enemies if p.get("position") == me["position"]), None
        )
    else:
        snapshot["direct_rival"] = None
    return snapshot


# ------------------------------------------------------------- spectator

def snapshot_from_spectator(
    active_game: dict,
    repo: MatchRepository,
    ddragon: DataDragon,
    my_puuid: str | None,
) -> dict[str, Any] | None:
    """Snapshot desde Spectator-V5: composicion real, sin senales en vivo."""
    participants = active_game.get("participants") or []
    if not participants:
        return None

    by_team: dict[str, list[dict]] = {"ORDER": [], "CHAOS": []}
    for p in participants:
        champ = ddragon.champion_by_key(p.get("championId", -1))
        p["championName"] = champ["id"] if champ else f"Champ{p.get('championId')}"
        by_team["ORDER" if p.get("teamId") == 100 else "CHAOS"].append(p)

    players: list[dict] = []
    me = None
    for team_name, team in by_team.items():
        roles = _infer_roles(team, repo, ddragon)
        for i, raw in enumerate(team):
            riot_id = raw.get("riotId") or raw.get("summonerName") or (raw.get("puuid") or "?")[:12]
            player = _base_player(riot_id, raw["championName"], team_name, roles.get(i), ddragon)
            players.append(player)
            if my_puuid and raw.get("puuid") == my_puuid:
                me = player

    if me is None:
        me = players[0]

    my_team = me["team"]
    snapshot = {
        "game_time_seconds": max(0, active_game.get("gameLength") or 0),
        "game_mode": active_game.get("gameMode"),
        "me": me,
        "my_team": my_team,
        "active_player": {"current_gold": None, "level": None, "champion_stats": {}},
        "allies": [p for p in players if p["team"] == my_team and p is not me],
        "enemies": [p for p in players if p["team"] != my_team],
        "events_summary": None,
        "data_source": "partida_activa",
        "simulated": "spectator",
        "live_signals_available": False,
        "warnings": [
            "Partida detectada via Spectator-V5: composiciones y campeones "
            "son reales, pero kills/oro/items/niveles solo estaran disponibles "
            "cuando el Live Client local responda en esta maquina.",
            "Los roles se infirieron heuristicamente (Smite, historial local, "
            "arquetipos): pueden no coincidir con los reales.",
        ],
    }
    return _finish_snapshot(snapshot, ddragon)


# ---------------------------------------------------------------- replay

def snapshot_from_replay(
    repo: MatchRepository,
    ddragon: DataDragon,
    my_puuid: str | None = None,
    match_id: str | None = None,
    minute: float = 15.0,
) -> dict[str, Any] | None:
    """Re-simula una partida historica de la DB al minuto dado (offline)."""
    df = repo.participants_df(enriched=True)
    if df.empty:
        logger.error("Replay imposible: no hay partidas en la DB local.")
        return None

    if match_id is None:
        pool = df[df["puuid"] == my_puuid] if my_puuid and (df["puuid"] == my_puuid).any() else df
        match_id = pool.sort_values("gameCreation", ascending=False)["matchId"].iloc[0]

    match_df = df[df["matchId"] == match_id]
    if match_df.empty:
        logger.error("Replay: matchId %s no existe en la DB.", match_id)
        return None

    duration = float(match_df["gameDuration"].iloc[0] or 1800)
    seconds = min(minute * 60.0, duration)
    frac = seconds / duration

    def scale(value, as_int=True):
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        scaled = float(value) * frac
        return int(round(scaled)) if as_int else round(scaled, 1)

    players, me = [], None
    for _, row in match_df.iterrows():
        team = "ORDER" if row["teamId"] == 100 else "CHAOS"
        riot_id = f"{row.get('riotIdGameName') or row.get('summonerName') or '?'}#{row.get('riotIdTagline') or ''}".rstrip("#")
        player = _base_player(riot_id, row["championName"], team, row.get("teamPosition"), ddragon)
        n_items = max(1, int(round(6 * frac)))
        item_ids = [int(row[f"item{i}"]) for i in range(6) if row.get(f"item{i}") and int(row[f"item{i}"]) > 0]
        player.update({
            "level": max(1, scale(row.get("champLevel")) or 1),
            "kills": scale(row.get("kills")) or 0,
            "deaths": scale(row.get("deaths")) or 0,
            "assists": scale(row.get("assists")) or 0,
            "creep_score": scale(row.get("csTotal")),
            "items": [
                {"id": iid, "name": ddragon.item_name(iid),
                 "image_url": ddragon.item_image_url(iid), "count": 1}
                for iid in item_ids[:n_items]
            ],
        })
        players.append(player)
        if my_puuid and row["puuid"] == my_puuid:
            me = player

    if me is None:
        me = players[0]
    my_team = me["team"]

    teams_df = repo.teams_df()
    events = {"ORDER": {}, "CHAOS": {}, "first_blood": None}
    match_teams = teams_df[teams_df["matchId"] == match_id]
    for _, trow in match_teams.iterrows():
        side = "ORDER" if trow["teamId"] == 100 else "CHAOS"
        events[side] = {
            "dragons": scale(trow.get("dragon_kills")) or 0,
            "barons": scale(trow.get("baron_kills")) or 0,
            "heralds": scale(trow.get("riftHerald_kills")) or 0,
            "turrets": scale(trow.get("tower_kills")) or 0,
            "grubs": scale(trow.get("horde_kills")) or 0,
        }

    snapshot = {
        "game_time_seconds": seconds,
        "game_mode": "CLASSIC",
        "me": me,
        "my_team": my_team,
        "active_player": {
            "current_gold": None,  # el oro exacto por minuto requeriria timeline
            "level": me["level"],
            "champion_stats": {},
        },
        "allies": [p for p in players if p["team"] == my_team and p is not me],
        "enemies": [p for p in players if p["team"] != my_team],
        "events_summary": events,
        "data_source": "historico",
        "simulated": "replay",
        "live_signals_available": True,
        "replay_match_id": match_id,
        "warnings": [
            f"MODO PRUEBA (replay): esto es la partida historica {match_id} "
            f"re-simulada al minuto {seconds / 60:.0f} escalando estadisticas "
            "finales. No es una partida en curso.",
        ],
    }
    return _finish_snapshot(snapshot, ddragon)

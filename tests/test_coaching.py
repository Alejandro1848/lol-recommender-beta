"""Tests del coaching de momento: timers, desempeno por jugador y coach."""
from __future__ import annotations

from app.coaching.coach_engine import CoachEngine
from app.coaching.objective_timers import (
    BARON_FIRST_SPAWN,
    DRAGON_FIRST_SPAWN,
    DRAGON_RESPAWN,
    next_objective,
    objective_timers,
)
from app.coaching.team_performance import (
    estimate_gold_diff,
    player_performance,
    team_performance_summary,
)


def _player(champion, role, team, kills=0, deaths=0, assists=0, cs=0, level=1, riot_id=None):
    return {
        "riot_id": riot_id or f"{champion}#TST",
        "champion": champion,
        "team": team,
        "position": role,
        "level": level,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "creep_score": cs,
        "items": [],
    }


def _snapshot(minute=15.0, objective_events=None, live_signals=True):
    """Snapshot sintetico: mi equipo (ORDER) va por delante."""
    me = _player("Ahri", "MIDDLE", "ORDER", kills=5, deaths=1, assists=4, cs=int(minute * 7.5), level=12, riot_id="me#TST")
    allies = [
        _player("Garen", "TOP", "ORDER", kills=2, deaths=2, assists=2, cs=int(minute * 6), level=11),
        _player("Malphite", "JUNGLE", "ORDER", kills=1, deaths=1, assists=6, cs=int(minute * 5), level=10),
        _player("Jinx", "BOTTOM", "ORDER", kills=4, deaths=1, assists=3, cs=int(minute * 7.5), level=11),
        _player("Lux", "UTILITY", "ORDER", kills=0, deaths=2, assists=8, cs=int(minute * 1), level=9),
    ]
    enemies = [
        _player("Malphite", "TOP", "CHAOS", kills=1, deaths=3, assists=1, cs=int(minute * 5.5), level=10),
        _player("Garen", "JUNGLE", "CHAOS", kills=1, deaths=2, assists=2, cs=int(minute * 4.5), level=9),
        _player("Zed", "MIDDLE", "CHAOS", kills=6, deaths=1, assists=1, cs=int(minute * 7), level=12),
        _player("Jinx", "BOTTOM", "CHAOS", kills=1, deaths=3, assists=1, cs=int(minute * 6.5), level=10),
        _player("Lux", "UTILITY", "CHAOS", kills=0, deaths=3, assists=3, cs=int(minute * 0.8), level=8),
    ]
    return {
        "game_time_seconds": minute * 60,
        "game_mode": "CLASSIC",
        "me": me,
        "my_team": "ORDER",
        "active_player": {"current_gold": 1800.0},
        "allies": allies,
        "enemies": enemies,
        "direct_rival": enemies[2],
        "events_summary": {
            "ORDER": {"dragons": 2, "barons": 0, "heralds": 0, "turrets": 3, "grubs": 0},
            "CHAOS": {"dragons": 0, "barons": 0, "heralds": 1, "turrets": 1, "grubs": 3},
            "first_blood": None,
        },
        "objective_events": objective_events or [],
        "data_source": "live_client",
        "live_signals_available": live_signals,
    }


# ------------------------------------------------------------------ timers

def test_dragon_timer_before_first_spawn():
    timers = objective_timers(240, [])
    dragon = next(t for t in timers if t["objective"] == "dragon")
    assert dragon["status"] == "en_camino"
    assert dragon["seconds_until"] == DRAGON_FIRST_SPAWN - 240


def test_dragon_timer_after_kill_uses_respawn_rule():
    events = [{"EventName": "DragonKill", "EventTime": 600.0, "KillerName": "x"}]
    timers = objective_timers(700, events)
    dragon = next(t for t in timers if t["objective"] == "dragon")
    assert dragon["status"] == "en_camino"
    assert dragon["seconds_until"] == int(600 + DRAGON_RESPAWN - 700)


def test_baron_active_after_20_minutes():
    timers = objective_timers(BARON_FIRST_SPAWN + 10, [])
    baron = next(t for t in timers if t["objective"] == "baron")
    assert baron["status"] == "activo"


def test_next_objective_prefers_imminent():
    events = [{"EventName": "DragonKill", "EventTime": 660.0, "KillerName": "x"}]
    # Minuto 15:15 -> dragon reaparece en 45s, heraldo ya esta activo.
    timers = objective_timers(915, events)
    upcoming = next_objective(timers)
    assert upcoming is not None
    # El heraldo activo gana al dragon en camino.
    assert upcoming["objective"] == "herald"


def test_next_objective_none_when_far():
    timers = objective_timers(60, [])
    assert next_objective(timers) is None


# ------------------------------------------------------------- desempeno

def test_player_performance_fed_vs_struggling():
    fed = player_performance(
        _player("Zed", "MIDDLE", "CHAOS", kills=8, deaths=1, assists=2, cs=120), minute=15
    )
    feeding = player_performance(
        _player("Lux", "UTILITY", "CHAOS", kills=0, deaths=6, assists=1, cs=10), minute=15
    )
    assert fed["score"] > feeding["score"]
    assert fed["fed"] is True
    assert feeding["struggling"] is True


def test_early_minutes_dampen_score():
    # Mismo desempeno relativo (3 kills, CS al ritmo esperado): el indice
    # temprano debe pesar menos que el tardio.
    early = player_performance(
        _player("Zed", "MIDDLE", "CHAOS", kills=3, cs=int(3 * 7)), minute=3
    )
    late = player_performance(
        _player("Zed", "MIDDLE", "CHAOS", kills=3, cs=int(20 * 7)), minute=20
    )
    assert abs(early["score"]) < abs(late["score"])


def test_team_summary_identifies_threat_and_win_condition():
    summary = team_performance_summary(_snapshot())
    assert summary["top_threat"]["champion"] == "Zed"
    assert summary["win_condition"]["champion"] in ("Ahri", "Jinx")
    assert "MIDDLE" in summary["lane_status"]


def test_gold_estimate_is_labeled_estimate():
    gold = estimate_gold_diff(_snapshot())
    assert gold["is_estimate"] is True
    assert isinstance(gold["gold_diff"], int)
    # ORDER lleva mas kills/torres/dragones: la estimacion debe ser positiva.
    assert gold["gold_diff"] > 0


# ------------------------------------------------------------------ coach

def _recommendations(probability=0.62):
    return {
        "win_probability": {
            "probability": probability, "confidence": "alta",
            "method": "modelo_live_lightgbm", "warnings": [],
        },
        "items": [{
            "title": "Item 3031", "image_url": "https://fake/item/3031.png",
            "confidence": "alta",
            "extra": {"item_id": 3031, "gold_cost": 3450, "remaining_gold": 1650,
                      "reasons": ["mejora directa de Item 3006"]},
        }],
        "ganks": [{"title": "1. Gank hacia Top", "extra": {"role": "TOP", "score": 2.0}}],
        "objectives": [],
    }


def test_item_priorities_core_then_boots_then_alt():
    """Plan de compra: nucleo primero, botas segundas, alternativa tercera;
    sin botas candidatas solo quedan 2 items nucleo."""
    def rec(item_id, title, is_boots=False):
        return {
            "title": title, "confidence": "alta",
            "extra": {"item_id": item_id, "is_boots": is_boots,
                      "stat_summary": "+80 AP", "remaining_gold": 100},
        }

    engine = CoachEngine()
    with_boots = {"items": [
        rec(3006, "Botas Zerker", is_boots=True),
        rec(3115, "Diente de Nashor"),
        rec(3089, "Sombrero"),
        rec(3157, "Reloj"),
    ]}
    plan = engine._item_priorities(with_boots)
    assert [p["name"] for p in plan] == ["Diente de Nashor", "Botas Zerker", "Sombrero"]
    assert plan[1]["is_boots"] is True

    without_boots = {"items": [rec(3115, "Nashor"), rec(3089, "Sombrero"), rec(3157, "Reloj")]}
    plan2 = engine._item_priorities(without_boots)
    assert [p["name"] for p in plan2] == ["Nashor", "Sombrero"]
    assert all("stat_summary" in p for p in plan2)


def test_coach_no_game():
    payload = CoachEngine().build(None, None)
    assert payload["in_game"] is False
    assert "message" in payload


def test_coach_full_payload_structure():
    payload = CoachEngine().build(_snapshot(), _recommendations())
    assert payload["in_game"] is True
    assert payload["probability"]["value"] == 0.62
    assert payload["role"] == "MIDDLE"
    assert payload["next_action"]["title"]
    assert payload["item_priority"]["name"] == "Item 3031"
    assert payload["item_priority"]["gold_note"] == "faltan 1650 de oro"
    assert payload["top_threat"]["champion"] == "Zed"
    assert payload["gold_diff"]["is_estimate"] is True


def test_coach_objective_action_when_spawn_imminent():
    # Dragon muerto en 10:00 -> reaparece 15:00; snapshot al 14:30.
    events = [{"EventName": "DragonKill", "EventTime": 600.0, "KillerName": "me#TST"}]
    snapshot = _snapshot(minute=14.5, objective_events=events)
    # Sin heraldo activo en el snapshot? A los 14.5 min el heraldo esta activo;
    # se marca su kill para que el dragon inminente mande.
    events.append({"EventName": "HeraldKill", "EventTime": 850.0, "KillerName": "x"})
    payload = CoachEngine().build(snapshot, _recommendations())
    action = payload["next_action"]
    assert action["objective"] == "dragon"
    assert action["timer_seconds"] == 30
    assert "Dragon" in action["title"]
    # Rol MIDDLE: la accion pide empujar y rotar.
    assert "mid" in action["title"].lower()


def test_coach_role_actions_differ_by_role():
    titles = {}
    for role in ("JUNGLE", "MIDDLE", "TOP", "BOTTOM", "UTILITY"):
        snapshot = _snapshot(minute=8)
        snapshot["me"]["position"] = role
        payload = CoachEngine().build(snapshot, _recommendations())
        titles[role] = payload["next_action"]["title"]
    assert len(set(titles.values())) == 5  # cada rol recibe un consejo distinto


def test_coach_behind_advises_caution_on_objectives():
    events = [
        {"EventName": "DragonKill", "EventTime": 600.0, "KillerName": "x"},
        {"EventName": "HeraldKill", "EventTime": 850.0, "KillerName": "x"},
    ]
    snapshot = _snapshot(minute=14.5, objective_events=events)
    # Invertir el marcador: mi equipo va muy por detras.
    snapshot["events_summary"] = {
        "ORDER": {"dragons": 0, "barons": 0, "heralds": 0, "turrets": 0, "grubs": 0},
        "CHAOS": {"dragons": 3, "barons": 0, "heralds": 1, "turrets": 5, "grubs": 3},
        "first_blood": None,
    }
    for ally in snapshot["allies"] + [snapshot["me"]]:
        ally["kills"], ally["deaths"] = 0, 4
    payload = CoachEngine().build(snapshot, _recommendations(probability=0.25))
    detail = payload["next_action"]["detail"]
    assert "cedelo" in detail or "no lo disputes" in detail or "pick" in detail


def test_coach_without_live_signals_warns():
    snapshot = _snapshot(live_signals=False)
    payload = CoachEngine().build(snapshot, _recommendations(probability=None))
    assert payload["gold_diff"] is None
    assert payload["top_threat"] is None
    assert any("Live Client" in w for w in payload["warnings"])


def test_coach_baron_buff_overrides_objectives():
    """Tras un BaronKill aliado reciente, la accion es empujar con el buff,
    no volver a recomendar el Baron."""
    events = [{"EventName": "BaronKill", "EventTime": 1500.0, "KillerName": "me"}]
    snapshot = _snapshot(minute=25.5, objective_events=events)  # 30s despues
    payload = CoachEngine().build(snapshot, _recommendations())
    action = payload["next_action"]
    assert action["objective"] == "baron_buff"
    assert "buff" in action["title"].lower()


def test_coach_enemy_baron_kill_not_buff_action():
    events = [{"EventName": "BaronKill", "EventTime": 1500.0, "KillerName": "Enemigo"}]
    snapshot = _snapshot(minute=25.5, objective_events=events)
    payload = CoachEngine().build(snapshot, _recommendations())
    assert payload["next_action"]["objective"] != "baron_buff"
    # El timer del Baron enemigo si corre: reaparece en 6 min desde el kill.
    baron = next(t for t in payload["objective_timers"] if t["objective"] == "baron")
    assert baron["status"] == "en_camino"


def test_coach_extrapolates_stale_snapshot_clock():
    """Snapshot cacheado hace 20s: el reloj y los timers deben correr."""
    snapshot = _snapshot(minute=4.0)  # 240s; dragon aparece a los 300s
    fresh = CoachEngine().build(snapshot, _recommendations())
    stale = CoachEngine().build(
        snapshot, _recommendations(), snapshot_age_seconds=20.0
    )
    assert stale["game_time_seconds"] == fresh["game_time_seconds"] + 20
    dragon_fresh = next(t for t in fresh["objective_timers"] if t["objective"] == "dragon")
    dragon_stale = next(t for t in stale["objective_timers"] if t["objective"] == "dragon")
    assert dragon_stale["seconds_until"] == dragon_fresh["seconds_until"] - 20


def test_coach_active_objective_asks_for_confirmation():
    """El fraseo de un objetivo 'arriba' debe pedir confirmarlo en el mapa
    (el estado puede venir con hasta REFRESH_SECONDS de retraso)."""
    events = [{"EventName": "HeraldKill", "EventTime": 850.0, "KillerName": "x"}]
    snapshot = _snapshot(minute=16, objective_events=events)
    payload = CoachEngine().build(snapshot, _recommendations())
    action = payload["next_action"]
    assert action["objective"] == "dragon"
    assert "confirmalo" in action["title"]


# Eventos que cierran todos los objetivos cercanos al minuto 8 (dragon
# recien muerto, larvas tomadas): el coach cae a la rutina por rol.
NO_OBJECTIVE_EVENTS = [
    {"EventName": "DragonKill", "EventTime": 450.0, "KillerName": "x"},
    {"EventName": "HordeKill", "EventTime": 400.0, "KillerName": "x"},
]


def test_coach_numbers_advantage_overrides_everything():
    snapshot = _snapshot(minute=15)
    for enemy in snapshot["enemies"][:3]:
        enemy["is_dead"] = True
    payload = CoachEngine().build(snapshot, _recommendations())
    action = payload["next_action"]
    assert action["objective"] == "numeros"
    assert "3 enemigos muertos" in action["title"]
    assert action["urgency"] == "ahora"


def test_coach_dead_rival_gives_tempo_action():
    snapshot = _snapshot(minute=8, objective_events=list(NO_OBJECTIVE_EVENTS))
    snapshot["enemies"][2]["is_dead"] = True   # Zed, rival directo de mid
    snapshot["enemies"][2]["respawn_timer"] = 22.0
    payload = CoachEngine().build(snapshot, _recommendations())
    action = payload["next_action"]
    assert action["objective"] == "tempo"
    assert "Zed" in action["title"]
    assert action["timer_seconds"] == 22


def test_support_playbook_roams_by_lane_state():
    engine = CoachEngine()
    # Mid aliado va por delante: el soporte debe roamear.
    title, _ = engine._playbook(
        "UTILITY", 8, "pareja", None, None,
        {"MIDDLE": {"diff": 1.2}, "BOTTOM": {"diff": 0.3}}, {},
    )
    assert "roamea a Mid" in title
    # Bot va perdiendo: el soporte se queda con su ADC.
    title, _ = engine._playbook(
        "UTILITY", 8, "pareja", None, None,
        {"MIDDLE": {"diff": 1.2}, "BOTTOM": {"diff": -1.5}}, {},
    )
    assert "Quedate con tu ADC" in title
    # Mid game: cubrir la linea aliada mas presionada.
    title, _ = engine._playbook(
        "UTILITY", 18, "pareja", None, None,
        {"TOP": {"diff": -2.0}, "BOTTOM": {"diff": 0.5}}, {},
    )
    assert "Cubre Top" in title


def test_coach_warns_enemy_soul_point():
    snapshot = _snapshot(minute=15)  # dragon activo -> accion de dragon
    snapshot["events_summary"]["ORDER"]["dragons"] = 0
    snapshot["events_summary"]["CHAOS"]["dragons"] = 3
    payload = CoachEngine().build(snapshot, _recommendations())
    assert any("alma" in r.lower() for r in payload["next_action"]["reasons"])


def test_coach_unknown_role_generic_advice():
    snapshot = _snapshot()
    snapshot["me"]["position"] = None
    payload = CoachEngine().build(snapshot, _recommendations())
    assert payload["next_action"]["title"]
    assert any("Rol no reportado" in w for w in payload["warnings"])

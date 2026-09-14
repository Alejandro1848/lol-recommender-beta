"""Tests de la revision post-partida: curva, inflexiones, resumen y patrones."""
from __future__ import annotations

import pandas as pd
import pytest

from app.analytics.postgame_review import (
    build_summary,
    detect_turning_points,
    perspective_frame,
    probability_curve,
    weekly_patterns,
)


def _timeline(minutes: int = 30, **overrides) -> pd.DataFrame:
    """Timeline sintetica de una partida (equipo 100 neutral por defecto)."""
    rows = []
    for m in range(minutes + 1):
        row = {
            "matchId": "TEST_1", "minute": m,
            "kills_100": 0, "kills_200": 0,
            "level_100": 5 * min(m, 18), "level_200": 5 * min(m, 18),
            "cs_100": 8 * m * 5, "cs_200": 8 * m * 5,
            "turrets_100": 0, "turrets_200": 0,
            "dragons_100": 0, "dragons_200": 0,
            "barons_100": 0, "barons_200": 0,
        }
        for key, fn in overrides.items():
            row[key] = fn(m)
        rows.append(row)
    return pd.DataFrame(rows)


def test_perspective_flips_sign_for_red_team():
    tl = _timeline(10, kills_100=lambda m: m)  # azul acumula kills
    blue = perspective_frame(tl, 100)
    red = perspective_frame(tl, 200)
    assert blue["kill_diff"].iloc[-1] == 10
    assert red["kill_diff"].iloc[-1] == -10


def test_curve_starts_at_anchor_and_reacts_to_advantage():
    tl = _timeline(
        30,
        kills_100=lambda m: max(0, (m - 10) * 2),   # ventaja azul creciente
        turrets_100=lambda m: max(0, (m - 15) // 3),
        dragons_100=lambda m: max(0, (m - 12) // 8),
    )
    frame = perspective_frame(tl, 100)
    curve, method = probability_curve(frame, model_payload=None)
    assert method == "heuristico_baseline"
    # Minuto 0: peso live 0 -> exactamente el ancla del 50%.
    assert curve["probability"].iloc[0] == pytest.approx(0.5)
    # Con ventaja grande al final, la probabilidad debe superar claramente 50%.
    assert curve["probability"].iloc[-1] > 0.65
    # Y desde la perspectiva del equipo rojo, lo contrario.
    curve_red, _ = probability_curve(perspective_frame(tl, 200), None)
    assert curve_red["probability"].iloc[-1] < 0.35


def test_turning_points_detect_baron_throw():
    # Azul domina hasta el minuto 22; en el 23 pierde Baron + 4 kills.
    tl = _timeline(
        30,
        kills_100=lambda m: min(m, 10),
        turrets_100=lambda m: min(m // 5, 4),
        kills_200=lambda m: 0 if m < 23 else 8,
        barons_200=lambda m: 0 if m < 23 else 1,
    )
    curve, _ = probability_curve(perspective_frame(tl, 100), None)
    points = detect_turning_points(curve)
    assert points, "el throw del Baron debe detectarse como inflexion"
    drops = [p for p in points if p["direction"] == "caida"]
    assert drops
    main = max(drops, key=lambda p: abs(p["swing"]))
    assert 20 <= main["minute"] <= 24
    causes = " ".join(main["causes"])
    assert "Baron" in causes or "pelea" in causes
    assert "cayo" in main["description"]
    # No mas de 3 puntos y sin solaparse.
    assert len(points) <= 3


def test_turning_points_empty_on_flat_game():
    curve, _ = probability_curve(perspective_frame(_timeline(30), 100), None)
    assert detect_turning_points(curve) == []


def test_summary_is_five_lines_and_flags_throw():
    tl = _timeline(
        32,
        kills_100=lambda m: min(m, 12),
        turrets_100=lambda m: min(m // 4, 6),
        kills_200=lambda m: 0 if m < 26 else 14,
        barons_200=lambda m: 0 if m < 26 else 1,
        turrets_200=lambda m: 0 if m < 27 else 5,
    )
    curve, _ = probability_curve(perspective_frame(tl, 100), None)
    points = detect_turning_points(curve)
    match = {"champion": "Jhin", "opponent_champion": "Jinx", "win": False,
             "duration_min": 33.0, "kda_text": "5/7/9"}
    lines = build_summary(match, curve, points)
    assert len(lines) == 5
    assert "Derrota" in lines[0] and "Jhin" in lines[0]
    joined = " ".join(lines)
    assert "Throw detectado" in joined
    assert "Consejo" in lines[4]


def test_weekly_patterns_close_earlier_insight():
    # 4 cortas ganadas, 4 largas perdidas -> insight de cerrar antes.
    reviews = (
        [{"win": True, "duration_min": 26.0, "peak": 0.8, "low": 0.45, "p_at_15": 0.6}] * 4
        + [{"win": False, "duration_min": 38.0, "peak": 0.7, "low": 0.2, "p_at_15": 0.6}] * 4
    )
    result = weekly_patterns(reviews)
    assert result["available"] and result["games"] == 8
    assert result["throws"] == 4  # las largas llegaron a 70% y se perdieron
    joined = " ".join(result["insights"])
    assert "cerrar antes" in joined or "cierra antes" in joined.lower()
    assert "throw" in joined.lower()


def test_weekly_patterns_empty():
    result = weekly_patterns([])
    assert result["available"] is False and result["games"] == 0


def test_chat_intents_route_to_review(monkeypatch):
    """El chat enruta '¿donde se perdio?' y 'patrones' a los handlers nuevos."""
    from app.chat.chat_service import ChatService

    class FakeReview:
        def review_last(self, puuid):
            return {
                "available": True, "champion": "Jhin", "opponent_champion": "Jinx",
                "summary_lines": ["l1", "l2", "l3", "l4", "l5"],
                "turning_points": [
                    {"description": "d1", "direction": "caida"},
                    {"description": "d2", "direction": "subida"},
                ],
                "method": "modelo_live_xgboost",
            }

        def patterns(self, puuid, limit=20):
            return {"available": True, "games": 8, "winrate": 0.5, "throws": 2,
                    "comebacks": 1, "insights": ["insight-1"], "warnings": []}

    chat = ChatService(
        repo=None, ddragon=None, scout=None,
        live_state_fn=lambda: (None, None),
        my_puuid_fn=lambda: "PUUID",
        review_service=FakeReview(),
    )
    r1 = chat.answer("¿Donde se perdio la partida?")
    assert r1["intent"] == "match_review" and r1["data_available"]
    assert "l1" in r1["answer"] and "d2" in r1["answer"]

    r2 = chat.answer("¿Que patrones tengo esta semana?")
    assert r2["intent"] == "weekly_patterns" and r2["data_available"]
    assert "insight-1" in r2["answer"]

    r3 = chat.answer("resumen de mi ultima partida")
    assert r3["intent"] == "match_review"

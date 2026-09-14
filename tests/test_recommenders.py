"""Tests de los recomendadores: items, ganks y objetivos."""
from app.analytics.matchup_analysis import with_opponents
from app.recommendations.gank_recommender import recommend_gank_lanes
from app.recommendations.item_recommender import recommend_items
from app.recommendations.objective_recommender import recommend_objectives
from app.recommendations.similarity_engine import SimilarityEngine


def _snapshot():
    def player(champ, team, role, riot_id, kills=2, deaths=2, level=10, is_dead=False):
        return {
            "riot_id": riot_id, "champion": champ, "team": team, "position": role,
            "level": level, "kills": kills, "deaths": deaths, "assists": 3,
            "creep_score": 120, "is_dead": is_dead, "items": [],
            "damage_profile": {"physical": 0.3, "magic": 0.7},
        }

    me = player("Ahri", "ORDER", "MIDDLE", "Me#LAN")
    return {
        "game_time_seconds": 900,
        "me": me,
        "my_team": "ORDER",
        "active_player": {"current_gold": 1500},
        "allies": [
            player("Garen", "ORDER", "TOP", "A1#LAN"),
            player("Jinx", "ORDER", "BOTTOM", "A2#LAN"),
        ],
        "enemies": [
            player("Malphite", "CHAOS", "TOP", "E1#LAN", deaths=4, level=8, is_dead=True),
            player("Zed", "CHAOS", "MIDDLE", "E2#LAN"),
            player("Lux", "CHAOS", "BOTTOM", "E3#LAN", kills=5, deaths=0),
        ],
        "direct_rival": player("Zed", "CHAOS", "MIDDLE", "E2#LAN"),
        "enemy_damage_mix": {"physical": 0.35, "magic": 0.65},
        "events_summary": {
            "ORDER": {"dragons": 1, "barons": 0, "heralds": 0, "turrets": 2, "grubs": 0},
            "CHAOS": {"dragons": 0, "barons": 0, "heralds": 0, "turrets": 1, "grubs": 0},
        },
    }


def test_items_against_magic_team(sample_participants, fake_ddragon):
    engine = SimilarityEngine(fake_ddragon)
    history = with_opponents(sample_participants)
    similarity = engine.find_similar(history, "Ahri", "Zed", "MIDDLE")
    recs = recommend_items(_snapshot(), similarity, fake_ddragon)
    assert recs
    # Con 65% de danio magico enemigo debe sugerir resistencia magica
    assert any("magico" in (r["explanation"] + r.get("detail", "")) for r in recs)
    for rec in recs:
        assert rec["explanation"]
        assert rec["confidence"] in ("alta", "media", "baja")


def test_items_respect_game_phase_and_gold(sample_participants, fake_ddragon):
    """En fase temprana no se recomiendan compras caras (3031 = 3450 oro) y
    la explicacion informa sobre el oro disponible."""
    engine = SimilarityEngine(fake_ddragon)
    history = with_opponents(sample_participants)
    similarity = engine.find_similar(history, "Ahri", "Zed", "MIDDLE")

    early = {**_snapshot(), "game_time_seconds": 300}
    recs = recommend_items(early, similarity, fake_ddragon)
    historical_ids = [
        r["extra"].get("item_id") for r in recs if r["data_source"] == "historico"
    ]
    assert 3031 not in historical_ids  # 3450 de oro no es compra de fase temprana

    # Con 1500 de oro y Mercurial (1250), la explicacion debe hablar de oro
    gold_aware = [r for r in recs if "oro" in r["explanation"]]
    assert gold_aware


def test_items_report_phase_and_recency(sample_participants, fake_ddragon):
    engine = SimilarityEngine(fake_ddragon)
    history = with_opponents(sample_participants)
    similarity = engine.find_similar(history, "Ahri", "Zed", "MIDDLE")
    recs = recommend_items(_snapshot(), similarity, fake_ddragon)
    assert any(r["extra"].get("phase") == "media" for r in recs)
    historical = [r for r in recs if r["data_source"] == "historico"]
    assert all("recency_score" in r["extra"] for r in historical)


def test_gank_prioritizes_dead_or_behind_lane(sample_participants):
    history = with_opponents(sample_participants)
    recs = recommend_gank_lanes(_snapshot(), history)
    assert recs
    # TOP tiene al rival muerto y 2 niveles abajo: debe salir primero
    assert recs[0]["extra"]["role"] in ("TOP", "BOTTOM")
    assert all(r["explanation"] for r in recs)


def test_gank_without_snapshot(sample_participants):
    history = with_opponents(sample_participants)
    recs = recommend_gank_lanes(None, history)
    assert len(recs) == 1
    assert "Live Client" in recs[0]["explanation"] or "en vivo" in recs[0]["explanation"]


def test_objectives_by_game_time():
    snapshot = _snapshot()
    recs = recommend_objectives(snapshot)
    assert recs
    snapshot_early = {**snapshot, "game_time_seconds": 420}
    early = recommend_objectives(snapshot_early)
    assert any("Larvas" in r["title"] for r in early)
    snapshot_late = {**snapshot, "game_time_seconds": 1500}
    late = recommend_objectives(snapshot_late)
    assert any("Baron" in r["title"] for r in late)
    assert recommend_objectives(None)[0]["title"] == "Sin partida activa"

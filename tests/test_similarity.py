"""Tests de la jerarquia de similitud."""
from app.analytics.matchup_analysis import with_opponents
from app.recommendations.similarity_engine import SimilarityEngine, confidence_from_sample


def test_confidence_thresholds():
    assert confidence_from_sample(25) == "alta"
    assert confidence_from_sample(10) == "media"
    assert confidence_from_sample(3) == "baja"


def test_level2_match(sample_participants, fake_ddragon):
    engine = SimilarityEngine(fake_ddragon)
    history = with_opponents(sample_participants)
    result = engine.find_similar(history, "Ahri", "Zed", "MIDDLE", queue_id=420)
    # 4 partidas Ahri vs Zed en MIDDLE: alcanza el minimo en nivel 1 o 2
    assert result.level in (1, 2)
    assert result.size >= 3
    assert result.confidence in ("baja", "media")


def test_fallback_to_role_level(sample_participants, fake_ddragon):
    engine = SimilarityEngine(fake_ddragon)
    history = with_opponents(sample_participants)
    # Campeon que no existe en el historial: debe caer a nivel 4/5 (por rol)
    result = engine.find_similar(history, "Yasuo", "Aatrox", "MIDDLE")
    assert result.level in (4, 5)
    assert result.size > 0


def test_empty_history(fake_ddragon):
    import pandas as pd

    engine = SimilarityEngine(fake_ddragon)
    result = engine.find_similar(pd.DataFrame(), "Ahri", "Zed", "MIDDLE")
    assert result.level is None
    assert result.size == 0
    assert result.confidence == "baja"

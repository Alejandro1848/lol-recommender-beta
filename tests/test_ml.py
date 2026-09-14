"""Tests de evaluacion, baseline y clasificacion de estilo.

(Los tests del modelo pregame win_model se eliminaron junto con el modelo:
la probabilidad arranca en 50% fijo y la construye el modelo in-game.)
"""
from app.ml.baseline import live_state_probability, pregame_baseline_probability
from app.ml.evaluation import compare_to_baseline, evaluate_probabilities
from app.ml.player_style_model import classify_style


def test_evaluation_metrics():
    y = [1, 0, 1, 0, 1, 1, 0, 0]
    p = [0.9, 0.2, 0.7, 0.4, 0.6, 0.8, 0.3, 0.1]
    metrics = evaluate_probabilities(y, p)
    assert metrics["accuracy"] == 1.0
    assert metrics["roc_auc"] == 1.0
    comparison = compare_to_baseline(y, p)
    assert comparison["model"]["brier_score"] < comparison["baseline"]["brier_score"]


def test_baselines():
    assert pregame_baseline_probability() == 0.5
    snapshot = {
        "me": {"kills": 5, "deaths": 1, "assists": 4, "level": 12, "creep_score": 150},
        "my_team": "ORDER",
        "allies": [], "enemies": [{"kills": 1, "level": 10, "creep_score": 100}],
        "events_summary": {"ORDER": {"turrets": 3, "dragons": 2, "barons": 0},
                           "CHAOS": {"turrets": 0, "dragons": 0, "barons": 0}},
    }
    result = live_state_probability(snapshot)
    assert result["probability"] > 0.5
    assert result["factors"]


def test_style_classification(sample_participants):
    me = sample_participants[sample_participants["puuid"] == "me"]
    style = classify_style(me)
    assert style["style"] in ("agresivo", "defensivo", "neutral")
    assert style["games"] > 0
    assert "evidence" in style

"""Tests del feature engineering y el split temporal."""
from app.ml.features import (
    FEATURE_COLUMNS,
    build_feature_row,
    build_training_frame,
    split_features_target,
    temporal_split,
)


def test_feature_row_columns(fake_ddragon):
    row = build_feature_row("Ahri", "Zed", "MIDDLE", ["Garen", "Jinx"], ["Lux", "Malphite"], fake_ddragon)
    assert set(FEATURE_COLUMNS) <= set(row.keys())
    assert row["role_MIDDLE"] == 1.0
    assert row["role_TOP"] == 0.0
    assert row["own_magic"] == 8      # Ahri
    assert row["opp_attack"] == 9     # Zed
    assert 0 <= row["enemy_magic_share"] <= 1


def test_training_frame(sample_participants, fake_ddragon):
    dataset = build_training_frame(sample_participants, fake_ddragon)
    assert len(dataset) == len(sample_participants)
    X, y = split_features_target(dataset)
    assert list(X.columns) == FEATURE_COLUMNS
    assert set(y.unique()) <= {0, 1}
    # En cada partida hay ganadores y perdedores
    assert y.mean() == 0.5


def test_temporal_split_order(sample_participants, fake_ddragon):
    dataset = build_training_frame(sample_participants, fake_ddragon)
    train, test = temporal_split(dataset, test_fraction=0.25)
    assert len(train) + len(test) == len(dataset)
    assert train["gameCreation"].max() <= test["gameCreation"].min()

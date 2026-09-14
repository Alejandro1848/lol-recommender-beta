"""Tests de los fixes de items: no-comprables (mision de soporte) y
candidatos del modelo entrenado por campeon."""
from __future__ import annotations

import pandas as pd

from app.recommendations.item_recommender import recommend_items
from app.recommendations.similarity_engine import SimilarityResult
from tests.conftest import FakeDataDragon

# IDs de la linea de mision de soporte (parche 14+): Atlas del Mundo se
# compra; Brujula Runica y el item final evolucionan gratis.
WORLD_ATLAS = 3865
RUNIC_COMPASS = 3866


BASIC_BOOTS = 1001


class SupportQuestDataDragon(FakeDataDragon):
    """Fake con la linea de soporte (3865 -> 3866 no comprable) y botas."""

    ITEM_GOLD = {
        **FakeDataDragon.ITEM_GOLD,
        WORLD_ATLAS: 400, RUNIC_COMPASS: 400, BASIC_BOOTS: 300,
    }
    ITEM_TAGS = {
        BASIC_BOOTS: ["Boots"],
        3111: ["Boots"], 3047: ["Armor", "Boots"], 3020: ["Boots", "MagicPenetration"],
    }
    ITEM_FROM = {
        3111: [BASIC_BOOTS], 3047: [BASIC_BOOTS, 1029], 3020: [BASIC_BOOTS],
    }

    def item_into(self, item_id):
        return [RUNIC_COMPASS] if item_id == WORLD_ATLAS else []

    def item_purchasable(self, item_id):
        if item_id == RUNIC_COMPASS:
            return False
        if item_id in self.ITEM_GOLD:
            return True
        return None

    def item_tags(self, item_id):
        return self.ITEM_TAGS.get(item_id, [])

    def item_from(self, item_id):
        return self.ITEM_FROM.get(item_id, [])


class FakeItemModel:
    """predict_proba estilo OneVsRest: una fila, prob por item."""

    def __init__(self, probabilities):
        self._probabilities = probabilities

    def predict_proba(self, X):
        import numpy as np

        return np.array([self._probabilities])


class FakeRegistry:
    def __init__(self, payload, meta):
        self._payload, self._meta = payload, meta

    def latest(self, kind, champion, tier):
        return (self._payload, self._meta)


def _snapshot(champion="Lux", role="UTILITY", items=None):
    return {
        "game_time_seconds": 900,
        "me": {
            "riot_id": "me#TST", "champion": champion, "team": "ORDER",
            "position": role, "level": 9, "kills": 1, "deaths": 1, "assists": 5,
            "creep_score": 20,
            "items": items or [],
            "damage_profile": {"physical": 0.2, "magic": 0.8},
        },
        "my_team": "ORDER",
        "active_player": {"current_gold": 1500.0},
        "allies": [], "enemies": [],
        "direct_rival": None,
        "enemy_damage_mix": {"physical": 0.5, "magic": 0.5},
        "events_summary": {},
        "data_source": "live_client",
        "live_signals_available": True,
    }


def _empty_similarity():
    return SimilarityResult(pd.DataFrame(), 5, "test", "baja")


def test_support_quest_upgrade_not_recommended():
    """Con Atlas del Mundo en el inventario, la mejora automatica (Brujula
    Runica, no comprable) NO debe recomendarse como compra."""
    ddragon = SupportQuestDataDragon()
    snapshot = _snapshot(items=[{"id": WORLD_ATLAS, "name": "Atlas del Mundo"}])
    recs = recommend_items(snapshot, _empty_similarity(), ddragon)
    recommended_ids = {r["extra"].get("item_id") for r in recs}
    assert RUNIC_COMPASS not in recommended_ids


def test_purchasable_items_still_recommended():
    ddragon = SupportQuestDataDragon()
    recs = recommend_items(_snapshot(), _empty_similarity(), ddragon)
    # Debe seguir habiendo recomendaciones (los counters genericos son comprables).
    assert recs
    for rec in recs:
        item_id = rec["extra"].get("item_id")
        if item_id is not None:
            assert ddragon.item_purchasable(item_id) is not False


def test_model_candidates_rank_first():
    """El item con mayor probabilidad del modelo del campeon debe dominar
    la recomendacion frente a las reglas genericas."""
    ddragon = SupportQuestDataDragon()
    # El modelo predice fuerte el item 3072 y debil el 3156.
    payload = {
        "model": FakeItemModel([0.9, 0.1]),
        "item_ids": [3072, 3156],
        "feature_columns": None,  # usa FEATURE_COLUMNS por defecto
    }
    registry = FakeRegistry(payload, {"n_samples": 1200})
    recs = recommend_items(
        _snapshot(), _empty_similarity(), ddragon,
        champion_registry=registry, tier="GOLD",
    )
    top_ids = [r["extra"].get("item_id") for r in recs]
    assert 3072 in top_ids
    model_rec = next(r for r in recs if r["extra"].get("item_id") == 3072)
    assert "modelo" in model_rec["extra"]["sources"]
    assert any("90%" in reason for reason in model_rec["extra"]["reasons"])


def test_model_below_threshold_excluded():
    ddragon = SupportQuestDataDragon()
    payload = {
        "model": FakeItemModel([0.05, 0.04]),
        "item_ids": [3072, 3156],
        "feature_columns": None,
    }
    registry = FakeRegistry(payload, {})
    recs = recommend_items(
        _snapshot(), _empty_similarity(), ddragon,
        champion_registry=registry, tier="GOLD",
    )
    for rec in recs:
        assert "modelo" not in (rec["extra"].get("sources") or [])


def test_support_quest_evolution_never_recommended_even_if_model_predicts_it():
    """Data Dragon marca las evoluciones finales (Zaz'Zak & cia.) como
    purchasable=True: la denylist debe bloquearlas aunque el modelo del
    campeon las prediga con probabilidad alta."""
    from app.analytics.item_analysis import SUPPORT_QUEST_ITEM_IDS

    ddragon = SupportQuestDataDragon()
    zazzak = 3871
    payload = {
        "model": FakeItemModel([0.95, 0.6]),
        "item_ids": [zazzak, 3072],
        "feature_columns": None,
    }
    registry = FakeRegistry(payload, {"n_samples": 2427})
    recs = recommend_items(
        _snapshot(), _empty_similarity(), ddragon,
        champion_registry=registry, tier="GOLD",
    )
    recommended_ids = {r["extra"].get("item_id") for r in recs}
    assert recommended_ids.isdisjoint(SUPPORT_QUEST_ITEM_IDS)
    # El resto de la prediccion del modelo sigue entrando.
    assert 3072 in recommended_ids


def test_no_second_boots_when_boots_finished():
    """Con botas terminadas (Steelcaps), otras botas (Mercurial) no entran
    aunque el equipo enemigo sea magico."""
    ddragon = SupportQuestDataDragon()
    snapshot = _snapshot(items=[{"id": 3047, "name": "Steelcaps"}])
    snapshot["enemy_damage_mix"] = {"physical": 0.3, "magic": 0.7}
    recs = recommend_items(snapshot, _empty_similarity(), ddragon)
    recommended_ids = {r["extra"].get("item_id") for r in recs}
    assert 3111 not in recommended_ids
    assert 3047 not in recommended_ids  # ya la tienes


def test_boots_upgrade_allowed_from_basic_boots():
    """Con botas basicas si se puede recomendar su mejora directa."""
    ddragon = SupportQuestDataDragon()
    snapshot = _snapshot(items=[{"id": BASIC_BOOTS, "name": "Botas"}])
    snapshot["enemy_damage_mix"] = {"physical": 0.3, "magic": 0.7}
    recs = recommend_items(snapshot, _empty_similarity(), ddragon)
    recommended_ids = {r["extra"].get("item_id") for r in recs}
    assert 3111 in recommended_ids


def test_regression_no_steelcaps_after_sorcerer_shoes():
    """Caso real reportado: con Botas del Hechicero (3020) compradas, las
    Grebas/Punteras (3047) no deben recomendarse aunque el rival sea AD."""
    ddragon = SupportQuestDataDragon()
    snapshot = _snapshot(items=[{"id": 3020, "name": "Botas del Hechicero"}])
    snapshot["enemy_damage_mix"] = {"physical": 0.7, "magic": 0.3}
    recs = recommend_items(snapshot, _empty_similarity(), ddragon)
    recommended_ids = {r["extra"].get("item_id") for r in recs}
    assert 3047 not in recommended_ids
    assert 3111 not in recommended_ids


def test_pure_utility_item_filtered():
    """Items de pura utilidad (vida chica + movimiento, estilo Placa Lunar)
    no deben recomendarse sin respaldo de modelo o ruta de compra."""
    moonplate = 3066

    class WithMoonplate(SupportQuestDataDragon):
        ITEM_GOLD = {**SupportQuestDataDragon.ITEM_GOLD, moonplate: 800}

        def item_tags(self, item_id):
            if item_id == moonplate:
                return ["Health", "NonbootsMovement"]
            return super().item_tags(item_id)

        def item_stats(self, item_id):
            if item_id == moonplate:
                return {"FlatHPPoolMod": 200}
            return {}

    # El unico candidato viene del historial simulado via modelo debil? No:
    # se inyecta como candidato fuerte del "modelo" NO (exento); se usa una
    # fuente counter simulada apuntando al item de utilidad.
    from app.recommendations import item_recommender as ir

    fit, note = ir._item_fit_for_champion(
        ir._item_stats(WithMoonplate(), moonplate),
        {"physical": 0.45, "magic": 0.55, "tags": ["Fighter", "Mage"]},
        item_tags=["Health", "NonbootsMovement"],
        sources={"historico"},
    )
    assert fit is False
    assert "utilidad" in note
    # Con respaldo del modelo del campeon si se permite.
    fit_model, _ = ir._item_fit_for_champion(
        ir._item_stats(WithMoonplate(), moonplate),
        {"physical": 0.45, "magic": 0.55, "tags": ["Fighter", "Mage"]},
        item_tags=["Health", "NonbootsMovement"],
        sources={"modelo"},
    )
    assert fit_model is True


def test_ad_item_filtered_for_mage_class():
    """Un item AD puro no debe recomendarse a un campeon Mage aunque una
    regla generica lo proponga."""
    ddragon = SupportQuestDataDragon()
    snapshot = _snapshot()  # Lux: magic 0.8, tags via damage_profile
    snapshot["me"]["damage_profile"] = {
        "physical": 0.2, "magic": 0.8, "tags": ["Mage", "Support"],
    }
    # El modelo (fuente fuerte) propone un item AD puro: 3031 Infinity Edge.
    payload = {
        "model": FakeItemModel([0.9]),
        "item_ids": [3031],
        "feature_columns": None,
    }
    registry = FakeRegistry(payload, {})

    class WithStats(SupportQuestDataDragon):
        def item_stats(self, item_id):
            if item_id == 3031:
                return {"FlatPhysicalDamageMod": 65, "FlatCritChanceMod": 0.25}
            return {}

    recs = recommend_items(
        _snapshot() | snapshot, _empty_similarity(), WithStats(),
        champion_registry=registry, tier="GOLD",
    )
    recommended_ids = {r["extra"].get("item_id") for r in recs}
    assert 3031 not in recommended_ids


def test_effective_cost_discounts_owned_components():
    """Caso real reportado: el 'faltan X de oro' debe descontar los
    componentes que ya llevas, igual que la tienda del juego."""
    from app.recommendations.item_recommender import _effective_cost

    FINAL, COMP_A, COMP_B = 9001, 9002, 9003

    class TreeDataDragon(SupportQuestDataDragon):
        DATA = {
            FINAL: {"gold": {"base": 1000, "total": 3000}},
            COMP_A: {"gold": {"base": 1250, "total": 1250}},
            COMP_B: {"gold": {"base": 750, "total": 750}},
        }

        def item_data(self, item_id):
            return self.DATA.get(item_id)

        def item_from(self, item_id):
            return [COMP_A, COMP_B] if item_id == FINAL else []

        def item_gold(self, item_id):
            data = self.DATA.get(item_id)
            return data["gold"]["total"] if data else super().item_gold(item_id)

    ddragon = TreeDataDragon()
    # Sin componentes: costo completo.
    assert _effective_cost(ddragon, FINAL, {}) == 3000
    # Con el componente A (1250) comprado: 3000 - 1250 = 1750.
    assert _effective_cost(ddragon, FINAL, {COMP_A: 1}) == 1750
    # Con ambos componentes: solo el costo de combinacion.
    assert _effective_cost(ddragon, FINAL, {COMP_A: 1, COMP_B: 1}) == 1000


def test_remaining_gold_uses_effective_cost():
    from app.recommendations.item_recommender import _effective_cost  # noqa: F401

    FINAL, COMP_A = 9001, 9002

    class TreeDataDragon(SupportQuestDataDragon):
        DATA = {
            FINAL: {"gold": {"base": 1000, "total": 3000, "purchasable": True}},
            COMP_A: {"gold": {"base": 1250, "total": 1250, "purchasable": True}},
        }

        def item_data(self, item_id):
            # None solo para el arbol de prueba; el resto sin datos (no
            # filtra porque recommend_items exige item_data solo si existe).
            return self.DATA.get(item_id, {"gold": {}})

        def item_from(self, item_id):
            return [COMP_A] if item_id == FINAL else []

        def item_gold(self, item_id):
            data = self.DATA.get(item_id)
            return data["gold"]["total"] if data else super().item_gold(item_id)

        def item_stats(self, item_id):
            if item_id == FINAL:
                return {"FlatMagicDamageMod": 120}
            return {}

    ddragon = TreeDataDragon()
    payload = {"model": FakeItemModel([0.9]), "item_ids": [FINAL], "feature_columns": None}
    registry = FakeRegistry(payload, {})
    # 2000 de oro, item de 3000 con componente de 1250 comprado -> alcanzable.
    snapshot = _snapshot(items=[{"id": COMP_A, "name": "Componente", "count": 1}])
    snapshot["active_player"]["current_gold"] = 2000.0
    recs = recommend_items(
        snapshot, _empty_similarity(), ddragon,
        champion_registry=registry, tier="GOLD",
    )
    final_rec = next(r for r in recs if r["extra"].get("item_id") == FINAL)
    # Unico componente (1250) ya comprado: queda el costo de combinacion.
    assert final_rec["extra"]["buy_cost"] == 1000
    assert final_rec["extra"]["remaining_gold"] == 0  # 2000 >= 1000
    assert final_rec["extra"]["gold_cost"] == 3000


def test_no_registry_keeps_working():
    ddragon = SupportQuestDataDragon()
    recs = recommend_items(
        _snapshot(), _empty_similarity(), ddragon,
        champion_registry=None, tier=None,
    )
    assert recs


def test_only_one_boot_pair_is_recommended():
    ddragon = SupportQuestDataDragon()
    snapshot = _snapshot()
    snapshot["enemy_damage_mix"] = {"physical": 0.2, "magic": 0.8}
    snapshot["enemies"] = [
        {"champion": "Leona", "items": [], "damage_profile": {"physical": 0.2, "magic": 0.8}},
        {"champion": "Lux", "items": [], "damage_profile": {"physical": 0.2, "magic": 0.8}},
    ]
    recs = recommend_items(snapshot, _empty_similarity(), ddragon)
    boots = [r for r in recs if r["extra"].get("is_boots")]
    assert len(boots) == 1
    assert boots[0]["extra"]["recommendation_role"] == "botas"
    assert boots[0]["extra"]["boot_weights"]["historical"] == 0.70
    assert boots[0]["extra"]["boot_weights"]["live_composition"] == 0.30


def test_two_historical_core_items_are_prioritized():
    ddragon = SupportQuestDataDragon()
    payload = {
        "model": FakeItemModel([0.92, 0.81]),
        "item_ids": [3072, 3156],
        "feature_columns": None,
    }
    recs = recommend_items(
        _snapshot(), _empty_similarity(), ddragon,
        champion_registry=FakeRegistry(payload, {"n_samples": 1200}), tier="GOLD",
    )
    core = [r for r in recs if r["extra"].get("recommendation_role") == "principal"]
    assert len(core) == 2
    assert [r["extra"]["priority"] for r in core] == [1, 2]
    assert [r["extra"]["item_id"] for r in core] == [3072, 3156]

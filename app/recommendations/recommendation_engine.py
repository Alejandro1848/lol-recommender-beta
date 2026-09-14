"""Orquestador de recomendaciones: junta similitud, ML, items, ganks y
objetivos en una sola respuesta coherente para la API."""
from __future__ import annotations

import logging
from typing import Any

from app.analytics.matchup_analysis import with_opponents
from app.data.repositories import MatchRepository
from app.ml.inference import InferenceService
from app.recommendations.gank_recommender import recommend_gank_lanes
from app.recommendations.item_recommender import recommend_items
from app.recommendations.objective_recommender import recommend_objectives
from app.recommendations.similarity_engine import SimilarityEngine
from app.riot.data_dragon import DataDragon

logger = logging.getLogger(__name__)


class RecommendationEngine:
    def __init__(
        self,
        repo: MatchRepository,
        similarity: SimilarityEngine,
        inference: InferenceService,
        ddragon: DataDragon,
    ):
        self.repo = repo
        self.similarity = similarity
        self.inference = inference
        self.ddragon = ddragon

    def build(
        self,
        snapshot: dict | None,
        queue_id: int | None = None,
        item_style: str = "neutral",
    ) -> dict[str, Any]:
        """Respuesta completa de recomendaciones para el estado actual."""
        warnings: list[str] = []

        participants = self.repo.participants_df()
        history = with_opponents(participants) if not participants.empty else participants
        if participants.empty:
            warnings.append(
                "No hay historial local: las recomendaciones seran solo heuristicas."
            )

        similarity_result = None
        if snapshot and snapshot.get("me"):
            me = snapshot["me"]
            rival = snapshot.get("direct_rival") or {}
            similarity_result = self.similarity.find_similar(
                history,
                me.get("champion", ""),
                rival.get("champion"),
                me.get("position"),
                queue_id=queue_id,
            )
        else:
            from app.recommendations.similarity_engine import SimilarityResult
            import pandas as pd
            similarity_result = SimilarityResult(pd.DataFrame(), None, "Sin partida activa", "baja")

        if snapshot and snapshot.get("simulated"):
            warnings.extend(snapshot.get("warnings", []))

        win_probability = self.inference.win_probability(snapshot)
        warnings.extend(win_probability.get("warnings", []))

        items = recommend_items(
            snapshot, similarity_result, self.ddragon, style=item_style,
            champion_registry=self.inference.champion_registry,
            tier=self.inference.tier,
        )
        ganks = recommend_gank_lanes(snapshot, history)
        objectives = recommend_objectives(snapshot, self.repo.teams_df())

        return {
            "in_game": snapshot is not None,
            "win_probability": win_probability,
            "items": items,
            "ganks": ganks,
            "objectives": objectives,
            "similarity": similarity_result.summary() if similarity_result else None,
            "warnings": warnings,
            "message": None if snapshot else (
                "No hay partida activa. Abre una partida de LoL en esta maquina "
                "para recibir recomendaciones en vivo."
            ),
        }

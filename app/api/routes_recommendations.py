"""Ruta de recomendaciones en vivo (items, ganks, objetivos, win prob)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.container import ServiceContainer
from app.data.schemas import Recommendation, RecommendationsResponse, WinProbability

router = APIRouter(tags=["recommendations"])


def _container(request: Request) -> ServiceContainer:
    return request.app.state.container


def _to_schema(raw: dict) -> Recommendation:
    return Recommendation(
        kind=raw.get("kind", "general"),
        title=raw.get("title", ""),
        detail=raw.get("detail", ""),
        explanation=raw.get("explanation", ""),
        confidence=raw.get("confidence", "baja"),
        sample_size=raw.get("sample_size"),
        similarity_level=raw.get("similarity_level"),
        similarity_label=raw.get("similarity_label"),
        image_url=raw.get("image_url"),
        data_source=raw.get("data_source", "mixto"),
        extra=raw.get("extra", {}),
    )


@router.get("/live/recommendations", response_model=RecommendationsResponse)
def live_recommendations(request: Request, item_style: str = "neutral") -> RecommendationsResponse:
    c = _container(request)
    snapshot, recommendations = c.live_state()
    if item_style != "neutral":
        recommendations = c.engine.build(
            snapshot,
            queue_id=c.settings.queue_id,
            item_style=item_style,
        )
    recommendations = recommendations or {}
    wp = recommendations.get("win_probability")
    return RecommendationsResponse(
        in_game=recommendations.get("in_game", False),
        win_probability=WinProbability(**wp) if wp else None,
        items=[_to_schema(r) for r in recommendations.get("items", [])],
        ganks=[_to_schema(r) for r in recommendations.get("ganks", [])],
        objectives=[_to_schema(r) for r in recommendations.get("objectives", [])],
        similarity=recommendations.get("similarity"),
        warnings=recommendations.get("warnings", []),
        message=recommendations.get("message"),
    )

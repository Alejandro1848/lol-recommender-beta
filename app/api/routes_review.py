"""Rutas de la revision post-partida ("¿donde se perdio?").

- GET /api/review/matches   -> ultimas partidas propias con flag de timeline
- GET /api/review/match/{id}-> curva + puntos de inflexion + resumen 5 lineas
- GET /api/review/patterns  -> patrones agregados de las ultimas N partidas

Las revisiones pueden descargar la timeline bajo demanda (1 llamada
Match-V5 por partida) si hay RIOT_API_KEY; una vez en la DB, todo es local.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request

from app.container import ServiceContainer
from app.data.schemas import MatchReview, ReviewMatchListItem, WeeklyPatternsResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["review"])


def _container(request: Request) -> ServiceContainer:
    return request.app.state.container


@router.get("/review/matches", response_model=list[ReviewMatchListItem])
def review_matches(request: Request, limit: int = Query(10, ge=1, le=50)) -> list[ReviewMatchListItem]:
    c = _container(request)
    puuid = c.my_puuid()
    if not puuid:
        return []
    return [ReviewMatchListItem(**item) for item in c.review.recent_matches(puuid, limit=limit)]


@router.get("/review/match/{match_id}", response_model=MatchReview)
def review_match(request: Request, match_id: str) -> MatchReview:
    c = _container(request)
    puuid = c.my_puuid()
    if not puuid:
        return MatchReview(
            available=False,
            reason=(
                "No se pudo resolver tu PUUID (revisa GAME_NAME/TAG_LINE en .env "
                "y ejecuta la ingesta de historial)."
            ),
        )
    return MatchReview(**c.review.review(match_id, puuid))


@router.get("/review/patterns", response_model=WeeklyPatternsResponse)
def review_patterns(request: Request, limit: int = Query(20, ge=3, le=50)) -> WeeklyPatternsResponse:
    c = _container(request)
    puuid = c.my_puuid()
    if not puuid:
        return WeeklyPatternsResponse(
            available=False,
            reason="No se pudo resolver tu PUUID; revisa la configuracion e ingesta.",
        )
    return WeeklyPatternsResponse(**c.review.patterns(puuid, limit=limit))

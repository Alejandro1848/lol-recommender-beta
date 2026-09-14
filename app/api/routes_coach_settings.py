"""Configuracion amigable de ingesta y modelos desde el frontend local."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.coaching.model_job_manager import (
    DEFAULTS,
    TIERS,
    estimate_minutes,
    job_manager,
    normalize_options,
)
from app.security import secrets

router = APIRouter(tags=["coach-ai-settings"])


def _container(request: Request):
    return request.app.state.container


def _champions(container) -> list[dict]:
    values = []
    for champion in container.ddragon.champions().values():
        name = champion.get("name") or champion.get("id")
        internal_id = champion.get("id") or name
        # Match-V5 usa Wukong, mientras Data Dragon conserva MonkeyKing.
        canonical = name if internal_id == "MonkeyKing" else internal_id
        if not name:
            continue
        values.append({
            "name": str(name),
            "value": str(canonical),
            "image_url": container.ddragon.champion_image_url(str(canonical)),
        })
    return sorted(values, key=lambda item: item["name"].casefold())


@router.get("/coach-ai/options")
def coach_options(request: Request):
    c = _container(request)
    defaults = {**DEFAULTS, "target_tier": c.settings.target_tier or "GOLD"}
    return {
        "champions": _champions(c),
        "tiers": TIERS,
        "defaults": defaults,
        "default_estimate": estimate_minutes(defaults),
        "riot_api_configured": secrets.has_riot_api_key(),
        "active_job": job_manager.active(),
    }


@router.post("/coach-ai/estimate")
async def coach_estimate(request: Request):
    raw = await request.json()
    options = normalize_options(raw, _container(request).settings.target_tier)
    return estimate_minutes(options)


@router.post("/coach-ai/jobs", status_code=202)
async def start_coach_job(request: Request):
    c = _container(request)
    raw = await request.json()
    # Validacion canonica para que "Di" no inicie un trabajo por accidente.
    champions = _champions(c)
    by_name = {
        item["name"].casefold(): item["value"] for item in champions
    } | {
        item["value"].casefold(): item["value"] for item in champions
    }
    requested = str(raw.get("champion") or "").strip()
    canonical = by_name.get(requested.casefold())
    if canonical is None:
        raise HTTPException(status_code=422, detail="Selecciona un campeon valido de la lista.")
    raw["champion"] = canonical
    try:
        return job_manager.start(c, raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/coach-ai/jobs/{job_id}")
def coach_job(job_id: str):
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
    return job

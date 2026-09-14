"""Tareas locales de ingesta y entrenamiento iniciadas desde la interfaz.

Solo se permite una tarea a la vez: Riot aplica limites de peticiones y dos
ingestas concurrentes producirian datos duplicados y tiempos impredecibles.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from math import ceil
from typing import Any


TIERS = [
    "IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD",
    "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER",
]

DEFAULTS = {
    "champion": "",
    "target_tier": "GOLD",
    "ingest_ladder": False,
    "ingest_timelines": True,
    "train_models": True,
    "force_retrain": True,
    "target_records": 20_000,
    "per_player": 20,
    "days": 30,
    "max_players": 300,
    "timeline_matches": 300,
}


def normalize_options(raw: dict[str, Any], default_tier: str = "GOLD") -> dict[str, Any]:
    options = {**DEFAULTS, **(raw or {})}
    options["champion"] = str(options.get("champion") or "").strip()
    tier = str(options.get("target_tier") or default_tier or "GOLD").upper()
    options["target_tier"] = tier if tier in TIERS else "GOLD"
    for key in ("ingest_ladder", "ingest_timelines", "train_models", "force_retrain"):
        options[key] = bool(options.get(key))
    options["target_records"] = max(10, min(int(options.get("target_records") or 20_000), 200_000))
    options["per_player"] = max(1, min(int(options.get("per_player") or 20), 100))
    options["days"] = max(1, min(int(options.get("days") or 30), 90))
    options["max_players"] = max(1, min(int(options.get("max_players") or 300), 2_000))
    options["timeline_matches"] = max(1, min(int(options.get("timeline_matches") or 300), 3_000))
    return options


def estimate_minutes(options: dict[str, Any]) -> dict[str, Any]:
    """Rango deliberadamente conservador bajo limites normales de Riot API."""
    low = high = 0.0
    parts = []
    if options.get("ingest_ladder"):
        matches = ceil(options["target_records"] / 10)
        step_low = max(3.0, matches / 50.0)
        step_high = max(8.0, matches / 22.0)
        low += step_low
        high += step_high
        parts.append({"step": "Ingesta de liga", "minutes_low": ceil(step_low), "minutes_high": ceil(step_high)})
    if options.get("ingest_timelines"):
        matches = options["timeline_matches"]
        step_low = max(1.0, matches / 55.0)
        step_high = max(3.0, matches / 25.0)
        low += step_low
        high += step_high
        parts.append({"step": "Timelines", "minutes_low": ceil(step_low), "minutes_high": ceil(step_high)})
    if options.get("train_models"):
        step_low, step_high = 1.0, 6.0
        low += step_low
        high += step_high
        parts.append({"step": "Reentrenamiento", "minutes_low": 1, "minutes_high": 6})
    return {
        "minutes_low": ceil(low),
        "minutes_high": ceil(high),
        "label": f"{ceil(low)}-{ceil(high)} min" if high else "Sin procesos seleccionados",
        "parts": parts,
        "note": "Estimado; puede aumentar por limites de Riot, red o partidas ya descargadas.",
    }


class CoachJobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_id: str | None = None

    def active(self) -> dict[str, Any] | None:
        return self.get(self._active_id or "")

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            public = dict(job) if job else None
        if public and public.get("status") == "running" and public.get("started_at"):
            started = datetime.fromisoformat(public["started_at"])
            public["elapsed_seconds"] = round(
                (datetime.now(timezone.utc) - started).total_seconds()
            )
        return public

    def start(self, container, raw_options: dict[str, Any]) -> dict[str, Any]:
        options = normalize_options(raw_options, container.settings.target_tier)
        if not options["champion"]:
            raise ValueError("Selecciona un campeon.")
        if not any(options[key] for key in ("ingest_ladder", "ingest_timelines", "train_models")):
            raise ValueError("Selecciona al menos un proceso.")
        with self._lock:
            active = self._jobs.get(self._active_id or "")
            if active and active["status"] in {"queued", "running"}:
                raise RuntimeError("Ya hay una ingesta o entrenamiento en curso.")
            job_id = uuid.uuid4().hex[:12]
            steps = [
                label for key, label in (
                    ("ingest_ladder", "Ingesta de liga"),
                    ("ingest_timelines", "Descarga de timelines"),
                    ("train_models", "Reentrenamiento de modelos"),
                ) if options[key]
            ]
            job = {
                "id": job_id,
                "status": "queued",
                "champion": options["champion"],
                "tier": options["target_tier"],
                "options": options,
                "estimate": estimate_minutes(options),
                "steps": steps,
                "step_index": 0,
                "current_step": "En cola",
                "message": "La tarea se iniciara en segundo plano.",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "started_at": None,
                "finished_at": None,
                "elapsed_seconds": 0,
            }
            self._jobs[job_id] = job
            self._active_id = job_id
        threading.Thread(target=self._run, args=(container, job_id), daemon=True).start()
        return dict(job)

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(changes)

    def _run(self, container, job_id: str) -> None:
        from main_orchestrator import mode_ingest_ladder, mode_ingest_timelines, mode_train_champion

        job = self.get(job_id) or {}
        options = job["options"]
        started = time.monotonic()
        self._update(
            job_id, status="running", started_at=datetime.now(timezone.utc).isoformat(),
            message="Proceso iniciado.",
        )
        try:
            runners = []
            if options["ingest_ladder"]:
                runners.append(("Ingesta de liga", lambda: mode_ingest_ladder(
                    container,
                    target_records=options["target_records"],
                    per_player=options["per_player"],
                    max_players=options["max_players"],
                    target_tier=options["target_tier"],
                    days=options["days"],
                )))
            if options["ingest_timelines"]:
                runners.append(("Descarga de timelines", lambda: mode_ingest_timelines(
                    container, champion=options["champion"],
                    max_matches=options["timeline_matches"],
                )))
            if options["train_models"]:
                runners.append(("Reentrenamiento de modelos", lambda: mode_train_champion(
                    container, champion=options["champion"],
                    target_tier=options["target_tier"], force=options["force_retrain"],
                )))
            for index, (label, runner) in enumerate(runners, start=1):
                self._update(
                    job_id, step_index=index, current_step=label,
                    message=f"{label} en curso...",
                    elapsed_seconds=round(time.monotonic() - started),
                )
                if runner() != 0:
                    raise RuntimeError(f"{label} no pudo completarse. Revisa la clave de Riot y los logs del servidor.")
            self._update(
                job_id, status="completed", current_step="Completado",
                message="Ingesta y reentrenamiento completados correctamente.",
                finished_at=datetime.now(timezone.utc).isoformat(),
                elapsed_seconds=round(time.monotonic() - started),
            )
        except Exception as exc:
            self._update(
                job_id, status="failed", current_step="Error", message=str(exc),
                finished_at=datetime.now(timezone.utc).isoformat(),
                elapsed_seconds=round(time.monotonic() - started),
            )


job_manager = CoachJobManager()

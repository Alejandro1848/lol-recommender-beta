"""Registry de modelos por campeon, segmentado por liga y versionado por parche.

Estructura en disco:
  models/champions/<Campeon>/<TIER>/
      win_model_<timestamp>_patch<patch>_n<muestras>.joblib/.json
      item_model_<timestamp>_patch<patch>_n<muestras>.joblib/.json
      + reportes (csv/json) y plots (png) del entrenamiento

La regla de vigencia es por parche: si ya existe un modelo del campeon para
la liga y el parche actual, no hace falta reentrenar (caso OTP: el jugador
repite campeon y reutiliza el modelo hasta que Riot publique un parche).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

import joblib

logger = logging.getLogger(__name__)

# "item": recomendacion de builds; "live": probabilidad de victoria in-game.
# (El antiguo kind "win" pregame se elimino: rendia como una moneda.)
MODEL_KINDS = ("item", "live")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", value or "") or "unknown"


def _patch_tag(patch: str | None) -> str:
    return (patch or "unknown").replace(".", "-")


class ChampionModelRegistry:
    def __init__(self, models_dir: Path):
        self.base_dir = Path(models_dir) / "champions"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def champion_dir(self, champion: str, tier: str) -> Path:
        path = self.base_dir / _safe_name(champion) / _safe_name(tier).upper()
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -------------------------------------------------------------- lectura

    def _metas(self, kind: str, champion: str, tier: str) -> list[tuple[Path, dict]]:
        """Pares (ruta .joblib, meta) ordenados del mas viejo al mas nuevo."""
        directory = self.champion_dir(champion, tier)
        entries = []
        for meta_path in sorted(directory.glob(f"{kind}_model_*.json")):
            if meta_path.stem.endswith("_comparison"):
                continue
            model_path = meta_path.with_suffix(".joblib")
            if not model_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Meta ilegible %s: %s", meta_path.name, exc)
                continue
            entries.append((model_path, meta))
        return entries

    def latest(
        self, kind: str, champion: str, tier: str, patch: str | None = None
    ) -> tuple[object, dict] | None:
        """Modelo mas reciente del campeon; con patch, solo el de ese parche."""
        entries = self._metas(kind, champion, tier)
        if patch is not None:
            entries = [e for e in entries if e[1].get("patch") == patch]
        for model_path, meta in reversed(entries):
            try:
                return joblib.load(model_path), meta
            except Exception as exc:
                logger.warning("No se pudo cargar %s: %s", model_path.name, exc)
        return None

    def needs_training(self, kind: str, champion: str, tier: str, patch: str | None) -> bool:
        """True si no hay modelo vigente para (campeon, liga, parche)."""
        entries = self._metas(kind, champion, tier)
        if patch is None:
            return not entries
        return not any(meta.get("patch") == patch for _, meta in entries)

    def status(self, champion: str, tier: str, patch: str | None = None) -> dict:
        out: dict = {"champion": champion, "tier": tier.upper(), "patch": patch}
        for kind in MODEL_KINDS:
            entries = self._metas(kind, champion, tier)
            meta = entries[-1][1] if entries else None
            out[f"{kind}_model"] = None if meta is None else {
                "version": meta.get("version"),
                "algorithm": meta.get("algorithm"),
                "patch": meta.get("patch"),
                "created_at": meta.get("created_at"),
                "n_samples": meta.get("n_samples"),
                "metrics": meta.get("metrics"),
                "up_to_date": patch is not None and meta.get("patch") == patch,
            }
        return out

    # ------------------------------------------------------------- escritura

    def save(self, kind: str, champion: str, tier: str, model, meta: dict) -> dict:
        if kind not in MODEL_KINDS:
            raise ValueError(f"kind debe ser uno de {MODEL_KINDS}, no {kind!r}.")
        directory = self.champion_dir(champion, tier)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"{kind}_model_{timestamp}_patch{_patch_tag(meta.get('patch'))}_n{meta.get('n_samples', 0)}"
        joblib.dump(model, directory / f"{stem}.joblib")
        meta = {
            **meta,
            "version": stem,
            "champion": champion,
            "tier": tier.upper(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        (directory / f"{stem}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("Modelo %s de %s (%s) guardado: %s", kind, champion, tier, stem)
        return meta

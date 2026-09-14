"""Motor de similitud historica con jerarquia de fallback explicita.

Niveles (de mas a menos especifico):
1. mismo campeon + mismo rival + mismo rol + misma cola + patch similar
2. mismo campeon + mismo rival + mismo rol
3. mismo campeon + mismo rol
4. mismo rol + arquetipo de campeon similar (tag principal de Data Dragon)
5. global por rol

Toda recomendacion basada en similitud reporta: nivel usado, tamanio de
muestra y confianza derivada del tamanio.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.riot.data_dragon import DataDragon

LEVEL_LABELS = {
    1: "Mismo campeon, mismo rival, mismo rol, misma cola y patch similar",
    2: "Mismo campeon, mismo rival y mismo rol",
    3: "Mismo campeon y mismo rol",
    4: "Mismo rol y arquetipo de campeon similar",
    5: "Global por rol",
}

MIN_SAMPLE = 3


def confidence_from_sample(n: int) -> str:
    if n >= 20:
        return "alta"
    if n >= 8:
        return "media"
    return "baja"


@dataclass
class SimilarityResult:
    sample: pd.DataFrame
    level: int | None
    label: str
    confidence: str

    @property
    def size(self) -> int:
        return 0 if self.sample is None else len(self.sample)

    def summary(self) -> dict:
        return {
            "sample_size": self.size,
            "level": self.level,
            "level_label": self.label,
            "confidence": self.confidence,
            "match_ids": (
                self.sample["matchId"].unique().tolist()[:10] if self.size else []
            ),
        }


class SimilarityEngine:
    def __init__(self, ddragon: DataDragon):
        self.ddragon = ddragon

    def find_similar(
        self,
        participants_with_opp: pd.DataFrame,
        own_champion: str,
        opponent_champion: str | None,
        role: str | None,
        queue_id: int | None = None,
        patch: str | None = None,
        min_sample: int = MIN_SAMPLE,
    ) -> SimilarityResult:
        df = participants_with_opp
        if df is None or df.empty:
            return SimilarityResult(pd.DataFrame(), None, "Sin historial local", "baja")

        own = df["championName"].fillna("").str.lower() == (own_champion or "").lower()
        opp = df["opponentChampionName"].fillna("").str.lower() == (opponent_champion or "").lower()
        role_mask = df["teamPosition"] == role if role else pd.Series(True, index=df.index)
        queue_mask = df["queueId"] == queue_id if queue_id else pd.Series(True, index=df.index)
        patch_mask = pd.Series(True, index=df.index)
        if patch and "patch" in df.columns:
            major = str(patch).split(".")[0]
            patch_mask = df["patch"].fillna("").str.startswith(major)

        archetype = self.ddragon.primary_tag(own_champion) if own_champion else None
        archetype_mask = pd.Series(False, index=df.index)
        if archetype:
            archetype_mask = df["championName"].fillna("").map(
                lambda name: self.ddragon.primary_tag(name) == archetype
            )

        level_masks = [
            (1, own & opp & role_mask & queue_mask & patch_mask),
            (2, own & opp & role_mask),
            (3, own & role_mask),
            (4, role_mask & archetype_mask),
            (5, role_mask),
        ]

        last_non_empty: SimilarityResult | None = None
        for level, mask in level_masks:
            if opponent_champion is None and level in (1, 2):
                continue
            sample = df[mask]
            if len(sample) >= min_sample:
                return SimilarityResult(
                    sample, level, LEVEL_LABELS[level], confidence_from_sample(len(sample))
                )
            if len(sample) > 0 and last_non_empty is None:
                last_non_empty = SimilarityResult(
                    sample, level, LEVEL_LABELS[level], confidence_from_sample(len(sample))
                )

        if last_non_empty is not None:
            return last_non_empty
        return SimilarityResult(pd.DataFrame(), None, "Sin partidas similares", "baja")

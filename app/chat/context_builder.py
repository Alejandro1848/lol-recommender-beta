"""Construye el contexto de datos que el chatbot puede citar.

El chatbot SOLO responde con lo que hay aqui: snapshot en vivo,
historial local, perfiles y Data Dragon. Si un dato no esta, la
respuesta correcta es decir que no esta.
"""
from __future__ import annotations

from typing import Any

from app.analytics.matchup_analysis import with_opponents
from app.data.repositories import MatchRepository
from app.riot.data_dragon import DataDragon


class ChatContextBuilder:
    def __init__(self, repo: MatchRepository, ddragon: DataDragon):
        self.repo = repo
        self.ddragon = ddragon

    def build(self, snapshot: dict | None, recommendations: dict | None) -> dict[str, Any]:
        participants = self.repo.participants_df()
        history = with_opponents(participants) if not participants.empty else participants
        return {
            "snapshot": snapshot,
            "recommendations": recommendations,
            "history": history,
            "history_matches": int(participants["matchId"].nunique()) if not participants.empty else 0,
            "ddragon": self.ddragon,
            "known_champions": (
                sorted(participants["championName"].dropna().unique().tolist())
                if not participants.empty else []
            ),
        }

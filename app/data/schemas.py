"""Schemas Pydantic de la API. Contrato entre backend y frontend.

Convencion clave del proyecto: cada payload marca su procedencia con
`data_source` ('historico' | 'partida_activa' | 'live_client') para que
la UI nunca mezcle silenciosamente datos de distinta naturaleza.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DataSource = Literal["historico", "partida_activa", "live_client", "mixto", "llm"]
Confidence = Literal["alta", "media", "baja"]


class RankedEntry(BaseModel):
    queue_type: str
    tier: str | None = None
    rank: str | None = None
    league_points: int = 0
    wins: int = 0
    losses: int = 0


class ChampionSummary(BaseModel):
    name: str
    games: int
    wins: int
    winrate: float
    kda: float | None = None
    image_url: str | None = None


class PlayerProfile(BaseModel):
    riot_id: str
    puuid: str | None = None
    summoner_level: int | None = None
    profile_icon_url: str | None = None
    ranked_entries: list[RankedEntry] = []
    top_champions: list[ChampionSummary] = []
    main_role: str | None = None
    recent_winrate: float | None = None
    recent_games: int = 0
    style: dict[str, Any] | None = None
    data_source: DataSource = "historico"
    warnings: list[str] = []


class PlayerAnalytics(BaseModel):
    """Analiticas de CUALQUIER jugador (estilo porofessor): no requiere que
    el jugador este jugando en esta maquina, solo su Riot ID."""

    available: bool
    reason: str | None = None
    riot_id: str | None = None
    puuid: str | None = None
    summoner_level: int | None = None
    profile_icon_url: str | None = None
    ranked_entries: list[RankedEntry] = []
    games: int = 0
    top_champions: list[ChampionSummary] = []
    main_role: str | None = None
    recent_form: dict[str, Any] | None = None
    style: dict[str, Any] | None = None
    data_source: DataSource = "mixto"
    warnings: list[str] = []


class MatchHistoryItem(BaseModel):
    match_id: str
    champion: str
    champion_image_url: str | None = None
    role: str | None = None
    win: bool
    kills: int
    deaths: int
    assists: int
    kda: float
    cs_per_min: float | None = None
    gold_per_min: float | None = None
    damage_per_min: float | None = None
    vision_per_min: float | None = None
    kill_participation: float | None = None
    duration_min: float | None = None
    game_creation: int | None = None
    patch: str | None = None
    opponent_champion: str | None = None


class LiveStatus(BaseModel):
    live_client_available: bool
    spectator_active_game: bool
    in_game: bool
    game_time_seconds: float | None = None
    game_mode: str | None = None
    refresh_seconds: int
    message: str
    data_source: DataSource = "live_client"
    live_client_diagnostics: dict[str, Any] | None = None


class LivePlayer(BaseModel):
    riot_id: str
    champion: str
    champion_image_url: str | None = None
    team: str
    position: str | None = None
    level: int | None = None
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    creep_score: int | None = None
    is_dead: bool = False
    respawn_timer: float | None = None
    items: list[dict[str, Any]] = []
    runes: dict[str, Any] | None = None
    damage_profile: dict[str, Any] | None = None


class LiveGame(BaseModel):
    in_game: bool
    game_time_seconds: float | None = None
    me: LivePlayer | None = None
    direct_rival: LivePlayer | None = None
    allies: list[LivePlayer] = []
    enemies: list[LivePlayer] = []
    enemy_damage_mix: dict[str, float] | None = None
    events_summary: dict[str, Any] | None = None
    data_source: DataSource = "live_client"
    live_signals_available: bool = False
    active_player: dict[str, Any] | None = None
    live_client_diagnostics: dict[str, Any] | None = None
    message: str | None = None


class Recommendation(BaseModel):
    kind: Literal["item", "gank", "objetivo", "general"]
    title: str
    detail: str
    explanation: str
    confidence: Confidence
    sample_size: int | None = None
    similarity_level: int | None = None
    similarity_label: str | None = None
    image_url: str | None = None
    data_source: DataSource = "mixto"
    extra: dict[str, Any] = Field(default_factory=dict)


class WinProbability(BaseModel):
    probability: float | None = None
    confidence: Confidence = "baja"
    method: str
    top_factors: list[dict[str, Any]] = []
    warnings: list[str] = []
    data_source: DataSource = "mixto"


class RecommendationsResponse(BaseModel):
    in_game: bool
    win_probability: WinProbability | None = None
    items: list[Recommendation] = []
    ganks: list[Recommendation] = []
    objectives: list[Recommendation] = []
    similarity: dict[str, Any] | None = None
    warnings: list[str] = []
    message: str | None = None


class CurvePoint(BaseModel):
    minute: int
    probability: float


class TurningPoint(BaseModel):
    minute: int
    end_minute: int
    from_probability: float
    to_probability: float
    swing: float
    direction: Literal["subida", "caida"]
    causes: list[str] = []
    description: str


class ReviewMatchListItem(BaseModel):
    match_id: str
    champion: str
    champion_image_url: str | None = None
    opponent_champion: str | None = None
    role: str | None = None
    win: bool
    duration_min: float | None = None
    game_creation: int | None = None
    has_timeline: bool = False
    reviewed: bool = False


class MatchReview(BaseModel):
    """Revision post-partida: curva de probabilidad reconstruida desde la
    timeline + puntos de inflexion + resumen de 5 lineas."""

    available: bool
    reason: str | None = None
    match_id: str | None = None
    champion: str | None = None
    champion_image_url: str | None = None
    opponent_champion: str | None = None
    role: str | None = None
    win: bool | None = None
    duration_min: float | None = None
    kda: str | None = None
    game_creation: int | None = None
    method: str | None = None
    curve: list[CurvePoint] = []
    turning_points: list[TurningPoint] = []
    summary_lines: list[str] = []
    stats: dict[str, Any] = {}
    warnings: list[str] = []
    data_source: DataSource = "historico"


class WeeklyPatternsResponse(BaseModel):
    """Patrones agregados sobre las ultimas N partidas revisadas."""

    available: bool
    reason: str | None = None
    games: int = 0
    wins: int = 0
    winrate: float | None = None
    throws: int = 0
    comebacks: int = 0
    buckets: list[dict[str, Any]] = []
    ahead_at_15: dict[str, Any] | None = None
    behind_at_15: dict[str, Any] | None = None
    insights: list[str] = []
    requested: int = 0
    skipped_without_timeline: int = 0
    warnings: list[str] = []
    data_source: DataSource = "historico"


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class ChatResponse(BaseModel):
    answer: str
    intent: str
    data_available: bool
    sources: list[str] = []
    data_source: DataSource = "mixto"

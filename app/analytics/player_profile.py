"""Perfil analitico de un jugador (propio o rival) desde el historial local
y, para rivales, con ingesta bajo demanda de sus partidas recientes."""
from __future__ import annotations

import logging

import pandas as pd

from app.analytics.champion_stats import top_champions
from app.data.cache import FileCache
from app.data.normalizers import ROLE_LABELS_ES, flatten_matches
from app.data.repositories import MatchRepository
from app.ml.player_style_model import classify_style
from app.riot.data_dragon import DataDragon
from app.riot.riot_client import RiotApiError, RiotClient

logger = logging.getLogger(__name__)

RIVAL_CACHE_TTL = 600  # 10 min
RIVAL_MATCH_SAMPLE = 10


def recent_form(player_df: pd.DataFrame, n: int = 10) -> dict:
    """Rendimiento en las ultimas n partidas del jugador."""
    if player_df.empty:
        return {"games": 0}
    recent = player_df.sort_values("gameCreation", ascending=False).head(n)
    return {
        "games": int(len(recent)),
        "wins": int(recent["win"].sum()),
        "winrate": round(float(recent["win"].mean()), 3),
        "kda": round(float(recent["kda"].mean()), 2),
        "cs_per_min": round(float(recent["csPerMin"].mean()), 2),
        "gold_per_min": round(float(recent["goldPerMin"].mean()), 1),
        "damage_per_min": round(float(recent["damagePerMin"].mean()), 1),
        "vision_per_min": round(float(recent["visionPerMin"].mean()), 2),
        "kill_participation": round(float(recent["killParticipation"].mean()), 3),
    }


def main_role(player_df: pd.DataFrame) -> str | None:
    if player_df.empty or player_df["teamPosition"].dropna().empty:
        return None
    role = player_df["teamPosition"].dropna().mode()
    return role.iloc[0] if not role.empty else None


def build_local_profile(player_df: pd.DataFrame, ddragon: DataDragon) -> dict:
    """Perfil basado solo en la muestra local (sin llamadas a la API)."""
    if player_df.empty:
        return {"games": 0, "warnings": ["Sin partidas historicas en la base local."]}
    tops = top_champions(player_df, n=3)
    role = main_role(player_df)
    return {
        "games": int(player_df["matchId"].nunique()),
        "top_champions": [
            {
                "name": row["championName"],
                "games": int(row["games"]),
                "wins": int(row["wins"]),
                "winrate": float(row["winrate"]),
                "kda": round(float(row["kda"]), 2),
                "image_url": ddragon.champion_image_url(row["championName"]),
            }
            for _, row in tops.iterrows()
        ],
        "main_role": role,
        "main_role_label": ROLE_LABELS_ES.get(role) if role else None,
        "recent_form": recent_form(player_df),
        "style": classify_style(player_df),
        "warnings": [],
    }


class OpponentScout:
    """Obtiene el historial reciente de cualquier jugador bajo demanda.

    Funciona igual que porofessor.gg: no necesita que el jugador este en
    esta maquina; con su Riot ID descarga sus partidas recientes via la
    Riot API. Las partidas descargadas se guardan en la DB (son ranked
    reales y enriquecen el dataset). El resultado se cachea 10 minutos
    para respetar rate limits.
    """

    def __init__(self, client: RiotClient, repo: MatchRepository, cache: FileCache, ddragon: DataDragon):
        self.client = client
        self.repo = repo
        self.cache = cache
        self.ddragon = ddragon

    def resolve_platform(self, puuid: str) -> str | None:
        """Plataforma real del jugador (la1, eun1...); None si no se pudo."""
        try:
            return self.client.get_active_platform(puuid)
        except RiotApiError as exc:
            logger.warning("No se pudo resolver la region del jugador: %s", exc)
            return None

    def ingest_recent_matches(
        self, puuid: str, count: int, platform: str | None = None
    ) -> list[str]:
        """Descarga las partidas recientes del jugador a la DB local.

        Cadena de fallback honesta: primero la cola ranked configurada,
        luego cualquier ranked (flex incluida) y por ultimo cualquier cola.
        Devuelve advertencias sobre que muestra se uso.
        """
        warnings: list[str] = []
        match_ids = self.client.get_recent_ranked_match_ids(
            puuid, count=count, platform=platform
        )
        if not match_ids:
            match_ids = self.client.get_recent_match_ids(
                puuid, count=count, match_type="ranked", platform=platform
            )
            if match_ids:
                warnings.append(
                    "Sin soloq reciente: la muestra incluye otras colas ranked (flex)."
                )
        if not match_ids:
            match_ids = self.client.get_recent_match_ids(
                puuid, count=count, platform=platform
            )
            if match_ids:
                warnings.append(
                    "Sin partidas ranked recientes: la muestra incluye partidas "
                    "normales/ARAM, tomala como orientativa."
                )
        known = self.repo.known_match_ids()
        new_raw = [
            match for mid in match_ids if mid not in known
            if (match := self.client.get_match(mid, platform=platform)) is not None
        ]
        if new_raw:
            self.repo.upsert_bundle(flatten_matches(new_raw))
        return warnings

    def profile(
        self,
        game_name: str,
        tag_line: str,
        match_sample: int = RIVAL_MATCH_SAMPLE,
        with_league: bool = False,
    ) -> dict:
        cache_key = f"rival_{game_name}_{tag_line}_{match_sample}_{int(with_league)}".lower()
        cached = self.cache.get(cache_key, ttl_seconds=RIVAL_CACHE_TTL)
        if cached:
            return cached

        try:
            account = self.client.get_account_by_riot_id(game_name, tag_line)
        except RiotApiError as exc:
            return {"available": False, "reason": str(exc)}
        if account is None:
            return {"available": False, "reason": "Riot ID no encontrado."}

        puuid = account["puuid"]
        # La cuenta resuelve globalmente, pero sus partidas viven en SU
        # region: consultarla permite leer jugadores de LAN, KR, etc.
        platform = self.resolve_platform(puuid)
        extra_warnings: list[str] = []
        if platform and platform != self.client.current_platform:
            extra_warnings.append(
                f"Jugador de otra region ({platform}): datos consultados en su region."
            )
        try:
            extra_warnings.extend(
                self.ingest_recent_matches(puuid, match_sample, platform=platform)
            )
        except RiotApiError as exc:
            logger.warning("Scout del jugador fallo parcialmente: %s", exc)
            extra_warnings.append(f"Descarga parcial del historial: {exc}")

        player_df = self.repo.player_participants_df(puuid)
        result = {
            "available": not player_df.empty,
            "riot_id": f"{account.get('gameName')}#{account.get('tagLine')}",
            "puuid": puuid,
            "platform": platform,
            **build_local_profile(player_df, self.ddragon),
        }
        result.setdefault("warnings", []).extend(extra_warnings)
        if with_league:
            league = self._league_info(puuid, platform=platform)
            league_warnings = league.pop("warnings", [])
            result.update(league)
            result.setdefault("warnings", []).extend(league_warnings)
        self.cache.set(cache_key, result)
        return result

    def _league_info(self, puuid: str, platform: str | None = None) -> dict:
        """Nivel, icono y colas ranked del jugador (Summoner-V4 + League-V4)."""
        info: dict = {}
        try:
            summoner = self.client.get_summoner_by_puuid(puuid, platform=platform) or {}
            info["summoner_level"] = summoner.get("summonerLevel")
            if summoner.get("profileIconId") is not None:
                info["profile_icon_url"] = self.ddragon.profile_icon_url(
                    summoner["profileIconId"]
                )
            info["ranked_entries"] = [
                {
                    "queue_type": e.get("queueType", "?"),
                    "tier": e.get("tier"),
                    "rank": e.get("rank"),
                    "league_points": e.get("leaguePoints", 0),
                    "wins": e.get("wins", 0),
                    "losses": e.get("losses", 0),
                }
                for e in self.client.get_league_entries_by_puuid(puuid, platform=platform)
            ]
        except RiotApiError as exc:
            logger.warning("League info no disponible: %s", exc)
            info.setdefault("warnings", []).append(f"Perfil ranked no disponible: {exc}")
        return info

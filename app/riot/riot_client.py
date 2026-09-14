"""Cliente de la Riot API (Account-V1, Summoner-V4, League-V4, Match-V5, Spectator-V5).

Refactorizacion profesional de Learning/extract_lan_ranked_matches.py:
- API key siempre desde secrets (nunca hardcodeada, nunca en atributos).
- Rate limiting local + respeto de Retry-After en 429.
- Reintentos con backoff en errores 5xx.
- Errores traducidos a mensajes accionables.
"""
from __future__ import annotations

import logging
import time
from urllib.parse import quote

import requests

from app.config import Settings
from app.riot.rate_limit import SlidingWindowRateLimiter
from app.security.secrets import get_riot_api_key

logger = logging.getLogger(__name__)


class RiotApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# Plataforma -> cluster regional de Match-V5. Account-V1 es global, pero las
# partidas de cada jugador viven en el cluster de SU region: un jugador de
# LAN (la1) no aparece si se consulta europe aunque su cuenta si resuelva.
PLATFORM_TO_REGIONAL = {
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe", "me1": "europe",
    "kr": "asia", "jp1": "asia",
    "oc1": "sea", "ph2": "sea", "sg2": "sea", "th2": "sea", "tw2": "sea", "vn2": "sea",
}


class RiotClient:
    def __init__(
        self,
        settings: Settings,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings
        self.rate_limiter = rate_limiter or SlidingWindowRateLimiter()
        self.session = session or requests.Session()
        # Routing por defecto desde .env; puede reajustarse solo al resolver
        # la cuenta (autoconfigure_routing_for), asi el .env no necesita
        # cambiar cuando se usa una cuenta de otra region.
        self.current_platform = settings.platform_routing
        self._regional = f"https://{settings.regional_routing}.api.riotgames.com"
        self._platform = f"https://{settings.platform_routing}.api.riotgames.com"

    # ------------------------------------------------------------------ core

    def _get(self, base_url: str, path: str, params: dict | None = None, max_retries: int = 4):
        url = f"{base_url}{path}"
        headers = {"X-Riot-Token": get_riot_api_key()}

        for attempt in range(max_retries):
            self.rate_limiter.acquire()
            try:
                response = self.session.get(url, headers=headers, params=params, timeout=30)
            except requests.RequestException as exc:
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RiotApiError(
                    f"Error de red hacia Riot ({type(exc).__name__}): {exc}. "
                    "Revisa tu conexion o certificados TLS."
                ) from exc

            if response.status_code == 200:
                return response.json()

            if response.status_code == 404:
                return None

            if response.status_code == 429:
                wait_seconds = int(response.headers.get("Retry-After", "2"))
                logger.warning("Rate limit de Riot (429). Esperando %ss...", wait_seconds)
                time.sleep(wait_seconds)
                continue

            if response.status_code in {500, 502, 503, 504} and attempt < max_retries - 1:
                backoff = 2 * (attempt + 1)
                logger.warning("Error %s de Riot. Reintentando en %ss...", response.status_code, backoff)
                time.sleep(backoff)
                continue

            if response.status_code == 401:
                raise RiotApiError(
                    "401 de Riot: API key invalida o expirada. Genera una nueva en "
                    "developer.riotgames.com y actualiza tu .env.",
                    401,
                )
            if response.status_code == 403:
                raise RiotApiError(
                    "403 de Riot: la key no tiene permisos para este endpoint o expiro. "
                    "Las development keys expiran cada 24h.",
                    403,
                )
            raise RiotApiError(
                f"Error {response.status_code} de Riot en {path}: {response.text[:300]}",
                response.status_code,
            )

        raise RiotApiError(f"Se agotaron los reintentos para {path}.")

    # ------------------------------------------------------------- account-v1

    def get_account_by_riot_id(self, game_name: str, tag_line: str) -> dict | None:
        path = (
            f"/riot/account/v1/accounts/by-riot-id/"
            f"{quote(game_name)}/{quote(tag_line)}"
        )
        return self._get(self._regional, path)

    def get_active_platform(self, puuid: str) -> str | None:
        """Plataforma (la1, eun1, kr...) donde el jugador juega LoL activamente.

        Permite consultar jugadores de OTRAS regiones: la cuenta resuelve
        globalmente, pero Match/Summoner/League viven en su region.
        """
        data = self._get(
            self._regional, f"/riot/account/v1/region/by-game/lol/by-puuid/{puuid}"
        )
        region = ((data or {}).get("region") or "").lower()
        return region or None

    def autoconfigure_routing_for(self, puuid: str) -> str | None:
        """Detecta la region real del jugador y ajusta el routing por defecto.

        Hace que PLATFORM_ROUTING/REGIONAL_ROUTING del .env sean solo un
        punto de partida: si la cuenta configurada vive en otra region,
        todos los endpoints (match, summoner, league, spectator) pasan a
        consultarse donde corresponde, sin editar la configuracion.
        """
        try:
            platform = self.get_active_platform(puuid)
        except RiotApiError as exc:
            logger.warning("No se pudo autodetectar la region: %s", exc)
            return None
        if not platform or platform not in PLATFORM_TO_REGIONAL:
            return None
        if platform != self.current_platform:
            logger.info(
                "Routing autodetectado: %s/%s (el .env indicaba %s/%s).",
                platform, PLATFORM_TO_REGIONAL[platform],
                self.settings.platform_routing, self.settings.regional_routing,
            )
        self.current_platform = platform
        self._platform = f"https://{platform}.api.riotgames.com"
        self._regional = f"https://{PLATFORM_TO_REGIONAL[platform]}.api.riotgames.com"
        return platform

    def routing_for_platform(self, platform: str | None) -> tuple[str, str]:
        """(base_platform, base_regional) para una plataforma dada; si es None
        o desconocida, se usan las de la configuracion."""
        if platform and platform in PLATFORM_TO_REGIONAL:
            return (
                f"https://{platform}.api.riotgames.com",
                f"https://{PLATFORM_TO_REGIONAL[platform]}.api.riotgames.com",
            )
        return self._platform, self._regional

    # ------------------------------------------------------------ summoner-v4

    def get_summoner_by_puuid(self, puuid: str, platform: str | None = None) -> dict | None:
        base, _ = self.routing_for_platform(platform)
        return self._get(base, f"/lol/summoner/v4/summoners/by-puuid/{puuid}")

    def get_summoner_by_id(self, summoner_id: str, platform: str | None = None) -> dict | None:
        base, _ = self.routing_for_platform(platform)
        return self._get(base, f"/lol/summoner/v4/summoners/{summoner_id}")

    # -------------------------------------------------------------- league-v4

    def get_league_entries_by_puuid(self, puuid: str, platform: str | None = None) -> list[dict]:
        base, _ = self.routing_for_platform(platform)
        data = self._get(base, f"/lol/league/v4/entries/by-puuid/{puuid}")
        return data or []

    def get_league_entries(
        self,
        tier: str,
        division: str,
        queue: str = "RANKED_SOLO_5x5",
        page: int = 1,
        platform: str | None = None,
    ) -> list[dict]:
        base, _ = self.routing_for_platform(platform)
        path = f"/lol/league/v4/entries/{queue}/{tier.upper()}/{division.upper()}"
        data = self._get(base, path, {"page": page})
        return data or []

    def get_apex_league(
        self,
        tier: str,
        queue: str = "RANKED_SOLO_5x5",
        platform: str | None = None,
    ) -> dict | None:
        """Challenger/Grandmaster/Master league payload."""
        normalized = tier.strip().lower()
        if normalized not in {"challenger", "grandmaster", "master"}:
            raise ValueError("tier apex debe ser challenger, grandmaster o master")
        base, _ = self.routing_for_platform(platform)
        return self._get(base, f"/lol/league/v4/{normalized}leagues/by-queue/{queue}")

    # --------------------------------------------------------------- match-v5

    def get_recent_match_ids(
        self,
        puuid: str,
        count: int | None = None,
        queue_id: int | None = None,
        match_type: str | None = None,
        start: int = 0,
        start_time: int | None = None,
        end_time: int | None = None,
        platform: str | None = None,
    ) -> list[str]:
        """Ids de partidas recientes; sin filtros trae todas las colas."""
        params: dict = {
            "start": start,
            "count": min(count or self.settings.match_count, 100),
        }
        if queue_id:
            params["queue"] = queue_id
        if match_type:
            params["type"] = match_type
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        _, regional = self.routing_for_platform(platform)
        data = self._get(regional, f"/lol/match/v5/matches/by-puuid/{puuid}/ids", params)
        return data or []

    def get_recent_ranked_match_ids(
        self, puuid: str, count: int | None = None, queue_id: int | None = None,
        start: int = 0, start_time: int | None = None, end_time: int | None = None,
        platform: str | None = None,
    ) -> list[str]:
        return self.get_recent_match_ids(
            puuid,
            count=count,
            queue_id=queue_id or self.settings.queue_id,
            match_type="ranked",
            start=start,
            start_time=start_time,
            end_time=end_time,
            platform=platform,
        )

    def get_match(self, match_id: str, platform: str | None = None) -> dict | None:
        _, regional = self.routing_for_platform(platform)
        return self._get(regional, f"/lol/match/v5/matches/{match_id}")

    def get_match_timeline(self, match_id: str, platform: str | None = None) -> dict | None:
        _, regional = self.routing_for_platform(platform)
        return self._get(regional, f"/lol/match/v5/matches/{match_id}/timeline")

    # ----------------------------------------------------------- spectator-v5

    def get_active_game(self, puuid: str) -> dict | None:
        """Partida activa via Spectator-V5. None si no hay partida (404)."""
        return self._get(
            self._platform, f"/lol/spectator/v5/active-games/by-summoner/{puuid}"
        )

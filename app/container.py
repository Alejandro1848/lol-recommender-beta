"""Composition root: construye y cablea todos los servicios una sola vez.

No esta en la estructura original pedida, pero se justifica: el orquestador
y el servidor FastAPI necesitan exactamente el mismo grafo de dependencias;
centralizarlo aqui evita duplicar el cableado y facilita los tests (se
puede inyectar cualquier pieza falsa).
"""
from __future__ import annotations

import logging
import threading
import time

from app.analytics.player_profile import OpponentScout
from app.analytics.postgame_review import PostGameReviewService
from app.chat.chat_service import ChatService
from app.chat.llm_client import LLMClient
from app.coaching.coach_engine import CoachEngine
from app.chat.context_builder import ChatContextBuilder
from app.config import Settings
from app.data.cache import FileCache
from app.data.database import Database
from app.data.repositories import MatchRepository
from app.ml.champion_registry import ChampionModelRegistry
from app.ml.inference import InferenceService
from app.pipelines.ingest_live import build_live_snapshot
from app.pipelines.simulate_live import snapshot_from_spectator
from app.recommendations.recommendation_engine import RecommendationEngine
from app.recommendations.similarity_engine import SimilarityEngine
from app.riot.data_dragon import DataDragon
from app.riot.live_client import LiveClient
from app.riot.riot_client import RiotApiError, RiotClient
from app.security import secrets

logger = logging.getLogger(__name__)


class ServiceContainer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.database_path)
        self.repo = MatchRepository(self.db)
        self.cache = FileCache(settings.storage_dir / "cache")
        self.ddragon = DataDragon(settings.ddragon_cache_dir, settings.language)
        self.riot_client = RiotClient(settings)
        self.live_client = LiveClient(settings.live_client_base_url)
        self.champion_registry = ChampionModelRegistry(settings.models_dir)
        self.similarity = SimilarityEngine(self.ddragon)
        self.inference = InferenceService(
            champion_registry=self.champion_registry,
            tier=settings.target_tier,
        )
        self.engine = RecommendationEngine(self.repo, self.similarity, self.inference, self.ddragon)
        self.coach = CoachEngine()
        self.scout = OpponentScout(self.riot_client, self.repo, self.cache, self.ddragon)
        self.review = PostGameReviewService(
            self.repo, settings, self.champion_registry,
            riot_client=self.riot_client, ddragon=self.ddragon,
        )
        self.chat_context = ChatContextBuilder(self.repo, self.ddragon)
        self.llm = LLMClient(settings.llm_provider, settings.llm_model)
        self.chat = ChatService(
            self.repo,
            self.ddragon,
            self.scout if secrets.has_riot_api_key() else None,
            live_state_fn=self.live_state,
            my_puuid_fn=self.my_puuid,
            review_service=self.review,
            llm=self.llm,
        )

        self._account: dict | None = None
        self._account_error: str | None = None
        self._live_lock = threading.Lock()
        # Single-flight: una sola reconstruccion de estado a la vez. La
        # primera tras el arranque tarda (lee todo el historial); sin esto,
        # cada request bloqueado lanzaba SU PROPIA reconstruccion.
        self._refresh_lock = threading.Lock()
        self._live_cache: dict = {
            "ts": 0.0,
            "snapshot": None,
            "recommendations": None,
            "live_client_diagnostics": None,
        }

        # Punto de inyeccion para pruebas: si se asigna un callable que
        # devuelva un snapshot (p. ej. simulate_live), reemplaza al Live
        # Client real. Lo usa main_orchestrator_test.py.
        self.snapshot_provider = None

    # ------------------------------------------------------------- cuenta

    def resolve_account(self, force: bool = False) -> dict | None:
        """Resuelve Riot ID -> PUUID una vez y lo cachea en memoria."""
        if self._account is not None and not force:
            return self._account
        if not secrets.has_riot_api_key():
            self._account_error = "RIOT_API_KEY no configurada."
            return None
        if not self.settings.game_name or not self.settings.tag_line:
            self._account_error = "GAME_NAME/TAG_LINE no configurados en .env."
            return None
        try:
            self._account = self.riot_client.get_account_by_riot_id(
                self.settings.game_name, self.settings.tag_line
            )
            if self._account is None:
                self._account_error = "Riot ID no encontrado en la region configurada."
            else:
                # La cuenta puede vivir en otra region que la del .env:
                # autodetectarla ajusta match/summoner/league/spectator.
                self.riot_client.autoconfigure_routing_for(self._account["puuid"])
        except RiotApiError as exc:
            self._account_error = str(exc)
            logger.warning("No se pudo resolver la cuenta: %s", exc)
        return self._account

    @property
    def account_error(self) -> str | None:
        return self._account_error

    def my_puuid(self) -> str | None:
        account = self.resolve_account()
        if account:
            return account.get("puuid")
        # Fallback sin API key: buscar el Riot ID en el historial local
        return self.puuid_from_local_history(self.settings.game_name, self.settings.tag_line)

    def puuid_from_local_history(self, game_name: str, tag_line: str | None) -> str | None:
        """Busca el PUUID de un Riot ID entre los participantes ya guardados."""
        participants = self.repo.participants_df(enriched=False)
        if participants.empty or not game_name:
            return None
        mask = (
            participants["riotIdGameName"].fillna("").str.lower()
            == game_name.lower()
        )
        if tag_line:
            mask &= (
                participants["riotIdTagline"].fillna("").str.lower()
                == tag_line.lower()
            )
        subset = participants[mask]
        return subset["puuid"].iloc[0] if not subset.empty else None

    def my_riot_id(self) -> str:
        return f"{self.settings.game_name}#{self.settings.tag_line}"

    # ------------------------------------------------------- estado en vivo

    def refresh_live_state(self) -> tuple[dict | None, dict | None]:
        """Reconstruye snapshot + recomendaciones (inferencia periodica).

        Single-flight: si ya hay una reconstruccion en curso, se espera su
        resultado en lugar de duplicar el trabajo.
        """
        if not self._refresh_lock.acquire(blocking=False):
            with self._refresh_lock:
                pass  # esperar a que termine la reconstruccion en curso
            with self._live_lock:
                cache = dict(self._live_cache)
            return cache["snapshot"], cache["recommendations"]
        try:
            return self._do_refresh_live_state()
        finally:
            self._refresh_lock.release()

    def _do_refresh_live_state(self) -> tuple[dict | None, dict | None]:
        if self.snapshot_provider is not None:
            snapshot = self.snapshot_provider()
            diagnostics = None
        else:
            snapshot = build_live_snapshot(self.live_client, self.ddragon, self.my_riot_id())
            diagnostics = (
                {"available": True, "message": "Live Client Data API local disponible."}
                if snapshot is not None
                else self.live_client.diagnostics()
            )
            if snapshot is None:
                # Fallback: si el juego no corre en ESTA maquina (o aun
                # carga), Spectator-V5 permite recomendaciones basadas en
                # la composicion real desde cualquier maquina.
                snapshot = self._spectator_snapshot()
                if snapshot is not None:
                    snapshot["live_client_diagnostics"] = diagnostics
        recommendations = self.engine.build(snapshot, queue_id=self.settings.queue_id)
        with self._live_lock:
            self._live_cache = {
                "ts": time.time(),
                "snapshot": snapshot,
                "recommendations": recommendations,
                "live_client_diagnostics": diagnostics,
            }
        return snapshot, recommendations

    def cached_live_state(self) -> tuple[dict | None, dict | None, float | None]:
        """(snapshot, recomendaciones, edad_del_cache) SIN reconstruir.

        Para el overlay: la reconstruccion sincrona puede tardar mas que el
        timeout del cliente y la ventana mostraba "servidor no disponible".
        El refresco periodico del servidor mantiene este cache al dia; el
        coach compensa la edad extrapolando el reloj de juego.
        """
        with self._live_lock:
            cache = dict(self._live_cache)
        if cache["ts"] > 0:
            age = max(0.0, time.time() - cache["ts"])
            return cache["snapshot"], cache["recommendations"], age
        # Arranque frio: NO bloquear al cliente (la primera reconstruccion
        # puede tardar ~1 min). Se dispara en background (el single-flight
        # evita duplicados) y age=None senala "calentando".
        threading.Thread(target=self.refresh_live_state, daemon=True).start()
        return None, None, None

    def live_cache_age_seconds(self) -> float | None:
        """Antiguedad del snapshot cacheado; None si nunca se ha llenado.

        El coach la usa para extrapolar el reloj de juego: un snapshot de
        hace 12s ya tiene los timers de objetivos corridos 12s.
        """
        with self._live_lock:
            ts = self._live_cache.get("ts") or 0.0
        if not ts:
            return None
        return max(0.0, time.time() - ts)

    def live_client_diagnostics(self) -> dict | None:
        with self._live_lock:
            cached = self._live_cache.get("live_client_diagnostics")
        return cached or self.live_client.diagnostics()

    def _spectator_snapshot(self) -> dict | None:
        """Snapshot desde Spectator-V5 si Riot reporta partida activa."""
        if not secrets.has_riot_api_key():
            return None
        account = self.resolve_account()
        if not account:
            return None
        try:
            active = self.riot_client.get_active_game(account["puuid"])
        except RiotApiError as exc:
            logger.debug("Spectator-V5 no disponible: %s", exc)
            return None
        if active is None:
            return None
        return snapshot_from_spectator(active, self.repo, self.ddragon, account["puuid"])

    def live_state(self, max_age: float | None = None) -> tuple[dict | None, dict | None]:
        """Snapshot + recomendaciones cacheados (TTL = REFRESH_SECONDS)."""
        ttl = max_age if max_age is not None else self.settings.refresh_seconds
        with self._live_lock:
            cache = dict(self._live_cache)
        if time.time() - cache["ts"] <= ttl and cache["ts"] > 0:
            return cache["snapshot"], cache["recommendations"]
        return self.refresh_live_state()

    def close(self) -> None:
        self.db.close()

"""Configuracion central de la aplicacion.

Todos los parametros se leen de variables de entorno o de un archivo .env.
La API key de Riot NUNCA se guarda en el objeto Settings: se resuelve bajo
demanda via app.security.secrets, para que no aparezca en logs, repr() ni
en el estado serializado de la app.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# En Windows es comun que un antivirus/proxy corporativo intercepte TLS con
# certificados que certifi no conoce. truststore valida contra el almacen de
# certificados del sistema operativo (el mismo que usa el navegador).
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # opcional: sin truststore se usa certifi normal
    pass


def app_root() -> Path:
    """Raiz de la aplicacion.

    Si corre congelada con PyInstaller, es la carpeta donde vive el .exe
    (ahi se espera el .env del usuario). En desarrollo, la carpeta del proyecto.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_root() -> Path:
    """Raiz de recursos empaquetados (frontend build) dentro del .exe."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_root()))
    return app_root()


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Parametros de ejecucion. Inmutable a proposito."""

    game_name: str
    tag_line: str
    platform_routing: str
    regional_routing: str
    queue_id: int
    match_count: int
    champion: str
    target_tier: str
    champion_history_matches: int
    language: str
    llm_provider: str
    llm_model: str
    refresh_seconds: int
    host: str
    port: int
    log_level: str
    live_client_base_url: str
    storage_dir: Path
    models_dir: Path
    seed_data_dir: Path
    frontend_dist_dir: Path
    public_demo: bool = False

    @property
    def database_path(self) -> Path:
        return self.storage_dir / "lol_recommender.db"

    @property
    def ddragon_cache_dir(self) -> Path:
        return self.storage_dir / "ddragon"

    @property
    def training_dataset_path(self) -> Path:
        return self.storage_dir / "training_dataset.csv"

    def ensure_dirs(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.ddragon_cache_dir.mkdir(parents=True, exist_ok=True)


def load_settings(*, data_root: Path | None = None) -> Settings:
    root = app_root()
    public_demo = os.getenv("PUBLIC_DEMO", "").lower() == "true"
    if not public_demo:
        load_dotenv(root / ".env", override=False)
    runtime_root = data_root or root

    default_seed = root.parent / "Learning" / "lan_ranked_match_sample"
    seed_dir = Path(_env_str("SEED_DATA_DIR", str(default_seed)))

    settings = Settings(
        game_name=_env_str("GAME_NAME", ""),
        tag_line=_env_str("TAG_LINE", ""),
        platform_routing=_env_str("PLATFORM_ROUTING", "la1").lower(),
        regional_routing=_env_str("REGIONAL_ROUTING", "americas").lower(),
        queue_id=_env_int("QUEUE_ID", 420),
        match_count=_env_int("MATCH_COUNT", 30),
        # Campeon con el que se esta a punto de jugar: selecciona que modelo
        # por campeon se entrena/usa. La liga (tier) define el segmento de
        # jugadores del que proviene el historico.
        champion=_env_str("CHAMPION", ""),
        target_tier=_env_str("TARGET_TIER", "GOLD").upper(),
        champion_history_matches=_env_int("CHAMPION_HISTORY_MATCHES", 3000),
        language=_env_str("LANGUAGE", "es_MX"),
        # LLM opcional para el chat (lore/conceptos): groq | gemini |
        # openrouter | openai | "" (desactivado). La key va en LLM_API_KEY
        # y se lee bajo demanda via app.security.secrets, nunca se guarda.
        llm_provider=_env_str("LLM_PROVIDER", "").lower(),
        llm_model=_env_str("LLM_MODEL", ""),
        refresh_seconds=max(5, _env_int("REFRESH_SECONDS", 15)),
        host=_env_str("APP_HOST", "127.0.0.1"),
        port=_env_int("APP_PORT", 8000),
        log_level=_env_str("LOG_LEVEL", "INFO").upper(),
        live_client_base_url=_env_str(
            "LIVE_CLIENT_BASE_URL", "https://127.0.0.1:2999/liveclientdata"
        ),
        storage_dir=runtime_root / "storage",
        models_dir=runtime_root / "models",
        seed_data_dir=seed_dir,
        frontend_dist_dir=bundle_root() / "frontend" / "dist",
        public_demo=public_demo,
    )
    settings.ensure_dirs()
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()

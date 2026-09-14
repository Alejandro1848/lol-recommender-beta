"""Resolucion de secretos.

Orden de resolucion:
1. Variable de entorno (incluye lo cargado desde .env por config.py).
2. (Futuro) Google Secret Manager: ver stub mas abajo.

La API key jamas se hardcodea, jamas se envia al frontend y jamas
se empaqueta dentro del .exe: siempre se lee en tiempo de ejecucion.
"""
from __future__ import annotations

import os


class MissingSecretError(RuntimeError):
    pass


def get_secret(name: str, default: str | None = None) -> str | None:
    """Lee un secreto del entorno. Punto unico de acceso a secretos."""
    value = os.getenv(name, "").strip()
    if value:
        return value

    # Preparado para Google Cloud: si se define GCP_PROJECT y esta instalada
    # google-cloud-secret-manager, aqui se consultaria Secret Manager.
    # Se deja como stub deliberadamente para no agregar dependencias pesadas.
    return default


def get_riot_api_key() -> str:
    """Devuelve la API key de Riot o lanza un error claro si no existe."""
    key = get_secret("RIOT_API_KEY")
    if not key or not key.startswith("RGAPI-") or key == "RGAPI-tu-api-key":
        raise MissingSecretError(
            "RIOT_API_KEY no esta configurada (o sigue siendo el placeholder). "
            "Copia .env.example como .env y coloca tu key real de "
            "https://developer.riotgames.com. Nunca la compartas ni la subas a git."
        )
    return key


def has_riot_api_key() -> bool:
    try:
        get_riot_api_key()
        return True
    except MissingSecretError:
        return False

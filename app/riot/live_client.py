"""Cliente de la Live Client Data API local del cliente de LoL.

Solo funciona mientras el juego esta abierto en ESTA maquina, en
https://127.0.0.1:2999. Usa certificado self-signed de Riot, por eso
verify=False (es trafico loopback, no sale de la maquina).

Importante: esta API expone datos limitados de los enemigos (campeon,
posicion, nivel, scores, items, vida NO disponible para enemigos).
No se inventa nada que la API no exponga.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class LiveClient:
    def __init__(self, base_url: str = "https://127.0.0.1:2999/liveclientdata", timeout: float = 2.0):
        self.base_url = self._normalize_base_url(base_url)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.verify = False
        self.last_error: dict[str, Any] | None = None

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        """Acepta tanto la raiz 2999 como la ruta /liveclientdata."""
        normalized = (base_url or "https://127.0.0.1:2999/liveclientdata").strip().rstrip("/")
        if not normalized.endswith("/liveclientdata"):
            normalized = f"{normalized}/liveclientdata"
        return normalized

    def _get(self, path: str):
        url = f"{self.base_url}{path}"
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                self.last_error = None
                return response.json()
            self.last_error = {
                "url": url,
                "kind": "http_status",
                "status_code": response.status_code,
                "message": f"Live Client respondio HTTP {response.status_code}.",
            }
            return None
        except requests.RequestException as exc:
            message = self._friendly_error(exc)
            self.last_error = {
                "url": url,
                "kind": exc.__class__.__name__,
                "message": message,
                "detail": str(exc),
            }
            return None

    @staticmethod
    def _friendly_error(exc: requests.RequestException) -> str:
        text = str(exc)
        if isinstance(exc, requests.exceptions.ConnectTimeout):
            return "Timeout conectando con 127.0.0.1:2999; el Live Client local no respondio."
        if isinstance(exc, requests.exceptions.ConnectionError):
            if "10061" in text or "Connection refused" in text or "connection refused" in text:
                return "Conexion rechazada en 127.0.0.1:2999; el juego aun no expone Live Client."
            return "No se pudo conectar con 127.0.0.1:2999; Live Client local no disponible."
        if isinstance(exc, requests.exceptions.SSLError):
            return "Error SSL con el certificado local del cliente de LoL."
        return text[:240]

    def _get_with_retries(self, path: str, retries: int = 2, delay_seconds: float = 0.25):
        attempts = max(1, retries + 1)
        for attempt in range(attempts):
            data = self._get(path)
            if data is not None:
                return data
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
        return None

    def is_available(self) -> bool:
        return self._get("/gamestats") is not None

    def get_all_game_data(self, retries: int = 2) -> dict | None:
        """Snapshot completo: activePlayer, allPlayers, events, gameData."""
        return self._get_with_retries("/allgamedata", retries=retries)

    def get_active_player(self) -> dict | None:
        return self._get("/activeplayer")

    def get_player_list(self) -> list[dict] | None:
        return self._get("/playerlist")

    def get_events(self) -> dict | None:
        return self._get("/eventdata")

    def get_game_stats(self) -> dict | None:
        return self._get("/gamestats")

    def diagnostics(self) -> dict[str, Any]:
        """Estado util para la UI cuando el puerto local no responde."""
        if self._get("/gamestats") is not None:
            return {
                "available": True,
                "url": f"{self.base_url}/gamestats",
                "message": "Live Client Data API local disponible.",
            }
        error = self.last_error or {
            "url": f"{self.base_url}/gamestats",
            "kind": "unknown",
            "message": "Live Client Data API local no respondio.",
        }
        return {
            "available": False,
            "url": error.get("url"),
            "kind": error.get("kind"),
            "status_code": error.get("status_code"),
            "message": error.get("message"),
            "detail": error.get("detail"),
            "hint": (
                "Este endpoint solo existe cuando la partida esta cargada en "
                "esta misma maquina. Si el juego ya esta dentro de la Grieta, "
                "revisa que 127.0.0.1:2999 no este bloqueado y reinicia la "
                "partida/cliente si sigue en connection refused."
            ),
        }

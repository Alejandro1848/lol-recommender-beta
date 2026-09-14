"""Cliente LLM opcional para preguntas abiertas (lore, conceptos, tecnica).

El chat sigue siendo determinista para todo lo que depende de datos reales
(items, oro, historial): ahi un LLM podria alucinar. El LLM entra SOLO
cuando la base de conocimiento local no cubre la pregunta (lore de
campeones, mecanicas generales, dudas de jugador nuevo).

Proveedores soportados (todos exponen API compatible con OpenAI):
- groq:       gratis con registro en https://console.groq.com (sin tarjeta).
- gemini:     capa gratuita de Google en https://aistudio.google.com/apikey.
- openrouter: modelos ":free" con cuenta en https://openrouter.ai.
- openai:     de pago (se soporta por completitud; NO es gratuito).

Se configura en .env con LLM_PROVIDER + LLM_API_KEY (+ LLM_MODEL opcional).
Sin configuracion, el chat se comporta exactamente como antes.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from app.security import secrets

logger = logging.getLogger(__name__)

GEMINI_LITE_FALLBACK_MODEL = "gemini-flash-lite-latest"

# base_url SIN barra final; el modelo por defecto es la opcion gratuita
# mas solvente de cada proveedor al momento de escribir esto.
PROVIDERS: dict[str, dict[str, str]] = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        # Alias oficial al flash vigente: los modelos con version fija van
        # quedando sin capa gratuita para cuentas nuevas (p. ej. 2.0-flash).
        "model": "gemini-flash-latest",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
}

SYSTEM_PROMPT = (
    "Eres un coach amable de League of Legends orientado a jugadores nuevos. "
    "Respondes en espanol neutro, claro y breve (maximo ~150 palabras) sobre "
    "mecanicas del juego, conceptos (tempo, wave management, prioridad), "
    "objetivos, y el lore/historia de campeones y regiones de Runaterra. "
    "No expliques ni respondas preguntas sobre temas ajenos a League of "
    "Legends, aunque conozcas la respuesta. Si una consulta esta fuera de "
    "este dominio, responde unicamente: 'Mi especialidad es ayudarte a "
    "mejorar en League of Legends. Si tienes dudas sobre mecánicas básicas, "
    "cómo controlar las oleadas de súbditos, cuándo pelear por el Dragón o "
    "la historia de algún campeón, ¡aquí estaré para ayudarte a ganar tus "
    "partidas!'. No describas, definas ni menciones datos sobre el tema externo. "
    "Si la pregunta pide datos de la partida EN CURSO del usuario (su oro, "
    "sus items, su historial), NO los inventes: responde que esos datos los "
    "da la app con sus paneles en vivo. Si no estas seguro de algo, dilo."
)


class LLMClient:
    """Cliente minimalista de chat-completions compatible con OpenAI."""

    def __init__(
        self,
        provider: str | None,
        model: str | None = None,
        timeout: float = 25.0,
    ):
        self.provider = (provider or "").strip().lower()
        config = PROVIDERS.get(self.provider)
        self.base_url = config["base_url"] if config else None
        self.model = (model or "").strip() or (config["model"] if config else None)
        self.timeout = timeout

    @property
    def api_key(self) -> str | None:
        # Bajo demanda y nunca almacenada, igual que la key de Riot.
        return secrets.get_secret("LLM_API_KEY")

    def available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def ask(self, question: str, context_note: str | None = None) -> str | None:
        """Respuesta del LLM o None si no esta configurado o fallo la red."""
        if not self.available():
            return None
        system = SYSTEM_PROMPT
        if context_note:
            system += (
                "\n\nContexto recuperado de la base local:\n"
                + context_note
                + "\n\nUsa este contexto como fuente principal y no lo contradigas. "
                "Responde directamente a lo que se pregunto; no repitas un "
                "fragmento si no contiene la respuesta. Si el contexto es "
                "insuficiente, dilo claramente y no inventes el dato."
            )
        models = [self.model]
        if self.provider == "gemini" and self.model != GEMINI_LITE_FALLBACK_MODEL:
            models.append(GEMINI_LITE_FALLBACK_MODEL)

        try:
            for index, model in enumerate(models):
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": question},
                        ],
                        "temperature": 0.4,
                        # Holgado a proposito: en modelos con razonamiento
                        # (Gemini 3.x) los tokens de "pensar" cuentan aqui y un
                        # limite corto trunca la respuesta visible.
                        "max_tokens": 2000,
                    },
                    timeout=self.timeout,
                )
                if response.status_code == 200:
                    data: dict[str, Any] = response.json()
                    content = (
                        (data.get("choices") or [{}])[0]
                        .get("message", {})
                        .get("content")
                    )
                    return content.strip() if content else None

                has_fallback = index + 1 < len(models)
                if response.status_code == 503 and has_fallback:
                    logger.warning(
                        "LLM gemini modelo %s saturado (HTTP 503); "
                        "reintentando con %s",
                        model,
                        models[index + 1],
                    )
                    continue

                logger.warning(
                    "LLM %s respondio HTTP %s: %s",
                    self.provider, response.status_code, response.text[:300],
                )
                return None
        except (requests.RequestException, ValueError) as exc:
            logger.warning("LLM %s no disponible: %s", self.provider, exc)
            return None

        return None

"""Constructor de prompts para una futura integracion con un LLM.

Hoy el chatbot es determinista (intents + datos reales). Esta pieza
serializa el mismo contexto a texto para que conectar un LLM local o
una API (Claude, etc.) sea trivial sin tocar chat_service: el LLM
recibiria este prompt como sistema + la pregunta del usuario.
"""
from __future__ import annotations

import json
from typing import Any

SYSTEM_TEMPLATE = """Eres un asistente analitico de League of Legends estrictamente basado en datos.
Reglas:
- Responde SOLO con la informacion del contexto JSON proporcionado.
- Si el dato no esta en el contexto, di claramente que no hay data suficiente.
- Diferencia siempre datos historicos, de partida activa y en vivo.
- Da opciones razonadas con su evidencia, nunca ordenes absolutas.
- Responde en espaniol neutro y conciso.
"""


def build_prompt(context: dict[str, Any], question: str) -> dict[str, str]:
    """Devuelve {system, user} listos para un LLM."""
    snapshot = context.get("snapshot")
    slim_context = {
        "en_partida": snapshot is not None,
        "snapshot": _slim_snapshot(snapshot) if snapshot else None,
        "partidas_historicas": context.get("history_matches", 0),
        "recomendaciones_actuales": context.get("recommendations"),
    }
    return {
        "system": SYSTEM_TEMPLATE + "\n\nContexto:\n" + json.dumps(
            slim_context, ensure_ascii=False, default=str
        ),
        "user": question,
    }


def _slim_snapshot(snapshot: dict) -> dict:
    def slim_player(p):
        if not p:
            return None
        return {
            "riot_id": p.get("riot_id"), "champion": p.get("champion"),
            "position": p.get("position"), "level": p.get("level"),
            "kda": f"{p.get('kills', 0)}/{p.get('deaths', 0)}/{p.get('assists', 0)}",
        }

    return {
        "tiempo_seg": snapshot.get("game_time_seconds"),
        "yo": slim_player(snapshot.get("me")),
        "rival_directo": slim_player(snapshot.get("direct_rival")),
        "aliados": [slim_player(p) for p in snapshot.get("allies", [])],
        "enemigos": [slim_player(p) for p in snapshot.get("enemies", [])],
        "mezcla_danio_enemigo": snapshot.get("enemy_damage_mix"),
        "objetivos": snapshot.get("events_summary"),
    }

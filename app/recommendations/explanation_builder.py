"""Construccion de explicaciones legibles en espaniol para cada recomendacion.

Regla de la casa: toda explicacion debe citar su evidencia (porcentajes,
tamanios de muestra, nivel de similitud) y admitir incertidumbre. Nunca
se redacta una certeza que los datos no soportan.
"""
from __future__ import annotations

CONFIDENCE_LABELS = {"alta": "confianza alta", "media": "confianza media", "baja": "confianza baja"}


def sample_clause(sample_size: int, level_label: str | None = None) -> str:
    if sample_size <= 0:
        return "No hay partidas similares en tu historial local"
    base = f"basado en {sample_size} partida{'s' if sample_size != 1 else ''} similares"
    if level_label:
        base += f" ({level_label.lower()})"
    return base


def small_sample_warning(sample_size: int, threshold: int = 8) -> str | None:
    if 0 < sample_size < threshold:
        return (
            f"Ojo: la muestra es chica ({sample_size} partidas); toma esta "
            "recomendacion como orientacion, no como certeza."
        )
    if sample_size == 0:
        return "No hay data suficiente para respaldar esto con tu historial."
    return None


def damage_mix_clause(mix: dict | None) -> str:
    if not mix:
        return "no hay datos del tipo de danio enemigo"
    magic = round(mix.get("magic", 0) * 100)
    physical = round(mix.get("physical", 0) * 100)
    return (
        f"el perfil del equipo enemigo es ~{physical}% danio fisico y "
        f"~{magic}% danio magico (segun atributos oficiales de Data Dragon)"
    )


def compose(*clauses: str | None) -> str:
    """Une clausulas no vacias en una explicacion fluida."""
    parts = [c.strip().rstrip(".") for c in clauses if c]
    return (". ".join(parts) + ".") if parts else ""

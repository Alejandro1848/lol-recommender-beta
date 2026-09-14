"""Pipeline de actualizacion de datos estaticos (Data Dragon)."""
from __future__ import annotations

import logging

from app.riot.data_dragon import DataDragon

logger = logging.getLogger(__name__)


def update_static_data(ddragon: DataDragon) -> dict:
    """Refresca versiones, campeones e items. Seguro de correr sin red
    (mantiene el cache previo)."""
    summary = ddragon.refresh()
    if summary.get("version"):
        logger.info(
            "Data Dragon actualizado: version %s, %s campeones, %s items.",
            summary["version"], summary["champions"], summary["items"],
        )
    else:
        logger.warning(
            "Data Dragon no disponible (sin red). Se usara el cache local si existe."
        )
    return summary

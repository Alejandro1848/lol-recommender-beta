"""Pipeline que construye el dataset de entrenamiento desde la DB local."""
from __future__ import annotations

import logging

import pandas as pd

from app.config import Settings
from app.data.repositories import MatchRepository
from app.ml.features import build_training_frame
from app.riot.data_dragon import DataDragon

logger = logging.getLogger(__name__)

# El modelo de probabilidad de victoria se entrena SOLO con ranked
# (soloq 420 y flex 440): el scout puede ingestar normales/ARAM de otros
# jugadores a la DB y no deben contaminar el dataset.
RANKED_QUEUE_IDS = {420, 440}


def build_training_dataset(
    repo: MatchRepository, ddragon: DataDragon, settings: Settings
) -> pd.DataFrame:
    """Genera y persiste el dataset (features + target + metadatos).

    Una fila por participante historico. El CSV resultante en storage/
    sirve para auditoria y reproducibilidad del entrenamiento.
    """
    participants = repo.participants_df(enriched=True)
    if participants.empty:
        logger.warning("No hay partidas en la DB; dataset vacio.")
        return pd.DataFrame()

    if "queueId" in participants.columns:
        queue_ids = pd.to_numeric(participants["queueId"], errors="coerce")
        ranked_mask = queue_ids.isin(RANKED_QUEUE_IDS) | queue_ids.isna()
        dropped = int((~ranked_mask).sum())
        if dropped:
            logger.info("Dataset: se excluyen %s filas de colas no ranked.", dropped)
        participants = participants[ranked_mask]
        if participants.empty:
            logger.warning("Sin partidas ranked en la DB; dataset vacio.")
            return pd.DataFrame()

    dataset = build_training_frame(participants, ddragon)
    dataset.to_csv(settings.training_dataset_path, index=False)
    logger.info(
        "Dataset de entrenamiento: %s filas (%s partidas) -> %s",
        len(dataset), dataset["matchId"].nunique(), settings.training_dataset_path,
    )
    return dataset

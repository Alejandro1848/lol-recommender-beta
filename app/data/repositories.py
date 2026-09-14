"""Repositorios: unica puerta de entrada/salida a la base de datos."""
from __future__ import annotations

import logging

import pandas as pd

from app.data.database import Database
from app.data.normalizers import (
    BAN_COLUMNS,
    MATCH_COLUMNS,
    PARTICIPANT_COLUMNS,
    TEAM_COLUMNS,
    coerce_participants,
    enrich_participants,
)

logger = logging.getLogger(__name__)


def _upsert(conn, table: str, columns: list[str], df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    slim = df.copy()
    for col in columns:
        if col not in slim.columns:
            slim[col] = None
    slim = slim[columns].astype(object).where(pd.notnull(slim[columns]), None)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    conn.executemany(sql, slim.itertuples(index=False, name=None))
    return len(slim)


class MatchRepository:
    def __init__(self, db: Database):
        self.db = db
        # Cache en memoria de los DataFrames grandes (participants/teams).
        # Leer y enriquecer 100k+ participantes en cada refresh en vivo
        # tarda segundos; el cache lo vuelve instantaneo y se invalida en
        # cada escritura (upsert_bundle).
        self._df_cache: dict = {}

    # ------------------------------------------------------------ escritura

    def upsert_bundle(self, tables: dict[str, pd.DataFrame]) -> dict[str, int]:
        """Guarda el resultado de flatten_matches / CSVs seed."""
        participants = coerce_participants(tables.get("participants", pd.DataFrame()))
        if "win" in participants.columns:
            participants["win"] = participants["win"].fillna(0).astype(int)
        teams = tables.get("teams", pd.DataFrame()).copy()
        if not teams.empty and not pd.api.types.is_numeric_dtype(teams["win"]):
            teams["win"] = (
                teams["win"].astype(str).str.lower().map({"true": 1, "false": 0}).fillna(0).astype(int)
            )
        counts = {}
        with self.db.lock, self.db.conn:
            counts["matches"] = _upsert(self.db.conn, "matches", MATCH_COLUMNS, tables.get("matches"))
            counts["participants"] = _upsert(self.db.conn, "participants", PARTICIPANT_COLUMNS, participants)
            counts["teams"] = _upsert(self.db.conn, "teams", TEAM_COLUMNS, teams)
            counts["bans"] = _upsert(self.db.conn, "bans", BAN_COLUMNS, tables.get("bans"))
        self._df_cache.clear()
        logger.info("Upsert en DB: %s", counts)
        return counts

    # -------------------------------------------------------------- lectura

    def known_match_ids(self) -> set[str]:
        with self.db.lock:
            rows = self.db.conn.execute("SELECT matchId FROM matches").fetchall()
        return {row["matchId"] for row in rows}

    def match_count(self) -> int:
        with self.db.lock:
            row = self.db.conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()
        return int(row["n"])

    def matches_df(self) -> pd.DataFrame:
        with self.db.lock:
            return pd.read_sql_query("SELECT * FROM matches", self.db.conn)

    def teams_df(self) -> pd.DataFrame:
        cached = self._df_cache.get("teams")
        if cached is not None:
            return cached.copy()
        with self.db.lock:
            df = pd.read_sql_query("SELECT * FROM teams", self.db.conn)
        self._df_cache["teams"] = df
        return df.copy()

    def participants_df(self, enriched: bool = True) -> pd.DataFrame:
        """Todos los participantes; con metricas derivadas si enriched=True.

        gameVersion se rellena desde la tabla matches cuando falta (los CSV
        seed originales no lo traian a nivel participante).
        """
        cache_key = ("participants", enriched)
        cached = self._df_cache.get(cache_key)
        if cached is not None:
            return cached.copy()
        with self.db.lock:
            df = pd.read_sql_query(
                """
                SELECT p.*, COALESCE(p.gameVersion, m.gameVersion) AS gameVersionFilled
                FROM participants p LEFT JOIN matches m ON p.matchId = m.matchId
                """,
                self.db.conn,
            )
        if df.empty:
            return df
        df["gameVersion"] = df.pop("gameVersionFilled")
        df = coerce_participants(df)
        result = enrich_participants(df) if enriched else df
        self._df_cache[cache_key] = result
        return result.copy()

    # ------------------------------------------------------------ timelines

    TIMELINE_COLUMNS = [
        "matchId", "minute",
        "kills_100", "kills_200", "level_100", "level_200", "cs_100", "cs_200",
        "turrets_100", "turrets_200", "dragons_100", "dragons_200",
        "barons_100", "barons_200",
    ]

    def upsert_timeline_minutes(self, rows: pd.DataFrame) -> int:
        if rows is None or rows.empty:
            return 0
        with self.db.lock, self.db.conn:
            return _upsert(self.db.conn, "timeline_minutes", self.TIMELINE_COLUMNS, rows)

    def matches_with_timeline(self) -> set[str]:
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT DISTINCT matchId FROM timeline_minutes"
            ).fetchall()
        return {row["matchId"] for row in rows}

    def timeline_minutes_df(self) -> pd.DataFrame:
        """Estado por minuto de cada partida, con el ganador (team 100) unido."""
        with self.db.lock:
            return pd.read_sql_query(
                """
                SELECT tm.*, t.win AS win_100, m.gameCreation, m.gameVersion
                FROM timeline_minutes tm
                JOIN teams t ON t.matchId = tm.matchId AND t.teamId = 100
                JOIN matches m ON m.matchId = tm.matchId
                """,
                self.db.conn,
            )

    def timeline_minutes_for_match(self, match_id: str) -> pd.DataFrame:
        """Estado por minuto de UNA partida (para la revision post-partida)."""
        with self.db.lock:
            return pd.read_sql_query(
                "SELECT * FROM timeline_minutes WHERE matchId = ? ORDER BY minute",
                self.db.conn,
                params=(match_id,),
            )

    def player_participants_df(self, puuid: str) -> pd.DataFrame:
        df = self.participants_df()
        if df.empty:
            return df
        return df[df["puuid"] == puuid].sort_values("gameCreation", ascending=False)


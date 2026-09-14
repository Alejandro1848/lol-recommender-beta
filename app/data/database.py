"""Base de datos local SQLite.

SQLite es suficiente para una app local de un solo usuario, no requiere
servidor y empaqueta bien en un .exe. Todas las escrituras pasan por
repositories.py; nadie mas toca la conexion directamente.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    matchId TEXT PRIMARY KEY,
    dataVersion TEXT,
    gameId INTEGER,
    gameCreation INTEGER,
    gameStartTimestamp INTEGER,
    gameEndTimestamp INTEGER,
    gameDuration INTEGER,
    gameMode TEXT,
    gameType TEXT,
    gameVersion TEXT,
    mapId INTEGER,
    queueId INTEGER,
    platformId TEXT
);

CREATE TABLE IF NOT EXISTS participants (
    matchId TEXT NOT NULL,
    puuid TEXT,
    riotIdGameName TEXT,
    riotIdTagline TEXT,
    summonerName TEXT,
    championId INTEGER,
    championName TEXT,
    teamId INTEGER,
    teamPosition TEXT,
    individualPosition TEXT,
    win INTEGER,
    kills INTEGER,
    deaths INTEGER,
    assists INTEGER,
    champLevel INTEGER,
    goldEarned INTEGER,
    goldSpent INTEGER,
    totalMinionsKilled INTEGER,
    neutralMinionsKilled INTEGER,
    totalDamageDealtToChampions INTEGER,
    physicalDamageDealtToChampions INTEGER,
    magicDamageDealtToChampions INTEGER,
    trueDamageDealtToChampions INTEGER,
    totalDamageTaken INTEGER,
    damageDealtToObjectives INTEGER,
    visionScore INTEGER,
    wardsPlaced INTEGER,
    turretTakedowns INTEGER,
    dragonKills INTEGER,
    baronKills INTEGER,
    killingSprees INTEGER,
    largestMultiKill INTEGER,
    totalTimeSpentDead INTEGER,
    item0 INTEGER, item1 INTEGER, item2 INTEGER,
    item3 INTEGER, item4 INTEGER, item5 INTEGER, item6 INTEGER,
    gameCreation INTEGER,
    gameDuration INTEGER,
    queueId INTEGER,
    platformId TEXT,
    gameVersion TEXT,
    PRIMARY KEY (matchId, puuid)
);

CREATE TABLE IF NOT EXISTS teams (
    matchId TEXT NOT NULL,
    teamId INTEGER NOT NULL,
    win INTEGER,
    baron_kills INTEGER,
    champion_kills INTEGER,
    dragon_kills INTEGER,
    horde_kills INTEGER,
    inhibitor_kills INTEGER,
    riftHerald_kills INTEGER,
    tower_kills INTEGER,
    PRIMARY KEY (matchId, teamId)
);

CREATE TABLE IF NOT EXISTS bans (
    matchId TEXT NOT NULL,
    teamId INTEGER,
    championId INTEGER,
    pickTurn INTEGER,
    PRIMARY KEY (matchId, teamId, pickTurn)
);

CREATE TABLE IF NOT EXISTS timeline_minutes (
    matchId TEXT NOT NULL,
    minute INTEGER NOT NULL,
    kills_100 INTEGER, kills_200 INTEGER,
    level_100 INTEGER, level_200 INTEGER,
    cs_100 INTEGER, cs_200 INTEGER,
    turrets_100 INTEGER, turrets_200 INTEGER,
    dragons_100 INTEGER, dragons_200 INTEGER,
    barons_100 INTEGER, barons_200 INTEGER,
    PRIMARY KEY (matchId, minute)
);

CREATE INDEX IF NOT EXISTS idx_participants_puuid ON participants(puuid);
CREATE INDEX IF NOT EXISTS idx_participants_champ ON participants(championName, teamPosition);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        team_columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(teams)").fetchall()
        }
        if "horde_kills" not in team_columns:
            logger.info("Migrando tabla teams: agregando horde_kills")
            self._conn.execute("ALTER TABLE teams ADD COLUMN horde_kills INTEGER")

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    def close(self) -> None:
        self._conn.close()

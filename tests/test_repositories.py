"""Tests de la capa de persistencia (SQLite en tmpdir) y rate limiter."""
import time

from app.data.database import Database
from app.data.normalizers import flatten_matches
from app.data.repositories import MatchRepository
from app.riot.rate_limit import SlidingWindowRateLimiter
from tests.test_normalizers import _raw_match


def test_upsert_and_read(tmp_path):
    db = Database(tmp_path / "test.db")
    repo = MatchRepository(db)
    tables = flatten_matches([_raw_match()])

    counts = repo.upsert_bundle(tables)
    assert counts["matches"] == 1
    assert counts["participants"] == 2
    assert repo.match_count() == 1
    assert repo.known_match_ids() == {"LA1_1"}

    # Idempotente: reinsertar no duplica
    repo.upsert_bundle(tables)
    assert repo.match_count() == 1

    df = repo.participants_df()
    assert len(df) == 2
    assert "kda" in df.columns and "patch" in df.columns
    mine = repo.player_participants_df("p1")
    assert len(mine) == 1
    db.close()


def test_participants_cache_invalidated_on_write(tmp_path):
    """El cache en memoria acelera lecturas repetidas y una escritura lo
    invalida (los datos nuevos deben verse en la siguiente lectura)."""
    db = Database(tmp_path / "test.db")
    repo = MatchRepository(db)
    repo.upsert_bundle(flatten_matches([_raw_match()]))

    first = repo.participants_df()
    second = repo.participants_df()
    assert len(first) == len(second) == 2
    # Devolver copias: mutar el resultado no contamina el cache.
    second["kills"] = 999
    assert (repo.participants_df()["kills"] != 999).all()

    raw = _raw_match()
    raw["metadata"]["matchId"] = "LA1_2"
    repo.upsert_bundle(flatten_matches([raw]))
    assert len(repo.participants_df()) == 4
    assert len(repo.teams_df()) == repo.teams_df().shape[0]
    db.close()


def test_rate_limiter_blocks_burst():
    limiter = SlidingWindowRateLimiter(per_second=3, per_two_minutes=1000)
    start = time.monotonic()
    for _ in range(7):
        limiter.acquire()
    elapsed = time.monotonic() - start
    # 7 requests con limite 3/s => al menos ~1s de espera acumulada
    assert elapsed >= 0.9

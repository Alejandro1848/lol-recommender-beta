"""Tests del modo prueba: snapshots simulados sin cliente de LoL."""
import pandas as pd

from app.pipelines.simulate_live import snapshot_from_replay, snapshot_from_spectator


class FakeRepo:
    def __init__(self, participants):
        self._participants = participants

    def participants_df(self, enriched=True):
        return self._participants

    def teams_df(self):
        rows = []
        for match_id in self._participants["matchId"].unique():
            for team_id, win in ((100, 1), (200, 0)):
                rows.append({
                    "matchId": match_id, "teamId": team_id, "win": win,
                    "baron_kills": 1, "champion_kills": 20, "dragon_kills": 3,
                    "horde_kills": 2, "inhibitor_kills": 1,
                    "riftHerald_kills": 1, "tower_kills": 8,
                })
        return pd.DataFrame(rows)


def test_replay_snapshot_scales_stats(sample_participants, fake_ddragon):
    repo = FakeRepo(sample_participants)
    snapshot = snapshot_from_replay(repo, fake_ddragon, my_puuid="me", minute=15)

    assert snapshot is not None
    assert snapshot["simulated"] == "replay"
    assert snapshot["live_signals_available"] is True
    assert snapshot["warnings"]
    assert snapshot["game_time_seconds"] == 900
    assert snapshot["me"]["champion"] == "Ahri"
    assert snapshot["direct_rival"]["champion"] == "Zed"
    assert len(snapshot["allies"]) == 4
    assert len(snapshot["enemies"]) == 5
    # Al 50% de la partida (30 min totales), las stats van escaladas a la mitad
    assert snapshot["me"]["kills"] <= 5
    assert snapshot["events_summary"]["ORDER"]["dragons"] <= 3
    assert snapshot["events_summary"]["ORDER"]["grubs"] <= 2
    assert snapshot["enemy_damage_mix"] is not None


def test_replay_without_data(fake_ddragon):
    repo = FakeRepo(pd.DataFrame())
    assert snapshot_from_replay(repo, fake_ddragon, minute=10) is None


def test_spectator_snapshot_infers_roles(sample_participants, fake_ddragon):
    repo = FakeRepo(sample_participants)
    active_game = {
        "gameLength": 600,
        "gameMode": "CLASSIC",
        "participants": [
            # key de FakeDataDragon: Ahri=1, Zed=2, Garen=3, Malphite=4, Jinx=5, Lux=6
            {"championId": 3, "teamId": 100, "puuid": "me", "riotId": "Me#TST",
             "spell1Id": 4, "spell2Id": 12},
            {"championId": 1, "teamId": 100, "puuid": "a1", "riotId": "A1#TST",
             "spell1Id": 4, "spell2Id": 11},  # smite => JUNGLE
            {"championId": 5, "teamId": 100, "puuid": "a2", "riotId": "A2#TST",
             "spell1Id": 4, "spell2Id": 7},
            {"championId": 6, "teamId": 100, "puuid": "a3", "riotId": "A3#TST",
             "spell1Id": 4, "spell2Id": 3},
            {"championId": 4, "teamId": 100, "puuid": "a4", "riotId": "A4#TST",
             "spell1Id": 4, "spell2Id": 14},
            *[{"championId": cid, "teamId": 200, "puuid": f"e{cid}", "riotId": f"E{cid}#TST",
               "spell1Id": 4, "spell2Id": 12} for cid in (2, 3, 4, 5, 6)],
        ],
    }
    snapshot = snapshot_from_spectator(active_game, repo, fake_ddragon, my_puuid="me")

    assert snapshot is not None
    assert snapshot["simulated"] == "spectator"
    assert snapshot["live_signals_available"] is False
    assert snapshot["me"]["riot_id"] == "Me#TST"
    # El que lleva Smite quedo como JUNGLE
    jungler = next(p for p in snapshot["allies"] if p["riot_id"] == "A1#TST")
    assert jungler["position"] == "JUNGLE"
    # Los 5 roles del equipo aliado quedaron asignados sin repetirse
    ally_roles = {p["position"] for p in snapshot["allies"] + [snapshot["me"]]}
    assert ally_roles == {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}
    assert len(snapshot["enemies"]) == 5

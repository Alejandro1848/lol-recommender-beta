"""Tests de normalizacion: flatten, roles, derivadas y rival directo."""
import pandas as pd

from app.data.normalizers import (
    attach_direct_opponent,
    coerce_participants,
    enrich_participants,
    flatten_matches,
    normalize_role,
)


def _raw_match():
    def participant(puuid, champ, team, role, win):
        return {
            "puuid": puuid, "championName": champ, "championId": 1,
            "teamId": team, "teamPosition": role, "win": win,
            "kills": 4, "deaths": 2, "assists": 8, "goldEarned": 12000,
            "totalMinionsKilled": 180, "neutralMinionsKilled": 10,
            "totalDamageDealtToChampions": 15000, "totalDamageTaken": 18000,
            "visionScore": 25,
        }

    return {
        "metadata": {"matchId": "LA1_1", "dataVersion": "2"},
        "info": {
            "gameCreation": 1700000000000, "gameDuration": 1800, "queueId": 420,
            "gameVersion": "15.10.1", "platformId": "LA1", "mapId": 11,
            "gameMode": "CLASSIC", "gameType": "MATCHED_GAME", "gameId": 1,
            "participants": [
                participant("p1", "Ahri", 100, "MIDDLE", True),
                participant("p2", "Zed", 200, "MIDDLE", False),
            ],
            "teams": [
                {"teamId": 100, "win": True,
                 "objectives": {"dragon": {"kills": 3}, "baron": {"kills": 1},
                                "tower": {"kills": 8}, "champion": {"kills": 20},
                                "horde": {"kills": 2}, "inhibitor": {"kills": 1},
                                "riftHerald": {"kills": 1}},
                 "bans": [{"championId": 55, "pickTurn": 1}]},
                {"teamId": 200, "win": False, "objectives": {}, "bans": []},
            ],
        },
    }


def test_normalize_role_aliases():
    assert normalize_role("MID") == "MIDDLE"
    assert normalize_role("bottom") == "BOTTOM"
    assert normalize_role("SUPPORT") == "UTILITY"
    assert normalize_role("NONE") is None
    assert normalize_role(None) is None


def test_flatten_matches_shapes():
    tables = flatten_matches([_raw_match()])
    assert len(tables["matches"]) == 1
    assert len(tables["participants"]) == 2
    assert len(tables["teams"]) == 2
    assert len(tables["bans"]) == 1
    assert tables["teams"].iloc[0]["dragon_kills"] == 3
    assert tables["teams"].iloc[0]["horde_kills"] == 2
    assert tables["participants"].iloc[0]["gameVersion"] == "15.10.1"


def test_enrich_and_opponent():
    tables = flatten_matches([_raw_match()])
    df = enrich_participants(coerce_participants(tables["participants"]))
    assert df.loc[df["puuid"] == "p1", "csPerMin"].iloc[0] > 0
    assert df.loc[df["puuid"] == "p1", "patch"].iloc[0] == "15.10"

    with_opp = attach_direct_opponent(df)
    me = with_opp[with_opp["puuid"] == "p1"].iloc[0]
    assert me["opponentChampionName"] == "Zed"


def test_coerce_handles_string_bools():
    df = pd.DataFrame([{"matchId": "X", "puuid": "p", "win": "True", "teamPosition": "MID"}])
    out = coerce_participants(df)
    assert out["win"].iloc[0] == 1
    assert out["teamPosition"].iloc[0] == "MIDDLE"

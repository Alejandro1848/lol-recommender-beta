"""Fixtures compartidos: datos sinteticos y un DataDragon falso sin red."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data.normalizers import coerce_participants, enrich_participants  # noqa: E402


class FakeDataDragon:
    """DataDragon determinista para tests (sin red, sin cache)."""

    CHAMPS = {
        "Ahri": {"attack": 3, "defense": 4, "magic": 8, "difficulty": 5, "tags": ["Mage"]},
        "Zed": {"attack": 9, "defense": 2, "magic": 1, "difficulty": 7, "tags": ["Assassin"]},
        "Garen": {"attack": 7, "defense": 7, "magic": 1, "difficulty": 1, "tags": ["Fighter"]},
        "Malphite": {"attack": 5, "defense": 9, "magic": 7, "difficulty": 2, "tags": ["Tank"]},
        "Jinx": {"attack": 9, "defense": 2, "magic": 4, "difficulty": 6, "tags": ["Marksman"]},
        "Lux": {"attack": 2, "defense": 4, "magic": 9, "difficulty": 5, "tags": ["Mage"]},
    }

    def champion_info(self, name):
        info = self.CHAMPS.get(name)
        if info:
            return {**info, "source": "ddragon"}
        return {"attack": 5, "defense": 5, "magic": 5, "difficulty": 5, "tags": [], "source": "fallback"}

    def champion_damage_profile(self, name):
        info = self.champion_info(name)
        total = max(info["attack"] + info["magic"], 1)
        return {
            "physical": round(info["attack"] / total, 3),
            "magic": round(info["magic"] / total, 3),
            "tags": info["tags"],
            "source": info["source"],
        }

    def primary_tag(self, name):
        tags = self.champion_info(name)["tags"]
        return tags[0] if tags else None

    def champions(self):
        return {
            name: {"id": name, "name": name, "key": str(i), "tags": data["tags"]}
            for i, (name, data) in enumerate(self.CHAMPS.items(), start=1)
        }

    def champion_by_key(self, champion_id):
        return next(
            (c for c in self.champions().values() if int(c["key"]) == int(champion_id)),
            None,
        )

    def champion_image_url(self, name):
        return f"https://fake/{name}.png"

    ITEM_GOLD = {
        3031: 3450, 3006: 1100, 3072: 3400, 3111: 1250, 3065: 2700,
        3047: 1200, 3143: 2700, 3156: 3100,
    }

    def item_image_url(self, item_id):
        return f"https://fake/item/{item_id}.png"

    def item_name(self, item_id):
        return f"Item {item_id}"

    def item_gold(self, item_id):
        return self.ITEM_GOLD.get(item_id)

    def item_is_final(self, item_id):
        # En el fake, todo item con costo conocido cuenta como compra final.
        return True if item_id in self.ITEM_GOLD else None


@pytest.fixture
def fake_ddragon():
    return FakeDataDragon()


def _participant(match_id, puuid, champ, team, role, win, opp_pool, game_creation, **kw):
    return {
        "matchId": match_id, "puuid": puuid, "riotIdGameName": puuid, "riotIdTagline": "TST",
        "summonerName": puuid, "championId": 1, "championName": champ, "teamId": team,
        "teamPosition": role, "individualPosition": role, "win": win,
        "kills": kw.get("kills", 5), "deaths": kw.get("deaths", 4), "assists": kw.get("assists", 6),
        "champLevel": 15, "goldEarned": 11000, "goldSpent": 10000,
        "totalMinionsKilled": 150, "neutralMinionsKilled": 20,
        "totalDamageDealtToChampions": 18000, "physicalDamageDealtToChampions": 9000,
        "magicDamageDealtToChampions": 8000, "trueDamageDealtToChampions": 1000,
        "totalDamageTaken": 20000, "damageDealtToObjectives": 5000,
        "visionScore": 22, "wardsPlaced": 10, "turretTakedowns": 2,
        "dragonKills": 0, "baronKills": 0, "killingSprees": 1, "largestMultiKill": 1,
        "totalTimeSpentDead": 120,
        "item0": kw.get("item0", 3031), "item1": 3006, "item2": 3072,
        "item3": 0, "item4": 0, "item5": 0, "item6": 3363,
        "gameCreation": game_creation, "gameDuration": 1800, "queueId": 420,
        "platformId": "LA1", "gameVersion": "15.10.680.1234",
    }


@pytest.fixture
def sample_participants():
    """4 partidas sinteticas: Ahri (MID) vs Zed/Lux, con roles completos."""
    rows = []
    roles = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
    blue = ["Garen", "Malphite", "Ahri", "Jinx", "Lux"]
    red = ["Malphite", "Garen", "Zed", "Jinx", "Lux"]
    for g in range(4):
        match_id = f"LA1_{1000 + g}"
        creation = 1700000000000 + g * 86400000
        blue_wins = g % 2 == 0
        for i, role in enumerate(roles):
            rows.append(_participant(
                match_id, f"player_blue_{i}" if i != 2 else "me",
                blue[i], 100, role, blue_wins, red, creation,
            ))
            rows.append(_participant(
                match_id, f"player_red_{i}", red[i], 200, role, not blue_wins, blue, creation,
            ))
    df = coerce_participants(pd.DataFrame(rows))
    df["win"] = df["win"].astype(int)
    return enrich_participants(df)

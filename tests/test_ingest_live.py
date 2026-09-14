"""Tests para normalizacion del Live Client Data API."""
from __future__ import annotations

from app.pipelines.ingest_live import _normalize_player


def test_normalize_player_accepts_non_dict_items(fake_ddragon):
    player = _normalize_player(
        {
            "riotId": "Dohkoo25#TAG",
            "championName": "Ahri",
            "team": "ORDER",
            "position": "MIDDLE",
            "level": 6,
            "scores": {"kills": 1, "deaths": 0, "assists": 2, "creepScore": 50},
            "items": [
                {"itemID": 1056, "displayName": "Doran's Ring", "count": 1},
                "2003",
                0,
                None,
            ],
        },
        fake_ddragon,
    )

    assert player["items"] == [
        {
            "id": 1056,
            "name": "Doran's Ring",
            "image_url": "https://fake/item/1056.png",
            "count": 1,
        },
        {
            "id": 2003,
            "name": "Item 2003",
            "image_url": "https://fake/item/2003.png",
            "count": 1,
        },
    ]

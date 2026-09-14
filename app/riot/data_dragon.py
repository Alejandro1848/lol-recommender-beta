"""Cliente de Data Dragon (datos estaticos publicos de Riot, sin API key).

Descarga y cachea en storage/ddragon: versiones, campeones e items en el
idioma configurado. Expone URLs de imagenes y un perfil de tipo de danio
por campeon derivado de los datos oficiales (info.attack / info.magic).

Si no hay red y no hay cache, degrada con valores neutros y lo reporta;
nunca inventa datos.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DDRAGON_BASE = "https://ddragon.leagueoflegends.com"


class DataDragon:
    def __init__(self, cache_dir: Path, language: str = "es_MX", session: requests.Session | None = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.language = language
        self.session = session or requests.Session()
        self._version: str | None = None
        self._champions: dict | None = None
        self._items: dict | None = None
        self._champ_by_key: dict[int, dict] | None = None

    # ------------------------------------------------------------- descarga

    def _fetch_json(self, url: str):
        try:
            response = self.session.get(url, timeout=15)
            if response.status_code == 200:
                return response.json()
        except requests.RequestException as exc:
            logger.warning("Data Dragon no disponible (%s): %s", url, exc)
        return None

    def _cached_or_fetch(self, filename: str, url: str, force: bool = False):
        path = self.cache_dir / filename
        if not force and path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        data = self._fetch_json(url)
        if data is not None:
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
        return None

    def refresh(self) -> dict:
        """Fuerza la actualizacion del cache estatico. Devuelve resumen."""
        self._version = None
        self._champions = None
        self._items = None
        self._champ_by_key = None
        versions = self._cached_or_fetch("versions.json", f"{DDRAGON_BASE}/api/versions.json", force=True)
        version = versions[0] if versions else None
        summary = {"version": version, "champions": 0, "items": 0}
        if version:
            champs = self._cached_or_fetch(
                f"champion_{self.language}_{version}.json",
                f"{DDRAGON_BASE}/cdn/{version}/data/{self.language}/champion.json",
                force=True,
            )
            items = self._cached_or_fetch(
                f"item_{self.language}_{version}.json",
                f"{DDRAGON_BASE}/cdn/{version}/data/{self.language}/item.json",
                force=True,
            )
            summary["champions"] = len((champs or {}).get("data", {}))
            summary["items"] = len((items or {}).get("data", {}))
        return summary

    # -------------------------------------------------------------- acceso

    @property
    def available(self) -> bool:
        return self.version() is not None

    def version(self) -> str | None:
        if self._version is None:
            versions = self._cached_or_fetch("versions.json", f"{DDRAGON_BASE}/api/versions.json")
            self._version = versions[0] if versions else None
        return self._version

    def champions(self) -> dict:
        """Dict {championId(str): data} de champion.json, o {} si no hay datos."""
        if self._champions is None:
            version = self.version()
            data = None
            if version:
                data = self._cached_or_fetch(
                    f"champion_{self.language}_{version}.json",
                    f"{DDRAGON_BASE}/cdn/{version}/data/{self.language}/champion.json",
                )
            self._champions = (data or {}).get("data", {})
        return self._champions

    def items(self) -> dict:
        """Dict {itemId(str): data} de item.json, o {} si no hay datos."""
        if self._items is None:
            version = self.version()
            data = None
            if version:
                data = self._cached_or_fetch(
                    f"item_{self.language}_{version}.json",
                    f"{DDRAGON_BASE}/cdn/{version}/data/{self.language}/item.json",
                )
            self._items = (data or {}).get("data", {})
        return self._items

    def champion_by_key(self, champion_id: int) -> dict | None:
        """Busca campeon por championId numerico de Match-V5/Spectator."""
        if self._champ_by_key is None:
            self._champ_by_key = {
                int(champ["key"]): champ for champ in self.champions().values()
            }
        return self._champ_by_key.get(int(champion_id))

    def champion_by_name(self, name: str) -> dict | None:
        """Busca por id interno (MonkeyKing) o por nombre visible (Wukong)."""
        if not name:
            return None
        champs = self.champions()
        if name in champs:
            return champs[name]
        lowered = name.strip().lower()
        for champ in champs.values():
            if champ.get("id", "").lower() == lowered or champ.get("name", "").lower() == lowered:
                return champ
        return None

    # ------------------------------------------------------------- imagenes

    def champion_image_url(self, champion_name: str) -> str | None:
        champ = self.champion_by_name(champion_name)
        version = self.version()
        if not champ or not version:
            return None
        return f"{DDRAGON_BASE}/cdn/{version}/img/champion/{champ['image']['full']}"

    def champion_splash_url(self, champion_name: str) -> str | None:
        champ = self.champion_by_name(champion_name)
        if not champ:
            return None
        return f"{DDRAGON_BASE}/cdn/img/champion/loading/{champ['id']}_0.jpg"

    def item_image_url(self, item_id: int) -> str | None:
        version = self.version()
        if not version or not item_id:
            return None
        return f"{DDRAGON_BASE}/cdn/{version}/img/item/{item_id}.png"

    def profile_icon_url(self, icon_id: int) -> str | None:
        version = self.version()
        if not version:
            return None
        return f"{DDRAGON_BASE}/cdn/{version}/img/profileicon/{icon_id}.png"

    def item_name(self, item_id: int) -> str:
        item = self.items().get(str(item_id))
        return item["name"] if item else f"Item {item_id}"

    def item_data(self, item_id: int) -> dict | None:
        """Payload completo de item.json para un item."""
        return self.items().get(str(item_id))

    def item_gold(self, item_id: int) -> int | None:
        """Costo total del item segun item.json, o None si no hay datos."""
        item = self.item_data(item_id)
        if not item:
            return None
        return (item.get("gold") or {}).get("total")

    def item_sell_gold(self, item_id: int) -> int | None:
        item = self.item_data(item_id)
        if not item:
            return None
        return (item.get("gold") or {}).get("sell")

    def item_tags(self, item_id: int) -> list[str]:
        item = self.item_data(item_id) or {}
        return item.get("tags") or []

    def item_stats(self, item_id: int) -> dict:
        item = self.item_data(item_id) or {}
        return item.get("stats") or {}

    def item_into(self, item_id: int) -> list[int]:
        item = self.item_data(item_id) or {}
        out = []
        for value in item.get("into") or []:
            try:
                out.append(int(value))
            except (TypeError, ValueError):
                continue
        return out

    def item_from(self, item_id: int) -> list[int]:
        item = self.item_data(item_id) or {}
        out = []
        for value in item.get("from") or []:
            try:
                out.append(int(value))
            except (TypeError, ValueError):
                continue
        return out

    def item_depth(self, item_id: int) -> int:
        """Profundidad aproximada del arbol de componentes."""
        parents = self.item_from(item_id)
        if not parents:
            return 0
        return 1 + max(self.item_depth(parent) for parent in parents)

    def item_purchasable(self, item_id: int) -> bool | None:
        """True si el item se puede COMPRAR en tienda; False para items que
        solo se obtienen por evolucion/mision (linea de soporte: Atlas del
        Mundo evoluciona gratis a Brujula Runica y al item final) o que no
        estan en tienda. None si no hay datos estaticos."""
        item = self.item_data(item_id)
        if item is None:
            return None
        if item.get("inStore") is False:
            return False
        return bool((item.get("gold") or {}).get("purchasable", True))

    def item_is_final(self, item_id: int) -> bool | None:
        """True si el item es una compra terminada (no componente, no
        consumible/trinket). None si no hay datos estaticos disponibles."""
        item = self.item_data(item_id)
        if item is None:
            return None
        tags = set(item.get("tags") or [])
        if tags & {"Consumable", "Trinket"}:
            return False
        if item.get("into"):
            return False
        return bool((item.get("gold") or {}).get("purchasable", True))

    # --------------------------------------------------- perfiles derivados

    def champion_info(self, champion_name: str) -> dict:
        """Atributos oficiales 0-10 de champion.json (attack/defense/magic/difficulty).

        Si el campeon no esta en cache, devuelve valores neutros (5) y
        source='fallback' para que las capas superiores lo comuniquen.
        """
        champ = self.champion_by_name(champion_name)
        if champ and "info" in champ:
            info = champ["info"]
            return {
                "attack": info.get("attack", 5),
                "defense": info.get("defense", 5),
                "magic": info.get("magic", 5),
                "difficulty": info.get("difficulty", 5),
                "tags": champ.get("tags", []),
                "source": "ddragon",
            }
        return {"attack": 5, "defense": 5, "magic": 5, "difficulty": 5, "tags": [], "source": "fallback"}

    def champion_damage_profile(self, champion_name: str) -> dict:
        """Mezcla fisico/magico estimada desde info.attack e info.magic oficiales."""
        info = self.champion_info(champion_name)
        total = max(info["attack"] + info["magic"], 1)
        return {
            "physical": round(info["attack"] / total, 3),
            "magic": round(info["magic"] / total, 3),
            "tags": info["tags"],
            "source": info["source"],
        }

    def primary_tag(self, champion_name: str) -> str | None:
        """Arquetipo principal (Fighter, Mage, Marksman, ...)."""
        tags = self.champion_info(champion_name).get("tags") or []
        return tags[0] if tags else None

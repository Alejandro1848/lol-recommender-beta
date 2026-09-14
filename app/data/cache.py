"""Cache JSON en disco con TTL, para respuestas costosas (perfil del rival,
snapshots, etc.). Simple a proposito: una app local no necesita Redis."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


def _safe_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", key)[:150]


class FileCache:
    def __init__(self, cache_dir: Path | str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{_safe_key(key)}.json"

    def get(self, key: str, ttl_seconds: float | None = None) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if ttl_seconds is not None and time.time() - payload.get("_ts", 0) > ttl_seconds:
            return None
        return payload.get("value")

    def set(self, key: str, value: Any) -> None:
        payload = {"_ts": time.time(), "value": value}
        self._path(key).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def invalidate(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

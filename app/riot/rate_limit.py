"""Rate limiter local para la Riot API.

Las development keys permiten 20 requests/1s y 100 requests/120s.
Este limitador de ventana deslizante se aplica ANTES de cada request para
no depender solo de los 429 de Riot (que igualmente se respetan via
Retry-After en riot_client).
"""
from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowRateLimiter:
    def __init__(
        self,
        per_second: int = 20,
        per_two_minutes: int = 100,
    ) -> None:
        self._limits = [
            (1.0, per_second, deque()),
            (120.0, per_two_minutes, deque()),
        ]
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Bloquea hasta que sea seguro hacer el siguiente request."""
        while True:
            with self._lock:
                now = time.monotonic()
                wait = 0.0
                for window, limit, stamps in self._limits:
                    while stamps and now - stamps[0] > window:
                        stamps.popleft()
                    if len(stamps) >= limit:
                        wait = max(wait, window - (now - stamps[0]) + 0.01)
                if wait == 0.0:
                    for _, _, stamps in self._limits:
                        stamps.append(now)
                    return
            time.sleep(min(wait, 2.0))

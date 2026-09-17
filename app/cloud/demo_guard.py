"""Limites compartidos por la unica instancia de la demo; no son un tope de gasto."""
from collections import deque
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class DemoGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, chat_limit: int = 30, api_limit: int = 300):
        super().__init__(app)
        self.chat_limit = chat_limit
        self.api_limit = api_limit
        self._chat_calls = deque()
        self._api_calls = deque()

    @staticmethod
    def _consume(calls, limit, now):
        while calls and calls[0] <= now - 60:
            calls.popleft()
        if len(calls) >= limit:
            return False
        calls.append(now)
        return True

    async def dispatch(self, request, call_next):
        path = request.url.path.rstrip("/")
        if path.startswith("/api/coach-ai") or (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and not (path == "/api/chat" and request.method == "POST")
        ):
            return JSONResponse({"detail": "La demo no permite ingesta ni entrenamiento."}, status_code=403)
        if path.startswith("/api/") and path != "/api/health":
            now = time.monotonic()
            # No se confia en X-Forwarded-For: el limite es global, no por IP.
            allowed = self._consume(self._api_calls, self.api_limit, now)
            if path == "/api/chat" and request.method == "POST":
                allowed = allowed and self._consume(self._chat_calls, self.chat_limit, now)
            if not allowed:
                return JSONResponse(
                    {"detail": "Limite temporal de la demo. Intenta nuevamente en un minuto."},
                    status_code=429, headers={"Retry-After": "60"},
                )
        return await call_next(request)

"""Stub de autenticacion, preparado para despliegue futuro en Google Cloud.

Hoy la app corre 100% local en localhost y no requiere login. Esta capa
existe para que agregar autenticacion (p. ej. Google OAuth / Identity
Platform) sea un cambio aislado y no una reescritura:

- `AuthStubMiddleware` ya esta enganchado en el server (passthrough).
- `get_current_user` es la dependencia que las rutas usarian con Depends().
- Para activar OAuth real: implementar la validacion del token en
  `verify_token` y poner AUTH_ENABLED=true.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


@dataclass(frozen=True)
class User:
    user_id: str
    email: str | None = None
    is_local: bool = True


LOCAL_USER = User(user_id="local", email=None, is_local=True)


def auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").strip().lower() == "true"


def verify_token(token: str) -> User | None:
    """Punto de extension: validar aqui un ID token de Google (aud, iss, exp).

    Implementacion futura tipica:
        from google.oauth2 import id_token
        from google.auth.transport import requests as grequests
        info = id_token.verify_oauth2_token(token, grequests.Request(), CLIENT_ID)
        return User(user_id=info["sub"], email=info.get("email"), is_local=False)
    """
    return None


class AuthStubMiddleware(BaseHTTPMiddleware):
    """Middleware passthrough. Adjunta el usuario local a cada request.

    Cuando AUTH_ENABLED=true y exista verify_token real, aqui se rechazan
    requests sin token valido (401) para rutas /api/*.
    """

    async def dispatch(self, request: Request, call_next):
        request.state.user = LOCAL_USER
        if auth_enabled():
            auth_header = request.headers.get("Authorization", "")
            token = auth_header.removeprefix("Bearer ").strip()
            user = verify_token(token) if token else None
            if user is None and request.url.path.startswith("/api/"):
                from starlette.responses import JSONResponse

                return JSONResponse(
                    {"detail": "No autorizado: token invalido o ausente."},
                    status_code=401,
                )
            if user is not None:
                request.state.user = user
        return await call_next(request)


async def get_current_user(request: Request) -> User:
    """Dependencia FastAPI para rutas que necesiten el usuario."""
    return getattr(request.state, "user", LOCAL_USER)

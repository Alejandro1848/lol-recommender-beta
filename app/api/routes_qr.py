"""Acceso desde el movil: pagina con codigos QR hacia el dashboard y el
overlay usando la IP local del PC en la red Wi-Fi.

Requisitos para que el telefono pueda entrar:
1. El servidor debe escuchar en la red (APP_HOST=0.0.0.0 en .env), no solo
   en 127.0.0.1.
2. El firewall de Windows debe permitir el puerto (Windows lo pregunta la
   primera vez que python escucha en la red; hay que aceptar).
3. PC y telefono en la misma red Wi-Fi.
"""
from __future__ import annotations

import base64
import io
import socket

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["qr"])


def lan_ip() -> str:
    """IP del PC en la red local (la interfaz con salida por defecto)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no envia nada: solo resuelve la interfaz
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _qr_data_uri(data: str) -> str:
    """QR como data-URI SVG (negro sobre blanco, lo que mejor escanea)."""
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage,
                        box_size=12, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


QR_PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LoL Coach en tu movil</title>
<style>
  body {{
    margin: 0; min-height: 100vh; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 18px; padding: 24px;
    box-sizing: border-box; font-family: 'Segoe UI', sans-serif;
    background: linear-gradient(165deg, #10203a 0%, #0a1428 55%, #060d18 100%);
    color: #f0e6d2;
  }}
  h1 {{ font-size: 1.2rem; margin: 0; letter-spacing: .06em; color: #c8aa6e; }}
  p {{ margin: 0; color: #a09b8c; font-size: .85rem; text-align: center; max-width: 560px; }}
  .warn {{
    color: #f0b232; border: 1px solid rgba(240,178,50,.5); background: rgba(240,178,50,.1);
    padding: 10px 14px; border-radius: 8px; font-size: .85rem; max-width: 560px; text-align: center;
  }}
  .grid {{ display: flex; flex-wrap: wrap; gap: 22px; justify-content: center; }}
  .card {{
    background: rgba(6, 11, 20, .55); border: 2px solid #785a28; border-radius: 12px;
    padding: 16px 20px; text-align: center;
    box-shadow: inset 0 0 0 1px rgba(200,170,110,.35), 0 4px 18px rgba(0,0,0,.55);
  }}
  .card h2 {{
    margin: 0 0 10px; font-size: .8rem; letter-spacing: .1em;
    text-transform: uppercase; color: #c8aa6e;
  }}
  .card img {{ width: 190px; height: 190px; background: #fff; border-radius: 8px; padding: 8px; box-sizing: border-box; }}
  .card a {{ display: block; margin-top: 10px; color: #f0e6d2; font-size: .8rem; word-break: break-all; }}
</style>
</head>
<body>
<h1>LoL COACH EN TU MOVIL</h1>
<p>Escanea con la camara del telefono. El telefono debe estar en la MISMA red Wi-Fi que este PC.</p>
{warning}
<div class="grid">
  <div class="card">
    <h2>Dashboard completo</h2>
    <img src="{dashboard_qr}" alt="QR dashboard">
    <a href="{dashboard_url}">{dashboard_url}</a>
  </div>
  <div class="card">
    <h2>Overlay de partida</h2>
    <img src="{overlay_qr}" alt="QR overlay">
    <a href="{overlay_url}">{overlay_url}</a>
  </div>
</div>
</body>
</html>"""

LOOPBACK_WARNING = (
    '<div class="warn">El servidor solo escucha en 127.0.0.1: el telefono NO '
    "podra entrar. Pon APP_HOST=0.0.0.0 en el .env y reinicia la app.</div>"
)


@router.get("/qr", response_class=HTMLResponse, include_in_schema=False)
def qr_page(request: Request) -> str:
    settings = request.app.state.container.settings
    ip = lan_ip()
    base = f"http://{ip}:{settings.port}"
    dashboard_url = f"{base}/"
    overlay_url = f"{base}/overlay"
    warning = "" if settings.host not in ("127.0.0.1", "localhost") else LOOPBACK_WARNING
    return QR_PAGE.format(
        warning=warning,
        dashboard_qr=_qr_data_uri(dashboard_url),
        dashboard_url=dashboard_url,
        overlay_qr=_qr_data_uri(overlay_url),
        overlay_url=overlay_url,
    )

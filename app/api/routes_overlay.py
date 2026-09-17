"""Overlay in-game: estado compacto (probabilidad + accion + compra) y la
pagina ligera que lo pinta.

El overlay muestra exactamente 3 cosas y nada mas:
  1. probabilidad actual de victoria (modelo in-game calibrado),
  2. proxima accion recomendada segun rol y timers de objetivos,
  3. compra prioritaria al volver a base.

La pagina /overlay funciona en cualquier navegador u OBS; la ventana
always-on-top nativa (overlay_window.py) consume el mismo endpoint.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.coaching.coach_engine import refresh_item_gold
from app.container import ServiceContainer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["overlay"])


def _container(request: Request) -> ServiceContainer:
    return request.app.state.container


@router.get("/overlay/state")
def overlay_state(request: Request) -> dict:
    c = _container(request)
    # Sin reconstruccion sincrona: el overlay debe responder siempre rapido.
    snapshot, recommendations, age = c.cached_live_state()
    if age is None:
        # Arranque frio del servidor: el cache aun se esta construyendo.
        return {
            "in_game": False,
            "warming": True,
            "message": (
                "Preparando historial y modelos (primer arranque del "
                "servidor)... El overlay se activara solo."
            ),
            "refresh_seconds": 5,
        }
    payload = c.coach.build(snapshot, recommendations, snapshot_age_seconds=0 if c.settings.public_demo else age)
    if c.settings.public_demo:
        payload["refresh_seconds"] = c.settings.refresh_seconds
        return payload
    # Oro e inventario en tiempo real: el cache puede tener hasta
    # REFRESH_SECONDS de edad y una compra intermedia lo desactualiza.
    try:
        refresh_item_gold(payload, c.live_client, c.ddragon)
    except Exception:  # el overlay nunca debe caerse por esto
        logger.exception("No se pudo refrescar el oro en vivo del overlay")
    # El overlay sondea mas seguido que el resto de la UI: el recalculo de
    # oro es loopback barato y asi el "faltan X de oro" sigue a la tienda.
    payload["refresh_seconds"] = min(5, c.settings.refresh_seconds)
    return payload


OVERLAY_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>LoL Coach Overlay</title>
<style>
  /* HUD estilo League of Legends: panel metalico oscuro con marco dorado,
     texto marfil/dorado sobre fondo azul noche y acentos por estado. */
  :root { color-scheme: dark; }
  body {
    margin: 0; background: transparent; font-family: 'Segoe UI', sans-serif;
    color: #f0e6d2; -webkit-user-select: none; user-select: none;
  }
  #card {
    max-width: 400px; border-radius: 10px; padding: 12px 14px;
    background: linear-gradient(165deg, #10203a 0%, #0a1428 55%, #060d18 100%);
    border: 2px solid #785a28;
    box-shadow: inset 0 0 0 1px rgba(200, 170, 110, .35), 0 4px 18px rgba(0, 0, 0, .55);
  }
  .tag {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .1em; color: #c8aa6e;
  }
  /* ---- cabecera ---- */
  #header { display: flex; align-items: center; gap: 10px; }
  #gauge { flex: none; }
  #gauge text { font: 700 11px 'Segoe UI', sans-serif; fill: #f0e6d2; }
  #gauge .up { stroke: #0ac8b9; } #gauge .down { stroke: #e84057; } #gauge .even { stroke: #f0b232; }
  #gauge text.up { fill: #0ac8b9; stroke: none; } #gauge text.down { fill: #e84057; stroke: none; }
  #gauge text.even { fill: #f0b232; stroke: none; }
  #portrait-wrap { position: relative; flex: none; width: 42px; height: 42px; }
  #champ-img {
    width: 42px; height: 42px; border-radius: 50%;
    border: 2px solid #785a28; box-sizing: border-box; display: none;
  }
  #role-icon {
    position: absolute; right: -5px; bottom: -3px; width: 18px; height: 18px;
    background: #0a1428; border: 1px solid #785a28; border-radius: 50%;
    display: none; align-items: center; justify-content: center;
  }
  #head-main { flex: 1; min-width: 0; }
  #champ-name {
    font-size: 15px; font-weight: 700; letter-spacing: .12em;
    text-transform: uppercase; color: #f0e6d2;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  #pills { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 4px; }
  .pill {
    font-size: 9.5px; font-weight: 700; padding: 2px 8px; border-radius: 999px;
    border: 1px solid; letter-spacing: .03em;
  }
  .pill.conf-alta { color: #0ac8b9; border-color: rgba(10,200,185,.5); background: rgba(10,200,185,.12); }
  .pill.conf-media { color: #f0b232; border-color: rgba(240,178,50,.5); background: rgba(240,178,50,.12); }
  .pill.conf-baja { color: #e84057; border-color: rgba(232,64,87,.5); background: rgba(232,64,87,.12); }
  .pill.neutral { color: #a09b8c; border-color: rgba(160,155,140,.45); background: rgba(160,155,140,.10); }
  #clock { flex: none; font-size: 14px; font-weight: 700; color: #c8aa6e; align-self: flex-start; }
  /* ---- accion en turno: el protagonista ---- */
  #action-box {
    margin-top: 10px; padding: 9px 11px; border-radius: 7px;
    background: rgba(6, 11, 20, .55);
    border: 1px solid rgba(200, 170, 110, .3); border-left: 3px solid #c8aa6e;
    display: flex; gap: 10px; align-items: stretch;
  }
  #action-box.urgent { border-color: rgba(255, 137, 54, .55); border-left-color: #ff8936; }
  #action-box.urgent .tag { color: #ff8936; }
  #action-texts { flex: 1; min-width: 0; }
  #action { display: block; margin-top: 3px; font-size: 18px; font-weight: 700; line-height: 1.2; color: #f0e6d2; }
  #action.urgent { color: #ff8936; }
  #action-detail { display: block; margin-top: 4px; font-size: 11px; color: #a09b8c; line-height: 1.4; }
  #mini-map { flex: none; align-self: center; }
  /* ---- compras: bloque secundario ---- */
  #items-box { margin-top: 9px; border-top: 1px solid rgba(200, 170, 110, .25); padding-top: 7px; }
  .item-row { display: flex; align-items: center; gap: 8px; margin-top: 5px; }
  .item-row img {
    width: 26px; height: 26px; border-radius: 4px; flex: none;
    border: 1px solid #785a28;
  }
  .item-main { flex: 1; min-width: 0; }
  .item-name {
    font-size: 12px; font-weight: 600; color: #f0e6d2;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .item-stats { font-size: 10px; color: #a09b8c; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .item-gold {
    flex: none; display: flex; align-items: center; gap: 4px;
    font-size: 11.5px; font-weight: 700; color: #f0b232;
  }
  .item-gold.ready { color: #0ac8b9; }
  #msg { font-size: 13px; color: #c8aa6e; }
  .hidden { display: none; }
</style>
</head>
<body>
<div id="card">
  <div id="msg">Conectando con la partida...</div>
  <div id="content" class="hidden">
    <div id="header">
      <svg id="gauge" width="46" height="46" viewBox="0 0 46 46">
        <circle cx="23" cy="23" r="19" fill="none" stroke="#26334a" stroke-width="4"/>
        <circle id="gauge-arc" class="even" cx="23" cy="23" r="19" fill="none"
                stroke-width="4" stroke-linecap="round" stroke-dasharray="0 119.4"
                transform="rotate(-90 23 23)"/>
        <text id="gauge-text" class="even" x="23" y="27" text-anchor="middle">--%</text>
      </svg>
      <div id="portrait-wrap">
        <img id="champ-img" alt="">
        <span id="role-icon"></span>
      </div>
      <div id="head-main">
        <div id="champ-name"></div>
        <div id="pills">
          <span id="pill-conf" class="pill hidden"></span>
          <span id="pill-state" class="pill neutral hidden"></span>
        </div>
      </div>
      <span id="clock"></span>
    </div>
    <div id="action-box">
      <div id="action-texts"><span class="tag">Siguiente accion</span>
        <span id="action"></span>
        <span id="action-detail"></span></div>
      <svg id="mini-map" width="84" height="84" viewBox="0 0 84 84">
        <rect width="84" height="84" rx="6" fill="#0d1f14"/>
        <line x1="4" y1="4" x2="80" y2="80" stroke="#17394a" stroke-width="13"/>
        <path d="M8 76 V8 H76" fill="none" stroke="#22331f" stroke-width="6" stroke-linejoin="round"/>
        <path d="M8 76 H76 V8" fill="none" stroke="#22331f" stroke-width="6" stroke-linejoin="round"/>
        <line x1="10" y1="74" x2="74" y2="10" stroke="#22331f" stroke-width="6"/>
        <circle cx="11" cy="73" r="5" fill="#1c74a8"/>
        <circle cx="73" cy="11" r="5" fill="#8a2f3d"/>
        <rect x="1" y="1" width="82" height="82" rx="6" fill="none" stroke="rgba(200,170,110,.4)" stroke-width="1.5"/>
        <defs><marker id="arrow-head" viewBox="0 0 8 8" refX="6" refY="4"
          markerWidth="5" markerHeight="5" orient="auto">
          <path d="M0 0 L8 4 L0 8 Z" fill="#f0e6d2"/></marker></defs>
        <path id="route" d="" fill="none" stroke="#f0e6d2" stroke-width="2.5"
              stroke-linecap="round" marker-end="url(#arrow-head)" opacity="0.95"/>
      </svg>
    </div>
    <div id="items-box"><span class="tag">Compras al volver a base</span>
      <div id="item-list"></div></div>
  </div>
</div>
<script>
const POLL_DEFAULT = 15000;
let timer = null;

function fmtClock(s) {
  if (s == null) return "";
  const m = Math.floor(s / 60), ss = Math.floor(s % 60);
  return m + ":" + String(ss).padStart(2, "0");
}

async function poll() {
  let delay = POLL_DEFAULT;
  try {
    const r = await fetch("/api/overlay/state");
    const d = await r.json();
    delay = Math.max(5, d.refresh_seconds || 15) * 1000;
    render(d);
  } catch (e) {
    document.getElementById("msg").textContent = "Servidor no disponible...";
    document.getElementById("msg").classList.remove("hidden");
    document.getElementById("content").classList.add("hidden");
  }
  timer = setTimeout(poll, delay);
}

const GAUGE_LEN = 2 * Math.PI * 19;

// Iconos de rol minimalistas (traza dorada = tu linea).
const ROLE_ICONS = {
  TOP: '<svg width="11" height="11" viewBox="0 0 14 14"><path d="M12 6 V12 H6" stroke="#5a6478" stroke-width="2" fill="none"/><path d="M2 9 V2 H9" stroke="#c8aa6e" stroke-width="2.6" fill="none"/></svg>',
  MIDDLE: '<svg width="11" height="11" viewBox="0 0 14 14"><path d="M2 5 V2 H5" stroke="#5a6478" stroke-width="2" fill="none"/><path d="M12 9 V12 H9" stroke="#5a6478" stroke-width="2" fill="none"/><path d="M2 12 L12 2" stroke="#c8aa6e" stroke-width="2.6" fill="none"/></svg>',
  BOTTOM: '<svg width="11" height="11" viewBox="0 0 14 14"><path d="M2 9 V2 H9" stroke="#5a6478" stroke-width="2" fill="none"/><path d="M12 6 V12 H6" stroke="#c8aa6e" stroke-width="2.6" fill="none"/></svg>',
  JUNGLE: '<svg width="11" height="11" viewBox="0 0 14 14"><path d="M7 1 C4 4.5 4 9 7 13 C10 9 10 4.5 7 1 Z" fill="#c8aa6e"/><line x1="7" y1="5" x2="7" y2="12" stroke="#0a1428" stroke-width="1.2"/></svg>',
  UTILITY: '<svg width="11" height="11" viewBox="0 0 14 14"><path d="M7 1 L12 3 V7.5 C12 10.5 9.5 12.4 7 13 C4.5 12.4 2 10.5 2 7.5 V3 Z" fill="#c8aa6e"/></svg>',
};

const COIN = '<svg width="12" height="12" viewBox="0 0 12 12"><circle cx="6" cy="6" r="5" fill="#f0b232" stroke="#8a6a1e"/><circle cx="6" cy="6" r="2.6" fill="none" stroke="#8a6a1e"/></svg>';

// Destino de la flecha en el minimapa segun el texto de la accion.
// El orden importa: "rio superior" debe ganarle a "superior" (top).
const ROUTE_TARGETS = [
  [/rio superior|baron|herald|grub|larva/, [31, 24]],
  [/rio inferior|dragon|alma/, [55, 61]],
  [/\btop\b|superior/, [13, 13]],
  [/\bbot\b|inferior/, [71, 71]],
  [/\bmid\b|medio/, [42, 42]],
  [/\bbase\b|tienda/, [13, 71]],
];

function normalize(text) {
  return (text || "").normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").toLowerCase();
}

function drawRoute(actionText) {
  const route = document.getElementById("route");
  const text = normalize(actionText);
  let target = null;
  for (const [re, xy] of ROUTE_TARGETS) {
    if (re.test(text)) { target = xy; break; }
  }
  if (!target) { route.setAttribute("d", ""); return; }
  const sx = 46, sy = 50, tx = target[0], ty = target[1];
  const dx = tx - sx, dy = ty - sy, len = Math.hypot(dx, dy) || 1;
  const cx = (sx + tx) / 2 - (dy / len) * 12, cy = (sy + ty) / 2 + (dx / len) * 12;
  route.setAttribute("d", "M " + sx + " " + sy + " Q " + cx + " " + cy + " " + tx + " " + ty);
}

function render(d) {
  const msg = document.getElementById("msg");
  const content = document.getElementById("content");
  if (!d.in_game) {
    msg.textContent = d.message || "Sin partida activa.";
    msg.classList.remove("hidden"); content.classList.add("hidden");
    return;
  }
  msg.classList.add("hidden"); content.classList.remove("hidden");

  // Medidor circular de probabilidad.
  const p = (d.probability || {}).value;
  const arc = document.getElementById("gauge-arc");
  const gaugeText = document.getElementById("gauge-text");
  if (p == null) {
    gaugeText.textContent = "--%"; gaugeText.setAttribute("class", "even");
    arc.setAttribute("class", "even"); arc.setAttribute("stroke-dasharray", "0 " + GAUGE_LEN);
  } else {
    const cls = p >= 0.55 ? "up" : (p <= 0.45 ? "down" : "even");
    gaugeText.textContent = Math.round(p * 100) + "%";
    gaugeText.setAttribute("class", cls);
    arc.setAttribute("class", cls);
    arc.setAttribute("stroke-dasharray", (p * GAUGE_LEN) + " " + GAUGE_LEN);
  }

  // Retrato + icono de rol + nombre.
  const img = document.getElementById("champ-img");
  if (d.champion_image_url) {
    img.src = d.champion_image_url; img.style.display = "block";
    img.onerror = () => { img.style.display = "none"; };
  } else { img.style.display = "none"; }
  const roleIcon = document.getElementById("role-icon");
  if (ROLE_ICONS[d.role]) {
    roleIcon.innerHTML = ROLE_ICONS[d.role]; roleIcon.style.display = "flex";
  } else { roleIcon.style.display = "none"; }
  document.getElementById("champ-name").textContent = d.champion || "";
  document.getElementById("clock").textContent = fmtClock(d.game_time_seconds);

  // Pildoras: confianza del modelo + estado de la partida.
  const conf = (d.probability || {}).confidence;
  const pillConf = document.getElementById("pill-conf");
  if (conf) {
    pillConf.textContent = "Confianza " + conf;
    pillConf.className = "pill conf-" + conf;
  } else { pillConf.className = "pill hidden"; }
  const pillState = document.getElementById("pill-state");
  const stateLabel = (d.gold_diff && d.gold_diff.label) || null;
  if (stateLabel) {
    pillState.textContent = stateLabel; pillState.className = "pill neutral";
  } else { pillState.className = "pill neutral hidden"; }

  // Accion en turno + ruta sugerida en el minimapa.
  const a = d.next_action || {};
  const urgent = a.urgency === "ahora";
  const actionEl = document.getElementById("action");
  actionEl.textContent = a.title || "";
  actionEl.className = urgent ? "urgent" : "";
  document.getElementById("action-box").className = urgent ? "urgent" : "";
  const extras = [a.detail].concat(a.reasons || []).filter(Boolean);
  document.getElementById("action-detail").textContent = extras.join(" | ");
  drawRoute([a.title, a.detail].concat(a.reasons || []).filter(Boolean).join(" "));

  // Compras: icono + nombre/stats + oro faltante resaltado.
  const items = d.item_priorities || (d.item_priority ? [d.item_priority] : []);
  const list = document.getElementById("item-list");
  list.innerHTML = "";
  if (!items.length) {
    list.textContent = "Sin datos suficientes";
    return;
  }
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "item-row";
    if (item.image_url) {
      const icon = document.createElement("img");
      icon.src = item.image_url;
      icon.onerror = () => icon.remove();
      row.appendChild(icon);
    }
    const main = document.createElement("div");
    main.className = "item-main";
    const name = document.createElement("div");
    name.className = "item-name";
    name.textContent = item.name || "?";
    main.appendChild(name);
    if (item.stat_summary) {
      const stats = document.createElement("div");
      stats.className = "item-stats";
      stats.textContent = item.stat_summary;
      main.appendChild(stats);
    }
    row.appendChild(main);
    const goldEl = document.createElement("div");
    const remaining = item.remaining_gold;
    if (remaining === 0) {
      goldEl.className = "item-gold ready";
      goldEl.innerHTML = "<span>te alcanza</span>" + COIN;
    } else if (remaining != null) {
      goldEl.className = "item-gold";
      goldEl.innerHTML = "<span>faltan " + remaining + " G</span>" + COIN;
    } else if (item.gold_note) {
      goldEl.className = "item-gold";
      goldEl.innerHTML = "<span>" + item.gold_note + "</span>";
    }
    if (goldEl.className) row.appendChild(goldEl);
    list.appendChild(row);
  });
}
poll();
</script>
</body>
</html>"""


@router.get("/overlay/page", response_class=HTMLResponse, include_in_schema=False)
def overlay_page() -> str:
    return OVERLAY_HTML

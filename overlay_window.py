"""Ventana overlay always-on-top para el coaching de momento.

Ventana ligera (tkinter, sin dependencias nuevas) que flota sobre el juego
y muestra exactamente 3 cosas: probabilidad actual, proxima accion
recomendada y compra prioritaria. Consume /api/overlay/state del backend
local; no habla con Riot directamente.

Uso:
  python overlay_window.py                    # servidor ya corriendo en :8000
  python main_orchestrator.py --mode overlay  # levanta servidor + overlay

Notas:
- LoL debe correr en "Pantalla completa (sin bordes)" o "Ventana" para que
  el overlay sea visible; en pantalla completa exclusiva Windows lo tapa.
- Arrastra con el mouse para moverla; doble clic la compacta; se cierra
  SOLO con la X de la esquina superior derecha (un clic accidental durante
  la partida no debe matar el overlay).
"""
from __future__ import annotations

import argparse
import json
import threading
import tkinter as tk
import urllib.request

# HUD estilo League of Legends: azul noche metalico, texto marfil y
# acento dorado; funcionales en los tonos clasicos del cliente.
BG = "#0a1428"
FG = "#f0e6d2"
MUTED = "#a09b8c"
FAINT = "#5a6478"
ACCENT = "#c8aa6e"
GREEN = "#0ac8b9"
RED = "#e84057"
YELLOW = "#f0b232"
ORANGE = "#ff8936"
# Panel de la accion en turno: es lo que mas debe resaltar del overlay.
ACTION_BG = "#152238"

POLL_FALLBACK_MS = 15_000
WIDTH = 380

# Handle del mutex: debe vivir lo que viva el proceso.
_INSTANCE_MUTEX = None


def acquire_single_instance() -> bool:
    """Garantiza UNA sola ventana de overlay por sesion (mutex de Windows).

    Dos instancias simultaneas se apilan en la misma posicion y se ven
    como un overlay "doble". Devuelve False si ya hay una abierta.
    """
    global _INSTANCE_MUTEX
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        _INSTANCE_MUTEX = kernel32.CreateMutexW(None, False, "Local\\LoLRecommenderOverlay")
        ERROR_ALREADY_EXISTS = 183
        return kernel32.GetLastError() != ERROR_ALREADY_EXISTS
    except Exception:
        return True  # sin win32 no se puede verificar: no bloquear


class OverlayWindow:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.compact = False
        self._latest: dict | None = None
        self._poll_ms = POLL_FALLBACK_MS

        self.root = tk.Tk()
        self.root.title("LoL Coach")
        self.root.overrideredirect(True)          # sin bordes ni barra
        self.root.attributes("-topmost", True)    # siempre encima
        self.root.attributes("-alpha", 0.93)
        self.root.configure(bg=BG)
        self.root.geometry(f"{WIDTH}x150+60+60")

        self.card = tk.Frame(self.root, bg=BG, padx=12, pady=8)
        self.card.pack(fill="both", expand=True)

        top = tk.Frame(self.card, bg=BG)
        top.pack(fill="x")
        self.prob_label = tk.Label(
            top, text="--%", font=("Segoe UI", 20, "bold"), fg=YELLOW, bg=BG
        )
        self.prob_label.pack(side="left")
        # Unico gesto de cierre: la X. El clic derecho cerraba demasiado
        # facil en medio de una partida.
        self.close_label = tk.Label(
            top, text="✕", font=("Segoe UI", 10, "bold"), fg=MUTED, bg=BG,
            cursor="hand2", padx=4,
        )
        self.close_label.pack(side="right")
        self.close_label.bind("<Button-1>", lambda _e: self.root.destroy())
        self.close_label.bind("<Enter>", lambda _e: self.close_label.config(fg=RED))
        self.close_label.bind("<Leave>", lambda _e: self.close_label.config(fg=MUTED))
        self.meta_label = tk.Label(
            top, text="conectando...", font=("Segoe UI", 8), fg=MUTED, bg=BG,
            anchor="e", justify="right",
        )
        self.meta_label.pack(side="right", padx=(8, 4))

        # La accion en turno vive en un panel resaltado y con la fuente mas
        # grande de la tarjeta; las compras quedan como bloque secundario.
        self.action_frame = tk.Frame(self.card, bg=ACTION_BG, padx=9, pady=6)
        self.action_tag = tk.Label(
            self.action_frame, text="SIGUIENTE ACCION", font=("Segoe UI", 7, "bold"),
            fg=ACCENT, bg=ACTION_BG, anchor="w",
        )
        self.action_label = tk.Label(
            self.action_frame, text="", font=("Segoe UI", 13, "bold"), fg=FG,
            bg=ACTION_BG, anchor="w", justify="left", wraplength=WIDTH - 48,
        )
        self.action_detail = tk.Label(
            self.action_frame, text="", font=("Segoe UI", 8), fg=MUTED,
            bg=ACTION_BG, anchor="w", justify="left", wraplength=WIDTH - 48,
        )
        for widget in (self.action_tag, self.action_label, self.action_detail):
            widget.pack(fill="x")
        self.item_tag = tk.Label(
            self.card, text="COMPRAS AL VOLVER A BASE", font=("Segoe UI", 7, "bold"),
            fg=FAINT, bg=BG, anchor="w",
        )
        self.item_label = tk.Label(
            self.card, text="", font=("Segoe UI", 8), fg=MUTED, bg=BG,
            anchor="w", justify="left", wraplength=WIDTH - 30,
        )
        self.action_frame.pack(fill="x", pady=(6, 4))
        self.item_tag.pack(fill="x")
        self.item_label.pack(fill="x")

        # Mover con arrastre; doble clic compacta. Cerrar: solo la X.
        for widget in (self.root, self.card, self.prob_label,
                       self.action_frame, self.action_label):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<Double-Button-1>", self._toggle_compact)

        self._drag_origin = (0, 0)
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self.root.after(500, self._render_latest)
        # El juego se re-impone en el z-order al recibir foco/clics; el
        # -topmost de tkinter solo se aplica una vez. Se reafirma via win32
        # de forma periodica, sin robarle el foco al juego.
        self.root.after(200, self._enforce_topmost)

    # ----------------------------------------------------------- topmost

    def _enforce_topmost(self) -> None:
        """Reafirma HWND_TOPMOST cada 2 s (win32) para flotar sobre el juego.

        Ademas marca la ventana como TOOLWINDOW (no aparece en Alt+Tab) y
        NOACTIVATE (clic en el overlay no le quita el foco al juego). Si
        ctypes/win32 no esta disponible, cae al -topmost de tkinter.
        """
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()

            GWL_EXSTYLE = -20
            WS_EX_TOPMOST = 0x0008
            WS_EX_TOOLWINDOW = 0x0080
            WS_EX_NOACTIVATE = 0x08000000
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            wanted = style | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            if wanted != style:
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, wanted)

            HWND_TOPMOST = -1
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
            user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
            )
        except Exception:
            self.root.attributes("-topmost", True)
            self.root.lift()
        self.root.after(2000, self._enforce_topmost)

    # -------------------------------------------------------------- red

    def _fetch_state(self) -> dict | None:
        try:
            with urllib.request.urlopen(
                f"{self.base_url}/api/overlay/state", timeout=15
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            return None

    def _poll_loop(self) -> None:
        import time
        while True:
            state = self._fetch_state()
            if state is None:
                # No pisar el ultimo estado bueno por un fallo puntual;
                # reintentar pronto y avisar solo si el fallo persiste.
                if self._latest is None or self._latest.get("error"):
                    self._latest = {"error": True}
                else:
                    self._latest = {**self._latest, "stale": True}
                time.sleep(5)
                continue
            self._latest = state
            refresh = state.get("refresh_seconds") or 15
            self._poll_ms = max(5, int(refresh)) * 1000
            time.sleep(self._poll_ms / 1000)

    # ------------------------------------------------------------ render

    def _render_latest(self) -> None:
        state = self._latest
        if state is not None:
            self._render(state)
        self.root.after(1000, self._render_latest)

    def _render(self, d: dict) -> None:
        if d.get("error"):
            self.prob_label.config(text="--%", fg=MUTED)
            self.meta_label.config(text="servidor no disponible")
            self.action_label.config(text="Abre la app: --mode app u --mode overlay")
            self.action_detail.config(text="")
            self.item_label.config(text="")
            return
        if not d.get("in_game"):
            self.prob_label.config(text="--%", fg=MUTED)
            self.meta_label.config(
                text="preparando datos..." if d.get("warming") else "sin partida activa"
            )
            self.action_label.config(
                text=d.get("message")
                or "Esperando partida (el mapa debe haber cargado)..."
            )
            self.action_detail.config(text="")
            self.item_label.config(text="")
            self._resize()
            return

        prob = (d.get("probability") or {}).get("value")
        if prob is None:
            self.prob_label.config(text="--%", fg=MUTED)
        else:
            color = GREEN if prob >= 0.55 else (RED if prob <= 0.45 else YELLOW)
            self.prob_label.config(text=f"{prob * 100:.0f}%", fg=color)

        bits = []
        if d.get("role_label"):
            bits.append(d["role_label"])
        if d.get("champion"):
            bits.append(d["champion"])
        seconds = d.get("game_time_seconds")
        if seconds is not None:
            bits.append(f"{int(seconds // 60)}:{int(seconds % 60):02d}")
        confidence = (d.get("probability") or {}).get("confidence")
        if confidence:
            bits.append(f"conf. {confidence}")
        gold = d.get("gold_diff")
        if gold and gold.get("label"):
            bits.append(gold["label"])
        if d.get("stale"):
            bits.append("actualizando...")
        self.meta_label.config(text=" - ".join(bits))

        action = d.get("next_action") or {}
        urgent = action.get("urgency") == "ahora"
        panel_bg = "#31202c" if urgent else ACTION_BG
        self.action_frame.config(bg=panel_bg)
        self.action_tag.config(bg=panel_bg, fg=ORANGE if urgent else ACCENT)
        self.action_label.config(
            text=action.get("title") or "", bg=panel_bg,
            fg=ORANGE if urgent else FG,
        )
        extras = [action.get("detail")] + list(action.get("reasons") or [])
        self.action_detail.config(
            text=" | ".join(x for x in extras if x), bg=panel_bg
        )

        items = d.get("item_priorities")
        if items is None and d.get("item_priority"):
            items = [d["item_priority"]]
        lines = []
        for index, item in enumerate(items or [], start=1):
            line = f"{index}. {item.get('name') or '?'}"
            if item.get("stat_summary"):
                line += f"  ({item['stat_summary']})"
            if item.get("gold_note"):
                line += f" — {item['gold_note']}"
            lines.append(line)
        self.item_label.config(text="\n".join(lines) or "Sin datos suficientes")

        self._resize()

    def _resize(self) -> None:
        self.root.update_idletasks()
        height = self.card.winfo_reqheight() + 4
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.geometry(f"{WIDTH}x{height}+{x}+{y}")

    # ----------------------------------------------------------- ventana

    def _drag_start(self, event) -> None:
        self._drag_origin = (event.x_root - self.root.winfo_x(),
                             event.y_root - self.root.winfo_y())

    def _drag_move(self, event) -> None:
        dx, dy = self._drag_origin
        self.root.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _toggle_compact(self, _event) -> None:
        self.compact = not self.compact
        widgets = (self.action_frame, self.item_tag, self.item_label)
        if self.compact:
            for widget in widgets:
                widget.pack_forget()
        else:
            self.action_frame.pack(fill="x", pady=(6, 4))
            self.item_tag.pack(fill="x")
            self.item_label.pack(fill="x")
        self._resize()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Overlay in-game de LoL Recommender")
    parser.add_argument(
        "--url", default="http://127.0.0.1:8000",
        help="URL del backend local (default: http://127.0.0.1:8000)",
    )
    args = parser.parse_args()
    if not acquire_single_instance():
        print("Ya hay un overlay abierto: no se abre otro.")
        return 0
    OverlayWindow(args.url).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

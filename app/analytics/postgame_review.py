"""Revision post-partida automatica: "¿donde se perdio la partida?".

Con las timelines de Match-V5 ya almacenadas (tabla timeline_minutes) se
reconstruye la curva de probabilidad de victoria de cada partida JUGADA
usando el mismo modelo in-game y la misma mezcla con el ancla del 50% que
la inferencia en vivo (paridad total: la curva post-partida es exactamente
lo que la app habria mostrado minuto a minuto).

Sobre esa curva se detectan los 2-3 puntos de inflexion mas grandes
("al minuto 23 tenias 71% y cayo a 38% tras una pelea desfavorable y
perder el Baron"), se genera un resumen de 5 lineas por partida y, sobre
las ultimas N partidas, patrones agregados ("pierdes 18 pp mas cuando la
partida pasa del minuto 32: cierra antes").

Si la partida propia aun no tiene timeline en la DB, se descarga bajo
demanda (1 llamada Match-V5 /timeline) y queda persistida para siempre.
Regla dura del proyecto: si el dato no existe, se dice explicitamente.
"""
from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

from app.ml.inference import (
    LIVE_WEIGHT_FULL_AT_SECONDS,
    MAX_LIVE_WEIGHT,
    PREGAME_PROBABILITY,
)

logger = logging.getLogger(__name__)

# Ventana (en minutos) sobre la que se mide cada swing de probabilidad y
# umbral minimo (en puntos de probabilidad) para considerarlo inflexion.
SWING_WINDOW_MINUTES = 3
MIN_SWING = 0.10
MAX_TURNING_POINTS = 3

# Umbrales de lectura de la curva para el resumen y los patrones.
THROW_THRESHOLD = 0.65      # llego a >=65% y perdio -> throw
COMEBACK_THRESHOLD = 0.35   # estuvo <=35% y gano -> comeback
EARLY_MINUTE = 14           # fin de fase de lineas para el diagnostico early
AHEAD_AT_MINUTE = 15        # corte "ibas delante/detras al 15"
LONG_GAME_MINUTES = 32      # partida "larga" para el patron de cierre

# Pesos del heuristico explicable (mismos que ml/baseline.py) usados como
# fallback cuando no hay modelo in-game entrenado para el campeon/liga.
_HEURISTIC_WEIGHTS = {
    "kill_diff": 0.05,
    "level_diff": 0.03,
    "cs_diff": 0.04 / 50.0,
    "turret_diff": 0.12,
    "dragon_diff": 0.10,
    "baron_diff": 0.25,
}

DIFF_COLUMNS = ["kill_diff", "level_diff", "cs_diff", "turret_diff", "dragon_diff", "baron_diff"]


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _live_weight(minute: float) -> float:
    """Mismo peso del modelo in-game que usa la inferencia en vivo."""
    seconds = minute * 60.0
    return min(MAX_LIVE_WEIGHT, (seconds / LIVE_WEIGHT_FULL_AT_SECONDS) * MAX_LIVE_WEIGHT)


def perspective_frame(match_timeline: pd.DataFrame, player_team_id: int) -> pd.DataFrame:
    """Diffs por minuto desde la perspectiva del equipo del jugador."""
    if match_timeline.empty:
        return pd.DataFrame()
    sign = 1 if int(player_team_id) == 100 else -1
    df = match_timeline.sort_values("minute").drop_duplicates("minute")
    return pd.DataFrame({
        "minute": df["minute"].astype(int),
        "kill_diff": sign * (df["kills_100"] - df["kills_200"]),
        "level_diff": sign * (df["level_100"] - df["level_200"]),
        "cs_diff": sign * (df["cs_100"] - df["cs_200"]),
        "turret_diff": sign * (df["turrets_100"] - df["turrets_200"]),
        "dragon_diff": sign * (df["dragons_100"] - df["dragons_200"]),
        "baron_diff": sign * (df["barons_100"] - df["barons_200"]),
    }).reset_index(drop=True)


def probability_curve(
    frame: pd.DataFrame, model_payload: dict | None
) -> tuple[pd.DataFrame, str]:
    """Curva P(victoria) por minuto con la MISMA mezcla que la inferencia
    en vivo: P = (1-w)*0.50 + w*P_in_game, con w creciendo hasta 90%.

    Devuelve (frame + columna probability, metodo usado)."""
    if frame.empty:
        return frame, "sin_datos"
    out = frame.copy()
    p_live = None
    method = "heuristico_baseline"
    if model_payload and isinstance(model_payload, dict) and "model" in model_payload:
        try:
            from app.ml.train_live_model import LIVE_FEATURE_COLUMNS, MAX_MINUTE

            X = out[["minute"] + DIFF_COLUMNS].copy()
            X["minute"] = X["minute"].clip(upper=MAX_MINUTE)
            columns = model_payload.get("feature_columns") or LIVE_FEATURE_COLUMNS
            X = X[columns].astype(float).fillna(0.0)
            p_live = model_payload["model"].predict_proba(X)[:, 1]
            method = f"modelo_live_{model_payload.get('algorithm', 'desconocido')}"
        except Exception as exc:
            logger.warning("Modelo in-game no utilizable para la curva: %s", exc)
            p_live = None
    if p_live is None:
        scores = sum(out[col] * weight for col, weight in _HEURISTIC_WEIGHTS.items())
        p_live = scores.map(_sigmoid).to_numpy()

    weights = out["minute"].map(_live_weight).to_numpy()
    out["probability"] = (
        (1.0 - weights) * PREGAME_PROBABILITY + weights * p_live
    ).round(3)
    return out, method


# ------------------------------------------------------- puntos de inflexion


def _swing_causes(curve: pd.DataFrame, start_idx: int, end_idx: int, gained: bool) -> list[str]:
    """Que cambio en el marcador dentro de la ventana del swing."""
    start, end = curve.iloc[start_idx], curve.iloc[end_idx]
    causes: list[str] = []
    kills = int(end["kill_diff"] - start["kill_diff"])
    if abs(kills) >= 2:
        causes.append(
            f"pelea {'favorable' if kills > 0 else 'desfavorable'} ({kills:+d} kills)"
        )
    turrets = int(end["turret_diff"] - start["turret_diff"])
    if abs(turrets) >= 1:
        causes.append(
            f"{'+' if turrets > 0 else '-'}{abs(turrets)} torre(s) "
            f"{'a favor' if turrets > 0 else 'en contra'}"
        )
    dragons = int(end["dragon_diff"] - start["dragon_diff"])
    if abs(dragons) >= 1:
        causes.append(f"dragon {'ganado' if dragons > 0 else 'cedido'}")
    barons = int(end["baron_diff"] - start["baron_diff"])
    if abs(barons) >= 1:
        causes.append(f"Baron {'ganado' if barons > 0 else 'cedido'}")
    cs = int(end["cs_diff"] - start["cs_diff"])
    if not causes and abs(cs) >= 30:
        causes.append(f"diferencia de farmeo ({cs:+d} CS)")
    if not causes:
        causes.append("acumulacion gradual de ventajas" if gained else "perdida gradual de mapa")
    return causes


def detect_turning_points(
    curve: pd.DataFrame,
    max_points: int = MAX_TURNING_POINTS,
    min_swing: float = MIN_SWING,
    window: int = SWING_WINDOW_MINUTES,
) -> list[dict[str, Any]]:
    """Los 2-3 swings mas grandes de la curva, no solapados, explicados."""
    if curve.empty or len(curve) < window + 1:
        return []
    probs = curve["probability"].to_numpy()
    minutes = curve["minute"].to_numpy()
    candidates = []
    for i in range(len(probs) - window):
        j = i + window
        swing = float(probs[j] - probs[i])
        if abs(swing) >= min_swing:
            candidates.append((abs(swing), i, j, swing))
    candidates.sort(reverse=True)

    picked: list[tuple[int, int, float]] = []
    for _, i, j, swing in candidates:
        if any(not (j <= pi or i >= pj) for pi, pj, _ in picked):
            continue  # solapa con un swing ya elegido
        picked.append((i, j, swing))
        if len(picked) >= max_points:
            break
    picked.sort(key=lambda t: t[0])  # orden cronologico

    points = []
    for i, j, swing in picked:
        gained = swing > 0
        from_p, to_p = float(probs[i]), float(probs[j])
        causes = _swing_causes(curve, i, j, gained)
        description = (
            f"Al minuto {int(minutes[i])} tenias {from_p:.0%} y "
            f"{'subio' if gained else 'cayo'} a {to_p:.0%} al minuto {int(minutes[j])} "
            f"tras {', '.join(causes)}."
        )
        points.append({
            "minute": int(minutes[i]),
            "end_minute": int(minutes[j]),
            "from_probability": round(from_p, 3),
            "to_probability": round(to_p, 3),
            "swing": round(swing, 3),
            "direction": "subida" if gained else "caida",
            "causes": causes,
            "description": description,
        })
    return points


# ------------------------------------------------------------ resumen 5 lineas


def _curve_stats(curve: pd.DataFrame) -> dict[str, Any]:
    probs = curve["probability"]
    minutes = curve["minute"]
    early = curve[(minutes >= 10) & (minutes <= EARLY_MINUTE)]
    at_15 = curve[minutes >= AHEAD_AT_MINUTE]
    return {
        "peak": float(probs.max()),
        "peak_minute": int(minutes.iloc[int(probs.to_numpy().argmax())]) if len(probs) else 0,
        "low": float(probs.min()),
        "final": float(probs.iloc[-1]),
        "early_avg": float(early["probability"].mean()) if not early.empty else None,
        "p_at_15": float(at_15["probability"].iloc[0]) if not at_15.empty else None,
        "last_minute": int(minutes.iloc[-1]),
    }


def build_summary(
    match: dict[str, Any],
    curve: pd.DataFrame,
    turning_points: list[dict[str, Any]],
) -> list[str]:
    """Resumen de exactamente 5 lineas, en espanol, con datos reales."""
    win = bool(match.get("win"))
    stats = _curve_stats(curve)
    duration = match.get("duration_min")
    lines = []

    # 1. Resultado y contexto.
    rival = f" vs {match['opponent_champion']}" if match.get("opponent_champion") else ""
    lines.append(
        f"{'Victoria' if win else 'Derrota'} con {match.get('champion', '?')}{rival} "
        f"en {duration:.0f} min ({match.get('kda_text', '?')} KDA)."
        if duration else
        f"{'Victoria' if win else 'Derrota'} con {match.get('champion', '?')}{rival} "
        f"({match.get('kda_text', '?')} KDA)."
    )

    # 2. Fase de lineas.
    early = stats["early_avg"]
    if early is None:
        lines.append("Partida demasiado corta para evaluar la fase de lineas por separado.")
    elif early >= 0.57:
        lines.append(f"Fase de lineas ganada: al minuto {EARLY_MINUTE} promediabas {early:.0%} de probabilidad.")
    elif early <= 0.43:
        lines.append(f"Fase de lineas perdida: al minuto {EARLY_MINUTE} promediabas {early:.0%} de probabilidad.")
    else:
        lines.append(f"Fase de lineas pareja (~{early:.0%} al minuto {EARLY_MINUTE}).")

    # 3. Punto de inflexion principal (el swing mas grande).
    if turning_points:
        main = max(turning_points, key=lambda p: abs(p["swing"]))
        lines.append(f"Momento clave: {main['description']}")
    else:
        lines.append("Sin puntos de inflexion bruscos: la partida se decidio de forma gradual.")

    # 4. Lectura del cierre (throw / comeback / consistencia).
    if not win and stats["peak"] >= THROW_THRESHOLD:
        lines.append(
            f"Throw detectado: llegaste a {stats['peak']:.0%} (min {stats['peak_minute']}) "
            f"y terminaste perdiendo."
        )
    elif win and stats["low"] <= COMEBACK_THRESHOLD:
        lines.append(f"Comeback: estuviste en {stats['low']:.0%} y le diste la vuelta.")
    elif win:
        lines.append(f"Cierre solido: terminaste con {stats['final']:.0%} y sin ceder el control.")
    else:
        lines.append(
            f"Nunca recuperaste el control: maximo {stats['peak']:.0%} en toda la partida."
        )

    # 5. Consejo accionable.
    if not win and stats["peak"] >= THROW_THRESHOLD:
        lines.append(
            "Consejo: con >=65% de probabilidad juega simple - no fuerces peleas, "
            "empuja lineas laterales con vision y cierra por objetivos."
        )
    elif not win and early is not None and early <= 0.43:
        lines.append(
            "Consejo: la desventaja vino del early - revisa los primeros recalls y "
            "el control de oleadas antes del minuto 10."
        )
    elif not win:
        drops = [p for p in turning_points if p["direction"] == "caida"]
        if drops:
            lines.append(
                f"Consejo: la mayor caida vino de {drops[0]['causes'][0]} - "
                "evalua si esa pelea/objetivo era necesaria."
            )
        else:
            lines.append("Consejo: partida cerrada; pequenos margenes en farmeo y vision deciden.")
    elif stats["low"] <= COMEBACK_THRESHOLD:
        lines.append("Consejo: buen comeback, pero evita llegar a ese deficit - costo minutos de riesgo alto.")
    else:
        lines.append("Consejo: replica este patron - ventaja temprana convertida sin regalar peleas.")
    return lines[:5]


# ------------------------------------------------------- patrones semanales


def weekly_patterns(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Patrones agregados sobre las revisiones disponibles.

    reviews: lista de dicts con win, duration_min, peak, low, p_at_15.
    Genera insights accionables SOLO cuando la muestra los sostiene."""
    usable = [r for r in reviews if r.get("duration_min") is not None]
    games = len(usable)
    if games == 0:
        return {"available": False, "games": 0, "insights": [],
                "reason": "No hay partidas con curva reconstruida."}
    wins = sum(1 for r in usable if r["win"])

    short = [r for r in usable if r["duration_min"] < LONG_GAME_MINUTES]
    long_ = [r for r in usable if r["duration_min"] >= LONG_GAME_MINUTES]
    throws = [r for r in usable if not r["win"] and r.get("peak", 0) >= THROW_THRESHOLD]
    comebacks = [r for r in usable if r["win"] and r.get("low", 1) <= COMEBACK_THRESHOLD]
    ahead_15 = [r for r in usable if (r.get("p_at_15") or 0.5) >= 0.55]
    behind_15 = [r for r in usable if (r.get("p_at_15") or 0.5) <= 0.45]

    def wr(subset):
        return (sum(1 for r in subset if r["win"]) / len(subset)) if subset else None

    insights: list[str] = []
    wr_short, wr_long = wr(short), wr(long_)
    if wr_short is not None and wr_long is not None and len(short) >= 3 and len(long_) >= 3:
        gap = (wr_short - wr_long) * 100
        if gap >= 8:
            insights.append(
                f"Pierdes {gap:.0f} pp mas cuando la partida pasa del minuto {LONG_GAME_MINUTES} "
                f"({wr_long:.0%} vs {wr_short:.0%} en partidas cortas): prioriza cerrar antes."
            )
        elif gap <= -8:
            insights.append(
                f"Ganas {-gap:.0f} pp mas en partidas largas ({wr_long:.0%} vs {wr_short:.0%}): "
                "tu escalado tardio es un arma - no fuerces el early."
            )
    if throws:
        insights.append(
            f"{len(throws)} de {games - wins} derrota(s) fueron throws (llegaste a >={THROW_THRESHOLD:.0%} "
            "y perdiste): con ventaja grande, juega por objetivos y evita peleas innecesarias."
        )
    if comebacks:
        insights.append(
            f"{len(comebacks)} victoria(s) fueron comebacks desde <={COMEBACK_THRESHOLD:.0%}: "
            "no te rindas en desventaja, tus remontadas son reales."
        )
    wr_ahead, wr_behind = wr(ahead_15), wr(behind_15)
    if wr_ahead is not None and len(ahead_15) >= 3:
        insights.append(
            f"Cuando vas delante al minuto {AHEAD_AT_MINUTE} ganas el {wr_ahead:.0%} "
            f"({len(ahead_15)} partidas)"
            + (f"; cuando vas detras, el {wr_behind:.0%} ({len(behind_15)})." if wr_behind is not None else ".")
        )
    if not insights:
        insights.append(
            f"Muestra de {games} partida(s): aun sin patrones estadisticamente utiles; "
            "juega mas partidas (o descarga mas timelines) para detectarlos."
        )

    return {
        "available": True,
        "games": games,
        "wins": wins,
        "winrate": round(wins / games, 3),
        "throws": len(throws),
        "comebacks": len(comebacks),
        "buckets": [
            {"label": f"< {LONG_GAME_MINUTES} min", "games": len(short),
             "winrate": None if wr_short is None else round(wr_short, 3)},
            {"label": f">= {LONG_GAME_MINUTES} min", "games": len(long_),
             "winrate": None if wr_long is None else round(wr_long, 3)},
        ],
        "ahead_at_15": {"games": len(ahead_15),
                        "winrate": None if wr_ahead is None else round(wr_ahead, 3)},
        "behind_at_15": {"games": len(behind_15),
                         "winrate": None if wr_behind is None else round(wr_behind, 3)},
        "insights": insights,
    }


# --------------------------------------------------------------- servicio


class PostGameReviewService:
    """Orquesta la revision: timeline (DB o descarga), modelo, curva, resumen.

    Las revisiones se cachean en memoria por matchId: la timeline es
    inmutable una vez jugada la partida.
    """

    def __init__(self, repo, settings, champion_registry, riot_client=None, ddragon=None):
        self.repo = repo
        self.settings = settings
        self.champion_registry = champion_registry
        self.riot_client = riot_client
        self.ddragon = ddragon
        self._cache: dict[str, dict] = {}

    # ------------------------------------------------------------- listado

    def recent_matches(self, puuid: str, limit: int = 10) -> list[dict[str, Any]]:
        """Ultimas partidas del jugador con flag de timeline disponible."""
        mine = self._player_rows(puuid)
        if mine.empty:
            return []
        with_tl = self.repo.matches_with_timeline()
        items = []
        for _, row in mine.head(limit).iterrows():
            items.append({
                "match_id": row["matchId"],
                "champion": row["championName"],
                "champion_image_url": (
                    self.ddragon.champion_image_url(row["championName"]) if self.ddragon else None
                ),
                "opponent_champion": row.get("opponentChampionName"),
                "role": row.get("teamPosition"),
                "win": bool(row["win"]),
                "duration_min": self._duration_min(row),
                "game_creation": int(row["gameCreation"]) if row.get("gameCreation") else None,
                "has_timeline": row["matchId"] in with_tl,
                "reviewed": row["matchId"] in self._cache,
            })
        return items

    # ------------------------------------------------------------- revision

    def review(self, match_id: str, puuid: str) -> dict[str, Any]:
        if match_id in self._cache:
            return self._cache[match_id]
        mine = self._player_rows(puuid)
        row = mine[mine["matchId"] == match_id]
        if row.empty:
            return {"available": False,
                    "reason": f"La partida {match_id} no aparece en tu historial local."}
        row = row.iloc[0]

        warnings: list[str] = []
        timeline = self.repo.timeline_minutes_for_match(match_id)
        if timeline.empty:
            timeline, download_error = self._download_timeline(match_id)
            if timeline.empty:
                return {"available": False, "match_id": match_id, "reason": download_error}
            warnings.append("Timeline descargada bajo demanda de Match-V5 y persistida en la DB.")

        champion = row["championName"]
        frame = perspective_frame(timeline, int(row["teamId"]))
        model_payload, model_note = self._load_live_model(champion)
        if model_note:
            warnings.append(model_note)
        curve, method = probability_curve(frame, model_payload)
        turning_points = detect_turning_points(curve)

        match_info = {
            "champion": champion,
            "opponent_champion": row.get("opponentChampionName"),
            "win": bool(row["win"]),
            "duration_min": self._duration_min(row),
            "kda_text": f"{int(row['kills'] or 0)}/{int(row['deaths'] or 0)}/{int(row['assists'] or 0)}",
        }
        summary = build_summary(match_info, curve, turning_points)
        stats = _curve_stats(curve)

        result = {
            "available": True,
            "match_id": match_id,
            "champion": champion,
            "champion_image_url": (
                self.ddragon.champion_image_url(champion) if self.ddragon else None
            ),
            "opponent_champion": match_info["opponent_champion"],
            "role": row.get("teamPosition"),
            "win": match_info["win"],
            "duration_min": match_info["duration_min"],
            "kda": match_info["kda_text"],
            "game_creation": int(row["gameCreation"]) if row.get("gameCreation") else None,
            "method": method,
            "curve": [
                {"minute": int(r.minute), "probability": float(r.probability)}
                for r in curve.itertuples()
            ],
            "turning_points": turning_points,
            "summary_lines": summary,
            "stats": {k: (round(v, 3) if isinstance(v, float) else v)
                      for k, v in stats.items() if v is not None},
            "warnings": warnings,
            "data_source": "historico",
        }
        self._cache[match_id] = result
        return result

    def review_last(self, puuid: str) -> dict[str, Any]:
        matches = self.recent_matches(puuid, limit=1)
        if not matches:
            return {"available": False,
                    "reason": "No hay partidas tuyas en el historial local; corre --mode ingest-history."}
        return self.review(matches[0]["match_id"], puuid)

    # ------------------------------------------------------------- patrones

    def patterns(self, puuid: str, limit: int = 20) -> dict[str, Any]:
        """Patrones sobre las ultimas `limit` partidas (descarga las
        timelines que falten, acotado por `limit`)."""
        matches = self.recent_matches(puuid, limit=limit)
        if not matches:
            return {"available": False, "games": 0, "insights": [],
                    "reason": "No hay partidas tuyas en el historial local."}
        reviews, skipped = [], 0
        for item in matches:
            review = self.review(item["match_id"], puuid)
            if not review.get("available"):
                skipped += 1
                continue
            stats = review.get("stats", {})
            reviews.append({
                "win": review["win"],
                "duration_min": review["duration_min"],
                "peak": stats.get("peak"),
                "low": stats.get("low"),
                "p_at_15": stats.get("p_at_15"),
            })
        result = weekly_patterns(reviews)
        result["requested"] = len(matches)
        result["skipped_without_timeline"] = skipped
        if skipped:
            result.setdefault("warnings", []).append(
                f"{skipped} partida(s) sin timeline disponible quedaron fuera del analisis"
                + ("." if self.riot_client and self._has_key() else
                   " (configura RIOT_API_KEY para descargarlas).")
            )
        return result

    # -------------------------------------------------------------- helpers

    def _player_rows(self, puuid: str) -> pd.DataFrame:
        from app.analytics.matchup_analysis import with_opponents

        participants = self.repo.participants_df()
        if participants.empty:
            return participants
        full = with_opponents(participants)
        return (
            full[full["puuid"] == puuid]
            .sort_values("gameCreation", ascending=False)
            .drop_duplicates("matchId")
        )

    @staticmethod
    def _duration_min(row) -> float | None:
        try:
            duration = float(row.get("gameDuration") or 0)
            return round(duration / 60, 1) if duration > 0 else None
        except (TypeError, ValueError):
            return None

    def _has_key(self) -> bool:
        from app.security import secrets

        return secrets.has_riot_api_key()

    def _download_timeline(self, match_id: str) -> tuple[pd.DataFrame, str]:
        """Descarga y persiste la timeline de una partida propia (1 llamada)."""
        if self.riot_client is None or not self._has_key():
            return pd.DataFrame(), (
                "Esta partida no tiene timeline en la DB y no hay RIOT_API_KEY "
                "para descargarla. Configura la key en .env."
            )
        from app.pipelines.ingest_timelines import timeline_to_minute_rows
        from app.riot.riot_client import RiotApiError

        try:
            raw = self.riot_client.get_match_timeline(match_id)
        except RiotApiError as exc:
            return pd.DataFrame(), f"Riot API no devolvio la timeline: {exc}"
        rows = timeline_to_minute_rows(match_id, raw) if raw else []
        if not rows:
            return pd.DataFrame(), "La timeline llego vacia desde Match-V5."
        self.repo.upsert_timeline_minutes(pd.DataFrame(rows))
        return self.repo.timeline_minutes_for_match(match_id), ""

    def _load_live_model(self, champion: str) -> tuple[dict | None, str | None]:
        """Modelo in-game del campeon; si no existe, el del campeon
        configurado (las features son diffs de equipo: transferibles);
        si tampoco, heuristico con aviso."""
        if self.champion_registry is None or not self.settings.target_tier:
            return None, "Sin registry de modelos: curva con heuristico explicable."
        tier = self.settings.target_tier
        loaded = self.champion_registry.latest("live", champion, tier)
        if loaded is not None:
            payload, _ = loaded
            if isinstance(payload, dict) and "model" in payload:
                return payload, None
        fallback_champion = self.settings.champion
        if fallback_champion and fallback_champion != champion:
            loaded = self.champion_registry.latest("live", fallback_champion, tier)
            if loaded is not None:
                payload, _ = loaded
                if isinstance(payload, dict) and "model" in payload:
                    return payload, (
                        f"Sin modelo in-game de {champion}: se usa el de "
                        f"{fallback_champion} (features de equipo, transferibles)."
                    )
        return None, (
            f"Sin modelo in-game entrenado para {champion} ({tier}): curva con "
            "heuristico explicable. Ejecuta --mode train-champion para mejorarla."
        )

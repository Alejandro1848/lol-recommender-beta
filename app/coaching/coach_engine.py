"""Coach de momento: decide UNA accion y UNA compra para el overlay.

Entradas (todas ya calculadas por el resto de la app):
- snapshot en vivo normalizado (ingest_live),
- recomendaciones completas del RecommendationEngine (win prob del modelo
  in-game calibrado, items, ganks, objetivos),
- desempeno por jugador (team_performance) y timers (objective_timers).

Salida: payload compacto para el overlay. La probabilidad NO se altera
aqui: viene del modelo in-game calibrado. El desempeno de aliados y
enemigos decide QUE accion se recomienda con esa probabilidad, no el
numero en si.
"""
from __future__ import annotations

from typing import Any

from app.coaching.objective_timers import next_objective, objective_timers
from app.recommendations import item_recommender
from app.coaching.team_performance import (
    estimate_gold_diff,
    team_performance_summary,
)
from app.data.normalizers import ROLE_LABELS_ES

AHEAD_THRESHOLD = 0.55
BEHIND_THRESHOLD = 0.45

# Donde vive cada objetivo (para frasear la preparacion de vision).
OBJECTIVE_ZONE = {
    "dragon": "rio inferior",
    "baron": "rio superior",
    "herald": "rio superior",
    "grubs": "rio superior",
}


class CoachEngine:
    """Sin estado: cada build() es una foto del momento."""

    def build(
        self,
        snapshot: dict | None,
        recommendations: dict | None,
        snapshot_age_seconds: float | None = None,
    ) -> dict[str, Any]:
        recommendations = recommendations or {}
        if snapshot is None:
            return {
                "in_game": False,
                "message": (
                    "Sin partida activa. El overlay se activa solo cuando el "
                    "juego corre en esta maquina."
                ),
            }

        me = snapshot.get("me") or {}
        role = me.get("position")
        # El snapshot viene de un cache (REFRESH_SECONDS): extrapolar el
        # reloj evita timers vencidos ("aparece en 45s" cuando ya salio).
        # Los kills de objetivos posteriores al cache no son extrapolables:
        # por eso el fraseo de objetivos activos pide confirmar en el mapa.
        game_time = snapshot.get("game_time_seconds")
        if (
            game_time is not None
            and snapshot_age_seconds
            and not snapshot.get("simulated")
        ):
            game_time += min(float(snapshot_age_seconds), 60.0)
        minute = (game_time or 0) / 60.0
        live_signals = bool(snapshot.get("live_signals_available", True))

        timers = objective_timers(
            game_time,
            snapshot.get("objective_events"),
            dragons_killed_total=self._total_dragons(snapshot),
        )
        performance = team_performance_summary(snapshot) if live_signals else None
        gold = estimate_gold_diff(snapshot) if live_signals else None
        win_probability = recommendations.get("win_probability") or {}

        action = self._next_action(
            snapshot, role, minute, timers, performance, gold,
            win_probability, recommendations,
        )
        items = self._item_priorities(recommendations)

        warnings = []
        if not live_signals:
            warnings.append(
                "Sin senales del Live Client (kills/oro no visibles): consejos "
                "solo por composicion y tiempo de juego."
            )
        if not role:
            warnings.append(
                "Rol no reportado por el Live Client (comun fuera de ranked): "
                "se usan consejos genericos."
            )

        return {
            "in_game": True,
            "game_time_seconds": game_time,
            "champion": me.get("champion"),
            "champion_image_url": me.get("champion_image_url"),
            "role": role,
            "role_label": ROLE_LABELS_ES.get(role or "", None),
            "probability": {
                "value": win_probability.get("probability"),
                "confidence": win_probability.get("confidence", "baja"),
                "method": win_probability.get("method"),
            },
            "next_action": action,
            "item_priorities": items,
            # Compatibilidad: el primero de la lista.
            "item_priority": items[0] if items else None,
            "gold_diff": gold,
            "top_threat": (performance or {}).get("top_threat"),
            "win_condition": (performance or {}).get("win_condition"),
            "objective_timers": timers,
            "warnings": warnings,
            "data_source": snapshot.get("data_source", "live_client"),
        }

    # ------------------------------------------------------------ accion

    def _next_action(
        self,
        snapshot: dict,
        role: str | None,
        minute: float,
        timers: list[dict],
        performance: dict | None,
        gold: dict | None,
        win_probability: dict,
        recommendations: dict,
    ) -> dict[str, Any]:
        probability = win_probability.get("probability")
        stance = "pareja"
        if probability is not None:
            if probability >= AHEAD_THRESHOLD:
                stance = "delante"
            elif probability <= BEHIND_THRESHOLD:
                stance = "detras"

        objective = next_objective(timers)
        reasons: list[str] = []

        # 0a) Ventaja numerica AHORA (enemigos muertos): la ventana mas
        # accionable de todas, dura segundos.
        numbers_action = self._numbers_advantage_action(snapshot, role)
        if numbers_action is not None:
            return numbers_action

        # 0b) Baron reciente de tu equipo: aprovechar el buff manda.
        buff_action = self._baron_buff_action(snapshot, minute * 60, role)
        if buff_action is not None:
            return buff_action

        # 1) Objetivo inminente manda sobre cualquier rutina de rol.
        if objective is not None:
            if objective["objective"] == "dragon":
                reasons.extend(self._soul_clauses(snapshot))
            return self._objective_action(
                objective, role, stance, gold, performance, reasons
            )

        # 2) Sin objetivo cercano: rutina por rol y fase.
        return self._role_action(
            snapshot, role, minute, stance, gold, performance, recommendations
        )

    def _numbers_advantage_action(
        self, snapshot: dict, role: str | None
    ) -> dict[str, Any] | None:
        """2+ enemigos muertos de diferencia: hay que convertir YA."""
        if not snapshot.get("live_signals_available", True):
            return None
        enemies = snapshot.get("enemies") or []
        allies = (snapshot.get("allies") or []) + [snapshot.get("me") or {}]
        dead_enemies = sum(1 for p in enemies if p.get("is_dead"))
        dead_allies = sum(1 for p in allies if p.get("is_dead"))
        if dead_enemies < 2 or dead_enemies - dead_allies < 2:
            return None
        role_bit = {
            "JUNGLE": "fuerza dragon/heraldo/baron o roba la jungla enemiga",
            "UTILITY": "avanza con tu equipo y planta vision profunda",
            "TOP": "empuja tu linea hasta la torre o rota a la pelea",
            "MIDDLE": "empuja mid y rota al objetivo mas cercano",
            "BOTTOM": "tira la torre mas cercana con tu soporte",
        }.get(role or "", "convierte en torres, dragon o vision")
        return {
            "title": f"Ventaja numerica ({dead_enemies} enemigos muertos): {role_bit}",
            "detail": "esta ventana dura segundos: no la gastes farmeando",
            "reasons": [],
            "urgency": "ahora",
            "timer_seconds": None,
            "objective": "numeros",
        }

    def _baron_buff_action(
        self, snapshot: dict, game_time: float, role: str | None
    ) -> dict[str, Any] | None:
        """Si tu equipo mato al Baron hace <150s, la accion es empujar con
        el buff, no preparar el siguiente objetivo."""
        ally_names = set()
        for player in (snapshot.get("allies") or []) + [snapshot.get("me") or {}]:
            riot_id = player.get("riot_id") or ""
            if riot_id:
                ally_names.add(riot_id)
                ally_names.add(riot_id.split("#")[0])
        for event in snapshot.get("objective_events") or []:
            if event.get("EventName") != "BaronKill":
                continue
            event_time = float(event.get("EventTime") or 0)
            if 0 <= game_time - event_time <= 150 and event.get("KillerName") in ally_names:
                remaining = int(180 - (game_time - event_time))
                role_bit = (
                    "empuja side lanes con el buff" if role == "TOP"
                    else "agrupa y tira torres/inhibidor con las oleadas potenciadas"
                )
                return {
                    "title": f"Tienes buff de Baron (~{max(remaining, 0)}s): {role_bit}",
                    "detail": "no lo desperdicies peleando en la jungla; convierte en torres",
                    "reasons": [],
                    "urgency": "ahora",
                    "timer_seconds": max(remaining, 0),
                    "objective": "baron_buff",
                }
        return None

    def _objective_action(
        self,
        objective: dict,
        role: str | None,
        stance: str,
        gold: dict | None,
        performance: dict | None,
        reasons: list[str],
    ) -> dict[str, Any]:
        label = objective["label"]
        zone = OBJECTIVE_ZONE.get(objective["objective"], "rio")
        seconds = objective.get("seconds_until") or 0

        if seconds > 0:
            when = f"{label} aparece en {int(seconds)}s"
        else:
            # El estado viene con hasta REFRESH_SECONDS de retraso: si el
            # objetivo cayo hace segundos, este mensaje aun no lo sabe.
            when = f"{label} deberia estar arriba (confirmalo en el mapa)"

        if gold and gold["gold_diff"] >= 800:
            reasons.append(f"vas {gold['label']}")
            fight_clause = "puedes forzar la pelea si llegan 5"
        elif gold and gold["gold_diff"] <= -800:
            reasons.append(f"vas {gold['label']}")
            fight_clause = "no lo disputes a ciegas: cambia por torres o cedelo sin muertes"
        else:
            fight_clause = "disputa solo con vision y numeros iguales"
        if stance == "detras" and (not gold or gold["gold_diff"] > -800):
            fight_clause = "disputa solo con un pick o ventaja numerica"

        threat = (performance or {}).get("top_threat")
        if threat and threat.get("fed"):
            reasons.append(
                f"{threat['champion']} enemigo va {threat['kda']}: no inicies sobre el"
            )

        role_bit = {
            "UTILITY": f"adelanta vision en {zone} y limpia la enemiga",
            "JUNGLE": f"asegura vision en {zone} y guarda el smite",
            "MIDDLE": f"empuja mid y rota primero a {zone}",
            "BOTTOM": f"empuja la oleada y rota con tu soporte a {zone}",
            "TOP": (
                f"empuja tu oleada y rota a {zone}"
                if objective["objective"] in ("baron", "herald", "grubs")
                else "empuja top y mantente listo para TP o para presionar arriba"
            ),
        }.get(role or "", f"prepara {zone} con tu equipo")

        return {
            "title": f"{when}: {role_bit}",
            "detail": fight_clause,
            "reasons": reasons,
            "urgency": "ahora" if seconds <= 30 else "preparar",
            "timer_seconds": int(seconds),
            "objective": objective["objective"],
        }

    def _role_action(
        self,
        snapshot: dict,
        role: str | None,
        minute: float,
        stance: str,
        gold: dict | None,
        performance: dict | None,
        recommendations: dict,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if gold and abs(gold["gold_diff"]) >= 800:
            reasons.append(f"vas {gold['label']}")
        reasons.extend(self._soul_clauses(snapshot))

        threat = (performance or {}).get("top_threat")
        win_condition = (performance or {}).get("win_condition")
        lane_status = (performance or {}).get("lane_status") or {}

        # Rival directo muerto: ventana de tempo para las lineas.
        rival = snapshot.get("direct_rival") or {}
        if rival.get("is_dead") and role in ("TOP", "MIDDLE", "BOTTOM", "UTILITY"):
            respawn = rival.get("respawn_timer")
            respawn_bit = f" (~{int(respawn)}s)" if respawn else ""
            move = (
                "roamea a otra linea o adelanta vision"
                if role in ("MIDDLE", "UTILITY")
                else "tira la oleada a torre y recupera CS/plates"
            )
            return {
                "title": f"Tu rival ({rival.get('champion', '?')}) esta muerto{respawn_bit}: {move}",
                "detail": "gana tempo mientras no hay presion enemiga; vuelve antes de que reviva",
                "reasons": reasons,
                "urgency": "ahora",
                "timer_seconds": int(respawn) if respawn else None,
                "objective": "tempo",
            }

        title, detail = self._playbook(
            role, minute, stance, threat, win_condition, lane_status, recommendations
        )
        if threat and threat.get("fed"):
            reasons.append(
                f"cuidado con {threat['champion']} ({threat['kda']})"
            )
        if win_condition and win_condition.get("fed") and role in ("UTILITY", "JUNGLE"):
            reasons.append(
                f"{win_condition['champion']} aliado va {win_condition['kda']}: juega a su lado"
            )

        return {
            "title": title,
            "detail": detail,
            "reasons": reasons,
            "urgency": "info",
            "timer_seconds": None,
            "objective": None,
        }

    def _playbook(
        self,
        role: str | None,
        minute: float,
        stance: str,
        threat: dict | None,
        win_condition: dict | None,
        lane_status: dict,
        recommendations: dict,
    ) -> tuple[str, str]:
        """Rutina por rol cuando no hay objetivo inminente. Devuelve (titulo, detalle)."""
        early, mid_game = minute < 14, 14 <= minute < 25

        if role == "JUNGLE":
            gank = self._best_gank(recommendations)
            if early:
                if gank:
                    target_bit = f": a por {gank['enemy']}" if gank.get("enemy") else ""
                    return (
                        f"Ruta hacia {gank['label']}{target_bit}",
                        gank.get("note")
                        or "gankea la linea con mas ventaja; roba campamentos solo con vision",
                    )
                return (
                    "Farmea full clear y lee el mapa",
                    "gankea solo lineas con empuje aliado; no fuerces sin vision",
                )
            if stance == "delante":
                if gank and gank.get("enemy"):
                    return (
                        f"Invade o gankea {gank['label']}: presiona a {gank['enemy']}",
                        gank.get("note")
                        or "roba campamentos y coloca vision profunda; fuerza al rival a jugar corto",
                    )
                return (
                    "Invade la jungla enemiga con un companero",
                    "roba campamentos y coloca vision profunda; fuerza al rival a jugar corto",
                )
            return (
                "Farmea seguro y cubre el lado de tu win condition",
                f"protege a {win_condition['champion']}" if win_condition
                else "no pelees sin numeros; espera el siguiente objetivo",
            )

        if role == "MIDDLE":
            if early:
                weak_side = self._weakest_enemy_lane(lane_status)
                if weak_side:
                    return (
                        f"Empuja mid y rota a {weak_side}",
                        "tu prioridad de mid abre roams; avisa antes de moverte",
                    )
                return ("Prioridad en mid", "empuja la oleada y controla el rio con tu jungla")
            if stance == "detras":
                return (
                    "Limpia oleadas y no mueras",
                    "defiende torres; escala hasta tu proximo pico de items",
                )
            return (
                "Agrupa con tu equipo tras empujar mid",
                "busca picks con vision; no dividas el mapa sin razon",
            )

        if role == "TOP":
            if early:
                return (
                    "Gestiona la oleada y vigila al jungla enemigo",
                    "congela si vas parejo; empuja solo con vision en rio",
                )
            if stance == "delante" and mid_game:
                return (
                    "Presiona side lane (split push)",
                    "empuja top/bot con TP disponible; sal antes de perder la ventaja",
                )
            return (
                "Agrupa con TP listo para la proxima pelea",
                "no te aisles: tu equipo te necesita en las peleas por objetivos",
            )

        if role == "BOTTOM":
            if early:
                return (
                    "Farmea y juega el 2v2 con tu soporte",
                    "respeta los ganks: el ADC muerto pierde mas de lo que gana una kill",
                )
            if threat and threat.get("fed"):
                return (
                    f"Posicionate lejos de {threat['champion']}",
                    "entra tarde a las peleas y pega a lo mas cercano; tu DPS gana si sobrevives",
                )
            return (
                "Agrupa y pega desde atras",
                "manten distancia maxima; prioriza torres tras cada pelea ganada",
            )

        if role == "UTILITY":
            # Un soporte asiste a TODO el mapa: el roam se decide por el
            # estado real de las lineas, tambien en fase de lineas.
            bot = lane_status.get("BOTTOM") or {}
            if early:
                if (bot.get("diff") or 0) <= -1.0:
                    return (
                        "Quedate con tu ADC: bot va perdiendo",
                        "juega defensivo bajo torre, pide gank y tradea solo con "
                        "los enfriamientos del rival gastados",
                    )
                roam_target = self._best_support_roam(lane_status)
                if roam_target:
                    return (
                        f"Empuja bot y roamea a {roam_target}",
                        "warda el rio en el camino y vuelve rapido: tu ADC no debe "
                        "recibir 2v1 largo",
                    )
                return (
                    "Vision de rio y protege a tu ADC",
                    "compra wards de control; roamea a mid solo con bot empujado",
                )
            weak_lane = self._weakest_ally_lane(lane_status)
            if weak_lane:
                return (
                    f"Cubre {weak_lane} con vision y presencia",
                    "es la linea aliada mas presionada; un ward y un flanco a "
                    "tiempo valen mas que seguir pegado a bot",
                )
            return (
                "Vision alrededor del proximo objetivo",
                "limpia vision enemiga con tu equipo cerca; no wardees solo",
            )

        # Rol desconocido: consejo generico honesto.
        if stance == "delante":
            return ("Convierte la ventaja en objetivos", "agrupa, fuerza vision y toma torres")
        if stance == "detras":
            return ("Juega seguro y escala", "evita peleas innecesarias; espera un error rival")
        return ("Juega con tu equipo", "controla vision y pelea solo con numeros")

    # ------------------------------------------------------------- items

    def _item_priorities(self, recommendations: dict) -> list[dict[str, Any]]:
        """Plan de compra: item nucleo primero, botas en segundo lugar y una
        alternativa de alto valor. Con botas ya compradas (el recomendador
        no emite botas) quedan solo 2 items nucleo."""
        items = [
            i for i in recommendations.get("items") or []
            if (i.get("extra") or {}).get("item_id")
        ]
        boots = [i for i in items if i["extra"].get("is_boots")]
        core = [i for i in items if not i["extra"].get("is_boots")]

        ordered: list[dict] = []
        if core:
            ordered.append(core[0])
        if boots:
            ordered.append(boots[0])
            ordered.extend(core[1:2])
        else:
            ordered.extend(core[1:2])
        if not ordered and boots:
            ordered = boots[:1]
        return [self._item_payload(i) for i in ordered[:3]]

    def _item_payload(self, rec: dict) -> dict[str, Any]:
        extra = rec.get("extra") or {}
        remaining = extra.get("remaining_gold")
        if remaining == 0:
            gold_note = "te alcanza ahora"
        elif remaining is not None:
            gold_note = f"faltan {remaining} de oro"
        else:
            gold_note = None
        return {
            "name": rec.get("title"),
            "image_url": rec.get("image_url"),
            "item_id": extra.get("item_id"),
            "gold_cost": extra.get("gold_cost"),
            "buy_cost": extra.get("buy_cost"),
            "remaining_gold": remaining,
            "gold_note": gold_note,
            "stat_summary": extra.get("stat_summary"),
            "is_boots": extra.get("is_boots", False),
            "reason": (extra.get("reasons") or [None])[0],
            "confidence": rec.get("confidence"),
        }

    # ----------------------------------------------------------- helpers

    def _best_gank(self, recommendations: dict) -> dict[str, Any] | None:
        """Mejor gank disponible con su objetivo concreto (campeon y nota)."""
        for gank in recommendations.get("ganks") or []:
            extra = gank.get("extra") or {}
            if extra.get("role"):
                return {
                    "label": ROLE_LABELS_ES.get(extra["role"], extra["role"]),
                    "enemy": extra.get("enemy"),
                    "note": extra.get("target_note"),
                }
        return None

    def _soul_clauses(self, snapshot: dict) -> list[str]:
        """Avisos de punto de alma (3+ dragones de un lado)."""
        events = snapshot.get("events_summary") or {}
        my_team = snapshot.get("my_team")
        if not my_team:
            return []
        mine = (events.get(my_team) or {}).get("dragons") or 0
        theirs = (events.get("CHAOS" if my_team == "ORDER" else "ORDER") or {}).get("dragons") or 0
        if mine >= 3:
            return [f"llevan {mine} dragones: el proximo puede dar ALMA, prepara ese timer"]
        if theirs >= 3:
            return [f"el rival lleva {theirs} dragones: NIEGA el alma o cambia por otra cosa"]
        return []

    def _best_support_roam(self, lane_status: dict) -> str | None:
        """Linea (mid primero) donde un roam del soporte puede rematar la
        ventaja aliada."""
        for role in ("MIDDLE", "TOP"):
            status = lane_status.get(role) or {}
            if (status.get("diff") or 0) >= 0.75:
                return ROLE_LABELS_ES.get(role)
        return None

    def _weakest_ally_lane(self, lane_status: dict) -> str | None:
        """Linea aliada mas presionada (para que el soporte la cubra en mid game)."""
        candidates = [
            (role, status.get("diff"))
            for role, status in lane_status.items()
            if role in ("TOP", "MIDDLE", "BOTTOM") and status.get("diff") is not None
        ]
        if not candidates:
            return None
        role, diff = min(candidates, key=lambda x: x[1])
        return ROLE_LABELS_ES.get(role) if diff <= -1.0 else None

    def _weakest_enemy_lane(self, lane_status: dict) -> str | None:
        """Linea (no mid) donde el aliado va mas por delante: buen roam."""
        candidates = [
            (role, status["diff"])
            for role, status in lane_status.items()
            if role in ("TOP", "BOTTOM") and status.get("diff") is not None
        ]
        if not candidates:
            return None
        role, diff = max(candidates, key=lambda x: x[1])
        return ROLE_LABELS_ES.get(role) if diff >= 1.0 else None

    def _total_dragons(self, snapshot: dict) -> int:
        events = snapshot.get("events_summary") or {}
        return (
            (events.get("ORDER", {}).get("dragons") or 0)
            + (events.get("CHAOS", {}).get("dragons") or 0)
        )


def refresh_item_gold(payload: dict, live_client, ddragon) -> None:
    """Recalcula el oro faltante de las compras con datos frescos del juego.

    El payload del overlay sale de un cache que se reconstruye cada
    REFRESH_SECONDS: si el jugador compro OTRA cosa en medio, su oro y sus
    componentes ya no son los del cache y el "faltan X de oro" quedaba
    desfasado. /activeplayer y /playerlist son llamadas loopback baratas,
    asi que se consultan por request y el costo real de tienda se recalcula
    con el inventario actual (descuenta componentes como la tienda misma).
    Si el Live Client no responde (modo espectador), se deja el payload tal
    cual: mejor un dato viejo etiquetado que romper el overlay.
    """
    items = [i for i in payload.get("item_priorities") or [] if i.get("item_id")]
    if not items:
        return
    active = live_client.get_active_player() or {}
    gold = active.get("currentGold")
    if gold is None:
        return
    owned = _live_owned_counts(live_client, active)
    for item in items:
        buy_cost = None
        if owned is not None:
            buy_cost = item_recommender.live_buy_cost(ddragon, item["item_id"], owned)
        if buy_cost is None:
            buy_cost = item.get("buy_cost") or item.get("gold_cost")
        if buy_cost is None:
            continue
        remaining = max(0, int(round(buy_cost - gold)))
        item["buy_cost"] = int(buy_cost)
        item["remaining_gold"] = remaining
        item["gold_note"] = (
            "te alcanza ahora" if remaining == 0 else f"faltan {remaining} de oro"
        )


def _live_owned_counts(live_client, active: dict) -> dict[int, int] | None:
    """Inventario actual del jugador activo segun /playerlist, o None."""
    my_name = active.get("riotId") or active.get("summonerName") or ""
    players = live_client.get_player_list() or []

    def player_name(p: dict) -> str:
        return str(p.get("riotId") or p.get("summonerName") or "")

    me = next((p for p in players if player_name(p) == my_name), None)
    if me is None and my_name:
        short = my_name.split("#")[0]
        me = next((p for p in players if player_name(p).split("#")[0] == short), None)
    if me is None:
        return None
    counts: dict[int, int] = {}
    for item in me.get("items") or []:
        try:
            item_id = int(item.get("itemID") or item.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if item_id:
            counts[item_id] = counts.get(item_id, 0) + int(item.get("count") or 1)
    return counts

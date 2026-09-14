"""Chatbot analitico basado en intents deterministas.

Decision de disenio: en lugar de un LLM (que podria alucinar datos), el
chat clasifica la pregunta con patrones y responde COMPONIENDO datos
reales (live, historicos, Data Dragon). prompt_builder.py deja lista la
integracion futura con un LLM sin tocar esta capa.

Sobre jugadores: las preguntas pueden referirse al rival directo de la
partida activa O a CUALQUIER jugador escribiendo su Riot ID (Nombre#TAG)
en la pregunta; en ese caso el historial se descarga de la Riot API bajo
demanda (como hace porofessor.gg), sin necesidad de que ese jugador este
jugando en esta maquina.

Regla dura: si el dato no existe, se dice explicitamente.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Callable

from app.analytics.champion_stats import games_with_champion, top_champions
from app.analytics.player_profile import OpponentScout, build_local_profile
from app.chat.game_knowledge import GameKnowledgeBase
from app.chat.llm_client import LLMClient
from app.data.normalizers import ROLE_LABELS_ES
from app.data.repositories import MatchRepository
from app.ml.player_style_model import classify_style
from app.riot.data_dragon import DataDragon

logger = logging.getLogger(__name__)

# Riot ID: nombre (2-16 chars, permite espacios/apostrofes/unicode) + #TAG.
RIOT_ID_RE = re.compile(r"([\w'.][\w'. ]{1,15})\s*#\s*(\w{2,5})", re.UNICODE)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


class ChatService:
    OUT_OF_SCOPE_MESSAGE = (
        "Mi especialidad es ayudarte a mejorar en League of Legends. "
        "Si tienes dudas sobre mecánicas básicas, cómo controlar las oleadas "
        "de súbditos, cuándo pelear por el Dragón o la historia de algún "
        "campeón, ¡aquí estaré para ayudarte a ganar tus partidas!"
    )

    # Terminos suficientemente especificos para permitir una pregunta abierta
    # al LLM. Se evitan palabras ambiguas como "objetivo", "historia" o
    # "personaje", que tambien aparecen en consultas ajenas al juego.
    LOL_DOMAIN_TOKENS = {
        "ad", "adc", "ap", "aram", "baron", "bot", "build", "campeon",
        "campeones", "cc", "challenger", "cs", "demacia", "dragon", "elo",
        "freljord", "gank", "grubs", "heraldo", "invocador",
        "inhibidor", "ionia", "jungla", "jungler", "kda", "laning", "lunari",
        "lol", "macro", "maestria", "matchup", "mid", "minion", "moba",
        "nexo", "noxus",
        "oleada", "pentakill", "peel", "piltover", "ranked", "roam", "runas",
        "runa", "runaterra", "shurima", "solari", "subdito", "subditos",
        "support", "targon", "teamfight", "tempo", "top", "torre", "torres",
        "torreta", "ward", "wards", "wave", "winrate", "zaun",
    }
    LOL_DOMAIN_PHRASES = {
        "control de oleadas", "grieta del invocador", "league of legends",
        "manejo de oleadas", "riot id", "split push", "wave management",
    }

    def __init__(
        self,
        repo: MatchRepository,
        ddragon: DataDragon,
        scout: OpponentScout | None,
        live_state_fn: Callable[[], tuple[dict | None, dict | None]],
        my_puuid_fn: Callable[[], str | None],
        review_service=None,
        llm: LLMClient | None = None,
    ):
        self.repo = repo
        self.ddragon = ddragon
        self.scout = scout
        self.live_state_fn = live_state_fn
        self.my_puuid_fn = my_puuid_fn
        self.review_service = review_service
        self.knowledge = GameKnowledgeBase()
        self.llm = llm

    # ------------------------------------------------------------- routing

    INTENTS = [
        ("match_review", r"(donde|en que momento|cuando).*(se )?(perdio|gano|torcio|cayo)|"
                         r"por ?que (perdi|gane|perdimos|ganamos)|"
                         r"revis(a|ion).*(partida|ultima)|resumen.*(partida|ultima)|"
                         r"analiza.*(mi |la )?(ultima )?partida|"
                         r"puntos? de inflexion|curva de probabilidad"),
        ("weekly_patterns", r"patron(es)?|resumen semanal|analiza (mi|esta|la) semana|"
                            r"tendencias?\b|en que (estoy )?fallando|que (debo|puedo) mejorar"),
        ("item", r"(que|cual)\b.*(item|objeto|compra|comprar)|deberia comprar"),
        ("gank", r"gank|(que|cual)\b.*linea.*(prior|gank)|linea debo priorizar"),
        ("damage_type", r"(tipo de dan|dano|danio).*(afecta|le hace|le pega)|que dano"),
        ("rival_champion_games", r"(cuantas|cuantos).*(partidas|juegos).*(rival|enemigo|oponente|#|con)"),
        ("rival_top_champions", r"(tres|3|top).*(campeones).*(jugados|del rival|rival|#)|campeones mas jugados"),
        ("rival_main_role", r"(que|cual).*(linea|rol|posicion).*(juega|frecuente)"),
        ("style", r"estilo.*(agresivo|defensivo|neutral|reciente)|es agresivo|estilo de juego"),
        ("objective", r"(que|cual).*(objetivo).*(prior|deberiamos|tomar)|objetivo"),
        ("win_probability", r"(probabilidad|chance).*(ganar|victoria)"),
        ("player_profile", r"perfil|como juega|estadisticas|winrate|informacion de|analiza"),
        # Las formas genericas ("que es", "quien es") no son un intent por
        # si solas: enviaban cualquier tema desconocido al corpus local. Los
        # conceptos conocidos tambien se buscan en _handle_fallback.
        ("game_basics", r"dragon|heraldo|baron|grubs|abismo|tempo|vision|prioridad|gank"),
    ]

    def answer(self, question: str) -> dict[str, Any]:
        normalized = _norm(question)
        for intent, pattern in self.INTENTS:
            if re.search(pattern, normalized):
                handler = getattr(self, f"_handle_{intent}")
                try:
                    return handler(question, normalized)
                except Exception:
                    logger.exception("Error en intent %s", intent)
                    return self._response(
                        "Ocurrio un error procesando tu pregunta con los datos disponibles.",
                        intent, False,
                    )
        return self._handle_fallback(question)

    @staticmethod
    def _response(answer: str, intent: str, data_available: bool, sources: list[str] | None = None,
                  data_source: str = "mixto") -> dict[str, Any]:
        return {
            "answer": answer,
            "intent": intent,
            "data_available": data_available,
            "sources": sources or [],
            "data_source": data_source,
        }

    def proactive_tip(self) -> dict[str, Any]:
        """Coach automatico para la UI: una recomendacion breve y accionable."""
        snapshot, recommendations = self._live()
        if not snapshot or not recommendations:
            return self._response(
                "Sin partida activa no genero alertas proactivas.",
                "proactive", False,
            )

        ganks = [
            g for g in recommendations.get("ganks", [])
            if g.get("extra", {}).get("role") and g.get("confidence") in {"alta", "media"}
        ]
        objectives = [
            o for o in recommendations.get("objectives", [])
            if o.get("extra", {}).get("success_probability") is not None
        ]
        items = [
            i for i in recommendations.get("items", [])
            if i.get("extra", {}).get("remaining_gold") == 0
        ]

        parts = []
        sources = []
        if ganks:
            gank = ganks[0]
            parts.append(f"Linea inicial: {gank['title']}. {gank['detail']}.")
            sources.append("gank_model")
        if objectives:
            obj = objectives[0]
            probability = obj["extra"]["success_probability"]
            parts.append(f"Objetivo: {obj['title']} ({probability:.0%} exito estimado).")
            sources.append("objective_model")
        if items:
            item = items[0]
            parts.append(f"Compra ahora: {item['title']} ({item['detail']}).")
            sources.append("item_recommender")

        if not parts:
            return self._response(
                "No hay una alerta fuerte ahora: juega por vision, oleadas y evita iniciar sin prioridad.",
                "proactive", True, sources=["live_client"], data_source="mixto",
            )
        return self._response(
            "Coach: " + " ".join(parts),
            "proactive", True, sources=sources, data_source="mixto",
        )

    # ------------------------------------------------------------ helpers

    def _live(self) -> tuple[dict | None, dict | None]:
        return self.live_state_fn()

    def _rival(self, snapshot: dict | None) -> dict | None:
        return (snapshot or {}).get("direct_rival")

    def _find_champion_in_text(self, text: str) -> str | None:
        normalized = _norm(text)
        for champ in self.ddragon.champions().values():
            for candidate in (champ.get("name", ""), champ.get("id", "")):
                normalized_candidate = _norm(candidate)
                if candidate and re.search(
                    rf"(?<!\w){re.escape(normalized_candidate)}(?!\w)",
                    normalized,
                ):
                    return champ["id"]
        return None

    def _is_lol_question(self, question: str) -> bool:
        """Decide localmente si una pregunta abierta pertenece al dominio LoL."""
        normalized = _norm(question)
        tokens = set(re.findall(r"\b\w+\b", normalized))
        if tokens & self.LOL_DOMAIN_TOKENS:
            return True
        if any(phrase in normalized for phrase in self.LOL_DOMAIN_PHRASES):
            return True
        if RIOT_ID_RE.search(question):
            return True
        return self._find_champion_in_text(question) is not None

    # Conectores que la regex puede arrastrar antes del nombre real
    # ("campeones de Faker#KR1" captura "de Faker"). Los nombres con espacios
    # legitimos ("Hide on bush") no empiezan con estas palabras.
    _NAME_STOPWORDS = {
        "de", "del", "a", "al", "el", "la", "los", "las", "un", "una",
        "jugador", "sobre", "para", "con", "es", "perfil", "juega",
    }

    @classmethod
    def _riot_id_candidates(cls, question: str) -> list[tuple[str, str]]:
        """Candidatos de Riot ID extraidos de la pregunta.

        Como los nombres pueden llevar espacios ('Hide on bush#KR1'), la
        regex puede arrastrar palabras de la frase ('jugados de Käzah').
        Se devuelven sufijos progresivos (del mas largo al mas corto) y el
        que resuelva primero contra la API/historial gana.
        """
        match = RIOT_ID_RE.search(question)
        if not match:
            return []
        tag = match.group(2).strip()
        parts = match.group(1).strip().split()
        while len(parts) > 1 and parts[0].lower() in cls._NAME_STOPWORDS:
            parts.pop(0)
        return [(" ".join(parts[i:]), tag) for i in range(len(parts))]

    def _extract_riot_id(self, question: str) -> tuple[str, str] | None:
        candidates = self._riot_id_candidates(question)
        return candidates[0] if candidates else None

    def _local_profile_by_riot_id(self, game_name: str, tag_line: str) -> dict | None:
        """Busca al jugador en el historial local (fallback sin API key)."""
        participants = self.repo.participants_df()
        if participants.empty:
            return None
        mask = participants["riotIdGameName"].fillna("").str.lower() == game_name.lower()
        mask &= participants["riotIdTagline"].fillna("").str.lower() == tag_line.lower()
        subset = participants[mask]
        if subset.empty:
            return None
        puuid = subset["puuid"].iloc[0]
        player_df = participants[participants["puuid"] == puuid]
        profile = build_local_profile(player_df, self.ddragon)
        return {
            "available": profile.get("games", 0) > 0,
            "riot_id": f"{game_name}#{tag_line}",
            "puuid": puuid,
            **profile,
        }

    NO_TARGET_HELP = (
        "No detecto una partida activa con rival identificable. Puedes "
        "preguntarme por cualquier jugador escribiendo su Riot ID, por "
        "ejemplo: 'campeones mas jugados de Faker#KR1'."
    )

    def _target_player(self, question: str) -> dict:
        """Resuelve de que jugador trata la pregunta.

        Prioridad: Riot ID explicito en el texto > rival directo en vivo.
        Con API key el historial se descarga bajo demanda (Riot API); sin
        key se usa lo que exista en el historial local.
        """
        candidates = self._riot_id_candidates(question)
        if candidates:
            if self.scout:
                for game_name, tag in candidates:
                    profile = self.scout.profile(game_name, tag)
                    if profile.get("available"):
                        return {"riot_id": profile.get("riot_id", f"{game_name}#{tag}"),
                                "profile": profile, "origin": "riot_api", "error": None}
                    if profile.get("puuid"):
                        # La cuenta existe pero sin partidas descargables:
                        # el nombre ya resolvio, no probar sufijos mas cortos.
                        return {"riot_id": profile.get("riot_id", f"{game_name}#{tag}"),
                                "profile": None, "origin": "riot_api",
                                "error": "La cuenta existe pero no tiene partidas "
                                         "recientes descargables en ninguna cola."}
            # Fallback al historial local (sin key o si la API no lo encontro).
            for game_name, tag in candidates:
                local = self._local_profile_by_riot_id(game_name, tag)
                if local and local.get("available"):
                    return {"riot_id": f"{game_name}#{tag}", "profile": local,
                            "origin": "historico", "error": None}
            riot_id = "#".join(candidates[-1])
            if self.scout:
                return {"riot_id": riot_id, "profile": None, "origin": "riot_api",
                        "error": f"No encontre a {riot_id} en la region configurada."}
            return {"riot_id": riot_id, "profile": None, "origin": None,
                    "error": (f"Sin RIOT_API_KEY solo puedo buscar en el historial local, "
                              f"y {riot_id} no aparece en el.")}

        snapshot, _ = self._live()
        rival = self._rival(snapshot)
        if rival and "#" in rival.get("riot_id", ""):
            game_name, tag = rival["riot_id"].split("#", 1)
            if self.scout:
                profile = self.scout.profile(game_name, tag)
                if profile.get("available"):
                    return {"riot_id": rival["riot_id"], "profile": profile,
                            "origin": "riot_api", "champion": rival.get("champion"), "error": None}
                return {"riot_id": rival["riot_id"], "profile": None, "origin": "riot_api",
                        "champion": rival.get("champion"),
                        "error": profile.get("reason", "No pude obtener su historial.")}
            local = self._local_profile_by_riot_id(game_name, tag)
            if local and local.get("available"):
                return {"riot_id": rival["riot_id"], "profile": local,
                        "origin": "historico", "champion": rival.get("champion"), "error": None}
            return {"riot_id": rival["riot_id"], "profile": None, "origin": None,
                    "champion": rival.get("champion"),
                    "error": "No tengo API key ni historial local de tu rival."}

        return {"riot_id": None, "profile": None, "origin": None, "error": self.NO_TARGET_HELP}

    # ------------------------------------------------------------ intents

    def _handle_item(self, question, normalized):
        _, recommendations = self._live()
        items = (recommendations or {}).get("items", [])
        real_items = [i for i in items if i.get("extra")]
        if not real_items:
            return self._response(
                "Ahora mismo no tengo una recomendacion de item fundamentada: "
                "no hay partida activa o no hay suficientes partidas similares "
                "en tu historial local.",
                "item", False,
            )
        lines = [f"- {i['title']}: {i['explanation']}" for i in real_items[:3]]
        return self._response(
            "Opciones de compra razonadas:\n" + "\n".join(lines),
            "item", True,
            sources=[i.get("data_source", "mixto") for i in real_items[:3]],
        )

    def _handle_gank(self, question, normalized):
        _, recommendations = self._live()
        ganks = (recommendations or {}).get("ganks", [])
        useful = [g for g in ganks if g.get("extra", {}).get("role")]
        if not useful:
            return self._response(
                "No puedo priorizar lineas sin partida activa (Live Client). "
                "Abre una partida y vuelve a preguntar.",
                "gank", False,
            )
        lines = [f"- {g['title']} - {g['explanation']}" for g in useful]
        return self._response("\n".join(lines), "gank", True, sources=["live_client", "historico"])

    def _handle_damage_type(self, question, normalized):
        champion = self._find_champion_in_text(question)
        if champion is None:
            snapshot, _ = self._live()
            rival = self._rival(snapshot)
            champion = rival.get("champion") if rival else None
        if not champion:
            return self._response(
                "Dime el campeon (ej. '¿que tipo de danio le afecta mas a Malphite?') "
                "o abre una partida para usar a tu rival directo.",
                "damage_type", False,
            )
        info = self.ddragon.champion_info(champion)
        if info["source"] == "fallback":
            return self._response(
                f"No tengo datos de Data Dragon para '{champion}' (sin cache ni red).",
                "damage_type", False,
            )
        # 'Que danio le afecta mas' = contra que es menos resistente segun su
        # perfil defensivo oficial; ademas reportamos que danio HACE el.
        profile = self.ddragon.champion_damage_profile(champion)
        deals = "fisico" if profile["physical"] >= profile["magic"] else "magico"
        answer = (
            f"{champion} (arquetipo: {', '.join(info['tags']) or 'desconocido'}) "
            f"inflige principalmente danio {deals} "
            f"(~{round(max(profile['physical'], profile['magic']) * 100)}% segun Data Dragon). "
            f"Su atributo de defensa oficial es {info['defense']}/10: "
            + ("es duro de matar; considera penetracion o danio sostenido."
               if info["defense"] >= 7 else
               "no es especialmente tanque; el danio directo funciona bien.")
            + " Riot no publica resistencias exactas por nivel en esta API, asi que esto es un perfil, no un numero exacto."
        )
        return self._response(answer, "damage_type", True, sources=["ddragon"], data_source="historico")

    def _handle_rival_champion_games(self, question, normalized):
        target = self._target_player(question)
        profile = target.get("profile")
        if not profile:
            return self._response(target["error"], "rival_champion_games", False)
        champion = self._find_champion_in_text(question) or target.get("champion")
        if not champion:
            return self._response(
                f"¿Con que campeon? Ej.: '¿cuantas partidas ha jugado "
                f"{target['riot_id']} con Ahri?' (o abre una partida para usar "
                "el campeon actual de tu rival).",
                "rival_champion_games", False,
            )
        stats = games_with_champion(
            self.repo.participants_df(), profile["puuid"], champion
        )
        return self._response(
            f"En su muestra reciente ({stats['sample_total']} registros locales), "
            f"{target['riot_id']} jugo {stats['games']} partida(s) con {champion} "
            f"y gano {stats['wins']}. La muestra cubre solo sus ultimas partidas "
            "ranked descargadas, no todo su historial.",
            "rival_champion_games", True, sources=["historico", "match-v5"],
            data_source="historico",
        )

    def _handle_rival_top_champions(self, question, normalized):
        target = self._target_player(question)
        profile = target.get("profile")
        if not profile:
            return self._response(target["error"], "rival_top_champions", False)
        tops = profile.get("top_champions", [])
        if not tops:
            return self._response(
                f"No hay partidas de {target['riot_id']} en la muestra para responder.",
                "rival_top_champions", False,
            )
        lines = [
            f"- {c['name']}: {c['games']} partidas, winrate {c['winrate']:.0%}, KDA {c['kda']}"
            for c in tops
        ]
        return self._response(
            f"Campeones mas jugados de {target['riot_id']} (muestra: sus ultimas "
            f"partidas ranked descargadas):\n" + "\n".join(lines),
            "rival_top_champions", True, sources=[target.get("origin") or "historico"],
            data_source="historico",
        )

    def _handle_rival_main_role(self, question, normalized):
        target = self._target_player(question)
        profile = target.get("profile")
        if not profile:
            return self._response(target["error"], "rival_main_role", False)
        role = profile.get("main_role")
        if not role:
            return self._response(
                f"No hay suficientes partidas de {target['riot_id']} en la muestra.",
                "rival_main_role", False,
            )
        return self._response(
            f"{target['riot_id']} juega mas frecuentemente {ROLE_LABELS_ES.get(role, role)} "
            f"en su muestra reciente ({profile.get('games', '?')} partidas descargadas).",
            "rival_main_role", True, sources=[target.get("origin") or "historico"],
            data_source="historico",
        )

    def _handle_style(self, question, normalized):
        explicit_id = self._extract_riot_id(question)
        about_other = explicit_id is not None or any(
            word in normalized for word in ("rival", "enemigo", "su estilo", " su ")
        )
        if about_other:
            target = self._target_player(question)
            profile = target.get("profile")
            if not profile:
                return self._response(target["error"], "style", False)
            style = profile.get("style") or {}
            subject = target["riot_id"]
        else:
            puuid = self.my_puuid_fn()
            if not puuid:
                return self._response(
                    "Aun no he resuelto tu PUUID (falta ingesta o API key); no puedo "
                    "clasificar el estilo.",
                    "style", False,
                )
            style = classify_style(self.repo.player_participants_df(puuid))
            subject = "Tu estilo reciente"
        if not style or not style.get("style"):
            return self._response(
                "No hay partidas suficientes en la muestra local para clasificar el estilo.",
                "style", False,
            )
        evidence = style.get("evidence", {})
        return self._response(
            f"{subject}: {style['label']} (metodo: {style['method']}, "
            f"{style['games']} partidas). Evidencia: "
            f"{evidence.get('kills_assists_por_min', '?')} K+A/min, "
            f"{evidence.get('muertes_por_min', '?')} muertes/min, "
            f"{evidence.get('damage_share', '?')} de damage share.",
            "style", True, sources=["historico"], data_source="historico",
        )

    def _handle_player_profile(self, question, normalized):
        target = self._target_player(question)
        profile = target.get("profile")
        if not profile:
            return self._response(target["error"], "player_profile", False)
        form = profile.get("recent_form", {}) or {}
        style = profile.get("style") or {}
        role = profile.get("main_role_label") or ROLE_LABELS_ES.get(profile.get("main_role"), None)
        lines = [f"Perfil de {target['riot_id']} (muestra: {profile.get('games', '?')} "
                 "partidas ranked descargadas):"]
        if role:
            lines.append(f"- Rol mas frecuente: {role}")
        if form.get("games"):
            lines.append(
                f"- Forma reciente: winrate {form.get('winrate', 0):.0%} en "
                f"{form['games']} partidas, KDA {form.get('kda', '?')}, "
                f"{form.get('cs_per_min', '?')} CS/min"
            )
        if style.get("label"):
            lines.append(f"- Estilo: {style['label']}")
        tops = profile.get("top_champions", [])
        if tops:
            champs = ", ".join(
                f"{c['name']} ({c['games']} partidas, {c['winrate']:.0%} WR)" for c in tops
            )
            lines.append(f"- Campeones mas jugados: {champs}")
        return self._response(
            "\n".join(lines), "player_profile", True,
            sources=[target.get("origin") or "historico"], data_source="historico",
        )

    def _handle_objective(self, question, normalized):
        _, recommendations = self._live()
        objectives = (recommendations or {}).get("objectives", [])
        useful = [o for o in objectives if o.get("title") != "Sin partida activa"]
        if not useful:
            return self._response(
                "Sin partida activa no hay objetivos que priorizar. Abre una partida "
                "y vuelve a preguntar.",
                "objective", False,
            )
        lines = [f"- {o['title']}: {o['explanation']}" for o in useful]
        return self._response("\n".join(lines), "objective", True, sources=["live_client"])

    def _handle_win_probability(self, question, normalized):
        _, recommendations = self._live()
        wp = (recommendations or {}).get("win_probability") or {}
        if wp.get("probability") is None:
            return self._response(
                "No hay partida activa, asi que no hay probabilidad que estimar.",
                "win_probability", False,
            )
        warnings = " ".join(wp.get("warnings", []))
        return self._response(
            f"Probabilidad estimada de victoria: {wp['probability']:.0%} "
            f"(confianza {wp.get('confidence')}, metodo: {wp.get('method')}). {warnings}".strip(),
            "win_probability", True, sources=["mixto"],
        )

    def _handle_match_review(self, question, normalized):
        """Revision post-partida: 5 lineas + puntos de inflexion, con la
        curva de probabilidad reconstruida desde la timeline real."""
        if self.review_service is None:
            return self._response(
                "La revision post-partida no esta disponible en esta instancia.",
                "match_review", False,
            )
        puuid = self.my_puuid_fn()
        if not puuid:
            return self._response(
                "Aun no he resuelto tu PUUID (revisa GAME_NAME/TAG_LINE y corre "
                "la ingesta de historial); sin el no puedo revisar tus partidas.",
                "match_review", False,
            )
        review = self.review_service.review_last(puuid)
        if not review.get("available"):
            return self._response(
                review.get("reason", "No pude reconstruir la ultima partida."),
                "match_review", False,
            )
        lines = list(review.get("summary_lines", []))
        extra_points = [
            p["description"] for p in review.get("turning_points", [])[1:]
        ]
        if extra_points:
            lines.append("Otros puntos de inflexion:")
            lines.extend(f"- {d}" for d in extra_points)
        header = (
            f"Revision de tu ultima partida ({review.get('champion')}"
            + (f" vs {review['opponent_champion']}" if review.get("opponent_champion") else "")
            + "):"
        )
        return self._response(
            header + "\n" + "\n".join(lines),
            "match_review", True,
            sources=["timeline_match_v5", review.get("method", "heuristico")],
            data_source="historico",
        )

    def _handle_weekly_patterns(self, question, normalized):
        """Patrones agregados sobre las ultimas partidas revisadas."""
        if self.review_service is None:
            return self._response(
                "El analisis de patrones no esta disponible en esta instancia.",
                "weekly_patterns", False,
            )
        puuid = self.my_puuid_fn()
        if not puuid:
            return self._response(
                "Aun no he resuelto tu PUUID; sin el no puedo agregarte patrones.",
                "weekly_patterns", False,
            )
        patterns = self.review_service.patterns(puuid, limit=20)
        if not patterns.get("available"):
            return self._response(
                patterns.get("reason", "No hay partidas suficientes para detectar patrones."),
                "weekly_patterns", False,
            )
        lines = [
            f"Patrones de tus ultimas {patterns['games']} partidas "
            f"(winrate {patterns['winrate']:.0%}, {patterns['throws']} throw(s), "
            f"{patterns['comebacks']} comeback(s)):"
        ]
        lines.extend(f"- {insight}" for insight in patterns.get("insights", []))
        lines.extend(f"({w})" for w in patterns.get("warnings", []))
        return self._response(
            "\n".join(lines),
            "weekly_patterns", True,
            sources=["timeline_match_v5", "historico"],
            data_source="historico",
        )

    def _handle_game_basics(self, question, normalized):
        knowledge_answer = self._answer_with_knowledge(question)
        if knowledge_answer:
            return knowledge_answer

        # El tema o atributo pedido no esta cubierto por el corpus. Por
        # ejemplo, conocer un texto sobre Baron no implica saber su color.
        llm_answer = self._llm_answer(question)
        if llm_answer:
            return llm_answer
        return self._response(
            "No tengo una entrada confiable en la base local para esa pregunta.",
            "game_basics", False, sources=[], data_source="historico",
        )

    def _answer_with_knowledge(self, question: str) -> dict[str, Any] | None:
        """RAG: recupera pasajes y, si hay LLM, genera sobre ese contexto."""
        documents = self.knowledge.search(question, top_k=3)
        if not documents:
            return None

        sources = [doc["source"] for doc in documents]
        context = "\n\n".join(
            f"FUENTE: {doc['title']}\n{doc['text']}"
            for doc in documents
        )
        llm_answer = self._llm_answer(
            question,
            context_note=context,
            intent="game_basics",
            extra_sources=sources,
        )
        if llm_answer:
            return llm_answer

        # El chat sigue funcionando sin API key o durante una caida del LLM.
        best = documents[0]
        return self._response(
            f"{best['title']}: {best['text']}",
            "game_basics", True, sources=[best["source"]], data_source="historico",
        )

    def _llm_answer(
        self,
        question: str,
        context_note: str | None = None,
        intent: str = "llm_general",
        extra_sources: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Respuesta del LLM opcional, o None si no aplica/no responde."""
        if self.llm is None or not self.llm.available():
            return None
        answer = self.llm.ask(question, context_note=context_note)
        if not answer:
            return None
        sources = list(extra_sources or [])
        sources.append(f"llm_{self.llm.provider}")
        return self._response(
            answer + "\n\n(Respuesta generada por IA; puede contener errores.)",
            intent, True,
            sources=sources, data_source="mixto" if extra_sources else "llm",
        )

    def _handle_fallback(self, question: str):
        # Un termino conocido puede llegar aqui aunque la forma de la pregunta
        # no figure en INTENTS (p. ej. "quien es Nasus").
        knowledge_answer = self._answer_with_knowledge(question)
        if knowledge_answer:
            return knowledge_answer

        # Las preguntas claramente externas no se mandan al proveedor. Esto
        # evita respuestas sobre tecnologia, politica, salud, etc. y tampoco
        # consume tokens del LLM para rechazarlas.
        if not self._is_lol_question(question):
            return self._response(
                self.OUT_OF_SCOPE_MESSAGE,
                "out_of_scope", False, sources=[], data_source="mixto",
            )

        # Antes de la ayuda generica, el LLM cubre lore y preguntas abiertas
        # que no estan en el corpus local ni en los intents deterministas.
        llm_answer = self._llm_answer(question)
        if llm_answer:
            return llm_answer
        return self._response(
            "Puedo responder sobre: que item comprar, que linea gankear, tipo de "
            "danio de un campeon, historial/campeones/rol/estilo del rival o de "
            "CUALQUIER jugador si escribes su Riot ID (ej. 'perfil de Faker#KR1'), "
            "objetivos a priorizar, probabilidad de victoria, revision de tu "
            "ultima partida ('¿donde se perdio?') y patrones semanales "
            "('¿en que estoy fallando?'). Todo con datos "
            "reales (Riot API, historial local, partida en vivo y Data Dragon); "
            "si un dato no existe, te lo dire. Configura LLM_PROVIDER y "
            "LLM_API_KEY en el .env para que ademas pueda responder dudas "
            "abiertas de mecanicas y lore.",
            "fallback", True,
        )

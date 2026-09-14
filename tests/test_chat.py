"""Tests del chatbot: routing de intents y honestidad sin datos."""
import pandas as pd
import pytest

from app.chat.chat_service import ChatService


class FakeRepo:
    def __init__(self, participants):
        self._participants = participants

    def participants_df(self, enriched=True):
        return self._participants

    def player_participants_df(self, puuid):
        df = self._participants
        return df[df["puuid"] == puuid] if not df.empty else df


@pytest.fixture
def chat(sample_participants, fake_ddragon):
    recommendations = {
        "items": [{
            "title": "Considera Item 3111", "explanation": "por el danio magico enemigo",
            "extra": {"item_id": 3111}, "data_source": "live_client",
        }],
        "ganks": [{
            "title": "1. Gank hacia Top", "explanation": "rival muerto",
            "extra": {"role": "TOP"},
        }],
        "objectives": [{"title": "Dragon disponible", "explanation": "ventana activa"}],
        "win_probability": {"probability": 0.62, "confidence": "baja", "method": "mixto", "warnings": []},
    }
    snapshot = {"me": {"champion": "Ahri"}, "direct_rival": None}
    return ChatService(
        repo=FakeRepo(sample_participants),
        ddragon=fake_ddragon,
        scout=None,
        live_state_fn=lambda: (snapshot, recommendations),
        my_puuid_fn=lambda: "me",
    )


def test_item_intent(chat):
    res = chat.answer("¿Qué item debo comprar ahora?")
    assert res["intent"] == "item"
    assert res["data_available"] is True
    assert "3111" in res["answer"]


def test_gank_intent(chat):
    res = chat.answer("¿Qué línea debo priorizar para gank?")
    assert res["intent"] == "gank"
    assert "Top" in res["answer"]


def test_damage_type_with_champion(chat):
    res = chat.answer("¿Qué tipo de daño le afecta más a Malphite?")
    assert res["intent"] == "damage_type"
    assert res["data_available"] is True
    assert "Malphite" in res["answer"]


def test_style_intent_self(chat):
    res = chat.answer("¿Mi estilo de juego reciente es agresivo, defensivo o neutral?")
    assert res["intent"] == "style"
    assert res["data_available"] is True


def test_win_probability_intent(chat):
    res = chat.answer("¿Cuál es mi probabilidad de ganar?")
    assert res["intent"] == "win_probability"
    assert "62%" in res["answer"]


def test_rival_without_live_game_is_honest(chat):
    res = chat.answer("¿Cuántas partidas ha jugado mi rival con el campeón actual?")
    assert res["intent"] == "rival_champion_games"
    assert res["data_available"] is False
    # Debe orientar al usuario a preguntar por Riot ID
    assert "Riot ID" in res["answer"]


def test_arbitrary_player_from_local_history(chat):
    """Con Riot ID explicito responde aunque no haya partida activa
    (los participantes seed usan riotIdGameName=puuid, tagline TST)."""
    res = chat.answer("¿Cuáles son los campeones más jugados de player_red_2#TST?")
    assert res["intent"] == "rival_top_champions"
    assert res["data_available"] is True
    assert "player_red_2#TST" in res["answer"]


def test_player_profile_intent(chat):
    res = chat.answer("perfil de player_red_2#TST")
    assert res["intent"] == "player_profile"
    assert res["data_available"] is True
    assert "Rol" in res["answer"] or "Campeones" in res["answer"]


def test_unknown_player_is_honest(chat):
    res = chat.answer("perfil de NoExiste#XXX")
    assert res["intent"] == "player_profile"
    assert res["data_available"] is False


def test_out_of_scope_fallback(chat):
    res = chat.answer("hola que tal")
    assert res["intent"] == "out_of_scope"
    assert "League of Legends" in res["answer"]


class FakeLLM:
    provider = "fake"

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def available(self):
        return True

    def ask(self, question, context_note=None):
        self.calls.append({"question": question, "context_note": context_note})
        return self.answer


def test_runeterra_uses_retrieved_context_in_llm(chat):
    llm = FakeLLM("Runaterra es el mundo de League of Legends.")
    chat.llm = llm

    res = chat.answer("Que es Runaterra?")

    assert res["intent"] == "game_basics"
    assert "rag_local:Runaterra" in res["sources"]
    assert "llm_fake" in res["sources"]
    assert "Runaterra" in llm.calls[0]["context_note"]


def test_unsupported_baron_attribute_skips_irrelevant_context(chat):
    llm = FakeLLM("El Baron Nashor es principalmente violeta.")
    chat.llm = llm

    res = chat.answer("De que color es el Baron?")

    assert res["intent"] == "llm_general"
    assert res["sources"] == ["llm_fake"]
    assert llm.calls[0]["context_note"] is None


def test_known_champion_lore_works_without_llm(chat):
    res = chat.answer("Quien es Nasus?")

    assert res["intent"] == "game_basics"
    assert res["data_available"] is True
    assert "Ascendido de Shurima" in res["answer"]


@pytest.mark.parametrize("question", [
    "Que es Kubeflow?",
    "Explicame Kubernetes",
    "Quien es el presidente de Mexico?",
    "Que es la Luna?",
])
def test_external_question_is_not_sent_to_llm(chat, question):
    llm = FakeLLM("Esta respuesta externa no debe mostrarse.")
    chat.llm = llm

    res = chat.answer(question)

    assert res["intent"] == "out_of_scope"
    assert res["data_available"] is False
    assert res["sources"] == []
    assert "League of Legends" in res["answer"]
    assert llm.calls == []


def test_open_lol_question_can_still_reach_llm(chat):
    llm = FakeLLM("Wave management es el control de las oleadas.")
    chat.llm = llm

    res = chat.answer("Como funciona el wave management?")

    assert res["intent"] == "llm_general"
    assert res["sources"] == ["llm_fake"]
    assert len(llm.calls) == 1

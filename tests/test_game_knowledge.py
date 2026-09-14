"""Tests de recuperacion y control de falsos positivos del RAG local."""
from app.chat.game_knowledge import GameKnowledgeBase


def test_retrieves_known_concept_ignoring_spanish_question_words():
    knowledge = GameKnowledgeBase()

    results = knowledge.search("Que es Runaterra?")

    assert results
    assert results[0]["title"] == "Runaterra"


def test_does_not_confuse_topic_with_unsupported_attribute():
    knowledge = GameKnowledgeBase()

    assert knowledge.search("De que color es el Baron?") == []


def test_unknown_concept_does_not_match_only_by_stopwords():
    knowledge = GameKnowledgeBase()

    assert knowledge.search("Que es la fotosintesis?") == []


def test_returns_multiple_related_documents_for_combined_question():
    knowledge = GameKnowledgeBase()

    results = knowledge.search("Como ayudan la vision y prioridad al dragon?")

    assert results
    assert any(result["title"] == "Dragon" for result in results)

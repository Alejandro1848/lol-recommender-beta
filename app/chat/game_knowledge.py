"""RAG local para conceptos basicos de League of Legends.

No depende de una API externa. Usa un corpus pequeno y recuperacion TF-IDF
para responder preguntas generales sin alucinar datos de la partida.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


@dataclass(frozen=True)
class KnowledgeDoc:
    title: str
    text: str
    tags: tuple[str, ...]


# Scikit-learn no incluye stopwords en espanol. Esta lista evita que palabras
# de la forma de la pregunta ("que", "es", "como") creen coincidencias
# artificiales cuando el concepto consultado no existe en el corpus.
SPANISH_STOP_WORDS = frozenset({
    "a", "al", "algo", "ante", "como", "con", "contra", "cual", "cuando",
    "de", "del", "desde", "debo", "deberia", "donde", "el", "ella", "en",
    "es", "esta", "este", "esto", "hacer", "hay", "la", "las", "lo", "los",
    "me", "mi", "para", "pero", "por", "puedo", "que", "quien", "se", "ser",
    "si", "sin", "sobre", "son", "su", "sus", "un", "una", "y",
})


DOCS = [
    KnowledgeDoc(
        "Prioridad de lineas",
        "Prioridad de linea significa que tu aliado puede empujar primero y moverse antes que su rival. "
        "Antes de iniciar dragon, heraldo, grubs o una invasion, revisa que mid y la linea cercana puedan rotar.",
        ("macro", "lineas", "objetivos"),
    ),
    KnowledgeDoc(
        "Dragon",
        "El dragon es un objetivo del lado inferior. Conviene tomarlo cuando bot/mid tienen prioridad, "
        "el jungla enemigo esta lejos o muerto, y tu equipo puede llegar primero a vision.",
        ("dragon", "objetivos"),
    ),
    KnowledgeDoc(
        "Monstruos del abismo",
        "Los monstruos del abismo o void grubs son un objetivo temprano del lado superior. Ayudan a tirar torres "
        "por su mejora de dano a estructuras. Suelen ser mejores si top/mid tienen presion o tu composicion quiere split push.",
        ("grubs", "objetivos", "top"),
    ),
    KnowledgeDoc(
        "Heraldo",
        "El Heraldo sirve para convertir prioridad en placas, primera torre o presion de mapa. Es especialmente valioso "
        "si puedes usarlo en una linea ganadora o abrir mid.",
        ("heraldo", "objetivos"),
    ),
    KnowledgeDoc(
        "Baron",
        "Baron no debe iniciarse a ciegas. Es mejor cuando tienes vision, un pick, jungla enemigo muerto o ventaja clara. "
        "Si vas detras, muchas veces conviene forzar vision y buscar pelea antes de empezarlo.",
        ("baron", "objetivos"),
    ),
    KnowledgeDoc(
        "Gank",
        "Un buen gank prioriza lineas con control de oleada, enemigo sin movilidad, rival adelantado, aliado con CC o una recompensa alta. "
        "Si el enemigo esta fed, pararlo puede valer mas que atacar una linea ya ganada.",
        ("gank", "jungla"),
    ),
    KnowledgeDoc(
        "Vision",
        "La vision convierte objetivos en jugadas seguras. Antes de objetivos, limpia wards con lente/control ward y coloca vision profunda "
        "en entradas por donde llegara el rival.",
        ("vision", "macro"),
    ),
    KnowledgeDoc(
        "Tempo",
        "Tempo es llegar antes que el rival porque acabas de empujar, resetear o forzar una muerte. Si tu equipo no tiene tempo, iniciar objetivos grandes suele ser arriesgado.",
        ("tempo", "macro"),
    ),
    KnowledgeDoc(
        "Runaterra",
        "Runaterra es el mundo ficticio donde transcurren las historias de League of Legends. "
        "Incluye regiones con culturas y conflictos propios, como Demacia, Noxus, Jonia, "
        "Shurima, Freljord, Piltover, Zaun y el Monte Targon.",
        ("runaterra", "lore", "regiones"),
    ),
    KnowledgeDoc(
        "Diana",
        "Diana es una guerrera de los Lunari y portadora del poder del Aspecto de la Luna. "
        "Su historia esta ligada al Monte Targon, a la fe Lunari y a su conflicto con las "
        "creencias de los Solari.",
        ("diana", "lunari", "targon", "lore", "campeones"),
    ),
    KnowledgeDoc(
        "Nasus",
        "Nasus es un Ascendido de Shurima, erudito y estratega que fue transformado mediante "
        "el Disco Solar. Es hermano de Renekton y busca proteger y reconstruir el legado de "
        "Shurima.",
        ("nasus", "shurima", "ascendidos", "renekton", "lore", "campeones"),
    ),
]


class GameKnowledgeBase:
    def __init__(self, docs: list[KnowledgeDoc] | None = None):
        self.docs = docs or DOCS
        self.vectorizer = TfidfVectorizer(
            lowercase=True,
            strip_accents="unicode",
            ngram_range=(1, 2),
            stop_words=sorted(SPANISH_STOP_WORDS),
        )
        corpus = [f"{d.title}. {d.text} {' '.join(d.tags)}" for d in self.docs]
        self.matrix = self.vectorizer.fit_transform(corpus)
        self._doc_terms = [self._meaningful_terms(text) for text in corpus]
        self._doc_anchors = [
            self._meaningful_terms(f"{doc.title} {' '.join(doc.tags)}")
            for doc in self.docs
        ]

    @staticmethod
    def _meaningful_terms(text: str) -> set[str]:
        normalized = unicodedata.normalize("NFD", text.lower())
        normalized = "".join(
            char for char in normalized if unicodedata.category(char) != "Mn"
        )
        return {
            token
            for token in re.findall(r"(?u)\b\w\w+\b", normalized)
            if token not in SPANISH_STOP_WORDS
        }

    def search(
        self,
        question: str,
        top_k: int = 3,
        min_score: float = 0.12,
        min_coverage: float = 0.6,
    ) -> list[dict]:
        """Recupera pasajes relevantes sin confundir tema con respuesta.

        Ademas de similitud TF-IDF se exige que el documento cubra una parte
        suficiente de los terminos significativos de la pregunta. Asi,
        "color del Baron" no acepta un texto que solo menciona "Baron".
        """
        question_terms = self._meaningful_terms(question)
        if not question_terms:
            return []

        query = self.vectorizer.transform([question])
        if query.nnz == 0:
            return []

        scores = cosine_similarity(query, self.matrix)[0]
        ranked_indices = scores.argsort()[::-1]
        results = []
        for index in ranked_indices:
            score = float(scores[index])
            if score < min_score:
                break
            # Al menos un concepto central (titulo o tag), no solo una palabra
            # incidental del cuerpo, debe aparecer en la pregunta.
            if not question_terms & self._doc_anchors[index]:
                continue
            coverage = len(question_terms & self._doc_terms[index]) / len(question_terms)
            if coverage < min_coverage:
                continue
            doc = self.docs[int(index)]
            results.append({
                "title": doc.title,
                "text": doc.text,
                "tags": list(doc.tags),
                "score": round(score, 3),
                "coverage": round(coverage, 3),
                "source": f"rag_local:{doc.title}",
            })
            if len(results) >= max(1, top_k):
                break
        return results

    def answer(self, question: str, min_score: float = 0.12) -> dict:
        """Compatibilidad extractiva para instalaciones sin un LLM."""
        results = self.search(question, top_k=1, min_score=min_score)
        if not results:
            return {
                "available": False,
                "answer": "No tengo una entrada confiable en la base local para esa pregunta basica.",
                "sources": [],
                "score": 0.0,
            }
        result = results[0]
        return {
            "available": True,
            "answer": f"{result['title']}: {result['text']}",
            "sources": [result["source"]],
            "score": result["score"],
        }

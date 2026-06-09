"""
Recuperador (retrieval) del RAG.

Modo principal — semántico: embebe la pregunta con Gemini y busca los
documentos más cercanos en ChromaDB.

Modo de respaldo — léxico: si no hay índice de ChromaDB o no hay API key,
construye los documentos en memoria y los puntúa por solapamiento de términos.
Esto permite que el agente funcione (de forma degradada) sin haber indexado.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from agent.config import CHROMA_COLLECTION, CHROMA_DIR, TOP_K, gemini_disponible
from agent.data_source import cargar_tarifas
from agent.documents import Documento, construir_documentos

logger = logging.getLogger(__name__)

_PALABRAS_AGREGADAS = {
    "bajo", "barato", "minimo", "menor", "alto", "caro", "maximo", "mayor",
    "promedio", "media", "ranking", "comparar", "comparativo", "mercado",
}
_STOPWORDS = {
    "el", "la", "los", "las", "de", "del", "en", "para", "por", "un", "una",
    "y", "o", "a", "es", "que", "cual", "cuales", "cuanto", "como", "mas",
    "este", "esta", "ese", "esa", "su", "al", "se", "con", "ha", "han",
}

_docs_cache: list[Documento] | None = None


@dataclass
class Fragmento:
    texto: str
    metadata: dict
    fuente: str
    score: float


def _normalizar_texto(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s


def _tokens(s: str) -> set[str]:
    palabras = re.findall(r"[a-z0-9]+", _normalizar_texto(s))
    return {p for p in palabras if p not in _STOPWORDS and len(p) > 1}


def etiqueta_fuente(meta: dict) -> str:
    """Construye una cita corta y legible a partir de la metadata del documento."""
    tipo = meta.get("tipo")
    nivel = meta.get("nivel_tension")
    ciclo = meta.get("ciclo", "")
    ciclo_legible = f"{ciclo[:4]}-{ciclo[4:]}" if len(str(ciclo)) == 6 else ciclo
    if tipo == "resumen":
        return f"Resumen de mercado · {ciclo_legible} · NT{nivel}"
    if tipo == "evolucion":
        return f"Evolución {meta.get('comercializador')} · NT{nivel}"
    return f"{meta.get('comercializador')} · {ciclo_legible} · NT{nivel}"


# ── Modo semántico (ChromaDB + Gemini) ───────────────────────────────────────
def _get_collection_lectura():
    """Abre la colección de ChromaDB en solo-lectura; None si no existe/está vacía."""
    if not CHROMA_DIR.exists():
        return None
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        coleccion = client.get_collection(CHROMA_COLLECTION)
        if coleccion.count() == 0:
            return None
        return coleccion
    except Exception as exc:
        logger.info("ChromaDB no disponible para lectura (%s).", exc.__class__.__name__)
        return None


def _recuperar_semantico(pregunta: str, top_k: int) -> list[Fragmento] | None:
    coleccion = _get_collection_lectura()
    if coleccion is None or not gemini_disponible():
        return None
    try:
        from agent.embeddings import embed_textos

        vec = embed_textos([pregunta], es_consulta=True)[0]
        res = coleccion.query(query_embeddings=[vec], n_results=top_k)
    except Exception as exc:
        logger.warning("Fallo en recuperación semántica (%s) — fallback léxico.", exc)
        return None

    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[None] * len(docs)])[0]
    fragmentos = []
    for texto, meta, dist in zip(docs, metas, dists):
        score = 1.0 - dist if isinstance(dist, (int, float)) else 0.0
        fragmentos.append(Fragmento(texto, meta or {}, etiqueta_fuente(meta or {}), score))
    return fragmentos


# ── Modo de respaldo (léxico) ────────────────────────────────────────────────
def _documentos_memoria() -> list[Documento]:
    global _docs_cache
    if _docs_cache is None:
        _docs_cache = construir_documentos(cargar_tarifas())
    return _docs_cache


def _recuperar_lexico(pregunta: str, top_k: int) -> list[Fragmento]:
    documentos = _documentos_memoria()
    if not documentos:
        return []
    q_tokens = _tokens(pregunta)
    es_agregada = bool(q_tokens & _PALABRAS_AGREGADAS)

    puntuados: list[tuple[float, Documento]] = []
    for doc in documentos:
        d_tokens = _tokens(doc.texto)
        solapamiento = len(q_tokens & d_tokens)
        score = solapamiento / (len(q_tokens) + 1)
        # Si la pregunta es de tipo agregado, priorizar los resúmenes de mercado.
        if es_agregada and doc.metadata.get("tipo") == "resumen":
            score += 0.5
        puntuados.append((score, doc))

    puntuados.sort(key=lambda x: x[0], reverse=True)
    return [
        Fragmento(doc.texto, doc.metadata, etiqueta_fuente(doc.metadata), score)
        for score, doc in puntuados[:top_k]
        if score > 0
    ]


def recuperar(pregunta: str, top_k: int | None = None) -> list[Fragmento]:
    """
    Recupera los fragmentos más relevantes para la pregunta.

    Usa el modo semántico (ChromaDB + Gemini) si está disponible; de lo
    contrario, cae al modo léxico en memoria.
    """
    top_k = top_k or TOP_K
    fragmentos = _recuperar_semantico(pregunta, top_k)
    if fragmentos is not None and fragmentos:
        return fragmentos
    return _recuperar_lexico(pregunta, top_k)


def reset_cache() -> None:
    """Limpia la caché de documentos en memoria (tras re-indexar)."""
    global _docs_cache
    _docs_cache = None

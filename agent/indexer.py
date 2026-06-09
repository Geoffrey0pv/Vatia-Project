"""
Indexador del RAG: carga las tarifas, construye documentos, genera embeddings
con Gemini y los persiste en ChromaDB.

Es idempotente: re-ejecutar recrea la colección desde cero (en < 1 min para el
volumen actual de datos), por lo que puede invocarse tras cada carga ETL.

Uso:
    python -m agent.indexer
"""

from __future__ import annotations

import logging

from agent.config import CHROMA_COLLECTION, CHROMA_DIR, EMBED_DIM
from agent.data_source import cargar_tarifas
from agent.documents import Documento, construir_documentos
from agent.embeddings import embed_textos

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _get_collection(reset: bool = False):
    """Abre (o recrea) la colección persistente de ChromaDB."""
    import chromadb

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    if reset:
        try:
            client.delete_collection(CHROMA_COLLECTION)
        except Exception:
            pass  # no existía aún

    return client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine", "dim": EMBED_DIM},
    )


def _saneizar_metadata(meta: dict) -> dict:
    """ChromaDB no admite valores None en metadata: se omiten esas claves."""
    return {k: v for k, v in meta.items() if v is not None}


def indexar(documentos: list[Documento] | None = None) -> int:
    """
    Genera embeddings de los documentos y los guarda en ChromaDB.

    Args:
        documentos: Documentos a indexar. Si None, se construyen desde la
                    fuente de datos (Postgres/CSV).

    Returns:
        Número de documentos indexados.
    """
    if documentos is None:
        df = cargar_tarifas()
        documentos = construir_documentos(df)

    if not documentos:
        logger.warning("No hay documentos para indexar (¿base de datos vacía?).")
        return 0

    logger.info("Generando embeddings de %d documentos con Gemini…", len(documentos))
    textos = [d.texto for d in documentos]
    vectores = embed_textos(textos, es_consulta=False)

    coleccion = _get_collection(reset=True)
    coleccion.add(
        ids=[d.id for d in documentos],
        documents=textos,
        embeddings=vectores,
        metadatas=[_saneizar_metadata(d.metadata) for d in documentos],
    )

    logger.info(
        "✔ Indexación completa: %d documentos en la colección '%s' (%s).",
        len(documentos),
        CHROMA_COLLECTION,
        CHROMA_DIR,
    )
    return len(documentos)


if __name__ == "__main__":
    indexar()

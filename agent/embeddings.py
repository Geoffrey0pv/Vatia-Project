"""
Cliente de Gemini y utilidades de embeddings.

Encapsula el SDK ``google-genai`` para que el resto del agente no dependa de
sus detalles. Importación perezosa: el SDK solo se carga al usarse, de modo
que los módulos que solo construyen documentos no requieren la dependencia.
"""

from __future__ import annotations

import math

from agent.config import EMBED_DIM, GEMINI_API_KEY, GEMINI_EMBED_MODEL, gemini_disponible

# Lotes pequeños para no exceder límites de la API de embeddings.
_BATCH = 64
_cliente_cache = None


def get_client():
    """Devuelve un cliente de Gemini cacheado. Lanza si falta la API key."""
    global _cliente_cache
    if not gemini_disponible():
        raise RuntimeError(
            "GEMINI_API_KEY no está configurada. Añádela a tu archivo .env "
            "(ver .env.example)."
        )
    if _cliente_cache is None:
        from google import genai  # import perezoso

        _cliente_cache = genai.Client(api_key=GEMINI_API_KEY)
    return _cliente_cache


def _normalizar(vec: list[float]) -> list[float]:
    """Normaliza el vector a norma unitaria (recomendado si dim < 3072)."""
    norma = math.sqrt(sum(v * v for v in vec))
    if norma == 0:
        return vec
    return [v / norma for v in vec]


def embed_textos(textos: list[str], *, es_consulta: bool = False) -> list[list[float]]:
    """
    Genera embeddings para una lista de textos con el modelo de Gemini.

    Args:
        textos:     Lista de cadenas a vectorizar.
        es_consulta: True para una pregunta (task_type RETRIEVAL_QUERY),
                     False para documentos a indexar (RETRIEVAL_DOCUMENT).

    Returns:
        Lista de vectores (uno por texto), normalizados a norma unitaria.
    """
    from google.genai import types

    client = get_client()
    task_type = "RETRIEVAL_QUERY" if es_consulta else "RETRIEVAL_DOCUMENT"
    config = types.EmbedContentConfig(
        task_type=task_type,
        output_dimensionality=EMBED_DIM,
    )

    vectores: list[list[float]] = []
    for inicio in range(0, len(textos), _BATCH):
        lote = textos[inicio : inicio + _BATCH]
        resp = client.models.embed_content(
            model=GEMINI_EMBED_MODEL,
            contents=lote,
            config=config,
        )
        vectores.extend(_normalizar(list(e.values)) for e in resp.embeddings)
    return vectores

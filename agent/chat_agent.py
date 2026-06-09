"""
Orquestador del agente RAG.

Flujo: pregunta → recuperación (ChromaDB/léxico) → construcción del contexto →
generación con Gemini (incluyendo historial) → respuesta + fuentes citadas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent.config import GEMINI_MODEL, MAX_HISTORIAL, TEMPERATURE, gemini_disponible
from agent.prompts import SYSTEM_PROMPT, construir_prompt_usuario
from agent.retriever import Fragmento, recuperar

logger = logging.getLogger(__name__)


@dataclass
class Respuesta:
    texto: str
    fuentes: list[str] = field(default_factory=list)
    fragmentos: list[Fragmento] = field(default_factory=list)
    ok: bool = True


def _construir_contexto(fragmentos: list[Fragmento]) -> str:
    """Numera los fragmentos recuperados para inyectarlos como contexto."""
    if not fragmentos:
        return "(No se encontraron datos relevantes para esta pregunta.)"
    return "\n".join(
        f"[{i}] ({f.fuente}) {f.texto}" for i, f in enumerate(fragmentos, start=1)
    )


def _fuentes_unicas(fragmentos: list[Fragmento]) -> list[str]:
    vistas: list[str] = []
    for f in fragmentos:
        if f.fuente not in vistas:
            vistas.append(f.fuente)
    return vistas


def _historial_a_contents(historial: list[dict] | None) -> list[dict]:
    """Convierte el historial de chat al formato de 'contents' de Gemini."""
    if not historial:
        return []
    contents: list[dict] = []
    for msg in historial[-MAX_HISTORIAL:]:
        rol = "model" if msg.get("role") == "assistant" else "user"
        texto = str(msg.get("content", "")).strip()
        if texto:
            contents.append({"role": rol, "parts": [{"text": texto}]})
    return contents


def responder(pregunta: str, historial: list[dict] | None = None) -> Respuesta:
    """
    Responde una pregunta sobre tarifas usando RAG + Gemini.

    Args:
        pregunta:  Pregunta del usuario en lenguaje natural.
        historial: Mensajes previos [{role, content}, ...] (sin la pregunta actual).

    Returns:
        :class:`Respuesta` con el texto, las fuentes citadas y los fragmentos.
    """
    pregunta = (pregunta or "").strip()
    if not pregunta:
        return Respuesta("Por favor, escribe una pregunta sobre las tarifas.", ok=False)

    if not gemini_disponible():
        return Respuesta(
            "⚠️ El agente no está configurado: falta `GEMINI_API_KEY` en el archivo "
            "`.env`. Añádela y reinicia la aplicación para activar el chat.",
            ok=False,
        )

    # 1. Recuperación de contexto.
    fragmentos = recuperar(pregunta)
    contexto = _construir_contexto(fragmentos)

    # 2. Construcción de la conversación para Gemini.
    contents = _historial_a_contents(historial)
    contents.append(
        {"role": "user", "parts": [{"text": construir_prompt_usuario(pregunta, contexto)}]}
    )

    # 3. Generación.
    try:
        from google.genai import types

        from agent.embeddings import get_client

        client = get_client()
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=TEMPERATURE,
            ),
        )
        texto = (resp.text or "").strip() or "No pude generar una respuesta."
    except Exception as exc:
        logger.error("Error llamando a Gemini: %s", exc, exc_info=True)
        return Respuesta(
            f"❌ Ocurrió un error al consultar el modelo Gemini "
            f"({exc.__class__.__name__}). Verifica tu `GEMINI_API_KEY` y el "
            f"modelo `{GEMINI_MODEL}`.",
            fragmentos=fragmentos,
            ok=False,
        )

    return Respuesta(
        texto=texto,
        fuentes=_fuentes_unicas(fragmentos),
        fragmentos=fragmentos,
    )

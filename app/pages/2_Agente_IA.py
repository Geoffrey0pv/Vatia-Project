"""
Página del Agente IA — Chat RAG sobre tarifas (Gemini 3.1 Flash Lite).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from agent.chat_agent import responder
from agent.config import GEMINI_MODEL, gemini_disponible
from app.styles.theme import MAIN_CSS, VATIA

st.set_page_config(page_title="Agente IA — VATIA", page_icon="🤖", layout="wide")
st.markdown(MAIN_CSS, unsafe_allow_html=True)

_HAY_KEY = gemini_disponible()

# ── Header ────────────────────────────────────────────────────────────────────
_badge = "LIVE" if _HAY_KEY else "SIN API KEY"
st.markdown(
    f"""
    <div class="vatia-header">
        <div class="vatia-header-left">
            <h1>🤖 v<span class="vatia-accent">A</span>tia
                <span style="font-weight:400; color:#D8FFB0;">AI</span>
                — Agente de Inteligencia Comercial</h1>
            <p>Consulta los datos de tarifas en lenguaje natural · Modelo
               <strong style="color:white;">{GEMINI_MODEL}</strong></p>
        </div>
        <div class="vatia-badge">{_badge}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Aviso si falta la API key ─────────────────────────────────────────────────
if not _HAY_KEY:
    st.warning(
        "**Falta la clave de Gemini.** Añade `GEMINI_API_KEY` a tu archivo `.env` "
        "y reinicia la app. Para activar la búsqueda semántica, ejecuta además "
        "`python -m agent.indexer`. Sin clave, el chat queda deshabilitado.",
        icon="⚠️",
    )

# ── Estado de la conversación ─────────────────────────────────────────────────
if "mensajes" not in st.session_state:
    st.session_state.mensajes = []
if "pendiente" not in st.session_state:
    st.session_state.pendiente = None

# ── Preguntas de ejemplo (clicables) ──────────────────────────────────────────
EJEMPLOS = [
    "¿Cuál es el CU más bajo para Nivel 2 este mes?",
    "¿Qué competidor tiene el D más alto en el último ciclo?",
    "¿Cómo ha variado el CU de EPM en Nivel 1?",
    "Compara el CU de los comercializadores en Nivel 3.",
]

if not st.session_state.mensajes:
    st.markdown('<p class="section-title">💡 Preguntas de ejemplo</p>', unsafe_allow_html=True)
    cols = st.columns(2)
    for i, ej in enumerate(EJEMPLOS):
        if cols[i % 2].button(ej, key=f"ej_{i}", use_container_width=True, disabled=not _HAY_KEY):
            st.session_state.pendiente = ej
            st.rerun()

# ── Render del historial ──────────────────────────────────────────────────────
for msg in st.session_state.mensajes:
    with st.chat_message(msg["role"], avatar="🤖" if msg["role"] == "assistant" else "👤"):
        st.markdown(msg["content"])
        if msg.get("fuentes"):
            with st.expander(f"📚 Fuentes consultadas ({len(msg['fuentes'])})"):
                for fte in msg["fuentes"]:
                    st.markdown(f"- {fte}")

# ── Entrada del usuario ───────────────────────────────────────────────────────
entrada = st.chat_input(
    "Haz una pregunta sobre las tarifas…" if _HAY_KEY else "Configura GEMINI_API_KEY para chatear",
    disabled=not _HAY_KEY,
)

pregunta = entrada or st.session_state.pendiente
st.session_state.pendiente = None

if pregunta:
    # Mostrar la pregunta del usuario.
    st.session_state.mensajes.append({"role": "user", "content": pregunta})
    with st.chat_message("user", avatar="👤"):
        st.markdown(pregunta)

    # Generar respuesta (el historial excluye la pregunta recién añadida).
    historial = st.session_state.mensajes[:-1]
    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("Consultando los datos…"):
            resp = responder(pregunta, historial=historial)
        st.markdown(resp.texto)
        if resp.fuentes:
            with st.expander(f"📚 Fuentes consultadas ({len(resp.fuentes)})"):
                for fte in resp.fuentes:
                    st.markdown(f"- {fte}")

    st.session_state.mensajes.append(
        {"role": "assistant", "content": resp.texto, "fuentes": resp.fuentes}
    )
    st.rerun()

# ── Botón para limpiar la conversación ────────────────────────────────────────
if st.session_state.mensajes and st.button("🗑️ Limpiar conversación"):
    st.session_state.mensajes = []
    st.rerun()

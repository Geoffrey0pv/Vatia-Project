"""
Página del Agente IA — Chat RAG
(Fase 2 — Por implementar)
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
from app.styles.theme import MAIN_CSS, VATIA

st.set_page_config(
    page_title="Agente IA — VATIA",
    page_icon="🤖",
    layout="wide",
)
st.markdown(MAIN_CSS, unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <div class="vatia-header">
        <div class="vatia-header-left">
            <h1>🤖 v<span class="vatia-accent">A</span>tia
                <span style="font-weight:400; color:#D8FFB0;">AI</span>
                — Agente de Inteligencia Comercial</h1>
            <p>Consulta los datos de tarifas en lenguaje natural</p>
        </div>
        <div class="vatia-badge">PRÓXIMAMENTE</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Placeholder UI ────────────────────────────────────────────────────────────
st.markdown(
    f"""
    <div style="
        background: {VATIA['bg_light']};
        border: 1px dashed {VATIA['border_accent']};
        border-radius: 14px;
        padding: 48px;
        text-align: center;
        margin: 32px 0;
    ">
        <div style="font-size: 3rem; margin-bottom: 16px;">🤖</div>
        <h3 style="color: {VATIA['text_dark']}; margin-bottom: 8px;">
            Agente IA en construcción
        </h3>
        <p style="color: {VATIA['text_muted']}; max-width: 480px; margin: 0 auto 24px;">
            El agente RAG (Retrieval-Augmented Generation) podrá responder preguntas
            sobre tarifas en lenguaje natural, citando las fuentes de datos.
        </p>
        <div style="
            display: inline-grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            text-align: left;
            margin: 0 auto;
        ">
    """,
    unsafe_allow_html=True,
)

ejemplos = [
    "¿Cuál es el CU más bajo para Nivel 2 este mes?",
    "¿Cómo ha variado el componente G en los últimos 3 meses?",
    "¿Cuánto cobramos de más vs. el promedio del mercado en NT1?",
    "¿Qué competidor tiene el D más alto este ciclo?",
]

cols = st.columns(2)
for i, ejemplo in enumerate(ejemplos):
    with cols[i % 2]:
        st.markdown(
            f"""
            <div style="
                background: white;
                border: 1px solid {VATIA['border']};
                border-left: 3px solid {VATIA['lime']};
                border-radius: 8px;
                padding: 12px 16px;
                font-size: 0.85rem;
                color: {VATIA['text_dark']};
                cursor: pointer;
                margin-bottom: 8px;
            ">
                💬 {ejemplo}
            </div>
            """,
            unsafe_allow_html=True,
        )

st.markdown("---")
st.info(
    "**Fase 2 — LangChain + ChromaDB + OpenAI GPT-4o mini.**  \n"
    "Requiere: `OPENAI_API_KEY` en `.env` y ejecutar el indexador con "
    "`python -m agent.indexer`.",
    icon="ℹ️",
)

# ── Chat input deshabilitado (placeholder) ────────────────────────────────────
st.markdown(
    f'<p class="section-title">Chat</p>',
    unsafe_allow_html=True,
)
st.chat_input("Haz una pregunta sobre las tarifas... (disponible en Fase 2)", disabled=True)

"""
Pruebas unitarias del agente IA RAG.

No requieren clave de Gemini ni red: usan datos sintéticos y verifican la
lógica de construcción de documentos, recuperación léxica y manejo de errores.
"""

from __future__ import annotations

import pandas as pd
import pytest

from agent import documents, retriever
from agent.chat_agent import responder
from agent.documents import construir_documentos
from agent.retriever import Fragmento, etiqueta_fuente, recuperar


@pytest.fixture
def df_tarifas() -> pd.DataFrame:
    """Dos comercializadores en un ciclo, Nivel 2, con CU distinto."""
    return pd.DataFrame(
        [
            {
                "fecha": "2026-01-01", "ciclo": "202601", "operador_red": "CENS",
                "comercializador": "CENS", "nivel_tension": 2, "tipo_red": None,
                "comb_nt": None, "dueno_red": "100% OPERADOR",
                "g": 298.9, "t": 52.9, "d": 198.4, "cv": 120.1, "pr": 23.0,
                "r": 19.3, "cu": 712.6,
            },
            {
                "fecha": "2026-01-01", "ciclo": "202601", "operador_red": "EPM",
                "comercializador": "EPM", "nivel_tension": 2, "tipo_red": None,
                "comb_nt": None, "dueno_red": "100% OPERADOR",
                "g": 250.0, "t": 40.0, "d": 100.0, "cv": 80.0, "pr": 20.0,
                "r": 10.0, "cu": 500.0,
            },
        ]
    )


class TestDocumentos:
    def test_genera_fila_y_resumen(self, df_tarifas):
        docs = construir_documentos(df_tarifas)
        tipos = {d.metadata["tipo"] for d in docs}
        assert "fila" in tipos
        assert "resumen" in tipos
        # 2 filas + 1 resumen (un solo ciclo×nivel, sin evolución).
        assert len(docs) == 3

    def test_resumen_identifica_cu_minimo(self, df_tarifas):
        docs = construir_documentos(df_tarifas)
        resumen = next(d for d in docs if d.metadata["tipo"] == "resumen")
        assert resumen.metadata["comercializador_min"] == "EPM"
        assert resumen.metadata["cu_min"] == 500.0
        assert "EPM" in resumen.texto

    def test_dataframe_vacio_no_falla(self):
        assert construir_documentos(pd.DataFrame()) == []


class TestRetrieverLexico:
    def test_pregunta_agregada_prioriza_resumen(self, df_tarifas, monkeypatch):
        docs = construir_documentos(df_tarifas)
        monkeypatch.setattr(retriever, "_docs_cache", docs)
        # Forzar modo léxico (sin ChromaDB ni API).
        monkeypatch.setattr(retriever, "_recuperar_semantico", lambda *a, **k: None)

        frags = recuperar("¿Cuál es el CU más bajo en Nivel 2?", top_k=3)
        assert frags, "debe recuperar al menos un fragmento"
        assert frags[0].metadata["tipo"] == "resumen"

    def test_retorna_fragmentos(self, df_tarifas, monkeypatch):
        docs = construir_documentos(df_tarifas)
        monkeypatch.setattr(retriever, "_docs_cache", docs)
        monkeypatch.setattr(retriever, "_recuperar_semantico", lambda *a, **k: None)
        frags = recuperar("tarifa de EPM", top_k=5)
        assert all(isinstance(f, Fragmento) for f in frags)


class TestEtiquetaFuente:
    def test_fila(self):
        meta = {"tipo": "fila", "comercializador": "CENS", "ciclo": "202601", "nivel_tension": 2}
        assert etiqueta_fuente(meta) == "CENS · 2026-01 · NT2"

    def test_resumen(self):
        meta = {"tipo": "resumen", "ciclo": "202601", "nivel_tension": 2}
        assert "Resumen de mercado" in etiqueta_fuente(meta)


class TestChatAgent:
    def test_sin_api_key_responde_amablemente(self, monkeypatch):
        import agent.chat_agent as ca

        monkeypatch.setattr(ca, "gemini_disponible", lambda: False)
        resp = responder("¿Cuál es el CU más bajo?")
        assert resp.ok is False
        assert "GEMINI_API_KEY" in resp.texto

    def test_pregunta_vacia(self):
        resp = responder("   ")
        assert resp.ok is False

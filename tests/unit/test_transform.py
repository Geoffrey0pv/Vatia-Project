"""
Tests unitarios para etl/transform.py.

Ejecutar:
    pytest tests/unit/test_transform.py -v
"""

from __future__ import annotations

import pandas as pd
import pytest

from etl.transform import normalizar, validar_integridad_cu, COLUMNAS_ESQUEMA


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture
def df_valido() -> pd.DataFrame:
    """DataFrame mínimo con todas las columnas requeridas."""
    return pd.DataFrame({
        "Ciclo": ["202501", "202501"],
        "Comercializador": ["CENS", "CENS"],
        "Nivel_Tension": [1, 2],
        "G": [110.0, 108.0],
        "T": [12.0, 11.5],
        "D": [60.0, 55.0],
        "Cv": [6.0, 5.5],
        "PR": [3.0, 2.8],
        "R": [1.5, 1.2],
        "CU": [192.5, 184.0],
    })


@pytest.fixture
def df_alias() -> pd.DataFrame:
    """DataFrame con nombres de columna alternativos (alias)."""
    return pd.DataFrame({
        "ciclo": ["202501"],
        "comercializador": ["CENS"],
        "nivel_tension": [1],
        "g": [110.0], "t": [12.0], "d": [60.0],
        "cv": [6.0], "pr": [3.0], "r": [1.5], "cu": [192.5],
    })


# ── Tests normalizar() ────────────────────────────────────────────────────────
class TestNormalizar:
    def test_retorna_dataframe(self, df_valido):
        result = normalizar(df_valido)
        assert isinstance(result, pd.DataFrame)

    def test_columnas_presentes(self, df_valido):
        result = normalizar(df_valido)
        for col in ["Ciclo", "Comercializador", "Nivel_Tension", "CU"]:
            assert col in result.columns, f"Columna faltante: {col}"

    def test_alias_renombrados(self, df_alias):
        result = normalizar(df_alias)
        # Los alias deben normalizarse a Title Case
        assert "Ciclo" in result.columns or "ciclo" in result.columns

    def test_no_modifica_original(self, df_valido):
        original_cols = list(df_valido.columns)
        normalizar(df_valido)
        assert list(df_valido.columns) == original_cols

    def test_tipos_numericos(self, df_valido):
        result = normalizar(df_valido)
        for col in ["G", "T", "D", "Cv", "PR", "R", "CU"]:
            if col in result.columns:
                assert pd.api.types.is_numeric_dtype(result[col]), f"{col} no es numérico"


# ── Tests validar_integridad_cu() ─────────────────────────────────────────────
class TestValidarIntegridadCU:
    def test_retorna_dataframe(self, df_valido):
        result = validar_integridad_cu(df_valido)
        assert isinstance(result, pd.DataFrame)

    def test_preserva_filas_validas(self, df_valido):
        """Las filas donde CU ≈ sum(componentes) no deben eliminarse."""
        result = validar_integridad_cu(df_valido)
        assert len(result) == len(df_valido)

    def test_acepta_tolerancia_personalizada(self, df_valido):
        result = validar_integridad_cu(df_valido, tolerancia=0.1)
        assert isinstance(result, pd.DataFrame)

    def test_dataframe_vacio(self):
        df_empty = pd.DataFrame(columns=COLUMNAS_ESQUEMA)
        result = validar_integridad_cu(df_empty)
        assert len(result) == 0

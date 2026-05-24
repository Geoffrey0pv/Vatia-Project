"""
Tests unitarios para los scrapers ETL.

Ejecutar:
    pytest tests/unit/test_scrapers.py -v
"""

from __future__ import annotations

import types
import pytest
import pandas as pd

from etl.base_scraper import ScraperBase


# ── Scraper de prueba ─────────────────────────────────────────────────────────
class _DummyScraper(ScraperBase):
    """Implementación mínima para testear ScraperBase."""

    competidor = "TEST"

    def obtener_enlaces(self) -> list[tuple[str, str]]:
        return [("ciclo_202501", b"dummy_content")]  # type: ignore[return-value]

    def extraer(self, contenido: bytes, nombre_archivo: str) -> pd.DataFrame:
        return pd.DataFrame({
            "Ciclo": ["202501"],
            "Comercializador": ["TEST"],
            "Nivel_Tension": [1],
            "G": [100.0], "T": [10.0], "D": [50.0],
            "Cv": [5.0], "PR": [2.0], "R": [1.0], "CU": [168.0],
        })


# ── Tests ─────────────────────────────────────────────────────────────────────
class TestScraperBase:
    def test_competidor_definido(self):
        s = _DummyScraper()
        assert s.competidor == "TEST"

    def test_headers_user_agent(self):
        s = _DummyScraper()
        assert "User-Agent" in s._DEFAULT_HEADERS
        assert "Mozilla" in s._DEFAULT_HEADERS["User-Agent"]

    def test_extraer_retorna_dataframe(self):
        s = _DummyScraper()
        df = s.extraer(b"dummy", "archivo.pdf")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1

    def test_extraer_columnas_minimas(self):
        s = _DummyScraper()
        df = s.extraer(b"dummy", "archivo.pdf")
        for col in ["Ciclo", "Comercializador", "Nivel_Tension", "CU"]:
            assert col in df.columns, f"Falta columna: {col}"

    def test_parse_numero(self):
        s = _DummyScraper()
        assert s._parse_numero("1.234,56") == pytest.approx(1234.56)  # type: ignore[attr-defined]
        assert s._parse_numero("1234.56") == pytest.approx(1234.56)  # type: ignore[attr-defined]
        assert s._parse_numero("texto") is None  # type: ignore[attr-defined]


class TestCensScraper:
    """Tests de integración liviana para el scraper CENS (sin red)."""

    def test_instancia_creada(self):
        from etl.scrapers.cens import CensScraper
        s = CensScraper()
        assert s.competidor == "CENS"

    def test_mapeo_componentes_no_vacio(self):
        from etl.scrapers.cens import CensScraper
        s = CensScraper()
        assert len(s.MAPEO_COMPONENTES) > 0

    def test_mapeo_niveles_no_vacio(self):
        from etl.scrapers.cens import CensScraper
        s = CensScraper()
        assert len(s.MAPEO_NIVELES_PDF) > 0

"""
Scraper BIA (BIA ENERGY S.A.S E.S.P) — extrae los componentes del Costo Unitario
(CU) del mercado regulado para todos los mercados donde BIA comercializa energía.

A diferencia de las demás empresas (que publican PDF), BIA expone las tarifas a
través de una API pública JSON consumida por su SPA ``bia.app/tarifas``::

    https://api.bia.app/ms-calculator-prices/public-ms/rate/biaenergy?month=AAAA-MM

La respuesta es un *array* con un objeto por **mercado** (ANTIOQUIA, SANTANDER,
CALI, NORTE SANTANDER, …; ~22 por mes). Cada objeto trae los componentes con el
desglose de distribución/pérdidas/total por nivel de tensión y por propiedad de
la red (``operator`` / ``shared`` / ``user`` en el nivel 1). BIA publica niveles
**1, 2 y 3** (no tiene nivel 4).

Para mantener el patrón uniforme de los demás scrapers se usa la variante
``_operator`` (red propiedad del operador, ``Dueno_Red = "100% OPERADOR"``).

Mapeo de campos -> esquema unificado (identidad CU verificada: G+T+D+Cv+PR+R=CU)::

    G  = generation
    T  = transport
    Cv = commercialization
    R  = restriction
    D  = distribution_level_{N}_operator
    PR = loss_level_{N}_operator
    CU = total_level_{N}_operator

Los meses sin datos publicados devuelven HTTP 404 y se omiten automáticamente.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from etl.base_scraper import ScraperBase


class BiaScraper(ScraperBase):
    """Scraper para BIA — mercado regulado (niveles 1, 2 y 3) en todos sus mercados."""

    competidor = "BIA"

    API_URL         = (
        "https://api.bia.app/ms-calculator-prices/public-ms/rate/biaenergy"
    )
    COMERCIALIZADOR = "BIA"
    DUENO_RED       = "100% OPERADOR"
    MIN_CICLO       = "202501"
    MAX_CICLO       = "202612"

    _NIVELES = (1, 2, 3)
    _TOL_CU  = 1.0

    def __init__(self, directorio_raw: Path | None = None) -> None:
        super().__init__(directorio_raw)

    # ── Interfaz ScraperBase ──────────────────────────────────────────────

    def obtener_enlaces(self) -> list[tuple[str, str]]:
        """
        Genera un enlace a la API por cada mes del rango de ciclos. Cada respuesta
        trae TODOS los mercados de ese mes; ``extraer()`` produce una fila por
        (mercado, nivel). Los meses sin datos (HTTP 404) se omiten en ``ejecutar()``.
        """
        enlaces: list[tuple[str, str]] = []
        for ciclo in self._meses_en_rango():
            mes = f"{ciclo[:4]}-{ciclo[4:6]}"
            url = f"{self.API_URL}?month={mes}"
            enlaces.append((f"Tarifas_BIA_{ciclo}.json", url))
        return enlaces

    def _meses_en_rango(self) -> list[str]:
        """Lista de ciclos AAAAMM entre MIN_CICLO y MAX_CICLO (inclusive)."""
        anio, mes = int(self.MIN_CICLO[:4]), int(self.MIN_CICLO[4:6])
        fin_anio, fin_mes = int(self.MAX_CICLO[:4]), int(self.MAX_CICLO[4:6])
        ciclos: list[str] = []
        while (anio, mes) <= (fin_anio, fin_mes):
            ciclos.append(f"{anio:04d}{mes:02d}")
            mes += 1
            if mes > 12:
                anio, mes = anio + 1, 1
        return ciclos

    def extraer(self, contenido: bytes, nombre_archivo: str) -> pd.DataFrame:
        """
        Parsea la respuesta JSON de la API y devuelve una fila por (mercado, nivel)
        para los niveles 1, 2 y 3 (variante ``_operator``).
        """
        datos = json.loads(contenido)
        if not isinstance(datos, list):
            mensaje = ""
            if isinstance(datos, dict):
                mensaje = str(datos.get("message", datos))
            raise RuntimeError(
                f"Respuesta inesperada para '{nombre_archivo}': {mensaje or datos}"
            )

        registros: list[dict] = []
        for obj in datos:
            registros.extend(self._filas_de_mercado(obj))
        if not registros:
            raise RuntimeError(
                f"No se extrajo ninguna tarifa de '{nombre_archivo}'."
            )
        df = pd.DataFrame(registros)
        df = df.sort_values(["Ciclo", "Operador_Red", "Nivel_Tension"])
        df = df.reset_index(drop=True)
        self.logger.info(
            "[BIA] %s -> %d fila(s) en %d mercado(s)",
            nombre_archivo, len(df), df["Operador_Red"].nunique(),
        )
        return df

    # ── Construcción de filas ──────────────────────────────────────────────

    def _filas_de_mercado(self, obj: dict) -> list[dict]:
        mercado = obj.get("city")
        ciclo = self._ciclo_de_objeto(obj)
        if not mercado or not ciclo:
            return []
        if not (self.MIN_CICLO <= ciclo <= self.MAX_CICLO):
            return []
        fecha = datetime(int(ciclo[:4]), int(ciclo[4:6]), 1).strftime("%Y-%m-%d")

        g  = self._num(obj.get("generation"))
        t  = self._num(obj.get("transport"))
        cv = self._num(obj.get("commercialization"))
        r  = self._num(obj.get("restriction"))
        if None in (g, t, cv, r):
            return []

        filas: list[dict] = []
        for nivel in self._NIVELES:
            d  = self._num(obj.get(f"distribution_level_{nivel}_operator"))
            pr = self._num(obj.get(f"loss_level_{nivel}_operator"))
            cu = self._num(obj.get(f"total_level_{nivel}_operator"))
            if None in (d, pr, cu):
                continue
            if abs((g + t + d + cv + pr + r) - cu) > self._TOL_CU:
                self.logger.warning(
                    "[BIA] %s N%d: descartada por integridad CU (suma=%.4f, CU=%.4f)",
                    mercado, nivel, g + t + d + cv + pr + r, cu,
                )
                continue
            filas.append({
                "Fecha": fecha,
                "Ciclo": ciclo,
                "Operador_Red": mercado,
                "Comercializador": self.COMERCIALIZADOR,
                "Nivel_Tension": nivel,
                "Tipo_Red": "SDL",
                "Comb_NT": f"NT{nivel}",
                "Dueno_Red": self.DUENO_RED,
                "G":  round(g, 4),
                "T":  round(t, 4),
                "D":  round(d, 4),
                "Cv": round(cv, 4),
                "PR": round(pr, 4),
                "R":  round(r, 4),
                "CU": round(cu, 4),
            })
        return filas

    @staticmethod
    def _ciclo_de_objeto(obj: dict) -> str | None:
        """Deriva el ciclo AAAAMM de ``start_date`` (formato ISO ``AAAA-MM-...``)."""
        fecha = obj.get("start_date") or ""
        if len(fecha) >= 7 and fecha[4] == "-":
            return fecha[:4] + fecha[5:7]
        return None

    @staticmethod
    def _num(valor) -> float | None:
        """Convierte a float; devuelve None si no es un número válido."""
        if valor is None:
            return None
        try:
            return float(valor)
        except (TypeError, ValueError):
            return None

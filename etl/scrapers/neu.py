"""
Scraper NEU (NEU ENERGY / ERCO ENERGY S.A.S. E.S.P.) — extrae los componentes
del Costo Unitario (CU) del mercado regulado para todos los mercados donde NEU
comercializa energía.

Al igual que BIA, NEU no publica PDF: expone las tarifas a través de una API REST
consumida por su SPA Next.js ``erco.energy/co/servicios/comercializacion/tarifas``.

Hay dos servicios involucrados::

    1. Lista de mercados (sin autenticación)::
         GET https://main-api.erco.energy/tariffs/markets
       -> [{"id_market": 4, "name": "AFINIA - CARIBE MAR"}, ...]  (~23 mercados)

    2. Tarifa detallada por mercado y mes (requiere ``Authorization: Bearer``)::
         GET https://fvichq1b59.execute-api.us-east-1.amazonaws.com/tariffs-web
             ?id_market={id}&year={AAAA}&month={M}&cot=0
       -> un objeto por nivel/propiedad con el desglose CU.

El token Bearer es estático y viaja embebido en el bundle público de la SPA
(``/_next/static/chunks/pages/_app-<hash>.js``). Para no fijar un token que rota
con cada despliegue, ``_obtener_token()`` lo extrae en tiempo de ejecución:
descarga la página de tarifas, localiza el chunk ``_app`` y captura el JWT.

La respuesta de ``tariffs-web`` trae 5 filas por (mercado, mes)::

    Nivel 1 - Propiedad Cliente / Compartida / del OR, Nivel 2, Nivel 3

Para mantener el patrón uniforme de los demás scrapers se usa únicamente la
variante de **red propiedad del operador** en el nivel 1
(``"Nivel 1 - Propiedad del OR"``, ``Dueno_Red = "100% OPERADOR"``), produciendo
una fila por nivel 1/2/3 — igual que BIA.

Mapeo de campos -> esquema unificado (identidad CU verificada: G+T+D+Cv+PR+R=CU)::

    G  = _G
    T  = _T
    D  = _D
    Cv = _C
    PR = _PR
    R  = _R
    CU = _CU   (con cot=0, _CU == _CU_COT)

Los meses sin datos publicados devuelven HTTP 404 y se omiten automáticamente
en ``ejecutar()`` (manejo por archivo de la clase base).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from etl.base_scraper import ScraperBase


class NeuScraper(ScraperBase):
    """Scraper para NEU/ERCO — mercado regulado (niveles 1, 2 y 3) en todos sus mercados."""

    competidor = "NEU"

    TARIFAS_PAGE = "https://erco.energy/co/servicios/comercializacion/tarifas"
    BASE_PAGE    = "https://erco.energy"
    MARKETS_URL  = "https://main-api.erco.energy/tariffs/markets"
    TARIFFS_URL  = (
        "https://fvichq1b59.execute-api.us-east-1.amazonaws.com/tariffs-web"
    )
    COMERCIALIZADOR = "NEU"
    DUENO_RED       = "100% OPERADOR"
    MIN_CICLO       = "202501"
    MAX_CICLO       = "202612"

    _NIVELES = (1, 2, 3)
    _TOL_CU  = 1.0

    # Etiqueta de la API a usar por nivel: en el nivel 1 sólo la variante
    # de red propiedad del operador (patrón uniforme con los demás scrapers).
    _NIVEL_LABEL = {
        1: "Nivel 1 - Propiedad del OR",
        2: "Nivel 2",
        3: "Nivel 3",
    }

    def __init__(self, directorio_raw: Path | None = None) -> None:
        super().__init__(directorio_raw)
        self._token: str | None = None
        # filename -> (ciclo, operador_red); poblado en obtener_enlaces()
        self._meta: dict[str, tuple[str, str]] = {}

    # ── Token Bearer (extraído del bundle público en runtime) ───────────────

    def _obtener_token(self) -> str:
        """Descarga la página de tarifas, localiza el chunk ``_app`` y extrae el JWT."""
        if self._token:
            return self._token

        html = requests.get(
            self.TARIFAS_PAGE, headers=self._DEFAULT_HEADERS, timeout=60
        ).text
        m = re.search(r"/_next/static/chunks/pages/_app-[a-z0-9]+\.js", html)
        if not m:
            raise RuntimeError(
                "[NEU] No se encontró el chunk '_app' en la página de tarifas."
            )
        js = requests.get(
            self.BASE_PAGE + m.group(0), headers=self._DEFAULT_HEADERS, timeout=60
        ).text
        t = re.search(
            r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", js
        )
        if not t:
            raise RuntimeError(
                "[NEU] No se pudo extraer el token Bearer del bundle de la SPA."
            )
        self._token = t.group(0)
        return self._token

    def _obtener_mercados(self) -> list[dict]:
        """Lista de mercados ``{id_market, name}`` (sin autenticación)."""
        resp = requests.get(
            self.MARKETS_URL, headers=self._DEFAULT_HEADERS, timeout=60
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise RuntimeError(f"[NEU] Respuesta inesperada de mercados: {data}")
        return data

    # ── Interfaz ScraperBase ──────────────────────────────────────────────

    def obtener_enlaces(self) -> list[tuple[str, str]]:
        """
        Genera un enlace por cada combinación (mercado, mes) del rango de ciclos.
        Cada respuesta trae el desglose CU de un mercado/mes; ``extraer()`` produce
        una fila por nivel (1/2/3). Los meses sin datos (HTTP 404) se omiten.
        """
        mercados = self._obtener_mercados()
        meses = self._meses_en_rango()

        enlaces: list[tuple[str, str]] = []
        for ciclo in meses:
            anio, mes = int(ciclo[:4]), int(ciclo[4:6])
            for merc in mercados:
                idm = merc.get("id_market")
                nombre = (merc.get("name") or "").strip()
                if idm is None or not nombre:
                    continue
                url = (
                    f"{self.TARIFFS_URL}?id_market={idm}"
                    f"&year={anio}&month={mes}&cot=0"
                )
                slug = re.sub(r"[^A-Za-z0-9]+", "_", nombre).strip("_")
                nombre_archivo = f"Tarifas_NEU_{slug}_{ciclo}.json"
                self._meta[nombre_archivo] = (ciclo, nombre)
                enlaces.append((nombre_archivo, url))

        self.logger.info(
            "[NEU] %d enlace(s) generados (%d mercado(s) × %d mes(es))",
            len(enlaces), len(mercados), len(meses),
        )
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

    def descargar(self, url: str, headers: dict | None = None) -> bytes:
        """Descarga con el header ``Authorization: Bearer`` requerido por la API."""
        cabeceras = dict(headers or self._DEFAULT_HEADERS)
        cabeceras["Authorization"] = f"Bearer {self._obtener_token()}"
        cabeceras["Accept"] = "application/json"
        return super().descargar(url, headers=cabeceras)

    def extraer(self, contenido: bytes, nombre_archivo: str) -> pd.DataFrame:
        """
        Parsea la respuesta JSON de ``tariffs-web`` y devuelve una fila por nivel
        (1/2/3, variante de red del operador en el nivel 1).
        """
        datos = json.loads(contenido)
        if not isinstance(datos, list):
            mensaje = ""
            if isinstance(datos, dict):
                mensaje = str(datos.get("message") or datos.get("detail") or datos)
            raise RuntimeError(
                f"Respuesta inesperada para '{nombre_archivo}': {mensaje or datos}"
            )

        ciclo, mercado = self._meta.get(
            nombre_archivo, (self._ciclo_de_nombre(nombre_archivo), None)
        )
        if not ciclo:
            raise RuntimeError(
                f"No se pudo derivar el ciclo de '{nombre_archivo}'."
            )
        if mercado is None:
            mercado = self._operador_de_nombre(nombre_archivo)
        fecha = datetime(int(ciclo[:4]), int(ciclo[4:6]), 1).strftime("%Y-%m-%d")

        por_label = {
            row.get("level"): row for row in datos if isinstance(row, dict)
        }

        registros: list[dict] = []
        for nivel in self._NIVELES:
            row = por_label.get(self._NIVEL_LABEL[nivel])
            if not row:
                continue
            g  = self._num(row.get("_G"))
            t  = self._num(row.get("_T"))
            d  = self._num(row.get("_D"))
            cv = self._num(row.get("_C"))
            pr = self._num(row.get("_PR"))
            r  = self._num(row.get("_R"))
            cu = self._num(row.get("_CU"))
            if None in (g, t, d, cv, pr, r, cu):
                continue
            if abs((g + t + d + cv + pr + r) - cu) > self._TOL_CU:
                self.logger.warning(
                    "[NEU] %s N%d: descartada por integridad CU (suma=%.4f, CU=%.4f)",
                    mercado, nivel, g + t + d + cv + pr + r, cu,
                )
                continue
            registros.append({
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

        if not registros:
            raise RuntimeError(
                f"No se extrajo ninguna tarifa válida de '{nombre_archivo}'."
            )
        df = pd.DataFrame(registros)
        df = df.sort_values(["Ciclo", "Nivel_Tension"]).reset_index(drop=True)
        self.logger.info(
            "[NEU] %s -> %d fila(s) [%s]", nombre_archivo, len(df), mercado,
        )
        return df

    # ── Utilidades ──────────────────────────────────────────────────────────

    @staticmethod
    def _ciclo_de_nombre(nombre_archivo: str) -> str | None:
        """Deriva el ciclo AAAAMM del nombre del archivo."""
        m = re.search(r"(\d{6})", nombre_archivo)
        return m.group(1) if m else None

    @staticmethod
    def _operador_de_nombre(nombre_archivo: str) -> str:
        """Reconstruye un nombre de mercado legible desde el slug del archivo."""
        base = re.sub(r"^Tarifas_NEU_", "", nombre_archivo)
        base = re.sub(r"_\d{6}\.json$", "", base)
        return base.replace("_", " ").strip()

    @staticmethod
    def _num(valor) -> float | None:
        """Convierte a float; devuelve None si no es un número válido."""
        if valor is None:
            return None
        try:
            return float(valor)
        except (TypeError, ValueError):
            return None

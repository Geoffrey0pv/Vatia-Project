"""
Scraper ESSA (ELECTRIFICADORA DE SANTANDER S.A. E.S.P.) — extrae los
componentes del Costo Unitario (CU) del mercado regulado de energía.

ESSA publica, en la página ``consultar-tarifas``, un PDF mensual de tipo
``Tarifa <Mes> <Año>`` (distinto de los PDF ``Publicación COT <Mes>``, que solo
contienen el COT y NO el desglose del CU). Cada PDF de tarifa trae una única
tabla con el desglose por nivel de tensión. El encabezado de columnas es::

    Nivel | G | T | D | Cv | PR | R | CUv Calculado [| COT | CUf Aplicado]

es decir, el orden de las primeras 7 cifras de cada fila es
**G, T, D, Cv, PR, R, CU**. Algunos meses añaden dos columnas extra al final
(``COT`` y ``CUf Aplicado``, normalmente ``0.00``); el parser toma SIEMPRE las
primeras 7 cifras tras la etiqueta de nivel.

``fitz`` emite una celda por línea, de modo que la tabla aparece como:
la etiqueta del nivel (``I ESSA``, ``II``, ``III``, ``IV`` …) seguida de sus
cifras, una por línea. Las filas publicadas son::

    I ESSA   -> Nivel 1 (medida/red de BT propiedad del Operador)
    I CLIENTE-> (se omite: red propiedad del cliente)
    II       -> Nivel 2
    III      -> Nivel 3
    IV       -> Nivel 4
    I 50% / I 100% -> (se omiten: variantes de subsidio/contribución)
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, quote

import fitz  # PyMuPDF
import pandas as pd
import requests

from etl.base_scraper import ScraperBase


class EssaScraper(ScraperBase):
    """Scraper para ESSA — mercado regulado (niveles 1, 2, 3 y 4)."""

    competidor = "ESSA"

    BASE_URL        = "https://www.essa.com.co"
    URL_TARIFAS     = (
        "https://www.essa.com.co/site/mi-factura/"
        "formula-tarifaria-y-tarifas/consultar-tarifas"
    )
    COMERCIALIZADOR = "ESSA"
    OPERADOR_RED    = "ESSA"
    DUENO_RED       = "100% OPERADOR"
    MIN_CICLO       = "202501"
    MAX_CICLO       = "202612"

    # Orden de columnas de la tabla de ESSA (las 7 primeras cifras por fila).
    _COLUMNAS    = ["G", "T", "D", "Cv", "PR", "R", "CU"]
    _COMPONENTES = ["G", "T", "D", "Cv", "PR", "R"]
    _TOL_CU      = 1.0

    # Etiqueta de nivel (celda) -> nivel del esquema unificado.
    _MAPA_NIVEL = {"I ESSA": 1, "II": 2, "III": 3, "IV": 4}
    # Etiquetas que pertenecen a la tabla (se reconocen, aunque se omitan).
    _ETIQUETAS_TABLA = {
        "I ESSA", "I CLIENTE", "II", "III", "IV", "I 50%", "I 100%",
    }
    # Celdas de encabezado/ruido que pueden aparecer entre el ancla y las filas.
    _RUIDO = {"$/kWh", "COT", "CUf Aplicado", "CUv Calculado", ""}

    _MESES: dict[str, str] = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "setiembre": "09", "octubre": "10",
        "noviembre": "11", "diciembre": "12",
    }

    # Ancla de la tabla: la celda que contiene "CUv".
    _RE_ANCLA = re.compile(r"\bCUv\b", re.I)
    # "Tarifa <Mes> <Año>" en el texto del enlace.
    _RE_TITULO = re.compile(
        r"Tarifa\s+([A-Za-zÁÉÍÓÚáéíóúñ]+)\s*(20\d{2})?", re.I
    )
    _RE_ANIO_URL = re.compile(r"/tarifas/(20\d{2})/")
    _RE_CICLO    = re.compile(r"(20\d{2})(0[1-9]|1[0-2])")

    def __init__(self, directorio_raw: Path | None = None) -> None:
        super().__init__(directorio_raw)

    # ── Interfaz ScraperBase ──────────────────────────────────────────────

    def obtener_enlaces(self) -> list[tuple[str, str]]:
        """
        Raspa la página ``consultar-tarifas`` y devuelve un enlace por cada PDF
        ``Tarifa <Mes> <Año>`` dentro del rango de ciclos. Se descartan los PDF
        ``Publicación COT`` (solo traen el COT, no el desglose del CU).
        """
        from bs4 import BeautifulSoup

        resp = requests.get(
            self.URL_TARIFAS, headers=self._DEFAULT_HEADERS, timeout=60
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        enlaces: list[tuple[str, str]] = []
        vistos: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.lower().endswith(".pdf") or "/tarifas/" not in href:
                continue
            texto = a.get_text(" ", strip=True)
            ciclo = self._ciclo_desde(texto, href)
            if ciclo is None:
                continue
            if not (self.MIN_CICLO <= ciclo <= self.MAX_CICLO):
                continue
            if ciclo in vistos:
                continue
            vistos.add(ciclo)
            url = self._normalizar_url(href)
            enlaces.append((f"Tarifa_ESSA_{ciclo}.pdf", url))
            self.logger.info("[ESSA] %s -> %s", ciclo, url)

        if not enlaces:
            raise ValueError(
                "[ESSA] No se halló ningún PDF de tarifa dentro del rango "
                f"{self.MIN_CICLO}..{self.MAX_CICLO}."
            )
        enlaces.sort()
        return enlaces

    def _ciclo_desde(self, texto: str, href: str) -> str | None:
        """Deriva el ciclo AAAAMM de un enlace de tarifa.

        Solo acepta enlaces cuyo texto sea "Tarifa <Mes> ..." (excluye los
        "Publicación COT ..."). El mes se toma del texto; el año, de la ruta
        ``/tarifas/<año>/`` (con respaldo en el año del propio texto).
        """
        t = texto.strip()
        if "cot" in t.lower() or "publica" in t.lower():
            return None
        m = self._RE_TITULO.match(t)
        if not m:
            return None
        mes = self._MESES.get(m.group(1).lower())
        if not mes:
            return None
        m_anio_url = self._RE_ANIO_URL.search(href)
        anio = m_anio_url.group(1) if m_anio_url else (m.group(2) or "")
        if not anio:
            return None
        return f"{anio}{mes}"

    def _normalizar_url(self, href: str) -> str:
        """Convierte una ruta (posiblemente relativa o con espacios) en una URL
        absoluta y correctamente codificada."""
        absoluta = urljoin(self.BASE_URL + "/", href)
        partes = urlsplit(absoluta)
        ruta = quote(partes.path, safe="/%")
        return urlunsplit(
            (partes.scheme, partes.netloc, ruta, partes.query, partes.fragment)
        )

    def extraer(self, contenido: bytes, nombre_archivo: str) -> pd.DataFrame:
        """Parsea el PDF de tarifa y devuelve una fila por nivel (1..4)."""
        ciclo = self._ciclo_de_nombre(nombre_archivo)
        doc = fitz.open(stream=contenido, filetype="pdf")
        lineas = []
        for page in doc:
            lineas.extend(page.get_text("text").split("\n"))
        filas = self._parsear(lineas)
        if not filas:
            raise RuntimeError(
                f"No se extrajo ningún nivel de '{nombre_archivo}'."
            )
        df = self._construir_df(ciclo, filas)
        self.logger.info("[ESSA] %s -> %d nivel(es)", nombre_archivo, len(df))
        return df

    def _ciclo_de_nombre(self, nombre: str) -> str:
        m = self._RE_CICLO.search(nombre)
        if not m:
            raise ValueError(f"[ESSA] No se pudo derivar el ciclo de '{nombre}'.")
        return m.group(0)

    # ── Parser de la tabla ─────────────────────────────────────────────────

    _RE_NUM = re.compile(r"^\d{1,3}(?:[.,]\d{3})*[.,]\d{2,4}$|^\d+[.,]\d{2,4}$")

    @classmethod
    def _es_numero(cls, s: str) -> bool:
        return bool(cls._RE_NUM.match(s.strip()))

    @staticmethod
    def _val(texto: str) -> float:
        """Convierte una cifra (punto o coma decimal). El separador más a la
        derecha es el decimal; los demás son separadores de millares."""
        s = texto.strip()
        i_pt, i_cm = s.rfind("."), s.rfind(",")
        if i_pt == -1 and i_cm == -1:
            return float(s)
        sep = max(i_pt, i_cm)
        entero = re.sub(r"[.,]", "", s[:sep])
        return float(f"{entero}.{s[sep + 1:]}")

    def _parsear(self, lineas: list[str]) -> dict[int, dict[str, float]]:
        """Localiza el ancla 'CUv' y lee las filas de niveles. Por cada nivel de
        interés toma las primeras 7 cifras (G, T, D, Cv, PR, R, CU)."""
        ancla = next(
            (i for i, l in enumerate(lineas) if self._RE_ANCLA.search(l)), None
        )
        if ancla is None:
            return {}

        filas: dict[int, dict[str, float]] = {}
        etiqueta: str | None = None
        nums: list[float] = []

        def flush() -> None:
            nivel = self._MAPA_NIVEL.get(etiqueta) if etiqueta else None
            if nivel is not None and nivel not in filas and len(nums) >= 7:
                comps = dict(zip(self._COLUMNAS, nums[:7]))
                if self._fila_integra(comps):
                    filas[nivel] = comps

        i = ancla + 1
        while i < len(lineas):
            s = lineas[i].strip()
            if s in self._ETIQUETAS_TABLA:
                flush()
                etiqueta, nums = s, []
                i += 1
                continue
            if self._es_numero(s):
                if etiqueta is not None:
                    nums.append(self._val(s))
                i += 1
                continue
            if s in self._RUIDO or s.endswith("Aplicado") or s == "$/kWh":
                i += 1
                continue
            # Cualquier otra línea tras haber empezado una fila -> fin de tabla.
            if etiqueta is not None:
                break
            i += 1
        flush()
        return filas

    def _fila_integra(self, comps: dict[str, float]) -> bool:
        suma = sum(comps[c] for c in self._COMPONENTES)
        return abs(suma - comps["CU"]) <= self._TOL_CU

    # ── Construcción del DataFrame ─────────────────────────────────────────

    def _construir_df(
        self, ciclo: str, filas: dict[int, dict[str, float]]
    ) -> pd.DataFrame:
        fecha = datetime(int(ciclo[:4]), int(ciclo[4:6]), 1).strftime("%Y-%m-%d")
        registros = []
        for nivel in sorted(filas):
            c = filas[nivel]
            registros.append({
                "Fecha": fecha,
                "Ciclo": ciclo,
                "Operador_Red": self.OPERADOR_RED,
                "Comercializador": self.COMERCIALIZADOR,
                "Nivel_Tension": nivel,
                "Tipo_Red": "SDL",
                "Comb_NT": f"NT{nivel}",
                "Dueno_Red": self.DUENO_RED,
                "G":  round(c["G"], 4),
                "T":  round(c["T"], 4),
                "D":  round(c["D"], 4),
                "Cv": round(c["Cv"], 4),
                "PR": round(c["PR"], 4),
                "R":  round(c["R"], 4),
                "CU": round(c["CU"], 4),
            })
        return pd.DataFrame(registros).reset_index(drop=True)

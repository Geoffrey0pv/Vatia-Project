"""
Scraper EMCALI (EMPRESAS MUNICIPALES DE CALI E.I.C.E. E.S.P.) — extrae los
componentes del Costo Unitario (CU) del mercado regulado de energía.

EMCALI publica las tarifas en dos presentaciones, ambas con texto seleccionable
y con la MISMA tabla de componentes:

  • **PDF anual consolidado** (p.ej. ``mercado-regulado-2025``): un solo archivo
    con TODOS los meses del año, cada uno con su tabla. Cifras en formato
    colombiano (coma decimal: ``415,4435``).
  • **PDF mensual "publiweb"** (p.ej. ``publiweb-enero-2026-...``): un mes por
    archivo. Cifras en formato anglosajón (punto decimal: ``308.8267``).

La tabla de componentes tiene el encabezado::

    Nivel tensión  Gm   Tm   PRnm   DtUN   Rm   Cvm   Cuv (calculado)  1-CS  >CS

es decir, el orden de columnas es **G, T, PR, D, R, Cv, CU** (D = ``DtUN``).
Los niveles publicados son ``1 (A)``, ``1 (C)``, ``2``, ``3`` y ``Subnormal``;
EMCALI **no tiene Nivel 4**. Al esquema unificado se mapean:

    Nivel 1 ← "1 (A)"  (medida/transformador/red de baja propiedad del Operador)
    Nivel 2 ← "2"
    Nivel 3 ← "3"

``extraer()`` parsea TODOS los bloques mensuales presentes en el PDF y devuelve
una fila por (mes, nivel) dentro del rango de ciclos configurado.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
import pandas as pd
import requests

from etl.base_scraper import ScraperBase


class EmcaliScraper(ScraperBase):
    """Scraper para EMCALI — mercado regulado (niveles 1, 2 y 3)."""

    competidor = "EMCALI"

    BASE_URL        = "https://www.emcali.com.co"
    URL_TARIFAS     = "https://www.emcali.com.co/energia/tarifas-del-mercado-regulado"
    # La página oficial es un shell que embebe un SPA (Vite/React) alojado en
    # Render. El listado de documentos (un PDF anual por año, con todos los
    # meses) está "horneado" en el chunk de la ruta del bundle del SPA, no en
    # una API JSON. ``obtener_enlaces()`` reconstruye ese listado leyendo el
    # bundle, lo que lo hace 100% automático y resistente a slugs imprevisibles
    # (p.ej. ``tarifas-enero-mayo`` para 2026, ``mercado-regulado-2025-1-`` para
    # 2025) y a los hashes de build cambiantes.
    BASE_SPA        = "https://portalemcali.onrender.com"
    URL_SPA         = "https://portalemcali.onrender.com/energia/ratesRegulatedMarket"
    COMERCIALIZADOR = "EMCALI"
    OPERADOR_RED    = "EMCALI"
    DUENO_RED       = "100% OPERADOR"
    MIN_CICLO       = "202501"
    MAX_CICLO       = "202612"

    # Orden de columnas en la tabla de componentes de EMCALI.
    _COLUMNAS = ["G", "T", "PR", "D", "R", "Cv", "CU"]
    _COMPONENTES = ["G", "T", "D", "Cv", "PR", "R"]
    _TOL_CU = 1.0

    # Etiquetas de nivel -> nivel del esquema unificado (1 (A) = propiedad OR).
    _MAPA_NIVEL = {"1 (A)": 1, "2": 2, "3": 3}
    _ETIQUETAS = ("1 (A)", "1 (C)", "2", "3", "Subnormal")

    # Nombre de mes (español e inglés) -> número.
    _MESES: dict[str, str] = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "setiembre": "09", "octubre": "10",
        "noviembre": "11", "diciembre": "12",
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    _RE_MES = re.compile(
        r"^\s*([A-Za-zÁÉÍÓÚáéíóúñ]+)\s*[-/]\s*(20\d{2})\s*$"
    )
    # fitz emite una celda por línea: el encabezado de la tabla es la celda
    # "Nivel tensión" (las columnas Gm/Tm/... vienen en líneas siguientes).
    _RE_HEADER = re.compile(r"^nivel\s+tensi[oó]n\b", re.I)

    def __init__(self, directorio_raw: Path | None = None) -> None:
        super().__init__(directorio_raw)

    # ── Interfaz ScraperBase ──────────────────────────────────────────────

    # Regex para reconstruir el listado de documentos desde el bundle del SPA.
    _RE_MAIN_BUNDLE = re.compile(r"/assets/index-[A-Za-z0-9_]+\.js")
    _RE_CHUNK_RUTA  = re.compile(
        r"(?:assets/)?IndexRatesRegulatedMarket-[A-Za-z0-9_]+\.js"
    )
    # Pares título -> downloadUrl horneados en el chunk de la ruta.
    _RE_DOC = re.compile(
        r'title:"([^"]*Mercado Regulado[^"]*)"'
        r'[\s\S]{0,400}?downloadUrl:"([^"]+)"'
    )
    _RE_ANIO = re.compile(r"(20\d{2})")

    def obtener_enlaces(self) -> list[tuple[str, str]]:
        """
        Reconstruye el listado de documentos de tarifas leyendo el bundle JS del
        SPA de EMCALI (Render). Cada documento es el PDF anual consolidado de un
        año, con todos sus meses; ``extraer()`` produce una fila por (mes, nivel).

        Estrategia (100% automática, resistente a hashes de build y a slugs
        imprevisibles):

          1. Descarga el *shell* HTML del SPA y localiza el bundle principal
             ``/assets/index-<hash>.js``.
          2. Descarga el bundle principal y localiza el chunk de la ruta
             ``IndexRatesRegulatedMarket-<hash>.js``.
          3. Descarga el chunk y extrae los pares ``title`` / ``downloadUrl``,
             quedándose con los años dentro del rango de ciclos configurado.
        """
        shell = self._descargar_texto(self.URL_SPA)
        m_main = self._RE_MAIN_BUNDLE.search(shell)
        if not m_main:
            raise ValueError(
                "[EMCALI] No se encontró el bundle principal en el shell del SPA."
            )
        main_js = self._descargar_texto(self.BASE_SPA + m_main.group(0))

        m_chunk = self._RE_CHUNK_RUTA.search(main_js)
        if not m_chunk:
            raise ValueError(
                "[EMCALI] No se encontró el chunk de la ruta de mercado regulado."
            )
        chunk_url = self.BASE_SPA + "/assets/" + m_chunk.group(0).split("/")[-1]
        chunk_js = self._descargar_texto(chunk_url)

        min_anio, max_anio = int(self.MIN_CICLO[:4]), int(self.MAX_CICLO[:4])
        enlaces: list[tuple[str, str]] = []
        vistos: set[int] = set()
        for titulo, url in self._RE_DOC.findall(chunk_js):
            m_anio = self._RE_ANIO.search(titulo)
            if not m_anio:
                continue
            anio = int(m_anio.group(1))
            if not (min_anio <= anio <= max_anio) or anio in vistos:
                continue
            vistos.add(anio)
            enlaces.append((f"Tarifas_EMCALI_{anio}.pdf", url))
            self.logger.info("[EMCALI] Documento %d -> %s", anio, url)

        if not enlaces:
            raise ValueError(
                "[EMCALI] No se halló ningún documento dentro del rango de "
                f"ciclos {self.MIN_CICLO}..{self.MAX_CICLO} en el SPA."
            )
        enlaces.sort()
        return enlaces

    def _descargar_texto(self, url: str) -> str:
        """Descarga un recurso de texto (HTML/JS) del SPA con reintentos.

        El backend de Render puede estar "dormido" (cold start) y responder con
        lentitud o un 5xx la primera vez, así que se reintenta unas pocas veces.
        """
        ultimo_error: Exception | None = None
        for intento in range(4):
            try:
                resp = requests.get(
                    url, headers=self._DEFAULT_HEADERS, timeout=60
                )
                if resp.status_code == 200 and resp.text:
                    return resp.text
                ultimo_error = RuntimeError(
                    f"HTTP {resp.status_code} al pedir {url}"
                )
            except requests.RequestException as exc:
                ultimo_error = exc
            self.logger.warning(
                "[EMCALI] Reintentando %s (intento %d): %s",
                url, intento + 1, ultimo_error,
            )
            time.sleep(3 * (intento + 1))
        raise RuntimeError(f"[EMCALI] No se pudo descargar {url}: {ultimo_error}")

    def extraer(self, contenido: bytes, nombre_archivo: str) -> pd.DataFrame:
        """
        Parsea TODOS los bloques mensuales del PDF y devuelve una fila por
        (mes, nivel) dentro del rango ``MIN_CICLO``..``MAX_CICLO``.
        """
        doc = fitz.open(stream=contenido, filetype="pdf")
        texto = "\n".join(page.get_text("text") for page in doc)
        registros = self._parsear_documento(texto)
        if not registros:
            raise RuntimeError(
                f"No se extrajo ningún componente de '{nombre_archivo}'."
            )
        df = self._construir_df(registros)
        self.logger.info(
            "[EMCALI] %s -> %d fila(s) en %d ciclo(s)",
            nombre_archivo, len(df), df["Ciclo"].nunique(),
        )
        return df

    # ── Formato numérico (acepta coma o punto decimal) ─────────────────────

    _RE_NUM = re.compile(r"^\d{1,3}(?:[.,]\d{3})*[.,]\d{2,4}$|^\d+[.,]\d{2,4}$")

    @classmethod
    def _es_numero(cls, texto: str) -> bool:
        return bool(cls._RE_NUM.match((texto or "").strip()))

    @staticmethod
    def _val(texto: str) -> float:
        """
        Convierte una cifra en formato colombiano (``1.055,2552``) o anglosajón
        (``1,055.2552`` / ``308.8267``). Regla: el separador más a la derecha es
        el decimal; los demás se descartan como separadores de millares.
        """
        s = texto.strip()
        i_pt, i_cm = s.rfind("."), s.rfind(",")
        if i_pt == -1 and i_cm == -1:
            return float(s)
        sep = max(i_pt, i_cm)
        entero = re.sub(r"[.,]", "", s[:sep])
        return float(f"{entero}.{s[sep + 1:]}")

    def _fila_integra(self, comps: dict[str, float], cu: float) -> bool:
        suma = sum(comps.get(c, 0.0) for c in self._COMPONENTES)
        return abs(suma - cu) <= self._TOL_CU

    # ── Parser del documento (multi-mes) ───────────────────────────────────

    def _parsear_documento(self, texto: str) -> list[dict]:
        lineas = [l.strip() for l in texto.split("\n")]
        registros: list[dict] = []
        mes_actual: str | None = None  # AAAAMM
        i = 0
        while i < len(lineas):
            linea = lineas[i]

            m = self._RE_MES.match(linea)
            if m:
                mes_nom = m.group(1).lower()
                mes_num = self._MESES.get(mes_nom)
                if mes_num:
                    mes_actual = f"{m.group(2)}{mes_num}"
                i += 1
                continue

            if self._RE_HEADER.search(linea) and self._confirma_header(lineas, i):
                i, filas = self._leer_bloque(lineas, i + 1)
                if mes_actual and self.MIN_CICLO <= mes_actual <= self.MAX_CICLO:
                    for etiqueta, nums in filas:
                        nivel = self._MAPA_NIVEL.get(etiqueta)
                        if nivel is None or len(nums) < 7:
                            continue
                        comps = dict(zip(self._COLUMNAS, nums[:7]))
                        if not self._fila_integra(comps, comps["CU"]):
                            continue
                        registros.append({"ciclo": mes_actual, "nivel": nivel, **comps})
                continue

            i += 1
        return registros

    def _confirma_header(self, lineas: list[str], i: int) -> bool:
        """Confirma que tras 'Nivel tensión' aparezca la columna 'Gm' cerca."""
        for j in range(i + 1, min(i + 5, len(lineas))):
            if lineas[j].strip().lower().startswith("gm"):
                return True
        return False

    def _leer_bloque(self, lineas: list[str], i: int) -> tuple[int, list[tuple[str, list[float]]]]:
        """
        Desde la línea ``i`` (justo tras el encabezado) lee las filas de niveles
        hasta el fin del bloque. Devuelve (índice_siguiente, [(etiqueta, nums)]).
        Tolera que las cifras de una fila queden repartidas en varias líneas.
        """
        filas: list[tuple[str, list[float]]] = []
        etiqueta: str | None = None
        nums: list[float] = []

        def flush():
            if etiqueta is not None and nums:
                filas.append((etiqueta, list(nums)))

        while i < len(lineas):
            linea = lineas[i]
            if not linea:
                i += 1
                continue
            # ¿nuevo mes / nuevo encabezado / fin de tabla?
            if self._RE_MES.match(linea) or self._RE_HEADER.search(linea):
                break
            etq = self._coincide_etiqueta(linea)
            if etq is not None:
                flush()
                etiqueta, resto = etq
                nums = [self._val(t) for t in self._tokens_num(resto)]
                i += 1
                continue
            # línea solo de números -> continúa la fila actual
            toks = self._tokens_num(linea)
            if etiqueta is not None and toks and self._solo_numeros(linea):
                nums.extend(self._val(t) for t in toks)
                i += 1
                continue
            # línea no reconocida tras haber leído filas -> fin del bloque
            if filas or etiqueta is not None:
                break
            i += 1
        flush()
        return i, filas

    def _coincide_etiqueta(self, linea: str):
        for etq in self._ETIQUETAS:
            patron = r"^" + re.escape(etq) + r"(?=\s|$)"
            m = re.match(patron, linea)
            if m:
                return etq, linea[m.end():]
        return None

    @staticmethod
    def _tokens_num(texto: str) -> list[str]:
        return re.findall(r"\d{1,3}(?:[.,]\d{3})*[.,]\d{2,4}|\d+[.,]\d{2,4}", texto)

    def _solo_numeros(self, linea: str) -> bool:
        resto = re.sub(r"\d{1,3}(?:[.,]\d{3})*[.,]\d{2,4}|\d+[.,]\d{2,4}", "", linea)
        return resto.strip(" .,-") == ""

    # ── Construcción del DataFrame ─────────────────────────────────────────

    def _construir_df(self, registros: list[dict]) -> pd.DataFrame:
        filas = []
        for r in registros:
            ciclo = r["ciclo"]
            fecha = datetime(int(ciclo[:4]), int(ciclo[4:6]), 1).strftime("%Y-%m-%d")
            nivel = r["nivel"]
            filas.append({
                "Fecha": fecha,
                "Ciclo": ciclo,
                "Operador_Red": self.OPERADOR_RED,
                "Comercializador": self.COMERCIALIZADOR,
                "Nivel_Tension": nivel,
                "Tipo_Red": "SDL",
                "Comb_NT": f"NT{nivel}",
                "Dueno_Red": self.DUENO_RED,
                "G":  round(r["G"], 4),
                "T":  round(r["T"], 4),
                "D":  round(r["D"], 4),
                "Cv": round(r["Cv"], 4),
                "PR": round(r["PR"], 4),
                "R":  round(r["R"], 4),
                "CU": round(r["CU"], 4),
            })
        df = pd.DataFrame(filas)
        return df.sort_values(["Ciclo", "Nivel_Tension"]).reset_index(drop=True)

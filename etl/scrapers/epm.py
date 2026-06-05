"""
Scraper EPM — extrae la tabla de componentes CU de los PDFs de Tarifas.

A diferencia de CENS, los PDFs de EPM tienen **texto seleccionable**, así que
la extracción primaria usa PyMuPDF (``fitz``) directo. Si un PDF resultara ser
una imagen rasterizada (publicaciones antiguas), se cae a OCR con easyocr,
igual que CENS.

El PDF de EPM contiene varios mercados (EPM-Antioquia, ENEL, CELSIA, EMCALI).
Solo se extrae el mercado propio de EPM (Antioquia), que es la primera sección
del documento.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from etl.base_scraper import ScraperBase


class EpmScraper(ScraperBase):
    """
    Scraper para EPM — raspa las dos páginas de tarifas (año actual + años
    anteriores), descarga los PDFs y extrae los componentes del Costo Unitario
    (G, T, D, Cv, PR, R, CU) por nivel de tensión (1–4) del mercado EPM-Antioquia.
    """

    competidor = "EPM"

    URLS_TARIFAS = [
        "https://www.epm.com.co/clientesyusuarios/energia/tarifas-energia/",
        "https://www.epm.com.co/clientesyusuarios/energia/tarifas-energia/tarifas-anos-anteriores-energia/",
    ]
    BASE_URL        = "https://www.epm.com.co"
    COMERCIALIZADOR = "EPM"
    OPERADOR_RED    = "EPM"
    DUENO_RED       = "100% OPERADOR"

    # Marca dónde termina el mercado propio (EPM-Antioquia) y empiezan los demás.
    _FIN_MERCADO_EPM = re.compile(
        r"Mercado\s+Comercializaci[oó]n\s+Operador\s+de\s+Red\s+(?:ENEL|CELSIA|EMCALI)",
        re.I,
    )

    # ── Meses en español → número ─────────────────────────────────────────────
    _MESES: dict[str, str] = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "setiembre": "09", "octubre": "10",
        "noviembre": "11", "diciembre": "12",
    }
    # Abreviaturas (solo se usan si no aparece el nombre completo del mes).
    _MESES_ABREV: dict[str, str] = {
        "ene": "01", "feb": "02", "mar": "03", "abr": "04", "may": "05",
        "jun": "06", "jul": "07", "ago": "08", "sept": "09", "sep": "09",
        "oct": "10", "nov": "11", "dic": "12",
    }

    # ── Etiquetas de componentes en el PDF de EPM ─────────────────────────────
    _PATRONES_COMPONENTE: list[tuple[str, str]] = [
        ("G",  r"Gm,?\s*i|Costo\s+compra"),
        ("T",  r"\bTm\b|transporte\s+STN"),
        ("D",  r"Dn,?\s*m|transporte\s+SDL"),
        ("Cv", r"CVm|Margen\s+comercial"),
        ("PR", r"PRn|p[eé]rdidas"),
        ("R",  r"\bRm\b|Restricciones"),
    ]
    _ORDEN_COMPONENTES = ["G", "T", "D", "Cv", "PR", "R"]
    _RE_CU = re.compile(r"(?:CU\s+Total|Total\s+CU)\b", re.I)
    _RE_NUM_LINEA = re.compile(r"^\d+\.\d+$")
    _CICLOS_HISTORICOS_CONOCIDOS_FALLIDOS = {
        "200901", "200903", "200904", "200905", "200906", "200907", "200908",
        "200909", "200910", "200911", "200912", "201001", "201002", "201003",
        "201312", "201605", "202001", "202311", "202509",
    }

    def __init__(self, directorio_raw: Path | None = None) -> None:
        super().__init__(directorio_raw)
        self._ocr_reader = None  # lazy

    # ── Interfaz ScraperBase ─────────────────────────────────────────────────

    def obtener_enlaces(self) -> list[tuple[str, str]]:
        """
        Raspa ambas páginas de EPM y devuelve los PDFs de tarifas disponibles.

        El ciclo (AAAAMM) se deriva del mes (nombre en español, en el archivo) y
        del año (que puede estar en el nombre o en la carpeta de la URL). Se
        devuelve un nombre sintético ``Tarifas_EPM_<AAAAMM>.pdf`` para que
        ``extraer()`` recupere el ciclo igual que CENS.

        Returns:
            Lista de ``(nombre_sintetico, url_descarga)`` ordenada por ciclo.
        """
        vistos: dict[str, str] = {}  # ciclo → url (dedupe, se queda el último visto)

        for url in self.URLS_TARIFAS:
            self.logger.info("[EPM] Accediendo a: %s", url)
            try:
                resp = requests.get(url, headers=self._DEFAULT_HEADERS, timeout=30)
                resp.raise_for_status()
            except requests.RequestException as exc:
                self.logger.error("  ✖ No se pudo acceder a %s: %s", url, exc)
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            n_pagina = 0

            for tag_a in soup.find_all("a", href=True):
                href = tag_a["href"].split("?")[0]
                if ".pdf" not in href.lower():
                    continue

                url_desc = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                nombre_archivo = url_desc.split("/")[-1]

                # Filtro: debe ser un PDF de tarifas (nombre con mes o "tarifa").
                if not (self._tiene_mes(nombre_archivo) or "tarifa" in nombre_archivo.lower()):
                    continue

                ciclo = self._ciclo_desde_url(url_desc)
                if not ciclo:
                    self.logger.debug("  ? ciclo no determinable: %s", nombre_archivo)
                    continue

                vistos[ciclo] = url_desc
                n_pagina += 1

            self.logger.info("  ✔ %d PDF(s) de tarifas en la página", n_pagina)

        if not vistos:
            raise ValueError(
                "No se encontró ningún PDF de tarifas en las páginas de EPM. "
                "Verifique las URLs o el patrón de búsqueda."
            )

        enlaces = [
            (f"Tarifas_EPM_{ciclo}.pdf", url)
            for ciclo, url in sorted(vistos.items())
        ]
        self.logger.info("[EPM] %d ciclo(s) único(s) encontrado(s)", len(enlaces))
        return enlaces

    def extraer(self, contenido: bytes, nombre_archivo: str) -> pd.DataFrame:
        """
        Extrae los componentes CU por nivel de tensión de un PDF de EPM.

        Primero intenta texto con ``fitz``; si el PDF es imagen, usa OCR.

        Returns:
            DataFrame con 4 filas (niveles 1–4) y columnas Fecha, Ciclo,
            Operador_Red, Comercializador, Dueno_Red, Nivel_Tension, G…CU.
        """
        ciclo, fecha = self._extraer_ciclo_y_fecha(nombre_archivo)
        self.logger.info("  Ciclo: %s → Fecha: %s", ciclo, fecha)

        texto = self._texto_pdf(contenido)
        if not self._texto_util(texto):
            self.logger.info("  PDF sin texto → fallback OCR")
            texto = self._texto_pdf_ocr(contenido)

        data_por_nivel = self._parsear_texto(texto)
        if not data_por_nivel:
            raise RuntimeError(
                f"No se extrajo ningún nivel de tensión de '{nombre_archivo}'. "
                "El layout del PDF pudo haber cambiado."
            )

        df_filas: list[dict] = []
        for nivel in sorted(data_por_nivel):
            comp = data_por_nivel[nivel]
            df_filas.append({
                "Fecha":           fecha,
                "Ciclo":           ciclo,
                "Operador_Red":    self.OPERADOR_RED,
                "Comercializador": self.COMERCIALIZADOR,
                "Tipo_Red":        comp.get("Tipo_Red"),
                "Comb_NT":         comp.get("Comb_NT"),
                "Dueno_Red":       self.DUENO_RED,
                "Nivel_Tension":   nivel,
                "G":  comp.get("G"),  "T": comp.get("T"),  "D": comp.get("D"),
                "Cv": comp.get("Cv"), "PR": comp.get("PR"), "R": comp.get("R"),
                "CU": comp.get("CU"),
            })

        df = pd.DataFrame(df_filas)
        if len(df) < 4:
            self.logger.warning(
                "PDF procesado parcialmente: se esperaban 4 niveles, se obtuvieron %d",
                len(df),
            )
        self.logger.info("  → %d filas × %d columnas", len(df), len(df.columns))
        return df

    def ejecutar(self) -> pd.DataFrame:
        """
        Ejecuta EPM con un resumen explícito de cobertura.
        """
        self.logger.warning(
            "[EPM] Cobertura histórica amplia: existen ciclos antiguos con layout "
            "heterogéneo y fallos conocidos; este resumen ayuda a distinguir "
            "procesamientos completos, parciales y fallidos."
        )
        self.logger.warning(
            "[EPM] Ciclos históricos fallidos conocidos (referencia actual): %s",
            ", ".join(sorted(self._CICLOS_HISTORICOS_CONOCIDOS_FALLIDOS)),
        )

        enlaces = self.obtener_enlaces()
        encontrados = len(enlaces)
        descargados = 0
        completos = 0
        parciales = 0
        fallidos = 0
        resultados: list[pd.DataFrame] = []

        for nombre, url in enlaces:
            try:
                contenido = self.descargar(url)
                descargados += 1
                self.guardar_raw(contenido, nombre)
                df = self.extraer(contenido, nombre)
                resultados.append(df)
                if len(df) >= 4:
                    completos += 1
                else:
                    parciales += 1
                    self.logger.warning(
                        "[EPM] %s procesado parcialmente: %d nivel(es) extraído(s)",
                        nombre,
                        len(df),
                    )
                self.logger.info("✔ %s procesado (%d filas)", nombre, len(df))
            except Exception as exc:
                fallidos += 1
                self.logger.warning("✖ %s falló: %s", nombre, exc)

        filas_finales = sum(len(df) for df in resultados)
        self.logger.info(
            "[EPM] Resumen -> encontrados=%d descargados=%d completos=%d "
            "parciales=%d fallidos=%d filas_validas=%d",
            encontrados,
            descargados,
            completos,
            parciales,
            fallidos,
            filas_finales,
        )

        if not resultados:
            raise RuntimeError("EPM: no se procesó ningún PDF válido.")

        return pd.concat(resultados, ignore_index=True)

    # ── Helpers de enlaces / ciclo ────────────────────────────────────────────

    def _tiene_mes(self, texto: str) -> bool:
        return self._mes_desde_texto(texto) is not None

    def _mes_desde_texto(self, texto: str) -> str | None:
        """Devuelve el número de mes ('01'..'12') a partir de un texto, o None."""
        t = texto.lower()
        for nombre, num in self._MESES.items():
            if nombre in t:
                return num
        # Abreviaturas: quitar 'energia' (contiene 'ene') para evitar falsos positivos.
        t2 = t.replace("energia", "").replace("energía", "")
        for abrev, num in self._MESES_ABREV.items():
            # Como token delimitado (ej. "-nov-") o pegado a un día (ej. "nov19").
            if re.search(rf"(?<![a-z]){abrev}(?![a-z])", t2) or re.search(rf"{abrev}\d", t2):
                return num
        return None

    def _ciclo_desde_url(self, url: str) -> str | None:
        """
        Construye el ciclo AAAAMM a partir de la URL.

        Mes: del nombre del archivo. Año: del nombre o de la carpeta.
        """
        nombre = url.split("/")[-1]
        mes = self._mes_desde_texto(nombre)
        if not mes:
            return None

        # Año: preferir el del nombre del archivo; si no, el de la ruta completa.
        anios = re.findall(r"(20\d{2})", nombre)
        if not anios:
            anios = re.findall(r"(20\d{2})", url)
        if not anios:
            return None
        anio = anios[-1]  # el más específico suele ser el último
        return f"{anio}{mes}"

    @staticmethod
    def _extraer_ciclo_y_fecha(nombre_archivo: str) -> tuple[str, str]:
        match = re.search(r"(\d{6})", nombre_archivo)
        if not match:
            raise ValueError(
                f"No se pudo extraer el ciclo de '{nombre_archivo}'. "
                "Se esperaba AAAAMM en el nombre."
            )
        ciclo = match.group(1)
        fecha = datetime(int(ciclo[:4]), int(ciclo[4:6]), 1).strftime("%Y-%m-%d")
        return ciclo, fecha

    # ── Helpers de extracción de texto ────────────────────────────────────────

    @staticmethod
    def _texto_pdf(pdf_bytes: bytes) -> str:
        """Extrae el texto de todas las páginas con fitz."""
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return "\n".join(page.get_text() for page in doc)

    @staticmethod
    def _texto_util(texto: str) -> bool:
        """Heurística: el texto sirve si contiene las etiquetas de componentes."""
        return bool(texto) and bool(re.search(r"Gm,?\s*i|Costo\s+compra", texto, re.I))

    def _texto_pdf_ocr(self, pdf_bytes: bytes, escala: float = 2.5) -> str:
        """Renderiza cada página a imagen y aplica OCR (fallback tipo CENS)."""
        import easyocr

        if self._ocr_reader is None:
            self.logger.info("  Inicializando OCR (primer uso, puede tardar)...")
            self._ocr_reader = easyocr.Reader(["en"], verbose=False)

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        lineas: list[str] = []
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(escala, escala))
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
            if pix.n == 4:
                img = img[:, :, :3]
            for _bbox, txt, conf in self._ocr_reader.readtext(img, detail=1, paragraph=False):
                if conf >= 0.4:
                    lineas.append(txt.strip())
        return "\n".join(lineas)

    # ── Parseo del texto a {nivel: {componente: valor}} ───────────────────────
    def _componente_de(self, linea: str) -> str | None:
        for comp, patron in self._PATRONES_COMPONENTE:
            if re.search(patron, linea, re.I):
                return comp
        return None

    @staticmethod
    def _lineas_limpias(texto: str) -> list[str]:
        return [l.strip() for l in texto.splitlines() if l.strip()]

    def _es_linea_numero(self, linea: str) -> bool:
        return bool(self._RE_NUM_LINEA.fullmatch(linea.strip()))

    def _leer_numeros_consecutivos(
        self,
        lineas: list[str],
        start_idx: int,
        cantidad: int,
    ) -> tuple[list[float], int]:
        """
        Lee ``cantidad`` líneas numéricas consecutivas a partir de ``start_idx``.

        Returns:
            (numeros, ultimo_indice_consumido)
        """
        numeros: list[float] = []
        idx = start_idx
        while idx < len(lineas) and len(numeros) < cantidad:
            linea = lineas[idx].strip()
            if not self._es_linea_numero(linea):
                break
            numeros.append(float(linea))
            idx += 1
        return numeros, idx - 1

    def _capturar_bloque(
        self,
        lineas: list[str],
        idx_cu: int,
        cantidad_numeros: int,
    ) -> dict[str, object] | None:
        """
        Captura un bloque CU + componentes a partir de una línea ``CU Total``.
        """
        cu, idx_cursor = self._leer_numeros_consecutivos(
            lineas, idx_cu + 1, cantidad_numeros
        )
        if len(cu) != cantidad_numeros:
            return None

        componentes: dict[str, list[float]] = {}
        for comp_name in self._ORDEN_COMPONENTES:
            idx_etiqueta = idx_cursor + 1
            while idx_etiqueta < len(lineas):
                candidato = lineas[idx_etiqueta]
                comp = self._componente_de(candidato)
                if comp is not None:
                    break
                # Si aparece otro encabezado CU antes del siguiente componente,
                # este bloque ya no es consistente.
                if self._RE_CU.search(candidato):
                    return None
                idx_etiqueta += 1

            if idx_etiqueta >= len(lineas):
                return None

            comp_detectado = self._componente_de(lineas[idx_etiqueta])
            if comp_detectado != comp_name:
                return None

            valores, idx_cursor = self._leer_numeros_consecutivos(
                lineas, idx_etiqueta + 1, cantidad_numeros
            )
            if len(valores) != cantidad_numeros:
                return None
            componentes[comp_name] = valores

        return {
            "line_start": idx_cu,
            "line_end": idx_cursor,
            "cu": cu,
            "componentes": componentes,
        }

    def _buscar_bloque(
        self,
        lineas: list[str],
        cantidad_numeros: int,
        start_idx: int = 0,
        must_contain: str | None = None,
    ) -> dict[str, object] | None:
        """
        Busca el primer bloque consistente de ``cantidad_numeros`` valores.
        """
        for idx in range(start_idx, len(lineas)):
            linea = lineas[idx]
            if not self._RE_CU.search(linea):
                continue

            bloque = self._capturar_bloque(lineas, idx, cantidad_numeros)
            if not bloque:
                continue

            if must_contain:
                # Algunas marcas del layout quedan justo después del bloque.
                contexto_fin = min(len(lineas), bloque["line_end"] + 4)
                rango = " ".join(
                    lineas[bloque["line_start"]: contexto_fin]
                )
                if must_contain not in rango:
                    continue
            return bloque
        return None

    def _fila_desde_bloque(
        self,
        bloque: dict[str, object],
        indice_variante: int,
        tipo_red: str | None,
        comb_nt: str | None,
    ) -> dict[str, float | str | None]:
        componentes = bloque["componentes"]
        fila: dict[str, float | str | None] = {
            comp: componentes[comp][indice_variante]
            for comp in self._ORDEN_COMPONENTES
        }
        fila["CU"] = bloque["cu"][indice_variante]
        fila["Tipo_Red"] = tipo_red
        fila["Comb_NT"] = comb_nt
        return fila

    def _parsear_texto(self, texto: str) -> dict[int, dict[str, float | None]]:
        """
        Reconstruye {nivel: {componente: valor}} para el mercado EPM-Antioquia.

        El texto de EPM llega como una secuencia vertical:
        etiqueta -> N líneas numéricas -> siguiente etiqueta.
        Por eso el parseo se hace por bloques y no por filas rígidas.
        """
        # Restringir al mercado propio de EPM (antes de los mercados de terceros).
        m = self._FIN_MERCADO_EPM.search(texto)
        region = texto[: m.start()] if m else texto

        lineas = self._lineas_limpias(region)

        data: dict[int, dict[str, float | None]] = {}
        bloque_nivel_1 = self._buscar_bloque(
            lineas,
            cantidad_numeros=3,
            must_contain="B.T.:",
        )
        if bloque_nivel_1:
            self.logger.info(
                "  Bloque Nivel 1 detectado: lineas %d-%d",
                bloque_nivel_1["line_start"],
                bloque_nivel_1["line_end"],
            )
            data[1] = self._fila_desde_bloque(
                bloque_nivel_1,
                indice_variante=0,  # Propiedad OR
                tipo_red="Monomia",
                comb_nt="Propiedad OR",
            )

        start_234 = (bloque_nivel_1["line_end"] + 1) if bloque_nivel_1 else 0
        bloque_niveles_234 = self._buscar_bloque(
            lineas,
            cantidad_numeros=6,
            start_idx=start_234,
            must_contain="CU Monomio",
        )
        if bloque_niveles_234:
            self.logger.info(
                "  Bloque Niveles 2-4 detectado: lineas %d-%d",
                bloque_niveles_234["line_start"],
                bloque_niveles_234["line_end"],
            )
            for nivel, idx in ((2, 1), (3, 3), (4, 5)):
                data[nivel] = self._fila_desde_bloque(
                    bloque_niveles_234,
                    indice_variante=idx,  # Fuera de punta por nivel
                    tipo_red="Fuera de Punta",
                    comb_nt=f"Nivel {nivel}",
                )

        return data

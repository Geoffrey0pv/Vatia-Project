"""
Scraper CENS — extrae la tabla de componentes CU de los PDFs de Tarifas.

Las páginas con la tabla están embebidas como imágenes rasterizadas
→ se usa PyMuPDF para renderizar + easyocr para OCR.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
import easyocr
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from etl.base_scraper import ScraperBase


class CensScraper(ScraperBase):
    """
    Scraper para CENS — descarga PDFs de la web oficial y extrae la tabla
    de componentes del Costo Unitario (G, T, D, Cv, PR, R, CU) por nivel
    de tensión mediante OCR.
    """

    competidor = "CENS"

    URL_TARIFAS    = "https://www.cens.com.co/clientes-y-usuarios/tarifas-de-energia"
    COMERCIALIZADOR = "CENS"
    DUENO_RED      = "100% OPERADOR"

    # ── Mapeos de componentes y niveles ──────────────────────────────────────
    MAPEO_COMPONENTES: list[tuple[str, str]] = [
        (r"^G$",          "G"),
        (r"^T$",          "T"),
        (r"DtUN|DtN|^D$", "D"),
        (r"^Cv$",         "Cv"),
        (r"^PR$",         "PR"),
        (r"^R$",          "R"),
        (r"CUv|^CU$",     "CU"),
    ]

    MAPEO_NIVELES_PDF: list[tuple[str, str]] = [
        (r"1-2.*CENS", "1"),
        (r"Nivel\s*2", "2"),
        (r"Nivel\s*3", "3"),
        (r"Nivel\s*4", "4"),
    ]
    _ORDEN_COMPONENTES = ["G", "T", "D", "Cv", "PR", "R"]
    _TOL_CU = 0.5

    def __init__(self, directorio_raw: Path | None = None) -> None:
        super().__init__(directorio_raw)
        self._ocr_reader: easyocr.Reader | None = None

    # ── Interfaz ScraperBase ─────────────────────────────────────────────────

    def obtener_enlaces(self) -> list[tuple[str, str]]:
        """
        Raspa la página de CENS y devuelve todos los PDFs de Tarifas disponibles.

        Returns:
            Lista de ``(nombre_archivo, url_descarga)`` ordenada por ciclo.
        """
        self.logger.info("[CENS] Accediendo a: %s", self.URL_TARIFAS)
        respuesta = requests.get(self.URL_TARIFAS, headers=self._DEFAULT_HEADERS, timeout=30)
        respuesta.raise_for_status()

        soup = BeautifulSoup(respuesta.text, "html.parser")
        enlaces: list[tuple[str, str]] = []

        for tabla in soup.find_all("table"):
            encabezados = [th.get_text(strip=True) for th in tabla.find_all("th")]
            if not encabezados or not re.search(r"periodo|per[ií]odo", encabezados[0], re.I):
                continue

            self.logger.info("  ✔ Tabla encontrada: %s", encabezados)

            for fila in tabla.find_all("tr"):
                celdas = fila.find_all("td")
                if not celdas:
                    continue
                tag_a = celdas[0].find("a", href=True)
                if tag_a:
                    href = tag_a["href"].split("?")[0]
                    if ".pdf" not in href.lower():
                        continue
                    url_desc = href if href.startswith("http") else f"https://www.cens.com.co{href}"
                    nombre = url_desc.split("/")[-1]
                    enlaces.append((nombre, url_desc))
                    ciclo = re.search(r"\d{6}", nombre)
                    self.logger.info("  → %s (ciclo %s)", nombre, ciclo.group() if ciclo else "?")
            break

        # Fallback: buscar por patrón en href si no se encontró tabla
        if not enlaces:
            self.logger.warning("  Tabla 'Periodo' no detectada — búsqueda por patrón.")
            for tag_a in soup.find_all("a", href=True):
                href = tag_a["href"]
                if re.search(r"Tarifas.*\.pdf", href, re.I):
                    href_limpio = href.split("?")[0]
                    url_desc = href_limpio if href_limpio.startswith("http") \
                        else f"https://www.cens.com.co{href_limpio}"
                    nombre = url_desc.split("/")[-1]
                    enlaces.append((nombre, url_desc))

        if not enlaces:
            raise ValueError(
                "No se encontró ningún enlace PDF en la página de CENS. "
                "Verifique la URL o actualice el patrón de búsqueda."
            )

        enlaces.sort(
            key=lambda t: re.search(r"\d{6}", t[0]).group()
            if re.search(r"\d{6}", t[0]) else t[0]
        )
        self.logger.info("[CENS] %d PDF(s) encontrado(s)", len(enlaces))
        return enlaces

    def extraer(self, contenido: bytes, nombre_archivo: str) -> pd.DataFrame:
        """
        Extrae la tabla de componentes CU de un PDF de Tarifas CENS mediante OCR.

        Returns:
            DataFrame con 4 filas (una por nivel de tensión) y columnas
            Fecha, Ciclo, Comercializador, Dueno_Red, Nivel_Tension, G…CU.
        """
        ciclo, fecha = self._extraer_ciclo_y_fecha(nombre_archivo)
        self.logger.info("  Ciclo: %s → Fecha: %s", ciclo, fecha)

        pagina_idx = self._encontrar_pagina_tabla(contenido)
        self.logger.info("  Tabla CU en página %d (imagen → OCR)", pagina_idx + 1)

        items = self._ocr_pagina(contenido, pagina_idx)
        self.logger.info("  OCR: %d elemento(s) detectados", len(items))

        if not items:
            raise RuntimeError(
                f"OCR no detectó texto en la página {pagina_idx + 1}. "
                "Ajuste la escala o verifique el PDF."
            )

        filas = self._agrupar_en_filas(items)
        self.logger.info("  Filas OCR agrupadas: %d", len(filas))

        data_por_nivel = self._parsear_filas_ocr(
            filas, self.MAPEO_COMPONENTES, self.MAPEO_NIVELES_PDF
        )

        df_filas: list[dict] = []
        filas_descartadas = 0
        for nivel, componentes in data_por_nivel.items():
            fila: dict = {
                "Fecha":           fecha,
                "Ciclo":           ciclo,
                "Comercializador": self.COMERCIALIZADOR,
                "Dueno_Red":       self.DUENO_RED,
                "Nivel_Tension":   nivel,
            }
            for _, comp_name in self.MAPEO_COMPONENTES:
                fila[comp_name] = componentes.get(comp_name)
            diff_cu = self._diff_cu(fila)
            if diff_cu is not None and diff_cu <= self._TOL_CU:
                df_filas.append(fila)
            else:
                filas_descartadas += 1
                self.logger.warning(
                    "  Fila descartada por integridad CU (ciclo=%s nivel=%s diff=%s): %s",
                    ciclo,
                    nivel,
                    f"{diff_cu:.4f}" if diff_cu is not None else "n/a",
                    {k: fila.get(k) for k in self._ORDEN_COMPONENTES + ["CU"]},
                )

        if not df_filas:
            raise RuntimeError(f"No se extrajeron filas de '{nombre_archivo}'.")

        df = pd.DataFrame(df_filas)
        if filas_descartadas:
            self.logger.warning(
                "  %d fila(s) descartada(s) por integridad CU en %s",
                filas_descartadas,
                nombre_archivo,
            )
        if len(df) < 4:
            self.logger.warning(
                "  PDF procesado parcialmente: se esperaban 4 niveles, se obtuvieron %d",
                len(df),
            )
        self.logger.info("  → %d filas × %d columnas", len(df), len(df.columns))
        return df

    # ── Métodos privados OCR ─────────────────────────────────────────────────

    def _get_ocr_reader(self) -> easyocr.Reader:
        """Inicializa el lector OCR como singleton (modelos ~200 MB, solo la primera vez)."""
        if self._ocr_reader is None:
            self.logger.info("  Inicializando OCR (puede tardar en el primer uso)...")
            self._ocr_reader = easyocr.Reader(["en"], verbose=False)
            self.logger.info("  OCR listo.")
        return self._ocr_reader

    @staticmethod
    def _extraer_ciclo_y_fecha(nombre_archivo: str) -> tuple[str, str]:
        match = re.search(r"(\d{6})", nombre_archivo)
        if not match:
            raise ValueError(
                f"No se pudo extraer el ciclo de '{nombre_archivo}'. "
                "Se esperaba AAAAMM en el nombre."
            )
        ciclo = match.group(1)
        fecha = datetime(int(ciclo[:4]), int(ciclo[4:]), 1).strftime("%Y-%m-%d")
        return ciclo, fecha

    @staticmethod
    def _parse_numero(valor: object) -> float | None:
        """Convierte texto OCR a float, tolerando variantes de formato."""
        s = str(valor or "").strip()
        if not s or s.lower() in ("none", "nan", "-", "n/a", ""):
            return None
        s = re.sub(r"[^\d.,]", "", s)
        if not s:
            return None
        if "." in s and "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None

    def _diff_cu(self, fila: dict) -> float | None:
        """Retorna |CU - suma(componentes)| o None si falta algún valor crítico."""
        try:
            cu = float(fila["CU"])
            suma = sum(float(fila[c]) for c in self._ORDEN_COMPONENTES)
        except (TypeError, ValueError, KeyError):
            return None
        return abs(cu - suma)

    def _ocr_pagina(self, pdf_bytes: bytes, pagina_idx: int, escala: float = 2.5) -> list:
        """Renderiza una página y aplica OCR. Devuelve lista de (y, x, texto)."""
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[pagina_idx]
        pix = page.get_pixmap(matrix=fitz.Matrix(escala, escala))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]

        reader = self._get_ocr_reader()
        results = reader.readtext(img, detail=1, paragraph=False)

        items = []
        for bbox, texto, conf in results:
            if conf < 0.4:
                continue
            xc = (bbox[0][0] + bbox[2][0]) / 2
            yc = (bbox[0][1] + bbox[2][1]) / 2
            items.append((yc, xc, texto.strip()))
        return items

    @staticmethod
    def _agrupar_en_filas(items: list, tolerancia_y: float = 18.0) -> list[list[str]]:
        """Agrupa items OCR en filas por proximidad en Y."""
        if not items:
            return []
        items_ord = sorted(items, key=lambda x: x[0])
        filas: list[list] = []
        fila_actual = [items_ord[0]]
        y_ref = items_ord[0][0]

        for item in items_ord[1:]:
            if abs(item[0] - y_ref) <= tolerancia_y:
                fila_actual.append(item)
            else:
                fila_actual.sort(key=lambda x: x[1])
                filas.append([t for _, _, t in fila_actual])
                fila_actual = [item]
                y_ref = item[0]

        if fila_actual:
            fila_actual.sort(key=lambda x: x[1])
            filas.append([t for _, _, t in fila_actual])
        return filas

    def _parsear_filas_ocr(
        self,
        filas: list[list[str]],
        mapeo_componentes: list[tuple[str, str]],
        mapeo_niveles: list[tuple[str, str]],
    ) -> dict[str, dict[str, float | None]]:
        """Reconstruye {nivel: {componente: valor}} a partir de las filas OCR."""
        # 1. Localizar fila de encabezados de nivel
        header_idx = None
        for i, fila in enumerate(filas):
            texto = " ".join(fila)
            if re.search(r"CENS|Nivel\s*\d|Compartido|Particular", texto, re.I):
                header_idx = i
                break

        if header_idx is None:
            raise ValueError(
                "No se encontró la fila de encabezados de nivel. "
                f"Filas disponibles: {[' | '.join(f) for f in filas[:8]]}"
            )

        fila_header = filas[header_idx]
        self.logger.debug("  Encabezados (fila %d): %s", header_idx, fila_header)

        # 2. Mapear posición de columna → nivel
        col_to_nivel: dict[int, str] = {}
        for col_idx, celda in enumerate(fila_header):
            if col_idx == 0:
                continue
            for patron, nivel_name in mapeo_niveles:
                if re.search(patron, celda, re.I):
                    col_to_nivel[col_idx] = nivel_name
                    break

        # Fallback: probar fila siguiente si no se mapeó nada
        if not col_to_nivel and header_idx + 1 < len(filas):
            fila_header = filas[header_idx + 1]
            for col_idx, celda in enumerate(fila_header):
                if col_idx == 0:
                    continue
                for patron, nivel_name in mapeo_niveles:
                    if re.search(patron, celda, re.I):
                        col_to_nivel[col_idx] = nivel_name
                        break
            if col_to_nivel:
                header_idx += 1

        if not col_to_nivel:
            raise ValueError(
                f"No se mapeó ningún nivel de tensión. "
                f"Fila analizada: {fila_header}\n"
                f"Patrones esperados: {[p for p, _ in mapeo_niveles]}"
            )

        # 3. Procesar filas de datos
        data: dict[str, dict[str, float | None]] = {n: {} for n in col_to_nivel.values()}
        comp_names_ordered = [name for _, name in mapeo_componentes]
        asignados: set[str] = set()
        n_cols_esperadas = len(fila_header)

        for fila in filas[header_idx + 1:]:
            if not fila:
                continue

            # Detectar fila sin etiqueta: G y T son valores nacionales idénticos en todas
            # las columnas y el OCR no detecta las letras sueltas con suficiente confianza.
            etiqueta_faltante = (
                len(fila) in (n_cols_esperadas - 1, n_cols_esperadas)
                and all(self._parse_numero(v) is not None for v in fila)
                and len(set(fila)) == 1
            )
            if etiqueta_faltante:
                fila = [""] + fila  # Insertar etiqueta vacía → fallback posicional

            etiqueta = fila[0].strip()

            componente = None
            for patron, comp_name in mapeo_componentes:
                if re.search(patron, etiqueta, re.I):
                    componente = comp_name
                    break

            # Fallback posicional para etiquetas no detectadas
            if componente is None and etiqueta == "":
                for cn in comp_names_ordered:
                    if cn not in asignados:
                        componente = cn
                        self.logger.debug(
                            "  ⚠ Etiqueta no detectada → asignada como '%s' (vals: %s)",
                            cn, fila[1:4],
                        )
                        break

            if componente is None:
                continue

            asignados.add(componente)
            for col_idx, nivel in col_to_nivel.items():
                valor_raw = fila[col_idx] if col_idx < len(fila) else None
                data[nivel][componente] = self._parse_numero(valor_raw)

        return data

    @staticmethod
    def _encontrar_pagina_tabla(pdf_bytes: bytes) -> int:
        """Retorna el índice (0-based) de la primera página sin texto (imagen = tabla)."""
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for i, page in enumerate(doc):
            if not page.get_text().strip():
                return i
        return len(doc) - 1  # Fallback: última página

"""
Scraper AFINIA — extrae la tabla de componentes CU de los PDFs de Tarifas.

La página de tarifas de Afinia (DotNetNuke) enlaza un PDF por publicación, con
una URL del tipo::

    /Portals/afinia/documentos/Documentos Tarifas y subsidios/Tarifas 2026/Mayo/tarifa-afinia-20-05-2026.pdf

El nombre del archivo incluye la fecha de publicación ``DD-MM-AAAA``, de la que
se deriva directamente el ciclo ``AAAAMM``.

A diferencia de EPM, el PDF de Afinia presenta una **única tabla posicional**
(no se puede leer con texto plano porque ``fitz`` desordena las columnas). Por
eso la extracción usa coordenadas de las palabras (``page.get_text("words")``)
y reconstruye la tabla por columnas. Los componentes G, T y R son compartidos
por todos los niveles (aparecen una sola vez bajo su encabezado) y D, Cv, PR y
CU varían por nivel. La identidad CU = G+T+D+Cv+PR+R coincide con la columna
"Cu Mes con COT" del documento.

Si una publicación fuera una imagen rasterizada, se cae a OCR con easyocr
(igual que CENS); en ese caso se usa un parseo de respaldo por texto plano.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import fitz  # PyMuPDF
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from etl.base_scraper import ScraperBase


class AfiniaScraper(ScraperBase):
    """
    Scraper para AFINIA — raspa la página de tarifas, descarga los PDFs y
    extrae los componentes del Costo Unitario (G, T, D, Cv, PR, R, CU) por
    nivel de tensión (1-4).

    Nota técnica:
    - Validado inicialmente para la ventana 202501–202605.
    - Para PDFs recientes se prioriza un parser textual.
    - Se descartan filas cuya identidad CU no cierre.
    - Se permiten PR negativos pequeños si la suma cierra con CU.
    """

    competidor = "AFINIA"

    URL_TARIFAS     = "https://afinia.com.co/inicio/tarifas-y-subsidios"
    BASE_URL        = "https://afinia.com.co"
    COMERCIALIZADOR = "AFINIA"
    OPERADOR_RED    = "AFINIA"
    DUENO_RED       = "100% OPERADOR"
    MIN_CICLO       = "202501"
    MAX_CICLO       = "202612"

    # Meses en espanol -> numero (ano/mes desde la carpeta de la URL)
    _MESES: dict[str, str] = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "setiembre": "09", "octubre": "10",
        "noviembre": "11", "diciembre": "12",
    }

    # Fecha DD-MM-AAAA dentro del nombre del archivo (separador -, _ o /).
    _RE_FECHA_ARCHIVO = re.compile(r"(\d{1,2})[-_/](\d{1,2})[-_/](20\d{2})")

    # Encabezados de columna del PDF (texto exacto en la fila de encabezado).
    _ENCABEZADOS = {
        "G":     ["Generación"],
        "T":     ["STN"],
        "PR":    ["PR:"],
        "D":     ["D:"],
        "R":     ["Restricciones"],
        "Cv":    ["C/cialización"],
        "CUsin": ["sin"],
        "CUcon": ["con"],
    }
    # Filas representativas por nivel de tension (etiqueta a la izquierda).
    _PATRON_NIVEL = {
        1: r"^Nivel\s*1\s*OR\b",
        2: r"^Nivel\s*2$",
        3: r"^Nivel\s*3$",
        4: r"^Nivel\s*4$",
    }
    _ORDEN_COMPONENTES = ["G", "T", "D", "Cv", "PR", "R"]
    _TOL_CU = 1.0

    # Tolerancias de geometria (puntos PDF).
    _TOL_COL = 25.0
    _TOL_FILA = 3.5
    _Y_HEADER = (98.0, 108.0)

    def __init__(self, directorio_raw: Path | None = None) -> None:
        super().__init__(directorio_raw)
        self._ocr_reader = None  # lazy

    # ── Interfaz ScraperBase ──────────────────────────────────────────────

    def obtener_enlaces(self) -> list[tuple[str, str]]:
        """
        Raspa la pagina de tarifas de Afinia y devuelve los PDFs disponibles.

        El ciclo (AAAAMM) se deriva de la fecha DD-MM-AAAA del nombre del
        archivo; si no, se usa el mes/ano de la carpeta de la URL. Devuelve un
        nombre sintetico ``Tarifas_AFINIA_<AAAAMM>.pdf``.
        """
        self.logger.info("[AFINIA] Accediendo a: %s", self.URL_TARIFAS)
        resp = requests.get(self.URL_TARIFAS, headers=self._DEFAULT_HEADERS, timeout=30)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        vistos: dict[str, str] = {}

        for tag_a in soup.find_all("a", href=True):
            href_raw = tag_a["href"]
            url_desc = self._normalizar_url_pdf(href_raw)
            if not url_desc:
                continue

            nombre = unquote(url_desc.split("/")[-1].split("?")[0])
            if "tarifa" not in nombre.lower() and "afinia" not in nombre.lower():
                continue

            ciclo = self._ciclo_desde_url(url_desc)
            if not ciclo:
                self.logger.debug("  ? ciclo no determinable: %s", nombre)
                continue
            if not (self.MIN_CICLO <= ciclo <= self.MAX_CICLO):
                continue

            vistos[ciclo] = url_desc

        if not vistos:
            raise ValueError(
                "No se encontro ningun PDF de tarifas en la pagina de Afinia. "
                "Verifique la URL o el patron de busqueda."
            )

        enlaces = [
            (f"Tarifas_AFINIA_{ciclo}.pdf", url)
            for ciclo, url in sorted(vistos.items())
        ]
        self.logger.info("[AFINIA] %d ciclo(s) unico(s) encontrado(s)", len(enlaces))
        return enlaces

    def extraer(self, contenido: bytes, nombre_archivo: str) -> pd.DataFrame:
        """
        Extrae los componentes CU por nivel de tension de un PDF de Afinia.
        Usa extraccion posicional si el PDF tiene texto; si es imagen, OCR.
        """
        ciclo, fecha = self._extraer_ciclo_y_fecha(nombre_archivo)
        self.logger.info("  Ciclo: %s -> Fecha: %s", ciclo, fecha)

        doc = fitz.open(stream=contenido, filetype="pdf")
        if self._tiene_texto(doc):
            data_por_nivel = self._parsear_pdf_texto(doc)
            if not data_por_nivel:
                data_por_nivel = self._parsear_pdf(doc)
        else:
            self.logger.info("  PDF sin texto -> fallback OCR")
            texto = self._texto_pdf_ocr(doc)
            data_por_nivel = self._parsear_texto_ocr(texto)

        if not data_por_nivel:
            raise RuntimeError(
                f"No se extrajo ningun nivel de tension de '{nombre_archivo}'. "
                "El layout del PDF pudo haber cambiado."
            )

        df_filas: list[dict] = []
        invalidas = 0
        for nivel in sorted(data_por_nivel):
            comp = data_por_nivel[nivel]
            fila = {
                "Fecha":           fecha,
                "Ciclo":           ciclo,
                "Operador_Red":    self.OPERADOR_RED,
                "Comercializador": self.COMERCIALIZADOR,
                "Dueno_Red":       self.DUENO_RED,
                "Nivel_Tension":   nivel,
                "G":  comp.get("G"),  "T": comp.get("T"),  "D": comp.get("D"),
                "Cv": comp.get("Cv"), "PR": comp.get("PR"), "R": comp.get("R"),
                "CU": comp.get("CU"),
            }
            if self._fila_integra(fila):
                df_filas.append(fila)
            else:
                invalidas += 1
                self.logger.warning(
                    "  Fila descartada por integridad CU (nivel %s): %s",
                    nivel,
                    {k: fila[k] for k in ["G", "T", "D", "Cv", "PR", "R", "CU"]},
                )

        df = pd.DataFrame(df_filas)
        if invalidas and df.empty:
            raise RuntimeError(
                f"Todas las filas extraidas de '{nombre_archivo}' fallaron la validacion CU."
            )
        if len(df) < 4:
            self.logger.warning(
                "PDF procesado parcialmente: se esperaban 4 niveles, se obtuvieron %d",
                len(df),
            )
        self.logger.info("  -> %d filas x %d columnas", len(df), len(df.columns))
        return df

    def ejecutar(self) -> pd.DataFrame:
        """
        Ejecuta Afinia con resumen explícito, priorizando integridad reciente.
        """
        self.logger.warning(
            "[AFINIA] Ventana validada y soportada operativamente: %s..%s. "
            "No se abre histórico adicional en esta ejecución.",
            self.MIN_CICLO,
            self.MAX_CICLO,
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
                self.logger.info("✔ %s procesado (%d filas)", nombre, len(df))
            except Exception as exc:
                fallidos += 1
                self.logger.error("✖ %s: %s", nombre, exc)

        filas_finales = sum(len(df) for df in resultados)
        self.logger.info(
            "[AFINIA] Resumen -> encontrados=%d descargados=%d completos=%d "
            "parciales=%d fallidos=%d filas_validas=%d",
            encontrados,
            descargados,
            completos,
            parciales,
            fallidos,
            filas_finales,
        )
        self.logger.info(
            "[AFINIA] PDFs recientes procesados=%d -> filas válidas recientes=%d",
            completos + parciales,
            filas_finales,
        )

        if not resultados:
            raise RuntimeError("AFINIA: no se procesó ningún PDF válido.")

        return pd.concat(resultados, ignore_index=True)

    # ── Helpers de enlaces / ciclo ─────────────────────────────────────────

    def _ciclo_desde_url(self, url: str) -> str | None:
        """Ciclo AAAAMM: fecha DD-MM-AAAA del nombre; si no, mes/ano de carpeta."""
        ruta = unquote(url)
        nombre = ruta.split("/")[-1]

        m = self._RE_FECHA_ARCHIVO.search(nombre)
        if m:
            _dia, mes, anio = m.groups()
            return f"{anio}{int(mes):02d}"

        ruta_baja = ruta.lower()
        mes = next((n for nom, n in self._MESES.items() if nom in ruta_baja), None)
        anios = re.findall(r"(20\d{2})", ruta)
        if mes and anios:
            return f"{anios[-1]}{mes}"
        return None

    def _normalizar_url_pdf(self, href_raw: str) -> str | None:
        """
        Limpia hrefs corruptos y conserva solo una URL/relpath PDF válido de Afinia.
        """
        href = unquote((href_raw or "").strip())
        href = re.sub(r"\s+", " ", href)
        if not href:
            return None

        candidatos: list[str] = []

        # Recuperar URLs absolutas incluso si el path tiene espacios.
        for prefix in ("https://afinia.com.co", "http://afinia.com.co"):
            start = href.lower().find(prefix)
            while start != -1:
                end = href.lower().find(".pdf", start)
                if end != -1:
                    tail_end = end + 4
                    while tail_end < len(href) and href[tail_end] not in (" ", "\"", "'"):
                        tail_end += 1
                    candidatos.append(href[start:tail_end].strip())
                    start = href.lower().find(prefix, tail_end)
                else:
                    break

        # Recuperar rutas relativas si no vino absoluta.
        if not candidatos:
            for match in re.finditer(r"/Portals/.*?\.pdf(?:\?[^\"']*)?", href, re.I):
                candidatos.append(match.group(0).strip())

        for candidato in candidatos:
            absoluta = urljoin(f"{self.BASE_URL}/", candidato.replace(" ", "%20"))
            parsed = urlparse(absoluta)
            host = (parsed.hostname or "").lower()
            if host != "afinia.com.co":
                continue
            if ".pdf" not in parsed.path.lower():
                continue
            return absoluta

        return None

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

    # ── Extraccion posicional (PDF con texto) ──────────────────────────────

    @staticmethod
    def _tiene_texto(doc) -> bool:
        return bool(doc[0].get_text("words"))

    @staticmethod
    def _es_numero(texto: str) -> bool:
        return bool(re.match(r"^\d[\d.]*,\d+$", texto))

    @staticmethod
    def _val(texto: str) -> float:
        return float(texto.replace(".", "").replace(",", "."))

    def _texto_lineas(self, doc) -> list[str]:
        texto = "\n".join(page.get_text() for page in doc)
        return [line.strip() for line in texto.splitlines() if line.strip()]

    def _fila_integra(self, fila: dict) -> bool:
        try:
            valores = [float(fila[c]) for c in self._ORDEN_COMPONENTES]
            cu = float(fila["CU"])
        except (TypeError, ValueError):
            return False

        if any(v < -50 for v in valores) or cu < 0:
            return False
        if fila.get("T") is not None and float(fila["T"]) > 500:
            return False

        diff = abs(sum(valores) - cu)
        return diff <= self._TOL_CU

    def _parsear_pdf_texto(self, doc) -> dict:
        """
        Parser textual para los PDFs recientes (2025-2026) donde la tabla sale
        como secuencia vertical de etiquetas y números.
        """
        lineas = self._texto_lineas(doc)

        footer = self._shared_footer_values(lineas)
        if not footer:
            return {}
        G, T, PR1, R, PR2 = footer

        filas: dict[int, list[float]] = {}
        for nivel, expected in ((1, 4), (2, 4), (3, 5), (4, 5)):
            valores = self._row_values_for_level(lineas, nivel, expected)
            if not valores:
                return {}
            filas[nivel] = valores

        data = {
            1: {"G": G, "T": T, "D": filas[1][0], "Cv": filas[1][1], "PR": PR1, "R": R, "CU": filas[1][-1]},
            2: {"G": G, "T": T, "D": filas[2][0], "Cv": filas[2][1], "PR": PR2, "R": R, "CU": filas[2][-1]},
            3: {"G": G, "T": T, "D": filas[3][1], "Cv": filas[3][2], "PR": filas[3][0], "R": R, "CU": filas[3][-1]},
            4: {"G": G, "T": T, "D": filas[4][1], "Cv": filas[4][2], "PR": filas[4][0], "R": R, "CU": filas[4][-1]},
        }
        return data

    @staticmethod
    def _extract_decimal_lines(lines: list[str]) -> list[float]:
        values: list[float] = []
        for line in lines:
            if re.fullmatch(r"-?\d[\d.]*,\d+", line):
                values.append(float(line.replace(".", "").replace(",", ".")))
        return values

    def _shared_footer_values(self, lineas: list[str]) -> list[float] | None:
        """
        En los PDFs recientes, los componentes compartidos se consolidan justo
        alrededor de la línea 'AFINIA S.A.S E.S.P'. El patrón útil son 5 números:
        G, T, PR nivel 1, R, PR nivel 2.
        """
        try:
            idx_afinia = next(i for i, line in enumerate(lineas) if "AFINIA" in line)
        except StopIteration:
            return None

        ventana = lineas[max(0, idx_afinia - 15):min(len(lineas), idx_afinia + 12)]
        valores = self._extract_decimal_lines(ventana)
        if len(valores) < 5:
            return None
        return valores[-5:]

    def _row_values_for_level(
        self,
        lineas: list[str],
        nivel: int,
        expected_values: int,
    ) -> list[float] | None:
        patrones = {
            1: r"^Nivel\s*1\s*OR\b",
            2: r"^Nivel\s*2$",
            3: r"^Nivel\s*3$",
            4: r"^Nivel\s*4$",
        }
        patron = patrones[nivel]
        for idx, line in enumerate(lineas):
            if not re.search(patron, line, re.I):
                continue
            ventana = lineas[idx + 1: idx + 1 + expected_values + 2]
            valores = self._extract_decimal_lines(ventana)
            if len(valores) >= expected_values:
                return valores[:expected_values]
        return None

    def _parsear_pdf(self, doc) -> dict:
        """
        Reconstruye {nivel: {componente: valor}} desde la tabla posicional.
        G, T, R compartidos; D, Cv, CU(con COT) por fila; PR en fila o flotante.
        """
        page = doc[0]
        W = page.get_text("words")

        def xc(w):
            return (w[0] + w[2]) / 2

        y0_h, y1_h = self._Y_HEADER
        cols: dict = {}
        for col, etiquetas in self._ENCABEZADOS.items():
            xs = [xc(w) for w in W if w[4] in etiquetas and y0_h <= w[1] <= y1_h]
            if xs:
                cols[col] = sum(xs) / len(xs)

        def col_de(x):
            mejor, dmin = None, self._TOL_COL
            for c, cx in cols.items():
                d = abs(x - cx)
                if d <= dmin:
                    dmin, mejor = d, c
            return mejor

        y_fin = min((w[1] for w in W if w[4] == "Cargo"), default=1e9)
        toks = []
        for w in W:
            if y1_h <= w[1] < y_fin and self._es_numero(w[4]):
                c = col_de(xc(w))
                if c:
                    toks.append((w[1], c, self._val(w[4])))

        def compartido(col):
            vs = [v for _y, c, v in toks if c == col]
            return vs[0] if vs else None

        G, T, R = compartido("G"), compartido("T"), compartido("R")
        pr_flotantes = [(y, v) for y, c, v in toks if c == "PR"]

        def etiqueta_en(y):
            ws = [w for w in W if abs(w[1] - y) <= self._TOL_FILA and xc(w) < 132]
            ws.sort(key=lambda w: w[0])
            return " ".join(w[4] for w in ws)

        ys = sorted({round(y, 1) for y, _c, _v in toks})

        def fila_y(patron):
            for y in ys:
                if re.search(patron, etiqueta_en(y)):
                    return y
            return None

        data = {}
        for nivel, patron in self._PATRON_NIVEL.items():
            ry = fila_y(patron)
            if ry is None:
                continue
            fila = {c: v for y, c, v in toks if abs(y - ry) <= self._TOL_FILA}
            D, Cv, CU = fila.get("D"), fila.get("Cv"), fila.get("CUcon")
            PR = fila.get("PR")
            if PR is None and pr_flotantes:
                PR = min(pr_flotantes, key=lambda t: abs(t[0] - ry))[1]

            comps = {"G": G, "T": T, "D": D, "Cv": Cv, "PR": PR, "R": R}
            if any(comps[c] is None for c in self._ORDEN_COMPONENTES):
                continue
            comps["CU"] = CU if CU is not None else round(
                sum(comps[c] for c in self._ORDEN_COMPONENTES), 2
            )
            data[nivel] = comps

        return data

    # ── Fallback OCR (PDF imagen) ──────────────────────────────────────────

    def _texto_pdf_ocr(self, doc, escala: float = 2.5) -> str:
        import easyocr

        if self._ocr_reader is None:
            self.logger.info("  Inicializando OCR (primer uso, puede tardar)...")
            self._ocr_reader = easyocr.Reader(["es"], verbose=False)

        lineas = []
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(escala, escala))
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
            if pix.n == 4:
                img = img[:, :, :3]
            for _bbox, txt, conf in self._ocr_reader.readtext(img, detail=1, paragraph=False):
                if conf >= 0.4:
                    lineas.append(txt.strip())
        return "\n".join(lineas)

    def _parsear_texto_ocr(self, texto: str) -> dict:
        self.logger.warning(
            "  PDF de Afinia llego como imagen; el parseo posicional no aplica."
        )
        return {}

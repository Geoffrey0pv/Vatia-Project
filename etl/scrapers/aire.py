"""
Scraper AIR-E — extrae la tabla de componentes CU de las publicaciones de tarifas.

AIR-E publica sus tarifas como un aviso legal dentro de un PDF de periódico
(La Libertad / Diario del Norte). La tabla de Costo Unitario está en la página 1,
dentro del recuadro "INFORMACIÓN DE INTERÉS", y NO es texto seleccionable ni una
imagen embebida: está **dibujada como gráfico vectorial (texto convertido a
curvas)**. Por eso la única vía de extracción es rasterizar la región y aplicar
OCR (easyocr en producción, igual que CENS).

La estructura de la tabla es idéntica a la de Afinia (ambas son herederas del
formato Electricaribe): mismas columnas
    Generación | STN | PR | D: STR SDL | Restricciones | Comercialización | CU Mes | COT
y mismas filas (``Nivel 1 OR propietario activos`` … ``Nivel 4``). Los
componentes G, T y R son compartidos por todos los niveles (celdas verticalmente
combinadas); D, Cv, PR y CU varían por nivel. La identidad
``CU = G + T + D + Cv + PR + R`` cierra contra la columna "CU Mes".

El ciclo (AAAAMM) se deriva del **nombre del mes en español** que aparece en el
nombre del archivo más el **año** de la carpeta ``tarifas-AAAA`` (no de una fecha
DD-MM-AAAA como Afinia).

La reconstrucción de la tabla es independiente del motor OCR: se agrupan las
palabras detectadas en filas por su coordenada Y, se localizan las filas de cada
nivel por su etiqueta, se agrupan los números en columnas por su coordenada X y
se mapean de izquierda a derecha a [G, T, PR, D, R, Cv, CU, COT]. Las filas cuya
identidad CU no cierra se descartan.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit
import unicodedata

import fitz  # PyMuPDF
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from etl.base_scraper import ScraperBase


class AireScraper(ScraperBase):
    """
    Scraper para AIR-E — raspa la página de tarifas, descarga los PDFs de prensa
    y extrae los componentes del Costo Unitario (G, T, D, Cv, PR, R, CU) por
    nivel de tensión (1-4) mediante OCR + reconstrucción posicional.

    Nota técnica:
    - Fuente oficial: https://www.air-e.com/tarifas.html
    - El PDF requiere rasterización + OCR.
    - Ventana validada inicialmente: 202604–202605.
    - Se usan recortes candidatos del resumen CU.
    - Abril 2026 requiere una variante específica de recorte.
    - Las filas se aceptan solo si CU ≈ G + T + D + Cv + PR + R.
    - El histórico completo todavía no se considera validado.
    """

    competidor = "AIR-E"

    BASE_URL        = "https://www.air-e.com"
    URL_TARIFAS     = "https://www.air-e.com/tarifas.html"
    COMERCIALIZADOR = "AIR-E"
    OPERADOR_RED    = "AIR-E"
    DUENO_RED       = "100% OPERADOR"
    MIN_CICLO       = "202604"
    MAX_CICLO       = "202605"
    _PAGE_HEADERS   = {
        **ScraperBase._DEFAULT_HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
        "Referer": "https://www.air-e.com/",
        "Connection": "keep-alive",
    }

    # Meses en español -> número (ciclo desde el nombre del archivo / carpeta).
    _MESES: dict[str, str] = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "setiembre": "09", "octubre": "10",
        "noviembre": "11", "diciembre": "12",
    }

    # Orden izquierda->derecha de las 8 columnas numéricas de la tabla CU.
    _ORDEN_COLUMNAS = ["G", "T", "PR", "D", "R", "Cv", "CU", "COT"]
    # Componentes que deben sumar el CU.
    _ORDEN_COMPONENTES = ["G", "T", "D", "Cv", "PR", "R"]
    # Etiqueta representativa de cada nivel (sobre el texto NO numérico de la fila).
    _PATRON_NIVEL = {
        1: r"nivel\s*1\s*or",
        2: r"^nivel\s*2$",
        3: r"^nivel\s*3$",
        4: r"^nivel\s*4$",
    }
    _TOL_CU = 1.0

    # Geometría OCR (en píxeles del render). Se reescala con el factor de render.
    _TOL_FILA_PX   = 16.0   # agrupar palabras en una fila por Y
    _TOL_NIVEL_PX  = 22.0   # tolerancia al leer la fila de un nivel
    _CROP_RESUMEN  = (0.12, 0.365, 0.60, 0.565)
    _CROP_ABRIL_202604 = (0.15, 0.34, 0.92, 0.60)
    _ESCALA_OCR    = 5.0

    def __init__(
        self,
        directorio_raw: Path | None = None,
        solo_desde: str | None = None,
        solo_hasta: str | None = None,
        max_pdfs: int | None = None,
    ) -> None:
        super().__init__(directorio_raw)
        self._ocr_reader = None  # lazy
        self.solo_desde = solo_desde or self.MIN_CICLO
        self.solo_hasta = solo_hasta or self.MAX_CICLO
        self.max_pdfs = max_pdfs

    # ── Interfaz ScraperBase ──────────────────────────────────────────────

    def obtener_enlaces(self) -> list[tuple[str, str]]:
        """
        Raspa la página de tarifas de AIR-E y devuelve los PDFs disponibles.

        El ciclo (AAAAMM) se deriva del nombre del mes + el año de la carpeta
        ``tarifas-AAAA``. Devuelve un nombre sintético ``Tarifas_AIR-E_<AAAAMM>.pdf``
        para que ``extraer`` recupere el ciclo de forma uniforme.
        """
        self.logger.info("[AIR-E] Accediendo a: %s", self.URL_TARIFAS)
        resp = requests.get(self.URL_TARIFAS, headers=self._PAGE_HEADERS, timeout=30)
        resp.raise_for_status()
        html = resp.content.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        vistos: dict[str, str] = {}

        for tag_a in soup.find_all("a", href=True):
            url_desc = self._normalizar_url_pdf(tag_a["href"])
            if not url_desc:
                continue

            nombre = unquote(url_desc.split("/")[-1].split("?")[0])
            blob = f"{nombre} {url_desc}".lower()
            if not any(k in blob for k in ("tarifa", "air-e", "publicacion", "costo")):
                continue

            ciclo = self._ciclo_desde_url(url_desc)
            if not ciclo:
                self.logger.debug("  ? ciclo no determinable: %s", nombre)
                continue
            if not (self.solo_desde <= ciclo <= self.solo_hasta):
                continue
            if ciclo in vistos:
                continue

            status, content_type = self._verificar_documento(url_desc)
            nombre_local = f"Tarifas_AIR-E_{ciclo}.pdf"
            self.logger.info(
                "  documento: status=%s type=%s ciclo=%s archivo=%s url=%s",
                status,
                content_type or "?",
                ciclo,
                nombre_local,
                url_desc,
            )
            if status != 200 or "pdf" not in (content_type or "").lower():
                self.logger.warning("  Enlace descartado: %s", url_desc)
                continue

            vistos[ciclo] = url_desc

        if not vistos:
            raise ValueError(
                "No se encontró ningún PDF de tarifas en la página de AIR-E. "
                "Verifique la URL o el patrón de búsqueda."
            )

        enlaces = [
            (f"Tarifas_AIR-E_{ciclo}.pdf", url)
            for ciclo, url in sorted(vistos.items())
        ]
        if self.max_pdfs is not None:
            enlaces = enlaces[: self.max_pdfs]
        self.logger.info("[AIR-E] %d ciclo(s) único(s) encontrado(s)", len(enlaces))
        return enlaces

    def extraer(self, contenido: bytes, nombre_archivo: str) -> pd.DataFrame:
        """
        Extrae los componentes CU por nivel de tensión de un PDF de AIR-E.

        La tabla es gráfico vectorial -> siempre se rasteriza y se aplica OCR;
        luego se reconstruye por posición y se valida la identidad del CU.
        """
        ciclo, fecha = self._extraer_ciclo_y_fecha(nombre_archivo)
        self.logger.info("  Ciclo: %s -> Fecha: %s", ciclo, fecha)

        doc = fitz.open(stream=contenido, filetype="pdf")
        df = pd.DataFrame()
        invalidas = 0
        recorte_usado: str | None = None

        for nombre_recorte, crop in self._recortes_resumen(ciclo):
            palabras = self._ocr_resumen_cu(doc, crop=crop)
            self.logger.info(
                "  Ciclo %s -> recorte %s -> OCR resumen CU: %d palabra(s)",
                ciclo,
                nombre_recorte,
                len(palabras),
            )
            data_por_nivel = self._parsear_resumen_cu(palabras)
            if not data_por_nivel:
                continue
            df_tmp, invalidas_tmp = self._construir_filas_validas(data_por_nivel, fecha, ciclo)
            self.logger.info(
                "  Ciclo %s -> recorte %s -> filas extraídas=%d validas_CU=%s",
                ciclo,
                nombre_recorte,
                len(df_tmp),
                "si" if len(df_tmp) == 4 else "no",
            )
            if len(df_tmp) == 4:
                df = df_tmp
                invalidas = invalidas_tmp
                recorte_usado = nombre_recorte
                break

        if df.empty:
            self.logger.warning("  No se pudo reconstruir el resumen CU; probando OCR amplio.")
            palabras = self._ocr_tabla(doc)
            self.logger.info("  OCR amplio: %d palabra(s) detectada(s)", len(palabras))
            data_por_nivel = self._parsear_ocr(palabras)
            if not data_por_nivel:
                raise RuntimeError(
                    f"No se extrajo ningún nivel de tensión de '{nombre_archivo}'. "
                    "El layout del PDF pudo haber cambiado o el OCR falló."
                )
            df, invalidas = self._construir_filas_validas(data_por_nivel, fecha, ciclo)
            recorte_usado = "ocr_amplio"

        if invalidas and df.empty:
            raise RuntimeError(
                f"Todas las filas extraídas de '{nombre_archivo}' fallaron la "
                "validación de integridad del CU."
            )
        if recorte_usado:
            self.logger.info("  Ciclo %s -> recorte usado: %s", ciclo, recorte_usado)
        if len(df) < 4:
            self.logger.warning(
                "PDF procesado parcialmente: se esperaban 4 niveles, se obtuvieron %d",
                len(df),
            )
        self.logger.info("  -> %d filas x %d columnas", len(df), len(df.columns))
        return df

    def ejecutar(self) -> pd.DataFrame:
        """
        Ejecuta AIR-E conservando la ventana validada y reportando el costo OCR.
        """
        if (
            self.solo_desde == self.MIN_CICLO
            and self.solo_hasta == self.MAX_CICLO
            and self.max_pdfs is None
        ):
            self.logger.warning(
                "[AIR-E] Integración parcial: por defecto se limita a %s..%s "
                "para evitar OCR pesado e histórico no validado.",
                self.MIN_CICLO,
                self.MAX_CICLO,
            )
        self.logger.warning(
            "[AIR-E] Este scraper usa rasterización + OCR; ejecutar ventanas grandes "
            "puede ser lento y frágil."
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
                        "[AIR-E] %s procesado parcialmente: %d nivel(es) extraído(s)",
                        nombre,
                        len(df),
                    )
                self.logger.info("✔ %s procesado (%d filas)", nombre, len(df))
            except Exception as exc:
                fallidos += 1
                self.logger.warning("✖ %s falló: %s", nombre, exc)

        filas_finales = sum(len(df) for df in resultados)
        self.logger.info(
            "[AIR-E] Resumen -> encontrados=%d descargados=%d completos=%d "
            "parciales=%d fallidos=%d filas_validas=%d",
            encontrados,
            descargados,
            completos,
            parciales,
            fallidos,
            filas_finales,
        )

        if not resultados:
            raise RuntimeError("AIR-E: no se procesó ningún PDF válido.")

        return pd.concat(resultados, ignore_index=True)

    # ── Helpers de enlaces / ciclo ─────────────────────────────────────────

    def _ciclo_desde_url(self, url: str) -> str | None:
        """Ciclo AAAAMM: mes (nombre en español) + año (carpeta tarifas-AAAA)."""
        ruta = unquote(url).lower()
        nombre = ruta.split("/")[-1]

        mes = next((n for nom, n in self._MESES.items() if nom in nombre), None)
        if not mes:
            mes = next((n for nom, n in self._MESES.items() if nom in ruta), None)

        anios = re.findall(r"tarifas?-(20\d{2})", ruta) or re.findall(r"(20\d{2})", ruta)
        if mes and anios:
            return f"{anios[-1]}{mes}"
        return None

    def _normalizar_url_pdf(self, href_raw: str) -> str | None:
        """Conserva solo URLs PDF del dominio air-e.com (absolutas o relativas)."""
        href = "".join(
            ch for ch in (href_raw or "").strip()
            if unicodedata.category(ch)[0] != "C"
        )
        href = re.sub(r"\s+", " ", href)
        if not href or ".pdf" not in href.lower():
            return None

        if "http" in href.lower() and ".pdf" in href.lower():
            match = re.search(r"https?://.*?\.pdf(?:\?[^\"'\s]*)?", href, flags=re.I)
            if match:
                href = match.group(0)
        elif href.lower().count(".pdf") > 1:
            match = re.search(r".*?\.pdf(?:\?[^\"'\s]*)?", href, flags=re.I)
            if match:
                href = match.group(0)

        absoluta = urljoin(f"{self.BASE_URL}/", href)
        parts = urlsplit(absoluta)
        ruta = quote(unquote(parts.path), safe="/-_.~")
        query = quote(unquote(parts.query), safe="=&%-_.~")
        absoluta = urlunsplit((parts.scheme, parts.netloc, ruta, query, ""))
        parsed = urlparse(absoluta)
        host = (parsed.hostname or "").lower()
        if "air-e.com" not in host:
            return None
        if ".pdf" not in parsed.path.lower():
            return None
        return absoluta

    def _verificar_documento(self, url: str) -> tuple[int | None, str | None]:
        """
        Verifica que el documento responda sin dejar que un enlace roto tumbe
        todo el scraping.
        """
        try:
            resp = requests.get(url, headers=self._PAGE_HEADERS, timeout=20, stream=True)
            status = resp.status_code
            content_type = resp.headers.get("Content-Type")
            resp.close()
            return status, content_type
        except requests.RequestException as exc:
            self.logger.warning("  No se pudo verificar %s: %s", url, exc)
            return None, None

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

    # ── OCR (PDF vectorial -> raster -> easyocr) ───────────────────────────

    def _get_reader(self):
        import easyocr
        if self._ocr_reader is None:
            self.logger.info("  Inicializando OCR (primer uso, puede tardar)...")
            self._ocr_reader = easyocr.Reader(["es"], verbose=False)
        return self._ocr_reader

    def _ocr_tabla(self, doc, escala: float = 4.0) -> list[tuple[float, float, str]]:
        """
        Rasteriza la mitad izquierda de la página 1 (donde está la tabla) y
        aplica OCR. Devuelve [(xc, yc, texto), ...] en píxeles del render.
        """
        page = doc[0]
        rect = fitz.Rect(0, 0, page.rect.width * 0.55, page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(escala, escala), clip=rect)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]

        reader = self._get_reader()
        palabras: list[tuple[float, float, str]] = []
        for bbox, txt, conf in reader.readtext(img, detail=1, paragraph=False):
            if conf < 0.30:
                continue
            xc = (bbox[0][0] + bbox[2][0]) / 2.0
            yc = (bbox[0][1] + bbox[2][1]) / 2.0
            palabras.append((float(xc), float(yc), txt.strip()))
        return palabras

    def _ocr_resumen_cu(self, doc, crop=None) -> list[tuple[float, float, str]]:
        """
        AIR-E publica el CU en un recuadro de periódico; el resumen superior
        contiene exactamente los 4 niveles que necesitamos. Este recorte evita
        mezclar el OCR con las columnas editoriales del periódico.
        """
        page = doc[0]
        x0, y0, x1, y1 = crop or self._CROP_RESUMEN
        rect = fitz.Rect(
            page.rect.width * x0,
            page.rect.height * y0,
            page.rect.width * x1,
            page.rect.height * y1,
        )
        pix = page.get_pixmap(matrix=fitz.Matrix(self._ESCALA_OCR, self._ESCALA_OCR), clip=rect)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]

        reader = self._get_reader()
        palabras: list[tuple[float, float, str]] = []
        for bbox, txt, conf in reader.readtext(img, detail=1, paragraph=False):
            if conf < 0.30:
                continue
            xc = (bbox[0][0] + bbox[2][0]) / 2.0
            yc = (bbox[0][1] + bbox[2][1]) / 2.0
            palabras.append((float(xc), float(yc), txt.strip()))
        return palabras

    def _recortes_resumen(self, ciclo: str) -> list[tuple[str, tuple[float, float, float, float]]]:
        recortes = [("resumen_base", self._CROP_RESUMEN)]
        if ciclo == "202604":
            recortes.insert(0, ("abril_202604", self._CROP_ABRIL_202604))
        return recortes

    # ── Reconstrucción posicional (independiente del motor OCR) ────────────

    @staticmethod
    def _es_numero(texto: str) -> bool:
        return bool(re.match(r"^-?\d{1,3}(\.\d{3})*,\d+$|^-?\d+,\d+$", texto.strip()))

    @staticmethod
    def _val(texto: str) -> float:
        return float(texto.strip().replace(".", "").replace(",", "."))

    def _agrupar_filas(self, palabras, tol):
        """Agrupa palabras en filas por proximidad en Y. Devuelve [(yc, [palabras])]."""
        if not palabras:
            return []
        ordenadas = sorted(palabras, key=lambda p: p[1])
        filas: list[list] = [[ordenadas[0]]]
        for p in ordenadas[1:]:
            if abs(p[1] - filas[-1][-1][1]) <= tol:
                filas[-1].append(p)
            else:
                filas.append([p])
        salida = []
        for grupo in filas:
            grupo.sort(key=lambda p: p[0])
            yc = sum(p[1] for p in grupo) / len(grupo)
            salida.append((yc, grupo))
        return salida

    @staticmethod
    def _bandas(xs, tol):
        """Agrupa coordenadas X en bandas (columnas). Devuelve centros ordenados."""
        if not xs:
            return []
        xs = sorted(xs)
        grupos = [[xs[0]]]
        for x in xs[1:]:
            if x - grupos[-1][-1] <= tol:
                grupos[-1].append(x)
            else:
                grupos.append([x])
        return [sum(g) / len(g) for g in grupos]

    def _parsear_ocr(self, palabras) -> dict:
        """
        Reconstruye {nivel: {componente: valor}} desde las palabras OCR.

        G, T, R son compartidos (una sola aparición); D, Cv, CU por fila de
        nivel; PR es un valor flotante por bloque (se asigna por cercanía en Y).
        """
        if not palabras:
            return {}

        filas = self._agrupar_filas(palabras, 8.0)

        # 1. Localizar la fila de cada nivel por su etiqueta no numérica.
        def etiqueta_de(grupo) -> str:
            return " ".join(p[2] for p in grupo if not self._es_numero(p[2])).strip()

        y_nivel: dict[int, float] = {}
        for nivel, patron in self._PATRON_NIVEL.items():
            for yc, grupo in filas:
                if re.search(patron, etiqueta_de(grupo).lower()):
                    y_nivel[nivel] = yc
                    break
        if not y_nivel:
            return {}

        y_top = min(y_nivel.values()) - self._TOL_NIVEL_PX
        y_bot = max(y_nivel.values()) + self._TOL_NIVEL_PX

        # 2. Tokens numéricos dentro de la franja de la tabla.
        toks = [
            (x, y, self._val(t))
            for (x, y, t) in palabras
            if self._es_numero(t) and (y_top - 30) <= y <= (y_bot + 30)
        ]
        if not toks:
            return {}

        # 3. Bandas de columnas a partir de la X de los tokens.
        anchura = max(x for x, _, _ in toks) - min(x for x, _, _ in toks)
        tol_col = max(anchura / 20.0, 8.0)
        centros = self._bandas([x for x, _, _ in toks], tol_col)

        # Mapear bandas a columnas. Se esperan 8 (G,T,PR,D,R,Cv,CU,COT);
        # si faltan a la derecha (p.ej. COT), se mapea desde la izquierda.
        cols = self._ORDEN_COLUMNAS
        if len(centros) >= len(cols):
            centros_usar = centros[: len(cols)]
        else:
            centros_usar = centros
        mapa_col = {centros_usar[i]: cols[i] for i in range(len(centros_usar))}

        def columna_de(x):
            mejor, dmin = None, tol_col
            for cx, nombre in mapa_col.items():
                d = abs(x - cx)
                if d <= dmin:
                    dmin, mejor = d, nombre
            return mejor

        # 4. Compartidos G, T, R (única aparición en su banda).
        def compartido(nombre):
            vals = [v for x, _y, v in toks if columna_de(x) == nombre]
            return vals[0] if vals else None

        G, T, R = compartido("G"), compartido("T"), compartido("R")

        # PR flotantes (uno por bloque/nivel).
        pr_flotantes = [(y, v) for x, y, v in toks if columna_de(x) == "PR"]

        # 5. Leer D, Cv, CU por fila de nivel.
        data: dict[int, dict] = {}
        for nivel, yc in y_nivel.items():
            fila = {}
            for x, y, v in toks:
                if abs(y - yc) <= self._TOL_NIVEL_PX:
                    c = columna_de(x)
                    if c in ("D", "Cv", "CU"):
                        fila[c] = v
            D, Cv, CU = fila.get("D"), fila.get("Cv"), fila.get("CU")

            PR = None
            if pr_flotantes:
                PR = min(pr_flotantes, key=lambda t: abs(t[0] - yc))[1]

            comps = {"G": G, "T": T, "D": D, "Cv": Cv, "PR": PR, "R": R}
            if any(comps[c] is None for c in self._ORDEN_COMPONENTES):
                continue
            comps["CU"] = CU if CU is not None else round(
                sum(comps[c] for c in self._ORDEN_COMPONENTES), 2
            )
            data[nivel] = comps

        return data

    def _parsear_resumen_cu(self, palabras) -> dict[int, dict[str, float]]:
        """
        Parser preferido para AIR-E: reconstruye la tabla resumen del recuadro
        "Costo Unitario" usando OCR de un recorte fijo del aviso.
        """
        if not palabras:
            return {}

        filas = self._agrupar_filas(palabras, 8.0)

        def limpiar(txt: str) -> str:
            txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")
            txt = txt.lower().replace("_", " ")
            txt = re.sub(r"\s+", " ", txt)
            return txt.strip()

        def es_numero_ocr(texto: str) -> bool:
            t = texto.strip()
            return bool(
                re.match(r"^-?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?$|^-?\d+[.,]\d+$", t)
                or re.match(r"^\d{4,6}$", t)
            )

        def a_valor(texto: str) -> float:
            t = texto.strip()
            if re.fullmatch(r"\d{4,6}", t):
                return int(t) / 100.0
            if "," in t and "." in t:
                if t.rfind(",") > t.rfind("."):
                    t = t.replace(".", "").replace(",", ".")
                else:
                    t = t.replace(",", "")
            elif "," in t:
                t = t.replace(".", "").replace(",", ".")
            return float(t)

        def etiqueta_fila(grupo) -> str:
            return limpiar(" ".join(p[2] for p in grupo if not es_numero_ocr(p[2])))

        header_y = None
        for yc, grupo in filas:
            etiqueta = etiqueta_fila(grupo)
            if "costos del mes" in etiqueta and "stn" in etiqueta and "cu mes" in etiqueta:
                header_y = yc
                break
        if header_y is None:
            return {}

        cols: dict[str, float] = {}
        header_tokens = [
            (x, y, txt)
            for x, y, txt in palabras
            if (header_y - 55) <= y <= (header_y + 30)
        ]
        for x, _y, txt in header_tokens:
            t = limpiar(txt)
            if t.startswith("gen"):
                cols["G"] = x
            elif t == "stn":
                cols["T"] = x
            elif t.startswith("pr:") or t == "pr":
                cols["PR"] = x
            elif t == "str sdl":
                cols["D"] = x
            elif t.startswith("restric"):
                cols["R"] = x
            elif t.startswith("comercializ"):
                cols["Cv"] = x
            elif t == "cu mes" or t == "cu":
                cols["CU"] = x
            elif t.startswith("cot"):
                cols["COT"] = x

        if not all(k in cols for k in ("G", "T", "PR", "D", "R", "Cv", "CU")):
            return {}

        target_rows = {
            1: re.compile(r"\bnivel\b.*\bor\b.*propietario activos"),
            2: re.compile(r"\bnivel 2\b"),
            3: re.compile(r"\bnivel 3\b"),
            4: re.compile(r"\bnivel 4\b"),
        }

        def mejor_y_nivel(nivel: int) -> float | None:
            patron = target_rows[nivel]
            candidatos: list[tuple[float, float]] = []
            for yc, grupo in filas:
                etiqueta = etiqueta_fila(grupo)
                if not patron.search(etiqueta):
                    continue
                score = 0.0
                if nivel == 1 and "or propietario activos" in etiqueta:
                    score += 5.0
                if nivel == 2:
                    if etiqueta.startswith("nivel 2"):
                        score += 4.0
                    if any(x in etiqueta for x in ("a p", "sn", "medido", "censado")):
                        score -= 2.0
                if nivel in (3, 4) and etiqueta.startswith(f"nivel {nivel}"):
                    score += 3.0
                candidatos.append((score, yc))
            if not candidatos:
                return None
            candidatos.sort(key=lambda item: (item[0], -item[1]), reverse=True)
            return candidatos[0][1]

        y_nivel = {nivel: mejor_y_nivel(nivel) for nivel in (1, 2, 3, 4)}
        if any(y is None for y in y_nivel.values()):
            return {}
        y_nivel = {nivel: float(y) for nivel, y in y_nivel.items()}

        y_max_tabla = max(y_nivel.values()) + 25.0
        tokens_num = [
            (x, y, a_valor(txt))
            for x, y, txt in palabras
            if es_numero_ocr(txt) and (header_y - 20) <= y <= y_max_tabla
        ]
        if not tokens_num:
            return {}

        orden_cols = ["G", "T", "PR", "D", "R", "Cv", "CU", "COT"]
        centros = [(col, cols[col]) for col in orden_cols if col in cols]
        xs = [cx for _col, cx in centros]
        gaps = [b - a for a, b in zip(xs, xs[1:])]
        tol_x = max(120.0, (min(gaps) * 0.75) if gaps else 120.0)

        def col_de_x(x: float) -> str | None:
            best = None
            dist = tol_x
            for col, cx in centros:
                d = abs(x - cx)
                if d <= dist:
                    best = col
                    dist = d
            return best

        col_tokens: dict[str, list[tuple[float, float]]] = {k: [] for k in cols}
        for x, y, v in tokens_num:
            col = col_de_x(x)
            if col:
                col_tokens[col].append((y, v))

        def valor_en_fila(col: str, y_ref: float, tol_y: float = 18.0) -> float | None:
            candidatos = [(abs(y - y_ref), v) for y, v in col_tokens.get(col, []) if abs(y - y_ref) <= tol_y]
            if not candidatos:
                return None
            candidatos.sort(key=lambda item: item[0])
            return candidatos[0][1]

        def shared_single(col: str) -> float | None:
            valores = sorted({round(v, 4) for _y, v in col_tokens.get(col, [])})
            if not valores:
                return None
            return valores[0]

        G = shared_single("G")
        T = shared_single("T")
        R = shared_single("R")
        if None in (G, T, R):
            return {}

        def pr_por_cercania(y_ref: float) -> float | None:
            candidatos = [(abs(y - y_ref), v) for y, v in col_tokens.get("PR", [])]
            if not candidatos:
                return None
            candidatos.sort(key=lambda item: item[0])
            return candidatos[0][1]

        data: dict[int, dict[str, float]] = {}
        for nivel in (1, 2, 3, 4):
            yc = y_nivel[nivel]
            fila = {
                "G": G,
                "T": T,
                "D": valor_en_fila("D", yc),
                "Cv": valor_en_fila("Cv", yc),
                "PR": pr_por_cercania(yc),
                "R": R,
                "CU": valor_en_fila("CU", yc),
            }
            if any(fila[c] is None for c in self._ORDEN_COMPONENTES + ["CU"]):
                continue
            data[nivel] = fila

        return data

    def _construir_filas_validas(
        self,
        data_por_nivel: dict[int, dict],
        fecha: str,
        ciclo: str,
    ) -> tuple[pd.DataFrame, int]:
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
        return pd.DataFrame(df_filas), invalidas

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
        return abs(sum(valores) - cu) <= self._TOL_CU

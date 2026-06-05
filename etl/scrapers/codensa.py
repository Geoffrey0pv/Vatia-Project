"""
Scraper CODENSA / ENEL Distribución — extrae la tabla de componentes CU de los
"pliegos tarifarios" mensuales publicados por Enel Colombia (antes Codensa).

**Modo basado en disco.** La página de tarifas de Enel y sus PDF están tras
Imperva/Incapsula, que sirve un challenge a todo cliente automático (requests,
cloudscraper y Playwright headless fallan; las descargas del DAM también). Por
eso los pliegos se descargan **manualmente** desde la página de Enel y se
guardan en ``data/raw/codensa/``. El nombre de cada archivo debe contener el
ciclo ``AAAAMM`` (p.ej. ``Tarifas_CODENSA_202605.pdf``); ``obtener_enlaces``
recorre esa carpeta, deriva el ciclo del nombre y entrega los PDF a ``extraer``.

La tabla de componentes admite DOS formatos de PDF:

  • **Texto seleccionable** (p.ej. mayo 2026): el pliego trae la tabla como texto;
    se localiza el encabezado "CUvn,m,i,j" y se leen los 7 números por nivel.
  • **Vectorial / curvas** (p.ej. septiembre 2025): la página no tiene texto
    (``get_text("words")`` vacío) sino miles de trazos; se rasteriza la región de
    la tabla y se aplica OCR (easyocr español en producción), reconstruyendo las
    columnas por posición y validando G+T+D+Cv+PR+R = CU.

En ambos casos las cifras usan **formato anglosajón** (coma de millares, punto
decimal: ``1,007.6090``).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
import pandas as pd

from etl.base_scraper import ScraperBase


class CodensaScraper(ScraperBase):
    """
    Scraper para CODENSA / ENEL — **basado en disco**.

    La página de tarifas de Enel y sus PDF están tras Imperva/Incapsula, que
    bloquea todo acceso automático (requests, cloudscraper y Playwright headless
    reciben un challenge; las descargas del DAM también). Por eso los pliegos se
    descargan **manualmente** y se guardan en ``data/raw/codensa/`` con el nombre
    ``Tarifas_CODENSA_<AAAAMM>.pdf``; este scraper los localiza en disco y extrae
    los componentes del Costo Unitario (G, T, D, Cv, PR, R, CU) por nivel de
    tensión (1-4), igual que los demás scrapers.
    """

    competidor = "CODENSA"

    # Página de origen (referencia para descargar los pliegos manualmente).
    URL_TARIFAS     = "https://www.enel.com.co/es/personas/tarifas-energia-enel-distribucion.html"
    COMERCIALIZADOR = "CODENSA"
    OPERADOR_RED    = "CODENSA"
    DUENO_RED       = "100% OPERADOR"
    MIN_CICLO       = "202501"
    MAX_CICLO       = "202612"

    # Orden de columnas de la tabla de componentes (izq. -> der.)
    _COLUMNAS = ["G", "T", "D", "Cv", "PR", "R", "CU"]
    _COMPONENTES = ["G", "T", "D", "Cv", "PR", "R"]
    _TOL_CU = 1.0          # tolerancia de integridad CU = sum(componentes)
    _TOL_FILA_PX = 40.0    # agrupación de tokens OCR en filas (px del render)
    _RE_NOMBRE_ESPERADO = re.compile(r"^Tarifas_CODENSA_(\d{6})\.pdf$", re.I)

    def __init__(self, directorio_raw: Path | None = None) -> None:
        super().__init__(directorio_raw)
        self._ocr_reader = None

    # ── Interfaz ScraperBase ──────────────────────────────────────────────

    def obtener_enlaces(self) -> list[tuple[str, str]]:
        """
        Lista los pliegos tarifarios CODENSA disponibles **en disco**.

        Como la página de Enel y sus PDF están tras Imperva/Incapsula (sin
        acceso automático posible sin proxy residencial), los pliegos se
        descargan **a mano** y se guardan en ``data/raw/codensa/``. El nombre
        de cada archivo debe contener el ciclo ``AAAAMM`` (p.ej.
        ``Tarifas_CODENSA_202605.pdf``); ese ciclo se deriva del nombre, se
        filtra al rango ``[MIN_CICLO, MAX_CICLO]`` y se devuelve la lista
        ordenada cronológicamente.

        Cada entrada es ``(nombre_archivo, ruta_absoluta)``; ``descargar`` lee
        el PDF directamente del disco (no hay red).
        """
        carpeta = self.directorio_raw
        if not carpeta.is_dir():
            raise FileNotFoundError(
                f"[CODENSA] No existe la carpeta de PDFs locales: {carpeta}. "
                "Créala y guarda allí los pliegos descargados a mano, con el "
                "ciclo en el nombre (p.ej. Tarifas_CODENSA_202605.pdf)."
            )

        encontrados: dict[str, Path] = {}
        ignorados: list[str] = []
        nombres_no_estandar: list[str] = []
        for ruta in sorted(carpeta.glob("*.pdf")):
            m = re.search(r"(\d{6})", ruta.name)
            if not m:
                ignorados.append(ruta.name)
                continue
            if not self._RE_NOMBRE_ESPERADO.match(ruta.name):
                nombres_no_estandar.append(ruta.name)
            ciclo = m.group(1)
            if not (self.MIN_CICLO <= ciclo <= self.MAX_CICLO):
                ignorados.append(ruta.name)
                continue
            encontrados.setdefault(ciclo, ruta)

        if ignorados:
            self.logger.warning(
                "[CODENSA] %d archivo(s) PDF ignorado(s) (sin ciclo AAAAMM "
                "válido en el nombre o fuera de rango): %s",
                len(ignorados), ", ".join(ignorados),
            )
        if nombres_no_estandar:
            self.logger.warning(
                "[CODENSA] %d archivo(s) usan un nombre no estándar. "
                "Se recomienda el patrón Tarifas_CODENSA_<AAAAMM>.pdf: %s",
                len(nombres_no_estandar),
                ", ".join(nombres_no_estandar),
            )

        if not encontrados:
            raise FileNotFoundError(
                f"[CODENSA] No se encontraron PDFs válidos en {carpeta}. "
                "Este scraper depende exclusivamente de archivos locales "
                "descargados manualmente desde la fuente oficial de Enel. "
                "Guarda los pliegos con el ciclo en el nombre, p.ej. "
                "Tarifas_CODENSA_202605.pdf (mayo 2026)."
            )

        enlaces = [
            (ruta.name, str(ruta)) for _, ruta in sorted(encontrados.items())
        ]
        self.logger.info(
            "[CODENSA] %d pliego(s) local(es) en %s: %s",
            len(enlaces), carpeta, ", ".join(sorted(encontrados)),
        )
        return enlaces

    def descargar(self, url: str, headers: dict | None = None) -> bytes:
        """
        "Descarga" un pliego leyéndolo **del disco local**.

        No hay acceso web (Incapsula bloquea página y DAM); ``url`` es en
        realidad la ruta absoluta del PDF entregada por ``obtener_enlaces``.
        """
        ruta = Path(url)
        if not ruta.is_file():
            raise FileNotFoundError(f"[CODENSA] PDF local no encontrado: {ruta}")
        contenido = ruta.read_bytes()
        if not contenido[:5].startswith(b"%PDF"):
            raise ValueError(
                f"[CODENSA] El archivo no es un PDF válido (no empieza con "
                f"%PDF): {ruta}"
            )
        return contenido

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

    # ── Extracción de la tabla de componentes ──────────────────────────────

    def extraer(self, contenido: bytes, nombre_archivo: str) -> pd.DataFrame:
        """
        Extrae los componentes CU (G, T, D, Cv, PR, R, CU) por nivel de tensión
        del pliego tarifario. Detecta automáticamente el formato del PDF:

          • Si alguna página tiene texto seleccionable con el encabezado de la
            tabla de componentes -> parser de texto.
          • Si no (página dibujada como curvas vectoriales) -> rasteriza + OCR.

        Devuelve un DataFrame con las 15 columnas del esquema unificado y una
        fila por nivel (1-4). Para el Nivel 1 se toma la modalidad
        "PROPIEDAD DE ENEL COLOMBIA".
        """
        ciclo, fecha = self._extraer_ciclo_y_fecha(nombre_archivo)
        doc = fitz.open(stream=contenido, filetype="pdf")

        pagina_texto = self._pagina_con_tabla_texto(doc)
        if pagina_texto is not None:
            self.logger.info("[CODENSA] %s: formato TEXTO (pág. %d)", ciclo, pagina_texto)
            por_nivel = self._parsear_texto(doc[pagina_texto])
            origen = "texto"
        else:
            self.logger.info("[CODENSA] %s: formato VECTORIAL -> OCR", ciclo)
            por_nivel = self._parsear_ocr(doc)
            origen = "ocr"

        if not por_nivel:
            raise RuntimeError(
                f"No se extrajo ningún nivel de '{nombre_archivo}' (formato {origen})."
            )

        df = self._construir_df(por_nivel, fecha, ciclo)
        if len(df) < 4:
            self.logger.warning(
                "[CODENSA] %s: se esperaban 4 niveles, se obtuvieron %d (%s)",
                ciclo, len(df), origen,
            )
        self.logger.info("[CODENSA] %s -> %d filas (%s)", ciclo, len(df), origen)
        return df

    def ejecutar(self) -> pd.DataFrame:
        """
        Ejecuta CODENSA reportando explícitamente su dependencia de archivos locales.
        """
        self.logger.warning(
            "[CODENSA] Modo local/manual: no se intenta automatizar la descarga "
            "web mientras la fuente oficial siga protegida por Imperva/Incapsula."
        )
        self.logger.info(
            "[CODENSA] Buscando pliegos locales en %s", self.directorio_raw.resolve()
        )

        enlaces = self.obtener_enlaces()
        encontrados = len(enlaces)
        disponibles_localmente = len(enlaces)
        completos = 0
        parciales = 0
        fallidos = 0
        resultados: list[pd.DataFrame] = []

        for nombre, ruta in enlaces:
            try:
                contenido = self.descargar(ruta)
                df = self.extraer(contenido, nombre)
                resultados.append(df)
                if len(df) >= 4:
                    completos += 1
                else:
                    parciales += 1
                    self.logger.warning(
                        "[CODENSA] %s procesado parcialmente: %d nivel(es) extraído(s)",
                        nombre,
                        len(df),
                    )
                self.logger.info("✔ %s procesado (%d filas)", nombre, len(df))
            except Exception as exc:
                fallidos += 1
                self.logger.warning("✖ %s falló: %s", nombre, exc)

        filas_finales = sum(len(df) for df in resultados)
        self.logger.info(
            "[CODENSA] Resumen -> encontrados=%d disponibles_localmente=%d "
            "completos=%d parciales=%d fallidos=%d filas_validas=%d",
            encontrados,
            disponibles_localmente,
            completos,
            parciales,
            fallidos,
            filas_finales,
        )

        if not resultados:
            raise RuntimeError("CODENSA: no se procesó ningún PDF local válido.")

        return pd.concat(resultados, ignore_index=True)

    # ── Formato anglosajón (1,007.6090) ────────────────────────────────────

    _RE_NUM = re.compile(r"^\d{1,3}(?:,\d{3})*\.\d{2,4}$|^\d+\.\d{2,4}$")

    @classmethod
    def _es_numero(cls, texto: str) -> bool:
        return bool(cls._RE_NUM.match((texto or "").strip()))

    @staticmethod
    def _val(texto: str) -> float:
        return float(texto.strip().replace(",", ""))

    def _fila_integra(self, comps: dict[str, float], cu: float) -> bool:
        suma = sum(comps.get(c, 0.0) for c in self._COMPONENTES)
        return abs(suma - cu) <= self._TOL_CU

    # ── Parser de TEXTO ────────────────────────────────────────────────────

    def _pagina_con_tabla_texto(self, doc) -> int | None:
        """Primera página con texto seleccionable y el encabezado de componentes."""
        for pno in range(len(doc)):
            txt = doc[pno].get_text("text")
            if not txt.strip():
                continue
            if "CUv" in txt and ("Generaci" in txt or "Restriccion" in txt):
                return pno
        return None

    def _parsear_texto(self, page) -> dict[str, list[float]]:
        """
        Lee la tabla de componentes desde el texto. Tras el encabezado
        "CUvn,m,i,j" cada bloque es: "NIVEL X" [+ etiqueta de propiedad] seguido
        de 7 cifras (G, T, D, Cv, PR, R, CU). Para el Nivel 1 se conserva la
        modalidad "ENEL COLOMBIA".
        """
        lineas = [l.strip() for l in page.get_text("text").split("\n")]
        # Localizar el encabezado de la tabla (la línea CUvn...)
        idx_cu = next(
            (i for i, l in enumerate(lineas) if l.upper().startswith("CUV")), None
        )
        if idx_cu is None:
            return {}

        registros: list[tuple[int, str, list[float]]] = []
        nivel = None
        propiedad = ""
        nums: list[float] = []

        def cerrar():
            if nivel is not None and len(nums) >= 7:
                registros.append((nivel, propiedad.upper(), nums[:7]))

        for l in lineas[idx_cu + 1:]:
            mn = re.match(r"NIVEL\s*([1-4])", l, re.I)
            if mn:
                cerrar()
                nivel = int(mn.group(1))
                propiedad = l[mn.end():]
                nums = []
                continue
            if nivel is None:
                continue
            if self._es_numero(l):
                if len(nums) < 7:
                    nums.append(self._val(l))
            elif re.search(r"PROPIEDAD|ENEL|CLIENTE|COMPARTIDA", l, re.I) and not nums:
                propiedad += " " + l
            elif l and not self._es_numero(l) and len(nums) >= 7:
                # fin del bloque del nivel actual
                cerrar()
                nivel = None
                nums = []
        cerrar()

        return self._seleccionar_niveles(registros)

    def _seleccionar_niveles(
        self, registros: list[tuple[int, str, list[float]]]
    ) -> dict[str, list[float]]:
        """
        De los registros (nivel, propiedad, [7 cifras]) escoge una fila por
        nivel 1-4. Para el Nivel 1 prioriza la modalidad "ENEL COLOMBIA"; para
        2-4 toma la primera fila válida del nivel.
        """
        por_nivel: dict[int, list[float]] = {}
        n1_es_enel = False
        for nivel, prop, nums in registros:
            comps = dict(zip(self._COLUMNAS, nums))
            if not self._fila_integra(comps, comps["CU"]):
                continue
            if nivel == 1:
                es_enel = "ENEL" in prop
                if es_enel and not n1_es_enel:
                    por_nivel[1] = nums       # la fila ENEL manda
                    n1_es_enel = True
                elif 1 not in por_nivel:
                    por_nivel[1] = nums       # primera fila N1 vista (provisional)
            else:
                por_nivel.setdefault(nivel, nums)

        return {str(n): por_nivel[n] for n in sorted(por_nivel)}

    # ── Parser OCR (PDF vectorial) ─────────────────────────────────────────

    def _get_reader(self):
        import easyocr
        if self._ocr_reader is None:
            self.logger.info("[CODENSA] Inicializando OCR (primer uso)...")
            self._ocr_reader = easyocr.Reader(["es"], verbose=False)
        return self._ocr_reader

    def _ocr_palabras(self, doc, escala: float = 6.0) -> list[tuple[float, float, str]]:
        """
        Rasteriza la mitad superior de la primera página (donde está la tabla de
        componentes) y devuelve [(xc, yc, texto), ...] vía easyocr.
        """
        page = doc[0]
        rect = fitz.Rect(0, 0, page.rect.width, page.rect.height * 0.55)
        pix = page.get_pixmap(matrix=fitz.Matrix(escala, escala), clip=rect)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]

        reader = self._get_reader()
        palabras: list[tuple[float, float, str]] = []
        for bbox, txt, conf in reader.readtext(img, detail=1, paragraph=False):
            if conf < 0.20:
                continue
            xc = (bbox[0][0] + bbox[2][0]) / 2.0
            yc = (bbox[0][1] + bbox[2][1]) / 2.0
            palabras.append((float(xc), float(yc), txt.strip().replace(" ", "")))
        return palabras

    def _parsear_ocr(self, doc) -> dict[str, list[float]]:
        palabras = self._ocr_palabras(doc)
        self.logger.info("[CODENSA] OCR: %d palabra(s)", len(palabras))
        return self._reconstruir_tabla(palabras)

    def _reconstruir_tabla(
        self, palabras: list[tuple[float, float, str]]
    ) -> dict[str, list[float]]:
        """
        Reconstrucción posicional independiente del motor OCR:

          1. Conserva los tokens numéricos (formato anglo) y los agrupa en filas
             por proximidad en Y.
          2. Detecta filas "ancla": exactamente 7 cifras cuyo
             G+T+D+Cv+PR+R = CU. Sus X definen los 7 centros de columna y su
             rango en Y acota la tabla (descarta otras tablas de la página).
          3. Dentro de esa banda, asigna cada token a su columna y rellena un
             único hueco por integridad (componente faltante = CU - resto).
          4. Mapea las filas válidas (orden vertical) a niveles 1-4.
        """
        numericos = [(x, y, t) for (x, y, t) in palabras if self._es_numero(t)]
        if not numericos:
            return {}

        filas = self._agrupar_filas(numericos, self._TOL_FILA_PX)

        # ── Anclas: filas de 7 cifras con CU integro ──────────────────────
        anclas = []
        for yc, grupo in filas:
            if len(grupo) != 7:
                continue
            vals = [self._val(t) for (_, _, t) in grupo]
            if abs(sum(vals[:6]) - vals[6]) <= self._TOL_CU:
                anclas.append((yc, grupo))
        if not anclas:
            return {}

        # Centros de columna = mediana de X por posición entre las anclas
        centros = [
            float(np.median([grupo[c][0] for _, grupo in anclas]))
            for c in range(7)
        ]
        ys_ancla = [yc for yc, _ in anclas]
        # margen vertical = mayor separación típica entre filas de la tabla
        margen = max(
            (sorted(ys_ancla)[i + 1] - sorted(ys_ancla)[i]
             for i in range(len(ys_ancla) - 1)),
            default=self._TOL_FILA_PX * 2,
        )
        y_min, y_max = min(ys_ancla) - margen, max(ys_ancla) + margen

        # ── Filas dentro de la banda de la tabla ──────────────────────────
        candidatas: list[tuple[float, dict[str, float]]] = []
        for yc, grupo in filas:
            if not (y_min <= yc <= y_max):
                continue
            slots: dict[int, float] = {}
            for (x, _, t) in grupo:
                col = min(range(7), key=lambda c: abs(centros[c] - x))
                slots.setdefault(col, self._val(t))
            fila = self._cerrar_por_integridad(slots, 7)
            if fila is not None:
                candidatas.append((yc, fila))

        if not candidatas:
            return {}

        candidatas.sort(key=lambda c: c[0])
        return self._mapear_niveles_ocr(candidatas, palabras)

    def _cerrar_por_integridad(self, slots: dict[int, float], ncols: int):
        """
        ``slots`` mapea índice-de-columna -> valor. Espera 7 columnas
        (0..5 componentes, 6 = CU). Devuelve la fila [G,T,D,Cv,PR,R,CU] si el
        CU cierra (directamente o rellenando un único componente faltante);
        si no, ``None``.
        """
        cu_idx = ncols - 1
        if cu_idx not in slots:
            return None
        cu = slots[cu_idx]
        comp_idx = list(range(cu_idx))
        presentes = [i for i in comp_idx if i in slots]
        faltantes = [i for i in comp_idx if i not in slots]

        if len(presentes) == len(comp_idx):
            suma = sum(slots[i] for i in comp_idx)
            if abs(suma - cu) > self._TOL_CU:
                return None
        elif len(faltantes) == 1:
            falta = faltantes[0]
            slots[falta] = cu - sum(slots[i] for i in presentes)
            if slots[falta] < -self._TOL_CU:
                return None
        else:
            return None

        return [slots[i] for i in range(ncols)]

    def _mapear_niveles_ocr(self, candidatas, palabras) -> dict[str, list[float]]:
        """
        Mapea las filas válidas (ordenadas por Y) a niveles 1-4.

        Estrategia: localiza las etiquetas "NIVEL 2/3/4" por OCR y asigna la
        fila numérica más cercana en Y; el Nivel 1 toma la fila más cercana a
        "ENEL COLOMBIA" (o, en su defecto, la primera fila). Si no se detectan
        etiquetas (OCR sin español), usa el orden posicional: la primera fila
        es Nivel 1 (ENEL) y las tres últimas son Niveles 2, 3 y 4.
        """
        etiquetas = self._localizar_etiquetas(palabras)
        por_nivel: dict[str, list[float]] = {}

        if etiquetas:
            for nivel, ye in etiquetas.items():
                fila = min(candidatas, key=lambda c: abs(c[0] - ye))
                por_nivel[str(nivel)] = fila[1]
        if "1" not in por_nivel:
            por_nivel["1"] = candidatas[0][1]
        faltan = [n for n in ("2", "3", "4") if n not in por_nivel]
        if faltan and len(candidatas) >= 4:
            # las tres últimas filas son N2, N3, N4 (tras las 3 sub-filas de N1)
            ultimas = candidatas[-3:]
            for n, (_, fila) in zip(("2", "3", "4"), ultimas):
                por_nivel.setdefault(n, fila)

        return {n: por_nivel[n] for n in sorted(por_nivel) if n in {"1", "2", "3", "4"}}

    def _localizar_etiquetas(self, palabras) -> dict[int, float]:
        """Y-centro de las etiquetas 'NIVEL 2/3/4' / 'ENEL', si el OCR las leyó."""
        etiquetas: dict[int, float] = {}
        for (x, y, t) in palabras:
            m = re.search(r"NIVEL\s*([2-4])", t, re.I) or re.fullmatch(
                r"NIVEL([2-4])", t.upper()
            )
            if m:
                etiquetas.setdefault(int(m.group(1)), y)
            elif "ENEL" in t.upper() and 1 not in etiquetas:
                etiquetas[1] = y
        return etiquetas

    @staticmethod
    def _agrupar_filas(palabras, tol):
        """Agrupa [(x,y,t)] en filas por proximidad en Y -> [(yc, [(x,y,t)])]."""
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
    def _bandas_x(xs, n_objetivo=7):
        """Agrupa X en ~``n_objetivo`` bandas y devuelve sus centros ordenados."""
        if not xs:
            return []
        xs_ord = sorted(xs)
        rango = xs_ord[-1] - xs_ord[0]
        tol = max(rango / (n_objetivo * 2.5), 1.0)
        bandas: list[list[float]] = [[xs_ord[0]]]
        for x in xs_ord[1:]:
            if x - bandas[-1][-1] <= tol:
                bandas[-1].append(x)
            else:
                bandas.append([x])
        centros = [sum(b) / len(b) for b in bandas]
        # Si quedaron más de n_objetivo bandas, fusiona las más cercanas
        while len(centros) > n_objetivo:
            i = min(range(len(centros) - 1), key=lambda k: centros[k + 1] - centros[k])
            centros[i] = (centros[i] + centros[i + 1]) / 2
            del centros[i + 1]
        return centros

    # ── Construcción del DataFrame ─────────────────────────────────────────

    def _construir_df(self, por_nivel: dict[str, list[float]], fecha, ciclo) -> pd.DataFrame:
        filas = []
        for nivel_str, vals in por_nivel.items():
            comps = dict(zip(self._COLUMNAS, vals))
            filas.append({
                "Fecha": fecha,
                "Ciclo": ciclo,
                "Operador_Red": self.OPERADOR_RED,
                "Comercializador": self.COMERCIALIZADOR,
                "Nivel_Tension": int(nivel_str),
                "Tipo_Red": "SDL",
                "Comb_NT": f"NT{nivel_str}",
                "Dueno_Red": self.DUENO_RED,
                "G":  round(comps["G"], 4),
                "T":  round(comps["T"], 4),
                "D":  round(comps["D"], 4),
                "Cv": round(comps["Cv"], 4),
                "PR": round(comps["PR"], 4),
                "R":  round(comps["R"], 4),
                "CU": round(comps["CU"], 4),
            })
        return pd.DataFrame(filas).sort_values("Nivel_Tension").reset_index(drop=True)

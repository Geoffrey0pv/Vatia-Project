"""
Scraper VATIA (VATIA S.A. E.S.P.) — extrae los componentes del Costo Unitario
(CU) del mercado regulado para todos los operadores/mercados donde VATIA
comercializa energía.

Al igual que BIA y NEU, VATIA no publica PDF: expone las tarifas a través de una
API REST consumida por la página pública
``vatia.com.co/tarifas-costo-unitario-mercado-regulado``.

El flujo (idéntico al que ejecuta el JS público de esa página) es:

    1. Login (token Bearer)::
         POST https://srappmovilweb.vatia.com.co/api/mnl/v1/auth/login
              {"user": "demovatia", "password": "Kwh.2022"}
       -> {"token": "<JWT>"}

       Las credenciales ``demovatia`` / ``Kwh.2022`` NO son secretos del
       usuario: vienen embebidas en el JS público de la página de tarifas y
       sólo habilitan el acceso anónimo de consulta (mismo flujo que abre
       cualquier visitante del sitio).

    2. Tarifas (requiere ``Authorization: Bearer``)::
         GET https://srappmovilweb.vatia.com.co/api/mnl/v1/fee/update-rates
       -> un array con TODAS las filas (operador × ciclo × nivel × propiedad),
          sin parámetros. Una sola llamada trae todo el histórico publicado.

Cada fila del array trae el desglose CU. El mapeo de campos -> esquema unificado
se tomó de la definición del encabezado de la tabla en el propio bundle JS
(``addTableHeader``)::

    G  = gen    (GEN — Generación)
    T  = stn    (STN — Sistema de Transmisión Nacional)
    D  = sdl    (SDL — Sistema de Distribución Local)
    Cv = cmt    (CMT — Comercialización)
    PR = perg   (PERD — Pérdidas)
    R  = crs    (R   — Restricciones)
    CU = cu

    Operador_Red    = or_abbreviation
    Nivel_Tension   = voltage_level
    Ciclo           = cycle
    Propiedad red   = asset_ownership   (se conserva sólo la variante del OR)

Identidad CU verificada por construcción: G+T+D+Cv+PR+R = CU (se descartan las
filas que no la cumplen, igual que BIA/NEU).

Estado al implementar: el día de la implementación el backend de autenticación
de Vatia (``dnn.vatia.com.co``, upstream de ``srappmovilweb``) estaba caído
(login -> HTTP 401 "dial tcp ..."). El contrato (endpoints, cuerpo del login y
mapeo de columnas) quedó reconfirmado contra la API en vivo y el JS público,
pero NO se pudo correr una verificación end-to-end con datos reales. Por eso
``_obtener_token()`` levanta un ``RuntimeError`` explícito cuando el servidor no
responde, de modo que ``ejecutar()`` falle de forma ruidosa y trazable (nunca en
silencio) cuando Vatia esté indisponible.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from etl.base_scraper import ScraperBase


class VatiaScraper(ScraperBase):
    """Scraper para VATIA — mercado regulado (niveles 1, 2 y 3) en todos sus mercados."""

    competidor = "VATIA"

    TARIFAS_PAGE = "https://vatia.com.co/tarifas-costo-unitario-mercado-regulado/"
    LOGIN_URL = "https://srappmovilweb.vatia.com.co/api/mnl/v1/auth/login"
    RATES_URL = "https://srappmovilweb.vatia.com.co/api/mnl/v1/fee/update-rates"

    # Credenciales demo embebidas en el JS público (acceso anónimo de consulta;
    # no son secretos del usuario).
    _LOGIN_USER = "demovatia"
    _LOGIN_PASS = "Kwh.2022"

    COMERCIALIZADOR = "VATIA"
    DUENO_RED       = "100% OPERADOR"
    MIN_CICLO       = "202501"
    MAX_CICLO       = "202612"

    _NIVELES = (1, 2, 3)
    _TOL_CU  = 1.0

    # Campo de la respuesta -> componente del esquema unificado.
    _CAMPOS = {
        "G":  "gen",
        "T":  "stn",
        "D":  "sdl",
        "Cv": "cmt",
        "PR": "perg",
        "R":  "crs",
        "CU": "cu",
    }

    def __init__(self, directorio_raw: Path | None = None) -> None:
        super().__init__(directorio_raw)
        self._token: str | None = None
        self._ultima_fuente = "backend"

    def _cache_path(self, nombre_archivo: str = "Tarifas_VATIA_update_rates.json") -> Path:
        """Ruta del JSON cacheado localmente para fallback explícito."""
        return self.directorio_raw / nombre_archivo

    def _backend_unavailable(self, detalle: str) -> RuntimeError:
        """Error estándar y visible cuando el backend público de VATIA no responde."""
        return RuntimeError(f"VATIA backend unavailable. {detalle}")

    # ── Token Bearer (login anónimo de la página pública) ────────────────────

    def _obtener_token(self) -> str:
        """Hace login y devuelve el JWT. Levanta error claro si Vatia no responde."""
        if self._token:
            return self._token

        cuerpo = {"user": self._LOGIN_USER, "password": self._LOGIN_PASS}
        cabeceras = dict(self._DEFAULT_HEADERS)
        cabeceras["Content-Type"] = "application/json"
        cabeceras["Accept"] = "application/json"

        try:
            resp = requests.post(
                self.LOGIN_URL, json=cuerpo, headers=cabeceras, timeout=60
            )
        except requests.RequestException as exc:
            raise self._backend_unavailable(
                "[VATIA] No se pudo conectar al servidor de autenticación "
                f"({self.LOGIN_URL}). El backend público de Vatia parece caído o "
                f"inaccesible: {exc}"
            ) from exc

        if resp.status_code != 200:
            detalle = ""
            try:
                detalle = str(resp.json().get("message") or resp.text)
            except (ValueError, AttributeError):
                detalle = resp.text
            raise self._backend_unavailable(
                f"[VATIA] Login falló (HTTP {resp.status_code}). El backend de "
                "autenticación de Vatia puede estar caído. "
                f"Respuesta: {detalle[:200]}"
            )

        try:
            token = resp.json().get("token")
        except ValueError:
            token = None
        if not token:
            raise self._backend_unavailable(
                "[VATIA] El login respondió 200 pero no devolvió 'token'."
            )

        self._token = token
        return token

    def _descargar_dataset(self, url: str, headers: dict | None = None) -> bytes:
        """
        Replica el flujo real del frontend público:
        1. login anónimo para obtener Bearer token
        2. GET único a ``update-rates`` para traer TODO el dataset
        3. filtros operador/ciclo/NT se hacen del lado del cliente
        """
        cabeceras = dict(headers or self._DEFAULT_HEADERS)
        cabeceras["Authorization"] = f"Bearer {self._obtener_token()}"
        cabeceras["Accept"] = "application/json"
        self._ultima_fuente = "backend"
        return super().descargar(url, headers=cabeceras)

    # ── Interfaz ScraperBase ──────────────────────────────────────────────

    def obtener_enlaces(self) -> list[tuple[str, str]]:
        """
        VATIA expone todas las tarifas en una sola llamada a ``update-rates``
        (sin parámetros): un único "archivo" del que ``extraer()`` produce una
        fila por (operador, ciclo, nivel) dentro del rango de ciclos.
        """
        self.logger.info("[VATIA] 1 enlace (update-rates devuelve todo el histórico)")
        return [("Tarifas_VATIA_update_rates.json", self.RATES_URL)]

    def descargar(self, url: str, headers: dict | None = None) -> bytes:
        """Descarga con el header ``Authorization: Bearer`` requerido por la API."""
        cache_path = self._cache_path()
        try:
            return self._descargar_dataset(url, headers=headers)
        except Exception:
            if cache_path.is_file():
                self._ultima_fuente = "cache"
                self.logger.warning(
                    "[VATIA] Usando cache local por backend no disponible: %s",
                    cache_path,
                )
                return cache_path.read_bytes()
            raise

    def extraer(self, contenido: bytes, nombre_archivo: str) -> pd.DataFrame:
        """
        Parsea el array de ``update-rates`` y devuelve una fila por
        (operador, ciclo, nivel) — variante de red propiedad del operador.
        """
        datos = json.loads(contenido)
        arr = self._como_lista(datos)
        if arr is None:
            mensaje = ""
            if isinstance(datos, dict):
                mensaje = str(datos.get("message") or datos.get("detail") or datos)
            raise RuntimeError(
                f"Respuesta inesperada para '{nombre_archivo}': {mensaje or datos}"
            )

        operadores_detectados = {
            str(fila.get("or_abbreviation")).strip()
            for fila in arr
            if isinstance(fila, dict) and fila.get("or_abbreviation")
        }
        ciclos_detectados = {
            ciclo
            for fila in arr
            if isinstance(fila, dict)
            for ciclo in [self._ciclo_valido(fila.get("cycle"))]
            if ciclo is not None
        }
        niveles_detectados = {
            int(nivel)
            for fila in arr
            if isinstance(fila, dict)
            for nivel in [self._num(fila.get("voltage_level"))]
            if nivel is not None and int(nivel) in self._NIVELES
        }
        descartadas_integridad = 0
        registros: list[dict] = []
        for fila in arr:
            if not isinstance(fila, dict):
                continue

            ciclo = self._ciclo_valido(fila.get("cycle"))
            if ciclo is None:
                continue

            nivel = self._num(fila.get("voltage_level"))
            if nivel is None or int(nivel) not in self._NIVELES:
                continue
            nivel = int(nivel)

            valores = {
                comp: self._num(fila.get(campo))
                for comp, campo in self._CAMPOS.items()
            }
            if any(v is None for v in valores.values()):
                continue

            suma = (
                valores["G"] + valores["T"] + valores["D"]
                + valores["Cv"] + valores["PR"] + valores["R"]
            )
            if abs(suma - valores["CU"]) > self._TOL_CU:
                descartadas_integridad += 1
                self.logger.warning(
                    "[VATIA] %s N%d %s: descartada por integridad CU "
                    "(suma=%.4f, CU=%.4f)",
                    fila.get("or_abbreviation"), nivel,
                    fila.get("asset_ownership"), suma, valores["CU"],
                )
                continue

            operador = (fila.get("or_abbreviation") or "").strip()
            propiedad = (fila.get("asset_ownership") or "").strip()
            fecha = datetime(int(ciclo[:4]), int(ciclo[4:6]), 1).strftime("%Y-%m-%d")

            registros.append({
                "Fecha": fecha,
                "Ciclo": ciclo,
                "Operador_Red": operador,
                "Comercializador": self.COMERCIALIZADOR,
                "Nivel_Tension": nivel,
                "Tipo_Red": "SDL",
                "Comb_NT": f"NT{nivel}",
                "Dueno_Red": self.DUENO_RED,
                "G":  round(valores["G"], 4),
                "T":  round(valores["T"], 4),
                "D":  round(valores["D"], 4),
                "Cv": round(valores["Cv"], 4),
                "PR": round(valores["PR"], 4),
                "R":  round(valores["R"], 4),
                "CU": round(valores["CU"], 4),
                "_propiedad": propiedad,
            })

        # Una fila por (operador, ciclo, nivel): la variante de red del operador.
        registros = self._seleccionar_operador(registros)

        if not registros:
            raise RuntimeError(
                f"No se extrajo ninguna tarifa válida de '{nombre_archivo}'."
            )

        df = pd.DataFrame(registros).drop(columns=["_propiedad"])
        df = df.sort_values(
            ["Ciclo", "Operador_Red", "Nivel_Tension"]
        ).reset_index(drop=True)
        self.logger.info(
            "[VATIA] %s -> %d fila(s) válidas | fuente=%s | operadores=%d | ciclos=%d | niveles=%d | descartadas=%d",
            nombre_archivo,
            len(df),
            self._ultima_fuente,
            len(operadores_detectados),
            len(ciclos_detectados),
            len(niveles_detectados),
            descartadas_integridad,
        )
        return df

    # ── Utilidades ──────────────────────────────────────────────────────────

    def _seleccionar_operador(self, registros: list[dict]) -> list[dict]:
        """
        Deduplica por (operador, ciclo, nivel) conservando la variante de red
        propiedad del operador. Cuando ``asset_ownership`` no la marca de forma
        inequívoca, se toma la fila de mayor CU (la red del operador es la de
        mayor distribución, patrón confirmado en NEU/ERCO).
        """
        mejor: dict[tuple, dict] = {}
        for reg in registros:
            clave = (reg["Operador_Red"], reg["Ciclo"], reg["Nivel_Tension"])
            actual = mejor.get(clave)
            if actual is None:
                mejor[clave] = reg
                continue
            cand_op = self._es_operador(reg["_propiedad"])
            act_op = self._es_operador(actual["_propiedad"])
            if (cand_op and not act_op) or (
                cand_op == act_op and reg["CU"] > actual["CU"]
            ):
                mejor[clave] = reg
        return list(mejor.values())

    @staticmethod
    def _es_operador(propiedad: str) -> bool:
        """¿La propiedad de la red indica que es del operador (OR)?"""
        p = (propiedad or "").upper()
        return "OPERAD" in p or "DEL OR" in p or p in {"OR", "100% OPERADOR"}

    @staticmethod
    def _como_lista(datos) -> list | None:
        """Normaliza la respuesta a una lista de filas, o None si no se puede."""
        if isinstance(datos, list):
            return datos
        if isinstance(datos, dict):
            for clave in ("data", "rates", "result", "results"):
                valor = datos.get(clave)
                if isinstance(valor, list):
                    return valor
        return None

    def _ciclo_valido(self, valor) -> str | None:
        """Devuelve el ciclo AAAAMM si está dentro del rango configurado."""
        if valor is None:
            return None
        ciclo = str(valor).strip()
        if len(ciclo) != 6 or not ciclo.isdigit():
            return None
        if not (self.MIN_CICLO <= ciclo <= self.MAX_CICLO):
            return None
        return ciclo

    @staticmethod
    def _num(valor) -> float | None:
        """Convierte a float; devuelve None si no es un número válido."""
        if valor is None:
            return None
        try:
            return float(valor)
        except (TypeError, ValueError):
            return None

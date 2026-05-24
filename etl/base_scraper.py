"""Clase base abstracta para todos los scrapers de competidores."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd
import requests


class ScraperBase(ABC):
    """
    Interfaz común para todos los scrapers de tarifas de competidores.

    Cada subclase concreta (ej. CensScraper) debe:
      - Definir el atributo de clase ``competidor`` (nombre del operador).
      - Implementar ``obtener_enlaces()`` para detectar los archivos del mes.
      - Implementar ``extraer()`` para parsear el contenido descargado.

    El método ``ejecutar()`` orquesta el flujo completo y devuelve un
    DataFrame consolidado listo para normalización.
    """

    competidor: str  # Nombre único del operador — definir en cada subclase

    # ── Cabecera HTTP estándar ────────────────────────────────────────────────
    _DEFAULT_HEADERS: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    def __init__(self, directorio_raw: Path | None = None) -> None:
        self.directorio_raw = directorio_raw or (
            Path("data/raw") / self.competidor.lower()
        )
        self.directorio_raw.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(self.__class__.__name__)

    # ── Métodos abstractos (deben implementarse en subclases) ─────────────────

    @abstractmethod
    def obtener_enlaces(self) -> list[tuple[str, str]]:
        """
        Detecta y retorna los archivos disponibles del mes actual.

        Returns:
            Lista de ``(nombre_archivo, url_descarga)``.
            Ej: ``[("Tarifas_CENS_202601_.pdf", "https://...")]``
        """
        ...

    @abstractmethod
    def extraer(self, contenido: bytes, nombre_archivo: str) -> pd.DataFrame:
        """
        Parsea el contenido descargado y extrae la tabla de componentes CU.

        Args:
            contenido:       Bytes del archivo (PDF, Excel, etc.)
            nombre_archivo:  Nombre original del archivo (para extraer ciclo/fecha).

        Returns:
            DataFrame con columnas mínimas: Fecha, Ciclo, Comercializador,
            Nivel_Tension, G, T, D, Cv, PR, R, CU.
        """
        ...

    # ── Métodos concretos (heredados por todas las subclases) ─────────────────

    def descargar(self, url: str, headers: dict | None = None) -> bytes:
        """Descarga el contenido de una URL (60 s de timeout)."""
        resp = requests.get(
            url,
            headers=headers or self._DEFAULT_HEADERS,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.content

    def guardar_raw(self, contenido: bytes, nombre: str) -> Path:
        """Persiste el archivo descargado en ``data/raw/<competidor>/``."""
        ruta = self.directorio_raw / nombre
        ruta.write_bytes(contenido)
        return ruta

    def ejecutar(self) -> pd.DataFrame:
        """
        Orquesta el pipeline de un competidor:
            1. ``obtener_enlaces()``  — detecta archivos disponibles
            2. ``descargar()``        — descarga cada archivo
            3. ``guardar_raw()``      — persiste en data/raw/
            4. ``extraer()``          — parsea y extrae componentes CU
            5. Consolida resultados en un único DataFrame

        Returns:
            DataFrame consolidado con todas las filas extraídas.

        Raises:
            RuntimeError: Si ningún archivo pudo procesarse.
        """
        enlaces = self.obtener_enlaces()
        self.logger.info(
            "%s: %d archivo(s) encontrado(s)", self.competidor, len(enlaces)
        )

        resultados: list[pd.DataFrame] = []
        errores: list[str] = []

        for nombre, url in enlaces:
            try:
                contenido = self.descargar(url)
                self.guardar_raw(contenido, nombre)
                df = self.extraer(contenido, nombre)
                resultados.append(df)
                self.logger.info("✔ %s procesado (%d filas)", nombre, len(df))
            except Exception as exc:
                msg = f"{nombre}: {exc}"
                self.logger.error("✖ %s", msg)
                errores.append(msg)

        if not resultados:
            raise RuntimeError(
                f"{self.competidor}: no se procesó ningún archivo. "
                f"Errores: {errores}"
            )

        return pd.concat(resultados, ignore_index=True)

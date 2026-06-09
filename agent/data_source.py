"""
Fuente de datos del agente: carga las tarifas y las normaliza a un esquema
canónico en minúsculas, independientemente del origen.

Orden de preferencia:
    1. PostgreSQL  (si ``DATABASE_URL`` está disponible y responde)
    2. CSVs locales en ``data/processed/tarifas_*.csv``  (respaldo)

Esquema canónico devuelto (columnas en minúscula):
    fecha, ciclo, operador_red, comercializador, nivel_tension,
    tipo_red, comb_nt, dueno_red, g, t, d, cv, pr, r, cu
"""

from __future__ import annotations

import logging
import os

import pandas as pd

from agent.config import ROOT

logger = logging.getLogger(__name__)

# Columnas numéricas de componentes del Costo Unitario.
COMPONENTES: list[str] = ["g", "t", "d", "cv", "pr", "r", "cu"]

_COLUMNAS = [
    "fecha", "ciclo", "operador_red", "comercializador", "nivel_tension",
    "tipo_red", "comb_nt", "dueno_red", *COMPONENTES,
]


def _normalizar(df: pd.DataFrame) -> pd.DataFrame:
    """Lleva cualquier DataFrame de tarifas al esquema canónico en minúsculas."""
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Garantizar que existan todas las columnas esperadas.
    for col in _COLUMNAS:
        if col not in df.columns:
            df[col] = pd.NA

    # Tipos: ciclo y nivel como texto/entero estables.
    df["ciclo"] = df["ciclo"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df["comercializador"] = df["comercializador"].astype(str).str.strip().str.upper()
    df["nivel_tension"] = pd.to_numeric(df["nivel_tension"], errors="coerce").astype("Int64")

    # Componentes a float.
    for col in COMPONENTES:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df[_COLUMNAS]
    df = df.dropna(subset=["ciclo", "comercializador", "nivel_tension"])
    return df.reset_index(drop=True)


def _desde_postgres() -> pd.DataFrame | None:
    """Intenta cargar desde PostgreSQL; None si no es posible."""
    if not os.environ.get("DATABASE_URL"):
        return None
    try:
        from db.queries import obtener_todas_tarifas

        df = obtener_todas_tarifas()
        if df is not None and not df.empty:
            logger.info("Tarifas cargadas desde PostgreSQL: %d filas", len(df))
            return df
    except Exception as exc:  # DB caída, sin red, etc.
        logger.warning("PostgreSQL no disponible (%s) — usando CSV.", exc.__class__.__name__)
    return None


def _desde_csv() -> pd.DataFrame:
    """Carga y concatena todos los CSVs de data/processed/tarifas_*.csv."""
    carpeta = ROOT / "data" / "processed"
    rutas = sorted(carpeta.glob("tarifas_*.csv"))
    frames: list[pd.DataFrame] = []
    for ruta in rutas:
        try:
            frames.append(pd.read_csv(ruta, sep=";", decimal=",", encoding="utf-8-sig"))
        except Exception as exc:  # pragma: no cover - archivo corrupto puntual
            logger.warning("No se pudo leer %s: %s", ruta.name, exc)
    if not frames:
        logger.warning("No se encontraron CSVs en %s", carpeta)
        return pd.DataFrame(columns=_COLUMNAS)
    df = pd.concat(frames, ignore_index=True)
    logger.info("Tarifas cargadas desde %d CSV(s): %d filas", len(frames), len(df))
    return df


def cargar_tarifas() -> pd.DataFrame:
    """
    Devuelve todas las tarifas normalizadas al esquema canónico.

    Prefiere PostgreSQL; si no está disponible, usa los CSVs de respaldo.
    """
    df = _desde_postgres()
    if df is None:
        df = _desde_csv()
    return _normalizar(df)

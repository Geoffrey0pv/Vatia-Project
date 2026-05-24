"""
Normalización al esquema unificado y validación de integridad CU.

Esquema de salida (15 columnas):
    Fecha, Ciclo, Operador_Red, Comercializador, Nivel_Tension,
    Tipo_Red, Comb_NT, Dueno_Red, G, T, D, Cv, PR, R, CU
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

COLUMNAS_ESQUEMA: list[str] = [
    "Fecha", "Ciclo", "Operador_Red", "Comercializador",
    "Nivel_Tension", "Tipo_Red", "Comb_NT", "Dueno_Red",
    "G", "T", "D", "Cv", "PR", "R", "CU",
]

COMPONENTES_CU: list[str] = ["G", "T", "D", "Cv", "PR", "R"]

# Alias para unificar nombres de columnas que varían entre scrapers
_ALIAS: dict[str, str] = {
    "Dueño_Red": "Dueno_Red",
    "Dueno_red": "Dueno_Red",
    "Nivel_tension": "Nivel_Tension",
    "comercializador": "Comercializador",
    "ciclo": "Ciclo",
    "fecha": "Fecha",
}


def normalizar(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza un DataFrame de extracción al esquema unificado de 15 columnas.

    - Renombra columnas con alias.
    - Agrega columnas faltantes con ``None``.
    - Asegura tipos de dato correctos.
    - Ordena por Ciclo y Nivel_Tension.

    Args:
        df: DataFrame crudo de un scraper.

    Returns:
        DataFrame con exactamente las columnas de ``COLUMNAS_ESQUEMA``.
    """
    df = df.rename(columns=_ALIAS).copy()

    # Agregar columnas del esquema que no existan
    for col in COLUMNAS_ESQUEMA:
        if col not in df.columns:
            df[col] = None

    # Seleccionar y ordenar columnas según el esquema
    df = df[COLUMNAS_ESQUEMA].copy()

    # ── Tipos ──────────────────────────────────────────────────────────────
    df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["Ciclo"] = df["Ciclo"].astype(str).str.strip()
    df["Nivel_Tension"] = pd.to_numeric(df["Nivel_Tension"], errors="coerce").astype("Int64")

    for col in ["G", "T", "D", "Cv", "PR", "R", "CU"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").round(4)

    # Ordenar
    df = df.sort_values(
        ["Ciclo", "Nivel_Tension"],
        key=lambda s: s.astype(str),
    ).reset_index(drop=True)

    return df


def validar_integridad_cu(df: pd.DataFrame, tolerancia: float = 0.5) -> pd.DataFrame:
    """
    Verifica que CU ≈ G + T + D + Cv + PR + R (tolerancia en $/kWh).

    Registra advertencias para las filas que no cumplen la condición, pero
    **no las elimina** — la decisión de cómo tratarlas queda al pipeline.

    Args:
        df:          DataFrame normalizado.
        tolerancia:  Diferencia máxima aceptada (default 0.5 $/kWh).

    Returns:
        El mismo DataFrame sin modificaciones.
    """
    cols = [c for c in COMPONENTES_CU if c in df.columns]
    if len(cols) < len(COMPONENTES_CU) or "CU" not in df.columns:
        return df

    suma = df[cols].sum(axis=1)
    diff = (df["CU"] - suma).abs()
    invalidas = diff > tolerancia

    if invalidas.any():
        logger.warning(
            "%d fila(s) donde CU ≠ G+T+D+Cv+PR+R (diff > %.2f $/kWh):\n%s",
            invalidas.sum(),
            tolerancia,
            df.loc[invalidas, ["Ciclo", "Comercializador", "Nivel_Tension", "CU"] + cols]
            .to_string(index=False),
        )

    return df

"""
Capa de carga — escribe datos normalizados en PostgreSQL y exporta CSV.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ── Columnas del esquema unificado (en orden para INSERT) ────────────────────
_COLS_DB: list[str] = [
    "fecha", "ciclo", "operador_red", "comercializador",
    "nivel_tension", "tipo_red", "comb_nt", "dueno_red",
    "g", "t", "d", "cv", "pr", "r", "cu",
]

# Mapeo DataFrame → columna PostgreSQL
_COL_MAP: dict[str, str] = {
    "Fecha":           "fecha",
    "Ciclo":           "ciclo",
    "Operador_Red":    "operador_red",
    "Comercializador": "comercializador",
    "Nivel_Tension":   "nivel_tension",
    "Tipo_Red":        "tipo_red",
    "Comb_NT":         "comb_nt",
    "Dueno_Red":       "dueno_red",
    "G":  "g",  "T": "t",  "D": "d",
    "Cv": "cv", "PR": "pr", "R": "r", "CU": "cu",
}


def upsert_tarifas(df: pd.DataFrame) -> int:
    """
    Inserta o actualiza filas en la tabla ``tarifas`` de PostgreSQL.

    Usa ``ON CONFLICT (ciclo, comercializador, nivel_tension) DO UPDATE``
    para ser idempotente (re-ejecutar no duplica datos).

    Args:
        df: DataFrame normalizado (esquema de 15 columnas).

    Returns:
        Número de filas procesadas.

    Raises:
        EnvironmentError: Si ``DATABASE_URL`` no está configurado.
        psycopg2.Error:   Si la consulta falla.
    """
    import psycopg2
    from psycopg2.extras import execute_values

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise EnvironmentError(
            "DATABASE_URL no está configurado. "
            "Copia .env.example a .env y completa los valores."
        )

    df_db = df.rename(columns=_COL_MAP).copy()
    cols_presentes = [c for c in _COLS_DB if c in df_db.columns]
    records = [
        tuple(row[c] if pd.notna(row.get(c)) else None for c in cols_presentes)
        for _, row in df_db.iterrows()
    ]

    conflict_cols = {"ciclo", "comercializador", "nivel_tension"}
    update_set = ", ".join(
        f"{c} = EXCLUDED.{c}"
        for c in cols_presentes
        if c not in conflict_cols
    )

    sql = f"""
        INSERT INTO tarifas ({', '.join(cols_presentes)})
        VALUES %s
        ON CONFLICT (ciclo, comercializador, nivel_tension)
        DO UPDATE SET {update_set}, updated_at = NOW()
    """

    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, records)
        conn.commit()

    logger.info("Upserted %d filas en PostgreSQL (tabla: tarifas)", len(records))
    return len(records)


def exportar_csv(df: pd.DataFrame, ruta: str | Path) -> Path:
    """
    Exporta el DataFrame a CSV con formato Power BI (separador `;`, decimal `,`).

    Args:
        df:    DataFrame normalizado.
        ruta:  Ruta de salida (se crea el directorio si no existe).

    Returns:
        Ruta absoluta del CSV generado.
    """
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    # Redondear columnas numéricas
    df_out = df.copy()
    for col in df_out.select_dtypes(include="number").columns:
        df_out[col] = df_out[col].round(4)

    df_out.to_csv(ruta, sep=";", decimal=",", index=False, encoding="utf-8-sig")
    logger.info("CSV exportado: %s (%d filas)", ruta.resolve(), len(df_out))
    return ruta.resolve()

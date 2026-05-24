"""
Consultas SQL reutilizables para el dashboard de Streamlit.

Todas las funciones devuelven DataFrames de pandas y están diseñadas
para usarse con @st.cache_data en la capa de presentación.
"""

from __future__ import annotations

import pandas as pd

from db.connection import get_connection, get_engine


def listar_ciclos() -> list[str]:
    """Retorna los ciclos disponibles ordenados de más reciente a más antiguo."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ciclo FROM tarifas ORDER BY ciclo DESC")
            return [row["ciclo"] for row in cur.fetchall()]


def listar_comercializadores() -> list[str]:
    """Retorna todos los comercializadores con datos cargados."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT comercializador FROM tarifas ORDER BY comercializador"
            )
            return [row["comercializador"] for row in cur.fetchall()]


def obtener_tarifas(
    ciclo: str | None = None,
    nivel: int | None = None,
    comercializador: str | None = None,
) -> pd.DataFrame:
    """
    Consulta flexible de tarifas con filtros opcionales.

    Args:
        ciclo:            Filtro por ciclo AAAAMM.
        nivel:            Filtro por nivel de tensión (1-4).
        comercializador:  Filtro por nombre del comercializador.

    Returns:
        DataFrame con todas las columnas de la tabla ``tarifas``.
    """
    sql = "SELECT * FROM tarifas WHERE 1=1"
    params: list = []

    if ciclo:
        sql += " AND ciclo = %s"
        params.append(ciclo)
    if nivel:
        sql += " AND nivel_tension = %s"
        params.append(nivel)
    if comercializador:
        sql += " AND comercializador = %s"
        params.append(comercializador)

    sql += " ORDER BY ciclo DESC, comercializador, nivel_tension"

    return pd.read_sql_query(sql, get_engine(), params=params)


def obtener_todas_tarifas() -> pd.DataFrame:
    """Descarga todas las tarifas históricas (para uso con @st.cache_data)."""
    return pd.read_sql_query(
        "SELECT * FROM tarifas ORDER BY ciclo DESC, comercializador, nivel_tension",
        get_engine(),
    )


def comparativo_actual() -> pd.DataFrame:
    """Retorna el comparativo del último ciclo disponible desde la vista."""
    return pd.read_sql_query("SELECT * FROM v_comparativo_actual", get_engine())


def evolucion_cu(
    comercializador: str | None = None,
    nivel: int | None = None,
) -> pd.DataFrame:
    """Retorna la evolución histórica del CU con variación mes a mes."""
    sql = "SELECT * FROM v_evolucion_cu WHERE 1=1"
    params: list = []

    if comercializador:
        sql += " AND comercializador = %s"
        params.append(comercializador)
    if nivel:
        sql += " AND nivel_tension = %s"
        params.append(nivel)

    sql += " ORDER BY comercializador, nivel_tension, ciclo"

    return pd.read_sql_query(sql, get_engine(), params=params)


def stats_ciclo(ciclo: str) -> dict:
    """
    Estadísticas rápidas de un ciclo: min, max, promedio de CU por nivel.

    Returns:
        Dict con nivel_tension como llave y sub-dict {min, max, avg} como valor.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    nivel_tension,
                    MIN(cu)::float  AS cu_min,
                    MAX(cu)::float  AS cu_max,
                    ROUND(AVG(cu), 4)::float AS cu_avg,
                    COUNT(*)        AS n_competidores
                FROM tarifas
                WHERE ciclo = %s
                GROUP BY nivel_tension
                ORDER BY nivel_tension
                """,
                (ciclo,),
            )
            return {
                row["nivel_tension"]: {
                    "min": row["cu_min"],
                    "max": row["cu_max"],
                    "avg": row["cu_avg"],
                    "n":   row["n_competidores"],
                }
                for row in cur.fetchall()
            }

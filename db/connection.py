"""Gestión de la conexión a PostgreSQL."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.extras
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def get_db_url() -> str:
    """Devuelve DATABASE_URL desde las variables de entorno."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise EnvironmentError(
            "DATABASE_URL no está configurado. "
            "Copia .env.example a .env y completa los valores.\n"
            "Ejemplo: postgresql://vatia:password@localhost:5432/vatia"
        )
    return url


@contextmanager
def get_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Context manager que abre y cierra una conexión PostgreSQL.

    Uso:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ...")
    """
    conn = psycopg2.connect(get_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


def get_engine() -> Engine:
    """Retorna un SQLAlchemy Engine para uso con pandas.read_sql_query()."""
    return create_engine(get_db_url())

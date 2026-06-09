"""
Cargador de respaldo: inserta los CSVs ya procesados de ``data/processed/`` en
PostgreSQL sin volver a hacer scraping ni OCR.

Útil para poblar la base con todos los competidores cuyos CSV ya existen
(EPM, BIA, ESSA, …) y dejar los datos disponibles para el dashboard y el
agente RAG.

Uso:
    python -m etl.load_csvs                 # carga todos los CSVs
    python -m etl.load_csvs --solo epm      # carga uno
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from etl.load import upsert_tarifas

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PROCESSED_DIR = Path("data/processed")


def cargar(solo: str | None = None) -> dict[str, str]:
    """Carga los CSVs de data/processed/ a PostgreSQL vía upsert idempotente."""
    rutas = sorted(PROCESSED_DIR.glob("tarifas_*.csv"))
    if solo:
        rutas = [r for r in rutas if r.stem == f"tarifas_{solo.lower()}"]

    resultados: dict[str, str] = {}
    for ruta in rutas:
        nombre = ruta.stem.replace("tarifas_", "").upper()
        try:
            # Ciclo como texto (CHAR(6) en la DB) para evitar errores de tipo.
            df = pd.read_csv(
                ruta, sep=";", decimal=",", encoding="utf-8-sig", dtype={"Ciclo": str}
            )
            df.columns = [c.strip() for c in df.columns]
            # Deduplicar por la clave única (un INSERT no puede afectar la misma
            # fila dos veces en ON CONFLICT). Se conserva la última aparición.
            claves = ["Ciclo", "Comercializador", "Nivel_Tension"]
            antes = len(df)
            df = df.drop_duplicates(subset=claves, keep="last")
            if len(df) < antes:
                logger.info("  %s: %d filas duplicadas omitidas", nombre, antes - len(df))
            n = upsert_tarifas(df)
            resultados[nombre] = f"✔ {n} filas"
            logger.info("✔ %s: %d filas cargadas", nombre, n)
        except Exception as exc:
            resultados[nombre] = f"✖ ERROR: {exc}"
            logger.error("✖ %s falló: %s", nombre, exc)

    logger.info("%s", "=" * 50)
    logger.info("RESUMEN DE CARGA")
    for comp, estado in resultados.items():
        logger.info("  %-12s %s", comp, estado)
    return resultados


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cargar CSVs procesados a PostgreSQL")
    parser.add_argument("--solo", metavar="COMPETIDOR", help="Cargar solo un competidor")
    args = parser.parse_args()
    cargar(solo=args.solo)

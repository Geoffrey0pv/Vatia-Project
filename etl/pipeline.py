"""
Pipeline principal — orquesta todos los scrapers registrados.

Uso:
    python -m etl.pipeline                     # Todos los competidores
    python -m etl.pipeline --solo cens         # Solo CENS
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # Carga .env si existe (útil para desarrollo local)

from etl.scrapers.aire import AireScraper
from etl.scrapers.afinia import AfiniaScraper
from etl.scrapers.bia import BiaScraper
from etl.scrapers.cens import CensScraper
from etl.scrapers.codensa import CodensaScraper
from etl.scrapers.emcali import EmcaliScraper
from etl.scrapers.epm import EpmScraper
from etl.scrapers.essa import EssaScraper
from etl.transform import normalizar, validar_integridad_cu
from etl.load import upsert_tarifas, exportar_csv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Scrapers registrados — agregar nuevos aquí ────────────────────────────────
SCRAPERS = [
    AireScraper,
    AfiniaScraper,
    BiaScraper,
    CensScraper,
    CodensaScraper,
    EmcaliScraper,
    EpmScraper,
    EssaScraper,
]


def _slug(texto: str) -> str:
    """Normaliza nombres para permitir coincidencias con o sin guiones/espacios."""
    return "".join(ch for ch in texto.lower() if ch.isalnum())


def run_all(solo: str | None = None) -> None:
    """
    Ejecuta el pipeline ETL para todos (o un solo) competidor.

    Args:
        solo: Si se especifica, solo ejecuta el scraper con ese nombre.
    """
    scrapers_a_ejecutar = [
        S for S in SCRAPERS
        if solo is None or _slug(S.competidor) == _slug(solo)
    ]

    if not scrapers_a_ejecutar:
        nombres = [S.competidor for S in SCRAPERS]
        logger.error("Competidor '%s' no encontrado. Disponibles: %s", solo, nombres)
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("PIPELINE ETL VATIA — %d competidor(es)", len(scrapers_a_ejecutar))
    logger.info("=" * 60)

    resultados: dict[str, str] = {}

    for ScraperClass in scrapers_a_ejecutar:
        nombre = ScraperClass.competidor
        logger.info("\n── %s ─────────────────────────────────────", nombre)
        try:
            # 1. Extraer
            scraper = ScraperClass()
            df_raw = scraper.ejecutar()

            # 2. Transformar
            df_norm = normalizar(df_raw)
            df_norm = validar_integridad_cu(df_norm)

            # 3. Cargar a PostgreSQL (si DATABASE_URL disponible)
            try:
                upsert_tarifas(df_norm)
            except EnvironmentError as e:
                logger.warning("PostgreSQL no disponible: %s — solo CSV.", e)

            # 4. Exportar CSV de respaldo
            csv_ruta = Path("data/processed") / f"tarifas_{_slug(nombre)}.csv"
            exportar_csv(df_norm, csv_ruta)

            resultados[nombre] = f"✔ {len(df_norm)} filas"
            logger.info("✔ %s: %d filas procesadas", nombre, len(df_norm))

        except Exception as exc:
            resultados[nombre] = f"✖ ERROR: {exc}"
            logger.error("✖ %s falló: %s", nombre, exc, exc_info=True)

    # Resumen final
    logger.info("\n%s", "=" * 60)
    logger.info("RESUMEN FINAL")
    logger.info("=" * 60)
    for comp, estado in resultados.items():
        logger.info("  %-20s %s", comp, estado)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline ETL VATIA")
    parser.add_argument("--solo", metavar="COMPETIDOR", help="Ejecutar solo un competidor")
    args = parser.parse_args()
    run_all(solo=args.solo)

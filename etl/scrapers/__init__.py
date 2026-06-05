"""Scrapers disponibles para importación directa."""

from etl.scrapers.afinia import AfiniaScraper
from etl.scrapers.aire import AireScraper
from etl.scrapers.bia import BiaScraper
from etl.scrapers.cens import CensScraper
from etl.scrapers.codensa import CodensaScraper
from etl.scrapers.emcali import EmcaliScraper
from etl.scrapers.epm import EpmScraper
from etl.scrapers.essa import EssaScraper

__all__ = [
    "AfiniaScraper", "AireScraper", "BiaScraper", "CensScraper",
    "CodensaScraper", "EmcaliScraper", "EpmScraper", "EssaScraper",
]

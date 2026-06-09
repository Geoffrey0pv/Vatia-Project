"""
Configuración central del Agente IA RAG.

Lee las variables de entorno necesarias (clave de Gemini, modelos y rutas).
Cargar primero un ``.env`` con ``python-dotenv`` si está disponible.
"""

from __future__ import annotations

import os
from pathlib import Path

try:  # cargar .env en ejecución local (en Docker las vars vienen de env_file)
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv es opcional
    pass

# ── Raíz del proyecto ─────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent

# ── Gemini ────────────────────────────────────────────────────────────────────
# La clave NUNCA va en el código: se inyecta vía .env / variables de entorno.
GEMINI_API_KEY: str | None = os.environ.get("GEMINI_API_KEY") or os.environ.get(
    "GOOGLE_API_KEY"
)

# Modelo de generación (chat). Configurable para migrar sin tocar el código.
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")

# Modelo de embeddings para la indexación semántica en ChromaDB.
GEMINI_EMBED_MODEL: str = os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")

# Dimensión de los embeddings (gemini-embedding-001 admite 768/1536/3072).
EMBED_DIM: int = int(os.environ.get("GEMINI_EMBED_DIM", "768"))

# ── ChromaDB ──────────────────────────────────────────────────────────────────
# Persistencia bajo data/ (volumen montado en Docker → sobrevive reinicios).
CHROMA_DIR: Path = Path(os.environ.get("CHROMA_DIR", str(ROOT / "data" / "chroma_db")))
CHROMA_COLLECTION: str = os.environ.get("CHROMA_COLLECTION", "tarifas_vatia")

# ── Parámetros de recuperación / generación ──────────────────────────────────
TOP_K: int = int(os.environ.get("RAG_TOP_K", "8"))
TEMPERATURE: float = float(os.environ.get("RAG_TEMPERATURE", "0.2"))
MAX_HISTORIAL: int = int(os.environ.get("RAG_MAX_HISTORIAL", "8"))


def gemini_disponible() -> bool:
    """True si hay una API key de Gemini configurada y con aspecto válido."""
    key = GEMINI_API_KEY
    return bool(key) and key not in {"", "tu-api-key-aqui", "AIza..."}


def estado_config() -> dict[str, object]:
    """Resumen del estado de configuración (para diagnóstico en la UI)."""
    return {
        "gemini_api_key": gemini_disponible(),
        "modelo": GEMINI_MODEL,
        "modelo_embeddings": GEMINI_EMBED_MODEL,
        "chroma_dir": str(CHROMA_DIR),
        "coleccion": CHROMA_COLLECTION,
        "top_k": TOP_K,
    }

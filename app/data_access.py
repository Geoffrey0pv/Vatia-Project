"""Carga consolidada de datos para el dashboard multiempresa VATIA."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

EXPECTED_SOURCES = [
    "AIR-E",
    "AFINIA",
    "BIA",
    "CENS",
    "CODENSA",
    "EMCALI",
    "EPM",
    "ESSA",
    "NEU",
    "VATIA",
]

STANDARD_COLUMNS = [
    "Fecha",
    "Ciclo",
    "Operador_Red",
    "Comercializador",
    "Nivel_Tension",
    "Tipo_Red",
    "Comb_NT",
    "Dueno_Red",
    "G",
    "T",
    "D",
    "Cv",
    "PR",
    "R",
    "CU",
]

NUMERIC_COLUMNS = ["G", "T", "D", "Cv", "PR", "R", "CU"]
IGNORED_FILENAMES = {"tarifas_air-e.csv"}
CATEGORICAL_FILL_VALUES = {
    "Operador_Red": "SIN OPERADOR / RED",
    "Tipo_Red": "SIN TIPO DE RED",
    "Comb_NT": "SIN COMBINACIÓN",
    "Dueno_Red": "SIN DUEÑO DE RED",
}

SOURCE_TO_SLUG = {
    "AIR-E": "aire",
    "AFINIA": "afinia",
    "BIA": "bia",
    "CENS": "cens",
    "CODENSA": "codensa",
    "EMCALI": "emcali",
    "EPM": "epm",
    "ESSA": "essa",
    "NEU": "neu",
    "VATIA": "vatia",
}


def _slug(texto: str) -> str:
    return "".join(ch for ch in texto.lower() if ch.isalnum())


def _normalize_categorical_value(valor, fallback: str) -> str:
    """Normaliza nulos o vacíos categóricos a una etiqueta visible para UI."""
    texto = "" if pd.isna(valor) else str(valor).strip()
    if not texto or texto.lower() in {"nan", "none", "<na>", "nat"}:
        return fallback
    return texto


def _prepare_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza el DataFrame al esquema estándar y calcula diff_cu."""
    if df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS + ["diff_cu"])

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    faltantes = [c for c in STANDARD_COLUMNS if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas estándar: {faltantes}")

    df = df[STANDARD_COLUMNS].copy()

    for col in ["Fecha", "Ciclo", "Comercializador"]:
        df[col] = df[col].astype(str).str.strip()

    for col, fallback in CATEGORICAL_FILL_VALUES.items():
        df[col] = df[col].apply(lambda valor: _normalize_categorical_value(valor, fallback))

    df["Nivel_Tension"] = pd.to_numeric(df["Nivel_Tension"], errors="coerce")
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(",", ".", regex=False),
            errors="coerce",
        )

    df["diff_cu"] = (
        df["CU"] - df[["G", "T", "D", "Cv", "PR", "R"]].sum(axis=1)
    ).abs()

    return df.dropna(subset=["Ciclo", "Comercializador", "Nivel_Tension", "CU"]).reset_index(drop=True)


def load_from_database() -> pd.DataFrame:
    """Carga tarifas desde PostgreSQL si DATABASE_URL está disponible."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise EnvironmentError("DATABASE_URL no configurada")

    from db.queries import obtener_todas_tarifas

    return _prepare_dataframe(obtener_todas_tarifas())


def load_from_processed_csvs() -> tuple[pd.DataFrame, list[dict]]:
    """Concatena todos los CSV válidos de data/processed."""
    if not PROCESSED_DIR.exists():
        raise FileNotFoundError(f"No existe {PROCESSED_DIR}")

    frames: list[pd.DataFrame] = []
    archivos: list[dict] = []

    for path in sorted(PROCESSED_DIR.glob("tarifas_*.csv")):
        if path.name.lower() in IGNORED_FILENAMES:
            continue

        info = {
            "path": path,
            "name": path.name,
            "rows": 0,
            "valid": False,
            "reason": "",
        }

        if path.stat().st_size == 0:
            info["reason"] = "CSV vacío"
            archivos.append(info)
            continue

        try:
            df = pd.read_csv(path, sep=";", dtype=str, encoding="utf-8-sig")
        except Exception as exc:
            info["reason"] = f"Error leyendo CSV: {exc}"
            archivos.append(info)
            continue

        if df.empty:
            info["reason"] = "CSV sin filas"
            archivos.append(info)
            continue

        try:
            df = _prepare_dataframe(df)
        except Exception as exc:
            info["reason"] = f"CSV inválido: {exc}"
            archivos.append(info)
            continue

        if df.empty:
            info["reason"] = "Sin filas válidas tras normalización visual"
            archivos.append(info)
            continue

        info["rows"] = len(df)
        info["valid"] = True
        frames.append(df)
        archivos.append(info)

    if not frames:
        return pd.DataFrame(columns=STANDARD_COLUMNS + ["diff_cu"]), archivos

    return pd.concat(frames, ignore_index=True), archivos


def build_source_status(df: pd.DataFrame, archivos: list[dict] | None = None) -> pd.DataFrame:
    """Construye el estado de fuentes esperado para la UI."""
    archivos = archivos or []
    archivo_por_slug = {
        _slug(Path(item["name"]).stem.replace("tarifas_", "")): item
        for item in archivos
    }

    filas = []
    for source in EXPECTED_SOURCES:
        slug = SOURCE_TO_SLUG[source]
        df_source = df[df["Comercializador"].astype(str).str.upper() == source.upper()]
        filas_count = int(len(df_source))

        if filas_count > 0:
            estado = "Activa"
            obs = f"{filas_count} fila(s) cargadas correctamente."
        elif source == "VATIA":
            item = archivo_por_slug.get(slug)
            if item and not item["valid"]:
                estado = "Sin datos"
                obs = "CSV detectado pero sin filas válidas."
            else:
                estado = "No disponible"
                obs = "Backend externo de autenticación no disponible. Fuente integrada, sin datos cargados en esta ejecución."
        else:
            item = archivo_por_slug.get(slug)
            if item and not item["valid"]:
                estado = "Sin datos"
                obs = item["reason"] or "CSV sin datos válidos."
            elif item and item["valid"] and filas_count == 0:
                estado = "Sin datos"
                obs = "CSV presente, pero no produjo filas visibles."
            else:
                estado = "Sin datos"
                obs = "No se encontró un CSV válido en data/processed."

        filas.append(
            {
                "Comercializador": source,
                "Estado": estado,
                "Filas": filas_count,
                "Observación": obs,
            }
        )

    return pd.DataFrame(filas)

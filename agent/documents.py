"""
Construcción de documentos de texto para el RAG.

A partir del DataFrame de tarifas se generan tres tipos de documentos en
lenguaje natural (español), pensados tanto para la indexación semántica en
ChromaDB como para servir de contexto al LLM:

    1. fila      — una tarifa concreta (comercializador × ciclo × nivel).
    2. resumen   — agregado de mercado por (ciclo × nivel): mín/máx/promedio
                   y ranking de CU. Clave para responder preguntas numéricas.
    3. evolucion — serie histórica de un comercializador en un nivel.

Cada documento expone ``id``, ``texto`` y ``metadata`` (filtrable en ChromaDB).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from agent.data_source import COMPONENTES

_NOMBRE_COMPONENTE = {
    "g": "Generación (G)",
    "t": "Transmisión (T)",
    "d": "Distribución (D)",
    "cv": "Comercialización variable (Cv)",
    "pr": "Pérdidas reconocidas (PR)",
    "r": "Restricciones (R)",
    "cu": "Costo Unitario total (CU)",
}


@dataclass
class Documento:
    id: str
    texto: str
    metadata: dict = field(default_factory=dict)


def _fmt(x) -> str:
    """Formatea un número a string legible; '—' si es nulo."""
    if x is None or pd.isna(x):
        return "—"
    return f"{float(x):,.4f}".rstrip("0").rstrip(".")


def _ciclo_legible(ciclo: str) -> str:
    """'202601' -> '2026-01'."""
    c = str(ciclo)
    return f"{c[:4]}-{c[4:]}" if len(c) == 6 else c


def _doc_fila(row: pd.Series) -> Documento:
    comp = str(row["comercializador"])
    ciclo = str(row["ciclo"])
    nivel = int(row["nivel_tension"])
    partes = ", ".join(
        f"{_NOMBRE_COMPONENTE[c]} = {_fmt(row[c])}"
        for c in COMPONENTES
        if c != "cu"
    )
    texto = (
        f"Tarifa de {comp} en el ciclo {_ciclo_legible(ciclo)} ({ciclo}) "
        f"para el Nivel de Tensión {nivel}. "
        f"Costo Unitario (CU) = {_fmt(row['cu'])} $/kWh. "
        f"Desglose de componentes en $/kWh: {partes}. "
        f"Operador de red: {row.get('operador_red') or comp}. "
        f"Dueño de red: {row.get('dueno_red') or 'N/D'}."
    )
    return Documento(
        id=f"fila::{comp}::{ciclo}::{nivel}",
        texto=texto,
        metadata={
            "tipo": "fila",
            "comercializador": comp,
            "ciclo": ciclo,
            "nivel_tension": nivel,
            "cu": float(row["cu"]) if pd.notna(row["cu"]) else None,
        },
    )


def _doc_resumen(ciclo: str, nivel: int, grupo: pd.DataFrame) -> Documento:
    g = grupo.dropna(subset=["cu"]).sort_values("cu")
    if g.empty:
        ranking = "sin datos de CU"
        cu_min = cu_max = cu_avg = comp_min = comp_max = None
    else:
        fila_min = g.iloc[0]
        fila_max = g.iloc[-1]
        cu_min, comp_min = float(fila_min["cu"]), str(fila_min["comercializador"])
        cu_max, comp_max = float(fila_max["cu"]), str(fila_max["comercializador"])
        cu_avg = float(g["cu"].mean())
        ranking = "; ".join(
            f"{i}º {r['comercializador']} = {_fmt(r['cu'])}"
            for i, (_, r) in enumerate(g.iterrows(), start=1)
        )
    texto = (
        f"Resumen comparativo del mercado para el ciclo {_ciclo_legible(ciclo)} "
        f"({ciclo}) en el Nivel de Tensión {nivel}. "
        f"Participan {grupo['comercializador'].nunique()} comercializadores. "
        f"CU más bajo (más competitivo): {_fmt(cu_min)} $/kWh ({comp_min}). "
        f"CU más alto: {_fmt(cu_max)} $/kWh ({comp_max}). "
        f"CU promedio del mercado: {_fmt(cu_avg)} $/kWh. "
        f"Ranking de menor a mayor CU: {ranking}."
    )
    return Documento(
        id=f"resumen::{ciclo}::{nivel}",
        texto=texto,
        metadata={
            "tipo": "resumen",
            "ciclo": ciclo,
            "nivel_tension": nivel,
            "cu_min": cu_min,
            "cu_max": cu_max,
            "cu_promedio": cu_avg,
            "comercializador_min": comp_min,
            "comercializador_max": comp_max,
        },
    )


def _doc_evolucion(comp: str, nivel: int, serie: pd.DataFrame) -> Documento:
    s = serie.sort_values("ciclo")
    ciclos = s["ciclo"].tolist()
    # Limitar a los 12 ciclos más recientes para mantener el documento compacto.
    s_rec = s.tail(12)
    puntos = "; ".join(
        f"{_ciclo_legible(r['ciclo'])}: CU={_fmt(r['cu'])}, G={_fmt(r['g'])}, "
        f"T={_fmt(r['t'])}, D={_fmt(r['d'])}"
        for _, r in s_rec.iterrows()
    )
    texto = (
        f"Evolución histórica de {comp} en el Nivel de Tensión {nivel}. "
        f"Ciclos disponibles: {len(ciclos)} "
        f"(de {_ciclo_legible(ciclos[0])} a {_ciclo_legible(ciclos[-1])}). "
        f"Serie reciente (CU y componentes en $/kWh): {puntos}."
    )
    return Documento(
        id=f"evolucion::{comp}::{nivel}",
        texto=texto,
        metadata={
            "tipo": "evolucion",
            "comercializador": comp,
            "nivel_tension": nivel,
            "n_ciclos": len(ciclos),
        },
    )


def construir_documentos(df: pd.DataFrame) -> list[Documento]:
    """
    Genera todos los documentos (fila + resumen + evolución) del dataset.

    Args:
        df: DataFrame de tarifas en el esquema canónico (ver data_source).

    Returns:
        Lista de :class:`Documento` lista para indexar o usar como contexto.
    """
    if df.empty:
        return []

    docs: list[Documento] = []

    # 1. Documentos por fila.
    for _, row in df.iterrows():
        docs.append(_doc_fila(row))

    # 2. Resúmenes de mercado por (ciclo, nivel).
    for (ciclo, nivel), grupo in df.groupby(["ciclo", "nivel_tension"], dropna=True):
        docs.append(_doc_resumen(str(ciclo), int(nivel), grupo))

    # 3. Evolución por (comercializador, nivel) cuando hay más de un ciclo.
    for (comp, nivel), serie in df.groupby(["comercializador", "nivel_tension"], dropna=True):
        if serie["ciclo"].nunique() > 1:
            docs.append(_doc_evolucion(str(comp), int(nivel), serie))

    return docs

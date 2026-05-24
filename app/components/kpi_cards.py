"""Tarjetas KPI reutilizables para el dashboard VATIA."""

from __future__ import annotations

import streamlit as st


def kpi_card(
    label: str,
    value: float | str | None,
    unit: str = "$/kWh",
    variant: str = "default",
    delta: float | None = None,
) -> None:
    """
    Renderiza una tarjeta KPI con el estilo corporativo VATIA.

    Args:
        label:   Etiqueta descriptiva (ej. "CU Nivel 1").
        value:   Valor numérico a mostrar.
        unit:    Unidad de medida (default "$/kWh").
        variant: "default" | "success" | "danger" | "info" | "warning"
        delta:   Variación vs. período anterior (opcional, muestra flecha).
    """
    if value is None or (isinstance(value, float) and str(value) == "nan"):
        val_str = "—"
    elif isinstance(value, float):
        val_str = f"{value:,.4f}"
    else:
        val_str = str(value)

    delta_html = ""
    if delta is not None:
        arrow = "↑" if delta > 0 else "↓" if delta < 0 else "→"
        color = "#EF4444" if delta > 0 else "#22C55E" if delta < 0 else "#6B7280"
        delta_html = (
            f'<div style="font-size:0.72rem; color:{color}; margin-top:4px; font-weight:600;">'
            f"{arrow} {abs(delta):,.4f} vs. mes anterior</div>"
        )

    st.markdown(
        f"""
        <div class="kpi-card {variant}">
            <div class="kpi-label">{label}</div>
            <div class="kpi-value">{val_str}</div>
            <div class="kpi-unit">{unit}</div>
            {delta_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def kpi_row_niveles(
    df_ciclo,
    col_valor: str = "cu",
    comercializador: str | None = None,
) -> None:
    """
    Renderiza 4 KPIs (uno por nivel de tensión) en columnas.

    Args:
        df_ciclo:         DataFrame filtrado por el ciclo seleccionado.
        col_valor:        Columna de valor a mostrar (default "cu").
        comercializador:  Si se especifica, filtra por ese comercializador.
    """
    import pandas as pd

    df = df_ciclo.copy()
    # Normalizar nombres de columna a minúsculas
    df.columns = [c.lower() for c in df.columns]

    if comercializador:
        df = df[df["comercializador"].str.upper() == comercializador.upper()]

    cols = st.columns(4)
    for i, nivel in enumerate([1, 2, 3, 4]):
        df_nv = df[df["nivel_tension"] == nivel]
        valor = df_nv[col_valor].values[0] if not df_nv.empty else None
        with cols[i]:
            kpi_card(
                label=f"CU · Nivel {nivel}",
                value=float(valor) if valor is not None else None,
                variant="default",
            )

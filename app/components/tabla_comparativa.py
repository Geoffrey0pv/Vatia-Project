"""Tabla comparativa estilizada para el dashboard."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.styles.theme import VATIA


def tabla_comparativa(
    df: pd.DataFrame,
    ciclo: str,
    nivel: int,
    comercializador_propio: str = "CENS",
) -> None:
    """
    Renderiza una tabla comparativa de todos los competidores con highlight
    del comercializador propio, el más barato y el más caro.

    Args:
        df:                     DataFrame completo de tarifas.
        ciclo:                  Ciclo seleccionado.
        nivel:                  Nivel de tensión seleccionado.
        comercializador_propio: Nombre del comercializador propio (highlight diferente).
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    df_f = df[(df["ciclo"] == ciclo) & (df["nivel_tension"] == nivel)].copy()

    if df_f.empty:
        st.info("Sin datos para el ciclo y nivel seleccionados.")
        return

    cols_show = ["comercializador", "g", "t", "d", "cv", "pr", "r", "cu"]
    cols_exist = [c for c in cols_show if c in df_f.columns]
    df_show = df_f[cols_exist].copy()

    # Ordenar por CU ascendente
    if "cu" in df_show.columns:
        df_show = df_show.sort_values("cu")

    # Renombrar columnas para display
    df_show.columns = [c.upper() if c != "comercializador" else "Comercializador"
                       for c in df_show.columns]

    cu_min = df_show["CU"].min() if "CU" in df_show.columns else None
    cu_max = df_show["CU"].max() if "CU" in df_show.columns else None

    def highlight_row(row):
        nombre = row.get("Comercializador", "")
        if nombre.upper() == comercializador_propio.upper():
            return [f"background-color: {VATIA['dark']}; color: {VATIA['lime']}; font-weight:700"] * len(row)
        if "CU" in row and row["CU"] == cu_min:
            return [f"background-color: #F0FDF4; color: #15803D; font-weight:600"] * len(row)
        if "CU" in row and row["CU"] == cu_max:
            return [f"background-color: #FFF1F2; color: #B91C1C; font-weight:600"] * len(row)
        return [""] * len(row)

    numeric_cols = [c for c in df_show.columns if c != "Comercializador"]
    fmt = {c: "{:.4f}" for c in numeric_cols}

    styled = (
        df_show.reset_index(drop=True)
        .style.apply(highlight_row, axis=1)
        .format(fmt, na_rep="—")
        .set_properties(**{"font-size": "0.83rem", "text-align": "right"})
        .set_properties(subset=["Comercializador"], **{"text-align": "left"})
    )

    st.dataframe(styled, use_container_width=True, hide_index=True)

    # Leyenda
    st.markdown(
        f"""
        <div style="font-size:0.72rem; color:{VATIA['text_muted']}; margin-top:6px; display:flex; gap:16px;">
            <span style="color:#15803D;">● Más barato del mercado</span>
            <span style="color:{VATIA['lime']}; font-weight:700;">● {comercializador_propio} (propio)</span>
            <span style="color:#B91C1C;">● Más caro del mercado</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

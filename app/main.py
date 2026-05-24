"""
VATIA — Plataforma de Inteligencia Competitiva Tarifaria
Dashboard principal · Streamlit

Ejecutar:
    streamlit run app/main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Asegurar que el directorio raíz del proyecto esté en sys.path
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── Configuración de página ──────────────────────────────────────────────────
st.set_page_config(
    page_title="VATIA — Inteligencia Competitiva",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Imports internos (después de ajustar sys.path) ───────────────────────────
from app.styles.theme import MAIN_CSS, VATIA
from app.components.kpi_cards import kpi_card
from app.components.charts import (
    chart_evolucion_cu,
    chart_componentes,
    chart_heatmap,
)
from app.components.tabla_comparativa import tabla_comparativa

# Inyectar CSS global
st.markdown(MAIN_CSS, unsafe_allow_html=True)


# ── Carga de datos ────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def cargar_datos() -> pd.DataFrame:
    """
    Carga tarifas desde PostgreSQL.
    Fallback automático a CSV local si la DB no está disponible.
    """
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        try:
            from db.queries import obtener_todas_tarifas
            return obtener_todas_tarifas()
        except Exception as e:
            st.toast(f"DB no disponible ({e.__class__.__name__}) — usando CSV local.", icon="⚠️")

    # Fallback: CSV local
    candidatos = [
        _ROOT / "tarifas_cens_limpio.csv",
        _ROOT / "data" / "processed" / "tarifas_cens.csv",
        _ROOT / "data" / "processed" / "tarifas_cens_limpio.csv",
    ]
    for p in candidatos:
        if p.exists():
            df = pd.read_csv(p, sep=";", decimal=",", encoding="utf-8-sig")
            # Normalizar nombres de columna
            df.columns = [c.strip() for c in df.columns]
            return df

    return pd.DataFrame()


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    # Logo VATIA
    st.markdown(
        """
        <div class="sidebar-logo">
            <div class="logo-text">v<span class="logo-accent">A</span>tia</div>
            <div class="logo-sub">Inteligencia Competitiva</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### Filtros")

    df_all = cargar_datos()

    if df_all.empty:
        st.error("No hay datos disponibles. Ejecuta el pipeline ETL primero.")
        st.stop()

    # Normalizar columnas
    col_map = {c: c for c in df_all.columns}
    ciclo_col = next((c for c in df_all.columns if c.lower() == "ciclo"), "Ciclo")
    nivel_col = next((c for c in df_all.columns if c.lower() == "nivel_tension"), "Nivel_Tension")
    comp_col  = next((c for c in df_all.columns if c.lower() == "comercializador"), "Comercializador")

    ciclos = sorted(df_all[ciclo_col].dropna().unique(), reverse=True)
    ciclo_sel = st.selectbox("📅 Ciclo", ciclos, index=0)

    niveles_disponibles = sorted(df_all[nivel_col].dropna().unique().astype(int))
    nivel_sel = st.radio(
        "⚡ Nivel de Tensión",
        niveles_disponibles,
        format_func=lambda x: f"Nivel {x}",
        horizontal=False,
    )

    st.markdown("---")

    # Info de la fuente de datos
    n_comp = df_all[comp_col].nunique()
    n_ciclos = df_all[ciclo_col].nunique()
    st.markdown(
        f"""
        <div style="font-size:0.72rem; color:#D8FFB0; line-height:1.7;">
            <div>📊 <strong style="color:white;">{n_comp}</strong> competidores</div>
            <div>📅 <strong style="color:white;">{n_ciclos}</strong> ciclos históricos</div>
            <div>📝 <strong style="color:white;">{len(df_all)}</strong> registros totales</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")
    if st.button("🔄 Recargar datos", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ── Datos filtrados ───────────────────────────────────────────────────────────
df_ciclo = df_all[df_all[ciclo_col] == ciclo_sel].copy()
df_nivel = df_ciclo[df_ciclo[nivel_col].astype(int) == int(nivel_sel)].copy()

# Normalizar nombres para los componentes
df_all_norm    = df_all.copy()
df_ciclo_norm  = df_ciclo.copy()
df_nivel_norm  = df_nivel.copy()

for _df in [df_all_norm, df_ciclo_norm, df_nivel_norm]:
    _df.columns = [c.lower() for c in _df.columns]


# ── Header ────────────────────────────────────────────────────────────────────
fecha_display = ciclo_sel[:4] + "-" + ciclo_sel[4:] if len(ciclo_sel) == 6 else ciclo_sel
st.markdown(
    f"""
    <div class="vatia-header">
        <div class="vatia-header-left">
            <h1>⚡ v<span class="vatia-accent">A</span>tia
                — Plataforma de Inteligencia Competitiva Tarifaria</h1>
            <p>Monitoreo automatizado de tarifas de energía · Ciclo
                <strong style="color:white;">{fecha_display}</strong>
                · Nivel de Tensión <strong style="color:white;">{nivel_sel}</strong></p>
        </div>
        <div class="vatia-badge">LIVE</div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ── KPIs del ciclo seleccionado ───────────────────────────────────────────────
st.markdown('<p class="section-title">📌 Componentes CU — Ciclo actual · NT {}</p>'.format(nivel_sel),
            unsafe_allow_html=True)

_componentes = ["g", "t", "d", "cv", "pr", "r", "cu"]
_labels = {
    "g": "Generación (G)", "t": "Transmisión (T)", "d": "Distribución (D)",
    "cv": "Comerc. Variable (Cv)", "pr": "Pérdidas (PR)",
    "r": "Restricciones (R)", "cu": "Costo Unitario (CU)",
}
_variantes = {
    "g": "default", "t": "default", "d": "default",
    "cv": "default", "pr": "default", "r": "default", "cu": "info",
}

if not df_nivel_norm.empty:
    # Mostrar solo la primera fila de datos (primer comercializador disponible)
    # para el ciclo y nivel seleccionados
    primera_fila = df_nivel_norm.iloc[0]

    # Dividir en dos filas de KPIs
    row1_cols = st.columns(4)
    row2_cols = st.columns(4)
    comp_list = _componentes  # 7 componentes → 4 + 3

    for idx, comp in enumerate(comp_list):
        valor = primera_fila.get(comp)
        col_container = row1_cols[idx] if idx < 4 else row2_cols[idx - 4]
        with col_container:
            kpi_card(
                label=_labels.get(comp, comp.upper()),
                value=float(valor) if pd.notna(valor) else None,
                variant=_variantes.get(comp, "default"),
            )
else:
    st.warning(f"Sin datos para ciclo {ciclo_sel} · Nivel {nivel_sel}")


# ── Tabla comparativa ─────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(
    f'<p class="section-title">📋 Comparativo de tarifas — Ciclo {fecha_display} · NT {nivel_sel}</p>',
    unsafe_allow_html=True,
)

_comercializador_propio = df_all_norm[comp_col.lower()].iloc[0].upper() \
    if not df_all_norm.empty else "CENS"

tabla_comparativa(
    df=df_all_norm,
    ciclo=ciclo_sel,
    nivel=int(nivel_sel),
    comercializador_propio=_comercializador_propio,
)


# ── Gráficos ──────────────────────────────────────────────────────────────────
st.markdown("---")
col_left, col_right = st.columns([3, 2], gap="large")

with col_left:
    st.markdown(
        f'<p class="section-title">📈 Evolución histórica del CU — NT {nivel_sel}</p>',
        unsafe_allow_html=True,
    )
    fig_evol = chart_evolucion_cu(df_all_norm, nivel=int(nivel_sel))
    st.plotly_chart(fig_evol, use_container_width=True, config={"displayModeBar": False})

with col_right:
    st.markdown(
        f'<p class="section-title">📊 Desglose de componentes — {fecha_display} · NT {nivel_sel}</p>',
        unsafe_allow_html=True,
    )
    fig_comp = chart_componentes(df_all_norm, ciclo=ciclo_sel, nivel=int(nivel_sel))
    st.plotly_chart(fig_comp, use_container_width=True, config={"displayModeBar": False})


# ── Heatmap ───────────────────────────────────────────────────────────────────
if df_all_norm["comercializador"].nunique() > 1:
    st.markdown("---")
    st.markdown(
        f'<p class="section-title">🌡️ Mapa de calor CU — {fecha_display} (todos los niveles)</p>',
        unsafe_allow_html=True,
    )
    fig_heat = chart_heatmap(df_all_norm, ciclo=ciclo_sel)
    st.plotly_chart(fig_heat, use_container_width=True, config={"displayModeBar": False})


# ── Tabla completa (expandible) ───────────────────────────────────────────────
with st.expander("🗄️  Ver tabla completa de datos"):
    st.dataframe(
        df_all_norm.sort_values(["ciclo", "comercializador", "nivel_tension"], na_position="last")
        .reset_index(drop=True),
        use_container_width=True,
        hide_index=True,
    )

    # Botón de descarga
    csv_bytes = df_all_norm.to_csv(sep=";", decimal=",", index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        label="⬇️  Descargar CSV",
        data=csv_bytes,
        file_name=f"vatia_tarifas_{ciclo_sel}.csv",
        mime="text/csv",
    )


# ── Footer / status bar ───────────────────────────────────────────────────────
fuente = "PostgreSQL" if os.environ.get("DATABASE_URL") else "CSV local"
st.markdown(
    f"""
    <div class="status-bar">
        <span>⚡ Fuente: <strong>{fuente}</strong></span>
        <span>📊 Registros cargados: <strong>{len(df_all)}</strong></span>
        <span>🏢 Competidores: <strong>{df_all_norm['comercializador'].nunique()}</strong> de 10</span>
        <span>📅 Último ciclo: <strong>{df_all[ciclo_col].max()}</strong></span>
    </div>
    """,
    unsafe_allow_html=True,
)

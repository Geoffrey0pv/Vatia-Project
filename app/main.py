"""
VATIA - Plataforma de Inteligencia Competitiva Tarifaria
Dashboard principal · Streamlit

Ejecutar:
    streamlit run app/main.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv()

if "sidebar_visible" not in st.session_state:
    st.session_state.sidebar_visible = True


def hide_sidebar() -> None:
    st.session_state.sidebar_visible = False


def show_sidebar() -> None:
    st.session_state.sidebar_visible = True


st.set_page_config(
    page_title="VATIA - Inteligencia Competitiva",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

from app.components.charts import chart_componentes, chart_evolucion_cu, chart_heatmap
from app.components.kpi_cards import kpi_card
from app.components.tabla_comparativa import tabla_comparativa
from app.data_access import (
    EXPECTED_SOURCES,
    STANDARD_COLUMNS,
    build_source_status,
    load_from_database,
    load_from_processed_csvs,
)
from app.styles.theme import MAIN_CSS

st.markdown(MAIN_CSS, unsafe_allow_html=True)


@st.cache_data(ttl=300, show_spinner=False)
def cargar_datos() -> tuple[pd.DataFrame, pd.DataFrame, str, list[dict]]:
    """Carga tarifas desde PostgreSQL o, si falla, desde CSVs procesados."""
    if os.environ.get("DATABASE_URL"):
        try:
            df = load_from_database()
            status_df = build_source_status(df, [])
            return df, status_df, "PostgreSQL", []
        except Exception as exc:
            st.toast(
                f"DB no disponible ({exc.__class__.__name__}) - usando CSVs procesados.",
                icon="⚠️",
            )

    df, archivos = load_from_processed_csvs()
    status_df = build_source_status(df, archivos)
    return df, status_df, "CSV consolidado", archivos


def _opciones(df: pd.DataFrame, columna: str) -> list[str]:
    return sorted(
        [str(v).strip() for v in df[columna].dropna().astype(str).unique() if str(v).strip()]
    )


def _filtrar_por_lista(df: pd.DataFrame, columna: str, seleccion: list[str]) -> pd.DataFrame:
    if not seleccion:
        return df
    return df[df[columna].astype(str).isin(seleccion)]


def _variant_for_diff(valor: float) -> str:
    if valor > 0.5:
        return "danger"
    if valor > 0:
        return "warning"
    return "success"


def _init_multiselect_state(key: str, options: list[str]) -> None:
    if key not in st.session_state:
        st.session_state[key] = list(options)
        return

    current = st.session_state.get(key, [])
    if not isinstance(current, list):
        st.session_state[key] = list(options)
        return

    valid = [value for value in current if value in options]
    if not valid and options:
        valid = list(options)

    st.session_state[key] = valid


def _init_select_state(key: str, options: list, default):
    if key not in st.session_state:
        st.session_state[key] = default if default in options else (options[0] if options else default)
        return

    current = st.session_state.get(key, default)
    if current not in options:
        st.session_state[key] = default if default in options else (options[0] if options else default)


def _render_sidebar(
    comercializadores: list[str],
    operadores: list[str],
    ciclo_opts: list[str],
    nivel_opts: list,
    tipos: list[str],
    duenos: list[str],
    status_fuentes: pd.DataFrame,
    archivos_cargados: list[dict],
    total_filas: int,
) -> None:
    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-logo">
                <div class="logo-text">v<span class="logo-accent">A</span>tia</div>
                <div class="logo-sub">Inteligencia Competitiva</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.session_state.sidebar_visible:
            st.markdown("### Filtros")
            st.multiselect(
                "Comercializador",
                comercializadores,
                key="selected_comercializadores",
            )
            st.multiselect(
                "Operador / Red",
                operadores,
                key="selected_operadores",
            )
            st.selectbox(
                "Ciclo",
                ciclo_opts,
                key="selected_ciclos",
            )
            st.selectbox(
                "Nivel de Tension",
                nivel_opts,
                key="selected_niveles",
                format_func=lambda x: "Todos" if x == "Todos" else f"Nivel {x}",
            )
            st.multiselect(
                "Tipo de Red",
                tipos,
                key="selected_tipo_red",
            )
            st.multiselect(
                "Dueno de Red",
                duenos,
                key="selected_dueno_red",
            )

            st.markdown("---")
            st.markdown(
                f"""
                <div style="font-size:0.72rem; color:#D8FFB0; line-height:1.7;">
                    <div>📊 <strong style="color:white;">{status_fuentes[status_fuentes['Estado'] == 'Activa'].shape[0]}</strong> fuentes activas</div>
                    <div>📁 <strong style="color:white;">{len(archivos_cargados)}</strong> CSV(s) inspeccionados</div>
                    <div>📝 <strong style="color:white;">{total_filas:,}</strong> registros consolidados</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            st.markdown("---")
            if st.button("🔄 Recargar datos", use_container_width=True):
                st.cache_data.clear()
                st.rerun()
            st.button(
                "Ocultar SideBar",
                key="hide_sidebar_btn",
                use_container_width=True,
                on_click=hide_sidebar,
            )
        else:
            st.markdown("### SideBar oculta")
            st.caption("Los filtros aplicados se conservan en la sesión actual.")
            st.markdown("---")
            st.markdown(
                f"""
                <div style="font-size:0.72rem; color:#D8FFB0; line-height:1.7;">
                    <div>📊 <strong style="color:white;">{status_fuentes[status_fuentes['Estado'] == 'Activa'].shape[0]}</strong> fuentes activas</div>
                    <div>📝 <strong style="color:white;">{total_filas:,}</strong> registros consolidados</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


df_all, status_fuentes, fuente_datos, archivos_cargados = cargar_datos()

if df_all.empty:
    if not st.session_state.sidebar_visible:
        st.button("☰ Mostrar SideBar", key="show_sidebar_btn_empty", on_click=show_sidebar)
    st.error("No hay datos disponibles. Ejecuta el pipeline ETL o verifica data/processed.")
    st.stop()

comercializadores = _opciones(df_all, "Comercializador")
_init_multiselect_state("selected_comercializadores", comercializadores)

df_pre_operadores = _filtrar_por_lista(
    df_all, "Comercializador", st.session_state.get("selected_comercializadores", comercializadores)
)
operadores = _opciones(df_pre_operadores, "Operador_Red")
_init_multiselect_state("selected_operadores", operadores)

df_pre_ciclos = _filtrar_por_lista(
    df_pre_operadores, "Operador_Red", st.session_state.get("selected_operadores", operadores)
)
ciclo_opts = ["Todos"] + sorted(df_pre_ciclos["Ciclo"].dropna().astype(str).unique(), reverse=True)
_init_select_state("selected_ciclos", ciclo_opts, "Todos")
if st.session_state.get("selected_ciclos") != "Todos":
    df_pre_ciclos = df_pre_ciclos[df_pre_ciclos["Ciclo"].astype(str) == st.session_state["selected_ciclos"]]

nivel_opts = ["Todos"] + sorted(df_pre_ciclos["Nivel_Tension"].dropna().astype(int).unique().tolist())
_init_select_state("selected_niveles", nivel_opts, "Todos")
if st.session_state.get("selected_niveles") != "Todos":
    df_pre_ciclos = df_pre_ciclos[
        df_pre_ciclos["Nivel_Tension"].astype(int) == int(st.session_state["selected_niveles"])
    ]

tipos = _opciones(df_pre_ciclos, "Tipo_Red")
_init_multiselect_state("selected_tipo_red", tipos)

df_pre_dueno = _filtrar_por_lista(
    df_pre_ciclos, "Tipo_Red", st.session_state.get("selected_tipo_red", tipos)
)
duenos = _opciones(df_pre_dueno, "Dueno_Red")
_init_multiselect_state("selected_dueno_red", duenos)

_render_sidebar(
    comercializadores=comercializadores,
    operadores=operadores,
    ciclo_opts=ciclo_opts,
    nivel_opts=nivel_opts,
    tipos=tipos,
    duenos=duenos,
    status_fuentes=status_fuentes,
    archivos_cargados=archivos_cargados,
    total_filas=len(df_all),
)

selected_comercializadores = st.session_state.get("selected_comercializadores", comercializadores)
df_filtrado = _filtrar_por_lista(df_all, "Comercializador", selected_comercializadores)

selected_operadores = st.session_state.get("selected_operadores", operadores)
df_filtrado = _filtrar_por_lista(df_filtrado, "Operador_Red", selected_operadores)

selected_ciclo = st.session_state.get("selected_ciclos", "Todos")
if selected_ciclo != "Todos":
    df_filtrado = df_filtrado[df_filtrado["Ciclo"].astype(str) == selected_ciclo]

selected_nivel = st.session_state.get("selected_niveles", "Todos")
if selected_nivel != "Todos":
    df_filtrado = df_filtrado[df_filtrado["Nivel_Tension"].astype(int) == int(selected_nivel)]

selected_tipo_red = st.session_state.get("selected_tipo_red", tipos)
df_filtrado = _filtrar_por_lista(df_filtrado, "Tipo_Red", selected_tipo_red)

selected_dueno_red = st.session_state.get("selected_dueno_red", duenos)
df_filtrado = _filtrar_por_lista(df_filtrado, "Dueno_Red", selected_dueno_red)

if not st.session_state.sidebar_visible:
    st.button("☰ Mostrar SideBar", key="show_sidebar_btn", on_click=show_sidebar)
    st.caption("Filtros aplicados conservados · SideBar oculta")

if df_filtrado.empty:
    st.markdown(
        """
        <div class="vatia-header">
            <div class="vatia-header-left">
                <h1>⚡ v<span class="vatia-accent">A</span>tia - Inteligencia Tarifaria Multiempresa</h1>
                <p>No hay datos para los filtros seleccionados. Ajusta la SideBar para continuar.</p>
            </div>
            <div class="vatia-badge">LIVE</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.warning("No hay filas visibles con la combinación actual de filtros.")
    st.stop()

focus_ciclo = (
    selected_ciclo
    if selected_ciclo != "Todos"
    else sorted(df_filtrado["Ciclo"].astype(str).unique(), reverse=True)[0]
)
focus_nivel = (
    int(selected_nivel)
    if selected_nivel != "Todos"
    else int(sorted(df_filtrado["Nivel_Tension"].dropna().astype(int).unique())[0])
)

df_focus = df_filtrado[
    (df_filtrado["Ciclo"].astype(str) == str(focus_ciclo))
    & (df_filtrado["Nivel_Tension"].astype(int) == int(focus_nivel))
].copy()

fecha_display = f"{focus_ciclo[:4]}-{focus_ciclo[4:]}" if len(str(focus_ciclo)) == 6 else str(focus_ciclo)

st.markdown(
    f"""
    <div class="vatia-header">
        <div class="vatia-header-left">
            <h1>⚡ v<span class="vatia-accent">A</span>tia - Inteligencia Tarifaria Multiempresa</h1>
            <p>{len(df_all):,} registros consolidados · {status_fuentes[status_fuentes['Estado'] == 'Activa'].shape[0]} comercializadores activos · vista actual {fecha_display} · NT {focus_nivel}</p>
        </div>
        <div class="vatia-badge">LIVE</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown('<p class="section-title">📌 KPIs del conjunto filtrado</p>', unsafe_allow_html=True)
row1 = st.columns(4)
row2 = st.columns(4)

kpis = [
    ("Total de filas", float(len(df_filtrado)), "registros", "info"),
    ("Comercializadores activos", float(df_filtrado["Comercializador"].nunique()), "activos", "default"),
    ("Operadores / redes", float(df_filtrado["Operador_Red"].nunique()), "redes", "default"),
    ("Ciclos disponibles", float(df_filtrado["Ciclo"].nunique()), "ciclos", "default"),
    ("CU promedio", float(df_filtrado["CU"].mean()), "$/kWh", "default"),
    ("CU máximo", float(df_filtrado["CU"].max()), "$/kWh", "danger"),
    ("CU mínimo", float(df_filtrado["CU"].min()), "$/kWh", "success"),
    (
        "Filas con diff > 0.5",
        float((df_filtrado["diff_cu"] > 0.5).sum()),
        "filas",
        _variant_for_diff(float((df_filtrado["diff_cu"] > 0.5).sum())),
    ),
]

for idx, (label, value, unit, variant) in enumerate(kpis):
    col = row1[idx] if idx < 4 else row2[idx - 4]
    with col:
        kpi_card(label=label, value=value, unit=unit, variant=variant)

st.markdown("---")
st.markdown('<p class="section-title">🛰️ Estado de fuentes</p>', unsafe_allow_html=True)

status_display = status_fuentes.copy()
estado_rank = {"Activa": 0, "No disponible": 1, "Sin datos": 2}
status_display["_orden"] = status_display["Estado"].map(estado_rank).fillna(9)
status_display = status_display.sort_values(["_orden", "Comercializador"]).drop(columns=["_orden"])


def _highlight_estado(row):
    estado = row["Estado"]
    if estado == "Activa":
        color = "#F0FDF4"
        text = "#15803D"
    elif estado == "No disponible":
        color = "#FFF7ED"
        text = "#C2410C"
    else:
        color = "#F8FAFC"
        text = "#64748B"
    return [f"background-color: {color}; color: {text};"] * len(row)


st.dataframe(
    status_display.style.apply(_highlight_estado, axis=1),
    use_container_width=True,
    hide_index=True,
)

st.markdown("---")
col_left, col_right = st.columns([3, 2], gap="large")

with col_left:
    st.markdown(
        f'<p class="section-title">📈 Evolución de CU por ciclo · NT {focus_nivel}</p>',
        unsafe_allow_html=True,
    )
    conteo_comercializadores = df_filtrado.groupby("Comercializador").size().sort_index()
    st.caption(
        "Registros visibles por comercializador: "
        + " · ".join(f"{comp}: {cant}" for comp, cant in conteo_comercializadores.items())
    )
    fig_evol = chart_evolucion_cu(df_filtrado, nivel=int(focus_nivel))
    st.plotly_chart(fig_evol, use_container_width=True, config={"displayModeBar": False})

with col_right:
    st.markdown(
        f'<p class="section-title">📊 Comparación de CU por comercializador · {fecha_display} · NT {focus_nivel}</p>',
        unsafe_allow_html=True,
    )
    if df_focus.empty:
        st.info("Sin datos para comparar CU en el ciclo y nivel de referencia.")
    else:
        df_cu = df_focus.sort_values("CU")
        fig_cu = px.bar(
            df_cu,
            x="Comercializador",
            y="CU",
            color="Comercializador",
            text="CU",
            labels={"CU": "CU ($/kWh)", "Comercializador": ""},
            height=340,
        )
        fig_cu.update_traces(texttemplate="%{text:.2f}", textposition="outside")
        fig_cu.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=30, b=10),
            showlegend=False,
        )
        st.plotly_chart(fig_cu, use_container_width=True, config={"displayModeBar": False})

st.markdown("---")
col_left, col_right = st.columns([3, 2], gap="large")

with col_left:
    st.markdown(
        f'<p class="section-title">🧱 Desglose de componentes · {fecha_display} · NT {focus_nivel}</p>',
        unsafe_allow_html=True,
    )
    fig_comp = chart_componentes(df_filtrado, ciclo=str(focus_ciclo), nivel=int(focus_nivel))
    st.plotly_chart(fig_comp, use_container_width=True, config={"displayModeBar": False})

with col_right:
    st.markdown(
        f'<p class="section-title">🌡️ Heatmap de CU · {fecha_display}</p>',
        unsafe_allow_html=True,
    )
    fig_heat = chart_heatmap(df_filtrado, ciclo=str(focus_ciclo))
    st.plotly_chart(fig_heat, use_container_width=True, config={"displayModeBar": False})

st.markdown("---")
st.markdown(
    f'<p class="section-title">📋 Tabla comparativa · {fecha_display} · NT {focus_nivel}</p>',
    unsafe_allow_html=True,
)
tabla_comparativa(
    df=df_filtrado,
    ciclo=str(focus_ciclo),
    nivel=int(focus_nivel),
    comercializador_propio="VATIA",
)

with st.expander("🗂️ Ver tabla completa del conjunto filtrado"):
    columnas_tabla = STANDARD_COLUMNS + ["diff_cu"]
    tabla = df_filtrado[columnas_tabla].sort_values(
        ["Ciclo", "Comercializador", "Operador_Red", "Nivel_Tension"],
        ascending=[False, True, True, True],
    )
    st.dataframe(tabla, use_container_width=True, hide_index=True)

    csv_bytes = tabla.to_csv(
        sep=";",
        decimal=",",
        index=False,
        encoding="utf-8-sig",
    ).encode("utf-8-sig")
    st.download_button(
        label="⬇️ Descargar CSV filtrado",
        data=csv_bytes,
        file_name=f"vatia_dashboard_{focus_ciclo}_nt{focus_nivel}.csv",
        mime="text/csv",
    )

max_diff = float(df_all["diff_cu"].max()) if not df_all.empty else 0.0
st.markdown(
    f"""
    <div class="status-bar">
        <span>⚡ Fuente: <strong>{fuente_datos}</strong></span>
        <span>📊 Registros visibles: <strong>{len(df_filtrado):,}</strong></span>
        <span>🏢 Comercializadores activos: <strong>{status_fuentes[status_fuentes['Estado'] == 'Activa'].shape[0]}</strong> de {len(EXPECTED_SOURCES)}</span>
        <span>📅 Último ciclo visible: <strong>{df_filtrado['Ciclo'].astype(str).max()}</strong></span>
        <span>🧮 Máx diff CU global: <strong>{max_diff:.4f}</strong></span>
    </div>
    """,
    unsafe_allow_html=True,
)

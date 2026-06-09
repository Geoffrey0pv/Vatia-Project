"""Gráficos Plotly reutilizables para el dashboard VATIA."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from app.styles.theme import VATIA, COLORES_COMPETIDORES

# ── Config base para todos los charts ───────────────────────────────────────
_LAYOUT_BASE = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, sans-serif", color=VATIA["text_dark"], size=11),
    margin=dict(l=10, r=10, t=36, b=10),
    legend=dict(
        orientation="h",
        yanchor="bottom", y=1.02,
        xanchor="right", x=1,
        bgcolor="rgba(0,0,0,0)",
        font=dict(size=10),
    ),
    xaxis=dict(showgrid=False, zeroline=False),
    yaxis=dict(gridcolor="#F0F4F0", zeroline=False),
    hoverlabel=dict(bgcolor="white", bordercolor="#E2E8F0", font_size=12),
)


def _color_competidor(nombre: str) -> str:
    return COLORES_COMPETIDORES.get(nombre.upper(), VATIA["medium"])


# ── Gráfico 1: Evolución histórica del CU ────────────────────────────────────
def chart_evolucion_cu(
    df: pd.DataFrame,
    nivel: int = 1,
    altura: int = 340,
) -> go.Figure:
    """
    Líneas de evolución del CU por comercializador para un nivel de tensión.

    Args:
        df:     DataFrame con columnas ciclo, comercializador, nivel_tension, cu.
        nivel:  Nivel de tensión a graficar.
        altura: Altura en píxeles.
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    df_f = df[df["nivel_tension"] == nivel].sort_values("ciclo")

    if df_f.empty:
        return go.Figure().update_layout(title="Sin datos")

    fig = px.line(
        df_f,
        x="ciclo",
        y="cu",
        color="comercializador",
        color_discrete_map={c: _color_competidor(c) for c in df_f["comercializador"].unique()},
        markers=True,
        labels={"ciclo": "Ciclo", "cu": "CU ($/kWh)", "comercializador": ""},
        height=altura,
    )
    fig.update_traces(line_width=2.5, marker_size=6)
    fig.update_layout(**_LAYOUT_BASE, title_text="")
    return fig


# ── Gráfico 2: Barras comparativas de componentes ────────────────────────────
def chart_componentes(
    df: pd.DataFrame,
    ciclo: str,
    nivel: int = 1,
    altura: int = 340,
) -> go.Figure:
    """
    Barras apiladas de G, T, D, Cv, PR, R por comercializador.

    Args:
        df:     DataFrame completo con columnas de componentes.
        ciclo:  Ciclo seleccionado.
        nivel:  Nivel de tensión.
        altura: Altura en píxeles.
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    df_f = df[(df["ciclo"] == ciclo) & (df["nivel_tension"] == nivel)]

    if df_f.empty:
        return go.Figure().update_layout(title="Sin datos")

    componentes = ["g", "t", "d", "cv", "pr", "r"]
    nombres_comp = ["G", "T", "D", "Cv", "PR", "R"]
    colores_comp = [
        "#0A1F14", "#1B4332", "#2D6A4F",
        "#52B788", "#AAFF00", "#D8FFB0",
    ]

    fig = go.Figure()
    for comp, nombre, color in zip(componentes, nombres_comp, colores_comp):
        if comp in df_f.columns:
            fig.add_trace(go.Bar(
                name=nombre,
                x=df_f["comercializador"],
                y=df_f[comp],
                marker_color=color,
                hovertemplate=f"<b>{nombre}</b>: %{{y:.4f}} $/kWh<extra></extra>",
            ))

    fig.update_layout(
        **_LAYOUT_BASE,
        barmode="stack",
        title_text="",
        height=altura,
        xaxis_tickangle=-30,
    )
    return fig


# ── Gráfico 3: Heatmap CU por competidor × nivel ─────────────────────────────
def chart_heatmap(
    df: pd.DataFrame,
    ciclo: str,
    altura: int = 280,
) -> go.Figure:
    """
    Mapa de calor: comercializador (eje Y) × nivel de tensión (eje X) → CU.
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    df_f = df[df["ciclo"] == ciclo]

    if df_f.empty:
        return go.Figure().update_layout(title="Sin datos")

    pivot = df_f.pivot_table(
        values="cu", index="comercializador", columns="nivel_tension"
    )

    fig = go.Figure(go.Heatmap(
        z=pivot.values.tolist(),
        x=[f"NT {c}" for c in pivot.columns],
        y=pivot.index.tolist(),
        colorscale=[
            [0.0, "#0A1F14"],
            [0.5, "#2D6A4F"],
            [1.0, "#AAFF00"],
        ],
        text=[[f"{v:.0f}" for v in row] for row in pivot.values],
        texttemplate="%{text}",
        showscale=True,
        hovertemplate="<b>%{y}</b> · %{x}<br>CU: %{z:.4f} $/kWh<extra></extra>",
    ))
    layout = dict(_LAYOUT_BASE)
    layout["height"] = altura
    layout["title_text"] = ""
    layout["yaxis"] = {
        **layout.get("yaxis", {}),
        "autorange": "reversed",
    }
    fig.update_layout(**layout)
    return fig


# ── Gráfico 4: Gauge CU vs. mercado ──────────────────────────────────────────
def chart_gauge_cu(
    cu_propio: float,
    cu_min: float,
    cu_max: float,
    nivel: int = 1,
    altura: int = 220,
) -> go.Figure:
    """Indicador tipo gauge que muestra el CU propio vs. rango del mercado."""
    rango_mid = (cu_min + cu_max) / 2

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=cu_propio,
        number={"suffix": " $/kWh", "font": {"size": 20, "color": VATIA["text_dark"]}},
        delta={
            "reference": cu_min,
            "valueformat": ".4f",
            "increasing": {"color": VATIA["danger"]},
            "decreasing": {"color": VATIA["success"]},
        },
        gauge={
            "axis": {"range": [cu_min * 0.95, cu_max * 1.05], "tickformat": ".0f"},
            "bar":  {"color": VATIA["lime"]},
            "bgcolor": "white",
            "borderwidth": 0,
            "steps": [
                {"range": [cu_min * 0.95, rango_mid], "color": "#F0FAF4"},
                {"range": [rango_mid, cu_max * 1.05],   "color": "#FEF2F2"},
            ],
            "threshold": {
                "line": {"color": VATIA["danger"], "width": 2},
                "thickness": 0.8,
                "value": cu_max,
            },
        },
        title={"text": f"CU vs. Mercado · NT{nivel}", "font": {"size": 12}},
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        height=altura,
        margin=dict(l=20, r=20, t=40, b=10),
    )
    return fig

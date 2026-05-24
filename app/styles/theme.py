"""
Paleta de colores y CSS global de la plataforma VATIA.

Colores extraídos de la identidad corporativa de Vatia (verde oscuro + lima eléctrico).
"""

from __future__ import annotations

# ── Paleta de colores ────────────────────────────────────────────────────────
VATIA: dict[str, str] = {
    # Verdes corporativos
    "dark":       "#0A1F14",   # Verde muy oscuro (fondo sidebar, headers)
    "primary":    "#1B4332",   # Verde bosque (elementos primarios)
    "medium":     "#2D6A4F",   # Verde medio (hover, separadores)
    # Acento eléctrico
    "lime":       "#AAFF00",   # Lima eléctrico (logo, highlights, CTA)
    "lime_soft":  "#D8FFB0",   # Lima suave (labels sobre fondo oscuro)
    # Fondos
    "white":      "#FFFFFF",
    "bg_light":   "#F4F9F4",   # Blanco con toque verde
    "bg_card":    "#FFFFFF",
    # Texto
    "text_dark":  "#0A1F14",
    "text_muted": "#6B7280",
    "text_light": "#FFFFFF",
    # Estados
    "success":    "#22C55E",   # Verde brillante (valores buenos / CU bajo)
    "danger":     "#EF4444",   # Rojo (valores malos / CU alto)
    "warning":    "#F59E0B",   # Ámbar (valores medios)
    "info":       "#3B82F6",   # Azul informativo
    # Bordes
    "border":     "#E2E8F0",
    "border_accent": "#AAFF00",
}

# ── Colores de competidores (para gráficos de series) ────────────────────────
COLORES_COMPETIDORES: dict[str, str] = {
    "CENS":      "#AAFF00",   # Lima — nuestro color de marca
    "AFINIA":    "#3B82F6",   # Azul
    "AIRE":      "#8B5CF6",   # Violeta
    "EPM":       "#F59E0B",   # Ámbar
    "CODENSA":   "#EC4899",   # Rosa
    "EMCALI":    "#06B6D4",   # Cian
    "ESSA":      "#F97316",   # Naranja
    "ENELX":     "#14B8A6",   # Verde azulado
    "BIA":       "#A78BFA",   # Lavanda
    "NEU":       "#FB923C",   # Coral
}

# ── CSS global ───────────────────────────────────────────────────────────────
MAIN_CSS: str = """
<style>
/* ─── Ocultar chrome de Streamlit ──────────────────────────────────────── */
#MainMenu           { visibility: hidden; }
footer              { visibility: hidden; }
header              { visibility: hidden; }
[data-testid="stToolbar"] { display: none; }

/* ─── Sidebar ───────────────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: linear-gradient(175deg, #0A1F14 0%, #1B4332 100%) !important;
    border-right: 1px solid #2D6A4F;
}
[data-testid="stSidebar"] > div:first-child {
    padding-top: 1.5rem;
}
[data-testid="stSidebar"] * {
    color: #FFFFFF !important;
}
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stRadio   label,
[data-testid="stSidebar"] .stSlider  label {
    color: #D8FFB0 !important;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
}
[data-testid="stSidebar"] hr {
    border-color: #2D6A4F !important;
    margin: 1rem 0;
}
[data-testid="stSidebar"] [data-testid="stSelectbox"] > div > div {
    background: rgba(255,255,255,0.08) !important;
    border-color: #2D6A4F !important;
    color: white !important;
}

/* ─── Fondo principal ───────────────────────────────────────────────────── */
.main .block-container {
    padding-top: 1.5rem;
    padding-bottom: 2rem;
    max-width: 1400px;
}

/* ─── Header VATIA ──────────────────────────────────────────────────────── */
.vatia-header {
    background: linear-gradient(135deg, #0A1F14 0%, #1B4332 55%, #2D6A4F 100%);
    padding: 20px 28px;
    border-radius: 14px;
    margin-bottom: 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.vatia-header-left h1 {
    color: #FFFFFF;
    font-size: 1.45rem;
    font-weight: 800;
    margin: 0;
    letter-spacing: -0.02em;
}
.vatia-header-left p {
    color: #D8FFB0;
    margin: 4px 0 0;
    font-size: 0.82rem;
}
.vatia-accent  { color: #AAFF00; font-weight: 900; }
.vatia-badge {
    background: rgba(170,255,0,0.15);
    border: 1px solid #AAFF00;
    color: #AAFF00;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}

/* ─── KPI Cards ─────────────────────────────────────────────────────────── */
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 14px;
    margin-bottom: 24px;
}
.kpi-card {
    background: #FFFFFF;
    border-radius: 12px;
    padding: 18px 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
    border-left: 3px solid #AAFF00;
    transition: box-shadow 0.2s, transform 0.15s;
    min-height: 90px;
}
.kpi-card:hover {
    box-shadow: 0 6px 16px rgba(0,0,0,0.10);
    transform: translateY(-1px);
}
.kpi-label {
    font-size: 0.70rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #6B7280;
    margin-bottom: 6px;
}
.kpi-value {
    font-size: 1.65rem;
    font-weight: 800;
    color: #0A1F14;
    line-height: 1.1;
    font-variant-numeric: tabular-nums;
}
.kpi-unit {
    font-size: 0.72rem;
    color: #6B7280;
    margin-top: 4px;
}
.kpi-card.success { border-left-color: #22C55E; }
.kpi-card.danger  { border-left-color: #EF4444; }
.kpi-card.info    { border-left-color: #3B82F6; }
.kpi-card.warning { border-left-color: #F59E0B; }

/* ─── Section titles ────────────────────────────────────────────────────── */
.section-title {
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #6B7280;
    margin: 0 0 10px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.section-title::after {
    content: "";
    flex: 1;
    height: 1px;
    background: #E2E8F0;
}

/* ─── Gráficos / Cards de contenido ────────────────────────────────────── */
.chart-card {
    background: #FFFFFF;
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    margin-bottom: 16px;
}

/* ─── Tabla comparativa ──────────────────────────────────────────────────── */
.comparativo-table th {
    background: #0A1F14 !important;
    color: #AAFF00 !important;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 10px 14px;
}
.comparativo-table td {
    padding: 9px 14px;
    font-size: 0.85rem;
    border-bottom: 1px solid #F0F4F0;
}
.comparativo-table tr:hover td {
    background: #F4F9F4;
}

/* ─── Status / Métricas footer ───────────────────────────────────────────── */
.status-bar {
    background: #0A1F14;
    color: #D8FFB0;
    padding: 10px 20px;
    border-radius: 10px;
    font-size: 0.78rem;
    display: flex;
    gap: 24px;
    margin-top: 24px;
}
.status-item strong { color: #AAFF00; }

/* ─── Streamlit metric override ──────────────────────────────────────────── */
[data-testid="stMetric"] {
    background: #FFFFFF;
    border-radius: 10px;
    padding: 14px 18px;
    border-left: 3px solid #AAFF00;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
[data-testid="stMetricLabel"] {
    font-size: 0.70rem !important;
    font-weight: 700 !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #6B7280 !important;
}
[data-testid="stMetricValue"] {
    font-size: 1.6rem !important;
    font-weight: 800 !important;
    color: #0A1F14 !important;
}

/* ─── Logo text en sidebar ───────────────────────────────────────────────── */
.sidebar-logo {
    padding: 4px 16px 16px;
    border-bottom: 1px solid #2D6A4F;
    margin-bottom: 16px;
}
.sidebar-logo .logo-text {
    font-size: 1.6rem;
    font-weight: 900;
    color: white;
    letter-spacing: -0.03em;
}
.sidebar-logo .logo-accent { color: #AAFF00; }
.sidebar-logo .logo-sub {
    font-size: 0.68rem;
    color: #D8FFB0;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}
</style>
"""

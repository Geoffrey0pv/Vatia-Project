# VATIA — Plataforma de Inteligencia Competitiva Tarifaria
## Documento de Arquitectura, Diseño y Plan de Ejecución

**Versión:** 1.0  
**Fecha:** Mayo 2026  
**Equipo:** 3 desarrolladores  
**Estado:** Fase 1 completada ✅ — Fase 2 en progreso

---

## Tabla de Contenidos

1. [Contexto y Problema](#1-contexto-y-problema)
2. [Visión del Sistema](#2-visión-del-sistema)
3. [Arquitectura Propuesta](#3-arquitectura-propuesta)
4. [Stack Tecnológico](#4-stack-tecnológico)
5. [Estructura de Carpetas](#5-estructura-de-carpetas)
6. [Esquema de Datos](#6-esquema-de-datos)
7. [Requisitos Funcionales](#7-requisitos-funcionales)
8. [Requisitos No Funcionales](#8-requisitos-no-funcionales)
9. [División de Responsabilidades](#9-división-de-responsabilidades)
10. [Plan de Trabajo por Fases](#10-plan-de-trabajo-por-fases)
11. [Casos de Prueba](#11-casos-de-prueba)
12. [Riesgos y Mitigaciones](#12-riesgos-y-mitigaciones)

---

## 1. Contexto y Problema

### Problema actual
La recolección y análisis manual de tarifas de la competencia toma **~5 días**, generando retrasos que impiden reaccionar a tiempo frente a cambios del mercado. Se estima un impacto del **3% en ventas** por decisiones tardías.

### Competidores a monitorear (10 iniciales)

| # | Operador | Observaciones |
|---|----------|--------------|
| 1 | **CENS** | ✅ ETL funcional (PDF → OCR) |
| 2 | AFINIA | Por implementar |
| 3 | AIRE | Por implementar |
| 4 | EPM | Por implementar |
| 5 | CODENSA | Por implementar |
| 6 | EMCALI | Por implementar |
| 7 | ESSA | Por implementar |
| 8 | ENELX | Por implementar |
| 9 | BIA | Por implementar |
| 10 | NEU | Por implementar |

---

## 2. Visión del Sistema

El sistema consta de **4 componentes** que interactúan entre sí:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    PLATAFORMA VATIA — Vista de Alto Nivel               │
│                                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌─────────────────────────┐   │
│  │  COMPONENTE  │    │  COMPONENTE  │    │      COMPONENTE         │   │
│  │     ETL      │───▶│  DATOS / DB  │───▶│     DASHBOARD           │   │
│  │  (Scrapers)  │    │  (SQLite +   │    │     (Streamlit)         │   │
│  │              │    │   ChromaDB)  │    │                         │   │
│  └──────────────┘    └──────┬───────┘    └────────────┬────────────┘   │
│                             │                          │                │
│                             │            ┌─────────────▼────────────┐  │
│                             │            │      COMPONENTE           │  │
│                             └───────────▶│    AGENTE IA (RAG)        │  │
│                                          │  (LangChain + LLM)        │  │
│                                          └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Flujo de datos de punta a punta

```
Web competidor
     │
     ▼
[Scraper Python]  ←── por competidor, ejecución mensual automatizada
     │
     ▼
[ETL Transform]   ←── normalización al esquema unificado
     │
     ├──▶ [SQLite / PostgreSQL]  ←── datos estructurados para dashboard
     │
     └──▶ [ChromaDB]            ←── vectorización para el agente RAG
               │
               ├──▶ [Dashboard Streamlit]  ←── gráficos y KPIs ejecutivos
               │
               └──▶ [Chat RAG]             ←── consultas en lenguaje natural
```

---

## 3. Arquitectura Propuesta

### 3.1 Componente 1 — ETL / Extracción

Responsable de descargar y normalizar los datos de cada competidor.

```
etl/
 ├── scrapers/           # Un archivo Python por competidor
 │   ├── base.py         # Clase base ScraperBase con interfaz común
 │   ├── cens.py         # ✅ Implementado (PDF + OCR)
 │   ├── afinia.py
 │   ├── aire.py
 │   └── ...
 ├── transform.py        # Normalización al esquema unificado
 ├── load.py             # Escritura a SQLite y ChromaDB
 └── scheduler.py        # APScheduler: ejecución el día 1 de cada mes
```

**Interfaz `ScraperBase`:**
```python
class ScraperBase:
    competidor: str           # Nombre del operador
    
    def obtener_enlaces() -> list[str]  # Detecta los PDFs/Excel del mes
    def descargar(url) -> bytes         # Descarga el archivo
    def extraer(bytes) -> pd.DataFrame  # Extrae tabla de componentes CU
    def ejecutar() -> pd.DataFrame      # Orquesta los 3 pasos anteriores
```

Cada scraper concreto (ej. `CensScraperOCR`) hereda de `ScraperBase` e implementa `extraer()` según el formato de cada competidor (PDF imagen, PDF texto, Excel, HTML).

### 3.2 Componente 2 — Capa de Datos

| Almacén | Tecnología | Contenido |
|---------|-----------|-----------|
| Base de datos relacional | **SQLite** (MVP) → PostgreSQL (producción) | Tabla `tarifas`: todos los registros históricos por competidor/mes/nivel |
| Vector store | **ChromaDB** | Embeddings de los datos de tarifas para búsqueda semántica del agente |
| Caché | Archivos CSV locales | Respaldo de cada extracción mensual en `data/raw/` |

**Tabla principal `tarifas`:**
```sql
CREATE TABLE tarifas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha       TEXT NOT NULL,        -- YYYY-MM-DD
    ciclo       TEXT NOT NULL,        -- AAAAMM
    operador_red TEXT,
    comercializador TEXT NOT NULL,
    nivel_tension INTEGER NOT NULL,   -- 1, 2, 3, 4
    tipo_red    TEXT,
    comb_nt     TEXT,
    dueno_red   TEXT,
    g           REAL,
    t           REAL,
    d           REAL,
    cv          REAL,
    pr          REAL,
    r           REAL,
    cu          REAL,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(ciclo, comercializador, nivel_tension)
);
```

### 3.3 Componente 3 — Dashboard (Streamlit)

Aplicación web con dos pestañas principales:

**Pestaña 1 — Vista Ejecutiva (Dashboard)**
- KPIs: CU mínimo/máximo del mes, diferencia vs. CENS
- Tabla comparativa de todos los competidores por nivel de tensión
- Gráfico de líneas: evolución de CU por competidor (histórico)
- Gráfico de barras: comparativa de componentes G/T/D/Cv/PR/R del mes actual
- Mapa de calor: precios por competidor vs. nivel de tensión

**Paleta de colores propuesta (corporativo VATIA):**
```
Primario:   #1B3A6B  (azul oscuro)
Secundario: #2E86AB  (azul medio)
Acento:     #F5A623  (naranja/dorado)
Fondo:      #F4F7FA  (gris muy claro)
Texto:      #2D2D2D  (gris oscuro)
Éxito:      #27AE60  (verde)
Alerta:     #E74C3C  (rojo)
```

**Pestaña 2 — Agente IA (Chat)**
- Interfaz de chat conversacional integrada en la misma app
- Historial de conversación visible
- Indicador de fuentes (qué datos se consultaron)
- Ejemplos de preguntas sugeridas

### 3.4 Componente 4 — Agente IA (RAG)

**Arquitectura RAG (Retrieval-Augmented Generation):**

```
Pregunta del usuario
       │
       ▼
[Embedding de la pregunta]  ←── mismo modelo que indexó los datos
       │
       ▼
[Búsqueda en ChromaDB]       ←── top-K fragmentos más relevantes
       │
       ▼
[Construcción del contexto]  ←── prompt = sistema + contexto + pregunta
       │
       ▼
[LLM (GPT-4o mini / local)]  ←── genera la respuesta en español
       │
       ▼
[Respuesta al usuario]
```

**Modelo LLM:**
- **Opción A (recomendada para MVP):** OpenAI `gpt-4o-mini` — económico (~$0.15/1M tokens), respuestas en < 3 segundos
- **Opción B (sin costo, local):** `Ollama` con `llama3.1:8b` — requiere GPU o PC potente, sin costo por consulta

**Tipos de consultas que debe responder el agente:**
- "¿Cuál es el CU más bajo para Nivel 2 este mes?"
- "¿Cómo ha variado el componente G de EPM en los últimos 3 meses?"
- "¿Cuánto cobramos de más vs. el promedio del mercado en Nivel 1?"
- "¿Qué competidor tiene el D más alto este ciclo?"
- "Resume las tarifas de CODENSA para 2026"

---

## 4. Stack Tecnológico

### Decisión de plataforma: Streamlit all-in-one

Se elige **Streamlit** como única interfaz de usuario porque:
- Es Python nativo — el mismo lenguaje del ETL y el agente
- Integra dashboard + chat en una sola app sin frontend separado
- Tiempo de desarrollo 3-5× más rápido que React/Next.js
- Fácil de hospedar en Streamlit Cloud (gratuito para MVP)

### Stack completo

| Capa | Tecnología | Versión | Justificación |
|------|-----------|---------|--------------|
| **ETL — HTTP** | `requests` | 2.x | Descarga archivos, scraping web |
| **ETL — HTML** | `beautifulsoup4` | 4.x | Parseo de páginas HTML |
| **ETL — PDF imagen** | `pymupdf` + `easyocr` | 1.23+ / 1.7+ | OCR para PDFs con tablas rasterizadas |
| **ETL — PDF texto** | `pdfplumber` | 0.10+ | Extracción directa cuando hay texto |
| **ETL — Excel** | `openpyxl` | 3.x | Archivos `.xlsx` de algunos competidores |
| **Transformación** | `pandas` | 2.x | Normalización y limpieza de datos |
| **Scheduler** | `APScheduler` | 3.x | Ejecución automática mensual |
| **Base de datos** | `SQLite` → `PostgreSQL` | — | Almacén relacional de tarifas |
| **ORM / queries** | `sqlite3` (MVP) / `sqlalchemy` | — | Acceso a datos desde Python |
| **Vector store** | `chromadb` | 0.5+ | Base de datos de embeddings para RAG |
| **RAG / LLM** | `langchain` + `openai` | 0.3+ | Orquestación del agente conversacional |
| **Embeddings** | `text-embedding-3-small` (OpenAI) | — | Vectorización del contenido tarifario |
| **Dashboard** | `streamlit` | 1.35+ | UI del dashboard y del chat |
| **Visualizaciones** | `plotly` | 5.x | Gráficos interactivos en Streamlit |
| **Testing** | `pytest` + `pytest-cov` | 8.x | Pruebas unitarias e integración |
| **Linting** | `ruff` | 0.4+ | Calidad de código |
| **Control de versiones** | `git` + GitHub | — | Colaboración entre 3 devs |
| **Gestión de secretos** | `.env` + `python-dotenv` | — | API keys (OpenAI, etc.) |

---

## 5. Estructura de Carpetas

```
vatia-plataforma/
│
├── README.md
├── ARQUITECTURA_Y_DISENO.md       ← este documento
├── PIPELINE_TECNICO.md
│
├── .env.example                   ← plantilla de variables de entorno
├── .gitignore
├── requirements.txt               ← dependencias del proyecto
├── pyproject.toml                 ← configuración de ruff + pytest
│
├── data/
│   ├── raw/                       ← PDFs y Excel descargados (ignorado por git)
│   │   ├── cens/
│   │   ├── afinia/
│   │   └── .../
│   ├── processed/                 ← CSVs normalizados por mes
│   │   └── tarifas_AAAAMM.csv
│   └── vatia.db                   ← base de datos SQLite
│
├── etl/                           ── COMPONENTE 1: Extracción y Transformación
│   ├── __init__.py
│   ├── base_scraper.py            ← clase abstracta ScraperBase
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── cens.py                ← ✅ implementado
│   │   ├── afinia.py
│   │   ├── aire.py
│   │   ├── epm.py
│   │   ├── codensa.py
│   │   ├── emcali.py
│   │   ├── essa.py
│   │   ├── enelx.py
│   │   ├── bia.py
│   │   └── neu.py
│   ├── transform.py               ← normalización al esquema unificado
│   ├── load.py                    ← escritura a SQLite + ChromaDB
│   ├── scheduler.py               ← APScheduler (cron mensual)
│   └── pipeline.py                ← orquestador: extrae → transforma → carga
│
├── db/                            ── COMPONENTE 2: Acceso a Datos
│   ├── __init__.py
│   ├── schema.sql                 ← DDL de las tablas
│   ├── connection.py              ← singleton de conexión SQLite
│   └── queries.py                 ← consultas SQL reutilizables
│
├── agent/                         ── COMPONENTE 4: Agente IA RAG
│   ├── __init__.py
│   ├── indexer.py                 ← carga datos → genera embeddings → ChromaDB
│   ├── retriever.py               ← búsqueda semántica en ChromaDB
│   ├── prompts.py                 ← plantillas de sistema y usuario
│   └── chat_agent.py              ← orquestador LangChain
│
├── app/                           ── COMPONENTE 3: Dashboard + Chat UI
│   ├── __init__.py
│   ├── main.py                    ← punto de entrada: streamlit run app/main.py
│   ├── pages/
│   │   ├── 1_Dashboard.py         ← vista ejecutiva con KPIs y gráficos
│   │   └── 2_Agente_IA.py         ← chat conversacional
│   ├── components/
│   │   ├── kpi_cards.py           ← tarjetas de indicadores
│   │   ├── charts.py              ← funciones de Plotly reutilizables
│   │   ├── tabla_comparativa.py   ← tabla de competidores
│   │   └── chat_ui.py             ← componente del chat
│   └── styles/
│       └── theme.py               ← paleta de colores y estilos CSS
│
├── tests/                         ── PRUEBAS
│   ├── __init__.py
│   ├── unit/
│   │   ├── test_scrapers.py
│   │   ├── test_transform.py
│   │   └── test_agent.py
│   ├── integration/
│   │   ├── test_pipeline.py
│   │   └── test_db.py
│   └── fixtures/
│       ├── sample_cens.pdf        ← PDF de muestra para tests
│       └── sample_data.csv        ← datos de referencia esperados
│
└── notebooks/                     ── exploración y diagnóstico
    └── etl_tarifas_cens.ipynb     ← ✅ notebook original de CENS
```

---

## 6. Esquema de Datos

### Esquema unificado de salida (todos los competidores)

Basado en el prototipo de datos del proyecto:

```
Fecha ; Ciclo ; Operador_Red ; Comercializador ; Nivel_Tension ; Tipo_Red ;
Comb_NT ; Dueno_Red ; G ; T ; D ; Cv ; PR ; R ; CU
```

| Campo | Tipo | Ejemplo | Descripción |
|-------|------|---------|-------------|
| `Fecha` | `DATE` | `2026-01-01` | Primer día del ciclo |
| `Ciclo` | `TEXT` | `202601` | AAAAMM |
| `Operador_Red` | `TEXT` | `CENS` | ID del operador de red |
| `Comercializador` | `TEXT` | `CENS` | Nombre del comercializador |
| `Nivel_Tension` | `INT` | `1`, `2`, `3`, `4` | Nivel de tensión regulado |
| `Tipo_Red` | `TEXT` | `AEREA` | Tipo de red |
| `Comb_NT` | `TEXT` | `NIVEL 1-2` | Combinación nivel-tensión |
| `Dueno_Red` | `TEXT` | `100% OPERADOR` | Propietario de la red |
| `G` | `REAL` | `298.9047` | Generación ($/kWh) |
| `T` | `REAL` | `52.9743` | Transmisión ($/kWh) |
| `D` | `REAL` | `317.1201` | Distribución ($/kWh) |
| `Cv` | `REAL` | `132.3776` | Comercialización variable ($/kWh) |
| `PR` | `REAL` | `67.3480` | Pérdidas reconocidas ($/kWh) |
| `R` | `REAL` | `19.3343` | Restricciones ($/kWh) |
| `CU` | `REAL` | `888.0590` | Costo Unitario total ($/kWh) |

### Relación CU = G + T + D + Cv + PR + R

Esta relación es una **regla de validación de integridad** que debe cumplirse en cada fila (tolerancia: ±0.01 $/kWh por redondeos).

---

## 7. Requisitos Funcionales

### RF-01 — Extracción de tarifas
| ID | Requisito |
|----|-----------|
| RF-01.1 | El sistema debe extraer automáticamente las tarifas de los 10 competidores listados |
| RF-01.2 | La extracción debe ejecutarse el primer día hábil de cada mes (datos CREG) |
| RF-01.3 | Debe soportar al menos 3 formatos de fuente: PDF imagen (OCR), PDF texto, Excel |
| RF-01.4 | Si un competidor no tiene datos del mes, el sistema debe registrar el error y continuar |
| RF-01.5 | Los datos descargados en bruto deben guardarse en `data/raw/` como respaldo |

### RF-02 — Transformación y almacenamiento
| ID | Requisito |
|----|-----------|
| RF-02.1 | Todos los datos deben normalizarse al esquema unificado de 15 columnas |
| RF-02.2 | La relación CU = G + T + D + Cv + PR + R debe validarse antes de insertar |
| RF-02.3 | No puede haber duplicados: (Ciclo, Comercializador, Nivel_Tension) debe ser único |
| RF-02.4 | El CSV de salida debe usar separador `;` y decimal `,` (formato Power BI español) |
| RF-02.5 | Los datos deben persistirse en SQLite y en ChromaDB para el agente |

### RF-03 — Dashboard
| ID | Requisito |
|----|-----------|
| RF-03.1 | Mostrar KPIs del mes: CU min, CU max, CU promedio, competidor más barato |
| RF-03.2 | Tabla comparativa de todos los competidores × 4 niveles de tensión para el mes seleccionado |
| RF-03.3 | Gráfico de evolución histórica de CU por competidor (líneas) |
| RF-03.4 | Gráfico de barras comparativo de componentes (G, T, D, Cv, PR, R) del mes actual |
| RF-03.5 | Filtros: ciclo/mes, nivel de tensión, competidor |
| RF-03.6 | Posibilidad de descargar los datos filtrados como CSV |
| RF-03.7 | La UI debe usar la paleta de colores corporativa VATIA |
| RF-03.8 | El dashboard debe cargar en menos de 3 segundos |

### RF-04 — Agente IA
| ID | Requisito |
|----|-----------|
| RF-04.1 | El agente debe responder preguntas sobre los datos en lenguaje natural en español |
| RF-04.2 | Debe responder en menos de 10 segundos |
| RF-04.3 | Debe citar las fuentes de los datos que utiliza para responder |
| RF-04.4 | Debe mantener el contexto de la conversación (historial de mensajes) |
| RF-04.5 | Debe rechazar preguntas fuera de dominio con un mensaje amable |
| RF-04.6 | Los embeddings deben actualizarse automáticamente tras cada carga ETL |

---

## 8. Requisitos No Funcionales

| ID | Categoría | Requisito |
|----|-----------|-----------|
| RNF-01 | **Rendimiento** | El ETL completo de 10 competidores debe completarse en < 30 minutos |
| RNF-02 | **Rendimiento** | El dashboard debe cargar en < 3 segundos con hasta 12 meses de historia |
| RNF-03 | **Disponibilidad** | La app Streamlit debe estar disponible 24/7 (Streamlit Cloud o servidor propio) |
| RNF-04 | **Seguridad** | Las API keys (OpenAI) deben gestionarse via variables de entorno, nunca en el código |
| RNF-05 | **Seguridad** | Sin autenticación en MVP; agregar login básico (st.login o Google OAuth) en v2 |
| RNF-06 | **Mantenibilidad** | Cada scraper debe ser independiente; agregar un nuevo competidor no afecta los demás |
| RNF-07 | **Mantenibilidad** | Cobertura de tests ≥ 70% en los módulos ETL y agente |
| RNF-08 | **Portabilidad** | El proyecto debe ejecutarse en Windows, Linux y Google Colab |
| RNF-09 | **Trazabilidad** | Cada ejecución ETL debe generar un log con timestamp, competidor, filas y errores |
| RNF-10 | **Escalabilidad** | La arquitectura debe permitir agregar más competidores sin refactorización mayor |

---

## 9. División de Responsabilidades

### Persona 1 — ETL / Scraping (Geoff, ya tiene CENS)

**Módulos:** `etl/`, `db/`, `data/`

**Tareas:**
- [x] Crear clase base `ScraperBase` con la interfaz común → `etl/base_scraper.py`
- [x] Refactorizar `etl_tarifas_cens.ipynb` → `etl/scrapers/cens.py`
- [ ] Investigar e implementar scrapers para: AFINIA, ESSA, NEU
- [x] Implementar `transform.py`: normalización al esquema unificado + validación CU
- [x] Implementar `load.py`: escritura a **PostgreSQL** (producción) + CSV backup
- [ ] Implementar `scheduler.py`: cron mensual con APScheduler
- [x] Crear `db/schema.sql` y `db/queries.py`
- [x] Escribir tests unitarios de scrapers (`tests/unit/test_scrapers.py`)

**Conocimiento requerido:** Python, requests, BeautifulSoup, PyMuPDF, easyocr, pandas, SQLite

---

### Persona 2 — Agente IA / Backend (Dev 2)

**Módulos:** `agent/`, integración `etl/load.py → chromadb`

**Tareas:**
- [ ] Implementar `agent/indexer.py`: convierte filas de tarifas a documentos de texto + embeddings
- [ ] Implementar `agent/retriever.py`: búsqueda semántica en ChromaDB
- [ ] Implementar `agent/prompts.py`: system prompt en español con instrucciones de dominio
- [ ] Implementar `agent/chat_agent.py`: orquestador LangChain (RAG chain)
- [ ] Investigar e implementar scrapers para: EPM, CODENSA, EMCALI
- [ ] Escribir tests del agente (`tests/unit/test_agent.py`)
- [x] Documentar cómo configurar la API key de OpenAI / Ollama (`.env.example`)

**Conocimiento requerido:** LangChain, ChromaDB, OpenAI API, Python

---

### Persona 3 — Dashboard / Frontend (Dev 3)

**Módulos:** `app/`

**Tareas:**
- [x] Crear la estructura base de la app Streamlit (`app/main.py` con 2 páginas)
- [x] Implementar `app/styles/theme.py`: paleta de colores corporativa (VATIA verde + lima)
- [x] Implementar `app/components/charts.py`: todos los gráficos Plotly
- [x] Implementar `app/main.py`: KPIs + tabla + gráficos + filtros (all-in-one)
- [x] Implementar `app/pages/2_Agente_IA.py`: UI del chat (placeholder Fase 2)
- [x] Implementar `app/components/kpi_cards.py` y `tabla_comparativa.py`
- [ ] Investigar e implementar scrapers para: AIRE, ENELX, BIA
- [x] Escribir tests de la capa de visualización (test_scrapers, test_transform)

**Conocimiento requerido:** Streamlit, Plotly, Python, CSS básico

---

### Tareas compartidas (todos)

- [ ] Configurar el repositorio en GitHub con ramas: `main`, `develop`, `feat/scraper-*`
- [x] Crear `requirements.txt` con versiones fijadas
- [x] Crear `.env.example` con las variables necesarias
- [x] Configurar Docker Compose: PostgreSQL 16 + Streamlit + ETL (profile-gated)
- [ ] Revisiones de código cruzadas (pull requests) antes de mergear a `develop`

---

## 10. Plan de Trabajo por Fases

### Fase 1 — Fundamentos (Semana 1-2)
**Objetivo:** Repositorio configurado, un scraper completo por persona, base de datos funcionando.

| Tarea | Responsable | Entregable |
|-------|------------|-----------|
| Crear repo GitHub + estructura de carpetas | Todos | Repositorio base |
| `ScraperBase` + refactorizar CENS | Dev 1 | `etl/scrapers/cens.py` |
| `db/schema.sql` + `db/connection.py` | Dev 1 | SQLite funcional |
| Scraper CODENSA | Dev 2 | `etl/scrapers/codensa.py` |
| Scraper EMCALI | Dev 3 | `etl/scrapers/emcali.py` |
| App Streamlit esqueleto + tema | Dev 3 | App ejecutable vacía |

### Fase 2 — Core Features (Semana 3-4)
**Objetivo:** Pipeline ETL completo, dashboard con datos reales, agente básico funcionando.

| Tarea | Responsable | Entregable |
|-------|------------|-----------|
| `pipeline.py` (orquestador ETL) | Dev 1 | Pipeline ejecutable |
| `load.py` → SQLite + ChromaDB | Dev 1 + Dev 2 | Datos persistidos |
| Agente RAG básico (indexer + chain) | Dev 2 | Respuestas en consola |
| Dashboard Página 1: KPIs + tabla | Dev 3 | Vista ejecutiva funcional |
| Chat UI básico | Dev 3 | Chat integrado |

### Fase 3 — Completar Scrapers (Semana 5-6)
**Objetivo:** Los 10 competidores cubiertos.

| Competidor | Responsable |
|-----------|------------|
| CENS ✅ | Dev 1 |
| AFINIA, ESSA, NEU | Dev 1 |
| EPM, CODENSA, EMCALI | Dev 2 |
| AIRE, ENELX, BIA | Dev 3 |

### Fase 4 — Pulir y Testear (Semana 7-8)
**Objetivo:** Cobertura de tests ≥ 70%, UI refinada, deploy.

- Tests completos
- Refinamiento del dashboard (paleta, responsive)
- Mejora del agente (historial, citas de fuentes)
- Deploy en Streamlit Cloud
- Documentación final

---

## 11. Casos de Prueba

### 11.1 Tests Unitarios — ETL

```python
# tests/unit/test_scrapers.py

class TestScraperCENS:
    def test_extrae_7_componentes_por_nivel():
        """Debe extraer G, T, D, Cv, PR, R, CU para cada nivel."""
        # Dado: bytes del PDF de enero 2026 (fixture)
        # Cuando: ejecuto cens.extraer(pdf_bytes)
        # Entonces: df tiene 4 filas, columnas G/T/D/Cv/PR/R/CU sin nulos

    def test_relacion_cu_valida():
        """CU debe ser igual a G + T + D + Cv + PR + R (± 0.01)."""
        # Entonces: abs(df.CU - (df.G + df.T + df.D + df.Cv + df.PR + df.R)) < 0.01

    def test_ciclo_extraido_del_nombre():
        """Debe extraer AAAAMM del nombre del archivo."""
        # Dado: "Tarifas_CENS_202601_.pdf"
        # Entonces: ciclo == "202601", fecha == "2026-01-01"

    def test_fallback_etiqueta_g_no_detectada():
        """Si OCR no detecta 'G', el fallback posicional debe asignarla."""
        # Dado: fila de 6 valores idénticos (sin etiqueta)
        # Entonces: componente asignado == "G"

    def test_pdf_404_lanza_excepcion():
        """Si el PDF no existe, debe lanzar HTTPError."""
        # Dado: URL de un PDF inexistente
        # Entonces: raises requests.exceptions.HTTPError
```

```python
class TestTransform:
    def test_normalizar_cens_al_esquema_unificado():
        """DataFrame de CENS debe tener exactamente las 15 columnas del esquema."""

    def test_no_permite_duplicados():
        """Insertar el mismo (ciclo, comercializador, nivel) dos veces debe fallar."""

    def test_valores_numericos_son_float():
        """G, T, D, Cv, PR, R, CU deben ser float, nunca string."""
```

### 11.2 Tests Unitarios — Agente IA

```python
# tests/unit/test_agent.py

class TestAgentRAG:
    def test_responde_pregunta_de_dominio():
        """Pregunta sobre tarifas debe producir respuesta no vacía."""
        # Dado: datos de 3 competidores indexados
        # Cuando: "¿Cuál es el CU más bajo de este mes?"
        # Entonces: respuesta contiene un nombre de comercializador y un número

    def test_rechaza_pregunta_fuera_de_dominio():
        """Pregunta irrelevante debe producir respuesta de rechazo educado."""
        # Cuando: "¿Cuál es la capital de Francia?"
        # Entonces: respuesta contiene "no puedo" o "fuera de mi dominio"

    def test_cita_fuentes():
        """La respuesta debe incluir la referencia a los datos usados."""
        # Entonces: response.sources no está vacío

    def test_mantiene_historial():
        """Segunda pregunta debe poder referirse a la primera."""
        # Dado: pregunta1 = "¿CU de CENS?"
        # Cuando: pregunta2 = "¿Y el de EPM comparado con ese?"
        # Entonces: respuesta incluye información de ambos
```

### 11.3 Tests de Integración

```python
# tests/integration/test_pipeline.py

class TestPipelineIntegracion:
    def test_pipeline_completo_cens(tmp_path):
        """Pipeline ETL de CENS debe escribir en SQLite y ChromaDB."""
        # Dado: PDF local de CENS (fixture)
        # Cuando: ejecutar pipeline.run("cens", pdf_bytes)
        # Entonces:
        #   - db contiene 4 filas con ciclo 202601
        #   - chromadb contiene documentos indexados
        #   - CSV exportado en tmp_path

    def test_pipeline_maneja_error_404(tmp_path):
        """Si el scraper falla por 404, el pipeline registra error y continúa."""
        # Dado: mock que devuelve 404 para un competidor
        # Cuando: ejecutar pipeline.run_all()
        # Entonces: errores tiene 1 entrada, df_final tiene filas de los demás

class TestDBIntegracion:
    def test_upsert_no_duplica():
        """Reinsertar el mismo ciclo debe actualizar, no duplicar."""
        # Dado: 4 filas de ciclo 202601 ya en DB
        # Cuando: insertar las mismas 4 filas de nuevo
        # Entonces: total filas sigue siendo 4
```

### 11.4 Tests del Dashboard

```python
# tests/unit/test_charts.py

class TestCharts:
    def test_grafico_evolucion_retorna_figura():
        """Función de gráfico de evolución debe retornar una figura Plotly."""
        # Dado: DataFrame con 3 meses × 2 competidores
        # Cuando: charts.grafico_evolucion(df)
        # Entonces: isinstance(fig, plotly.graph_objs.Figure)

    def test_tabla_comparativa_no_vacia():
        """Tabla comparativa no debe estar vacía con datos válidos."""
        # Dado: DataFrame con datos de 5 competidores
        # Cuando: tabla_comparativa(df, nivel=1)
        # Entonces: df_resultado tiene 5 filas

    def test_kpi_cu_minimo_correcto():
        """KPI de CU mínimo debe retornar el valor más bajo."""
```

### 11.5 Matriz de cobertura objetivo

| Módulo | Cobertura objetivo |
|--------|-------------------|
| `etl/scrapers/cens.py` | ≥ 80% |
| `etl/transform.py` | ≥ 90% |
| `etl/load.py` | ≥ 75% |
| `agent/chat_agent.py` | ≥ 70% |
| `agent/indexer.py` | ≥ 80% |
| `app/components/` | ≥ 65% |

---

## 12. Riesgos y Mitigaciones

| # | Riesgo | Probabilidad | Impacto | Mitigación |
|---|--------|-------------|---------|-----------|
| R1 | Un competidor cambia el formato de su web/PDF | Alta | Medio | Scrapers independientes; alertas de monitoreo; tiempo de corrección < 1 día por scraper |
| R2 | OCR falla para un PDF con fuente/resolución inusual | Media | Medio | Fallback posicional ya implementado; opción de escala ajustable por scraper |
| R3 | OpenAI aumenta precios o restringe acceso | Baja | Alto | Arquitectura permite cambiar a Ollama (local) sin cambiar el código del agente |
| R4 | ChromaDB pierde los embeddings (corrupción) | Baja | Medio | `indexer.py` es idempotente: re-indexar desde SQLite en < 5 minutos |
| R5 | Streamlit Cloud tiene límite de recursos | Media | Bajo | Migrables a Railway o Render con `Dockerfile` |
| R6 | Un competidor bloquea el scraping (IP ban) | Media | Medio | User-Agent rotation; respecto de robots.txt; caché de 30 días |
| R7 | El equipo no tiene acceso a la API de OpenAI | Media | Alto | Usar Ollama con `llama3.1:8b` desde el inicio; documentar ambas opciones |

---

## Próximos pasos inmediatos

1. **Hoy:** Crear el repositorio en GitHub, copiar esta estructura de carpetas, crear `requirements.txt`
2. **Esta semana:** Cada persona elige su primer scraper nuevo y crea la rama `feat/scraper-{nombre}`
3. **Esta semana:** Dev 3 crea el esqueleto de la app Streamlit con el tema de colores
4. **Esta semana:** Dev 2 prueba LangChain + ChromaDB con los datos de CENS ya disponibles
5. **Fin de semana 2:** Primera demo interna con datos de al menos 3 competidores

---

*Documento vivo — actualizar en cada sprint.*

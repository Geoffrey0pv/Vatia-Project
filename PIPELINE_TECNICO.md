# Pipeline ETL — Tarifas CENS
**Plataforma VATIA · Módulo de Inteligencia Tarifaria**

---

## 1. Visión general

El pipeline extrae automáticamente los **componentes del Costo Unitario (CU)** de la energía eléctrica publicados mensualmente por CENS en su sitio web, y los entrega como un CSV listo para consumir en Power BI.

```
Web CENS ──scraping──▶ PDF de Tarifas ──OCR──▶ Tabla CU ──▶ tarifas_cens_limpio.csv
```

**Salida:** `tarifas_cens_limpio.csv`

| Fecha | Ciclo | Comercializador | Mercado | Nivel_Tension | Dueño_Red | G | T | D | Cv | PR | R | CU |
|-------|-------|----------------|---------|--------------|-----------|---|---|---|----|----|----|-----|
| 2026-01-01 | 202601 | CENS | CENS | 1 | 100% OPERADOR | 298.9047 | 52.9743 | 317.1201 | … | … | … | 888.059 |

- **4 filas por mes** (Niveles de Tensión 1, 2, 3, 4)
- Separador: `;` · Decimal: `,` · Codificación: UTF-8 BOM (compatible con Excel en español)

---

## 2. Flujo completo paso a paso

```
┌─────────────────────────────────────────────────────────────────────┐
│  PASO 1 — SCRAPING WEB                                              │
│  URL: cens.com.co/clientes-y-usuarios/tarifas-de-energia            │
│  → BeautifulSoup localiza la <table> con encabezado "Periodo"       │
│  → Extrae todos los href .pdf de la columna "Periodo"               │
│  → Construye lista de (nombre_archivo, url_descarga) por ciclo      │
└────────────────────────────┬────────────────────────────────────────┘
                             │  Lista de URLs
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PASO 2 — DESCARGA DEL PDF                                          │
│  requests.get() con headers de browser (evita bloqueos)             │
│  → Bytes del PDF en memoria (no toca el disco)                      │
└────────────────────────────┬────────────────────────────────────────┘
                             │  pdf_bytes
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PASO 3 — DETECCIÓN DE PÁGINA                                       │
│  PyMuPDF inspecciona cada página buscando texto extraíble           │
│  → Las páginas 1-3 tienen texto (info general, CU total)            │
│  → La página 4 tiene CERO caracteres = imagen rasterizada           │
│  → Se selecciona la primera página sin texto como página de tabla   │
└────────────────────────────┬────────────────────────────────────────┘
                             │  pagina_idx
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PASO 4 — RENDERIZADO Y OCR                                         │
│  PyMuPDF renderiza la página a imagen (escala 2.5x → ~3500×2500 px) │
│  easyocr.readtext() detecta cada bloque de texto con:               │
│    - Coordenadas del bounding box (x1,y1), (x2,y2)                 │
│    - Texto reconocido                                                │
│    - Nivel de confianza [0, 1]                                       │
│  → Se filtran elementos con confianza < 0.4                         │
│  → Resultado: lista de (y_centro, x_centro, texto)                  │
└────────────────────────────┬────────────────────────────────────────┘
                             │  items OCR (≈153 elementos)
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PASO 5 — AGRUPACIÓN EN FILAS                                       │
│  Los items se ordenan por Y y se agrupan:                           │
│    |ΔY| ≤ 18 px → misma fila · |ΔY| > 18 px → nueva fila          │
│  Dentro de cada fila, se ordenan por X (izquierda → derecha)        │
│  → Resultado: lista de listas de strings (≈38 filas)                │
└────────────────────────────┬────────────────────────────────────────┘
                             │  filas agrupadas
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PASO 6 — PARSEO DE LA TABLA                                        │
│  a) Localiza la fila de encabezado buscando "CENS", "Nivel N", etc. │
│  b) Mapea posición de columna → Nivel de Tensión (regex)            │
│     col 1 = "1-2,CENS" → NT1 · col 4 = "Nivel 2" → NT2 · etc.     │
│  c) Para cada fila de datos:                                         │
│     - fila[0] identifica el componente (regex: ^G$, ^T$, DtUN, …)  │
│     - fila[col_idx] extrae el valor numérico para cada nivel         │
│  d) Fallback para G y T: si el OCR no detectó la etiqueta           │
│     (carácter único de baja visibilidad), se identifica la fila      │
│     porque TODOS sus valores son idénticos entre sí                  │
│     → se asigna en orden secuencial (primero G, luego T)            │
└────────────────────────────┬────────────────────────────────────────┘
                             │  dict {nivel: {componente: valor}}
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PASO 7 — CONSTRUCCIÓN DEL DataFrame                                │
│  4 filas (NT1, NT2, NT3, NT4) × 13 columnas                         │
│  Fecha, Ciclo, Comercializador, Mercado, Nivel_Tension, Dueño_Red,  │
│  G, T, D, Cv, PR, R, CU                                             │
└────────────────────────────┬────────────────────────────────────────┘
                             │  loop por cada mes
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PASO 8 — CONSOLIDACIÓN Y EXPORTACIÓN                               │
│  pd.concat() de todos los meses → ordenar por Ciclo + Nivel         │
│  df.to_csv(sep=";", decimal=",", encoding="utf-8-sig")              │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Tecnologías utilizadas

| Librería | Versión mínima | Rol en el pipeline |
|----------|---------------|-------------------|
| **requests** | 2.x | Descarga HTTP del PDF y scraping de la página web |
| **BeautifulSoup4** | 4.x | Parseo del HTML para localizar la tabla de enlaces |
| **PyMuPDF** (`fitz`) | 1.23+ | Renderiza páginas del PDF a arrays de imagen numpy; detecta páginas sin texto |
| **easyocr** | 1.7+ | OCR en Python puro — reconocimiento de texto en imágenes; modelos en inglés (~200 MB, descarga única) |
| **numpy** | 1.24+ | Conversión de pixmap a array RGB para easyocr |
| **pandas** | 2.x | Construcción del DataFrame y exportación CSV |
| **pdfplumber** | 0.10+ | Importado (disponible para futuro uso; las páginas de tabla son imágenes, no texto) |

**Runtime:** Python 3.11+ · Compatible con Windows, Linux y Google Colab

---

## 4. Estructura del PDF de origen

```
Tarifas_CENS_AAAAMM_.pdf  (≈2.5 MB por archivo)
├── Página 1  — Texto extraíble: información general del periodo
├── Página 2  — Texto extraíble: valores CU resumidos, mención COT
├── Página 3  — Texto extraíble: condiciones y notas aclaratorias
├── Página 4  ─── IMAGEN RASTERIZADA ───  ← tabla principal (OCR aquí)
│              COMPONENTES DEL COSTO UNITARIO - CU en $/kWh
│              Componentes | 1-2,CENS | 1-2 Comp. | 1-2 Part. | Nivel 2 | Nivel 3 | Nivel 4
│              G           | xxx.xxxx | xxx.xxxx  | xxx.xxxx  | xxx.xxxx| xxx.xxxx| xxx.xxxx
│              T           | xxx.xxxx | ...
│              DtUN        | xxx.xxxx | ...
│              Cv          | xxx.xxxx | ...
│              PR          | xxx.xxxx | ...
│              R           | xxx.xxxx | ...
│              CUv         | xxx.xxxx | ...
├── Página 5  — IMAGEN RASTERIZADA: tabla de opciones tarifarias
└── Página 6  — IMAGEN RASTERIZADA: tabla de uso regulado CREG
```

**Por qué OCR y no extracción directa de texto:**
Las páginas 4–6 son imágenes PNG embebidas dentro del PDF. PyMuPDF y pdfplumber devuelven 0 caracteres al intentar `get_text()` — el texto no existe como datos, es parte de la imagen.

---

## 5. Desafíos técnicos resueltos

### 5.1 Etiquetas G y T no detectadas por OCR

**Problema:** Los caracteres `G` y `T` son letras únicas de pequeño tamaño. En algunos PDFs el OCR no los detecta (confianza < 0.4), produciendo una fila con 6 valores numéricos sin etiqueta.

**Solución — fallback posicional:**
```
G y T tienen el mismo valor en TODAS las columnas de la tabla
(son tarifas nacionales: idénticas para NT1, NT2, NT3 y NT4).

Si fila tiene todos sus elementos iguales → es una fila sin etiqueta.
→ Se asigna el primer componente pendiente en el orden canónico [G, T, D, Cv, PR, R, CU].
```

### 5.2 Header OCR pierde una columna (mes de marzo 2026)

**Problema:** En el PDF de 202603, el OCR no detectó el encabezado `1-2,Particular`, resultando en un header de 6 en vez de 7 elementos. La fila T (también de 6 elementos idénticos) no disparaba el fallback porque `len(fila) == n_cols_esperadas`.

**Solución:**
```python
etiqueta_faltante = (
    len(fila) in (n_cols_esperadas - 1, n_cols_esperadas)  # ← acepta ambos casos
    and all(_parse_numero(v) is not None for v in fila)
    and len(set(fila)) == 1   # todos los valores idénticos → G o T
)
```

### 5.3 Columnas "Compartido" y "Particular" fuera del esquema destino

**Decisión de diseño:** El prototipo de datos VATIA solo requiere los 4 niveles principales. Las columnas `1-2 Compartido` y `1-2,Particular` existen en el PDF pero no se mapean. El `MAPEO_NIVELES_PDF` solo incluye patrones para `1-2.*CENS`, `Nivel 2`, `Nivel 3` y `Nivel 4`.

---

## 6. Parámetros configurables

Todos en la **celda 5** (`## 1. Importaciones y Configuración Global`):

```python
URL_TARIFAS     = "https://www.cens.com.co/clientes-y-usuarios/tarifas-de-energia"
COMERCIALIZADOR = "CENS"
MERCADO         = "CENS"
DUENO_RED       = "100% OPERADOR"
OUTPUT_CSV      = "tarifas_cens_limpio.csv"

MAPEO_COMPONENTES = [
    (r"^G$",           "G"),
    (r"^T$",           "T"),
    (r"DtUN|DtN|^D$",  "D"),
    (r"^Cv$",          "Cv"),
    (r"^PR$",          "PR"),
    (r"^R$",           "R"),
    (r"CUv|^CU$",      "CU"),
]

MAPEO_NIVELES_PDF = [
    (r"1-2.*CENS",  "1"),
    (r"Nivel\s*2",  "2"),
    (r"Nivel\s*3",  "3"),
    (r"Nivel\s*4",  "4"),
]
```

---

## 7. Robustez y manejo de errores

| Escenario | Comportamiento |
|-----------|---------------|
| PDF de un mes aún no publicado (404) | El error se registra, el mes se omite, el pipeline continúa con los demás meses |
| SSL Error en la conexión | Reintento automático con `verify=False` + fallback a búsqueda por patrón de href |
| Tabla de Periodo no encontrada en HTML | Fallback: busca cualquier `<a href>` que coincida con `Tarifas*.pdf` en toda la página |
| OCR no detecta la etiqueta G o T | Fallback posicional: fila de valores idénticos → asignación en orden canónico |
| Header OCR con una columna menos | La condición `len(fila) in (n-1, n)` cubre ambos casos |
| Página de tabla no encontrada | Se usa la última página del PDF como fallback |

---

## 8. Limitaciones conocidas

1. **Velocidad:** El OCR tarda ~15–20 segundos por PDF (sin GPU). 5 meses ≈ 75–100 segundos totales. No es un bloqueante para uso mensual.

2. **Primera ejecución lenta:** easyocr descarga los modelos de reconocimiento (~200 MB) en el primer uso. Las ejecuciones posteriores usan el caché en `~/.EasyOCR/`.

3. **Sin GPU:** Si el entorno tiene GPU (CUDA), easyocr la usará automáticamente, reduciendo el tiempo a ~3–5 segundos por página.

4. **Dependencia del diseño del PDF:** Si CENS rediseña la tabla (colores, estructura de columnas, nuevos niveles), los regex en `MAPEO_COMPONENTES` y `MAPEO_NIVELES_PDF` necesitan actualización.

---

## 9. Mantenimiento esperado

| Evento | Acción requerida | Frecuencia estimada |
|--------|-----------------|---------------------|
| Nuevo mes publicado | Ninguna — el pipeline lo detecta automáticamente | Mensual |
| Cambio de año (2027) | Verificar si la ruta cambia de `/T2026/` a `/T2027/`; si la URL sigue el mismo patrón, no requiere cambio | Anual |
| Rediseño de la tabla PDF | Actualizar regex en `MAPEO_COMPONENTES` o `MAPEO_NIVELES_PDF` | Esporádico |
| Cambio en la estructura HTML de la web | Actualizar el selector de tabla en `obtener_enlaces_pdf()` | Esporádico |
| Nueva columna requerida en el CSV | Agregar entrada en `MAPEO_COMPONENTES` con su patrón regex | Según necesidad |

---

## 10. Cómo ejecutar

```bash
# Instalar dependencias (solo la primera vez)
pip install requests beautifulsoup4 pymupdf easyocr pandas pdfplumber

# Ejecutar el notebook completo en orden:
# Celda 3  → instala dependencias
# Celda 5  → carga configuración
# Celda 7  → define funciones de extracción (scraping)
# Celda 9  → define funciones de transformación (OCR)
# Celda 11 → define función de exportación
# Celda 14 → ejecuta el pipeline completo → genera tarifas_cens_limpio.csv
```

**Salida esperada por consola:**
```
PIPELINE ETL — TARIFAS CENS (TODOS LOS MESES)
[SCRAPING] ✔ Tabla encontrada — 5 PDFs disponibles
── Ciclo 202601 → 4 filas × 13 columnas ✔
── Ciclo 202602 → 4 filas × 13 columnas ✔
── Ciclo 202603 → 4 filas × 13 columnas ✔
── Ciclo 202604 → 4 filas × 13 columnas ✔
── Ciclo 202605 → ✖ 404 (aún no publicado)
✔ CSV exportado: .../tarifas_cens_limpio.csv
PIPELINE COMPLETADO — 4/5 meses · 16 filas
```

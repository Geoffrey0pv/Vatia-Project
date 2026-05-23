Aquí tienes el resumen completo y estructurado de nuestra conversación, seguido del prompt maestro (ingeniería de prompts) listo para que lo copies y pegues en tu agente de desarrollo (Cursor, ChatGPT, Claude, etc.) para construir el MVP.

### 📊 Resumen del Proyecto: Plataforma de Inteligencia Competitiva (VATIA)

**Contexto del Problema:**
Actualmente, la recolección y análisis de tarifas de los competidores se hace de forma manual. Este proceso toma unos 5 días, generando retrasos que impactan la toma de decisiones del equipo comercial y afectando potencialmente hasta un 3% de las ventas.

**Objetivo General del Proyecto:**
Desarrollar una plataforma automatizada que extraiga las tarifas de más de 20 competidores (comercializadoras de energía), procese los componentes tarifarios ($G, T, D, C_v, PR, R, CU$) y los exponga en "tiempo casi real" (mensualizado según la regulación) a través de:

1. Un Dashboard interactivo (Power BI).
2. Un Agente de Inteligencia Artificial que permita a los ejecutivos hacer consultas en lenguaje natural sobre los datos.

**Estrategia para la Primera Reunión (El MVP):**
En lugar de intentar abarcar los más de 20 links con múltiples formatos (PDFs, Excels, HTML), la estrategia es presentar un **Producto Mínimo Viable (MVP)** enfocado en un solo competidor: **CENS**.

* **¿Por qué CENS?** Porque publican archivos `.xlsx` directos (sin CAPTCHAs complejos).
* **El Flujo del MVP:** 1. Un bot en Python (Google Colab) ingresa a la web y descarga el archivo Excel del mes.
2. Un proceso ETL (Pandas) lee el archivo, ignora las primeras 9 filas de logos/membretes y recorre las hojas de los Niveles de Tensión (1 al 4).
3. Extrae dinámicamente el ciclo del nombre del archivo y lo convierte en una columna de **Fecha** (ej. `2026-01-01`) para permitir la "Inteligencia de Tiempo" en Power BI.
4. Busca y extrae los valores específicos de los componentes tarifarios.
5. Exporta un archivo `.csv` plano y estructurado, idéntico al prototipo de datos esperado para el dashboard.

---


# Prototipo de datos 

Fecha;Ciclo;Comercializador;Mercado;Nivel_Tension;Dueño_Red;G;T;D;Cv;PR;R;CU
2026-01-01;202601;CENS;CENS;1;100% OPERADOR;296,8074;55,9536;277,5855;205,3295;65,7922;26,2975;927,7657
2026-01-01;202601;CENS;CENS;2;100% OPERADOR;296,8074;55,9536;165,3711;205,3295;26,5586;26,2975;776,3177
2026-01-01;202601;CENS;CENS;3;100% OPERADOR;296,8074;55,9536;63,4500;205,3295;26,5586;26,2975;674,3966


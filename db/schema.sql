-- ════════════════════════════════════════════════════════════════════════════
-- VATIA — Schema PostgreSQL 16
-- Ejecutado automáticamente al inicializar el contenedor Docker.
-- ════════════════════════════════════════════════════════════════════════════

-- ── Tabla principal de tarifas ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tarifas (
    id              SERIAL          PRIMARY KEY,

    -- Dimensiones temporales
    fecha           DATE            NOT NULL,
    ciclo           CHAR(6)         NOT NULL,   -- AAAAMM (ej. "202601")

    -- Dimensiones del operador
    operador_red    VARCHAR(100),
    comercializador VARCHAR(100)    NOT NULL,
    nivel_tension   SMALLINT        NOT NULL CHECK (nivel_tension BETWEEN 1 AND 4),
    tipo_red        VARCHAR(50),
    comb_nt         VARCHAR(50),
    dueno_red       VARCHAR(100),

    -- Componentes del Costo Unitario ($/kWh, 4 decimales)
    g               NUMERIC(10, 4),   -- Generación
    t               NUMERIC(10, 4),   -- Transmisión
    d               NUMERIC(10, 4),   -- Distribución
    cv              NUMERIC(10, 4),   -- Comercialización variable
    pr              NUMERIC(10, 4),   -- Pérdidas reconocidas
    r               NUMERIC(10, 4),   -- Restricciones
    cu              NUMERIC(10, 4),   -- Costo Unitario total

    -- Auditoría
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Unicidad: un registro por ciclo × comercializador × nivel
    CONSTRAINT uq_ciclo_comercializador_nivel
        UNIQUE (ciclo, comercializador, nivel_tension)
);

-- ── Índices para consultas frecuentes del dashboard ──────────────────────────
CREATE INDEX IF NOT EXISTS idx_tarifas_ciclo
    ON tarifas (ciclo DESC);

CREATE INDEX IF NOT EXISTS idx_tarifas_comercializador
    ON tarifas (comercializador);

CREATE INDEX IF NOT EXISTS idx_tarifas_nivel
    ON tarifas (nivel_tension);

CREATE INDEX IF NOT EXISTS idx_tarifas_ciclo_nivel
    ON tarifas (ciclo, nivel_tension);

-- ── Vista: último ciclo disponible por comercializador ───────────────────────
CREATE OR REPLACE VIEW v_ultimo_ciclo AS
SELECT DISTINCT ON (comercializador, nivel_tension)
    ciclo, fecha, comercializador, nivel_tension,
    operador_red, dueno_red,
    g, t, d, cv, pr, r, cu
FROM tarifas
ORDER BY comercializador, nivel_tension, ciclo DESC;

-- ── Vista: evolución histórica del CU ────────────────────────────────────────
CREATE OR REPLACE VIEW v_evolucion_cu AS
SELECT
    ciclo,
    fecha,
    comercializador,
    nivel_tension,
    cu,
    ROUND(cu - LAG(cu) OVER (
        PARTITION BY comercializador, nivel_tension
        ORDER BY ciclo
    ), 4) AS variacion_cu
FROM tarifas
ORDER BY comercializador, nivel_tension, ciclo;

-- ── Vista: comparativo del mes más reciente ───────────────────────────────────
CREATE OR REPLACE VIEW v_comparativo_actual AS
SELECT
    t.ciclo,
    t.fecha,
    t.comercializador,
    t.nivel_tension,
    t.g, t.t, t.d, t.cv, t.pr, t.r, t.cu,
    MIN(t.cu) OVER (PARTITION BY t.ciclo, t.nivel_tension) AS cu_min_mercado,
    MAX(t.cu) OVER (PARTITION BY t.ciclo, t.nivel_tension) AS cu_max_mercado,
    ROUND(AVG(t.cu) OVER (PARTITION BY t.ciclo, t.nivel_tension), 4) AS cu_promedio_mercado,
    ROUND(t.cu - MIN(t.cu) OVER (PARTITION BY t.ciclo, t.nivel_tension), 4) AS diferencia_vs_min
FROM tarifas t
WHERE t.ciclo = (SELECT MAX(ciclo) FROM tarifas);

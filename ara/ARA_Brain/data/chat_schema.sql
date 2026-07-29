-- ============================================================
--  MIGRACIÓN BANDEJA DE MENSAJES — Proyecto ARA
--  Base de datos: ara/ARA_Brain/data/proyecto_ara.db
--  Segura (IF NOT EXISTS) — puede ejecutarse varias veces.
-- ============================================================

PRAGMA foreign_keys = OFF;

-- ------------------------------------------------------------
-- 1) CONTACTOS / USUARIOS DE CHAT
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contactos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id      TEXT,                          -- enlace externo (id de tabla usuarios, jefe, etc.)
    nombre          TEXT    NOT NULL,
    telefono        TEXT    NOT NULL UNIQUE,       -- identificador único (ej: 521234567890)
    foto_url        TEXT    DEFAULT '',
    ultimo_acceso   DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- 2) CONVERSACIONES (una por contacto)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversaciones (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    contacto_id       INTEGER NOT NULL,
    ultimo_mensaje    TEXT    DEFAULT '',
    fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP,
    unread_count      INTEGER DEFAULT 0,
    UNIQUE (contacto_id),
    FOREIGN KEY (contacto_id) REFERENCES contactos (id) ON DELETE CASCADE
);

-- Índice para listar conversaciones ordenadas por última actividad
CREATE INDEX IF NOT EXISTS idx_conversaciones_fecha
    ON conversaciones (fecha_actualizacion DESC);

-- ------------------------------------------------------------
-- 3) MENSAJES (hilo de cada conversación)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mensajes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    conversacion_id   INTEGER NOT NULL,
    remitente         TEXT    NOT NULL CHECK (remitente IN ('cliente','sistema','agente')),
    tipo              TEXT    NOT NULL DEFAULT 'texto'
                        CHECK (tipo IN ('texto','imagen','archivo','audio')),
    contenido         TEXT    NOT NULL DEFAULT '',
    sender_id         TEXT,                          -- quién envía (nombre usuario / teléfono)
    timestamp         DATETIME DEFAULT CURRENT_TIMESTAMP,
    estado            TEXT    NOT NULL DEFAULT 'enviado'
                        CHECK (estado IN ('enviado','entregado','leido')),
    FOREIGN KEY (conversacion_id) REFERENCES conversaciones (id) ON DELETE CASCADE
);

-- Índices para historial paginado y para marcar-no-leídos rápido
CREATE INDEX IF NOT EXISTS idx_mensajes_conv_ts
    ON mensajes (conversacion_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_mensajes_estado
    ON mensajes (conversacion_id, estado);

PRAGMA foreign_keys = ON;

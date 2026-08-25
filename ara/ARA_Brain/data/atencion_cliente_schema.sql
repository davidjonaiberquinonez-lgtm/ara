-- ============================================================
--  MÓDULO ATENCIÓN AL CLIENTE (WhatsApp Meta Cloud API) — Proyecto ARA
--  Base de datos: ara/ARA_Brain/data/proyecto_ara.db (misma BD que
--  chat_schema.sql — módulo separado a propósito, dominio distinto:
--  aquí el otro lado de la conversación es un CLIENTE EXTERNO
--  autenticado por co_cli, no un usuario interno del sistema).
--  Segura (IF NOT EXISTS) — puede ejecutarse varias veces.
-- ============================================================

PRAGMA foreign_keys = OFF;

-- ------------------------------------------------------------
-- 1) NÚMEROS DE WHATSAPP BUSINESS (Meta Cloud API)
--    Arquitectura elegida: UNA sola WABA/App de Meta para todo ARA,
--    con VARIOS phone_number_id registrados debajo — cada uno asignado
--    a un agente (usuario interno) distinto. El access token es único
--    por WABA (Meta no emite un token por número), así que NO se guarda
--    aquí: vive en el .env (WHATSAPP_ACCESS_TOKEN/WHATSAPP_WABA_ID).
--    Sin credenciales reales todavía: la fila puede existir con
--    modo_simulado=1 y funcionar igual para probar el flujo completo.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta_numeros (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_number_id TEXT    NOT NULL UNIQUE,   -- id de Meta (o "SIM-<usuario_id>" en modo simulado)
    numero_visible  TEXT    DEFAULT '',         -- +58 412 xxx xxxx, solo para mostrar en la UI
    usuario_id      TEXT    NOT NULL,           -- agente asignado (id de tabla usuarios)
    activo          INTEGER NOT NULL DEFAULT 1,
    modo_simulado   INTEGER NOT NULL DEFAULT 1, -- 1 = no envía a Meta de verdad todavía
    fecha_asignado  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (usuario_id)  -- 1 número por agente (regla acordada con el usuario)
);

-- ------------------------------------------------------------
-- 2) CONVERSACIONES DE ATENCIÓN AL CLIENTE
--    1 hilo por cliente autenticado (co_cli) POR número/agente que lo
--    atiende — un mismo cliente podría, en teoría, escribir a números
--    distintos si contacta a más de un agente, así que la unicidad es
--    por la pareja (co_cli, phone_number_id), no solo por co_cli.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS atencion_conversaciones (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    co_cli              TEXT    NOT NULL,       -- cliente autenticado (Profit)
    nombre_cliente      TEXT    DEFAULT '',
    telefono_cliente    TEXT    NOT NULL,       -- número real de WhatsApp del cliente
    phone_number_id     TEXT    NOT NULL,       -- número Meta / agente que lo atiende
    modo                TEXT    NOT NULL DEFAULT 'ia'
                            CHECK (modo IN ('ia','agente')),  -- quién controla el hilo ahora
    ultimo_mensaje      TEXT    DEFAULT '',
    fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP,
    unread_count        INTEGER DEFAULT 0,
    UNIQUE (co_cli, phone_number_id),
    FOREIGN KEY (phone_number_id) REFERENCES meta_numeros (phone_number_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_atencion_conv_numero
    ON atencion_conversaciones (phone_number_id);
CREATE INDEX IF NOT EXISTS idx_atencion_conv_fecha
    ON atencion_conversaciones (fecha_actualizacion DESC);

-- ------------------------------------------------------------
-- 3) MENSAJES DE ATENCIÓN AL CLIENTE
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS atencion_mensajes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    conversacion_id   INTEGER NOT NULL,
    remitente         TEXT    NOT NULL CHECK (remitente IN ('cliente','ia','agente')),
    tipo              TEXT    NOT NULL DEFAULT 'texto'
                        CHECK (tipo IN ('texto','imagen','archivo','audio')),
    contenido         TEXT    NOT NULL DEFAULT '',
    sender_id         TEXT,                     -- usuario_id del agente humano (si remitente='agente')
    origen_datos      TEXT    DEFAULT '',        -- 'consultar_saldo_cliente' / 'consultar_cliente' / '' (auditoría de qué tool alimentó la respuesta de IA)
    timestamp         DATETIME DEFAULT CURRENT_TIMESTAMP,
    estado            TEXT    NOT NULL DEFAULT 'enviado'
                        CHECK (estado IN ('enviado','entregado','leido')),
    FOREIGN KEY (conversacion_id) REFERENCES atencion_conversaciones (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_atencion_msg_conv_ts
    ON atencion_mensajes (conversacion_id, timestamp DESC);

PRAGMA foreign_keys = ON;

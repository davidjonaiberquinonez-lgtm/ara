-- ═══════════════════════════════════════════════════════════════════════
-- FASE 4.17 (v4.17) — AJUSTE DE ADAPTADORES: tablas locales app_* (MySQL legacy)
--
-- Aplica SOLO al MySQL legacy de gestión (192.168.4.148 / BD barquisimeto),
-- NUNCA a Profit (PRUEB25 es solo lectura). Los tools crean estas tablas
-- con CREATE TABLE IF NOT EXISTS al arrancar (best-effort); este guion es el
-- respaldo idempotente para el DBA.
--
-- Regla de la orden: "No crear tablas en Profit; usar app_ tablas locales si
-- se requiere auditoría". Estas tablas NO tocan el esquema de negocio.
-- ═══════════════════════════════════════════════════════════════════════

USE `barquisimeto`;

-- Auditoría de operaciones CRUD sobre notas de gestión (AD1 / AD4).
CREATE TABLE IF NOT EXISTS `app_log_notas` (
  `id`      BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `nota_id` VARCHAR(40)  NOT NULL,
  `accion`  VARCHAR(30)  NOT NULL,               -- CREADA | MODIFICADA | ELIMINADA
  `usuario` VARCHAR(80)  NOT NULL DEFAULT '',
  `fecha`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `detalle` VARCHAR(500) NULL DEFAULT NULL,
  KEY `idx_app_log_notas_nota`  (`nota_id`),
  KEY `idx_app_log_notas_fecha` (`fecha`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Eliminaciones de notas con motivo y monto (AD4).
CREATE TABLE IF NOT EXISTS `app_log_eliminaciones` (
  `id`                BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `nota_id`           VARCHAR(40)   NOT NULL,
  `doc_num`           VARCHAR(40)   NOT NULL DEFAULT '',
  `usuario`           VARCHAR(80)   NOT NULL DEFAULT '',
  `fecha_eliminacion` DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `motivo`            VARCHAR(255)  NULL DEFAULT NULL,
  `monto_total`       DECIMAL(18,2) NOT NULL DEFAULT 0,
  KEY `idx_app_log_elim_fecha` (`fecha_eliminacion`),
  KEY `idx_app_log_elim_nota`  (`nota_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Impresiones de notas (AD3: errores de impresión = Procesadas sin log).
CREATE TABLE IF NOT EXISTS `app_log_impresiones` (
  `id`               BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `nota_id`          VARCHAR(40) NOT NULL,
  `usuario`          VARCHAR(80) NOT NULL DEFAULT '',
  `fecha_impresion`  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY `idx_app_log_imp_nota`  (`nota_id`),
  KEY `idx_app_log_imp_fecha` (`fecha_impresion`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Consultas de clientes (AD7: últimas consultas, filtro 2 meses).
CREATE TABLE IF NOT EXISTS `app_log_consultas` (
  `id`              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `co_cli`          VARCHAR(20)  NOT NULL,
  `usuario`         VARCHAR(80)  NOT NULL DEFAULT '',
  `fecha_consulta`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `detalle`         VARCHAR(255) NULL DEFAULT NULL,
  KEY `idx_app_log_consultas_cli`    (`co_cli`),
  KEY `idx_app_log_consultas_fecha`  (`fecha_consulta`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Notas de gestión (AD1: CRUD de notas Pendiente creadas desde ARA).
CREATE TABLE IF NOT EXISTS `app_notas_gestion` (
  `id`            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `doc_num`       VARCHAR(40)   NOT NULL,
  `co_cli`        VARCHAR(20)   NOT NULL,
  `cli_des`       VARCHAR(200)  NULL DEFAULT NULL,
  `fecha_emision` DATETIME      NULL DEFAULT NULL,
  `vendedor`      VARCHAR(20)   NULL DEFAULT NULL,
  `estado`        VARCHAR(20)   NOT NULL DEFAULT 'Pendiente',  -- Pendiente|Modificado|Procesado
  `total_items`   INT           NOT NULL DEFAULT 0,
  `total_monto`   DECIMAL(18,2) NOT NULL DEFAULT 0,
  `creada_por`    VARCHAR(80)   NOT NULL DEFAULT '',
  `creada_en`     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY `idx_app_notas_gestion_estado` (`estado`),
  KEY `idx_app_notas_gestion_doc`    (`doc_num`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Renglones de las notas de gestión (AD1).
CREATE TABLE IF NOT EXISTS `app_notas_gestion_items` (
  `id`              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `nota_id`         BIGINT UNSIGNED NOT NULL,
  `co_art`          VARCHAR(30)   NOT NULL,
  `art_des`         VARCHAR(200)  NULL DEFAULT NULL,
  `cantidad`        DECIMAL(18,2) NOT NULL DEFAULT 0,
  `precio_unitario` DECIMAL(18,4) NOT NULL DEFAULT 0,
  `total_linea`     DECIMAL(18,4) NOT NULL DEFAULT 0,
  KEY `idx_app_notas_gestion_items_nota` (`nota_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

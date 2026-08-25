-- ============================================================================
-- Proyecto ARA — NVIDIA BRAIN (Módulo de Tecnología)
-- sp_IA_Obtener_Metricas
-- ----------------------------------------------------------------------------
-- Procedimiento almacenado de métricas operativas para TecnologiaAdapter.
-- Se ejecuta SOLO LECTURA sobre el esquema real de Profit Plus (CRISTM25),
-- verificado contra INFORMATION_SCHEMA (2026-08-07):
--   not_ent.fec_emis smalldatetime | anulada bit | fact_num int
--   factura.fec_emis smalldatetime | anulada bit | fact_num int
--   clientes.co_cli char | inactivo bit
--
-- Conjuntos de resultados (en orden; TecnologiaAdapter los itera con
-- nextRowset() y sanitiza con sanitizar_utf8()):
--   1. estatus_servicio : 'OK' + base de datos, fecha del servidor y usuario.
--   2. notas_del_dia    : conteo de notas de entrega del día (no anuladas).
--   3. facturas_del_dia : conteo de facturas del día (no anuladas).
--   4. clientes         : total de clientes y clientes inactivos.
--
-- Uso:
--   EXEC sp_IA_Obtener_Metricas
-- ============================================================================
IF OBJECT_ID('dbo.sp_IA_Obtener_Metricas', 'P') IS NOT NULL
    DROP PROCEDURE dbo.sp_IA_Obtener_Metricas;
GO

CREATE PROCEDURE dbo.sp_IA_Obtener_Metricas
AS
BEGIN
    SET NOCOUNT ON;

    -- 1) Estatus del servicio (campo esperado por el criterio de aceptación).
    SELECT
        'OK'                       AS estatus_servicio,
        DB_NAME()                  AS base_datos,
        GETDATE()                  AS fecha_servidor,
        CAST(GETDATE() AS DATE)    AS fecha_operacion,
        SYSTEM_USER                AS usuario_conexion;

    -- 2) Métrica de notas del día (not_ent, no anuladas).
    SELECT
        CAST(GETDATE() AS DATE) AS fecha,
        COUNT(*)                AS notas_del_dia
    FROM dbo.not_ent
    WHERE CAST(fec_emis AS DATE) = CAST(GETDATE() AS DATE)
      AND anulada = 0;

    -- 3) Facturas del día (factura, no anuladas).
    SELECT
        CAST(GETDATE() AS DATE) AS fecha,
        COUNT(*)                AS facturas_del_dia
    FROM dbo.factura
    WHERE CAST(fec_emis AS DATE) = CAST(GETDATE() AS DATE)
      AND anulada = 0;

    -- 4) Cartera de clientes registrados.
    SELECT
        COUNT(*) AS total_clientes,
        SUM(CASE WHEN inactivo = 1 THEN 1 ELSE 0 END) AS clientes_inactivos
    FROM dbo.clientes;
END;
GO

-- Verificación manual:
--   EXEC dbo.sp_IA_Obtener_Metricas;
-- El primer conjunto debe devolver estatus_servicio = 'OK'.

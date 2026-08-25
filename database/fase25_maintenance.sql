-- ============================================================================
-- Proyecto ARA — FASE 2.5: MANTENIMIENTO DE CONEXIONES SQL (PRUEB25)
-- ----------------------------------------------------------------------------
-- Hardening anti-zombi a nivel de servidor SQL Server, complementario al
-- candado anti-zombi PHP (Fase 2.5 T1) y al query timeout de ConnectionWrapper
-- (Fase 2.5 T2).
--
-- Contenido:
--   TABLA      log_Fase25_Kills         Auditoría de conexiones dormidas/kill.
--   PROCEDURE  sp_Fase25_MonitorConnections  Lista conexiones activas/dormidas.
--   PROCEDURE  sp_Fase25_KillSleeping        Mata conexiones dormidas > umbral.
--   PROCEDURE  sp_Fase25_AlertThreshold      Umbral de alerta (default 25).
--
-- Idempotente: todos los objetos se recrean (DROP IF EXISTS + CREATE), por lo
-- que ejecutarlo varias veces NO produce errores ni duplica datos (la tabla de
-- log acumula kills reales; si se desea limpiar, TRUNCATE manual).
--
-- Uso (en PRUEB25, como 'profit' o con permisos KILL):
--   sqlcmd -S 192.168.4.20 -d PRUEB25 -U profit -P profit -i fase25_maintenance.sql
--   EXEC dbo.sp_Fase25_MonitorConnections;
--   EXEC dbo.sp_Fase25_KillSleeping @max_sleep_s = 600;   -- kill dormidas > 10 min
--   EXEC dbo.sp_Fase25_AlertThreshold @umbral = 25;       -- valor por defecto
-- ============================================================================
USE [PRUEB25];
GO

-- ── TABLA DE AUDITORÍA ─────────────────────────────────────────────────────
IF OBJECT_ID('dbo.log_Fase25_Kills', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.log_Fase25_Kills
    (
        id               INT IDENTITY(1,1) NOT NULL,
        spid             SMALLINT         NOT NULL,
        hostname         NVARCHAR(128)    NULL,
        loginname        NVARCHAR(128)    NULL,
        bd               NVARCHAR(128)    NULL,
        estado           NVARCHAR(32)     NULL,
        sleep_segundos   INT              NOT NULL,
        cmd_actual       NVARCHAR(32)     NULL,
        quien_mato       NVARCHAR(128)    NOT NULL DEFAULT SUSER_SNAME(),
        fecha_kill       DATETIME2        NOT NULL DEFAULT SYSDATETIME(),
        CONSTRAINT PK_log_Fase25_Kills PRIMARY KEY CLUSTERED (id)
    );
END;
GO

-- ── MONITOR: conexiones activas / dormidas ─────────────────────────────────
IF OBJECT_ID('dbo.sp_Fase25_MonitorConnections', 'P') IS NOT NULL
    DROP PROCEDURE dbo.sp_Fase25_MonitorConnections;
GO

CREATE PROCEDURE dbo.sp_Fase25_MonitorConnections
    @top INT = 100
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (@top)
        s.session_id        AS spid,
        s.host_name         AS hostname,
        s.login_name        AS loginname,
        DB_NAME(s.database_id) AS bd,
        s.status            AS estado,
        s.last_request_end_time AS ultima_peticion,
        s.is_user_process   AS es_usuario,
        DATEDIFF(SECOND, s.last_request_end_time, GETDATE()) AS inactivo_segundos,
        r.command           AS cmd_actual,
        r.wait_type         AS espera_actual
    FROM sys.dm_exec_sessions s
    LEFT JOIN sys.dm_exec_requests r
        ON r.session_id = s.session_id
    WHERE s.is_user_process = 1
    ORDER BY inactivo_segundos DESC;
END;
GO

-- ── KILL: mata conexiones dormidas por encima del umbral ───────────────────
IF OBJECT_ID('dbo.sp_Fase25_KillSleeping', 'P') IS NOT NULL
    DROP PROCEDURE dbo.sp_Fase25_KillSleeping;
GO

CREATE PROCEDURE dbo.sp_Fase25_KillSleeping
    @max_sleep_s INT = 600,
    @excluir_login NVARCHAR(128) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @spid SMALLINT;
    DECLARE @host NVARCHAR(128);
    DECLARE @login NVARCHAR(128);
    DECLARE @bd NVARCHAR(128);
    DECLARE @estado NVARCHAR(32);
    DECLARE @segundos INT;
    DECLARE @cmd NVARCHAR(32);
    DECLARE @sql NVARCHAR(512);

    DECLARE cur CURSOR LOCAL FAST_FORWARD FOR
        SELECT
            s.session_id,
            s.host_name,
            s.login_name,
            DB_NAME(s.database_id),
            s.status,
            DATEDIFF(SECOND, s.last_request_end_time, GETDATE()),
            r.command
        FROM sys.dm_exec_sessions s
        LEFT JOIN sys.dm_exec_requests r
            ON r.session_id = s.session_id
        WHERE s.is_user_process = 1
          AND s.last_request_end_time IS NOT NULL
          AND DATEDIFF(SECOND, s.last_request_end_time, GETDATE()) > @max_sleep_s
          AND (@excluir_login IS NULL OR s.login_name <> @excluir_login);

    OPEN cur;
    FETCH NEXT FROM cur INTO @spid, @host, @login, @bd, @estado, @segundos, @cmd;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        -- Auditoría ANTES de matar.
        INSERT INTO dbo.log_Fase25_Kills
            (spid, hostname, loginname, bd, estado, sleep_segundos, cmd_actual)
        VALUES
            (@spid, @host, @login, @bd, @estado, @segundos, @cmd);

        -- KILL (los SPIDs no de usuario que se cuelen se ignoran silenciosamente).
        BEGIN TRY
            SET @sql = 'KILL ' + CAST(@spid AS NVARCHAR(10));
            EXEC sp_executesql @sql;
        END TRY
        BEGIN CATCH
            -- Sin reintento: la sesión pudo morir entre el SELECT y el KILL.
            IF ERROR_NUMBER() <> 6106
                PRINT 'Fase25: no se pudo matar spid ' + CAST(@spid AS NVARCHAR(10))
                    + ' (' + CAST(ERROR_NUMBER() AS NVARCHAR(10)) + ')';
        END CATCH;

        FETCH NEXT FROM cur INTO @spid, @host, @login, @bd, @estado, @segundos, @cmd;
    END;

    CLOSE cur;
    DEALLOCATE cur;

    SELECT COUNT(*) AS kills_realizados
    FROM dbo.log_Fase25_Kills
    WHERE fecha_kill >= DATEADD(SECOND, -5, GETDATE());
END;
GO

-- ── UMBRAL DE ALERTA (default 25) ──────────────────────────────────────────
IF OBJECT_ID('dbo.sp_Fase25_AlertThreshold', 'P') IS NOT NULL
    DROP PROCEDURE dbo.sp_Fase25_AlertThreshold;
GO

CREATE PROCEDURE dbo.sp_Fase25_AlertThreshold
    @umbral INT = 25
AS
BEGIN
    SET NOCOUNT ON;

    -- Alertas típicas: muchas conexiones dormidas (> umbral) o procesos
    -- bloqueados. Devuelve 0 filas cuando todo está sano.
    SELECT
        COUNT(*) AS conexiones_sleeping_total,
        SUM(CASE WHEN DATEDIFF(SECOND, s.last_request_end_time, GETDATE()) > 600 THEN 1 ELSE 0 END) AS dormidas_mas_10min,
        SUM(CASE WHEN r.blocking_session_id > 0 THEN 1 ELSE 0 END) AS en_espera_por_lock
    FROM sys.dm_exec_sessions s
    LEFT JOIN sys.dm_exec_requests r
        ON r.session_id = s.session_id
    WHERE s.is_user_process = 1
    HAVING COUNT(*) >= @umbral;

    -- Si no hay filas de alerta, devolver un conjunto explícito 'OK'.
    IF @@ROWCOUNT = 0
    BEGIN
        SELECT 'OK' AS estado_alertas,
               CAST(@umbral AS INT) AS umbral_configurado,
               (SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1) AS conexiones_actuales;
    END;
END;
GO

-- Verificación manual:
--   EXEC dbo.sp_Fase25_MonitorConnections;
--   EXEC dbo.sp_Fase25_KillSleeping @max_sleep_s = 600;
--   EXEC dbo.sp_Fase25_AlertThreshold;   -- umbral por defecto 25

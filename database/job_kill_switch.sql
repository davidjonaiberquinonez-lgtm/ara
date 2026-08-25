-- ============================================================================
-- Proyecto ARA — HOTFIX v4.17.1: KILL SWITCH CONEXIONES FANTASMA KICKSERVER
-- ----------------------------------------------------------------------------
-- KICKSERVER (Apache) deja SPIDs sleeping acumulados que tumban el SQL Server.
-- Este script (lado SQL Server / SQL Agent) es el refuerzo defensivo a nivel
-- de servidor, complementario a bin/sql_kill_switch.php (lado PHP).
--
-- Contenido:
--   TABLA      dbo.ara_log_kills                  Auditoría de SPIDs matados.
--   PROCEDURE  dbo.sp_ara_kill_sleeping_kickserver Mata SPIDs KICKSERVER
--               sleeping > 10 min; inserta en ara_log_kills antes de KILL;
--               retorna cuántos mató.
--   JOB AGENT  ARA_KillSwitch_Kickserver          Corre el SP cada 5 minutos.
--
-- Idempotente: objetos recreados con DROP IF EXISTS + CREATE (la tabla log
-- acumula kills reales; TRUNCATE manual si se quiere limpiar).
--
-- Seguridad:
--   - SOLO host_name = 'KICKSERVER' + program_name LIKE '%Apache%'
--     (nunca otros hosts, nunca sesiones del sistema).
--   - SOLO status = 'sleeping' con dormido > 10 min (nunca running).
--   - Escribe SOLO en master/msdb (tablas del sistema de la instancia);
--     NUNCA en tablas de negocio (PRUEB25/CRISTM25).
--
-- Uso (en el SQL Server 192.168.4.20, como 'profit' o con permisos KILL):
--   sqlcmd -S 192.168.4.20 -d master -U profit -P profit -i job_kill_switch.sql
--   EXEC dbo.sp_ara_kill_sleeping_kickserver;        -- opción manual
--   SELECT * FROM dbo.ara_log_kills ORDER BY id DESC; -- auditoría
--
-- SI NO HAY PERMISOS DE SQL AGENT:
--   Comente (o elimine) TODO el bloque marcado "── JOB AGENT ──" al final;
--   la tabla y el SP quedan disponibles como opción manual, y se puede
--   programar bin/sql_kill_switch.php en el Programador de Tareas de Windows.
-- ============================================================================
USE [master];
GO

-- ── TABLA DE AUDITORÍA ─────────────────────────────────────────────────────
IF OBJECT_ID('dbo.ara_log_kills', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ara_log_kills
    (
        id            INT IDENTITY(1,1) NOT NULL,
        session_id    SMALLINT         NOT NULL,
        host_name     NVARCHAR(128)    NULL,
        program_name  NVARCHAR(128)    NULL,
        db_name       NVARCHAR(128)    NULL,
        dormido_seg   INT              NOT NULL,
        killed_at     DATETIME2        NOT NULL DEFAULT SYSDATETIME(),
        CONSTRAINT PK_ara_log_kills PRIMARY KEY CLUSTERED (id)
    );
END;
GO

-- ── SP: MATAR SPIDs KICKSERVER DORMIDOS ─────────────────────────────────────
IF OBJECT_ID('dbo.sp_ara_kill_sleeping_kickserver', 'P') IS NOT NULL
    DROP PROCEDURE dbo.sp_ara_kill_sleeping_kickserver;
GO

CREATE PROCEDURE dbo.sp_ara_kill_sleeping_kickserver
    @min_dormido_min INT = 10
AS
BEGIN
    SET NOCOUNT ON;

    -- 1) Captura los candidatos en una tabla temporal (evita re-escaneos).
    SELECT
        s.session_id AS spid,
        s.host_name  AS host_name,
        s.program_name AS program_name,
        DB_NAME(s.database_id) AS db_name,
        DATEDIFF(SECOND, s.last_request_start_time, GETDATE()) AS dormido_seg
    INTO #kickserver_fantasmas
    FROM sys.dm_exec_sessions s
    WHERE s.host_name = 'KICKSERVER'
      AND s.program_name LIKE '%Apache%'
      AND s.status = 'sleeping'
      AND DATEDIFF(MINUTE, s.last_request_start_time, GETDATE()) > @min_dormido_min
      AND s.session_id <> @@SPID;

    DECLARE @matados INT = 0;
    DECLARE @spid SMALLINT, @host NVARCHAR(128), @prog NVARCHAR(128),
            @db NVARCHAR(128), @dormido INT;
    DECLARE @sql NVARCHAR(100);

    DECLARE cur_fantasma CURSOR LOCAL FAST_FORWARD FOR
        SELECT spid, host_name, program_name, db_name, dormido_seg
        FROM #kickserver_fantasmas;

    OPEN cur_fantasma;
    FETCH NEXT FROM cur_fantasma INTO @spid, @host, @prog, @db, @dormido;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        -- 2) Auditoría ANTES de matar.
        INSERT INTO dbo.ara_log_kills
            (session_id, host_name, program_name, db_name, dormido_seg)
        VALUES
            (@spid, @host, @prog, @db, @dormido);

        -- 3) KILL con manejo de error: la sesión pudo morir entre la captura
        --    y el KILL (error 6106/2084): se ignora y se continúa.
        BEGIN TRY
            SET @sql = 'KILL ' + CONVERT(VARCHAR(10), @spid);
            EXEC sys.sp_executesql @sql;
            SET @matados = @matados + 1;
        END TRY
        BEGIN CATCH
            -- Sin acción: la sesión ya no existe o no se puede matar.
        END CATCH

        FETCH NEXT FROM cur_fantasma INTO @spid, @host, @prog, @db, @dormido;
    END;

    CLOSE cur_fantasma;
    DEALLOCATE cur_fantasma;

    DROP TABLE #kickserver_fantasmas;

    -- 4) Retorna cuántos mató.
    SELECT @matados AS kills;
END;
GO

-- ── JOB AGENT (requiere permisos SQLAgentOperatorRole) ─────────────────────
-- Si no hay permisos de SQL Agent, comente TODO este bloque: el SP queda como
-- opción manual y el kill switch PHP puede programarse en el Programador de
-- Tareas de Windows.
-- ═══════════════════════════════════════════════════════════════════════════
IF EXISTS (SELECT 1 FROM msdb.dbo.sysjobs WHERE name = 'ARA_KillSwitch_Kickserver')
BEGIN
    EXEC msdb.dbo.sp_delete_job @job_name = N'ARA_KillSwitch_Kickserver';
END;
GO

EXEC msdb.dbo.sp_add_job
    @job_name = N'ARA_KillSwitch_Kickserver',
    @enabled = 1,
    @description = N'Kill Switch defensivo (HOTFIX v4.17.1): mata SPIDs KICKSERVER dormidos > 10 min vía sp_ara_kill_sleeping_kickserver. NO toca datos de negocio.',
    @category_name = N'Database Maintenance';
GO

EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'ARA_KillSwitch_Kickserver',
    @step_name = N'Kill sleeping KICKSERVER',
    @subsystem = N'TSQL',
    @command = N'EXEC dbo.sp_ara_kill_sleeping_kickserver @min_dormido_min = 10;',
    @database_name = N'master',
    @retry_attempts = 0;
GO

EXEC msdb.dbo.sp_add_schedule
    @schedule_name = N'ARA_KillSwitch_Kickserver_Every5Min',
    @freq_type = 4,                -- diario
    @freq_interval = 1,
    @freq_subday_type = 4,         -- cada N minutos
    @freq_subday_interval = 5,     -- cada 5 minutos
    @active_start_time = 0;        -- desde medianoche
GO

EXEC msdb.dbo.sp_attach_schedule
    @job_name = N'ARA_KillSwitch_Kickserver',
    @schedule_name = N'ARA_KillSwitch_Kickserver_Every5Min';
GO

EXEC msdb.dbo.sp_add_jobserver
    @job_name = N'ARA_KillSwitch_Kickserver';
GO

-- Verificación rápida (opcional):
--   EXEC dbo.sp_ara_kill_sleeping_kickserver;
--   SELECT TOP 20 * FROM dbo.ara_log_kills ORDER BY id DESC;
--   SELECT name, enabled FROM msdb.dbo.sysjobs WHERE name = 'ARA_KillSwitch_Kickserver';

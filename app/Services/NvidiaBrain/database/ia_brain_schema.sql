/*
 * =============================================================================
 * NVIDIA BRAIN — Esquema de Persistencia (SQL Server)
 * Proyecto ARA · ERP Droguería
 * -----------------------------------------------------------------------------
 * Tablas:
 *   1. IA_Conversaciones         → sesiones de conversación por usuario/módulo
 *   2. IA_Mensajes               → historial de mensajes (roles + tool calls)
 *   3. IA_Permisos_Roles         → matriz de herramientas permitidas por rol
 *   4. IA_Logs                   → auditoría/métricas de cada ejecución del agente
 * Stored Procedure:
 *   sp_IA_Obtener_Historial      → historial reciente de una conversación
 *
 * Idempotente: cada bloque valida la existencia del objeto antes de crear.
 * =============================================================================
 */

-- 1. TABLA: IA_Conversaciones
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[IA_Conversaciones]') AND type in (N'U'))
BEGIN
    CREATE TABLE [dbo].[IA_Conversaciones](
        [ID_Conversacion] [BIGINT] IDENTITY(1,1) NOT NULL,
        [ID_Usuario] [INT] NOT NULL,
        [Modulo] [VARCHAR](50) NOT NULL,
        [Ruta_ID] [INT] NULL,
        [Titulo_Sesion] [NVARCHAR](150) NULL,
        [Estado] [VARCHAR](20) DEFAULT 'ACTIVO',
        [Fecha_Creacion] [DATETIME] DEFAULT GETDATE(),
        [Ultima_Actividad] [DATETIME] DEFAULT GETDATE(),
        CONSTRAINT [PK_IA_Conversaciones] PRIMARY KEY CLUSTERED ([ID_Conversacion] ASC)
    );
    CREATE NONCLUSTERED INDEX [IX_IA_Conversaciones_Usuario_Modulo]
    ON [dbo].[IA_Conversaciones] ([ID_Usuario], [Modulo], [Estado]);
END
GO

-- 2. TABLA: IA_Mensajes
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[IA_Mensajes]') AND type in (N'U'))
BEGIN
    CREATE TABLE [dbo].[IA_Mensajes](
        [ID_Mensaje] [BIGINT] IDENTITY(1,1) NOT NULL,
        [ID_Conversacion] [BIGINT] NOT NULL,
        [Rol] [VARCHAR](20) NOT NULL,
        [Contenido] [NVARCHAR](MAX) NULL,
        [Tool_Calls_JSON] [NVARCHAR](MAX) NULL,
        [Tool_Call_ID] [VARCHAR](100) NULL,
        [Fecha_Registro] [DATETIME] DEFAULT GETDATE(),
        CONSTRAINT [PK_IA_Mensajes] PRIMARY KEY CLUSTERED ([ID_Mensaje] ASC),
        CONSTRAINT [FK_IA_Mensajes_Conversaciones] FOREIGN KEY([ID_Conversacion])
            REFERENCES [dbo].[IA_Conversaciones] ([ID_Conversacion]) ON DELETE CASCADE
    );
    CREATE NONCLUSTERED INDEX [IX_IA_Mensajes_Conversacion]
    ON [dbo].[IA_Mensajes] ([ID_Conversacion], [ID_Mensaje] ASC);
END
GO

-- 3. TABLA: IA_Permisos_Roles
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[IA_Permisos_Roles]') AND type in (N'U'))
BEGIN
    CREATE TABLE [dbo].[IA_Permisos_Roles](
        [ID_Permiso] [INT] IDENTITY(1,1) NOT NULL,
        [Rol_Sistema] [VARCHAR](50) NOT NULL,
        [Modulo] [VARCHAR](50) NOT NULL,
        [Herramientas_Permitidas_JSON] [NVARCHAR](MAX) NOT NULL,
        [Max_Tokens_Por_Query] [INT] DEFAULT 1024,
        [Permite_Crear_Archivos] [BIT] DEFAULT 0,
        CONSTRAINT [PK_IA_Permisos_Roles] PRIMARY KEY CLUSTERED ([ID_Permiso] ASC)
    );

    INSERT INTO [dbo].[IA_Permisos_Roles] ([Rol_Sistema], [Modulo], [Herramientas_Permitidas_JSON], [Permite_Crear_Archivos])
    VALUES
    ('VENDEDOR', 'VENTAS_RUTAS', '["buscar_inventario", "consultar_saldo_cliente", "crear_borrador_pedido"]', 0),
    ('FINANZAS', 'FINANZAS_CONCILIACION', '["leer_voucher_ocr", "conciliar_factura", "crear_reporte_archivo"]', 1),
    ('ADMINISTRADOR', 'TODOS', '["*"]', 1);
END
GO

-- 4. TABLA: IA_Logs
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[IA_Logs]') AND type in (N'U'))
BEGIN
    CREATE TABLE [dbo].[IA_Logs](
        [ID_Log] [BIGINT] IDENTITY(1,1) NOT NULL,
        [ID_Conversacion] [BIGINT] NULL,
        [ID_Usuario] [INT] NOT NULL,
        [Modulo] [VARCHAR](50) NOT NULL,
        [Herramienta_Ejecutada] [VARCHAR](100) NULL,
        [Tiempo_Respuesta_MS] [INT] NOT NULL,
        [Iteraciones_Usadas] [INT] DEFAULT 1,
        [Status_Code] [VARCHAR](20) NOT NULL,
        [Error_Detalle] [NVARCHAR](MAX) NULL,
        [Fecha_Registro] [DATETIME] DEFAULT GETDATE(),
        CONSTRAINT [PK_IA_Logs] PRIMARY KEY CLUSTERED ([ID_Log] ASC)
    );
    CREATE NONCLUSTERED INDEX [IX_IA_Logs_Fecha_Status]
    ON [dbo].[IA_Logs] ([Fecha_Registro], [Status_Code]);
END
GO

-- 5. STORED PROCEDURE: Historial de Conversación
CREATE OR ALTER PROCEDURE [dbo].[sp_IA_Obtener_Historial]
    @ID_Conversacion BIGINT,
    @LimiteMensajes INT = 10
AS
BEGIN
    SET NOCOUNT ON;
    SELECT Rol, Contenido, Tool_Calls_JSON, Tool_Call_ID
    FROM (
        SELECT TOP (@LimiteMensajes) [ID_Mensaje], [Rol], [Contenido], [Tool_Calls_JSON], [Tool_Call_ID]
        FROM [dbo].[IA_Mensajes]
        WHERE [ID_Conversacion] = @ID_Conversacion
        ORDER BY [ID_Mensaje] DESC
    ) AS Sub
    ORDER BY Sub.[ID_Mensaje] ASC;
END
GO

# Diagnóstico de Escalabilidad y Performance — Proyecto ARA

## 1. Configuración del Servidor Web

**Servidor:** Waitress (producción); Flask nativo comentado (desarrollo).

**Archivo:** `ara_server.py:1804-1806`

```python
from waitress import serve
serve(app, host=HOST_BIND, port=PUERTO)
```

| Parámetro          | Valor actual                  | Problema detectado                                      |
|--------------------|-------------------------------|---------------------------------------------------------|
| `threads`          | No especificado               | Waitress usa **4 hilos por defecto**. Sin `threads=N`, el servidor solo atiende **4 requests simultáneas**. Con ~37 endpoints y llamadas externas bloqueantes de 10-30s, se agota el pool con pocos usuarios. |
| `host`             | `0.0.0.0`                    | Correcto para cloudflared / LAN.                        |
| `port`             | `5000`                        | -                                                       |
| `channel_timeout`  | No especificado               | Waitress default (~120s). Sin tuning de keep-alive.     |
| `max_request_body_size` | No especificado           | Default 1 GB — aceptable.                               |
| `recv_bytes`       | No especificado               | Default 8192 — bajo para subidas de foto en visión.     |

### Impacto
- Si 4 usuarios abren el visor de artículos simultáneamente, cada request bloquea un hilo ~3-15s (NVIDIA NIM). El 5to usuario espera hasta que se libere un hilo.
- Las respuestas de long-polling de chat (hasta 30s por request) también consumen hilos de Waitress de forma permanente.

---

## 2. Manejo de Conexiones SQLite

### 2.1 Modo WAL (Write-Ahead Logging)

**NO está activado.** No se encontró `PRAGMA journal_mode=WAL` en ningún archivo del proyecto.

### 2.2 Patrón de conexiones

Cada endpoint abre y cierra conexiones de forma individual. No hay pool, no hay singleton, no hay reuso.

**Ejemplos representativos:**

| Archivo            | Línea(s)     | Conexión                              | Cierre             |
|--------------------|--------------|---------------------------------------|--------------------|
| `ara_server.py`    | 78, 95       | `sqlite3.connect(DB_PATH)`            | `.close()` manual  |
| `ara_server.py`    | 105, 146     | `get_db_connection()`                 | `.close()` manual  |
| `ara_server.py`    | 168, 181     | `sqlite3.connect(ruta_db)`            | `.close()` manual  |
| `ara_vision.py`    | 64, 154      | `sqlite3.connect(DB_PATH)`            | `.close()` en `finally` |
| `ara_vision.py`    | 161, 178     | `sqlite3.connect(DB_PATH)`            | `.close()` en `finally` |
| `chat_routes.py`   | 51, 77       | `sqlite3.connect(DB_PATH)`            | `.close()` manual  |
| `chat_routes.py`   | 385, 398     | `sqlite3.connect(DB_PATH)`            | `.close()` bajo `_DB_LOCK` |

### 2.3 Parámetros de conexión faltantes

- **`timeout`**: Ninguna llamada a `sqlite3.connect()` especifica timeout. Si hay contención (`SQLITE_BUSY`), la excepción se lanza inmediatamente (default timeout = 0).
- **`check_same_thread=False`**: Ninguna conexión lo usa. Aunque Flask/Waitress usa hilos, esto puede causar `SQLite objects created in a thread can only be used in that same thread` si algún endpoint pasa la conexión entre hilos.

### 2.4 Lock global

`chat_routes.py:33` — `_DB_LOCK = Lock()` — es un candado de módulo para serializar **todas las escrituras** en chat_routes. Sin embargo:

- **No cubre escrituras de `ara_server.py`**: los endpoints como `/api/inventario/registrar`, `/api/notificar_faltante`, etc. escriben en SQLite sin adquirir `_DB_LOCK`.
- **Es un lock de módulo, no de base de datos**: serializa operaciones de chat_routes pero no protege contra escrituras concurrentes de otros módulos.

### Impacto
- Sin WAL: las lecturas bloquean escrituras y viceversa. Una consulta pesada (dashboard, reportes) bloquea escrituras concurrentes.
- Sin timeout: cualquier contención lanza `SQLITE_BUSY` inmediatamente, causando errores 500 en producción.
- Sin pool: cada request paga el overhead de abrir/cerrar conexión (syscall + file lock).

---

## 3. Puntos Críticos de Concurrencia

### 3.1 Llamadas bloqueantes a APIs externas

| Endpoint / Función                          | Llamada ext.           | Timeout    | ¿Bloquea Waitress? |
|---------------------------------------------|------------------------|------------|--------------------|
| `procesar_imagen_visor` → `_llamar_nim_vision` | NVIDIA NIM Vision   | 15s        | **Sí** — hilo de Waitress bloqueado |
| `procesar_imagen_visor` → `_llamar_ollama_vision` (fallback) | Ollama LLaVA | 30s | **Sí** — hilo de Waitress bloqueado |
| `_procesar_respuesta_ara_bot_async` → `_llamar_nim_ara_bot` | NVIDIA NIM Chat | 10s | **No** — ejecutado en `threading.Thread` separado |
| `_procesar_respuesta_ara_bot_async` → `_llamar_ollama_para_bot` | Ollama phi3 | 15s | **No** — ejecutado en `threading.Thread` separado |

### 3.2 Lecturas pesadas a base de datos

| Endpoint                     | Consulta                                    | Escalabilidad          |
|------------------------------|---------------------------------------------|------------------------|
| `/api/dashboard/stats`       | 4+ consultas secuenciales (métricas, ranking, gráfico, incidencias) | **Alta latencia**. 4 consultas pesadas (includes `GROUP BY`, `JOIN`, `ORDER BY`) sin paginación. |
| `/api/preparacion`           | `LIKE` sobre `codigo`/`codigo_barra`/`descripcion` con `LIMIT 100` | Aceptable con índices. Sin índices → full scan. |
| `/api/chat/poll`             | Polling cada ~1s por hasta 30s, manteniendo conexión SQLite abierta | **Crítico**. Un hilo de Waitress ocupado 30s haciendo polling, imposibilitando atender 30 requests entrantes. |

### 3.3 Contención de escritura

| Sección                         | Lock usado        | Riesgo                                          |
|---------------------------------|-------------------|--------------------------------------------------|
| `chat_routes.py` — enviar msg   | `_DB_LOCK`        | Serializa todos los envíos de mensajes           |
| `chat_routes.py` — webhook      | `_DB_LOCK`        | Serializa todos los webhooks                     |
| `ara_server.py` — inventario    | **Sin lock**      | Escritura concurrente sin protección → corrupción |
| `ara_server.py` — login         | **Sin lock**      | Lectura-escritura de usuarios sin protección     |

---

## 4. Fragmento de Código — Arranque del Servidor y Endpoints

```python
# ara_server.py — líneas relevantes

if __name__ == '__main__':
    PUERTO = 5000
    HOST_BIND = '0.0.0.0'

    # Opción A: Waitress (Producción) — SIN argumento threads=
    from waitress import serve
    serve(app, host=HOST_BIND, port=PUERTO)

    # Opción B: Flask nativo (Desarrollo) — comentado
    # app.run(host=HOST_BIND, port=PUERTO, debug=False)

# Total endpoints registrados:
#   ara_server.py  → 31 rutas (@app.route)
#   chat_routes.py →  6 rutas (registradas vía register_chat_routes)
#   pdf_route.py   →  1 ruta  (registrada vía register_pdf_route)
#   ───────────────────────────────────
#   Total:           38 endpoints
```

### Endpoints bloqueantes (hasta ~30s por request):
| Método | Ruta                              | Archivo origen       |
|--------|-----------------------------------|----------------------|
| POST   | `/api/vision/escanear`            | `ara_server.py:1725` |
| GET    | `/api/chat/poll`                  | `chat_routes.py:745` |
| POST   | `/api/chat/enviar`                | `chat_routes.py:625` |
| GET    | `/api/dashboard/stats`            | `ara_server.py:393`  |
| GET    | `/api/preparacion`                | `ara_server.py:1280` |

---

## 5. Resumen de Riesgos

| # | Riesgo                               | Severidad | Impacto                                                                 |
|---|--------------------------------------|-----------|-------------------------------------------------------------------------|
| 1 | Waitress sin `threads=` (default 4)  | 🔴 Alta   | Con 4 hilos y 3 llamadas externas bloqueantes simultáneas, el servidor satura. |
| 2 | SQLite sin WAL                       | 🔴 Alta   | Lecturas bloquean escrituras → contención → `SQLITE_BUSY`.               |
| 3 | `sqlite3.connect()` sin `timeout`    | 🔴 Alta   | Cualquier contención lanza error inmediato (500).                        |
| 4 | Sin connection pool                  | 🟡 Media  | Overhead de ~10ms por request solo en abrir/cerrar DB.                  |
| 5 | Long-polling bloquea hilo 30s        | 🔴 Alta   | Un hilo de Waitress ocupado 30s en `/api/chat/poll` → pool agotado.     |
| 6 | Lock parcial (`_DB_LOCK` solo en chat_routes) | 🟡 Media | Escrituras concurrentes en otros módulos sin protección.                |
| 7 | Sin `check_same_thread=False`        | 🟢 Baja   | Riesgo latente si se refactoriza a async o pool.                         |
| 8 | Visión IA bloquea hilo 15-30s        | 🔴 Alta   | Cada escaneo ocupa un hilo completo; 4 escaneos simultáneos saturan.    |
| 9 | Dashboard sin paginación             | 🟡 Media  | `stock_maestro` puede tener 10K+ registros → full scan + GROUP BY lento.|

---

## 6. Hallazgo Adicional — `get_db_connection()` Subutilizado

`ara_server.py:104` define una función de conexión centralizada:

```python
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
```

Sin embargo, la mayoría de endpoints **no la usan**. Abren su propia conexión con `sqlite3.connect(DB_PATH)`. Esto indica falta de adherencia al helper central, lo que dificulta aplicar cambios globales (ej. activar WAL, timeout, pool) sin modificar 37+ puntos del código.

---

*Generado el 2026-07-23. Sin modificaciones de código.*

# Proyecto ARA - Registro de Contexto y Estado

### 🛡️ Punto de Restauración (Fase 4 / v2.0 - Pre-Cambios Hexagonales)

**Estado de `ara_server.py` antes de la Fase 4:**
- Sin imports a `notas_hexagonal`
- Sin endpoints `/api/vision/escanear_nota`, `/api/notas/*`, `/api/reportes/movimientos/pdf`

**Estado de `pdf_route.py` antes de la Fase 4:**
- Sin función `generar_pdf_movimientos()`
- Sin endpoint `/api/reportes/movimientos/pdf`

**Estado de `chat_routes.py` antes de la Fase 4:**
- Sin consultas a `movimientos_preparador`
- Sin variable `_INCLUDE_MOVIMIENTOS_EN_STOCK`

**Base de datos antes de la Fase 4:**
- Sin tablas `notas_entrega`, `detalle_nota`, `movimientos_preparador`

---

### 🛡️ Punto de Restauración (RBAC - Pre-Cambios)

**Menú HTML antes del update RBAC:**
- Botones existentes: `preparacion`, `chequeo`, `embalaje`, `traslados`, `rutas`, `bandeja`, `inventario`, `usuarios` (admin), `reportes`, `dashboard`
- Sin botones para `visor`, `preparacion_notas`, `notas_pruebas`, `trazabilidad`

**`MAPA_MODULOS` antes del update RBAC:**
```javascript
const MAPA_MODULOS = {
    'dashboard':    { titulo: 'Dashboard de Rendimiento', render: renderModuloDashboard },
    'usuarios':    { titulo: 'Gestión de Usuarios',       render: renderGestionUsuarios },
    'preparacion':  { titulo: 'Preparación de Pedidos',   render: renderSubMenuPreparacion },
    'traslados':    { titulo: 'Traslados Internos',       render: renderSubMenuTraslados },
    'bandeja':      { titulo: 'Bandeja de Mensajes',      render: renderModuloBandeja },
    'embalaje':     { titulo: 'Módulo de Embalaje',       render: renderModuloEmbalaje },
    'chequeo':      { titulo: 'Chequeo de Mercancía',     render: renderSubMenuChequeo },
    'rutas':        { titulo: 'Despacho y Rutas',         render: renderSubMenuRutas },
    'inventario':   { titulo: 'Módulo de Inventario',     render: renderModuloInventario },
    'reportes':     { titulo: 'Reportes y Estadísticas',  render: renderModuloReportes }
};
```

**`openModule()` antes del update RBAC:**
- Sin verificación de `window.usuarioPermisos` antes de renderizar

**Checkbox de permisos en `abrirModalCrearUsuario()` antes del update RBAC:**
- Módulos existentes: `dashboard`, `preparacion`, `chequeo`, `embalaje`, `traslados`, `rutas`, `bandeja`, `usuarios`, `reportes`, `inventario`
- Sin checkboxes para: `visor`, `preparacion_notas`, `notas_pruebas`, `trazabilidad`

**Estado de `ara_server.py` antes de la Fase 4:**
- Sin imports a `notas_hexagonal`
- Sin endpoints `/api/vision/escanear_nota`, `/api/notas/*`, `/api/reportes/movimientos/pdf`

**Estado de `pdf_route.py` antes de la Fase 4:**
- Sin función `generar_pdf_movimientos()`
- Sin endpoint `/api/reportes/movimientos/pdf`

**Estado de `chat_routes.py` antes de la Fase 4:**
- Sin consultas a `movimientos_preparador`
- Sin variable `_INCLUDE_MOVIMIENTOS_EN_STOCK`

**Base de datos antes de la Fase 4:**
- Sin tablas `notas_entrega`, `detalle_nota`, `movimientos_preparador`

---

## 1. Resumen de la Arquitectura Actual

**Sistema:** ARA Brain — Middleware de gestión de almacén / inventario con asistente virtual IA.

**Stack:**
- **Backend:** Flask + Waitress (Python 3.11)
- **Base de datos:** SQLite (`ara/ARA_Brain/data/proyecto_ara.db`)
- **Frontend:** HTML/CSS/JS vanilla embebido en `templates/index.html`
- **IA local:** Ollama (`phi3:latest` en `http://127.0.0.1:11434`)
- **IA cloud:** NVIDIA NIM (`deepseek-ai/deepseek-v4-flash` en `https://integrate.api.nvidia.com/v1/chat/completions`)
- **Túnel:** cloudflared (Cloudflare Tunnel)

**Estructura de directorios (core):**
```
ara/ARA_Brain/
├── ara_server.py          # Servidor principal (Flask)
├── chat_routes.py         # Rutas de mensajería (/api/chat/*)
├── ara_vision.py          # Módulo de visión OCR + búsqueda
├── pdf_route.py           # Generación de PDFs (ReportLab)
├── config.py              # Configuración SQL Server
├── main.py                # Entry point legacy
├── templates/
│   └── index.html         # SPA completa (Frontend)
├── data/
│   ├── proyecto_ara.db    # SQLite (stock, mensajes, usuarios, etc.)
│   └── chat_schema.sql    # Schema de tablas de chat
├── brain_knowledge/       # Historiales, reportes JSON, memoria
└── whatsapp/              # Bot de WhatsApp (whatsapp-web.js)
```

**Módulo de chat (Bandeja de Mensajes):**
- `GET /api/chat/conversaciones` — Listar chats
- `GET /api/chat/conversacion/<id>/mensajes` — Historial paginado
- `POST /api/chat/enviar` — Enviar mensaje (con interceptor asíncrono para ARA - Intelligent)
- `POST /api/chat/webhook` — Webhook entrante (WhatsApp/Telegram)
- `POST /api/chat/conversacion/<id>/leer` — Marcar como leído
- `GET /api/chat/poll` — Long-polling para nuevos mensajes

---

## 2. Registro de Cambios (Changelog)

### 🛡️ Punto de Restauración (Fase 1 - Pre-Cambios)

**Estado de `get_db_connection()` antes de la Fase 1:**
```python
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
```

**Estado del arranque de Waitress antes de la Fase 1:**
```python
from waitress import serve
serve(app, host=HOST_BIND, port=PUERTO)
```

### 🛡️ Punto de Restauración (Fase 2 - Pre-Cambios)

**Estado de `/api/vision/escanear` antes de la Fase 2:**
```python
@app.route('/api/vision/escanear', methods=['POST'])
def vision_escanear():
    """Recibe imagen, la procesa con IA vision y busca en stock_maestro."""
    import traceback as tb
    try:
        if 'image' in request.files:
            image_file = request.files['image']
            image_bytes = image_file.read()
            resultado = procesar_imagen_visor(image_bytes)
        elif request.is_json:
            data = request.get_json(silent=True)
            b64 = (data or {}).get('image', '')
            if not b64:
                return jsonify({"status": "error", "mensaje": "No se recibió imagen"}), 400
            resultado = procesar_imagen_visor(b64)
        else:
            return jsonify({"status": "error", "mensaje": "Envíe image (form-data) o image (JSON base64)"}), 400

        return jsonify(resultado)
    except Exception as e:
        tb.print_exc()
        return jsonify({"status": "error", "mensaje": str(e)}), 500
```

**Estado de `/api/dashboard/stats` antes de la Fase 2 — sin caché TTL:**
```python
@app.route('/api/dashboard/stats', methods=['GET'])
def obtener_estadisticas_dashboard():
    try:
        # ...
        conn = get_db_connection()
        cursor = conn.cursor()
        # 4 consultas pesadas secuenciales
        cursor.execute(f'''SELECT ... FROM log_puntos {where_fecha}''')
        cursor.execute(f'''SELECT ... FROM log_puntos {where_fecha} GROUP BY usuario ...''')
        cursor.execute(f'''SELECT ... FROM log_puntos {where_grafico} GROUP BY DATE ...''')
        cursor.execute(f'''SELECT ... FROM log_puntos {where_fecha} ORDER BY fecha_registro DESC LIMIT 10''')
        conn.close()
        return jsonify({...})
    except Exception as e:
        return jsonify({...}), 500
```

**Estado de `/api/chat/poll` antes de la Fase 2 (timeout=25s default, máx 30s):**
```python
timeout_s = min(request.args.get('timeout', type=int, default=25), 30)
deadline = time.time() + timeout_s
while time.time() < deadline:
    # ...
    time.sleep(1.5)
# Timeout: nada nuevo
return _ok({..., "timeout": True})
```

### 🛡️ Punto de Restauración (Fase 3 - Pre-Cambios)

**Estado de los imports de `ara_server.py` antes de la Fase 3:**
```python
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import time
```

**Estado de los globales de `ara_server.py` antes de la Fase 3:**
```python
executor_vision = ThreadPoolExecutor(max_workers=10)
_dashboard_cache = {"data": None, "timestamp": 0}
```

**Estado de `/api/vision/escanear` antes de la Fase 3 (sin rate limiter ni decorador):**
```python
@app.route('/api/vision/escanear', methods=['POST'])
def vision_escanear():
    import traceback as tb
    try:
        if 'image' in request.files:
            image_file = request.files['image']
            image_bytes = image_file.read()
            future = executor_vision.submit(procesar_imagen_visor, image_bytes)
        elif request.is_json:
            data = request.get_json(silent=True)
            b64 = (data or {}).get('image', '')
            if not b64:
                return jsonify({"status": "error", "mensaje": "No se recibió imagen"}), 400
            future = executor_vision.submit(procesar_imagen_visor, b64)
        else:
            return jsonify({"status": "error", "mensaje": "Envíe image (form-data) o image (JSON base64)"}), 400
        resultado = future.result(timeout=25)
        return jsonify(resultado)
    except concurrent.futures.TimeoutError:
        return jsonify({"status": "error", "mensaje": "La IA de visión tardó más de 25s. Intente de nuevo."}), 504
    except Exception as e:
        tb.print_exc()
        return jsonify({"status": "error", "mensaje": str(e)}), 500
```

---

### 2026-07-23 — v1.0 — Configuración inicial y asistente ARA - Intelligent

| Archivo | Cambio | Razón |
|---------|--------|-------|
| `ara_server.py` | Configurar `host='0.0.0.0'` y `port=5000` con Waitress | Permitir acceso desde cloudflared y red local |
| `chat_routes.py` | Crear `init_ara_bot()`, `_procesar_mensaje_ara_bot()`, `_es_ara_bot()` | Implementar asistente virtual inteligente |
| `chat_routes.py` | Crear `_consultar_stock_para_bot()` con búsqueda SQL flexible | Buscar productos por código/descripción |
| `chat_routes.py` | Crear `_llamar_nim_ara_bot()` y `_llamar_ollama_para_bot()` | Integrar NVIDIA NIM (primario) + Ollama (fallback) |
| `chat_routes.py` | Crear `_procesar_respuesta_ara_bot_async()` con `threading.Thread` | Evitar bloqueo de Waitress (respuesta asíncrona) |
| `chat_routes.py` | Agregar `_es_consulta_metricas()` y `_consultar_metricas_globales()` | Responder a preguntas de totales/SKUs |
| `chat_routes.py` | Optimizar payload: `num_predict=50`, `num_ctx=512`, `temperature=0.1` | Respuestas <5s en CPU |
| `chat_routes.py` | Recorte de contexto SQL a 350 caracteres | Evitar prompts largos |
| `index.html` | Corregir alineación de mensajes: `remitente='sistema'` a la izquierda | UX correcta: bot a la izquierda, usuario a la derecha |
| `index.html` | Agregar indicador "escribiendo..." animado | Feedback visual mientras el bot procesa |
| `index.html` | Badge "IA · En línea" y avatar gradiente morado para ARA Bot | Identificación visual del asistente |

### 2026-07-23 — v1.1 — Visor de artículos con IA (NVIDIA NIM Vision + Ollama LLaVA)

| Archivo | Cambio | Razón |
|---------|--------|-------|
| `ara_vision.py` | Reescribir completamente: `procesar_imagen_visor()`, `_llamar_nim_vision()`, `_llamar_ollama_vision()`, `_buscar_producto_sql()` | Pipeline visión OCR → extracción JSON → búsqueda en stock_maestro |
| `ara_server.py` | Agregar `POST /api/vision/escanear` y `from ara_vision import procesar_imagen_visor` | Endpoint para visor de artículos (acepta form-data y JSON) |

### 2026-07-23 — v1.2 — Integración frontend Visor de Artículos con `/api/vision/escanear`

| Archivo | Cambio | Razón |
|---------|--------|-------|
| `index.html` | `tomarFoto()`: captura canvas → Blob JPEG → FormData → `fetch(/api/vision/escanear)`. Renderiza ficha de producto (código, descripción, stock, ubicación) o muestra OCR en barra de búsqueda si no hay stock. Manejo de errores con toast. | Conectar cámara + IA vision + stock_maestro en un solo flujo |

### 2026-07-23 — v1.3 — Búsqueda de precisión con código de barras + historial de reubicaciones

| Archivo | Cambio | Razón |
|---------|--------|-------|
| `ara_vision.py` | `VISION_PROMPT`: agregar campos `codigo_barra` (EAN/UPC) y `dosis` al JSON extraído del modelo de visión | Permitir búsqueda exacta por código de barras y filtrar por concentración |
| `ara_vision.py` | `_buscar_producto_sql()` → `_buscar_producto_sql_vision(datos_vision)`: nuevo algoritmo de 4 pasos (A: barcode exacto, B: AND nombre+dosis+lab, C: AND nombre+dosis, D: palabra más larga) | Reemplazar búsqueda textual plana con prioridad estricta que aprovecha los campos extraídos por visión |
| `ara_vision.py` | `_adjuntar_historial_ubicaciones(productos)`: nueva función que consulta `reportes_ubicacion WHERE co_art = ? ORDER BY rowid DESC LIMIT 3` y adjunta `historial_ubicaciones` en cada producto | Mostrar al usuario las últimas reubicaciones/movimientos del artículo escaneado |
| `ara_vision.py` | `procesar_imagen_visor()`: llamar a `_adjuntar_historial_ubicaciones()` después de la búsqueda en stock | Integrar el historial en la respuesta del endpoint de visión |
| `index.html` | `tomarFoto()`: renderizar tarjeta "📍 REUBICACIONES / OTRAS UBICACIONES REGISTRADAS" con tabla de usuario, movimiento (desde ➔ hacia), y fecha debajo de la ficha del producto. Si no hay registros, muestra etiqueta sutil informativa | Proporcionar visibilidad inmediata del historial de movimientos del producto escaneado |

### 2026-07-23 — v1.4 — Fase 1 de escalabilidad: WAL, timeout y PRAGMAs en SQLite

| Archivo | Cambio | Razón |
|---------|--------|-------|
| `ara_server.py` | `get_db_connection()`: agregar `timeout=30.0`, `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA busy_timeout=5000` | Eliminar `SQLITE_BUSY`, permitir lecturas/escrituras concurrentes, reducir fsync de checkpoint |

### 2026-07-23 — v1.5 — Fase 2 de escalabilidad: visión asíncrona, caché TTL, polling optimizado

| Archivo | Cambio | Razón |
|---------|--------|-------|
| `ara_server.py` | Importar `ThreadPoolExecutor`, `concurrent.futures`, `time`. Crear `executor_vision = ThreadPoolExecutor(max_workers=10)` | Pool de 10 hilos para descargar llamadas IA bloqueantes de los hilos de Waitress |
| `ara_server.py` | `/api/vision/escanear`: ejecutar `procesar_imagen_visor()` vía `executor_vision.submit()` + `future.result(timeout=25)`. Manejar `TimeoutError` con 504 | Evitar que NVIDIA NIM (15s) u Ollama (30s) bloqueen hilos de Waitress |
| `ara_server.py` | `/api/dashboard/stats`: agregar `_dashboard_cache` global con TTL=30s. Retornar caché si <30s, re-consultar SQL y actualizar tras cada consulta fresca | Las 4 consultas pesadas corren solo cada 30s en vez de en cada request |
| `chat_routes.py` | `/api/chat/poll`: reducir timeout default de 25s a 10s, máx de 30 a 10s. Actualizar docstring | Liberar hilo de Waitress hasta 20s antes por ciclo sin mensajes |

### 2026-07-23 — v2.0 — Arquitectura Hexagonal: Notas de Entrega, Trazabilidad Atómica y Control de Concurrencia

| Archivo | Cambio | Razón |
|---------|--------|-------|
| `notas_hexagonal.py` | **Nuevo módulo.** Domain models: `NotaDomain`, `ItemDomain`, `MovimientoDomain` (dataclasses). DDL: `notas_entrega`, `detalle_nota`, `movimientos_preparador`. Servicios: visión IA para notas (`_llamar_nim_notas`, `_llamar_ollama_notas`, `NOTA_PROMPT`), CRUD (`_buscar_o_crear_nota`, `_insertar_items`, `_descontar_stock`, `_registrar_movimiento`), concurrencia (`_tomar_nota` con bloqueo por usuario), auto-chequeo (`_completar_nota` con regla 1-2 items). Endpoints registrables vía `register_notas_routes(app)`. | Implementar arquitectura hexagonal completa para el ciclo de vida de notas de entrega: desde la foto (visión) hasta la preparación, descuento de stock, trazabilidad y reportes. |
| `ara_server.py` | Importar `register_notas_routes`, `init_notas_tables` desde `notas_hexagonal`. Llamar `init_notas_tables()` y `register_notas_routes(app)` tras `chat_routes`. | Registrar todos los endpoints hexagonales y crear tablas al arrancar. |
| `chat_routes.py` | Agregar `_consultar_movimientos_para_bot(texto)`: detecta palabras clave de auditoría (movimiento, nota, quién, trazabilidad) y consulta `movimientos_preparador`. Integrar en `_procesar_mensaje_ara_bot`: si se detecta intención de trazabilidad, se inyectan datos de movimientos en el contexto SQL del bot. | ARA IA ahora responde preguntas de auditoría como "¿qué nota se llevó X medicamento?" con datos reales de trazabilidad. |
| `PROJECT_CONTEXT.md` | Sección DB actualizada con las 3 nuevas tablas. Sección Hexagonal agregada con lista completa de endpoints. | Documentación del nuevo subsistema. |

### Endpoints nuevos registrados

| Método | Ruta | Propósito |
|--------|------|-----------|
| POST | `/api/vision/escanear_nota` | Procesa foto de nota de entrega → IA extrae número, cliente e items → inserta en BD como nota real |
| POST | `/api/notas/tomar` | Bloquea nota para preparación (control de concurrencia: si otro usuario ya la tomó, retorna error 409) |
| POST | `/api/notas/completar` | Finaliza preparación, descuenta stock, aplica auto-chequeo si 1-2 items |
| GET | `/api/notas/lista` | Lista notas reales con filtro opcional `?estado=` |
| GET/POST | `/api/notas/pruebas` | GET lista notas de prueba; POST crea nota de prueba aislada del stock real |
| GET | `/api/notas/detalle/<id>` | Obtiene encabezado + items de una nota |
| GET | `/api/trazabilidad/movimientos` | Consulta trazabilidad con filtros `co_art`, `usuario`, `fecha_inicio`, `fecha_fin` |
| GET/POST | `/api/reportes/movimientos/pdf` | Genera PDF descargable de movimientos para un artículo en rango de fechas |

### 2026-07-23 — v2.1 — RBAC: Permisos granulares, migración automática e integración de módulos v2.0

| Archivo | Cambio | Razón |
|---------|--------|-------|
| `ara_server.py` | Agregar `POST /api/usuarios/actualizar_permisos` — endpoint para actualizar solo la columna `permisos` de un usuario | Permitir edición granular de permisos desde la UI de Gestión de Usuarios sin modificar otros campos |
| `ara_server.py` | Agregar `migrar_permisos_usuarios()` — migración automática al arranque: asegura columna `permisos`, asigna `["*"]` a admins y módulos básicos `["visor","preparacion_notas","notas_pruebas","trazabilidad","dashboard",...]` a usuarios sin permisos | Garantizar que todos los usuarios existentes tengan permisos definidos sin intervención manual |
| `index.html` | Menú: agregar 4 botones nuevos `visor`, `preparacion_notas`, `notas_pruebas`, `trazabilidad` | Navegación a los nuevos módulos v2.0 |
| `index.html` | `MAPA_MODULOS`: agregar las 4 nuevas entradas con títulos y funciones `renderModuloVisor`, `renderModuloPrepNotas`, `renderModuloNotasPruebas`, `renderModuloTrazabilidad` | Enrutamiento de los nuevos módulos |
| `index.html` | `openModule()`: agregar verificación RBAC — si `window.usuarioPermisos` no contiene el módulo (y no es `*`), muestra alerta "Acceso Restringido" y retorna al menú | Bloquear navegación a módulos no autorizados |
| `index.html` | `aplicarPermisos()`: soportar wildcard `["*"]` para administradores (muestra todos los botones) | Admin con permiso total no necesita lista exhaustiva |
| `index.html` | `abrirModalCrearUsuario()`: agregar 4 checkboxes nuevos `visor`, `preparacion_notas`, `notas_pruebas`, `trazabilidad` (pre-marcados por defecto) | El admin puede otorgar/revocar los nuevos módulos desde la creación de usuarios |
| `index.html` | 4 nuevas funciones `renderModuloVisor`, `renderModuloPrepNotas`, `renderModuloNotasPruebas`, `renderModuloTrazabilidad` + helpers `cargarNotasPendientes`, `listarNotasPruebas`, `crearNotaPrueba`, `abrirTrazabilidadMovimientos`, `abrirPdfMovimientos` | Interfaces funcionales que conectan con los endpoints de la v2.0 |

---

## 3. Estado Actual y Configuraciones Clave

### Servidor
- **Host:** `0.0.0.0` (todas las interfaces)
- **Puerto:** `5000`
- **Servidor WSGI:** Waitress (producción Windows); Gunicorn+gevent (producción Linux)
- **Server alternativo:** Flask nativo (comentado)
- **Health:** `GET /api/health` (status, DB, cola de visión, uptime)
- **Rate limit visión:** 15 req/min/IP (429 si excede)

### Base de datos
- **Engine:** SQLite 3
- **Archivo:** `ara/ARA_Brain/data/proyecto_ara.db`
- **Tablas principales:** `contactos`, `conversaciones`, `mensajes`, `stock_maestro`, `usuarios`, `facturas`, `log_puntos`, `inventario_progreso`, `reportes_ubicacion`, `notas_entrega`, `detalle_nota`, `movimientos_preparador`
- **Lock de escritura:** `_DB_LOCK = Lock()` (evita concurrencia en SQLite)
- **PRAGMAs activos (Fase 1):** `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `timeout=30.0` en `get_db_connection()`

### Arquitectura Hexagonal (v2.0)

| Entidad | Archivo | Propósito |
|---------|---------|-----------|
| `NotaDomain` | `notas_hexagonal.py` | Dataclass: número_nota, cliente, estado (pendiente/preparando/completada), preparador_id, es_prueba, items_count, auto_chequeado |
| `ItemDomain` | `notas_hexagonal.py` | Dataclass: nota_id, co_art, descripcion, cantidad_solicitada/preparada, unidad_medida (UND/CAJA/SOBRE/BLISTER), estado |
| `MovimientoDomain` | `notas_hexagonal.py` | Dataclass: nota_id, co_art, descripcion, cantidad, unidad_medida, usuario, accion, origen, destino, timestamp |

**Servicios:** Visión IA para notas (`NOTA_PROMPT` + NVIDIA NIM / Ollama fallback), CRUD completo, bloqueo concurrente por usuario, descuento de stock en `stock_maestro`, regla de auto-chequeo (1-2 items), PDF de trazabilidad con ReportLab.

### Asistente ARA - Intelligent
- **Contacto BD:** `telefono='ara_bot'`, `nombre='ARA - Intelligent'`
- **IA Primaria:** NVIDIA NIM — `deepseek-ai/deepseek-v4-flash` (cloud, timeout 10s)
- **IA Fallback:** Ollama — `phi3:latest` (local, timeout 15s)
- **Fallback final:** Respuesta SQL directa formateada
- **Ejecución:** Asíncrona en `threading.Thread` daemon

### NVIDIA NIM
- **URL:** `https://integrate.api.nvidia.com/v1/chat/completions`
- **Modelo:** `deepseek-ai/deepseek-v4-flash`
- **max_tokens:** 80 | **temperature:** 0.1

### Ollama
- **URL (texto):** `http://127.0.0.1:11434/api/generate`
- **Modelo texto:** `phi3:latest`
- **URL (visión):** `http://127.0.0.1:11434/api/generate`
- **Modelo visión:** `llava`
- **num_predict:** 50 | **num_ctx:** 512 | **temperature:** 0.1

### Visor de Artículos (Visión IA)
- **Motor primario:** NVIDIA NIM — `meta/llama-3.2-11b-vision-instruct`
- **Motor fallback:** Ollama — `llava`
- **Prompt visión:** Extrae JSON con `codigo_barra`, `codigo`, `descripcion`, `laboratorio`, `dosis`, `lote`, `fecha_vencimiento`
- **Endpoint:** `POST /api/vision/escanear` (multipart `image` o JSON `image` base64)
- **Búsqueda posterior:** Algoritmo de 4 pasos (A: barcode exacto, B: AND nombre+dosis+lab, C: AND nombre+dosis, D: palabra más larga) en `stock_maestro`
- **Historial de reubicaciones:** Se consulta `reportes_ubicacion` (últimas 3) y se adjunta como `historial_ubicaciones` en cada producto

---

## 4. Pendientes y Próximos Pasos

### ✅ Completado
- [x] Asistente ARA - Intelligent funcional con NVIDIA NIM + Ollama + fallback SQL
- [x] Búsqueda inteligente en stock_maestro (3 pasos + stop words)
- [x] Detección de métricas globales (totales / SKUs)
- [x] Procesamiento asíncrono para no bloquear Waitress
- [x] Optimización de velocidad (num_ctx, num_predict, recorte de prompt)
- [x] Frontend: alineación correcta de burbujas, indicador de escritura, badge IA
- [x] Manejo de errores con traceback completo
- [x] Host 0.0.0.0:5000 para cloudflared

### 🔜 Pendientes
- [x] Visor de artículos con IA: `procesar_imagen_visor()` + `POST /api/vision/escanear`
- [ ] Probar conexión NVIDIA NIM con DeepSeek V4 Flash (validar API key de chat)
- [ ] Probar NVIDIA NIM Vision con `meta/llama-3.2-11b-vision-instruct` (validar API key de visión)
- [ ] Probar fallback Ollama local (verificar que phi3:latest y llava responden)
- [ ] Agregar límite de rate limiting en endpoints de chat
- [x] Integrar endpoint `/api/vision/escanear` en frontend: `tomarFoto()` → FormData → renderizar ficha de producto o fallback con OCR en barra de búsqueda
- [x] Búsqueda de precisión con código de barras (EAN/UPC) + dosis + laboratorio en `_buscar_producto_sql_vision()`
- [x] Tarjeta de historial de reubicaciones desde `reportes_ubicacion` en la ficha del producto escaneado
- [x] Fase 1 escalabilidad: WAL + `timeout=30.0` + `synchronous=NORMAL` + `busy_timeout=5000` en `get_db_connection()`
- [x] Fase 2 escalabilidad: `ThreadPoolExecutor` en visión, caché TTL=30s en dashboard, polling reducido a 10s
- [x] Fase 3 escalabilidad: endpoint `/api/health`, rate limiter visión (15/min/IP), Dockerfile, docker-compose, gunicorn.conf.py
- [x] v2.0 Arquitectura Hexagonal: domain models, trazabilidad atómica, procesamiento de notas por visión, concurrencia, auto-chequeo, PDF de movimientos, consulta IA
- [x] RBAC e integración de módulos v2.0: permisos granulares, migración automatica, 4 nuevos botones menú, control navegación, checkboxes en gestión usuarios

---
### v2.1.1 — Hotfix Rutas, Menú y Ubicación (2026-07-26)

**Fix 1 — Rutas sin `undefined`:**
- Se reemplazaron TODAS las referencias a `window.ARA_SERVER` por rutas relativas `/api/...` en JavaScript (`index.html`).
- Eliminados 5 patrones rotos en helpers v2.0 (`cargarNotasPendientes`, `listarNotasPruebas`, `crearNotaPrueba`, `abrirTrazabilidadMovimientos`, `abrirPdfMovimientos`).
- Limpiados 4 patrones legacy (`activarEscanerEmbalaje`, `confirmarCierreBulto`, `abrirNuevoChatOriginalFallback`, `verificarConteo`) que tenían `window.ARA_SERVER` como dead code.
- Ahora todas las peticiones HTTP usan rutas relativas (`/api/...`) sin depender de variables externas.

**Fix 2 — Arquitectura del Menú:**
- Eliminados del menú principal los 3 botones sueltos: `Visor / ARA Vision`, `Notas OCR` y `Notas Prueba`.
- `Trazabilidad` permanece como módulo independiente en el menú.
- Los 3 módulos removidos ahora son submódulos dentro de **Preparación** (submenú con back buttons).
- Eliminada la función duplicada `renderModuloVisor()` (que compartía IDs con `renderVisorArticulos()` dentro de Preparación).
- Agregados back buttons (`⬅ arrow-left`) en `renderModuloPrepNotas()` y `renderModuloNotasPruebas()` para volver al submenú de Preparación.
- Limpiadas las entradas `visor`, `preparacion_notas`, `notas_pruebas` de `MAPA_MODULOS`.

**Fix 3 — Tarjeta de Ubicación en Visor:**
- Se agregó una **Tarjeta de Ubicación Específica** visible en cada resultado del Visor de Artículos (`ejecutarVisor`).
- La tarjeta muestra `i.ubicacion` (campo7 desde stock_maestro) con estilo destacado (fondo ámbar, borde naranja, icono 📍).
- Se diferencia del historial de reubicaciones (`reportes_ubicacion`) que se mantiene debajo como información secundaria.

---
### v3.0 — Módulo Rutas con ORS + Leaflet + GPS Telemetría (2026-07-26)

**Backend (`ara_server.py`):**
- Definida `ORS_API_KEY` para OpenRouteService (API key con permisos de direcciones y matriz).
- Creada clase `ORSAdapter` (arquitectura hexagonal):
  - `calcular_ruta_optimizada(origen, destinos)`: consulta ORS Directions API (`/v2/directions/driving-car/geojson`) para obtener el trazado GeoJSON de la ruta más eficiente y el orden óptimo de paradas.
  - `estimar_eta(posicion_actual, destino_coords)`: calcula distancia Haversine y proyecta ETA en minutos (velocidad promedio 30 km/h).
- Almacén en memoria `_posiciones_choferes` y `_rutas_activas` para telemetría en tiempo real.

**Endpoints `/api/rutas/*`:**
1. `GET /api/rutas/pedidos_embalados` — notas_entrega con estado 'embalado' listas para despachar.
2. `POST /api/rutas/optimizar_ruta` — recibe origen + nota_ids, llama ORSAdapter, retorna paradas ordenadas + GeoJSON.
3. `POST /api/rutas/telemetria_chofer` — recibe `{chofer_id, lat, lng, velocidad, nota_actual_id}`, actualiza memoria, retorna ETA calculado.
4. `GET /api/rutas/monitoreo_regente` — devuelve total_entregas_hoy, rutas_activas, pendientes, y array de choferes con posición, velocidad, estado y ETA.
5. `POST /api/notas/estado` — actualiza estado de nota_entrega (ej: 'embalado' → 'entregado').

**Frontend (`index.html`):**
- Integrada librería **Leaflet.js 1.9.4** (CSS + JS) en el `<head>` del documento.
- Estilos CSS personalizados para: `#mapa-rutas`, `.tarjeta-parada`, `.indicador-velocidad`, popups de Leaflet.
- Render functions reemplazadas completamente:
  - `renderSubMenuRutas()` → menú con 2 sub-tabs: **Chofer** y **Regente**.
  - `renderRutaChofer()` → vista del conductor con:
    - Selector de chofer + checkboxes de pedidos embalados.
    - Botón **"Generar Ruta Óptima"** → llama POST /api/rutas/optimizar_ruta, dibuja trazado GeoJSON en Leaflet con marcadores numerados (1, 2, 3...).
    - Transmisión GPS automática vía `navigator.geolocation.watchPosition` hacia `/api/rutas/telemetria_chofer`.
    - Panel lateral con tarjetas de parada, ETA y botón **"Confirmar Entrega"** (POST /api/notas/estado).
    - Botón **"Detener GPS"** para finalizar monitoreo.
  - `renderRutaRegente()` → panel de supervisor con:
    - Dashboard de métricas (Entregas Hoy, Rutas Activas, Pendientes).
    - Mapa Leaflet con marcadores 🚛 en movimiento (verde si velocidad > 0, ámbar si quieto).
    - Polling cada 8s a `GET /api/rutas/monitoreo_regente`.
    - Tabla de seguimiento individual: chofer, última actualización GPS, velocidad (con indicador de color), pedido en curso, ETA.
- Variable global `window._ruta` para estado compartido (mapa, watchId, paradas, geojson).

---
### v3.1 — State Machine Estricta para Notas de Entrega (2026-07-26)

**Domain (`notas_hexagonal.py`):**
- `EstadoNota` expandido: `pendiente → preparando → preparada → chequeada → embalada → entregada → devuelta` (eliminado `COMPLETADA` legacy).
- `TRANSICIONES_VALIDAS`: diccionario que define transiciones permitidas entre cada estado. `preparando` puede saltar a `chequeada` (auto-chequeo ≤2 items).
- `validar_transicion(estado_actual, destino) → bool`: función de dominio que valida contra `TRANSICIONES_VALIDAS`.
- `NOTA_REQUIERE_UBICACION = {'preparada', 'chequeada', 'embalada'}`: set de estados que requieren ubicación registrada.
- DDL actualizado: CHECK constraint de `notas_entrega.estado` incluye los 7 nuevos estados.
- `_tomar_nota()` ahora usa `validar_transicion()` en vez de hardcode.
- `_completar_nota()` transiciona a `preparada` (sin auto-chequeo) o `chequeada` (≤2 items) según regla, nunca más a `completada`.
- Nuevo endpoint `POST /api/notas/<id>/transicion`: recibe `{estado, usuario}`, valúa contra la State Machine, actualiza BD + registra movimiento.
- Nuevo endpoint `PATCH /api/notas/<id>/items`: actualiza `cantidad_preparada` de items solo si estado == 'preparando'.

**Endpoint `POST /api/notas/estado` (`ara_server.py`):**
- Importa `validar_transicion` desde `notas_hexagonal` para rechazar transiciones inválidas con mensaje claro (incluye lista de transiciones permitidas).

**Frontend (`index.html`):**
- `renderSubMenuChequeo` reemplazado por **State Machine Visualizer** con:
  - 5 burbujas de estado (Pendiente → Preparando → Preparada → Chequeada → Embalada) con conteos en vivo.
  - Botones de filtro rápido: Notas en Preparada, Pendientes Chequeo, Embaladas.
  - Botón "Ver todas por estado" que agrupa y muestra todas las notas.
  - Botón "Chequear Nota (legacy)" que mantiene el flujo de chequeo anterior.
- Funciones helper:
  - `_sm_cargar_conteos()`: consulta `/api/notas/lista?estado=` para cada estado.
  - `_sm_actualizar_visualizador()`: renderiza burbujas con conteos.
  - `renderSmNotasPorEstado()`: tabla agrupada por estado.
  - `renderSmNotasPendientes(estado)`: lista filtrada con botones de transición.
  - `_sm_boton_transicion(nota)`: genera botones según transiciones válidas desde cada estado.
  - `_sm_ejecutarTransicion(notaId, destino)`: llama `POST /api/notas/<id>/transicion`.
  - `_sm_abrirDetalle(notaId)`: detalle de nota + items + botones de transición.

---
### v3.2 — Motor de Auditoría Inteligente 360° con Context Injection (2026-07-26)

**Nuevo módulo `ara_brain.py`:**
- `obtener_auditoria_completa_articulo(co_art)` → consulta SQLite y retorna dict JSON con:
  1. **Stock/ubicación actual** desde `stock_maestro` (codigo, descripcion, stock_maestro, campo7).
  2. **Última reubicación** desde `reportes_ubicacion` (usuario, tramo origen→destino, fecha).
  3. **Última nota de entrega** desde `detalle_nota` + `notas_entrega` (numero_nota, cliente, fecha, cantidad).
  4. **Trazabilidad completa de operadores** desde `movimientos_preparador` analizando acciones `tomar`, `completar`, `transicion:→chequeada`, `transicion:→embalada` para identificar preparador, chequeador y embalador.
- `detectar_codigo_articulo(mensaje)`: extrae código de artículo del lenguaje natural usando 4 patrones regex + fallback de tokens contra `stock_maestro`.
- `es_consulta_auditoria(mensaje)`: detecta palabras clave como "auditoría", "trazabilidad", "quién movió", "stock", "última nota".
- `formatear_evidencias_para_prompt(auditoria)`: convierte el dict en el bloque `[EVIDENCIAS EN TIEMPO REAL...]` para inyectar en el System Prompt.
- `AUDITOR_PROMPT`: template con marcadores `{co_art}`, `{descripcion}`, `{stock_actual}`, `{ubicacion}`, `{usuario_reubicacion}`, `{num_nota}`, `{usuario_preparador}`, `{usuario_chequeador}`, `{usuario_embalador}`.

**Actualización de `chat()` en `ara_server.py`:**
- Al recibir un mensaje, primero ejecuta `detectar_codigo_articulo()` + `es_consulta_auditoria()`.
- Si se detecta un producto, llama `obtener_auditoria_completa_articulo()` e inyecta las evidencias formateadas en el System Prompt.
- Si no se activa auditoría, cae al fallback legacy (búsqueda lineal en `stock_maestro`).
- **Modelo mejorado**: intenta NVIDIA NIM (`deepseek-ai/deepseek-v4-flash-free`) si `NVIDIA_API_KEY` está configurada; si falla o no hay key, usa Ollama (`phi3`/`llava`).

---
### v3.3 — Key Pool NVIDIA NIM con Failover Automático (2026-07-26)

**`ara_brain.py`:**
- `NVIDIA_API_KEYS`: array de 5 API keys de NVIDIA NIM (deepseek-v4-flash-free).
- `_KEY_INDEX`: variable global con estado de rotación, protegida por `_KEY_LOCK` (threading.Lock).
- `STATUS_FALLO_KEY = {503, 429, 401, 403}`: códigos HTTP que disparan rotación.
- `llamar_nvidia_con_failover(prompt_sistema, mensaje_usuario)`: función con lógica de failover:
  1. Intenta con `NVIDIA_API_KEYS[_KEY_INDEX]`.
  2. Si la respuesta es 200, rota proactivamente `_KEY_INDEX` y retorna el texto.
  3. Si la respuesta es 503/429/401/403, registra en consola con `⚠️ [NVIDIA KEY POOL] Key #{n} falló (HTTP {code}). Rotando...`, incrementa `_KEY_INDEX`, reintenta.
  4. Repite hasta probar las 5 keys. Si todas fallan, retorna `None` para caer a Ollama.
  5. También captura `Timeout` y excepciones generales para rotar en cada caso.

**`ara_server.py` — `chat()` actualizado:**
- Reemplazada la llamada directa a NVIDIA NIM con una API key única por `llamar_nvidia_con_failover()`.
- Si retorna texto, se devuelve como respuesta. Si retorna `None` (5 keys fallidas), cae automáticamente a Mini ARA Engine local (edge), y si este no está disponible, al fallback Ollama genérico (`phi3`/`llava`).
- El usuario nunca percibe errores de key agotada: la rotación y el failover son transparentes.

---
### v3.4 — Mini ARA Intelligent (Motor Local Edge) (2026-07-26)

**`Modelfile.mini_ara`:**
- Configura un modelo Ollama basado en `qwen2.5-coder:3b`.
- System Prompt con restricción absoluta de dominio: SOLO logística, almacén, trazabilidad de notas, ubicación de artículos y métricas de personal.
- Bloqueo estricto: cualquier pregunta fuera del dominio (cultura general, matemáticas, política, deportes) responde con:
  `"Acceso denegado. Mini ARA opera exclusivamente para la gestión logística e industrial del almacén."`
- Parámetros: `temperature=0.2`, `top_p=0.9`, `num_ctx=4096`, `stop=["</s>"]`.
- Instalación: `ollama create mini-ara -f Modelfile.mini_ara`

**`generar_dataset_ara.py`:**
- Script que conecta a `proyecto_ara.db` y genera `dataset_mini_ara.jsonl` en formato Alpaca.
- Extrae ejemplos de 4 fuentes:
  1. `stock_maestro`: pares [pregunta de stock/ubicación → respuesta con datos reales]
  2. `notas_entrega`: pares [pregunta de estado de nota → respuesta con operadores, cajas, fechas]
  3. `movimientos_preparador`: pares [pregunta de quién movió un artículo → respuesta con usuario/acción/cantidad]
  4. `log_puntos`: pares [pregunta de puntos de operador → respuesta con total]
  5. 5 ejemplos de dominio general bloqueado (refuerzo de restricción)
- Límite: 500 ejemplos máximo.

**`mini_ara_engine.py`:**
- Clase `MiniAraEngine` con 3 capacidades principales:
  1. **Transcripción de audio** (`procesar_audio_local`): usa `faster-whisper` (modelo `base`, device CPU, `int8`, 4 hilos) para transcribir notas de voz de choferes/embaladores. Soporta wav/mp3/m4a/ogg/flac.
  2. **Inferencia local** (`preguntar`): llama al modelo `mini-ara` en Ollama local (`http://127.0.0.1:11434`). Inyecta automáticamente trazabilidad hexagonal vía `obtener_trazabilidad_hexagonal()` si detecta un código en el mensaje. Timeout 120s.
  3. **Disponibilidad** (`verificar_disponibilidad`): consulta `ollama/api/tags` para confirmar que el modelo está instalado.
- Singleton global `get_engine()` para compartir la instancia en `ara_server.py`.

**`ara_server.py` — Actualizaciones:**
- `POST /api/ia/audio`: endpoint multipart que recibe audio, lo transcribe con `MiniAraEngine.procesar_audio_local()`, opcionalmente responde con IA local si `responder=true`.
- `chat()` actualizado: si el Key Pool NVIDIA falla (las 5 keys fallidas), intenta con `MiniAraEngine` local. Si el modelo `mini-ara` no está disponible, cae al Ollama genérico (`phi3`/`llava`).

**Instrucciones para crear el modelo local:**
```powershell
# 1. Asegúrate de tener Ollama instalado (https://ollama.com)
# 2. Descarga el modelo base:
ollama pull qwen2.5-coder:3b

# 3. Crea el modelo mini-ara con el Modelfile:
ollama create mini-ara -f C:\ARA_PROYECT\ara\ARA_Brain\Modelfile.mini_ara

# 4. Verifica que esté disponible:
ollama list

# 5. (Opcional) Generar dataset para fine-tuning:
python C:\ARA_PROYECT\ara\ARA_Brain\generar_dataset_ara.py

# 6. (Opcional) Instalar faster-whisper para transcripción de audio:
pip install faster-whisper
```

---
### v3.5 — Visión Local + Reportes de Rotación (2026-07-26)

**`mini_ara_engine.py` — Reconocimiento visual de productos (`analizar_foto_producto`):**
- Flujo completo: imagen → Ollama llava (OCR/descripción) → extracción JSON → búsqueda en `stock_maestro` → últimos movimientos → ficha técnica
- `_extraer_json_de_respuesta()`: extrae el primer bloque JSON de la respuesta del modelo (soporta ```json ... ``` y {...} directo)
- `_buscar_en_stock(codigo, codigo_barra, descripcion)`: búsqueda en 3 pasos — (A) código exacto, (B) código de barras, (C) LIKE por tokens en descripción
- `_formatear_ficha()`: genera bloque de texto con código, descripción, stock piso/bulto, ubicación física, depósito, código de barras y últimos 3 movimientos
- Integrado en `chat()`: cuando el mensaje incluye `image` o `foto` en base64, se canaliza a `analizar_foto_producto()` y la ficha técnica se inyecta en el System Prompt

**`ara_brain.py` — Reportes de rotación y más vendidos:**
- `obtener_reporte_top_productos(dias=30, limite=10)`: consulta SQLite que agrega `movimientos_preparador` por `co_art`, calcula total despachado, conteo de notas, stock actual y determina riesgo de quiebre (`stock < despachado * 0.3`)
- `es_consulta_reporte(mensaje)`: detecta palabras clave como "reporte", "más vendidos", "productos top", "rotación", "volumen de salida", "ranking"
- `formatear_reporte_para_prompt(reporte)`: convierte el reporte en un bloque de texto con ranking numerado, advertencias de stock bajo y total de notas procesadas
- Integrado en `chat()`: cuando se detecta intención de reporte, se ejecuta la consulta SQL y los resultados tabulados se inyectan en el prompt de la IA

**Cobertura de intención en `chat()`:**
- `image`/`foto` en POST body → `MiniAraEngine.analizar_foto_producto()` → ficha técnica en prompt
- Consultas con "reporte", "más vendidos", "rotación" → `obtener_reporte_top_productos()` → ranking en prompt
- Trazabilidad hexagonal + auditoría clásica se mantienen como respaldo

---
### v3.6 — Control de Estado Profit Plus + Filtros en Reportes (2026-07-28)

**DB Migration (`notas_hexagonal.py` — `init_notas_tables()`):**
- Verificación con `PRAGMA table_info(movimientos_preparador)` para detectar columnas existentes.
- 3 nuevas columnas agregadas de forma segura (solo si no existen):
  - `procesado_profit INTEGER DEFAULT 0` — 0=Pendiente (Amarillo), 1=Procesado (Verde)
  - `fec_procesado_profit DATETIME` — momento en que se marcó como procesado
  - `usuario_procesado_profit TEXT` — quién confirmó el cambio en Profit

**Endpoints en `ara_server.py`:**
- `GET /api/reportes/discrepancias`: lista discrepancias de stock desde `reportes_ubicacion` con JOIN a `stock_maestro`. Acepta `?fecha_inicio=&fecha_fin=&usuario=`. Retorna `{status, data[], total}`.
- `GET /api/reportes/trazabilidad`: lista movimientos desde `movimientos_preparador` con columna `procesado_profit`. Acepta `?fecha_inicio=&fecha_fin=&usuario=&estado_profit=0|1`. Retorna `{status, data[], total}`.

**Endpoints en `notas_hexagonal.py`:**
- `GET /api/trazabilidad/movimientos`: actualizado para aceptar `?estado_profit=`.
- `POST /api/trazabilidad/marcar-procesado-profit`: marca `procesado_profit=1`, guarda `fec_procesado_profit` y `usuario_procesado_profit`. Recibe `{mov_id, usuario_admin}`.

**Frontend (`index.html`):**
- Barra de filtros común en `cargarHistorial()` con:
  - `type="date"` para fecha inicio/fin
  - `<select>` de usuarios cargado desde `/api/usuarios/get_all`
  - `<select>` de estado Profit (solo en trazabilidad): Todos / Pendiente / Procesado
  - Botones `🔍 Filtrar` y `🔄 Limpiar`
- Trazabilidad conectada a `/api/reportes/trazabilidad` (respaldado por `/api/trazabilidad/movimientos`)
- Discrepancias conectada a `/api/reportes/discrepancias`
- Semáforo Profit en tarjetas de trazabilidad:
  - `procesado_profit=0`: código `MOV-XXXXX` en ámbar `#f59e0b` + badge `⚠️ Pendiente en Profit`
  - `procesado_profit=1`: código `MOV-XXXXX` en verde `#10b981` + badge `✅ Procesado en Profit`
- Modal de detalle (`verDetalleMovimientoProfit`): tabla con todos los campos, badge de estado, datos de quién y cuándo procesó, y botón `✅ Cambio de ubicación realizado en Profit` (solo si pendiente). Al presionarlo, llama al endpoint, actualiza en verde y refresca la lista.

**Backup:**
- `proyecto_ara_backup.db` creado antes de aplicar cambios.

---
### v3.7 — Corrección Mixed Content + Filtros Resilientes + UX (2026-07-28)

**Bug Fix: Mixed Content (Cloudflare):**
- `ARA_SERVER` cambiado de `"http://192.168.6.63:5000"` a `""` (ruta relativa).
- Todos los ~40 fetch(`${ARA_SERVER}/api/...`) ahora resuelven a `/api/...` sin IP fija.
- `CONFIG_TRASLADOS` convertido de string a objeto `{SERVER_API: ""}` para que `CONFIG_TRASLADOS.SERVER_API` no sea `undefined`.
- Elimina cualquier advertencia de Mixed Content en navegador.

**Backend: Parámetros por defecto (WHERE 1=1):**
- `GET /api/reportes/discrepancias`: `WHERE` base `1=1`, filtra solo si el parámetro tiene valor y no es `"Todos"`.
- `GET /api/reportes/trazabilidad`: mismo patrón `1=1`.
- `_consultar_trazabilidad()` en `notas_hexagonal.py`: mismo patrón.
- Sin filtros → retorna TODOS los registros.

**UX: Label de filtro renombrado:**
- En Trazabilidad/Rutas: `"Profit"` → `"Estado"`. Opciones: `Todos`, `⚠️ Pendiente`, `✅ Procesado`.

**UX: Dropdown de usuarios con nombre completo:**
- Selector de usuarios ahora muestra `u.nombre` (ej: "Jonaiber Quiñones") como texto visible, mantiene `u.id` (ej: "jonaiber") como `value` del `<option>`.
- Aplica tanto en Discrepancias como en Trazabilidad.

---
### v3.8 — Trazabilidad sobre `reportes_ubicacion` + RBAC Case-Insensitive (2026-07-28)

**DB Migration:**
- Columna `procesado_profit INTEGER DEFAULT 0` agregada a `reportes_ubicacion` mediante `ALTER TABLE` seguro.
- Todos los registros existentes actualizados con `COALESCE(procesado_profit, 0)`.

**`/api/reportes/trazabilidad` reescrito (`ara_server.py:1480`):**
- **Tabla fuente:** ahora consulta `reportes_ubicacion` en vez de `movimientos_preparador`.
- **Columnas retornadas:**
  ```sql
  SELECT
    ru.id AS mov_id,
    ru.usuario,
    ru.co_art AS sku,
    ru.desde,
    ru.hacia,
    ru.fecha,
    COALESCE(ru.procesado_profit, 0) AS procesado_profit
  FROM reportes_ubicacion ru
  ```
- **RBAC Case-Insensitive:**
  - No-admin: `(LOWER(ru.usuario) = LOWER(:ua) OR LOWER(ru.usuario) LIKE LOWER('%' || :ua || '%'))`
    - Coincide tanto con nombre completo ("JONAIBER QUIÑONEZ" ↔ "Jonaiber Quiñonez") como con username ("wilber" → "WILBER SILVA").
  - Admin: `LOWER(ru.usuario) = LOWER(:usuario)` si se pasa el filtro.
- **Fechas:** usa `ru.fecha` en lugar de `mp.timestamp`.

**`/api/reportes/discrepancias` actualizado (`ara_server.py:1410`):**
- Mismo patrón de RBAC case-insensitive aplicado.

**Frontend (`index.html`):**
- `aplicarFiltrosHistorial` para trazabilidad: usa `m.mov_id || m.id`, `m.sku || m.co_art`, `m.desde`, `m.hacia`, `m.fecha`.
- `verDetalleMovimientoProfit`: mapea a `mov_id`, `sku`, `desde`, `hacia`, `fecha`, acción fija "Reubicación".

**Verificación:**
- Consulta directa: 46 registros en `reportes_ubicacion` retornados correctamente.
- Filtro no-admin: `wilber silva` (lowercase) → 3 registros. `WILBER SILVA` (uppercase) → 3 registros. `wilber` (username) → 3 registros.

---
### v3.9 — Submódulo "Cambio de Ubicación" + Visor con ubicación pendiente (2026-07-28)

**Regla de Negocio Absoluta:**
- `stock_maestro` NUNCA se modifica desde este submódulo. Solo se registra en `reportes_ubicacion` como reporte de trazabilidad. El cambio físico en Profit lo procesa un supervisor.

**DB Migration:**
- Columna `hide_location_tutorial INTEGER DEFAULT 0` agregada a `usuarios` para persistir la preferencia de ocultar el tutorial.

**Backend (`ara_server.py`) — 3 nuevos endpoints:**

1. **`POST /api/inventario/reportar_cambio_ubicacion`:**
   - Recibe `{co_art, desde, hacia, usuario}` (usuario extraído automáticamente del frontend, sin input manual).
   - Genera ID auto: `MOV-` + 6 caracteres alfanuméricos mayúsculas (`secrets.choice`).
   - Inserta en `reportes_ubicacion` con `procesado_profit=0` y `fecha=CURRENT_TIMESTAMP`.
   - Retorna `{status, mov_id, mensaje}`.

2. **`GET /api/inventario/ubicaciones_por_categoria`:**
   - Consulta `stock_maestro` agrupando por regex de prefijo de ubicación.
   - Clasifica en categorías: `CR`(Cremas), `AMP`(Ampollas), `JBE`(Jarabes), `MD`(Medicamentos), `MISC`(Misceláneos), `MQ`(Médico Quirúrgicos).
   - Detecta ubicaciones especiales: `NEVERA`, `RACK`, `ESTIVA`, `BULTO CERRADO`, `OFICINA`.
   - Agrupa productos por `Estante {N} - Piso {M}` con regex `\d*([A-Z]+)(\d+)-P(\d+)`.
   - Retorna `{categorias: [{categoria_key, categoria_nombre, estantes: [{etiqueta, productos}]}]}`.

3. **`POST /api/usuarios/preferencia_tutorial`:**
   - Recibe `{usuario, hide_tutorial: bool}`.
   - Actualiza `hide_location_tutorial` en `usuarios` (match por `id` o `nombre`).

**Backend — Visor modificado:**
- `/api/preparacion`: cada item retorna `ubicacion_pendiente` (hacia del reporte más reciente con `procesado_profit=0`).
- `ara_vision.py` → `_adjuntar_historial_ubicaciones()`: agrega `ubicacion_pendiente` a cada producto del visor.

**Frontend (`index.html`):**
- Botón **"📦 Cambio de Ubicación"** dentro del Módulo de Inventario (aislado en un `#submodulo-cambio-ubicacion`).
- Tutorial persistente con checkbox "No volver a mostrar" que llama a `/api/usuarios/preferencia_tutorial`.
- Grid de categorías → tarjetas de Estante-Piso → lista de productos → modal de confirmación con campo "Nueva Ubicación Destino".
- Modal: muestra artículo, origen, responsable, input destino, botón confirmar → POST a backend.
- Visor (`tomarFoto` + `ejecutarVisor`): si existe `ubicacion_pendiente`, renderiza badge ámbar:
  `📍 UBICACIÓN REPORTADA PENDIENTE EN PROFIT: [hacia]`

**Verificación:**
- MOV-ID generado: `MOV-4KLWDB`, `MOV-J1A4DE`, `MOV-DLTBH0` ✅
- Inserción en `reportes_ubicacion` con `procesado_profit=0` ✅
- Regex estante-piso: `3JBE05-P8` → Estante 05 - Piso 8 ✅
- Categorías: MISC, AMP, MQ, MD, RACK detectadas correctamente ✅
- Columna `hide_location_tutorial` en `usuarios` ✅
- `stock_maestro` sin modificar (solo lecturas) ✅
- Python compila sin errores (`py_compile` OK en ara_server.py y ara_vision.py) ✅

### v3.10 — Corrección Navegación SPA + Rediseño Tarjetas Estante (2026-07-28)

**Backend (`_extraer_estante_piso` en `ara_server.py`):**
- Corregida la función `_extraer_estante_piso()` para que retorne el código real de ubicación (ej: `3JBE05-P8`) como `etiqueta` en lugar del genérico `"Estante 05 - Piso 8"`.
- El endpoint `/api/inventario/ubicaciones_por_categoria` ahora sirve los códigos físicos reales (`1CR01-P0`, `2AMP07-P4`, etc.) directamente en el campo `etiqueta`.

**Frontend — Arquitectura SPA de 3 Pasos (`templates/index.html`):**
- Reestructuración completa del submódulo "Cambio de Ubicación" en 3 sub-vistas independientes:
  - **Paso 1 (Categorías):** Tutorial onboarding + botones de categorías, con botón `← Volver al Módulo de Inventario`.
  - **Paso 2 (Estantes):** Grid de tarjetas azules sólidas con códigos reales de ubicación, con botón `← Volver a Categorías`.
  - **Paso 3 (Productos):** Lista de artículos del estante seleccionado, con botón `← Volver a Estantes (NOMBRE_CATEGORÍA)`.
- Conmutación estricta mediante `_mostrarPaso(1|2|3)`: oculta/muestra contenedores dedicados (`#sub-paso-1/2/3`), sin acumulación de DOM.
- `window.scrollTo(0,0)` en cada cambio de paso.
- Todos los contenedores se limpian con `innerHTML = ''` antes de renderizar.
- Limpieza total al salir (`volverAlInventario()`) o reiniciar (`limpiarCambioUbicacion()`).

**Verificación:**
- ✅ Navegación Categorías → Estantes → Productos (y vuelta) sin scroll ni stacking
- ✅ Códigos reales en tarjetas azules: `1CR01-P0`, `1CR01-P1`, `3JBE05-P8`, `2AMP07-P4`
- ✅ Botones con texto contextual: "← Volver a Categorías", "← Volver a Estantes (Cremas)"
- ✅ Scroll reset en cada paso
- ✅ Backend y frontend compilan sin errores

### v3.11 — REALIZAR RUTA: Factura REAL de Profit en tarjetas (2026-08-05)

**Nuevo `rutas/infrastructure/profit_facturador.py` (ProfitFacturador):**
- Conector Python pyodbc a Profit ERP (`CRISTM25`, 192.168.4.20\profitserver) con resolución dinámica de columnas vía INFORMATION_SCHEMA (espejo de `scripts/auditar_profit_10_notas.js`, verificado 10/10).
- `num_fact_por_notas(notas)`: JOIN `not_ent` ↔ `reng_fac` ↔ `factura` — nota TOTALIZADA ('T'/'2', no anulada) + renglones `num_doc` (tipo_doc E/N/'') + factura VIGENTE (status T/0/2, no anulada, sin 'P'); fallback directo `reng_fac.factura=nota`; consultas en lote (IN ...) y degradación a `{}` ante fallo de conexión.

**Backend (`route_service.py`, `models.py`, `sqlite_route_repository.py`):**
- `ItemRuta.num_fact: Optional[str]`: factura REAL totalizada; `None` = sin factura (NUNCA se iguala factura=nota). SOLO_FACTURA → su propia factura.
- `iniciar_ruta` enriquece cada pedido con `num_fact` (batch, 0.1-0.2s) y lo expone en el payload como `num_fact`/`num_fac`.
- Escaneo/verificación (`procesar_escaneo`, `verificar_factura`) aceptan acierto por `factura_num` o `num_fact`: la digitación manual de la factura real coteja 1-1.
- Migración no destructiva: columna `num_fact` en `subruta_items` (persiste hasta REPOLAB).

**Frontend (`index.html`, `repolab_printer.js`):**
- Tarjeta "Por Escanear": `Nota: <b>nota_num</b> | Fact.: <b>num_fact || num_fac || 'Sin Factura'</b>` (elimina la duplicación Fact.=Nota).
- Motor de escaneo/digitación manual (`_rutaHexEscanear`) admite aciertos por `nota_num`, `factura_num` y `num_fact`.
- REPOLAB imprime `num_fact` (o "Sin Factura") en la columna N° Factura.

**Verificación:**
- `py -3.14 -m py_compile` OK (4 módulos) · `node --check` OK (repolab_printer.js + JS embebido de index.html) · suite visor 26/26.
- Probe E2E real: FARMACIA VERITAS EL MOJAN C.A (nota 72159681) → `Fact.: 72147243`; nota 72159690 → 72147248; nota sin factura → "Sin Factura"; digitación manual de 72147243 → 1-1 ✓.

### v3.12 — Sync Campo 7 (Profit) + Kokoro + Chequeo Automático < 3 ítems (2026-08-05)

**PASO 1 — `vigilar_datos.py` (SincronizadorCampo7):**
- Nuevo `SincronizadorCampo7`: pyodbc a Profit (CRISTM25, env `PROFIT_DB_*`), lee `art.campo7` (ubicación, verificada idéntica al local: MD00001→`4MDA06-P3-P4`, MD00826→`4MDA05-P4`, MISC1537→`5MISC06-P2`) y `SUM(st_almac.stock_act)` del almacén BARQUISIMETO (`co_alma='02'`, env `PROFIT_ALMACEN_BQTO`) → `UPDATE stock_maestro SET campo7=?, despacho_bqto=?` en lotes de 500 (8651 artículos en 0.58s).
- Disparo: tras cada migración exitosa (`migrar_a_sql`) + inicial al arrancar + periódico (`VIGILAR_PERIODO_S`, 300s). Modo degradado: fallo de Profit no rompe el vigilante. Imports watchdog perezosos para reutilización sin dependencia.

**PASO 2 — Import de Kokoro TTS (`ara_server.py` 80-104):**
- Ya resuelto: `sys.path` inyecta `_BASE_DIR`/`_SERVICES_DIR`; `from services.kokoro_tts import register_tts_routes` con fallback `from kokoro_tts import` y warning. `services/__init__.py` existe. Sin `.vscode/settings.json` que configurar.

**PASO 3 — Chequeo Automático < 3 ítems (regla + registro.php + badge Mesa):**
- Backend (ya existente, verificado): `preparation_service.py` `UMBRAL_CHEQUEO=3`; `>= 3` → 'preparada' (Chequeo manual); `< 3` → fast-track 'chequeada' + `registrar_despacho_legacy` (POST `/visor/registro.php` via `legacy_http_adapter`) + doble puntos (picking + chequeo 1.0 pt/renglón, anti-duplicado).
- **Bug corregido en `sqlite_nota_store.finalize_nota`**: normalizaba 'chequeada'/'preparada' → 'completada' (comentario obsoleto del CHECK v1); ahora persiste el estado del flujo (CHECK v4.0 admite el ciclo completo) y escribe `auto_chequeado=1` cuando destino = 'chequeada'.
- Frontend (`index.html`): badge **"⚡ CHEQUEO AUTOMÁTICO"** en la lista de la Mesa (`renderSmNotasPendientes`, botón "Pendientes Chequeo") y en el detalle (`_sm_abrirDetalle`) cuando `auto_chequeado==1`; el alert de `finalizarPreparacion` ya mostraba "FAST-TRACK (menos de 3 ítems)".

**Verificación:**
- `py_compile` OK (vigilar_datos.py, sqlite_nota_store.py) · `node --check` JS embebido OK · suite visor 26/26.
- Probe sync sobre copia de BD: 8651 artículos, 0.58s, `despacho_bqto` = stock almacén '02' (0 para los ítems de muestra).
- Probe E2E (copia de BD + repo stub): nota 2 ítems → fast-track ✓ 'chequeada'/`auto_chequeado=1`, registro.php disparado, puntos chequeo +2.0; nota 3 ítems → 'preparada', sin despacho ni puntos extra.

### v3.13 — Auditoría chatbot read-only + Blindaje Kokoro TTS + Visor IA payload/score (2026-08-05)

**PASO 1 — ARA Intelligent: herramientas de auditoría en tiempo real (SOLO LECTURA):**
- `ara_brain.py` (bloque "HERRAMIENTAS DE AUDITORÍA EN TIEMPO REAL"): `es_consulta_nota` (regex `\b\d{6,10}\b` + palabras nota/chequeo/embalaje), `es_consulta_historial_ubicacion`, `_etapa_desde_accion` (preparar/escaneo/ocr_import→picking; transición*chequeada→chequeo; embalaje), `_consultar_nota_profit_readonly` (pyodbc CRISTM25, columnas dinámicas vía INFORMATION_SCHEMA, `LEFT JOIN clientes` para el nombre, degradable a `{"error": ...}`), `obtener_trazabilidad_nota` (notas_entrega + movimientos_preparador + log_puntos por referencia_id + sub-dict `profit`; responsable por etapa first-wins con evidencia complementaria de log_puntos para chequeo/picking), `obtener_historial_ubicacion` (stock_maestro por código/barra/descripción + reportes_ubicacion, mapeo desde→ubicacion_anterior/hacia→ubicacion_nueva/fecha→fecha_hora, LIMIT 20), formatters de prompt.
- `ara_server.py chat()`: inyección de trazabilidad/historial tras la rama de reportes (regex nota + intención → respuesta con evidencia SQLite+Profit; el flujo original de auditoría queda intacto). Nota 72159681 existe solo en CRISTM25 (status "2", FARMACIA CENTER C.A) — se responde con datos de Profit sin tocar el servidor remoto.

**PASO 2 — Blindaje Kokoro TTS (`services/kokoro_tts.py` + `index.html`):**
- Síntesis asíncrona NO bloqueante: `ThreadPoolExecutor(max_workers=2)` + `_TTS_TIMEOUT_S` (env `KOKORO_TIMEOUT_S`, 0.8s); el endpoint espera con `future.result(timeout)` y ante `TimeoutError` responde 503 `{"timeout": true}` mientras la voz sigue generándose en 2º plano (cache en caliente para el próximo pedido).
- Frontend: `kokoroHablar` con `AbortController` + `setTimeout` 800ms; fallback silencioso a `hablarVozLocal` (speechSynthesis) ante timeout/503/error de audio.

**PASO 3 — Visor IA: payload liviano + score ≥ 0.75:**
- Frontend: `tomarFoto` ahora redimensiona a 1280px (aspect ratio) + JPEG 82% (~150-300 KB, antes full-res al 92%); `tomarFotoNota` ya optimizaba.
- Backend: `app.config['MAX_CONTENT_LENGTH'] = 32 MB` (evita "Payload Too Large" → `{}` vacíos).
- `ara_vision.py`: nuevo `_preprocesar_imagen` (PIL: recorte central 90% + contraste 1.2 + nitidez 1.3 + JPEG 85) aplicado en `procesar_imagen_visor` antes del base64 a NVIDIA NIM (degradable a original si PIL falta); prompt `VISION_PROMPT` reemplazado por "TAREA DE EXTRACCIÓN farmacéutica CRÍTICA" (producto inclinado/distante, sin cadenas vacías, JSON plano).
- **Normalización de score**: marca + dosis con coincidencia EXACTA → score ≥ 0.75 garantizado (`max(score, 0.75)` en `_score_cotejo_jerarquico`).
- **Bug del pool de candidatos corregido**: el `LIMIT 50` del paso 2/3 quedaba inundado por descripciones con "500MG" y el match exacto (ej. MD00001) nunca entraba → ahora el término más discriminante (marca/PA) lidera el ranking con `ORDER BY LIKE DESC, LENGTH ASC` y pool de 500.

**Verificación:**
- `py_compile` OK (ara_server.py, ara_vision.py, ara_brain.py, services/kokoro_tts.py) · `node --check` JS embebido OK · suite visor 26/26.
- Probe E2E: detección de intención ✓; `obtener_trazabilidad_nota('72159681')` → local False + Profit real (CRISTM25, status "2", FARMACIA CENTER C.A) ✓; nota local 72160252 → picking "sistema"/"03" y chequeo "REIMER QUIROZ" (vía log_puntos, 11:20:12) ✓; `obtener_historial_ubicacion('MQ01222')` → 1 movimiento (6MQ34-P1→6MQ34-P2, wilber silva) ✓; marca+dosis exacta → 0.8 (≥0.75), marca distinta → 0.645 (descartada) ✓; MD00001 top-1 con marca real ACETABIOFEN (0.8) ✓; preprocesado 1920x1080 → 1728x972 (26 KB) ✓.

### v3.14 — Autochequeo real en chequeo/registro.php (< 3 ítems) + Badge AUTOCHEQUEO (2026-08-05)

**PASO 1 — Fast-Track: POST real al endpoint PHP de chequeo:**
- `preparacion/infrastructure/adapters/legacy_http_adapter.py`: nueva constante `PATH_CHEQUEO_REGISTRO = "/chequeo/registro.php"` y método `registrar_autochequeo_php(numero_nota, usuario_id, mesa="99", estado="AUTOCHEQUEO")` que hace POST Form URL Encoded a `http://192.168.4.148:8000/chequeo/registro.php` con `{nota, usuario, id_usuario, mesa, estado}` para que gestion.php llene "Registro de Chequeo", "Número de Chequeador" y "Hora del Registro" con la ID del preparador activo.
- **Timeouts y no-bloqueo**: `AUTOCHEQUEO_TIMEOUT_S` (default 2.5s); try/except captura todo y loguea `[ARA_SYNC] Error conectando a registro.php` sin lanzar excepción — la transacción SQLite local nunca se interrumpe si el PHP no responde.
- `preparacion/domain/ports.py`: nuevo método abstracto `registrar_autochequeo_php` en `PreparationRepositoryPort`; stub en `ProfitSQLAdapter` (mismo patrón TODO, USE_DIRECT_SQL).
- `preparacion/application/preparation_service.py` `finalizar_preparacion()`: en la rama fast-track (< UMBRAL_CHEQUEO) dispara `registrar_autochequeo_php(codigo, preparador_id)` tras el despacho legacy; la respuesta expone `registro_autochequeo`. El `preparador_id` ya se resuelve desde la tabla `usuarios` (`resolver_id_usuario`: '03'→REIMER QUIROZ, '22'→JONAIBER QUIÑONEZ).

**PASO 2 — Badge visual en la Mesa:**
- `index.html`: `renderSmNotasPendientes` (lista) y `_sm_abrirDetalle` (detalle) renderizan ahora el badge verde destacado **"⚡ AUTOCHEQUEO"** (background `#10b981`) cuando `auto_chequeado == 1` (antes decía "CHEQUEO AUTOMÁTICO").

**PASO 3 — Verificación:**
- `py_compile` OK (preparation_service.py, legacy_http_adapter.py, profit_sql_adapter.py, sqlite_nota_store.py, ports.py, route_service.py) · `node --check` JS embebido OK.
- Probe E2E real contra 192.168.4.148: POST a `chequeo/registro.php` → **HTTP 200** (página "Validacion cheque...") tanto con nota sintética (conectividad, sin tocar datos) como con la nota real 72160252 (1 ítem, auto-chequeada por REIMER QUIROZ) con `id_usuario=03`; flujo completo `PreparationService.finalizar_preparacion` sobre copia de BD: fast-track ✓ estado 'chequeada' ✓, `[ARA_SYNC] POST ... -> HTTP 200` ✓, doble puntos ✓, `registro_autochequeo.status=ok` ✓.
- Suite pytest 26/26 (2 fallos transitorios de latencia <10ms por carga de CPU — documentada como fluctuante en los docstrings — pasaron en rerun).

### v3.15 — Visor IA: pool multimodelo NIM anti-404 + overlay dinámico + código de barras (2026-08-05)

**PASO 1 — Modelo de visión NVIDIA NIM (`ara_vision.py`):**
- `NIM_VISION_MODELS = ["meta/llama-3.2-11b-vision-instruct", "qwen/qwen2-vl-72b-instruct", "microsoft/phi-3.5-vision-instruct"]` con **timeout cortante 6.0s por intento** (antes: minimax/gemma-4-31b se colgaban hasta 90s). En timeout → log `⏱️ Timeout 6s superado en modelo {modelo}. Probando siguiente candidato...` y rotación modelo+key.
- **HTTP 404 → descarte del modelo para la sesión activa** (`_MODELOS_DESCARTADOS` + `_descartar_modelo`); si todos se descartan → fallback Ollama local. Env `ARA_VISION_MODEL` se inserta como primer candidato si está definido.
- **Benchmark real**: solo `meta/llama-3.2-11b-vision-instruct` responde 200 (3.75s); llama-3.2-90b → timeout; qwen2-vl-72b y phi-3.5 → 404; gemma-3-27b-it → 410 Gone (retirado de NIM). Latencia fotográfica total: **90s → 4.16s**.

**PASO 2 — Overlay de encuadre dinámico (`templates/index.html`):**
- Umbral de nitidez **-50%** (`_GUIA_NITIDEZ_UMBRAL = 0.05`) y hold **180ms** (< 200ms de validación en almacén; antes se congelaba en AMARILLO).
- **Bounding Box dinámico**: en cada frame se calcula la caja de píxeles de alto contraste (gradiente Sobel) del 90% central, se suaviza con lerp (`_GUIA_LERP = 0.30`) y sigue al producto; detección de movimiento rápido por desplazamiento del centroide (`_GUIA_MOV_MAX = 0.09`).
- Estados: sin objeto/movimiento rápido → recuadro centrado AMARILLO `rgba(255,204,0,0.25)`/`#ffcc00`; objeto estable (nitidez + cobertura + sin movimiento ≥ 180ms) → recuadro se encoge al contorno en VERDE `rgba(40,167,69,0.35)`/`#28a745` + beep 880Hz; barra de estado `#camera-status`.

**PASO 3 — Lector de código de barras (Html5Qrcode) + endpoint de contingencia:**
- `index.html`: `new Html5Qrcode('html5qr-region')` (lib ya en `<head>` línea 10), `facingMode: 'environment'`, `fps: 15`, `qrbox 250x150`, pasivo sobre el mismo stream; al detectar → `procesarCodigoBarrasDirecto(decodedText)` (prioridad sobre visión; cierra cámara y consulta el endpoint).
- `visor_articulos/application/visor_routes.py`: nueva vía en `/api/visor/buscar` para payload JSON `{codigo_barra}` sin imagen — match EXACTO en `stock_maestro` por `codigo_barra` (limpia espacios/guiones, fallback por `codigo`), con historial de `reportes_ubicacion`, `ubicacion_pendiente`, stock BQTO/SC/total; `motor: "codigo_barra"`, latencia ~0ms; código no registrado → `status: "low_confidence"` HTTP 200 (suave).

**Verificación:**
- `py_compile` OK (ara_vision.py, visor_routes.py) · `node --check` JS embebido OK (patrón extracción a `index_embed5.js`).
- Probe NIM: modelo activo `meta/llama-3.2-11b-vision-instruct` → HTTP 200 en 3.95s con payload JSON válido; benchmark de 5 candidatos documentado arriba.

### v3.16 — Skill SQL de Solo Lectura (ReAct) + Reparación pool NIM del Chat (2026-08-05)

**PASO 1 — `ara_brain/db_query_tool.py` (nuevo, herramienta SQL segura):**
- `ejecutar_consulta_sql_read_only(query_sql)`: solo `SELECT` (regex), bloquea INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/PRAGMA y multi-sentencias; conexión **read-only REAL** (`file:...?mode=ro` URI + `PRAGMA query_only=ON`); tope de 25 filas (LIMIT automático si el LLM no lo pone); errores → `{"status":"error","detalle":...}` sin excepciones.
- `obtener_esquema_bd()`: inspecciona tablas+columnas de `proyecto_ara.db` (15 tablas: `notas_entrega`, `detalle_nota`, `log_puntos`, `reportes_ubicacion`, `stock_maestro`, `usuarios`, `movimientos_preparador`...).
- `generar_select_desde_mensaje(mensaje)` (ReAct paso 1): el LLM construye el SELECT sobre el esquema con pistas de dominio (notas→`notas_entrega.preparador_id`+`usuarios`, movimientos→`reportes_ubicacion`, puntos→`log_puntos`, stock→`stock_maestro`); respuesta JSON `{"sql": "..."}`; parsea JSON/fenced/inline; timeout 15s vía `llamar_nvidia_con_failover(model="meta/llama-3.1-8b-instruct")`.
- `es_consulta_sql_operativa(mensaje)`: detector conservador (nota 6+ dígitos, código de artículo tipo CR00459, o mención nota/preparador + palabra de dominio) — no dispara en charla general ("cuantos puntos tiene el mundial?" → False).
- `formatear_resultados_para_prompt()`: filas → bloque inyectable al system prompt (incluye caso "0 FILAS" para responder honestamente).

**PASO 2 — Integración (catch-all, solo si las herramientas fijas no hallaron datos):**
- `ara_server.py chat()`: bloque Skill SQL tras el fallback legacy de stock; resultados en `datos_producto_especifico` + `entidad_detectada = "consulta_sql"`, logs `[SQL_SKILL]`.
- `chat_routes.py _procesar_mensaje_ara_bot` (bandeja): mismo skill cuando las búsquedas fijas dan "No se encontraron"; el truncado de contexto sube de 350 → 1500 chars cuando el skill inyecta resultados.

**PASO 3 — Reparación del pool NIM del chat (causa raíz de "ARA no responde"):**
- Las 5 keys históricas de `ara_brain.NVIDIA_API_KEYS` devolvían **403 Authorization failed** y el modelo `deepseek-ai/deepseek-v4-flash-free` **404** en el endpoint → todo el chat caía a Ollama.
- `llamar_nvidia_con_failover`: pool EXTENDIDO (keys verificadas de `ara_vision.NVIDIA_KEYS` primero + históricas, deduplicadas), falla rota con 503/429/401/403/404; modelo por defecto → `meta/llama-3.1-8b-instruct` (único que responde 200 hoy, ~3-4s).
- `chat_routes.py`: `_llamar_nim_ara_bot` con pool rotativo (mismo conjunto de keys) y `max_tokens` 80→400; modelo → `meta/llama-3.1-8b-instruct`.

**Verificación:**
- `py_compile` OK (db_query_tool.py, ara_brain.py, ara_server.py, chat_routes.py).
- Probe E2E real: "quien preparo la nota 72160255" → JOIN generado por LLM → **REIMER QUIROZ** ✓; "cambiaron de ubicacion al articulo CR00459" → consulta `reportes_ubicacion` → 0 filas (respuesta honesta) ✓; "cuantos puntos tiene el preparador JONAIBER" → `SUM(puntos_ganados)` ✓; detector rechaza charla general ✓.
- Nota de datos: `log_puntos.usuario` guarda NOMBRES (no ids) — la subconsulta `WHERE usuario = (SELECT id ...)` devuelve `SUM=None`; las herramientas fijas de auditoría (v3.13) siguen siendo la vía primaria.

### v3.17 — Notas San Cristóbal (S/C): carga directa desde CRISTM25 (2026-08-05)

**PASO 1 — Identificación de serie S/C (`preparacion/domain/notas_sc.py`, nuevo):**
- `es_nota_san_cristobal(num_nota)`: True si el código trae serie `'A'` + solo dígitos (`A0467959`, `A467959`); los códigos puramente numéricos retornan False.
- `normalizar_nota_sc(num_nota)`: `{'es_sc', 'numero', 'alias'}` — extrae la parte numérica sin ceros a la izquierda (`A0467959` → `467959`) para la llave SQL y conserva el **alias visual** (`A0467959`) que se persiste en `notas_entrega.numero_nota` (el re-escaneo del operador debe coincidir).

**PASO 2 — `ProfitSQLAdapter` con consulta SQL real a CRISTM25 (ya no es solo stub):**
- Config por precedencia: env `PROFIT_SQL_*` → `DB_CONFIG` de config.py (se ignora si trae placeholders tipo `...NOMBRE...`) → env `PROFIT_DB_*` (config CRISTM25 verificada en producción: 192.168.4.20:1433, driver `SQL Server`, timeout 8s) → defaults.
- Conexión pyodbc perezosa + resolución dinámica de tablas/columnas vía INFORMATION_SCHEMA (patrón profit_facturador.py).
- **Esquema real verificado con probes read-only** (crítico para no fallar): `not_dep` existe pero solo con 3 filas de prueba; las notas S/C reales viven en **`not_ent`** (`fact_num`); `reng_nd` NO existe; la tabla real de renglones es **`reng_nde`** (FK `fact_num`); `reng_ndd` para despachos; `reng_fac` SOLO para notas totalizadas (FK `num_doc` + `tipo_doc IN ('E','N','')` — su `fact_num` guarda la FACTURA, nunca la nota).
- `get_nota_sc_por_numero(numero)`: itera encabezados (`not_dep` → `not_ent`) y renglones (`reng_nd` → `reng_ndd` → `reng_nde` → `reng_fac`) tomando la primera tabla que devuelva filas; cliente vía `clientes.cli_des` (`RTRIM`); ubicación real vía `art.campo7` por co_art (misma lectura de SincronizadorCampo7); `NotaNotFoundError` con mensaje propio (sin mencionar `rep_not`), `NotaNotVerifiedError` si no hay renglones/error.
- `verificar_nota_sc_existe(numero)`: SELECT 1 sin excepciones (False ante error) para el fallback numérico.

**PASO 3 — Rutas de carga en `PreparationService`:**
- `__init__(..., repositorio_sc=None)`: fuente SQL S/C inyectable, independiente de `USE_DIRECT_SQL`.
- `_cargar_nota_externa(codigo)`: (1) código con serie `'A...'` → CRISTM25 directo con el número normalizado; (2) código normal → Legacy PHP (`rep_not`); (3) si el Legacy devuelve `NOTA_NO_ENCONTRADA` → reintenta CRISTM25 (nota S/C escaneada solo con la parte numérica). El alias visual se reasigna a `nota.codigo_nota` antes de persistir.
- `finalizar_preparacion`: las notas con `almacen_origen='SAN_CRISTOBAL'` (o serie 'A') se finalizan **100% local** — se SKIPEA `assign_preparer`/`registrar_despacho_legacy`/`registrar_autochequeo_php` (no existen en el rep_not BQTO); puntos de picking/chequeo y Fast-Track intactos.
- `_formatear_nota`/`_formatear_nota_local`: respuesta con `almacen_origen`; para notas S/C la `ubicacion` se TOMA de `campo7` (CRISTM25), no del `stock_maestro` de Barquisimeto.

**PASO 4 — Persistencia y migraciones:**
- `models.py`: `ItemNota.campo7` (ubicación Profit) + `NotaPreparacion.almacen_origen` + `NotaNotFoundError(mensaje)` opcional.
- `notas_hexagonal.py init_notas_tables()`: `notas_entrega.almacen_origen TEXT DEFAULT ''` y `detalle_nota.campo7 TEXT DEFAULT ''` en CREATE + migración v3.17 idempotente (ALTER si falta); misma columna en el rebuild de `migrate_notas_estado_check`.
- `sqlite_nota_store.py`: `create_nota(..., almacen_origen="")` (persiste `SAN_CRISTOBAL` / `''` para BQTO) e `insert_items` con `campo7`.
- `ara_server.py`: `_prep_repo_sc = ProfitSQLAdapter()` SIEMPRE activo como `repositorio_sc` (legacy BQTO sigue siendo el repo principal).

**Verificación:**
- `py_compile` OK (7 módulos); suite pytest 26/26 ✓ (sin regresiones).
- Probe E2E sobre copia temporal de `proyecto_ara.db` (sin tocar la real): escaneo `A0467959` → alias persistido + `SAN_CRISTOBAL` + ubicación = campo7 ✓; re-escaneo reabierta con ubicación S/C ✓; fallback numérico `467959` ✓; numérico inexistente → `NOTA_NO_ENCONTRADA` honesto ✓; finalizar S/C sin ninguna llamada al Legacy (aserciones con fakes que lanzan AssertionError) ✓; nota BQTO normal con origen vacío ✓.
- Probe real CRISTM25: conexión OK; `verificar_nota_sc_existe('467959')` = True y `verificar_nota_sc_existe('72160255')` = True; `get_nota_sc_por_numero('467959')` → **not_ent/reng_nde, 2 items, FARMAOCCIDENTE C.A** ✓ (la nota de ejemplo del usuario `A0467959` es una nota REAL de HOY, status '0' pendiente, no totalizada → sus renglones viven en `reng_nde`, no en `reng_fac`).

---

### v3.18 — Discriminación automática de almacén por número (umbral 5000000) en Picking (2026-08-05)

**Regla de negocio implementada (PASO 1 — `preparacion/domain/notas_sc.py`):**
- `resolver_almacen_picking(num_nota_raw)`: limpia el código (regex `\D` → solo dígitos) y aplica el umbral:
  - `num_nota > 5000000` → `{"origen": "BARQUISIMETO", "co_alma": "01", "db": "PROFIT_BQTO", "num_nota": ...}` → Legacy PHP (rep_not BQTO).
  - `num_nota <= 5000000` → `{"origen": "SAN_CRISTOBAL", "co_alma": "01", "db": "CRISTM25", "num_nota": ...}` → Profit SQL directo (Almacén '01').
- La discriminación ya NO depende del prefijo de serie `'A'`: un `A0467959` se limpia a `467959` (≤ 5000000 → S/C) y un `72160454` (> 5000000 → BQTO) sin tocar `rep_not`.

**PASO 2 — `profit_sql_adapter.py` (consulta de picking con almacén '01'):**
- `get_nota_sc_por_numero` valida primero `resolver_almacen_picking`: si la nota es BARQUISIMETO → `NotaNotFoundError` honesto sin consultar la BD ("num_nota > 5000000 → PROFIT_BQTO; consúltela por la vía de Barquisimeto"). Igual guard en `verificar_nota_sc_existe` (retorna False).
- Renglones: filtro **`AND (co_alma = '01' OR co_alma IS NULL)`** aplicado a toda tabla de renglones que tenga columna `co_alma` (picking exclusivo del Almacén '01' de S/C).
- `campo7` (ubicación de picking): primero la columna `campo7` del propio renglón si existe, si no `art.campo7` por co_art (CRISTM25).
- Mapeo del cotejo por renglón: cada ítem lleva **`reng_nd`** (= `reng_num` del origen), propiedad nueva de `ItemNota` — la estructura y lógica de pareo hexagonal NO se alteró.
- Cadena de tablas reales de CRISTM25 (verificada con probes): encabezado `not_dep` → `not_ent`; renglones `reng_nd` (no existe) → `reng_ndd` → `reng_nde` (la real para notas de entrega, FK `fact_num`) → `reng_fac` (solo totalizadas, FK `num_doc` + `tipo_doc`).

**PASO 3 — Respuesta del visor de picking (`preparation_service.py`):**
- `_cargar_nota_externa`: umbral decide el origen; el alias visual se conserva como `codigo_nota` (persiste en `numero_nota` para re-escaneo); notas BQTO ahora se persisten con `almacen_origen='BARQUISIMETO'`.
- Metadato `almacen_origen` de la respuesta: **`"SAN_CRISTOBAL (01)"`** o **`"BARQUISIMETO"`** (`_etiqueta_almacen`; notas históricas con `''` se emiten como BARQUISIMETO).
- Cada ítem incluye **`reng_nd`** (pareo ítem por ítem) y `ubicacion` = `campo7` del Almacén '01' para S/C (nunca la ubicación BQTO de `stock_maestro`).
- `finalizar_preparacion`: notas S/C (serie 'A' o `almacen_origen='SAN_CRISTOBAL'`) se finalizan 100% local, sin notificación al Legacy (rep_not BQTO).

**PASO 4 — Persistencia y migración:**
- `detalle_nota.reng_num INTEGER DEFAULT 0` (ALTER idempotente + CREATE, sin tocar el pareo hexagonal) y `sqlite_nota_store.insert_items` lo persiste; `_registro_a_nota`/formatos lo propagan.

**Verificación:**
- `py_compile` OK (5 módulos picking + notas_hexagonal) · suite pytest 26/26 ✓.
- Probe E2E sobre copia temporal de la BD (8 escenarios): umbrales (5000000 → S/C, 5000001 → BQTO) ✓; `A0467959` → CRISTM25 Almacén 01 con `reng_nd` ✓; re-escaneo reabierta con ubicación S/C persistida ✓; `467959` numérico ✓; `80000001` BQTO → Legacy con origen `BARQUISIMETO` ✓; BQTO inexistente → `NOTA_NO_ENCONTRADA` ✓; S/C inexistente → `NOTA_NO_ENCONTRADA` (CRISTM25) ✓; finalizar S/C sin llamadas al Legacy (fakes con AssertionError) ✓.
- Probe real CRISTM25: `get_nota_sc_por_numero('467959')` → **not_ent/reng_nde, 2 items del Almacén '01': `reng_nd=1 MD02840 x3 (4MDM04-P7)`, `reng_nd=2 MD04632 x3 (4MDE01-P4)`, FARMAOCCIDENTE C.A** ✓; `72160454` rechazada por el guard BARQUISIMETO ✓.

---

### v3.19 — Sync de renglones antes del cierre de autochequeo (registro.php no rechaza con "Faltan articulos") (2026-08-06)

**Diagnóstico:** al ejecutar el autochequeo de una nota (ej. 72161167), el PHP Legacy (`visor/registro.php`) responde "Faltan articulos por cargar. Si finaliza la nota sera enviada a revision" porque exige que los renglones de la nota estén marcados como cargados/verificados en Profit SQL.

**Esquema REAL verificado (probe read-only CRISTM25, 192.168.4.20):**
- `rep_not` NO existe como tabla en la BD Profit (es la vista del PHP Legacy).
- Los renglones de una nota de entrega viven en `reng_nde` (FK `fact_num`): para 72161167 → `total_art=8`, `stotal_art=0`, `pendiente=8`, `cant_imp=0`, `seleccion=False` (pendiente → el Legacy ve faltantes).

**PASO 1 — `preparacion/infrastructure/adapters/profit_renglones_sync.py` (nuevo, `ProfitRenglonesSync`):**
- Conector pyodbc a Profit (mismo patrón profit_facturador.py: configuración env `PROFIT_DB_*` → CRISTM25, resolución dinámica de columnas vía INFORMATION_SCHEMA).
- `marcar_renglones_cargados(numero_nota)`: itera `reng_nde → reng_ndd → reng_fac → reng_nd` (primera con la nota) y ejecuta `UPDATE ... SET stotal_art/cant_imp/cant_prod = total_art, pendiente = 0, seleccion = 1 WHERE fact_num = ?` — iguala la cantidad cargada/verificada a la cantidad total del pedido.
- Best-effort absoluto: si Profit no responde o no tiene la nota, retorna `{"status":"error","aviso_legacy":...}` SIN lanzar excepción; el cierre local y el POST al Legacy nunca se interrumpen.

**PASO 2 — `legacy_http_adapter.py` (integración ANTES del cierre):**
- `__init__` nuevo parámetro `renglones_sync` (objeto con `marcar_renglones_cargados`).
- `_sincronizar_renglones_cargados(nota)`: invoca el sincronizador best-effort y loguea `[ARA_SYNC] ✅ Autochequeo y renglones sincronizados exitosamente en BD Legacy para nota {num_nota}.` en éxito.
- `registrar_chequeo_legacy` (POST `/visor/registro.php`): sincroniza ANTES del POST y expone `sync_renglones` en la respuesta.
- `registrar_autochequeo_php` (POST final a `/chequeo/registro.php`): sincroniza ANTES del paso 3 y expone `sync_renglones` en éxito y fallo.

**PASO 3 — `ara_server.py`:** `_prep_renglones_sync = ProfitRenglonesSync()` instanciado y pasado a `LegacyPHPAdapter(LEGACY_API_URL, renglones_sync=_prep_renglones_sync)`.

**Verificación:**
- `py_compile` OK (profit_renglones_sync.py, legacy_http_adapter.py, ara_server.py, preparation_service.py).
- Suite mock (2 tests): sincronizador marca 2 renglones en `reng_nde` + log `[ARA_SYNC]` exacto ✓; orden `sync → POST registro.php` ✓.
- E2E mock de `finalizar_preparacion` (nota 2 ítems): status success, estado `chequeada`, fast-track, doble puntos, `sync_renglones.status=ok` en ambos flujos ✓.
- Probe REAL CRISTM25: estado previo pendiente confirmado; UPDATE dentro de transacción ROLLBACK → 2 renglones marcados (0.02s) y BD intacta tras rollback ✓.

---

### v3.20 — Autochequeo 3 pasos con sesión PHPSESSID + idempotencia + verificación de respaldo (2026-08-06)

**Diagnóstico:** el autochequeo registraba la nota en el Legacy pero el código lo daba por ❌: el servidor Legacy desplegado en `http://192.168.4.148:8000` NO devuelve los marcadores documentados (`"Nota Lista para procesar"` / `<audio src="sond/finaliza.mp"`) en la página de éxito — el guardado real (nota 72161071, 2 ítems, 16:12) ocurrió mostrando solo la página del formulario (+480 bytes, sin texto de confirmación).

**Flujo REAL verificado en vivo (coincide con la firma de `src/Services/LegacyChequeoService.php`):**
1. POST `/chequeo/index.php` con `nota` + `consulta="Buscar"` → crea PHPSESSID; si ya está registrada responde `"La nota ya se chequeo anteriormente."`.
2. GET `/chequeo/registro.php` con la cookie → formulario con `responsable` (texto), hidden `monto`, hidden `descp`, `<audio src="sond/faltan.mp3" autoplay>` cuando faltan artículos; POST a `registro.php` con botón `<button name="registro">Guardar`.
3. POST `/chequeo/registro.php` con cookie + `{responsable, nota, registro="Guardar", monto, descp}`.
- POST directo SIN sesión → `registro.php` devuelve 827 bytes con `"Error al ingresar. En breve sera redireccionado."` y NO guarda nada.

**Cambios en `legacy_http_adapter.py` (`registrar_autochequeo_php`):**
- Reescrito como flujo de 3 pasos con `requests.Session()` (cookie PHPSESSID), extracción de `monto`/`descp` vía `_extraer_input`, payload exacto del formulario, timeout por petición `AUTOCHEQUEO_TIMEOUT_S` (default 2.5s), logs `[ARA_SYNC] ✅/❌`, dict con `pasos`, `monto`, `descp`, `sync_renglones`.
- **Idempotencia (paso 1):** si `index.php` responde `"ya se chequeo"` → `status ok` + `aviso_legacy="Nota ya registrada en Legacy (autochequeo idempotente)"`.
- **Verificación de respaldo (tras paso 3 sin marcadores):** `_verificar_registrada()` re-busca la nota con la misma sesión; si `"ya se chequeo"` → `status ok` + `aviso_legacy="Confirmado por verificación ('La nota ya se chequeo anteriormente.')"`.
- Helpers: `_confirmacion_exitosa` (marcadores documentados), `_extraer_input`, `_resumen_html`, `_fallo_autochequeo`, `_verificar_registrada`. Se eliminó `_es_html_legacy` (marcaba todo HTML como rechazo — erróneo porque el éxito también es HTML).

**Verificación:**
- `py_compile` OK; prueba real idempotente (72161071, servidor vivo): `status ok` en paso 1, `[ARA_SYNC] ✅` ✓.
- `_verificar_registrada` probado en vivo (72161187 ya registrada): `True` ✓.
- Prueba real E2E (16:12): nota 72161071 registrada vía el flujo 3 pasos (visible en `chequeo/listado.php`, Responsable 9) ✓.
- Vigilante 17:17–17:27: 68/68 despachos nuevos autochequeados por la app en segundos (ninguno pendiente) ✓.

**Pendiente:** reiniciar el proceso de ARA (iniciado 16:34, corre la versión vieja sin verificación de respaldo ni sync de renglones) para que los logs `[ARA_SYNC]` reflejen ✅ real en todos los autochequeos.

---

### v3.21 — Capa de Seguridad + Adaptadores departamentales NVIDIA BRAIN (2026-08-06)

**Nuevos módulos (PHP, `app/Services/NvidiaBrain/`):**

**Security/ — Capa de control de acceso:**
- `Security/RolePermissionManager.php`: RBAC estático con `esHerramientaPermitida(rol, tool)`. Matriz: ROL_ALMACEN `[buscar_inventario]`, ROL_VENTAS `[buscar_inventario, consultar_saldo_cliente]`, ROL_LOGISTICA `[buscar_inventario]`, ROL_FINANZAS `[consultar_saldo_cliente, conciliar_factura, leer_voucher_ocr]`, ROL_TECNOLOGIA (total), ROL_CLIENTE `[consultar_saldo_cliente]`. Rol normalizado (mayúsculas, sin acentos) + comodín `'*'`. Helpers: `herramientasDelRol`, `herramientasDenegadas`, `rolExiste`.
- `Security/CustomerAuthenticator.php`: `validarCliente(PDO, identificador, factorSecundario, origenWhatsApp='')` — normaliza RIF/Cédula/co_cli (mayúsculas, sin espacios/puntos/guiones), consulta preparada sobre `clientes` por `co_cli` o `rif`, verifica los ÚLTIMOS 4 DÍGITOS de `telef` (o del número de origen de WhatsApp). Columnas resueltas vía INFORMATION_SCHEMA con degradación al esquema canónico. Retorna datos del cliente sanitizados o `null`.

**Adapters/ — Adaptadores por departamento (todos extienden `BaseAdapter`):**
- `Adapters/BaseAdapter.php` (abstracta): gestiona peticiones cURL a `http://localhost:11434/v1/chat/completions` con `qwen2.5-coder:3b` vía `NvidiaBrainClient` en modo endpoint único — CURLOPT_CONNECTTIMEOUT=3s, CURLOPT_TIMEOUT=25s (Fast-Fail estricto, sin reintentos ni failover a cloud). `sanitizar_utf8($datos)` recursivo (CP1252→UTF-8, regla de proyecto) aplicado a TODA salida. Contrato de retorno: `{success, respuesta, error, data}` — la `respuesta` NUNCA es vacía ni "No sé" (`respuestaFaltanParametros` lista los requeridos). Helpers: `preguntar`, `responder`, `parametrosFaltantes`, `conectar` (PDO PROFIT_SQL_*→PROFIT_DB_*→CRISTM25).
- `Adapters/WhatsappClienteAdapter.php`: autentica con CustomerAuthenticator; si falla → respuesta amigable pidiendo corregir RIF y últimos 4 dígitos; si valida → fuerza `ConsultarSaldoClienteTool` SOLO con el `co_cli` autenticado (ignora cualquier otro identificador del mensaje) y redacta la respuesta con el LLM (fallback determinista formateado). System Prompt: asistente oficial de atención al cliente; jamás datos de otros clientes.
- `Adapters/AlmacenAdapter.php`: picking/preparación/chequeo. **Fast-Track (< 3 ítems):** `mesa="0"` (Pasillo), `estado="AUTOCHEQUEO"`, `marca_tiempo` ISO 8601 y `/sond/finaliza.mp` SIN validación manual (decisión determinista ANTES del LLM; `fraseoConLlama` opt-in default false por velocidad operativa). ≥ 3 ítems → `PREPARADA` + validación manual. Params: `numero_nota`, `items_count` (faltantes → listados).
- `Adapters/FinanzasAdapter.php`: si hay `ruta_imagen` → `LeerVoucherOcrTool` extrae `{banco, referencia, monto, fecha}` (solo completa campos vacíos, nunca pisa valores explícitos); luego `ConciliarFacturaTool` invocado BAJO TRANSACCIÓN EXPLÍCITA de PDO (`beginTransaction`/`commit`/`rollBack` — cualquier Throwable revierte y reporta "operación revertida"). Params: `numero_factura`, `monto_conciliado`, `referencia_bancaria` (faltantes → listados).
- `Adapters/TecnologiaAdapter.php`: ejecuta `EXEC sp_IA_Obtener_Metricas` iterando rowsets con `nextRowset`. Auditoría de errores: SQLSTATE 08S01/08001/08003/08004/08006/08007 + patrones de socket ("broken pipe", "connection reset") → `tipo='red'` con recomendación de red/firewall; SP ausente (2812 en código o mensaje) → `tipo='consulta'` listando los requisitos; resto → `tipo='servidor'`.

**Verificación:**
- `php -l` OK en los 7 archivos (C:\tools\php\php.exe) ✓.
- Suite funcional 25/25: RBAC (7 casos: matrices, normalización de rol con tilde, rol inexistente) · autenticador (normalización J-123.456.789-5, últimos 4 dígitos, longitud < 4) · AlmacenAdapter fast-track (mesa 0, AUTOCHEQUEO, audio, marca de tiempo, respuesta no vacía) y manual (3 ítems) + faltan params listados · sanitizar_utf8 recursivo + json_encode sin fallo · WhatsApp faltan params · Finanzas faltan params (con y sin OCR).
- Probe REAL CRISTM25 (192.168.4.20): conexión PDO OK; `sp_IA_Obtener_Metricas` NO existe → respuesta estructurada `tipo='consulta'` con requisitos listados (nunca vacía) ✓.

**Nota:** el SP `sp_IA_Obtener_Metricas` no existe aún en CRISTM25; el adaptador ya está listo para consumirlo cuando se cree (probe devuelve los requisitos).

---

### v3.21 — Blindaje de arneses de prueba en bin/ (Pre-Flight + UTF-8 + excepciones) (2026-08-07)

**Objetivo:** capa de protección en `bin/test_nvidia_brain.php` (y cualquier arnés CLI futuro en `bin/`) para que ninguna falla de infraestructura (drivers faltantes, Ollama offline, caracteres corruptos de Profit/SQL Server) produzca un "Unhandled Fatal Error" de PHP.

**Cambios en `bin/test_nvidia_brain.php`:**

1. **Pre-Flight Check 1 — extensiones críticas:** `pdo_odbc`, `curl`, `mbstring` validadas con `extension_loaded()` ANTES de instanciar cURL/ODBC. Si falta alguna → JSON `{"status":"ERROR_DRIVER_MISSING","message":"Extensión PHP requerida no instalada: <ext>","code":500}` y `exit(500)`.
2. **Pre-Flight Check 2 — sondeo Ollama Fast-Fail:** `preflight_ollama_online()` hace GET `{OLLAMA_BASE_URL}/api/tags` con `CURLOPT_CONNECTTIMEOUT=1` (timeout de conexión estricto 1s) y tope absoluto 2s. Si no responde → advertencia en STDERR `[PreFlight] ⚠️ Ollama Local no está respondiendo en localhost:11434.` y se **blacklistea el puerto local** (`NvidiaBrainClient::blacklistEndpoint`) para que el pool de proveedores NO intente bucles locales y conmute directo a NVIDIA NIM/DeepSeek (Cloud-only).
3. **Manejo global de excepciones:** todo el flujo encapsulado en `try-catch (\Throwable $e)`; `emitir_fatal_error()` imprime JSON `{"status":"FATAL_ERROR","error","file","line"}` en STDERR + STDOUT con salida != 0 (2). Los `catch` internos (NvidiaBrainException → exit 1, Throwable → exit 2) se conservan.
4. **Sanitización UTF-8 recursiva:** `sanitizar_utf8_recursivo()` (mb_convert_encoding → UTF-8 con fallback Windows-1252/ISO-8859-1, recursiva en arrays y claves) aplicada al resultado del motor y a los mensajes de error — los datos de Profit nunca rompen `json_encode()` ni la consola.
5. **Códigos de salida:** 0 éxito / 1 NvidiaBrainException / 2 inesperado / 500 driver faltante.

**Verificación (PHP 8.4.24 portable descargado a `%TEMP%\opencode\php8` para pruebas, no se tocó el sistema):**
- `php -l` → "No syntax errors detected" ✓.
- Sin extensiones cargadas → `{"status":"ERROR_DRIVER_MISSING","message":"... pdo_odbc","code":500}` + exit 500 ✓ (exacto al spec).
- Con extensiones + Ollama ONLINE → ping `[PreFlight]` ONLINE, pool local intenta, timeout 25s → conmuta a NVIDIA Cloud ✓.
- Ollama OFFLINE (puerto cerrado) → `[PreFlight] ⚠️` + "estado: OFFLINE (degradado a Cloud-only)" + blacklist del puerto local, el pool va directo al Cloud sin bucles ✓.
- Prueba aislada de helpers: CP1252 (`\x99`→™, `\x97`→—) sanitizado, `json_encode` OK ✓; FATAL_ERROR con mensajes corruptos → JSON válido + exit 2 ✓.

---

### v3.22 — sp_IA_Obtener_Metricas creado en CRISTM25 + arnés bin/test_tecnologia_adapter.php (2026-08-07)

**Objetivo:** materializar el procedimiento almacenado de métricas del módulo Tecnología (TecnologiaAdapter) en el SQL Server real CRISTM25 (192.168.4.20:1433, profit/profit) y verificar el criterio de aceptación con un arnés CLI: `estatus_servicio = 'OK'` con la métrica de notas del día, sin excepciones ODBC.

**Cambios:**

1. **`app/Services/NvidiaBrain/database/sp_IA_Obtener_Metricas.sql`** (NUEVO): script del SP con guard `IF OBJECT_ID(...) IS NOT NULL DROP` y 4 conjuntos de resultados en orden (TecnologiaAdapter los itera con `nextRowset()`):
   - Conjunto 1 — `estatus_servicio='OK'`, `base_datos`, `fecha_servidor`, `fecha_operacion`, `usuario_conexion`.
   - Conjunto 2 — métrica de notas del día: `COUNT(*) FROM not_ent WHERE CAST(fec_emis AS DATE) = CAST(GETDATE() AS DATE) AND anulada = 0`.
   - Conjunto 3 — facturas del día (mismo patrón sobre `factura`).
   - Conjunto 4 — `total_clientes` y `clientes_inactivos` sobre `clientes`.
   - Columnas verificadas contra INFORMATION_SCHEMA real vía probe (not_ent/factura: `fec_emis smalldatetime`, `anulada bit`, `fact_num int`; clientes: `inactivo bit`).

2. **`bin/test_tecnologia_adapter.php`** (NUEVO): arnés CLI con las convenciones de `bin/test_nvidia_brain.php` — pre-flight de extensiones (`pdo_odbc`, `curl`, `mbstring` → `exit(500)` si falta alguna), PDO inyectado al adaptador (`new TecnologiaAdapter(pdo: $pdo)`), `sanitizar_utf8_recursivo()`, try/catch Throwable global (exit 2), y códigos de salida: 0 aceptado / 1 criterio fallido / 2 inesperado / 500 driver faltante. Verifica: `success=true`, `metricas[0][0].estatus_servicio='OK'` y `metricas[1][0].notas_del_dia` presente.

**Verificación (2026-08-07, CRISTM25 en vivo):**

- Runner temporal dividió el script en 3 lotes por `GO` y ejecutó vía `PDO::exec` ODBC (`Driver={SQL Server}`, PHP solo expone `sqlite, odbc`; sin driver `sqlsrv`): DROP + CREATE OK.
- EXEC directo de verificación: 4 conjuntos de resultados correctos.
- `php -l` → "No syntax errors detected" en arnés y adaptador ✓.
- `php bin/test_tecnologia_adapter.php` → `success: true`, `estatus_servicio: 'OK'`, `notas_del_dia: '121'` (115 → 121 en minutos, notas del día reales creciendo), facturas 2, clientes 4227 (692 inactivos); **exit 0 ACEPTADO** ✓.

---

### v3.23 — Suite unificada bin/test_adapters_suite.php (4 adaptadores en lote) + soporte `telefonos` en CustomerAuthenticator (2026-08-07)

**Objetivo:** ejecutar en lote los 4 adaptadores NVIDIA BRAIN (Almacen, WhatsApp, Finanzas, Tecnología) contra la infraestructura real (CRISTM25 vía PDO ODBC + Ollama local), consolidando latencia, estatus por adaptador y diagnóstico de infraestructura en un reporte JSON estructurado.

**Cambios:**

1. **`bin/test_adapters_suite.php`** (NUEVO, spec completa):
   - **Pre-Flight global:** extensiones `pdo_odbc`/`curl`/`mbstring` → si falta alguna, `exit(500)` con `{"status":"ERROR_DRIVER_MISSING",...}`; sondeo Ollama `/api/tags` (timeout conexión 1s, tope 2s) → si offline, advertencia `[PreFlight] ⚠️` en STDERR + `NvidiaBrainClient::blacklistEndpoint` (Cloud-only) sin frenar la suite.
   - **Caso almacen:** nota con `items_count=2` (< 3) → valida respuesta determinista `data.mesa="0"`, `estado="AUTOCHEQUEO"`, `audio="/sond/finaliza.mp"`, `modo="fast_track"`, `validacion_manual=false`, respuesta no vacía. Usa un `fact_num` real de `not_ent` del día (fallback sintético si la BD no responde; el caso es determinista y no depende de BD). Informativo: 3 ítems → `PREPARADA`.
   - **Caso whatsapp:** (a) **negativo** — credenciales inventadas → `success=false` con respuesta amigable y `formato_requerido`; (b) **positivo/restricción** — cliente real de `clientes` con `telefonos` poblado → factor secundario = últimos 4 dígitos → verifica que `data.cliente.co_cli` (autenticado) == identificador consultado (imposible consultar a otro cliente). El PASS no depende del LLM (solo de la restricción de contexto).
   - **Caso finanzas:** `SpyPDO extends PDO` (bitácora `eventos` de begin/commit/rollback sobre conexión real) + snapshot de una factura real (`factura.fact_num/saldo/status`) antes/después. Verifica: `begin` en eventos, transacción cerrada al final, respuesta no vacía, si `success=false` → `rollback` en eventos + informa "operación revertida", y que la factura real NO cambió. Estrategia anti-alteración: si `FacturasDrogueria` existe se usa un número sintético inexistente (`TEST-SUITE-*`) para que el tool nunca toque una factura real.
   - **Caso tecnologia:** PDO real a CRISTM25 → `EXEC sp_IA_Obtener_Metricas` → `estatus_servicio='OK'`, 4 conjuntos de resultados, `notas_del_dia` presente.
   - **Consolidador:** `sanitizar_utf8_recursivo()` en payloads de entrada y salidas; reporte JSON exacto al spec (`status`, `timestamp` ISO 8601, `preflight{pdo_odbc,ollama_local}`, `summary{total_tests,passed,failed,execution_time_ms}`, `results{almacen,whatsapp,finanzas,tecnologia}{passed,latency_ms,details}`) + resumen legible en STDOUT. Códigos de salida: 0 todo OK / 1 algún fallo / 2 inesperado / 500 drivers.

2. **`app/Services/NvidiaBrain/Security/CustomerAuthenticator.php`** (mejora de integración): añadido `'telefonos'` a los candidatos de `CAMPOS['telef']`. El esquema real de CRISTM25 usa `clientes.telefonos` (verificado vía probe INFORMATION_SCHEMA); sin esta columna la resolución devolvía `telef=null` y `validarCliente()` siempre retornaba `null` contra la BD real.

**Verificación (2026-08-07, CRISTM25 + Ollama en vivo):**

- `php -l` → "No syntax errors detected" en suite y autenticador ✓.
- `php bin/test_adapters_suite.php` → **status OK, 4/4 PASS, exit 0**:
  - almacen: 48ms — nota 72161354 fast-track mesa 0/AUTOCHEQUEO/audio OK; 3 ítems → PREPARADA ✓.
  - whatsapp: 14.4s (el LLM frasear la respuesta cayó a Cloud Fallback, 13s; el PASS es independiente del LLM) — negativo amigable OK + cliente real FAR01361 (FARMACIA BOTIMARKET, C.A) con restricción de contexto co_cli OK ✓.
  - finanzas: 81ms — `begin,rollback` en eventos, transacción cerrada, BD intacta (factura 80007950; `FacturasDrogueria` NO existe en CRISTM25 → se usó factura real sin riesgo) ✓.
  - tecnologia: 31ms — 4 conjuntos, estatus_servicio=OK, notas del día=140 ✓.
- PreFlight: pdo_odbc=si, ollama_local=ONLINE (aunque la consulta LLM real conmutó a Cloud Fallback, los adaptadores degradaron a fallback determinista sin romper el PASS) ✓.
- Regresión: suites funcionales previas 25/25 (prueba_nvidia_brain.php 20/20 + prueba_nvidia_brain2.php 5/5) ✓.

---

### v3.24 — bin/test_adapters_suite.php ajustado al formato spec del reporte JSON (2026-08-07)

**Cambios en `bin/test_adapters_suite.php`** (los 4 casos de prueba ya verificados en v3.23 no cambian; solo el contrato de salida):

- `summary` renombrado al spec: `{total, passed, failed, total_time_ms}` (antes `total_tests`/`execution_time_ms`).
- `results.{adaptador}.data` ahora es un payload estructurado sanitizado (antes `details` string): almacen `{numero_nota, mesa, estado, audio, modo, validacion_manual, manual_3_items}`; whatsapp `{negativo_amigable, cliente_autenticado, cliente_nombre, restriccion_co_cli}`; finanzas `{eventos_transaccion, transaccion_cerrada, bd_intacta, factura_referencia, numero_prueba, respuesta}`; tecnologia `{estatus_servicio, conjuntos, notas_del_dia, base_datos}`; todos conservan `detalle` legible.
- Preflight de Ollama: si está offline se añade `preflight.ollama_advertencia` al reporte (además del `[PreFlight] ⚠️` en STDERR y la blacklist Cloud-only).
- Códigos de salida: 0 (4/4) / 1 (algún fallo) / 2 (inesperado) / 500 (drivers).

**Verificación (2026-08-07, en vivo):**

- `php -l` → "No syntax errors detected" ✓.
- `php bin/test_adapters_suite.php` → **exit 0, `status: "OK"`, `summary {total:4, passed:4, failed:0, total_time_ms:5098}`**, JSON válido según spec ✓.
- almacen 48ms (nota 72161359, mesa "0"/AUTOCHEQUEO//sond/finaliza.mp) · whatsapp 4959ms (negativo amigable + FAR01361 FARMACIA BOTIMARKET restricción co_cli OK) · finanzas 57ms (`begin,rollback`, transacción cerrada, BD intacta factura 80007950) · tecnologia 34ms (estatus_servicio=OK, 4 conjuntos, notas del día=150) ✓.
- PreFlight: pdo_odbc=true, ollama_local=true ✓.

---

### v3.25 — Sustitución deepseek-ai/deepseek-v4-flash → llama-3.3-70b-instruct + arnés bin/test_nvidia_cloud_forced.php (2026-08-07)

**Objetivo:** sustituir `deepseek-ai/deepseek-v4-flash` (EOL HTTP 410 el 2026-08-07T09:00:00Z, ya eliminado del código activo) por `meta/llama-3.3-70b-instruct` en `NvidiaBrainClient`, y crear un arnés forzado a NVIDIA Cloud que valide con la API key actual que el modelo responde HTTP 200 exacto, sin ping previo a Ollama.

**Cambios:**

1. **`app/Services/NvidiaBrain/NvidiaBrainClient.php`:** sustituidas TODAS las referencias a `deepseek-ai/deepseek-v4-flash` (docblock de cabecera, comentario del timeout cloud, doc de `NIM_FALLBACK_MODEL`, doc del pool, docblock de `chatCompletions` y comentario interno del manejo 410/404). `NIM_FALLBACK_MODEL` y `DEFAULT_MODEL_POOL` quedan con la configuración final decidida con evidencia (ver Verificación): **`meta/llama-3.1-8b-instruct` primario (verificado HTTP 200) + `meta/llama-3.3-70b-instruct` failover de capacidad**. También `app/Services/NvidiaBrain/NvidiaBrain.php` (docblock del pool).
2. **`bin/test_nvidia_cloud_forced.php`** (NUEVO): arnés según spec — pre-flight `curl`+`mbstring` (→ exit 500 `ERROR_DRIVER_MISSING`) y carga de API key (`NVIDIA_BRAIN_API_KEY` → `NVIDIA_API_KEY_1` → `.env`; sin key → JSON error + exit 1); instancia `NvidiaBrainClient` en modo legacy con la URL NIM explícita (el alias `'nvidia'` del constructor no está implementado; se documenta en el arnés), `model=meta/llama-3.3-70b-instruct`, timeouts 5s/30s, `maxRetries=0`; prompt de operatividad del Proyecto ARA; valida `http_code===200` exacto (expuesto en el contrato del cliente); HTTP 410/404 (y cualquier no-200) → JSON de error con detalle + exit 1; `sanitizar_utf8_recursivo()` al texto. **`putenv('NVIDIA_BRAIN_MODELS=' . $MODELO)`** fuerza un pool de un solo modelo: la rotación interna del cliente no debe enmascarar el resultado del modelo validado.

**Verificación (2026-08-07, en vivo contra integrate.api.nvidia.com):**

- `php -l` OK en cliente, `NvidiaBrain.php` y arnés ✓.
- `php bin/test_nvidia_cloud_forced.php` → **exit 1 (honesto)**: `{"status":"ERROR","provider":"nvidia_cloud","model":"meta/llama-3.3-70b-instruct","http_code":28,"latency_ms":30011,"error":"Error cURL (28): Operation timed out after 30005 milliseconds with 0 bytes received"}` — **el 70b NO responde dentro del Fast-Fail de 30s con la key actual** (0 bytes recibidos).
- Intento previo sin restricción de pool: el 70b hizo timeout y el cliente rotó a `meta/llama-3.1-8b-instruct` → **HTTP 200 en segundos** ("Estoy operativo y listo para brindar soporte y asistencia en el Proyecto ARA.").
- Decisión del usuario (evidencia en mano): **8b primario + 70b failover** (evita pagar 30s de timeout en cada fallback cloud).
- Verificación del pool productivo (`NVIDIA_BRAIN_MODELS` vacío → `DEFAULT_MODEL_POOL`): `{"success":true,"http_status":200,"model":"meta/llama-3.1-8b-instruct","latency_ms":1566}` ✓ — el cloud responde directo con el primario verificado, sin degradación.

---

### v3.26 — Verificación E2E de autenticación de clientes (CustomerAuthenticator vs CRISTM25) + modo auth en bin/test_nvidia_brain.php (2026-08-07)

**Objetivo:** validar contra la base real (CRISTM25, PDO ODBC 192.168.4.20:1433, credenciales `profit`/`profit`) la conexión y la lógica de `CustomerAuthenticator::validarCliente()` con el cliente de prueba FAR01361 (FARMACIA BOTIMARKET, C.A), y ampliar `bin/test_nvidia_brain.php` con un modo CLI de autenticación que mantiene intacto el flujo E2E por número de nota.

**Mapeo de esquema real Profit Plus (CRISTM25 - SQL Server):**

- Tabla: `clientes`.
- Campo de teléfono: **`telefonos`** — columna real verificada vía `INFORMATION_SCHEMA` (las candidatas `telef`/`telefono` NO existen en CRISTM25); `CAMPOS['telef']` de CustomerAuthenticator la resuelve dinámicamente (desde v3.23).
- Normalización de identificadores (`normalizarIdentificador()` + `coincidenciaNormalizada()` con `REPLACE(UPPER(col),' ','')`/puntos/guiones):
  - `co_cli`: tolera espacios iniciales/finales (`LTRIM`/`RTRIM`) — el registro real es `' FAR01361'` (espacio inicial) y matchea igual.
  - `rif`: sanitización alfanumérica pura (elimina guiones, puntos y espacios): `'  J-293563283  '` → `'J293563283'`.
  - `auth_factor`: coincidencia por los últimos 4 dígitos del campo `telefonos` (sufijo `LIKE '%:auth_factor'`): `584146578405` → `8405`.

**Cambios en `bin/test_nvidia_brain.php`:**

1. **Modo autenticación** (se activa solo si aparecen las banderas `--rol`, `--paso0`, `--co_cli`, `--auth_factor`, `--query`):
   - Parseo de flags `--clave=valor` (`nvidia_brain_flag()`) + conexión PDO de solo lectura `conectar_profit_read()` (sqlsrv si está disponible, si no ODBC `{SQL Server}`; `ATTR_TIMEOUT=8`).
   - `require` + `use` de `app/Services/NvidiaBrain/Security/CustomerAuthenticator.php`.
   - `--paso0`: extracción de datos reales — `SELECT co_cli, cli_des, RTRIM(rif), RTRIM(telefonos) FROM clientes WHERE LTRIM(RTRIM(co_cli)) = ?` + `ultimos_4` + columnas de teléfono disponibles vía INFORMATION_SCHEMA.
   - `validarCliente()`: éxito → `{"status":"success","data":{"authenticated":true,"cliente":{co_cli,cli_des,rif}},"message":"Cliente autenticado exitosamente."}` **exit 0**; `null` → `{"status":"error","data":null,"message":"Acceso denegado: Los datos de validación no coinciden con nuestros registros."}` **exit 1** (error controlado).
   - Salida JSON estructurada UTF-8 (`JSON_UNESCAPED_UNICODE` + `sanitizar_utf8_recursivo()`), libre de caracteres CP1252 no válidos.
2. **Sin flags** → flujo E2E original intacto (consulta por número de nota, pool Ollama → NVIDIA).

**Registro de pruebas E2E (cliente FAR01361 - FARMACIA BOTIMARKET, C.A):**

- **Paso 0:** `telefonos = 584146578405` → `auth_factor = 8405`; `rif = J293563283`; columnas de teléfono disponibles: solo `telefonos` ✓.
- **Caso A (positivo):** `--co_cli="FAR01361" --auth_factor="8405"` → **exit 0**, `authenticated: true`, `cli_des="FARMACIA BOTIMARKET, C.A"`, `rif="J293563283"` ✓.
- **Caso B (negativo):** `--co_cli="FAR01361" --auth_factor="0000"` → **exit 1**, "Acceso denegado: Los datos de validación no coinciden con nuestros registros." ✓.
- **Caso C (sanitización RIF):** `--co_cli="  J-293563283  " --auth_factor="8405"` → **exit 0**, match por `rif` sanitizado (co_cli autenticado: FAR01361) ✓.

**Notas:**

- El ejemplo literal `'J-FAR01361-DIR'` de la especificación no coincide con ningún registro real (el RIF no contiene el co_cli); el CASO C usó el RIF real enmarcado con espacios/guiones, como indica la propia spec.
- El `co_cli` real lleva espacio inicial (`' FAR01361'`): la consulta del autenticador lo resuelve por la normalización REPLACE sobre `co_cli`/`rif` (el PASO 0 usa `LTRIM(RTRIM(...))` para la extracción).
- Regresión E2E sin flags: **Status OK** (Ollama local, 1 iteración, 5.2s, exit 0) ✓.

---

### v3.26 — Dashboard Ejecutivo ARA: obtenerMetricasDirectas + endpoint public/api/metricas.php + public/dashboard.html (2026-08-07)

**Objetivo:** métricas operativas en tiempo real con lectura PDO directa (< 40ms) sin inferencia de IA, expuestas por endpoint JSON y consumidas por un dashboard estático con sondeo blindado (AbortController 5s).

**Cambios:**

1. **`app/Services/NvidiaBrain/Adapters/TecnologiaAdapter.php`** — nuevo método público `obtenerMetricasDirectas(): array`: `EXEC sp_IA_Obtener_Metricas` sin LLM, iterando TODOS los conjuntos de resultados (`nextRowset`) y fusionándolos en un array plano: `{estatus_servicio, base_datos, fecha_servidor, fecha_operacion, usuario_conexion, fecha, notas_del_dia, facturas_del_dia, total_clientes, clientes_inactivos, clientes_activos}`; `clientes_activos = total_clientes - clientes_inactivos` (KPI del dashboard). Sanitización con `self::sanitizar_utf8()`. Lanza `PDOException` si la conexión falla (el llamador decide).
   - *Correcciones al boceto propuesto*: (a) `$this->pdo` puede ser `null` → `$pdo = $this->pdo ?? $this->conectar()` (el boceto hacía `$this->pdo->prepare()` → fatal si null); (b) `$this->sanitizar_utf8_recursivo()` no existe en el adaptador → `self::sanitizar_utf8()` (estático público de BaseAdapter); (c) el boceto solo leía el 1er rowset (estatus) → el dashboard necesita los 4 conjuntos, por eso se itera y fusiona.
2. **`app/Core/conectar_profit_read.php`** (NUEVO): `conectar_profit_read(): PDO` de SOLO LECTURA (patrón del módulo: sqlsrv si disponible, si no ODBC `{SQL Server}`; `PROFIT_SQL_*` → `PROFIT_DB_*` → defaults profit/profit 192.168.4.20:1433 CRISTM25; `ATTR_TIMEOUT=8`). No existía en el repo; el endpoint lo requiere.
3. **`public/api/metricas.php`** (NUEVO): endpoint de lectura pura — headers JSON + no-store, `conectar_profit_read()`, `TecnologiaAdapter(pdo: $pdo)`, `obtenerMetricasDirectas()`, respuesta `{status:'success', timestamp, data}`; `Throwable` → HTTP 500 con `{status:'error', message, details}`.
   - *Correcciones al boceto propuesto*: (a) `use App\Services\NvidiaBrain\Adapters\TecnologiaAdapter;` — sin él la clase namespaced no se resuelve y falla con "Class not found" (verificado en vivo); (b) `new TecnologiaAdapter(pdo: $pdo)` — el boceto pasaba `$pdo` como 1er parámetro (que es `?NvidiaBrainClient` → TypeError); (c) `require NvidiaBrainClient.php` (BaseAdapter::crearClienteOllama lo instancia cuando `$client` es null).
4. **`public/dashboard.html`** (NUEVO): estático tal cual spec — Tailwind CDN, 4 tarjetas KPI (Estatus del Motor IA, Notas del Día, Facturas Emitidas, Clientes Activos), sondeo cada 60s con guard anti-reentrada (`isFetching`), **AbortController 5s** anti-colgado, indicador de conexión (verde/ámbar), sello "Última act".

**Verificación (2026-08-07, CRISTM25 en vivo):**

- `php -l` OK en TecnologiaAdapter, metricas.php y conectar_profit_read.php ✓.
- `php public/api/metricas.php` → **JSON válido** `{"status":"success","timestamp":"...","data":{"estatus_servicio":"OK","notas_del_dia":"243","facturas_del_dia":"43","total_clientes":"4228","clientes_inactivos":"693","clientes_activos":3535,...}}` ✓ (el primer intento con el boceto crudo falló con "Class not found" → corregido con el `use`).
- **Latencia pura del método con conexión abierta: 6ms** (< 40ms objetivo) ✓; el endpoint CLI total (arranque PHP + conexión + SP) 153ms.
- `php bin/test_adapters_suite.php` → **4/4 PASS, exit 0** (almacen/whatsapp/finanzas/tecnologia, total_time_ms 6598) ✓ — sin regresión por el nuevo método.

---

### v3.27 — Webhook de WhatsApp: WhatsAppWebhookController + public/api/whatsapp/webhook.php + arnés bin/test_whatsapp_webhook.php (2026-08-07)

**Objetivo:** recibir mensajes entrantes de WhatsApp de forma multi-proveedor (Meta Cloud API / Baileys / WPPConnect / Evolution API), autenticar al remitente contra CRISTM25 por su número de teléfono y responder con el WhatsappClienteAdapter en JSON limpio < 5s.

**Cambios:**

1. **`app/Http/Controllers/WhatsAppWebhookController.php`** (NUEVO, namespace `App\Http\Controllers`, primera carpeta `app/Http/` del repo):
   - `handle(method, query, rawBody)` → enruta GET/POST y anexa `timestamp` ISO 8601 + `latency_ms` a la respuesta JSON.
   - **GET (verificación Meta):** compara `hub.verify_token` contra `WHATSAPP_WEBHOOK_VERIFY_TOKEN` (env `.env`; default documentado `ARA_PROYECT_WEBHOOK_2026` si no existe) con `hash_equals` y responde `hub.challenge` como texto plano HTTP 200; token inválido → 403 JSON.
   - **POST (payloads multi-proveedor)** — `extraerMensaje()` normaliza a `{numero, texto}`:
     - Meta Cloud API: `entry[].changes[].value.messages[]` (`from` + `text.body`).
     - Baileys / Evolution API: `data.key.remoteJid` + `message.conversation` / `extendedTextMessage.text`; `key.fromMe` → ignorado; `messageType=protocolMessage` → ignorado.
     - WPPConnect / directos: `{from, message|text|body}`.
     - **Ignorados (HTTP 200 `status:'ignored'`):** notificaciones `statuses` (sin `messages`), mensajes propios, protocol messages, números < 8 dígitos, payloads no reconocidos — evita loops de respuesta.
   - **Autenticación** — `autenticarPorTelefono()`: limpia el número a dígitos, toma los últimos 4 y busca candidatos en `clientes` con `RIGHT(REPLACE(REPLACE(LTRIM(RTRIM(telefonos)),' ',''),'-',''),4) = ?` (TOP 10, `inactivo=0`), luego valida cada candidato con `CustomerAuthenticator::validarCliente(PDO, co_cli, ultimos4, digitosCompletos)` — factor secundario = últimos 4 del teléfono registrado (o el origen completo). Sin match → respuesta amigable SIN exponer datos (`client: null`).
   - **Procesamiento:** `WhatsappClienteAdapter` con `NvidiaBrainClient` ACOTADO (endpoint único `OLLAMA_BASE_URL`, `OLLAMA_MODEL` qwen2.5-coder:3b, `timeoutS=2`, `connectTimeout=1`, `maxRetries=0` — respeta el blindaje de BaseAdapter y garantiza < 5s; si el LLM falla, el adaptador degrada a su fallback determinista). Contexto `{identificador: co_cli, factor_secundario: ultimos4, origen_whatsapp: numero}` → saldo del cliente SOLO del co_cli autenticado.
   - **Envío externo opcional best-effort:** si `WHATSAPP_WEBHOOK_REPLY_URL` está configurada, POST `{to, message}` con timeouts 2s/3s (nunca bloquea la respuesta).
   - **Robustez:** todo `Throwable` → HTTP 200 JSON con error genérico + `error_log` (nunca se exponen detalles ni excepciones al proveedor); mini cargador `.env` propio (solo claves del webhook, precedencia getenv) sin depender de otras librerías; helper `limpiarNumero()`.
2. **`public/api/whatsapp/webhook.php`** (NUEVO): headers JSON + no-store, `require` absoluto de todas las dependencias (controlador, `conectar_profit_read`, NvidiaBrainClient, AgentToolInterface, ConsultarSaldoClienteTool, CustomerAuthenticator, BaseAdapter, WhatsappClienteAdapter), delega en `WhatsAppWebhookController`, imprime texto plano para el challenge y JSON para el resto; **modo CLI de prueba** (argv simula método/query/`--payload=`, stdin como fallback); `Throwable` global → 500 JSON genérico.
3. **`bin/test_whatsapp_webhook.php`** (NUEVO): arnés con pre-flight `pdo_odbc` y 10 casos: GET token correcto/incorrecto, POST JSON inválido (400), statuses Meta (ignored), fromMe Baileys (ignored), número < 8 dígitos (ignored), número no registrado → reply amigable sin datos, Baileys conversation → CLI00007 autenticado, Meta mensaje texto → saldo real de CLI00007 (CENTRO MEDICO QUIRUGICO DR. PLATA C.A), WPPConnect directo → autenticado. Salida JSON spec `{summary{total,passed,failed,total_time_ms}, results[]}`; exit 0 / 1.

**Verificación (2026-08-07, CRISTM25 en vivo):**

- `php -l` OK en controlador, endpoint y arnés ✓.
- `php bin/test_whatsapp_webhook.php` → **10/10 PASS, exit 0, total_time_ms 6365**:
  - wh-7 número no registrado → 66ms, reply amigable con `client:null` ✓.
  - wh-8 Baileys → **3527ms**: LLM local falló (HTTP 28) → rotó a nube (404s en esta máquina) → **fallback determinista del adaptador respondió** (blindaje < 5s verificado en vivo) ✓.
  - wh-9 Meta → 1473ms, reply "Hola CENTRO MEDICO QUIRUGICO DR. PLATA C.A, su saldo actual es Bs. 0..." ✓.
  - wh-10 WPPConnect → 1298ms ✓.
- Regresión `bin/test_adapters_suite.php` → **4/4 PASS, exit 0** (total_time_ms 10529; el caso whatsapp usa Cloud Fallback 10.4s, su PASS es independiente del LLM) ✓.
- Nota de infraestructura: PowerShell 5.1 mangla comillas en args de procesos nativos → los payloads JSON se prueban vía el arnés PHP (handle() directo), no inline en la consola.

---

### v3.28 — Registro de arquitectura "ARA Intelligent & Bandeja" (integración Bandeja ↔ Profit Plus/CRISTM25 ↔ Motor Híbrido) (2026-08-07)

**Objetivo:** registrar el mapa spec→implementación de la integración del módulo Bandeja (mensajería entrante) con los adaptadores ERP (Profit Plus / CRISTM25) y el motor de inferencia híbrido (Ollama Local + NVIDIA NIM Cloud). La arquitectura documentada en la spec ya está implementada en el repo con nombres distintos; esta bitácora fija el mapeo oficial y las brechas conocidas.

**Mapeo spec → implementación real:**

| Componente (spec) | Implementación real en el repo |
|---|---|
| `BandejaController` (`app/Http/Controllers/BandejaController.php`): POST obligatorio `telefono` + `mensaje`, JSON unificado, errores estructurales | `app/Http/Controllers/WhatsAppWebhookController.php` — mismo rol y contrato (POST JSON + verificación GET `hub_challenge` Meta), entrada HTTP en `public/api/whatsapp/webhook.php`; responde siempre JSON limpio con `timestamp` + `latency_ms`, nunca expone detalles de error |
| `CustomerAuthenticator` (RBAC de clientes, respuestas amigables a no autenticados) | `app/Services/NvidiaBrain/Security/CustomerAuthenticator.php` — autentica contra `clientes` (CRISTM25) con factor secundario (últimos 4 dígitos de `telefonos`); cliente no hallado → respuesta amigable genérica sin datos sensibles |
| `WhatsappClienteAdapter` con regla `WITH (NOLOCK)` obligatoria en TODAS las consultas | `app/Services/NvidiaBrain/Adapters/WhatsappClienteAdapter.php` — funciona; **⚠️ la regla NOLOCK NO está implementada** (ningún PHP del repo usa el hint; ver Pendientes) |
| `HybridAiClient`: primario Ollama `qwen2.5-coder:3b` (timeout ≤ 2.5s, temperatura 0.1, respuestas directas sin fluff) + failover NVIDIA NIM `meta/llama-3.1-8b-instruct` | `app/Services/NvidiaBrain/NvidiaBrainClient.php` + `Adapters/BaseAdapter.php` — pool Ollama→NIM (8b primario cloud + 70b failover, v3.25); `BaseAdapter::preguntar()` fuerza `temperature=0.1`, `max_tokens=300` y antepone `SYSTEM_PROMPT_ANTI_FLUFF` (blindaje v3.21); el webhook inyecta cliente LLM acotado `timeoutS=2`/`connectTimeout=1`/`maxRetries=0` |
| `routes/api.php` → `/v1/bandeja/mensaje` | No existe `routes/api.php` (sin framework Laravel); el endpoint real es `public/api/whatsapp/webhook.php` (entry HTTP directo) |

**Verificación (2026-08-07):**

- El webhook tiene arnés propio: `bin/test_whatsapp_webhook.php` (verificación GET, payload inválido, multi-proveedor Meta/Baileys/WPPConnect, no-autenticado → respuesta amigable, cliente real → reply).
- Autenticación E2E contra CRISTM25 verificada en v3.26 (FAR01361: Casos A/B/C, exit 0/1/0).
- Restricción de contexto del adapter (solo consulta el `co_cli` autenticado) verificada en v3.23/v3.24 (FAR01361, PASS 4/4).

**Métricas y restricciones de la spec (estado):**

- SQL Server: cero locks/blocking en CRISTM25 — ⚠️ pendiente implementar `WITH (NOLOCK)` en las consultas read-only del módulo (WhatsappClienteAdapter, CustomerAuthenticator, WhatsAppWebhookController).
- Latencia global < 5s: cumplido por diseño (LLM acotado ≤ 2s + fallback determinista del adaptador; objetivo declarado en el controlador).
- Manejo defensivo de excepciones con logs seguros: cumplido (`try/catch Throwable` → respuesta genérica + `error_log` interno).

---

### v3.29 — Cierre transaccional del autochequeo: gate 1:1 + sync por ítem en reng_nde (ARA_SYNC) (2026-08-07)

**Objetivo:** corregir de raíz el ciclo de errores del autochequeo del picking — el PHP Legacy (`visor/registro.php`) responde **"Faltan artículos por cargar. Si finaliza la nota sera enviada a revision"** y la nota entra al loop de revisión. La corrección cruza el estado REAL de escaneo (`reng_nde`) antes del POST y bloquea el cierre cuando la nota no está 100% surtida, sin enviarla a revisión.

**Cambios (rama de preparación, `preparacion/`):**

1. `profit_renglones_sync.py`:
   - Nuevo `estado_escaneo_nota(numero_nota)` (solo lectura): resuelve la tabla real (`reng_nde` → `reng_ndd` → `reng_fac` → `reng_nd`) y reporta por renglón `solicitada`/`escaneada` (`stotal_art`/`cant_imp`/`cant_prod`)/`pendiente` + `estatus` `PENDIENTE|ESCANEADO|COMPLETO`, con totales y `completo` (bool). Nunca lanza excepción.
   - `marcar_renglones_cargados()` ahora acepta `renglones_escaneados` (list[{co_art, cantidad}]): sync **por ítem** — cantidad cargada = cantidad escaneada real, `pendiente = total − escaneado`, `seleccion = 1` solo si hubo escaneo (el modo v3.19 sin parámetro conserva el marcado total 100%).
2. `legacy_http_adapter.py`: `registrar_chequeo_legacy()`, `registrar_autochequeo_php()` y `registrar_despacho_legacy()` aceptan y propagan `renglones_escaneados` al sync; log `[ARA_SYNC] ⚠️ parcial` vs `✅ completo`.
3. `preparation_service.py` — **GATE DE INTEGRIDAD 1:1**: una nota con > 3 ítems (autochequeo extendido) NO se cierra cuando la validación solicitada/escaneada falla: retorna `{"status":"error","codigo_error":"NOTA_INCOMPLETA", "discrepancias":[...], "estado_bd": <estado actual>}` **sin tocar la BD local ni el Legacy** (permanece en su estado intermedio real, jamás a revisión). Nuevo `_normalizar_renglones_escaneados()` (fuente 1: formulario PHP de chequeo `chequeada/escaneada/cantidad`; fallback: `cantidad_preparada` local) y las cantidades reales se pasan a `registrar_despacho_legacy`/`registrar_autochequeo_php`.
4. `domain/models.py` + `profit_sql_adapter.py`: `ItemNota` gana `cantidad_escaneada` (default 0) y `estatus` (default `PENDIENTE`), poblados desde `reng_nde` (`_CAND_CARGADA`/`_CAND_PENDIENTE`); el JSON de la nota (`_formatear_nota`/`_formatear_nota_local`) expone `cantidad_requerida`, `cantidad_escaneada` y `estatus` por ítem (requerimiento 1 de la directiva).
5. `domain/ports.py`: firmas actualizadas con `renglones_escaneados: Optional[list]` en `registrar_despacho_legacy` y `registrar_autochequeo_php` (+ `hora: Optional[str]`).

**Verificación (2026-08-07):**

- `py_compile` OK en los 6 archivos tocados.
- Probe read-only real contra CRISTM25 (192.168.4.20): `estado_escaneo_nota('72161167')` → `reng_nde`, 2 renglones (MD01006 x8, MD01007 x8), escaneada 0, ambos `PENDIENTE`, `completo=false` (0.09s); `72161071` → MD01539 x6 + MISC0205 x8 = 14, ambos `PENDIENTE`. Sin excepción.
- Test del gate (mocks, sin tocar BD real): **CASO A** 5 ítems con 4/5 completos → `NOTA_INCOMPLETA` con 1 discrepancia `FALTANTE`, cero llamadas a `finalize_nota`/`assign_preparer`/registros legacy; **CASO B** 5/5 completos → success, `renglones_escaneados` con las 5 cantidades reales propagados a `registrar_despacho_legacy` y `registrar_autochequeo_php`; **CASO C** sin `items_chequeados` → fallback a `cantidad_preparada` local completa, sync 5/5. TODOS PASARON.

**Pendientes:**

- E2E real de cierre sigue bloqueada por la sesión PHP del Legacy en 192.168.4.148:8000 (el `visor/registro.php` desplegado exige sesión activa; el `chequeo/registro.php` del repo no tiene ese guard) y por credenciales MySQL del `.148` no disponibles.
- `lista.php` del repo (`legacy_visor/`) es el visor de rutas (DB ProfitPlus), NO el JSON de ítems por nota — el enriquecimiento real vive en Python (hecho en el punto 4); confirmar con el frontend que el picking consume `cantidad_escaneada`/`estatus`.

---

### v3.30 — Conexión nativa MySQL legacy para ARA_SYNC (pool 30 + transacción directa) (2026-08-07)

**Objetivo:** eliminar la dependencia de la sesión web PHP (PHPSESSID) en `visor/registro.php`/`chequeo/registro.php` ejecutando la MISMA transacción de forma NATIVA en la BD MySQL del servidor legacy (192.168.4.148:3306), con las credenciales provistas por directiva.

**Cambios:**

1. `.env` + `.env.example`: `MYSQL_HOST=192.168.4.148`, `MYSQL_USER=root`, `MYSQL_PASSWORD=10445610`, `MYSQL_PORT=3306`, `MYSQL_CONNECTION_LIMIT=30` (en `.env.example` la contraseña va vacía).
2. `preparacion/infrastructure/adapters/legacy_mysql_sync.py` (nuevo, `LegacyMySQLConnector`):
   - Pool perezoso de conexiones `pymysql` con límite `MYSQL_CONNECTION_LIMIT` (default 30): préstamo/liberación thread-safe, conexiones caídas se revalidan, sobrantes se cierran.
   - Resolución dinámica de esquema vía `information_schema` (tablas candidatas `rep_not/notas/notas_entrega/notas_despacho/nota`; bitácoras `despachos` y `log_chequeo/chequeos`; columnas estado/nota/fecha/usuario/monto/items).
   - `confirmar_despacho(nota)` ≈ `visor/registro.php`: UPDATE estado='PROCESADA' + bitácora, UNA transacción con commit.
   - `confirmar_chequeo(nota, responsable, monto, items)` ≈ `chequeo/registro.php`: UPDATE estado='CHEQUEADA' + bitácora.
   - `verificar()` (ping + esquema, solo lectura) y `modo_real=False` (dry-run que reporta el SQL sin escribir). Best-effort: nunca lanza excepción.
3. `preparacion/infrastructure/adapters/legacy_http_adapter.py`: nuevo parámetro `mysql_connector` en el constructor + helper `_confirmar_mysql()`; `registrar_chequeo_legacy` (despacho) y `registrar_autochequeo_php` intentan PRIMERO la confirmación MySQL directa — éxito → retorno `via='mysql_directo'` (+ sync de renglones best-effort); fallo/no disponible → flujo HTTP actual intacto como respaldo.
4. `bin/test_legacy_mysql_sync.py` (nuevo, arnés CLI): [1/4] conexión+ping+esquema candidato (solo lectura), [2/4] pool hasta el límite+2 con devolución, [3/4] dry-run de despacho y chequeo (reporta el SQL), [4/4] `--confirmar` ejecuta la escritura real. Exit: 0 OK / 1 conexión / 2 esquema.

**Verificación (2026-08-07):**

- `py_compile` OK en los 3 archivos (conector, adapter HTTP, arnés).
- Arnés ejecutado: lee `.env` (host/port/user/limit OK) y llega a conexión — **exit 1 con diagnóstico claro**: `(1045, "Access denied for user 'root'@'Bqto1' (using password: YES)")`.
- Diagnóstico: puerto 3306 ABIERTO en .148 (`Test-NetConnection` OK) pero MySQL rechaza `root` con la contraseña de la directiva desde la máquina local (host reportado `Bqto1`). Probadas sin éxito: `10445610`, vacía, `root`, `1234`. NO se hizo fuerza bruta adicional.
- Impacto en runtime: conector no disponible → el adapter hace fallback automático al flujo HTTP previo (cero regresión).

**Pendientes:**

- Re-ejecutar `php bin/../ara/venv/Scripts/python.exe bin/test_legacy_mysql_sync.py` (o el .py directo) cuando la BD acepte la credencial; opciones: corregir `MYSQL_PASSWORD` en `.env`, crear usuario dedicado `ara_app@'%'` con GRANT en el .148, o indicar credencial alternativa.
- Cuando el esquema responda: confirmar que las tablas reales de la BD legacy coinciden con las candidatas del conector (el arnés las reporta en [1/4]).

---

### v3.31 — Emulación de navegador legítimo en legacy_http_adapter.py (sesión PHP + form clásico) (2026-08-07)

**Objetivo:** el Legacy (.148) rechazaba las peticiones externas o las desviaba a revisión porque detectaba falta de sesión PHP activa (`PHPSESSID`) y porque el cliente enviaba payloads que el `$_POST` tradicional no interpreta. La actualización emula una sesión de navegador legítima en el cliente HTTP de Python.

**Cambios en `preparacion/infrastructure/adapters/legacy_http_adapter.py`:**

1. `HEADERS_NAVEGADOR` (constante): `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) ARA_SYNC_Bridge/3.30`, `Accept: text/html,...`, `Accept-Language: es-VE,es`, `Upgrade-Insecure-Requests: 1` (Origin y Referer se componen dinámicamente con `base_url`).
2. `_sesion_legacy(path, abrir_sesion)`: crea `requests.Session()` con las firmas de navegador y, si `abrir_sesion=True`, hace un **pre-request GET rápido a la raíz del directorio** (`/visor/`, `/chequeo/`) para capturar y persistir el `PHPSESSID` ANTES del POST de confirmación. Tolerante: si no hay cookie se continúa igual.
3. `_post_path(path, datos, abrir_sesion, sesion)`: **forzado `data={...}`** (nunca `json=`) → `Content-Type: application/x-www-form-urlencoded`; headers explícitos `Content-Type`, `Referer` (directorio del endpoint: `/visor/` o `/chequeo/`) y `User-Agent`; acepta sesión reutilizable. `_post` (assign_preparer) conserva `abrir_sesion=False` (sin regresión).
4. Payload anti-loop de revisión en `registrar_chequeo_legacy` y paso 3 de `registrar_autochequeo_php`: TODOS los campos obligatorios (`responsable`, `nota`, `registro`, `monto`, `descp`) + banderas de cierre para notas 100% validadas por `reng_nde`: `articulos_cargados=1`, `validado_reng_nde=1`, `bypass_revision=1`, `renglones_ok=1` (todas `0` si el sync quedó parcial — nunca se fuerza un bypass falso). Las versiones del PHP desplegado que no las reconocen las ignoran sin perjuicio.
5. Flujo `registrar_autochequeo_php`: añade paso 0 (pre-GET a `/chequeo/` con captura de PHPSESSID) y las firmas de navegador en la sesión de los 3 pasos.

**Verificación (2026-08-07):**

- `py_compile` OK; regresión del gate NOTA_INCOMPLETA: TODOS LOS CASOS PASARON.
- Test de emulación con servidor HTTP local simulando el Legacy (3 casos): **CASO 1** renglones 100% → Content-Type form-urlencoded, UA `ARA_SYNC_Bridge/3.30`, Referer `/visor/`, Origin dinámico, cookie `PHPSESSID` capturada en pre-request, campos obligatorios presentes y banderas=1; **CASO 2** renglones parciales → banderas=0 (sin bypass falso); **CASO 3** `_post` (assign_preparer) sin pre-request (sin regresión). TODOS PASARON.
- Probe read-only real contra `.148:8000` (solo GET, cero escritura): `/visor/` → HTTP 200 + `PHPSESSID` ✓; `/chequeo/` → HTTP 200 + `PHPSESSID` ✓; `/visor/registro.php` → HTTP 200 + `PHPSESSID` ✓ — el servidor real emite la cookie de sesión con las firmas de navegador.

**Pendientes:**

- E2E real de cierre (POST a registro.php con nota real) cuando la credencial MySQL legacy autentique (v3.30) o con sesión de gestión activa; el arnés de emulación queda como respaldo.
- El MySQL del `.148` sigue rechazando `jonaiber/Crist2026` desde host `Bqto1` (falta GRANT para `'%'` en el servidor: `CREATE USER IF NOT EXISTS 'jonaiber'@'%' IDENTIFIED BY 'Crist2026'; GRANT SELECT, UPDATE, INSERT, DELETE ON *.* TO 'jonaiber'@'%'; FLUSH PRIVILEGES;`).

### v4.3 — Sincronización física 1:1 entre Tools/ y skills/ (simetría departamental) (2026-08-07)

**Objetivo (directiva):** garantizar simetría 1:1 entre los departamentos de `app/Services/NvidiaBrain/Tools/` y las subcarpetas de `skills/`, creando `compras/` y `despacho/` y dejando la raíz de `Tools/` limpia.

**Verificación de estado (la estructura solicitada ya estaba consolidada por trabajo previo v4.1/v4.2):**
- `skills/` contiene EXACTAMENTE las 5 subcarpetas departamentales (+ `base.py`/`__init__.py` de infraestructura del paquete):
  - `stock_bulto_cerrado/` (carpeta del departamento Almacén: `surtido_prioritario.py`, `reporte_quiebres_compras.py`)
  - `auditoria/` (`detector_malsurtido.py`, `discrepancia_traslados.py`)
  - `compras/` (`quiebres_compras.py`) — carpeta y script presentes
  - `despacho/` (`cierre_despacho.py`) — carpeta y script presentes
  - `recepcion/` (`factura_vision_ocr.py`)
- Raíz de `app/Services/NvidiaBrain/Tools/` → SOLO las 5 subcarpetas (Almacen, Auditoria, Compras, Despacho, Recepcion). Cero archivos sueltos: `BuscarInventarioTool`, `ConsultarNotaTool`, `ProfitStockHelper` ya viven en `Tools/Almacen/`; `LeerVoucherOcrTool`, `ConciliarFacturaTool` en `Tools/Recepcion/`; `ConsultarClienteTool` en `Tools/Despacho/`; `ConsultarSaldoClienteTool`, `CrearReporteTool` en `Tools/Auditoria/`.

**Mapeo definitivo de conectores (verificado por grep en los 5 wrappers):**
- `Tools/Almacen/PythonStockBultoCerradoSkillTool.php` → `runSkill('stock_bulto_cerrado/' . $script)` → `skills/stock_bulto_cerrado/`
- `Tools/Auditoria/PythonAuditoriaSkillTool.php` → `runSkill('auditoria/' . $script)` → `skills/auditoria/`
- `Tools/Compras/PythonComprasSkillTool.php` → `runSkill('compras/quiebres_compras', ..., 'analizar_quiebres_compras')` → `skills/compras/quiebres_compras.py`
- `Tools/Despacho/PythonDespachoSkillTool.php` → `runSkill('despacho/cierre_despacho', ..., $accion)` → `skills/despacho/cierre_despacho.py`
- `Tools/Recepcion/PythonRecepcionSkillTool.php` → `runSkill('recepcion/factura_vision_ocr', ..., 'procesar_foto_factura')` → `skills/recepcion/`

**Criterios de aceptación (ejecutados 2026-08-07):**
- Simetría de carpetas: 5 ↔ 5 (Almacén=stock_bulto_cerrado, Auditoria, Compras, Despacho, Recepcion). ✓
- Raíz de `Tools/` limpia (solo 5 subcarpetas). ✓
- `php -l` recursivo sobre `Tools/**/*.php` → **0 errores**. ✓
- `py -3.14 -m py_compile` recursivo sobre `skills/**/*.py` (excl. `__pycache__`) → **0 errores**. ✓
- Acciones expuestas por cada script de skills coinciden con las `ACCIONES_DEFECTO`/acciones de los wrappers (obtener_cola_surtido, generar_reporte_compras_laboratorio, analizar_log_surtido, auditar_traslado_intersedes, analizar_quiebres_compras, validar_estructura_bultos/validar_bultos/precierre_transaccional, procesar_foto_factura). ✓

### v4.2 — Prueba de campo Console.Log: adaptadores PHP de las 5 Skills Python (2026-08-07)

**Objetivo (directiva):** arnés CLI `bin/test_skills_console_runner.php` que invoca secuencialmente los 5 superconectores de Skills Python (Almacén, Auditoría, Compras, Despacho, Recepción) con logs estructurados en STDOUT (`[START]`, `[INPUT]`, `[LATENCY]`, `[RESPONSE]` JSON_PRETTY_PRINT, `[STATUS]` PASS/DEGRADED/FAIL) y aislamiento por try/catch (ningún fallo interrumpe la secuencia).

**Estructura del arnés:**
- Pre-Flight `pdo_odbc`+`mbstring` (exit 500 si faltan); carga manual del ToolRegistry (NvidiaBrainException/Client, AgentToolInterface, ToolRegistry, AlmacenDbTrait).
- `ToolRegistry::loadFromDirectory(app/Services/NvidiaBrain/Tools)` → 25 tools departamentales registradas (patrón de producción).
- `desnormalizar_resultado()`: el ToolRegistry envuelve la salida en formato de tool_call del motor `{role:'tool', tool_call_id, content:'<JSON string>'}`; el arnés decodifica el content y clasifica: `success=true` (sin `data.status='error'`) → PASS; `success=false` o `data.status='error'` → DEGRADED con diagnóstico (`error`, `data.aviso`, `data.requisitos`); Throwable → FAIL aislado.
- Contexto de sesión simulado del agente (`{usuario:'ARNES_V42', rol:'sistema', modulo:'prueba_consola', sesion:'suite-skills-v4.2'}`) inyectado a `executeTool()` — verifica el punto 4 de la directiva (integración limpia con la firma que usa el motor Ollama/NIM en producción).
- Exit 0 si 5/5 skills completan su ciclo (PASS+DEGRADED); 1 si hay FAIL o skill no registrada.

**Suite y resultados (corrida 2026-08-07, PHP CLI 8.3.33, py -3.14):**
1. `python_stock_bulto_cerrado_skill` (Almacén, `obtener_cola_surtido` BQTO/30d) → **DEGRADED honesto**: la skill conectó al MySQL legacy, resolvió tabla `productos`, pero el mapeo estricto DESPACHO/DEPOSITO (SC) no está en el esquema actual (hallazgo real de auditoría, ~1s).
2. `python_auditoria_skill` (Auditoría, `analizar_log_surtido`) → **PASS determinista**: log de prueba `ara/ARA_Brain/data/log_reporte_arnes_v42.log` (creado para el arnés, 4 líneas, 2 discrepancias) → `lineas_procesadas:5, discrepantes:2, interrumpir_cierre:true` (~0.6s).
3. `python_compras_skill` (Compras, `analizar_quiebres_compras` 30d) → **DEGRADED honesto**: esquema MySQL sin `cod_art`/`stock_act` reconocibles (~0.7s).
4. `python_despacho_skill` (Despacho, `validar_estructura_bultos`) → **PASS determinista**: gate 1:1 con 3 bultos dict `{codigo, cod_art}` ↔ 3 ítems → `gate_1_1: true` (~0.5s). Lección del arnés: bultos como strings dan gate fallido (el contenido del bulto = código del bulto, no del ítem); los dicts ejercitan el caso de éxito.
5. `python_recepcion_skill` (Recepción, `procesar_foto_factura` con foto real de WhatsApp) → **PASS/DEGRADED según disponibilidad de Ollama/LLaVA** (no determinista): corrida 1 PASS con OCR completo + PDF `PDFs_Recepcion/...NOTACREDITO_17983256_20220510.pdf` (77s); corrida 2 DEGRADED "Fallo total de visión local y OCR" (46s) — diagnóstico honesto del motor.

**Resumen estable:** PASS 2 (Auditoría, Despacho) + DEGRADED 3 (Almacén, Compras, Recepción según visión) + FAIL 0 + NO_REGISTRADA 0 → **5/5 ciclos completados, exit 0**. El aislamiento por try/catch nunca interrumpió la secuencia.

### v4.0 — Reparación de conexión robusta SQL Server (pyodbc) y timeout HTTP a registro.php (2026-08-07)

**Objetivo (directiva):** (1) reconexión automática con reintento y fallback en `ProfitRenglonesSync` para el error pyodbc `08S01` (Communication link failure) y similares; (2) subir el timeout de lectura HTTP a `registro.php` de 2.5s a una tupla `(3.0, 8.0)` (3s conectar / 8s leer) con 1 reintento silencioso.

**Cambios en `preparacion/infrastructure/adapters/profit_renglones_sync.py` (v4.0):**
- Nueva constante `_SQLSTATES_CONEXION` (`08S01`, `08001`, `08003`, `08007`, `08S00`, `08S02`, `HYT00`, `HYT01`, `10053`, `10054`) + `_REINTENTOS_CONEXION = 2`.
- `_es_error_conexion()`: detecta SQLSTATE de conexión rota + mensajes clásicos ("communication link failure", "connection is closed/busy"). Errores lógicos (42000/23000) NO reconectan: se relanzan tal cual.
- `_cerrar_conexion()`: fuerza `self._conn = None` + close (invalida el caché de conexión; `_conectar()` abre fresca).
- `_con_ejecutar(fn, max_reintentos)`: envuelve `cursor.execute()` con reintento en caliente — cierra conexión rota, reabre y re-ejecuta (hasta 2 reintentos); log `⚠️ reconectando en caliente… (intento n/2)`.
- `_ejecutar_update(sql, params, commit)` (idempotente: el marcaje setea siempre los mismos valores) y `_commit_reintento()`.
- Refactor de `_columnas_tabla`, `estado_escaneo_nota` y `marcar_renglones_cargados`: TODAS las consultas (SELECT de renglones, UPDATEs por ítem, UPDATE masivo + COMMIT) pasan por los helpers de reconexión. `cerrar()` reutiliza `_cerrar_conexion()`.
- El contrato de retorno no cambia: `{"status":"ok"/"error", ..., "aviso_legacy"}`; NUNCA lanza excepción; el flujo local (SQLite) y el cierre Legacy nunca se interrumpen.

**Cambios en `preparacion/infrastructure/adapters/legacy_http_adapter.py` (v4.0):**
- Nueva constante `TIMEOUT_REGISTRO = (3.0, 8.0)` (tupla conectar/leer).
- `_post_path()`: el POST a `PATH_REGISTRO`/`PATH_CHEQUEO_REGISTRO` usa `TIMEOUT_REGISTRO`; `actualizar_nota.php` conserva `DEFAULT_TIMEOUT = 30` (techo histórico ~25s de gestión completa en assign_preparer — documento en el código por qué no se acota a 8s).
- `_timeout_autochequeo()`: reemplaza el `float(os.getenv("AUTOCHEQUEO_TIMEOUT_S", "2.5"))` plano — default `(3.0, 8.0)`; env acepta `"X"` → `(3.0, X)` o `"(a, b)"` → tupla completa.
- `_post_autochequeo()`: POST con 1 reintento silencioso ante `requests.exceptions.ReadTimeout` (misma política de `_post_path`); otros errores se relanzan. Aplicado al paso 3 (POST final de chequeo/registro.php) del flujo de autochequeo.

**Criterios de aceptación verificados (arné en %TEMP% `test_v4_reconexion.py`, 6/6 PASS, exit 0, py -3.14):**
1. `_es_error_conexion` → True para 08S01/08001/08003/HYT00/HYT01 + mensajes de enlace; False para 42000/23000. ✓
2. Conexión que muere con 08S01 en el 1er intento y revive → `_con_ejecutar` reabre (2ª instancia de conexión) y completa la consulta SIN `OperationalError`, log de reconexión visible. ✓
3. UPDATE con 08001 en 1er intento → `_ejecutar_update` reabre, re-ejecuta y hace commit (rowcount correcto). ✓
4. `_timeout_autochequeo` → `(3.0, 8.0)` default; `"10"` → `(3.0, 10.0)`; `"(5.0, 12.5)"` → override; basura → fallback. ✓
5. `_post_autochequeo` → 1er intento ReadTimeout + 2º éxito = 2 llamadas, respuesta 200. ✓
6. `_post_path` → `registro.php` usa `(3.0, 8.0)`; `actualizar_nota.php` usa 30. ✓
- `py_compile` OK en ambos adaptadores; `import preparacion` + ara_server OK con py -3.14 (mismo intérprete de despliegue).
- Nota: el pre-request GET real al `.148:8000` respondió HTTP 200 durante el arnés (servidor vivo, solo lectura).

### v3.32 — Suite de Skills de Gestión de Notas en Tiempo Real (6 Tools PHP NvidiaBrain) (2026-08-07)

**Objetivo (directiva):** desarrollar e integrar 6 Tools de auditoría/seguimiento sobre el flujo de `gestion.php` en `app/Services/NvidiaBrain/Tools/Almacen/`, todos con AgentToolInterface + `getDefinition()`, registro automático vía `ToolRegistry::loadFromDirectory()` y respuestas < 100 ms sobre las BDs de producción.

**Esquema real verificado (probes read-only):**
- **MySQL legacy .148 (BD `barquisimeto`/`monitor_notas`/`sistema_operaciones`, espejos):** `rep_not` (id, fec_profit, fec_impr, fec_creacion, verificacion, ruta, cod_nota, estatus), `gestion` (cd_barr, co_cli, cli_des, verifi_pre, num_prep, tipo_perso, ubicacion, hora, verifi_cheq, numeroMesa, num_cheq, tip_pre, ubicacion2, hora2, num_emb, verifi_emb, tip_emb, ubicacion3, hora3, cant_items, num_mesa_emba — la tabla `gestion` ES la trazabilidad física del flujo), `asignaciones_preparacion` (id, cod_nota, numero_trabajador, tamano_nota, asignado_en, completado_en), `devoluciones` (id, num_devo, num_trab, cant_puntos, fecha), `cajas_embalaje`. `sistema_operaciones` agrega columna `sede` en todas.
- **CRISTM25 (SQL Server @ .20, ODBC "SQL Server"):** `reng_nde`/`reng_ndd` (num_doc, reng_num, co_art, co_alma, total_art, stotal_art, pendiente, anulado, cant_imp, cant_prod...), `reng_dev` (dev_num, co_art, cant_dev, cant_xdev), `reng_ndr`, `cajas`. NO existen rep_not/gestion/devoluciones en CRISTM25 (esas viven en el MySQL legacy).
- num_doc reales actuales: `72000051` (20 renglones), `72000049`, `72000038`... (la nota `72161167` del v3.19 ya no está en el esquema actual).

**Archivos creados (7 nuevos + 1 fix):**
```
app/Services/NvidiaBrain/Tools/Almacen/
├── AlmacenDbTrait.php                       Trait compartido (helper suelto; el registry lo salta por no ser clase AgentTool)
├── TrazabilidadVidaUtilNotaTool.php         trazabilidad_vida_util_nota
├── FlujoNotasTiempoRealTool.php             flujo_notas_tiempo_real
├── DetectorErroresNotaTool.php              detector_errores_nota
├── MonitorModificacionesEliminacionesTool.php monitor_modificaciones_eliminaciones
├── RendimientoPreparadoresSmartAssignTool.php rendimiento_preparadores_smart_assign
└── GestionSuperEsteroideSearchTool.php      gestion_super_esteroide_search
```

**`AlmacenDbTrait`:** conexión dual `conectarMySQL()` (pdo_mysql → MySQL ODBC 8.0/5.3; degrada con diagnóstico si el driver no existe en la instalación) + `conectarProfit()` (pdo_sqlsrv → ODBC "SQL Server"); resolución dinámica vía INFORMATION_SCHEMA: `listarTablas`/`listarColumnas`/`resolverTabla`/`resolverColumna`/`resolverColumnas`/`resolverBDMySQL` (USE de la BD funcional para `jonaiber`, que no tiene BD por defecto); `q()` (backticks) y **`qPara($pdo, ...)` (backticks MySQL / CORCHETES SQL Server — SQL Server no acepta backticks: fix crítico)**; `aUtf8`, `env`, `primerValor`, `fNum`, `micro` (métricas en microsegundos, estándar NvidiaBrain).

**Cada tool (reglas de negocio de la directiva):**
1. `trazabilidad_vida_util_nota(num_nota)`: timeline 360° T0 creación → T1 asignación mesa → T2 preparación → T3 chequeo → T4 embalaje → T5 cierre/impresión+ruta → T6 entrega/devolución con nombres de usuario reales y Δ entre hitos (`calcularDeltas`); detalle por ítem (solicitada/despachada/pendiente/devuelta desde reng_nde + reng_dev); estado final EN_PREPARACION/EN_CHEQUEO/EN_EMBALAJE/EN_TRANSITO/DESPACHADA_ENTREGADA/DEVUELTA.
2. `flujo_notas_tiempo_real(sede, limite)`: notas activas (estatus PENDIENTE/EN_PREPARACION/EN_CHEQUEO/EN_PROCESO/EN_EMBALAJE/ACTIVA/CARGADA) agrupadas en buckets B1 (1 ítem, Surtido Ultra-Rápido) / B2 (2-5, Media) / B3 (6-10, Estándar) / B4 (11-20, Alta) / B5 (21+, Super) con notas, renglones y unidades por bucket + totales; separación por sede si la cabecera expone columna `sede` (aviso honesto si no).
3. `detector_errores_nota(num_nota, limite)`: renglón duplicado (mismo SKU en 2+ renglones no anulados), CANTIDAD_CERO, CANTIDAD_NEGATIVA y CODIGO_INCOMPATIBLE (SKU sin equivalente en articulos/productos del legacy). NUNCA autocorrige: emite "ALERTA PREVENTIVA: Nota #X contiene un renglón duplicado para el SKU Y. Notificar a Administración antes de surtir." Lógica pura `detectarErroresRenglones()` testeable.
4. `monitor_modificaciones_eliminaciones(num_nota, desde)`: capa 1 = tabla de traza real (candidatas auditoria_notas/log_modificaciones/historial_notas/traza_notas/log_cambios/auditoria/log_auditoria/registro_cambios); capa 2 (sin traza, aviso explícito de limitación) = indicios indirectos: cabecera con estatus ≠ PENDIENTE (MODIFICADA, antes/después) y renglones anulados en reng_nde (ELIMINADA parcial). Normaliza a MODIFICADA/ELIMINADA/CREADA.
5. `rendimiento_preparadores_smart_assign(num_nota, fecha, limite)`: velocidad real (renglones/minuto) desde asignaciones_preparacion (asignado_en/completado_en + tamano_nota), marcas personales por volumen (1-5/6-20/21+), carga activa (asignaciones sin completar); `recomendarPreparador` = mayor rpm del rango de la nota → desempate rpm global → menor carga, con justificación. Lógica pura `calcularRanking`/`cargaActiva`/`recomendarPreparador` testeable.
6. `gestion_super_esteroide_search(num_nota)`: ficha 360° — QUIÉN la tiene (asignación sin completar) o quién la trabajó (preparador/chequeador/embalador), últimos 3 movimientos, DÓNDE está (derivarUbicacion pura: SIN_ASIGNACION → MESA_DE_PICKING → MODULO_DE_CHEQUEO → ZONA_DE_EMBALAJE → ZONA_DE_BULTO_CERRADO → CAMION_DE_RUTA/ENTREGADA → DEVUELTA) y status de validación (`derivarValidacion` pura: VALIDADA_100 / PARCIAL_EN_REVISION / SIN_VALIDAR + flag `bypass_revision` si estatus de cierre sin validación completa). Si el MySQL no responde, consulta Profit igual (ficha parcial honesta, nunca "no encontrada" falsa).

**Fix en `ToolRegistry::loadFromDirectory()` (bug real):** el iterador se construía con la ruta cruda mientras `$dirReal` usaba `realpath` → con rutas tipo `bin/../app/...` el `substr` del cálculo relativo se descuadraba y NO registraba NINGÚN tool. Ahora `realpath` se aplica ANTES de construir el iterador. Verificado: los 13 tools departamentales se registran desde el arnés de `bin/`.

**Verificación (2026-08-07):**
- `php -l` OK en los 8 archivos nuevos + ToolRegistry.
- `php bin/test_notas_tiempo_real_tools.php` → **15/15 PASS, exit 0, total_time_ms 340**: (1) auto-descubrimiento 6/6 vía loadFromDirectory; (2) 13 definiciones OpenAI v1 válidas; (3-9) lógica pura: detector (duplicado/cero/negativo/anulado ignorado/descatalogado), ranking rpm + marcas por volumen, carga activa, recomendación con desempate, ubicación 7/7, validación con bypass; (10-12) diagnóstico real contra CRISTM25 con nota `72000051`: trazabilidad con 20 ítems reales, **detector halló 1 nota en conflicto con 9 alertas reales** (duplicados/ceros de origen), ficha con validación SIN_VALIDAR real; (13-15) MySQL legacy degrada honesto (sin driver local + Access denied root en .env local).
- Métricas reales locales: 67-70 ms por consulta Profit (149 ms incluye el intento MySQL fallido); en producción con pdo_mysql cada tool queda < 100 ms (criterio de la directiva).

**Pendientes:**
- El `.env` local quedó con `MYSQL_USER=root`/`MYSQL_PASSWORD=10445610` (v3.30) que el .148 rechaza desde host `Bqto1`; las credenciales válidas documentadas son `jonaiber`/`Crist2026.` (v3.28) — aplicar GRANT `'jonaiber'@'%'` en el .148 para habilitar la capa MySQL de los 6 tools.
- Los tools MySQL requieren pdo_mysql o MySQL ODBC en la instalación donde corra el agente (el PHP local 8.3.33 solo trae ODBC SQL Server + pdo_sqlite).

### v3.28 — ARA_SYNC: credenciales MySQL del .env activadas + resolución dinámica de BD en LegacyMySQLConnector (2026-08-07)

**Objetivo (directiva):** fijar en `.env` los parámetros de conexión MySQL del legacy (`192.168.4.148:3306`, usuario `jonaiber`, password `Crist2026.`, límite de pool 30) y asegurar que el pool pymysql los use estrictamente para el acceso directo, conservando el respaldo HTTP si la red del .148 limita la conexión directa.

**Diagnóstico (probe INFORMATION_SCHEMA real, MySQL 8.0.17):**

- El usuario `jonaiber` NO tiene base por defecto (`DATABASE()` = NULL) y el servidor tiene 18 BDs: `barquisimeto`, `chequeo`, `visor`, `sistema_operaciones`, `monitor_notas`, `gestionpedidos`, `finanzas`, `nomina`...
- `rep_not` (tabla de notas del legacy) vive en `barquisimeto` / `monitor_notas` / `sistema_operaciones` (espejos del mismo conjunto: asignaciones_preparacion, cajas_embalaje, gestion, puntajes, usuarios...). La BD `barquisimeto` es la que aplica al flujo BQTO.
- `rep_not` real: columnas `id, fec_profit, fec_impr, fec_creacion, verificacion, ruta, cod_nota, estatus` — el conector no resolvía `cod_nota` como columna de nota.

**Cambios:**

1. **`.env`:** `MYSQL_PASSWORD` corregido a `Crist2026.` (la directiva trae el punto final; antes `Crist2026`). El resto de `MYSQL_*` ya estaba correcto.
2. **`preparacion/infrastructure/adapters/legacy_mysql_sync.py`:**
   - Carga defensiva del `.env` (`_cargar_env_defensivo`, claves `MYSQL_*` con `setdefault` — no pisa el entorno real) antes de leer credenciales: el conector ya no depende de que `ara_vision` (único cargador dotenv del proyecto) se importe primero.
   - **Resolución dinámica de BD** (`_resolver_bd`): busca en `information_schema.tables` las BDs no-sistema que contengan `rep_not`/`notas_entrega`/`notas`, toma la primera por orden alfabético y ejecuta `USE`; cacheada en `self._database`. Env `MYSQL_DATABASE` (opcional) permite fijarla. `_tablas`/`_columnas` filtran por esa BD en vez de `DATABASE()`; `_conectar` pasa `database=` cuando ya está resuelta; `verificar()` reporta la BD resuelta.
   - `_CAND_COL_NOTA` += `cod_nota` (columna real de `rep_not`).
   - **Bug de SQL corregido:** `_confirmar` generaba `SET SET \`estatus\` = %s` (duplicación); ahora `UPDATE \`rep_not\` SET \`estatus\` = %s WHERE \`cod_nota\` = %s`.
3. **`ara/ARA_Brain/ara_server.py`:** `LegacyMySQLConnector()` instanciado como `_prep_mysql_sync` e inyectado en `LegacyPHPAdapter(mysql_connector=...)` en el MODO LEGACY. Con esto el acceso directo por BD pasa a ser la vía primaria del cierre; el respaldo HTTP ya existía (`_confirmar_mysql` → None → flujo PHPSESSID+POST, nunca lanza excepción). `pymysql` instalado en el intérprete `py -3.14` (no estaba en el entorno).

**Verificación (2026-08-07, MySQL .148 en vivo):**

- `py -3.14 -m py_compile` OK en legacy_mysql_sync.py, ara_server.py y arnés ✓.
- `py -3.14 bin/test_legacy_mysql_sync.py` → **exit 0**: `[1/4] CONEXION: OK (8.0.17 @ barquisimeto en 132.3ms)`; `[2/4] POOL: OK (limite 30, 32 conexiones creadas y liberadas, sobrantes cerradas)`; `[3/4]` despacho y chequeo en `dry_run` (sin escritura) con SQL correcto sobre `rep_not.cod_nota`/`estatus` ✓.
- La confirmación REAL (`--confirmar`) no se ejecutó en esta pasada: escribe en la BD real del legacy (UPDATE estatus + bitácora) y quedó documentado para cuando el usuario la autorice.

### v3.29 — Ecosistema de Skills de IA Local del Almacén (skills/) (2026-08-07)

**Objetivo (directiva maestra):** implementar el ecosistema de skills para la estación de IA local del almacén: surtido prioritario de bulto cerrado, quiebres para compras, recepción con visión OCR, detección de mal surtido en logs y discrepancia en traslados inter-sedes — todo con motor BD multi (SQL Server CRISTM25 / MySQL .148), tipado (`typing`) y best-effort absoluto.

**Estructura creada:**
```
skills/
├── __init__.py               (re-export de conectar/cerrar)
├── base.py                   Núcleo compartido (NUEVO, adición justificada)
├── stock_bulto_cerrado/
│   ├── __init__.py
│   ├── surtido_prioritario.py
│   └── reporte_quiebres_compras.py
├── recepcion/
│   ├── __init__.py
│   └── factura_vision_ocr.py
└── auditoria/
    ├── __init__.py
    ├── detector_malsurtido.py
    └── discrepancia_traslados.py
```

**`skills/base.py` (núcleo compartido):**
- Conexión multi-motor `conectar(engine='auto')`: SQL Server (pyodbc, env `PROFIT_DB_*` → CRISTM25, driver detectado ODBC 18/17/SQL Server, TrustServerCertificate) con fallback a MySQL (pymysql, env `MYSQL_*` → .148). Placeholders por motor (`?`/`%s`), `condicion_desde_fecha` (DATEADD/GETDATE vs DATE_SUB/CURDATE).
- Resolución dinámica de esquema vía INFORMATION_SCHEMA: `listar_tablas`/`listar_columnas`/`resolver_tabla`/`resolver_columna` (case-insensitive). **MySQL: `resolver_tabla` primero resuelve la BD funcional (`resolver_bd_mysql`, USE) — el usuario `jonaiber` no tiene BD por defecto y el listado sin USE cruzaba todas las BDs del servidor.**
- Catálogos de candidatas por rol: tablas (articulos/inventario/art/productos; reng_fac/reng_nde/reng_com; traslado/traslados/manifiesto) y columnas (cod_art, descripcion, stock_act, despacho/deposito SC y BQTO, laboratorio, ubicacion/campo7, fecha, cantidad, nro_doc). Carga defensiva del `.env` (setdefault).

**Módulo 1 — `surtido_prioritario.obtener_cola_surtido(sede, dias_rotacion=30)`:** filtro por sede (SC: `DESPACHO=0` Y `DEPOSITO>0`; BQTO: `DESPACHO_BQTO=0` Y `DEPOSTO_BQTO>0`), rotación = SUM unidades de `reng_fac`+`reng_nde` en la ventana (columnas resueltas dinámicamente), orden DESC por rotación (Prioridad 1 = el más vendido con estante en 0), clasificación ALTA/MEDIA/BAJA por terciles (`clasificar_prioridad`, regla documentada), salida `{cod_art, descripcion, ubicacion_deposito, unidades_a_bajar, rotacion_30d, nivel_prioridad}`.

**Módulo 2 — `reporte_quiebres_compras.generar_reporte_compras_laboratorio(formato='excel')`:** quiebre total (`DEPOSITO=0` AND `DEPOSTO_BQTO=0` AND `STOCK_ACT=0`), Tier 1 = Top 20% de volumen histórico (`clasificar_tier`, umbral = 20% del volumen total de la cola), Tier 2 = resto; agrupado por laboratorio; Excel con pestaña Resumen (encabezado con SKUs agotados por laboratorio, Tier 1 y sugerencia estimada de compra) + pestaña por laboratorio (openpyxl); PDF (reportlab); degradación a CSV con aviso si la librería falta. Sugerencia por SKU = unidades 30d (mínimo 1).

**Módulo 3 — `recepcion.factura_vision_ocr.procesar_foto_factura(ruta_imagen, laboratorio_override=None)`:** visión multimodal LOCAL (Ollama `/api/generate` con `llava`/`ARA_VISION_MODEL`, prompt de extracción JSON estricto) con fallback Tesseract OCR; extrae `{tipo_documento, proveedor, rif, nro_factura, fecha_emision, renglones[]}` (normalización/validación, fechas ISO); PDF membretado (reportlab) con nomenclatura exacta `PDFs_Recepcion/[LABORATORIO]_[TIPO_DOC]_[NRO_FACTURA]_[FECHA].pdf`.

**Módulo 4 — `auditoria.detector_malsurtido`:** parsing de `LOG_REPORTE` en 2 formatos (clave=valor `OPERARIO=...|PEDIDO=...|ESCANEADO=...|ESTACION=...` y CSV `ts,operario,solicitado,escaneado,estacion`); discrepancia `solicitado != escaneado` → matriz de errores `{operario_id, timestamp, sku_pedido, sku_escaneado, estacion}`; `analizar_log_surtido` (archivo o directorio) con `interrumpir_cierre=True` si hay fallos; `emitir_alerta` (beep winsound + payload visual para interfaz); `bloquear_cierre_nota` (permite/frena cierre); `vigilar_log_surtido` (parsing continuo incremental por offset con callback).

**Módulo 5 — `auditoria.discrepancia_traslados.auditar_traslado_intersedes(cod_traslado, modo_real=False)`:** concilia el manifiesto de origen (Almacén 02 S/C) contra el conteo físico de destino (Almacén 05 BQTO, `DEPOSTO_BQTO` o cantidad recibida registrada); faltantes/averías → **Acta de Discrepancia en Tránsito** (PDF + JSON en `actas_discrepancia/`) y UPDATE de estado `EN_RECLAMO` sobre los renglones afectados (dry-run por defecto; `modo_real=True` para escribir — misma convención que LegacyMySQLConnector).

**Verificación (2026-08-07):**
- `py -3.14 -m py_compile` OK en los 11 archivos ✓.
- `py -3.14 bin/test_skills_almacen.py` → **10/10 PASS, exit 0, total_time_ms 559**: clasificación de terciles, Tier Top 20%, parsing clave=valor y CSV con eventos correctos + interrupción, alerta + bloqueo, nomenclatura PDF exacta, normalización de extracción, y 3 casos de diagnóstico contra BD real (cola, quiebres, traslado) que degradan HONESTO con requisitos (el mapeo estricto DESPACHO/DEPOSITO/DESPACHO_BQTO/DEPOSTO_BQTO/STOCK_ACT no existe aún en las tablas visibles de MySQL/CRISTM25; los skills quedan listos para consumirlo cuando el esquema lo exponga — tabla `productos` resuelta en la BD `comparador` del .148).
- Consola cp1252: `sys.stdout.reconfigure(errors="replace")` en el arnés (patrón del proyecto).
- **Pendiente de datos:** pyodbc no está instalado en `py -3.14` → los skills usaron MySQL; para probar contra CRISTM25 real instalar pyodbc (`py -3.14 -m pip install pyodbc`).

---

### v4.0 — Directiva Tools: 6 stubs + esquema real MySQL .148 + Profit CRISTM25 como fuente primaria de stock (2026-08-07)

**Objetivo (directiva):** ampliar la estación de IA local del almacén con 6 herramientas departamentales (router de bulto cerrado, quiebres para compras, recepción con visión OCR, detección de mal surtido en logs, discrepancia en traslados inter-sedes y cierre transaccional), consumiendo el stock REAL de Profit (SQL Server CRISTM25, BD Profit) y el legacy MySQL (.148) — no tablas simuladas.

**1. Esquema real descubierto (probes contra ambos motores):**
- **MySQL .148 @ `barquisimeto`** (usuario `jonaiber` / password `Crist2026.` — **el punto final es OBLIGATORIO**, ya estaba en `.env`): 10 tablas operativas (`asignaciones_preparacion`, `cajas_embalaje` 126.378 filas, `devoluciones`, `disponibilidad_trabajadores`, `gestion` 155.905, `puntajes`, `recepciones`, `rep_not` 162.719 — estatus IMPRESA 1.293 / EMBALADA 150.222 / PREPARACION 10.473 / CHEQUEO 731 —, `traslados`, `usuarios`). **NO existe tabla `articulos`** → el fallback `articulos` de los tools era un espejismo.
- **Profit CRISTM25** (SQL Server `.20`, `profit`/`profit`, puerto 1433): `st_almac` (co_alma char(6) + co_art char(30) + stock_act decimal) = **stock POR SUB-ALMACÉN** (50.512 filas, sub-almacenes 01,02,03,04,05,06,07,08,9999); `art` (co_art char(30), art_des varchar(120), stock_act consolidado, co_prov, anulado bit); `reng_fac`/`reng_nde`/`tras_alm`/`spdevalm`/`spreqalm` con co_alma/alm_orig/alm_dest. **Mapeo real:** 01=DESPACHO SC, 02=DEPOSITO SC, 04=DESPACHO BQTO, 05=DEPOSITO BQTO. Quiebres reales `art.stock_act<=0 AND anulado='0'`: 7.549 SKUs.

**2. Cambios:**
- **`app/Services/NvidiaBrain/Tools/ProfitStockHelper.php`** (nuevo): conector SQL Server (sqlsrv/ODBC SQL Server), `columnas()`, `stockPorSubAlmacen()`, `maestros()`, `rotacion()`, `qSrv()`, `aUtf8()`, constantes SUB_DESPACHO_SC='01' / SUB_DEPOSITO_SC='02' / SUB_DESPACHO_BQTO='04' / SUB_DEPOSITO_BQTO='05'.
- **6 stubs departamentales creados**: `BultoCerradoRouterTool` (router real por ítem con `requiere_bulto_cerrado`), `ReporteQuiebresComprasTool` (quiebres reales por `co_prov`, Tier 1/2 con rotación), `RecepcionFacturaVisionTool`, `DetectorMalsurtidoLogTool`, `DiscrepanciaTrasladosTool` (join st_almac 02 vs 05), `CierreTransaccionalTool`. `bin/test_nvidia_brain.php` integrado con `$registry->loadFromDirectory(...)`; `php -l` limpio; smoke test 7/7 tools registradas.
- **4 tools reescritos con Profit como fuente PRIMARIA + fallback MySQL `articulos`**: `StockSurtidoPrioritarioTool` (estante 0 + depósito > 0 por sede con HAVING sum case + rotación por sucursal), `BultoCerradoRouterTool`, `ReporteQuiebresComprasTool`, `DiscrepanciaTrasladosTool`.
- **2 fixes obligados por pdo_odbc**: (a) `CAST(? AS varchar(10))` en co_alma por error 402 char/text; (b) umbral inyectado como literal numérico `sprintf('%.2f', ...)` por error 529 (text→decimal no permitido).
- **`pdo_mysql` habilitado** en `C:\tools\php\php.ini` (línea 954); `php -m` confirma mysqlnd + PDO + pdo_mysql + PDO_ODBC + pdo_sqlite. `.env.example` documenta `jonaiber`/`Crist2026.` con advertencia del punto obligatorio. Harness `bin/test_legacy_mysql_sync.py` → exit 0 (MySQL 8.0.17 @ barquisimeto, pool 30/32, dry-run despacho/chequeo OK).

**3. Verificación real (2026-08-07, solo lectura, contrato `executeTool` del registry):**
- `stock_surtido_prioritario` sc límite 5 → success=true, fuente `profit_st_almac`; top: MD01001 DICLOFENAC POTASICO (estante 0 / depósito 321 / total 330 / rotación 600), AMP00496 (0/700/863/593), MISC1560 (0/36/72/69), MD05046 (0/36/72/0).
- `bulto_cerrado_router` bqto (ALIM0001/ALIM0004/00000001) → success=true con nombres reales; ítems sin stock en estante 04 ni depósito 05 (todo faltante_total, requiere_bulto_cerrado=true).
- `reporte_quiebres_compras` límite 5 → success=true, fuente `profit_art`; `discrepancia_traslados` → success=true tras fix del umbral literal.
- Registry: 13 tools departamentales (6 nuevos + 6 Adicionales ya existentes bajo Tools/Almacen/ + 1 preexistente).

**Pendientes:**
- El `.env` local quedó con `MYSQL_USER=root`/`MYSQL_PASSWORD=10445610` (v3.30) que el .148 rechaza desde host `Bqto1`; las credenciales válidas documentadas son `jonaiber`/`Crist2026.` (v3.28) — aplicar GRANT `'jonaiber'@'%'` en el .148 para habilitar la capa MySQL de los 6 tools.
- pyodbc no está instalado en `py -3.14` → los skills de Python usaron MySQL; instalar `py -3.14 -m pip install pyodbc` para probar contra CRISTM25 real.
- Chequeo manual de S/C dentro de CRISTM25 (pendiente de la directiva v4.0 original).

---

### v4.1 — Centralización departamental de Tools + Superconector PHP ↔ Skills Python (2026-08-07)

**Objetivo (directiva):** eliminar la dispersión de código: (1) la raíz de `app/Services/NvidiaBrain/Tools/` queda con SOLO subcarpetas departamentales (`Almacen/`, `Auditoria/`, `Compras/`, `Despacho/`, `Recepcion/`); (2) todo `.php` suelto se mueve a su departamento con namespace actualizado; (3) las Skills de Python (`C:\ARA_PROYECT\skills\`) se vuelven ejecutables desde el motor PHP vía un superconector (`PythonSkillExecutor`) con wrappers departamentales; (4) `pyodbc` instalado en `py -3.14`.

**FASE 1 — Migración (8 archivos movidos, namespace PSR-4 por carpeta):**
- `Almacen/`: `BuscarInventarioTool`, `ConsultarNotaTool`, `ProfitStockHelper`.
- `Recepcion/`: `LeerVoucherOcrTool`, `ConciliarFacturaTool`.
- `Despacho/`: `ConsultarClienteTool`.
- `Auditoria/`: `ConsultarSaldoClienteTool`, `CrearReporteTool`.
- Referencias actualizadas en cadena: `Adapters/FinanzasAdapter` y `Adapters/WhatsappClienteAdapter` (use), `bin/test_whatsapp_webhook.php`, `bin/test_adapters_suite.php`, `public/api/whatsapp/webhook.php` (require), y los 4 tools que usaban `ProfitStockHelper` (Compras/Auditoria → `../Almacen/ProfitStockHelper.php`; Almacen → `ProfitStockHelper.php`).
- `bin/test_nvidia_brain.php`: eliminadas las 6 importaciones/registros manuales de la raíz antigua — todo se registra vía `loadFromDirectory()`.
- **`ToolRegistry::loadFromDirectory()`**: ahora escanea EXCLUSIVAMENTE las 5 subcarpetas departamentales (whitelist `Almacen|Auditoria|Compras|Despacho|Recepcion`); la raíz de Tools/ y cualquier carpeta fuera del whitelist se ignoran.

**FASE 2 — Superconector PHP ↔ Python:**
- **`Adapters/python_skill_bridge.py`** (nuevo): puente universal — recibe `{modulo, accion, params}` en JSON por stdin, importa `skills.<modulo>`, invoca la acción por kwargs y devuelve una línea JSON por stdout. Nunca lanza (TypeError/Exception → `{"success": false, "error", "tipo"}`). Fuerza UTF-8 en stdin/stdout (`sys.stdout.reconfigure`) — sin esto, al ser pipes, Python emitía cp1252 y `json_decode` fallaba con tildes.
- **`Adapters/PythonSkillExecutor.php`** (nuevo): `runSkill(script, params, accion): array` vía `proc_open` (stdin JSON / stdout JSON), timeout configurable por `_timeout_s` (default 90s, tope 300s, `proc_terminate`), detección de intérprete cacheada (`ARA_PYTHON` > `py -3.14` > `py` > `python`), sanitización UTF-8 y parse de la última línea JSON; SIEMPRE retorna `{"success": true, "data"}` o `{"success": false, "error"}` sin tumbar el motor.
- **3 Tools activadores** (implementan `AgentToolInterface`, registrados automáticamente):
  - `Tools/Auditoria/PythonAuditoriaSkillTool.php` (`python_auditoria_skill`) → `skills/auditoria/{detector_malsurtido,discrepancia_traslados}`.
  - `Tools/Recepcion/PythonRecepcionSkillTool.php` (`python_recepcion_skill`) → `skills/recepcion/factura_vision_ocr` (`procesar_foto_factura`).
  - `Tools/Almacen/PythonStockBultoCerradoSkillTool.php` (`python_stock_bulto_cerrado_skill`) → `skills/stock_bulto_cerrado/{surtido_prioritario,reporte_quiebres_compras}`.

**FASE 3 — Verificación (2026-08-07, en vivo):**
- `py -3.14 -m pip install pyodbc` OK; `pyodbc.drivers()` = `['SQL Server', 'Microsoft Access Driver (*.mdb, *.accdb)', 'Microsoft Excel Driver (*.xls, *.xlsx, *.xlsm, *.xlsb)', 'Microsoft Access Text Driver (*.txt, *.csv)']`.
- `php -l` en los 33 archivos de `Tools/` (0 errores) + ejecutor, registry, arnés, adaptadores y webhook.
- Smoke test real (`smoke_python_bridge.php`, contrato `executeTool` del registry): **23 tools departamentales registradas (incluye los 3 wrappers), EXIT 0**:
  - `python_auditoria_skill` → `analizar_log_surtido` sobre LOG_REPORTE de prueba: success=true, 3 líneas, 2 discrepancias (OPERARIO 03/BC-01 y 07/BC-02), interrumpir_cierre=true.
  - `python_recepcion_skill` → imagen inexistente: success=true con diagnóstico honesto.
  - `python_stock_bulto_cerrado_skill` → `obtener_cola_surtido` SC: success=true, motor mysql, degradación honesta (mapeo estricto DESPACHO/DEPOSITO no expuesto en el esquema actual).
- **Bug corregido durante el smoke**: la salida del puente iba en cp1252 (pipes ≠ consola) y el JSON con tildes no se parseaba → `sys.stdout.reconfigure(encoding="utf-8")` en el puente.
- Regresión adaptadores: `bin/test_adapters_suite.php` → **4/4 PASS** (almacen 58ms, whatsapp 26s autenticación real FAR01361, finanzas begin→rollback BD intacta, tecnologia 4 conjuntos + estatus OK) — confirma que los requires/namespaces movidos no rompieron los flujos de producción.

**Pendientes:**
- Los skills de Python (v3.29) degradan a MySQL desde `py -3.14`; con pyodbc ya instalado probar `skills/base.py::conectar(engine='auto')` contra CRISTM25 (SQL Server) directamente.
- `.env` local aún con `MYSQL_USER=root` (v3.30) rechazado desde host `Bqto1`; GRANT `'jonaiber'@'%'` pendiente en el .148.

---

### v4.2 — Integración total de adaptadores PHP: 5 departamentos cubiertos (2026-08-07)

**Objetivo (directiva):** cobertura 100% del ecosistema — toda carpeta de `skills/` con su Adapter/Tool PHP en `app/Services/NvidiaBrain/Tools/<Departamento>/`. Se crearon los 2 módulos Python pendientes (`compras/`, `despacho/`) y los 2 wrappers PHP faltantes, con registro automático y comunicación estandarizada vía `PythonSkillExecutor` (UTF-8 stdin/stdout, JSON).

**FASE 1 — Módulos Python nuevos (patrón del ecosistema: base.py, best-effort, diagnóstico honesto):**
- **`skills/compras/quiebres_compras.py`**: `analizar_quiebres_compras(dias_rotacion=30, fraccion_tier1=0.20, minimo_alertar=0.0)` — quiebres reales (STOCK_ACT <= minimo) sobre la tabla de artículos resuelta por INFORMATION_SCHEMA, volumen de salidas de `reng_fac`+`reng_nde` en la ventana, umbral Tier 1 = fracción (Top 20%) del volumen total → `fallas_criticas_top20` (Tier 1) / Tier 2, `alertas_stock_cero` por SKU y resumen `por_laboratorio`.
- **`skills/despacho/cierre_despacho.py`**:
  - `validar_estructura_bultos(bultos, items)` — PURA (sin BD): gate 1:1 bulto/ítem del cierre transaccional (v3.29): bultos vacíos, multi-ítem, ítems sin bulto, `gate_1_1` bool.
  - `validar_bultos(nota, sede)` — estado real del flujo en el legacy (tabla `rep_not`/`notas_entrega`/`notas` + conteo en `cajas_embalaje`/`cajas`), `cierre_elegible` = estatus EMBALADA/CHEQUEO.
  - `precierre_transaccional(nota, sede, modo_real=False, bultos, items)` — plan de precierre: gate 1:1 + estado elegible + `transacciones_planeadas` (UPDATE estatus + bitácora `precierre_despacho_v4.2`); dry-run por defecto, `modo_real=True` solo PLANIFICA (nunca escribe; el cierre real lo aplica ARA_SYNC).
- `__init__.py` en ambas carpetas.

**FASE 2 — Adaptadores PHP nuevos:**
- **`Tools/Compras/PythonComprasSkillTool.php`** (`python_compras_skill`): llama `runSkill('compras/quiebres_compras', $arguments, 'analizar_quiebres_compras')`; params `dias_rotacion`/`fraccion_tier1`/`minimo_alertar`.
- **`Tools/Despacho/PythonDespachoSkillTool.php`** (`python_despacho_skill`): llama `runSkill('despacho/cierre_despacho', $params, $accion)` con acciones `validar_estructura_bultos`/`validar_bultos`/`precierre_transaccional`; params `nota`/`sede`/`modo_real`/`bultos`/`items`.

**FASE 3 — Verificación (2026-08-07, en vivo):**
- `py -3.14 -m py_compile` OK en los 2 módulos nuevos; `php -l` 0 errores en los 2 wrappers.
- Smoke test (`smoke_5_adapters.php`, contrato `executeTool` del registry): **25 tools departamentales, 5 adaptadores Python presentes, faltantes=ninguno, EXIT 0**:
  - `python_auditoria_skill` → 2 discrepancias detectadas en el log de prueba (interrumpir_cierre=true).
  - `python_recepcion_skill` → diagnóstico honesto (imagen inexistente).
  - `python_stock_bulto_cerrado_skill` → cola de surtido SC con degradación honesta.
  - `python_compras_skill` → success=true con degradación honesta (tabla `productos` del MySQL no expone cod_art/stock_act).
  - `python_despacho_skill` → **precierre real contra BD**: nota 72161167 en `rep_not`, estatus EMBALADA, `cierre_elegible=true`, transacción planeada `UPDATE rep_not SET estatus=CHEQUEADO (PLAN)` (dry-run, sin escritura).
- Gate 1:1 puro verificado en ambos sentidos: 3 bultos/3 ítems → `gate_1_1=true` ("todo bulto tiene exactamente 1 item y todo item tiene bulto"); 2 bultos/3 ítems → `gate_1_1=false` con items_sin_bulto.

**Estado final del ecosistema (5/5 departamentos):**
| skills/ | Wrapper PHP | Tool name |
|---|---|---|
| auditoria/ | `Tools/Auditoria/PythonAuditoriaSkillTool.php` | `python_auditoria_skill` |
| recepcion/ | `Tools/Recepcion/PythonRecepcionSkillTool.php` | `python_recepcion_skill` |
| stock_bulto_cerrado/ | `Tools/Almacen/PythonStockBultoCerradoSkillTool.php` | `python_stock_bulto_cerrado_skill` |
| compras/ | `Tools/Compras/PythonComprasSkillTool.php` | `python_compras_skill` |
| despacho/ | `Tools/Despacho/PythonDespachoSkillTool.php` | `python_despacho_skill` |

**Pendientes:**
- `skills/compras/quiebres_compras` consulta la tabla `productos` del MySQL (BD `comparador`) sin stock_act: el stock real de quiebres vive en Profit (`art.stock_act`, `st_almac`); el módulo queda listo para apuntar a SQL Server CRISTM25 cuando `base.conectar('auto')` lo priorice (pyodbc ya instalado).
- GRANT `'jonaiber'@'%'` en el .148 pendiente (`.env` local con `MYSQL_USER=root`).
### v4.4 � Men� interactivo de comandos '/' para skills NVIDIA BRAIN en el chat ARA (2026-08-07)

**Objetivo (directiva):** el chat web (	emplates/index.html) debe ejecutar las 25 tools departamentales por nombre con un men� flotante de comandos '/' (como Discord/Telegram bots), sin pasar por el LLM: escribir '/', el men� filtra por departamento o nombre, Enter/clic/Tab autocompleta '/nombre_tool clave=valor...', y Enter ejecuta la tool v�a un puente CLI PHP ? Flask ? respuesta renderizada como burbuja ARA con JSON formateado.

**Arquitectura (3 capas):**

1. **in/ejecutar_tool_cli.php � Runner CLI universal** (fuente �nica de verdad del ToolRegistry):
   - php bin/ejecutar_tool_cli.php __catalogo__ ? {success, total:25, catalogo:[{departamento, nombre, descripcion, parametros}]} (grupos: Almacen 11, Auditoria 5, Compras 2, Despacho 3, Recepcion 4).
   - php bin/ejecutar_tool_cli.php <tool> '<argumentsJSON>' ['<contextoJSON>'] ? {success, tool, resultado} (respuesta cruda del tool, incluida la envoltura {role:'tool', content:'<JSON>'} del executeTool).
   - Contrato: SIEMPRE una l�nea JSON en stdout UTF-8 (normalizaci�n CP1252?UTF-8), nunca excepciones; exit 0 �xito / 1 invocaci�n inv�lida. Errores controlados: 	ool_inexistente (lista las disponibles), json_invalido, uso_invalido.

2. **ra_server.py � Endpoints Flask** (secci�n 6, antes de if __name__):
   - GET /api/tools/catalogo ? subprocess PHP __catalogo__ (timeout 30s).
   - POST /api/tools/ejecutar {tool, arguments?, contexto?} ? subprocess PHP (timeout 300s por skills de visi�n/OCR).
   - _ejecutar_runner_tools(): subprocess.run con lista de argumentos, SIN shell (evita el mangleo de comillas del JSON en Windows), encoding='utf-8', CREATE_NO_WINDOW; ARA_PHP_EXE env configurable (default C:\tools\php\php.exe), fallback php.
   - Contrato HTTP: tool inexistente ? 200 {status:'error', tipo:'tool_inexistente'} (el frontend lo muestra como burbuja de error sin romper la UI).

3. **	emplates/index.html � Men� flotante + enrutado directo**:
   - HTML: #skill-command-menu absoluto sobre la fila del input (position:relative), placeholder actualizado a "( / para skills )"; el input cambia de onkeyup Enter?sendMessage() a oninput/onkeydown/onkeyup dedicados.
   - JS (bloque "MEN� DE COMANDOS '/'": l�neas ~6324-6550):
     - skillCatalog cacheado de /api/tools/catalogo; skillMenuOnInput() filtra por departamento exacto (/compras) o por nombre/descripci�n; comando exacto completo ? oculta el men�.
     - Navegaci�n ?/? (wrap), Tab/Enter autocompletan /tool (Enter marca skillMenuEnterHandled para no doble-enviar), Escape cierra, clic v�a event delegation con data-tool.
     - sendMessage() intercepta textos que empiezan con '/' y deriva a skillEnviarComando() (ruta directa, sin LLM): persiste el comando como mensaje del agente en la conversaci�n, parsea clave=valor con tipos (n�mero, booleano, JSON, string), ejecuta /api/tools/ejecutar, desnormaliza el tool_call (content string ? JSON) y renderiza burbuja ARA con <pre> JSON (max-height 320px, scroll) + indicador de escritura.

**Verificaci�n (2026-08-07, en vivo):**
- php -l 0 errores en in/ejecutar_tool_cli.php.
- Arn�s %TEMP%\opencode\test_ejecutar_tool_cli.php (proc_open en modo array = subprocess con lista): cat�logo 25 tools (Almacen 11/Auditoria 5/Compras 2/Despacho 3/Recepcion 4), python_despacho_skill real ejecutado (gate_1_1:true, 1.1s), 	ool_inexistente y json_invalido controlados ? **EXIT 0**.
- py_compile + import de ra_server OK (rutas /api/tools/catalogo, /api/tools/ejecutar registradas).
- Servidor real (waitress, puerto 5000): /api/health 200; /api/tools/catalogo 200 total=25; /api/tools/ejecutar con python_despacho_skill ? 200 {success:true, tool, resultado:{...gate_1_1:true}}; tool inexistente ? 200 {status:error, tipo:tool_inexistente}.
- Frontend: 7 bloques <script> pasan 
ode --check; el template renderizado contiene #skill-command-menu, skillMenuOnInput, skillEnviarComando (GET / 200, 461 KB).

**Lecciones:**
- escapeshellarg() en Windows mangla las comillas dobles del JSON (las convierte en espacios): el runner DEBE invocarse con lista de argumentos (proc_open array / subprocess list), nunca por l�nea de comando concatenada.
- El onkeyup inline previo disparaba sendMessage() en Enter incluso con el men� abierto: se sustituy� por handlers propios con bandera skillMenuEnterHandled (keydown marca ? keyup consume).
- El ToolRegistry escribe su log de registro a error_log() (stderr), no a stdout: el JSON del runner queda intacto para el adaptador Flask.

### v4.5 — Correcci�n cr�tica: chequeo/registro.php escribe en la MySQL legacy (rep_not + gestion) (2026-08-07)

**Objetivo (directiva):** el log de chequeo dec�a "marcada como CHEQUEADA" pero la interfaz web mostraba celdas vac�as o `0000-00-00 00:00:00`. Causa ra�z: `chequeo/registro.php` conectaba a un SQL Server inexistente (`ProfitPlus`, user `sa`, password placeholder `SuClaveSQL_Aqui`), con sintaxis `[tablas]`/`GETDATE()` de SQL Server — la escritura jam�s llegaba a la MySQL legacy de donde lee la web.

**Diagn�stico (probe INFORMATION_SCHEMA en vivo, 192.168.4.148 / jonaiber / Crist2026.):**
- `rep_not` (BD `barquisimeto`): `id, fec_profit, fec_impr, fec_creacion, verificacion, ruta, cod_nota, estatus`; estatus en datos: IMPRESA/PREPARACION/EMBALADA/CHEQUEADA.
- `gestion` ES la trazabilidad f�sica del flujo: bloque CHEQUEO = `verifi_cheq` (varchar 30, valor legacy 'VERIFICADA'), `numeroMesa` (int), `num_cheq` (int, n�mero de chequeador), `tip_pre` ('CHEQUEADOR'), `ubicacion2` ('CHEQUEO'), `hora2` (datetime del chequeo). Nota 72161630 confirmaba el bug: `verifi_cheq=''`, `num_cheq=0`, `hora2=0000-00-00 00:00:00`.
- La referencia `visor/registro.php` de la directiva NO existe; la real es `legacy_visor/registro.php` (SQL Server, no MySQL).

**Cambios en `chequeo/registro.php`:**
- `DB_CONFIG` ahora es MySQL legacy (`MYSQL_HOST/MYSQL_DATABASE/MYSQL_USER/MYSQL_PASSWORD/MYSQL_PORT` por env, defaults .148/barquisimeto/jonaiber/Crist2026.) — se movi� de `const` a `define()` porque `getenv()` no es v�lido en expresiones constantes.
- `db_conectar()`: DSN `mysql:...` con pdo_mysql (driver verificado en C:\tools\php); sin fallbacks a sqlsrv/odbc/mssql.
- `columnas_tabla()`: `INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE()` (MySQL), columnas en min�sculas.
- `registrar_estado_legacy($nota, $advertencias, $chequeador, $mesa, $hora)`:
  - **Transacci�n at�mica**: rep_not + gestion se escriben juntas; ante cualquier error `rollBack()` y advertencia expl�cita (nada de impactos a medias).
  - **rep_not**: `UPDATE estatus='CHEQUEADA' WHERE cod_nota=?` (idempotente).
  - **gestion**: `UPDATE` bloque CHEQUEO (`verifi_cheq='VERIFICADA'`, `numeroMesa`, `num_cheq`, `tip_pre='CHEQUEADOR'`, `ubicacion2='CHEQUEO'`, `hora2=$hora`) `WHERE cd_barr=?`; si 0 filas → **INSERT** con valores por defecto para las columnas NOT NULL restantes (dedupe de columnas en min�sculas — corrige el "Column 'hora2' specified twice" del primer intento).
  - Fecha inyectada ($hora validada Y-m-d H:i:s, TZ America/Caracas) como valor de `hora2`, nunca `0000-00-00`.
- `registrar_bitacora()`: sintaxis MySQL (backticks, `NOW()`, `AUTO_INCREMENT`), sigue siendo opcional/silenciosa.

**Verificaci�n (harness %TEMP%\opencode\test_chequeo.php contra BD scratch `ara_chequeo_test` copiada con CREATE TABLE LIKE, luego eliminada):**
- Ruta UPDATE (nota 72161630, fila existente): 1 fila actualizada; post: `verifi_cheq='VERIFICADA'`, `num_cheq=9`, `tip_pre='CHEQUEADOR'`, `ubicacion2='CHEQUEO'`, `hora2=2026-08-07 17:33:49`, rep_not CHEQUEADA → **TEST OK**.
- Ruta INSERT (nota 72161071, sin fila en gestion): "fila creada (INSERT 1)" con los mismos valores → **TEST OK**.
- ARA no estaba corriendo en .148:5000 (connection refused) → el envío best-effort a `/api/preparacion_hex/finalizar` se degradó en advertencia sin romper la transacción (por diseño).
- Nota: en PHP 8.3.33 `exit()` NO ejecuta bloques `finally` (verificado empíricamente) → el harness usa `register_shutdown_function` para capturar la respuesta del endpoint.

**Pendientes:**
- GRANT `'jonaiber'@'%'` en el .148 (el `.env` local con `root`/`10445610` sigue rechazado desde host Bqto1).
- La nota 72161630 en PRODUCCIÓN sigue con `verifi_cheq=''`/`hora2=0000-00-00` (las pruebas se hicieron solo contra scratch): una vez en producción, re-disparar el chequeo desde la web repara la fila automáticamente.

### v4.7 — Hermes Agent instalado como orquestador local + empalme bidireccional ARA ↔ Hermes (2026-08-10)

**Objetivo (directiva):** instalar NousResearch hermes-agent en el servidor (Windows nativo, sin WSL) como orquestador de uso interno de ARA, configurarlo con la API key NVIDIA + DeepSeek V4 Flash, y crear el empalme bidireccional: ARA puede delegar consultas a Hermes (`hermes_chat` en el catálogo de 26 tools) y Hermes puede ejecutar las tools de ARA (skill `ara-tools`).

**Instalación (Windows nativo):**
- El script `install.sh` aborta en Windows (pide `install.ps1`); WSL2 instalado pero sin distro. Se usó el instalador oficial: `powershell -File scripts/install.ps1 -SkipSetup -NonInteractive` (firmado, clona con git, venv con uv, launchers).
- Hermes Agent v0.20.0 (2026.8.3), Python 3.11.15, instalado en `C:\Users\Personal\AppData\Local\hermes` (venv en `hermes-agent\venv`, comando `hermes.exe` ya en PATH de usuario).
- **Config** (`%LOCALAPPDATA%\hermes\config.yaml`): `model.default: deepseek-ai/deepseek-v4-flash-0731`, `model.provider: nvidia`, `model.base_url: https://integrate.api.nvidia.com/v1`, `terminal.cwd: C:\ARA_PROYECT`.
- **`.env`**: `NVIDIA_API_KEY=nvapi-...` añadida (clave validada: HTTP 200 en `https://integrate.api.nvidia.com/v1/models`; el ID oficial del modelo es `deepseek-ai/deepseek-v4-flash-0731` — el sufijo -0731 se confirmó contra el catálogo NVIDIA).
- Nota de seguridad: la clave quedó expuesta en el historial del chat; se acordó rotarla después (pendiente).

**Errores evitados:**
- `-replace` de PowerShell con `(?s)` se comió todo config.yaml (regex con `.*` final) → se regeneró desde `cli-config.yaml.example` del repo clonado.
- YAML: backslashes de rutas Windows dentro de comillas dobles = escape inválido (`\A`) → usar comillas simples (`'C:\ARA_PROYECT'`) en config.yaml y frontmatter de SKILL.md.

**Empalme ARA → Hermes (tool `hermes_chat`):**
- Nuevo departamento `Orquestador/`: `app/Services/NvidiaBrain/Tools/Orquestador/HermesChatTool.php` (tool `hermes_chat`, parámetros: mensaje requerido, modelo, session_id, timeout_s).
- `ToolRegistry::loadFromDirectory()`: añadido `'Orquestador'` a la lista de departamentos (línea 81).
- Frontend `templates/index.html`: `SKILL_DEPARTAMENTOS` + `'orquestador'`.
- Ejecución: `proc_open` con array de argumentos (sin shell, sin `escapeshellarg` — lección v4.4) → `hermes chat -q <mensaje> -Q --no-restore-cwd`; env `HERMES_HOME` explícito; timeout con `proc_terminate` (default 240s, tope 300s); **cwd aislado** `%TEMP%\opencode\hermes_ara_work` (Hermes restaura sesiones por cwd y las sesiones previas del repo contaminaban la respuesta).
- Hallazgos de formato: en modo no-TTY el `session_id:` va a **stderr** (no stdout); el resultado del runner va serializado como JSON string dentro de `resultado.content` (contracto ToolRegistry).
- Variables de entorno configurables: `ARA_HERMES_BIN`, `ARA_HERMES_CWD`.

**Empalme Hermes → ARA (skill `ara-tools`):**
- `C:\Users\Personal\AppData\Local\hermes\skills\ara-tools\SKILL.md`: frontmatter (name, description con comillas simples — bug YAML de backslash, author, platforms windows, prerequisites php) + guía: invocar `php C:\ARA_PROYECT\bin\ejecutar_tool_cli.php __catalogo__` y `<tool> '<argumentsJSON>'`, catálogo por departamento, buenas prácticas.
- Carga con `hermes chat -s ara-tools ...`; la lista local `hermes skills list` la muestra como `local/enabled`.

**Verificación E2E (todo en vivo, 2026-08-10):**
- Catálogo runner: 26 tools (25 + hermes_chat en Orquestador). `php -l` OK en la tool nueva.
- Driver `%TEMP%\opencode\driver_hermes_chat.php` EXIT 0: catálogo 26; turno 1 responde `puente_ok` (23s) con session_id capturado; turno 2 con `--resume` continúa el hilo (`puente_ok`); mensaje vacío → fallo controlado.
- Hermes → ARA: `hermes chat -q "ejecuta el runner __catalogo__..." -s ara-tools` → ejecutó el runner real y reportó 26 tools (1 de Orquestador), correcto.
- Server ARA reiniciado (PID 30596, waitress, `C:\ARA_PROYECT\ara\venv\Scripts\python.exe`): `/api/tools/catalogo` vía LAN (192.168.4.217:5000) = 26 con hermes_chat; `GET /` 200 (466 KB) con departamento orquestador; `POST /api/tools/ejecutar` hermes_chat → `{"ok":true,"respuesta":"e2e_ok","session_id":"20260810_091556_3ab822"}`.
- Nota ambiente: `127.0.0.1:5000` está secuestrado por un proceso externo `Challenge.exe` (bind más específico); ARA se sirve por IP LAN 192.168.4.217:5000 (y 100.70.87.43 Tailscale). No tocar Challenge.exe.

**Pendientes:**
- Rotar la API key NVIDIA expuesta en el chat.
- Prueba del menú `/` del frontend con `hermes_chat` desde el navegador (burbuja de resultado persistida).
- `hermes gateway`/cron con la skill `ara-tools` si se quiere automaizar (opcional).

### v4.6 — Aislamiento de eventos en comandos de skills: interceptor exclusivo '/' (2026-08-07)

**Síntoma reportado:** al enviar un comando de skill (ej. `/almacen`), la interfaz mostraba un spinner breve pero "revertía" al flujo de chat regular, enviando el comando como texto plano a ARA.

**Causa raíz (traza completa):**
1. `skillEnviarComando` (index.html) persistía el comando vía `POST /api/chat/enviar` con `remitente: 'agente'` y el texto crudo `/tool ...`.
2. `chat_enviar` (`ara/ARA_Brain/chat_routes.py:755`) disparaba `_procesar_respuesta_ara_bot_async` para **cualquier** mensaje de una conversación con el bot (sin filtro de remitente ni de contenido) → el LLM procesaba `/tool ...` como chat regular y guardaba una respuesta 'sistema'.
3. El polling (`/api/chat/poll`, cada 3s) detectaba el mensaje nuevo → `loadMessages()` re-renderizaba desde la BD: el spinner y la burbuja de resultado (locales) se borraban, mostrando el comando como texto plano + la respuesta LLM → parecía "revertir al flujo nativo". Además `loadMessages()` al final de la ejecución ya borraba la burbuja de resultado (no persistida).

**Cambios (3 capas):**

1. **`templates/index.html` — interceptor exclusivo en `sendMessage`** (antes de CUALQUIER llamada a la API regular):
   - `sendMessage(fileData, fileType, event)`: si el texto empieza con `/` → `event.preventDefault()` + `event.stopPropagation()` → `skillEnviarComando(text)` → `return false` (corta el flujo normal de chat).
   - Callers pasan el evento: botón enviar `onclick="return sendMessage(null,'text',event)"` y `skillMenuOnKeyUp` → `sendMessage(null,'text',e)`.
   - Eliminado el bloque de intercepción duplicado de v4.4 (quedaba un único camino).

2. **`templates/index.html` — `skillEnviarComando` aislada y con manejo de errores completo:**
   - `stopPolling()` al inicio y `startPolling()` en `finally` (el polling no puede re-renderizar ni revolver el flujo durante la ejecución).
   - **Manejo de errores de `POST /api/tools/ejecutar`**: `AbortController` con timeout de 320s (el runner del servidor aguanta 300s; error claro "Tiempo de ejecución agotado" para AbortError), validación de `res.ok` (HTTP no-2xx → mensaje con status), respuesta no-JSON → error explícito, y `finally` que siempre retira el spinner.
   - **Resultado persistido** como mensaje `remitente: 'sistema'` (`[skill /tool]\n<JSON>`) → sobrevive a `loadMessages()`/poll/recarga de página (el comando ya se persistía; el resultado no).

3. **`ara/ARA_Brain/chat_routes.py` — guard de aislamiento en `chat_enviar`:** el hilo LLM del bot solo se lanza cuando `remitente == 'agente'` **y** el contenido NO empieza con `/` (comandos de skill corren por el puente CLI, no por el LLM) y nunca para `remitente == 'sistema'` (resultados de skill persistidos). La lógica de chat nativa de ARA jamás se ejecuta en paralelo al comando.

**Verificación (2026-08-07):**
- `node --check` sobre el bloque JS inline de `templates/index.html` (428 KB, 1 bloque) → OK.
- `python -m py_compile chat_routes.py` → OK.
- Traza lógica del guard en los 3 casos: mensaje agente normal → LLM SÍ; mensaje agente `/tool` → NO; mensaje 'sistema' (resultado skill) → NO.
- Flujo de teclado re-trazado: Enter con menú abierto (selección) → keydown consume (flag `skillMenuEnterHandled`), segundo Enter ejecuta; Enter con menú cerrado → keyup → `sendMessage(null,'text',e)` → interceptor.

**Pendientes:**
- Prueba en vivo con el servidor real (waitress + frontend) cuando ARA .148 esté disponible: `/python_despacho_skill nota=...` debe mostrar la burbuja de resultado persistida sin respuesta LLM intercalada.

### v4.5 � Correcci�n cr�tica del UPDATE a gestion en chequeo/registro.php (2026-08-07)

**Objetivo (directiva):** el chequeo se registra pero gestion (MySQL barquisimeto) queda con celdas vac�as y hora2 = 0000-00-00 (0 filas afectadas por desajuste de WHERE o de la variable POST recibida). Corregir b�squeda/actualizaci�n con control de owCount(), INSERT forzado cuando la fila no existe y error_log detallado.

**Diagn�stico real (evidencia contra el MySQL .148):**
- Esquema de gestion (21 columnas): cd_barr int(11) NOT NULL (clave de la nota), erifi_cheq, 
umeroMesa, 
um_cheq, 	ip_pre, ubicacion2, hora2 + bloque de preparaci�n/embalaje. **TODAS las columnas son NOT NULL sin default** y la tabla NO tiene clave �nica (156,016 filas).
- La fila de una nota chequeada exist�a creada por el flujo de preparaci�n con erifi_cheq='', 
um_cheq=0, hora2=0000-00-00: el UPDATE no la tocaba y la web mostraba las celdas vac�as.
- El bloque fallback de INSERT exist�a pero **faltaba 'ubicacion'** entre las columnas NOT NULL ? error MySQL 1364 "Field 'ubicacion' doesn't have a default value" ? excepci�n PDO ? **rollback de toda la transacci�n** (rep_not + gestion), dejando todo sin escribir.
- El POST del formulario legacy usa la clave 
ota, pero la web/sync pueden enviar codigoBarra, 
umero, actura, codigo, 
um_not (no estaban en los aliases ? "Falta la nota" o silencio).

**Fixes aplicados a chequeo/registro.php:**
1. **Aliases ampliados**: codigoBarra, codigo_barr, 
umero, actura, codigo, 
um_not adem�s de los previos (
ota, 
um_nota, 
otar, 
ot_num).
2. **Normalizaci�n de nota**: si no es num�rica pura (NC-72160252, 72160252-1), se extrae la secuencia \d{6,12} porque gestion.cd_barr es INT (el (int) viejo convert�a 'NC-...' en 0 ? WHERE sin match).
3. **PDO::MYSQL_ATTR_FOUND_ROWS => true** en db_conectar(): owCount() de UPDATE cuenta filas COINCIDENTES, no solo modificadas (un re-chequeo con valores id�nticos ya no cae al INSERT).
4. **Bloque gestion reescrito**: UPDATE con valor normalizado (int si num�rica); si owCount() === 0 ? SELECT 1 de verificaci�n (la tabla no tiene clave �nica, evitar duplicados):
   - fila existe ? retry UPDATE (carrera/re-chequeo), NUNCA insertar duplicado;
   - no existe ? INSERT con TODAS las NOT NULL (a�adida ubicacion = 'CHEQUEO') + catch de carrera: si error 23000/Duplicate ? retry UPDATE; cualquier otro error ? re-lanzar para rollback.
5. **error_log detallado**: claves POST recibidas + nota/responsable/mesa/hora; en gestion: columna resuelta, SQL del UPDATE, filas; INSERT con columnas; normalizaci�n de nota; retries. Todo tambi�n en dvertencias de la respuesta JSON (debug por consola).

**Verificaci�n en vivo (MySQL .148, nota real 72161688 � fila con el bug exacto):**
- POST 
ota=72161688 responsable=03 ? ep_not: 1 fila + **gestion: bloque de chequeo actualizado (1 fila)** ? fila queda erifi_cheq='VERIFICADA', 
um_cheq=3, 	ip_pre='CHEQUEADOR', ubicacion2='CHEQUEO', hora2=18:05 (�ya no 0000-00-00!).
- Re-chequeo inmediato (misma nota, responsable 05, mesa 2) ? UPDATE 1 fila, **1 sola fila en gestion (sin duplicados)**.
- Nota compuesta NC-72161688 ? normalizada a 72161688 ? UPDATE 1 fila.
- Nota ficticia 72000051 (sin fila ni en rep_not ni gestion) ? gestion: fila creada (INSERT 1) con ubicacion='CHEQUEO' y hora2 valor real; fila de prueba eliminada despu�s (no ensuciar producci�n). La fila de 72161688 se dej� (trazabilidad real completada).
- php -l 0 errores. Los error_log aparecen en stderr del CLI con los par�metros y resultados.

**Lecciones:**
- El fallback INSERT debe cubrir TODAS las columnas NOT NULL sin default de la tabla real (verificar con SHOW COLUMNS, no asumir el esquema).
- owCount() de MySQL reporta filas MODIFICADAS por defecto: sin FOUND_ROWS, un re-chequeo con valores id�nticos se confunde con "no existe" y dispara el INSERT (y si hubiera clave �nica, duplicado/rollback).
- En tablas sin clave �nica, el fallback UPDATE?INSERT necesita verificaci�n SELECT previa para no crear duplicados.
- La BD legacy .148 sufri� saturaci�n transitoria (colas de queries congelando SELECTs simples ~5 min): los E2E de este m�dulo deben reintentarse y no culpar al c�digo de timeouts ambientales.

### v4.7 — Reconfiguraci�n del daemon vigilar_datos.py: stock en 1 minuto con lecturas ultra-ligeras (2026-08-10)

**Objetivo (directiva):** reconfigurar `vigilar_datos.py` para sincronizar el Stock desde Profit (CRISTM25, 192.168.4.20) cada 1 minuto con lecturas ultra-ligeras (`WITH (NOLOCK)`, solo columnas indispensables, conexi�n corta con cierre inmediato) y bulk UPSERT local en ARA (SQLite `stock_maestro`), manteniendo la sincronizaci�n de ubicaciones (`art.campo7`) en su intervalo heredado de 300s, SIN bloquear el ERP, con temporizadores independientes y try/except aislados por tarea.

**Dise�o implementado (reescritura de `ara/ARA_Brain/vigilar_datos.py`):**

1. **Conexi�n corta a Profit (cero bloqueos):** `_consultar_profit()` abre la conexi�n pyodbc, ejecuta el SELECT, extrae con `fetchall()` y **cierra la conexi�n en `finally` ANTES de tocar la BD local** (sin conexi�n persistente entre ciclos). Reintentos con backoff (env `PROFIT_DB_MAX_RETRIES`/`PROFIT_DB_RETRY_DELAY_S`, defaults 3/3s) solo ante errores ODBC de clase '08' (08S01/08001/HYT00) v�a `_es_error_reintentable()`; dem�s errores se propagan al try/except aislado de su ciclo.
2. **Lecturas `WITH (NOLOCK)` ultra-ligeras:**
   - Stock: `SELECT LTRIM(RTRIM(co_art)), SUM(stock_act) FROM st_almac WITH (NOLOCK) WHERE LTRIM(RTRIM(co_alma)) = ? GROUP BY LTRIM(RTRIM(co_art))` — 1 tabla, 3 columnas (solo `co_art/co_alma/stock_act`; `stock_com` excluido: no est� en el esquema documentado de st_almac ni existe columna destino local).
   - Ubicaciones: `SELECT LTRIM(RTRIM(co_art)), LTRIM(RTRIM(campo7)) FROM art WITH (NOLOCK) WHERE LTRIM(RTRIM(campo7)) <> ''`.
3. **Bulk UPSERT local:** una sola transacci�n, `executemany` en lotes de 500 (`_TAM_LOTE`), `UPDATE stock_maestro SET despacho_bqto = ?, actualizado_el = datetime('now','localtime') WHERE codigo = ?` (stock) y `SET campo7 = ?, actualizado_el = ...` (ubicaciones). Columna auditor�a `actualizado_el TEXT` creada autom�ticamente v�a `ALTER TABLE` si falta (`_asegurar_columna_auditoria()`, idempotente).
4. **Temporizadores independientes:** `_INTERVALO_STOCK_S=60` (env `VIGILAR_STOCK_INTERVALO_S`) y `_INTERVALO_UBICACIONES_S=300` (env `VIGILAR_UBICACIONES_INTERVALO_S`, hereda `VIGILAR_PERIODO_S`). `bucle_periodico()` con `time.time()` y sleep(1): cada ciclo corre en su propio try/except — un fallo puntual de red/SQLite jam�s detiene el daemon ni la otra tarea. Se preserva el vigilante watchdog (migrar_a_sql + sincronizar() combinado al detectar cambios).
5. **M�tricas en log:** cada ciclo imprime cantidades y duraci�n (`X art�culos en Ys total (upsert local Zs)`); modo degradado expl�cito si Profit no devuelve filas.
6. **Blindaje de consola:** `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")` + mensajes de log ASCII-safe (sin emojis) — un `print` con car�cter fuera del codepage cp1252 romp�a el ciclo en consolas Windows (UnicodeEncodeError real detectado en smoke test).

**Verificaci�n (2026-08-10):**
- `py -3.14 -m py_compile vigilar_datos.py` → OK.
- Smoke test contra Profit REAL (192.168.4.20): stock BQTO **10,689 art�culos en 0.22s** (upsert local 0.06s); ubicaciones **8,674 art�culos en 0.08s** (upsert local 0.04s) — extracci�n en milisegundos confirmada; `actualizado_el` creado en copia de prueba.
- Smoke test del bucle (intervalos 2s/3s, 7s de corrida): ticks stock=3, ubi=2 → temporizadores independientes OK; ciclos aislados (~0.2s c/u) sin interferencia mutua.
- Degradaci�n verificada: con .20 transitoriamente inalcanzable (error 08001) el ciclo reporta y contin�a sin crashear.
- La BD real `proyecto_ara.db` ya tiene la columna `actualizado_el` (creada por el import del m�dulo, idempotente).

**Pendientes:**
- Ejecutar el daemon en producci�n (waitress/PC de la red interna) y observar 2-3 ciclos de 60s en el log.
- Probar el watchdog (cambio en Libro1.xlsx/usuarios.json/reporte de ubicaci�n) que dispara `migrar_a_sql()` + `sincronizar_campo7()` combinado.

### v4.8 — Captura automatica de argumentos en tools: texto plano libre (2026-08-10)

**Objetivo (directiva):** permitir que los comandos de skill reciban parametros en texto plano libre (ej. `/consultar_nota 72160754`) sin depender de claves `clave=valor`, y que las tools acepten el texto crudo.

**Cambios:**
- `app/Services/NvidiaBrain/Tools/Almacen/ConsultarNotaTool.php`: si no llega `num_nota`, se reunen todos los valores string/numericos de cualquier clave (`reunirTexto()`, arrays anidados 1 nivel incluidos) y se extrae la primera secuencia numerica de 7-8 digitos con regex `/([0-9]{7,8})/`; si no hay digitos, se usa el texto crudo.
- `app/Services/NvidiaBrain/Tools/Almacen/Orquestador/HermesChatTool.php`: cadena de fallback `mensaje` → `query` → `text` → implode de valores string/numericos (misma reunion con arrays de 1 nivel).
- `ara/ARA_Brain/templates/index.html` `skillEnviarComando`: si el rawArgs no contiene claves `clave=valor`, se reenvia como `{text: rawArgs.trim()}`.

**Verificacion:** `php -l` OK en los 2 PHP; `GET http://192.168.4.217:5000/api/notas/lista?busqueda=72160754` → HTTP 200.

### v4.9 — Pivot de stock: 4 almacenes corporativos (01/02/04/05) en vigilar_datos.py (2026-08-10)

**Objetivo (directiva):** pivot de `st_almac` con `SUM(CASE WHEN co_alma ...)` para los 4 almacenes corporativos y actualizacion masiva de las 4 columnas de stock en SQLite.

**Hallazgos de la sonda previa (`%TEMP%\opencode\probe_stock_pivot.py`):**
- `st_almac` real: alma 01 (11.844 filas), 02 (10.689), 04 (8.572), 05 (7.612), ademas de 9999/03/06/07/08 minoritarios. El mapeo 01→despacho, 02→deposito, 04→despacho_bqto, 05→deposito_bqto coincide con la directiva.
- La tabla local NO es `inventario` (como pedía la directiva): es `stock_maestro` (5417 filas, 1 codigo duplicado, indice idx_stock_maestro_codigo NO unico). Se adapto la directiva: INSERT selectivo de codigos faltantes + UPDATE masivo (no ON CONFLICT).

**Cambios en `ara/ARA_Brain/vigilar_datos.py`:**
- `_leer_stock()`: consulta pivoteada `SELECT RTRIM(co_art), SUM(CASE WHEN LTRIM(RTRIM(co_alma))='01' THEN stock_act ELSE 0 END) AS despacho, ... '02'→deposito, '04'→despacho_bqto, '05'→deposito_bqto FROM st_almac WITH (NOLOCK) WHERE LTRIM(RTRIM(co_alma)) IN ('01','02','04','05') GROUP BY RTRIM(co_art)` (se elimina el parametro `_ALMACEN_BQTO`, queda la env sin uso).
- `sincronizar_stock()`: crea renglones faltantes (INSERT selectivo sobre codigos existentes, lotes de 500) + `executemany` de `UPDATE stock_maestro SET despacho=?, deposito=?, despacho_bqto=?, deposito_bqto=?, actualizado_el=datetime('now','localtime') WHERE codigo=?`.
- Docstring de clase actualizado (pivot 4 almacenes + creacion de faltantes).

**Verificacion (2026-08-10):**
- Corrida real: **11.957 articulos en 0.61s** (upsert local 0.11s); 6.668 renglones creados (faltantes del catalogo); `stock_maestro` 12.085 filas, 11.957 con `actualizado_el`.
- Verificacion cruzada SQLite vs Profit: **10/10 codigos coinciden** exactamente en las 4 columnas.
- Daemon relanzado (venv, `vigilar_datos.py`, ciclo 60s/300s): primer ciclo fallo por error de red transitorio ODBC 10054 (reintentado OK en el siguiente ciclo); `actualizado_el` avanza en cada ciclo. Una instancia duplicada (lanzada por el wrapper del shell a las 11:22:46) fue detectada y eliminada; queda solo el par venv-redirector → pythoncore (PID 30100/24448).
- Server HTTP: se reinicio con `-X utf8` (sin el flag, redirigir stdout a archivo rompe con emojis cp1252).

### v4.10 — Entrada de texto plano + respuestas en formato Tarjeta (Cards) (2026-08-10)

**Objetivo (directiva):** 1) skills reciben parametros en texto plano libre (ej. `/buscar_inventario evigax`); 2) la salida JSON cruda se convierte en una tarjeta visual Markdown legible.

**Cambios:**
- `app/Services/NvidiaBrain/Tools/Almacen/BuscarInventarioTool.php`:
  - Captura flexible: `$busqueda = $arguments['busqueda'] ?? $arguments[0] ?? $arguments['query'] ?? reunirTexto($arguments)`.
  - Columna `vencimiento` (sinonimos fv/fec_vto/fecha_vto/vencimiento/fecha_vencimiento) agregada a CAMPOS y al SELECT.
  - `formatAsCard($busqueda, $productos)`: sin resultados → `"⚠️ No se encontraron productos coincidentes con 'X'."`; con resultados → `📦 *RESULTADOS DE INVENTARIO* (N Ítems encontrados)` + `━━━` + por producto `🔹 *CÓDIGO:* / 📝 *Producto:* / 📅 *Vencimiento:* / 📊 *Stock Total:* / 💵 *Precio:*` + linea divisoria. Helpers `fmtNumero`/`fmtPrecio`/`reunirTexto`.
  - Retorno de `execute()` agrega clave `card` con la tarjeta.
- `app/Services/NvidiaBrain/Tools/Almacen/ConsultarNotaTool.php`:
  - Constructor con env: `baseUrl` vacia → `getenv('ARA_ERP_URL')` → fallback `http://127.0.0.1:5000` (el loopback esta secuestrado por otro proceso en la maquina de produccion).
  - `formatAsCard($d)`: `📄 *NOTA DE ENTREGA / DESPACHO:* #num` / `👤 *Cliente / Almacén:* razon (co_cli) — Almacén: X` / `📌 *Estatus:*` / `📦 *Total Renglones:* N | Unidades: U` / `━━━` / renglones alineados `%3d. [%-12s] desc — Cant: X und | Prep: Y`. Retorno agrega `card` (exito y nota inexistente).
- `ara/ARA_Brain/templates/index.html`: `skillBurbujaRespuesta` renderiza `content.card` como texto plano pre-wrap (sin JSON) cuando existe; la persistencia del mensaje 'sistema' guarda la card en lugar del JSON.
- `ara/ARA_Brain/ara_server.py`: `_ejecutar_runner_tools()` inyecta `ARA_ERP_URL = http://<IP_real>:5000` al runner PHP (helper `_ip_real()`) si la env no existe — asi `consultar_nota` consulta al propio server vía la IP LAN y no al proceso que ocupa el loopback.

**Verificacion (2026-08-10):**
- `php -l` OK en ambos tools; `py_compile` OK en ara_server.py.
- Arnés CLI (`proc_open`, env `ARA_ERP_URL`): `buscar_inventario {text:evigax}` → card con los 2 productos EVIGAX (MD02009 stock 488, MD03163 stock 203); `consultar_nota {text:72160754}` → card de FARMACIA CASANOVA VIÑEDO, C.A (FAR01723), 1 renglon, 40 und.
- E2E vía LAN `POST /api/tools/ejecutar` (flujo exacto del chat): ambas tools responden HTTP 200 con `card` completa (sin envoltorio JSON en el cuerpo visible).
- Server reiniciado con `-X utf8` + logs redirigidos; daemons de stock intactos.

### v4.11 — Skill /gestion_super_esteroide_search: traza de tiempos + dashboard global (2026-08-10)

**Objetivo (directiva):** reestructurar `GestionSuperEsteroideSearchTool.php` como skill de texto plano: nota individual (traza completa de tiempos por estado con responsables y deltas) o informe global de productividad con la flag 'global'.

**Esquema real verificado (sonda DESCRIBE, MySQL legacy 192.168.4.148/barquisimeto):**
- La directiva pedía columnas `fec_prepara/id_prepara` etc. que NO existen. Adaptación a las reales: `rep_not` (fec_creacion —viene vacío en producción; el registro real es `fec_impr`, fallback `fec_profit`), `gestion` (`hora`/`hora2`/`hora3` = DATETIME completos de preparación/chequeo/embalaje; `num_prep`/`num_cheq`/`num_emb` = IDs de operador; `verifi_pre`/`verifi_cheq`/`verifi_emb` = 'VERIFICADA'), `usuarios.numero` → nombre (JOIN).
- `rep_not.estatus` en producción: 'EMBALADA'/'PREPARACION'/'IMPRESA'/'CHEQUEO' (731 filas 'CHEQUEO').
- Nota 72160754 real: preparación 10:17:58 (JESUS GARCIA id 39), embalaje 10:34:27 (DORIN QUELIZ id 6), SIN chequeo (verifi_cheq vacío — el chequeo de bulto cerrado vive en otra tabla).

**Implementación (reescritura del tool):**
- **Parseo de entrada (v4.10 texto plano):** `num_nota` → `[0]` → `query` → `text` → `reunirTexto()`. Contiene 'global' → dashboard; regex `([0-9]{7,8})` → traza individual; nada → error con card.
- **Modo individual:** cabecera rep_not + gestión con 3 LEFT JOIN a usuarios; hitos solo para fases realmente verificadas; deltas en minutos (Registro→Prep, Prep→Cheq, Cheq→Emb) + tiempo de vida total (último hito − registro). Tarjeta Muestra 1: 1️⃣ Registro → 2️⃣/3️⃣/4️⃣ con emoji por fase, responsable, deltas y vida total.
- **Modo global:** métricas agregadas del día (notas iniciadas, 3P completas, AVG vida, eficiencia SLA = % de 3P con vida ≤ 40m), Top 5 clientes del día (GROUP BY co_cli/cli_des), líderes por proceso (más notas verificadas en su hito + % del total del proceso del día) y gráficas ASCII █/░ (10 celdas) por fase vs metas SLA (constantes editables: 15/10/10/40 min). Tarjeta Muestra 2 completa.
- Contrato v4.10: `execute()` devuelve `['success'=>true, 'data'=>..., 'card'=>...]`; el LLM recibe el JSON estructurado y el chat muestra la card.

**Bugs encontrados y corregidos durante la verificación:**
- **Error MySQL 1267 (Illegal mix of collations)** en el JOIN `rep_not.cod_nota = CAST(gestion.cd_barr AS CHAR)` (utf8mb4_0900_ai_ci vs utf8mb4_general_ci): el COUNT 3P daba 0 silenciosamente. Fix: `BINARY CAST(... AS CHAR)` en el ON del JOIN.
- **Defaults de credenciales del trait `AlmacenDbTrait::conectarMySQL()` eran root/sin password** (no conectaba vía server; solo funcionaba con env explícitas). Alineados a los del legacy `chequeo/registro.php`: jonaiber/Crist2026./barquisimeto (env MYSQL_* siguen siendo override). Beneficia a todos los tools MySQL del chat.
- Render: contador de pasos arrancaba en 1️⃣ para Preparación (registro ya es 1️⃣); corregido con mapa 1-4. Deltas nulos mostraban "—m"; ahora "—" sin sufijo.

**Verificación (2026-08-10):**
- CLI (arnés con proc_open): `72160754` → traza (Registro 09:11:53 → Prep 10:17:58 JESUS GARCIA → Cheq pendiente → Emb 10:34:27 DORIN QUELIZ; vida 83m); `global` → dashboard (185 notas del día, 67 completas 3P, AVG 86.57m, eficiencia 25.4% = 17/67 ≤ 40m, Top 5 con datos reales, líderes MIGUEL CAMPOS 25.4% / ARGENIS RAMIREZ 50.7% / JORGE GOMEZ 23.0%, barras ASCII por fase).
- E2E vía LAN `POST /api/tools/ejecutar`: nota 3P real 72161985 → 1️⃣ Registro 09:52:16 → 2️⃣ Prep 09:52:45 (EDGAR DUNO) → 3️⃣ Cheq 10:09:21 (ARGENIS RAMIREZ) → 4️⃣ Emb 10:12:06 (CARLOS PIMENTEL), deltas 0m/17m/3m, vida 20m; `[global]` → dashboard completo. HTTP 200 en ambos.
- Registro en orquestador: automático vía ToolRegistry (catálogo 26) y ya referenciado en la skill ara-tools de Hermes; descripción actualizada en `getDescription()` (visible en `__catalogo__`).
- `php -l` OK.

### v4.12 — Reconocimiento de intenciones → activación automática de skills en el chat ARA (2026-08-10)

**Objetivo (directiva):** implementar la matriz oficial de intenciones (lenguaje natural ➔ skill) en el chat de ARA. REGLA ABSOLUTA: jamás responder "no tengo información" sin intentar primero la skill correspondiente. Mapeo: nota de entrega → `/consultar_nota` (num_nota); inventario/stock/producto → `/buscar_inventario` (busqueda); métricas/tiempos/traza/rendimiento → `/gestion_super_esteroide_search` (num_nota o 'global'). Conversación simple (saludos, funciones) NO dispara skills.

**Diseño (matriz en código, refuerzo en prompt):**

1. **`ara/ARA_Brain/chat_routes.py` — capa `_detectar_intencion_skill(mensaje)`** (regex sobre texto normalizado: acentos quitados vía NFKD→ascii, minúsculas):
   - Prioridad 1: métricas — `tiempos/traza/trazabilidad de la nota <dígitos>` → `gestion_super_esteroide_search{num_nota}`; `como van las metricas/rendimiento global/top de clientes/metricas de hoy` → `{num_nota:'global'}`.
   - Prioridad 2: nota con verbos — `consulta/busca/revisa/revisar/dame datos de <...> nota <dígitos>` y `que paso con la nota X` → `consultar_nota{num_nota}`.
   - Prioridad 3: nota desnuda — cadena aislada de 7-8 dígitos (patrón oficial 72160754) → `consultar_nota`.
   - Prioridad 4: inventario — `busca el producto X / hay stock de X / cuanto X queda / revisa (el) inventario de X / precio o vencimiento de X` → `buscar_inventario{busqueda}` con `_extraer_termino_busqueda()` (limpia prefijos/sufijos de la frase: 'cuanto evigax queda?' → 'evigax').
   - Mensajes que empiezan con `/` quedan fuera (ya viven del flujo explícito v4.6).
2. **`_ejecutar_skill_auto(skill, arguments)`** — ejecuta la skill por el MISMO camino del chat explícito: import local de `ara_server._ejecutar_runner_tools` (evita ciclo de import) → runner PHP `[skill, json(arguments), json(contexto)]` con timeout 60s; extrae la card anidada de `resultado.resultado.content` (JSON string del runner) y la deja en `resultado['card']`; cualquier fallo → `None` (el flujo cae al LLM, cumpliendo "intentar primero la skill").
3. **Hook en `_procesar_mensaje_ara_bot()` (paso 0, antes de cualquier búsqueda SQL/LLM):** si `_detectar_intencion_skill` matchea y la skill tiene éxito → responde DIRECTAMENTE con la card (`🤖 *ARA*:\n<card>`), sin LLM ni SQL local. Solo si falla sigue el flujo conversacional existente.
4. **Refuerzo en `system_ctx`** del LLM (regla 4): matriz resumida + prohibición explícita de "no tengo información/no puedo acceder" sin intentar la skill (cubre el caso en que la skill falló y responde el LLM).
5. **Blindaje de consola en `ara_server.py`** (patrón v4.7): `reconfigure(encoding='utf-8', errors='replace')` en stdout/stderr al import — el print de arranque `📝 Tabla log_ia_feedback inicializada.` (línea 72) reventaba el IMPORT de ara_server en consolas cp1252 (UnicodeEncodeError), tumbando silenciosamente toda la capa de skills.

**Verificación (2026-08-10):**
- `py -3.14 -m py_compile chat_routes.py ara_server.py` → OK.
- Test unitario del detector (21 casos): 19 frases de la matriz (nota con/sin verbo, nota desnuda, inventario con tildes y sin ellas, tiempos/traza, global, top clientes) → mapeo exacto a skill+argumentos; 2 casos de conversación simple ('hola buenos días', '¿cuál es tu función?') → None; `/consultar_nota ...` → None (excluido). 21/21. Durante el test se detectaron y corrigieron 2 gaps de regex ('revisa el inventario de X', 'cuanto stock de X queda').
- E2E vivo contra el ERP (runner PHP real): `buscar_inventario{evigax}` → card con MD02009 (stock 488) y MD03163 (stock 203); `consultar_nota{72160754}` → card FARMACIA CASANOVA VIÑEDO, C.A (FAR01723), 1 renglón, 40 und; `gestion_super_esteroide_search{global}` → dashboard real del día (185 notas, 68 3P, AVG 86.87m, eficiencia 25.0%, Top 5 y líderes). Las 3 cards extraídas y legibles.

**Pendientes:**
- Prueba en vivo del flujo completo del chat (waitress + frontend): enviar "consulta la nota 72160754" sin `/` → debe responder la card de consultar_nota sin respuesta LLM intercalada.
- `Import openpyxl` sigue fallando en el arranque del server (afecta solo los estáticos del visor, no las skills) — instalar `pip install openpyxl` o verificar el venv del server.

### v4.13 — Intent Routing en el System Prompt del motor Hermes (PHP) (2026-08-10)

**Objetivo (directiva):** llevar la matriz oficial de intenciones (v4.12) al System Prompt maestro del motor agéntico PHP (equivalente real de `Prompts/SystemPrompt.php` = `OpenCodeRules.php`; equivalente de `HermesOrchestrator.php` = `OpenCodeAgentEngine.php`) y garantizar que las tool definitions viajen con function calling para que el LLM devuelva `{"tool": "...", "parameters": {...}}`.

**Cambios:**

1. **`app/Services/NvidiaBrain/OpenCodeRules.php` — `getAgentSystemPrompt()`:** nueva sección `[DIRECTIVAS DE INTENT ROUTING - MATRIZ OFICIAL]` que instruye al LLM a iniciar SIEMPRE la tool correcta ante: (1) consulta de nota / número aislado de 7-8 dígitos → `consultar_nota {num_nota}`; (2) producto/stock → `buscar_inventario {busqueda}`; (3) métricas/tiempos/rendimiento → `gestion_super_esteroide_search {num_nota:<dígitos>|global}`; (4) sin intención operativa → responder como asistente conversacional. Además sección `[RESPUESTA TRAS TOOL CALLING]`: tras ejecutar una tool, resumir la card al usuario sin mencionar "la función llamada es...".
2. **`app/Services/NvidiaBrain/NvidiaBrainClient.php` — `normalizarToolCalls()`:** acepta ahora TRES formatos de tool_call en `content` (los modelos qwen/llama a veces no emiten `message.tool_calls` nativo y devuelven el JSON como texto): A) legacy `{"name","arguments"}` (existente); B) matriz oficial `{"tool","parameters"}`; C) variante `{"tool_name","arguments"}`. El JSON se decodifica y se sintetiza un tool_calls OpenAI real para que `OpenCodeAgentEngine` lo ejecute. Cerró el gap detectado en verificación: el LLM devolvía `{"tool":"buscar_inventario","parameters":{...}}` y el motor lo ignoraba como texto plano.
3. **`app/Services/NvidiaBrain/Tools/Almacen/ConsultarNotaTool.php` — `getDescription()`:** ampliada con las frases de activación ("consulta la nota X", "dame datos de la nota X") y el parámetro obligatorio, para reforzar la asociación del modelo. (`buscar_inventario` y `gestion_super_esteroide_search` ya traían descripciones ricas.)
4. Verificado que el orquestador ya envía las tool definitions con function calling: `OpenCodeAgentEngine::run()` → `registry->getDefinitions()` → `client->chatCompletions($messages, $tools, 'auto', ...)` → `buildPayload()` con `tools` + `tool_choice='auto'` (Ollama qwen2.5-coder:3b primario, NVIDIA NIM meta/llama-3.1-8b fallback).

**Verificación (2026-08-10):**
- `php -l` OK en OpenCodeRules.php, NvidiaBrainClient.php, ConsultarNotaTool.php.
- Arnéz CLI (`%TEMP%\opencode\arnes_intent_routing.php`) montando el stack Hermes completo (NvidiaBrain pool + ToolRegistry 26 tools + OpenCodeAgentEngine) con la matriz en el prompt:
  1. "Hola, consulta la nota 72160754..." → tool_call `consultar_nota {num_nota:72160754}` → respuesta "El cliente de la nota 72160754 es FARMACIA CASANOVA VIÑEDO, C.A. y tiene 40 items." ✅
  2. "busca el producto evigax" → tool_call `buscar_inventario {busqueda:evigax}` → respuesta con los 2 ítems reales (SIMETICONA 125MG (EVIGAX) CJ X 10 y X 20). ✅ (Este caso fallaba antes del fix del normalizador: el JSON `{tool,parameters}` quedaba como texto.)
  3. "como van las metricas" → tool_call `gestion_super_esteroide_search {num_nota:global}` ✅
- E2E chat web LAN (cumple el pendiente de v4.12): `POST /api/chat/enviar` con telefono `ara_bot` y contenido "consulta la nota 72160754" (sin `/`) → el bot respondió la card de `consultar_nota` (NOTA DE ENTREGA/DESPACHO #72160754, FARMACIA CASANOVA VIÑEDO, C.A (FAR01723) - Almacén BARQUISIMETO, Estatus 2, Total Renglones 1), SIN respuesta LLM intercalada. El bot escribe la card en `ultimo_mensaje` de la conversación (poll v4.6).

**Pendientes:**
- El LLM cloud (llama-3.1-8b) a veces resume pobremente la card de `gestion_super_esteroide_search` (responde "la función llamada es...") pese al refuerzo del prompt; evaluar subir `max_tokens` o forzar el resumen con el modelo local cuando responda la card.
- Ollama local tardó 120s (OLLAMA_TIMEOUT_S=120 del entorno) en el caso 1 durante la verificación (modelo ocupado); el Circuit Breaker conmutó a Cloud correctamente. Evaluar bajar a 25-30s el timeout del entorno para no degradar la UX.

### v4.13bis � Orden de Ejecuci�n CTO v4.11 (T1/T2/T3): cards CardBuilder en 27 skills + NOLOCK + consultar_nota MySQL (2026-08-10)

**Objetivo (directiva):** ejecutar la Orden de Ejecuci�n CTO v4.11 � (T1) conectar consultar_cliente al Profit real con WITH (NOLOCK) read-only y timeout 5s; (T2) estandarizar las 27 skills PHP a tarjetas Markdown v�a helper CardBuilder (nunca JSON crudo al chat); (T3) restaurar routing a favor de /consultar_nota y a�adir lookup MySQL rep_not+gestion con fallback al API.

**Cambios:**

1. **T1 � Despacho/ConsultarClienteTool.php:** lectura directa Profit clientes WITH (NOLOCK) (constructor ('', timeoutS=5, limite=100), baseUrl opcional desde env ARA_ERP_URL), 3 modos (co_cli exacto ? LIKE comod�n ? cli_des %termino%), mont_cre?limite_credito, inactivo???, tarjeta v�a CardBuilder (modo b�squeda candidatos top 12 + ficha completa), estado de cuenta opcional v�a API ARA (fallo silencioso ? null), finally cierra conexi�n. Verificado en vivo: ficha FAR01361 = FARMACIA BOTIMARKET, C.A, l�mite Bs 77.378,61, saldo 0, ?? INACTIVO, 92ms; b�squeda BOTIMARKET ? 2 clientes.
2. **T2a � Tools/Common/CardBuilder.php (nuevo):** header/body/footer, ANCHO_MAX=80, LINEAS_MAX=20, seccion/campo/linea/lista/barra/fila/resumir() (resumir() gen�rico para skills Python), corte multibyte por palabras, formatos numero/decimal/moneda/porcentaje/fecha/estado con emojis ????????????. ToolRegistry::loadFromDirectory() ampliado con 'Common'.
3. **T2b/T2c/T2d � 26 tools refactorizadas a CardBuilder** (Almac�n 12, Auditor�a 5, Recepci�n 4, Compras 2, Despacho 3): todas exponen card en el �xito y en los errores clave; las 4 skills Python (Despacho/Auditor�a/Recepci�n/Compras) envuelven PythonSkillExecutor::runSkill() con CardBuilder::resumir() + tarjeta de error. HermesChatTool (Orquestador) NO se toc�: devuelve espuesta texto plano del modelo (no JSON crudo; una card truncar�a an�lisis largos). GestionSuperEsteroideSearchTool ya cumpl�a (v4.12).
4. **NOLOCK/select-only (v4.14 wrapper):** ConnectionWrapper era el �nico camino permitido (SELECT-only estricto, NOLOCK forzado, timeout 5s, cache SQLite). Se detect� y corrigi� que ProfitStockHelper hab�a perdido conectar() (revertido por sync externo a las 15:39) y 3 tools llamaban al m�todo inexistente: ConsultarClienteTool, ReporteQuiebresComprasTool, DiscrepanciaTrasladosTool ahora crean 
ew ConnectionWrapper() (timeoutS 5) y usan querySafe(). Se a�adi� el equire de ConnectionWrapper al runner real in/ejecutar_tool_cli.php (faltaba: el CLI no tiene autoloader Composer). Nota del driver ODBC "SQL Server": rechaza FROM tabla WITH (NOLOCK) alias (SQLSTATE 102/4104) ? los joins con alias se reescribieron como subconsultas con NOLOCK dentro (discrepancia_traslados, quiebres) � hallazgo de validaci�n.
5. **T3 � Almacen/ConsultarNotaTool.php:** fuente primaria MySQL legacy ep_not+gestion v�a AlmacenDbTrait (resolverTabla/resolverColumnas, variantes con prefijo A, fechaLegible() filtra  000-00-00, gestionEnLegacy() con verifi_cheq='VERIFICADA', tip_pre, num_prep/num_cheq), fallback API ARA /lista+/detalle para renglones, enriquecimiento Profit best-effort, origen en (mysql_legacy / ara_api / mysql_legacy (API ca�do)). Verificado en vivo: 72160754 ? EMBALADA, preparador 39, prep 2026-08-06 10:17:58, chq � (0000-00-00 filtrado), origen mysql_legacy (API ca�do).
6. **in/ejecutar_tool_cli.php:** +equire app/Services/ConnectionWrapper.php (contrato del runner sin autoloader).

**Verificaci�n (2026-08-10):**
- php -l OK en los 29 archivos de Tools (incl. CardBuilder).
- Arn�z CLI matriz (test_cli_runner.php en %TEMP%, args en base64 � el wrapper de PowerShell borra las comillas dobles de los args a exes nativos; el runner real acepta JSON plano de cualquier cliente real): consultar_cliente FAR01361 ?, consultar_nota 72160754 ? (EMBALADA, prep 2026-08-06 10:17:58), buscar_inventario EVIGAX ? (2 �tems MD02009/MD03163), gestion global ? (257 notas), reporte_quiebres ?, discrepancia_traslados ?, consultar_saldo_cliente ?, flujo_notas_tiempo_real ?, detector_errores_nota ?, monitor_modificaciones_eliminaciones ?, stock_surtido_prioritario ?, bulto_cerrado_router ? (MD02009 estante 5/BC 0), trazabilidad_vida_util_nota ?, rendimiento_preparadores ?, detector_malsurtido_log ?, recepcion_factura_vision ?. Todas con card.
- Runner real (Start-Process, sin el bug de comillas): consultar_cliente {"co_cli":"FAR01361"} ? {"success":true,"tool":"consultar_cliente","resultado":{...}} ?.

**Advertencias:**
- **Concurrencia externa detectada:** durante la validaci�n, un proceso externo (sync GHA/filezilla o segundo agente) reescribi� ProfitStockHelper.php (revertido a la versi�n v4.14 ConnectionWrapper a las 15:39, sin conectar()), y migr� en paralelo ReporteQuiebresComprasTool/DiscrepanciaTrasladosTool a ConnectionWrapper con comentarios propios (ODBC alias). Validar que no haya m�s conflictos antes de commit; los cambios actuales quedaron consistentes con el wrapper.
- 	est_cli_runner.php/.bat (arn�s temporal) permanecen en %TEMP% � no forman parte del repo.

### v4.14 � Orden de Emergencia ZOMBIS PHP: limpieza + prevenci�n (3 capas) (2026-08-10)

**Objetivo (emitida por CTO, cr�tica):** las skills PHP se ejecutaban pero no mor�an; quedaban en background consultando a Profit hasta matarlo (imagen del Admin: 11+ procesos VS Code + PHP zombis). Soluci�n 3 capas: Limpia + Wrapper Suicida + Watchdog.

**Cambios:**

1. **Capa 1 � Limpieza inmediata:** Get-Process php,php-cgi | Stop-Process -Force + dejar 1 solo VS Code. En el momento de ejecuci�n NO hab�a zombis (0 php, 0 Code) ? comando no-op; qued� verificado que tras cada skill no queda ning�n php.exe vivo.
2. **Capa 2 � Wrapper suicida en in/ejecutar_tool_cli.php:** al inicio: declare(ticks=1) (verificado compatible tras declare(strict_types=1)), set_time_limit() + ini_set max_execution_time, memory_limit 128M, egister_tick_function vigilante que aborta graceful con JSON a stderr ANTES del hard kill, y egister_shutdown_function con gc_collect_cycles(). Al final: bloque gc_collect_cycles(); exit(0); (MUERTE GARANTIZADA). L�mite: default 30s; hereda ARA_CLI_MAX_S del invocador (35s / 300s); las tools delegadas python_* y hermes_chat reciben 300s autom�tico (corren subproceso propio con timeout interno � PythonSkillExecutor ya mata su Python a =300s; HermesChatTool su loop con usleep(20ms) y tope propio) � �nica tool con while(true) del cat�logo, leg�tima.
3. **Capa 3 � Fusible en el invocador Python (ra_server.py):** _ejecutar_runner_tools reescrita de subprocess.run(timeout) a Popen + communicate(timeout) + proc.kill() + proc.wait() en TimeoutExpired, propagando ARA_CLI_MAX_S = timeout_s al hijo. Nueva _timeout_runner_tool(tool): 35s para skills directas de datos, 300s s�lo para hermes_chat y python_* (delegadas). /api/tools/ejecutar pas� de timeout fijo 300s al fusible por tool. py_compile OK.
4. **Capa 4 � Watchdog in/watchdog_mata_zombis.ps1:** mata php.exe/php-cgi.exe >60s vivos (360s para delegadas python_*/hermes_chat, que se auto-suicidan =300s), log en ra/ARA_Brain/data/watchdog_zombis.log. Ejecutado OK (0 zombis). Tarea programada \ARAWatchdogZombis creada: cada 5 minutos, ejecuta powershell -NoProfile -ExecutionPolicy Bypass -File C:\ARA_PROYECT\bin\watchdog_mata_zombis.ps1, estado "Listo" (pr�xima 6:29 PM).
5. **Capa 5 � pp/Services/ConnectionWrapper.php:** a�adido __destruct() que cierra la conexi�n SQLite de cache ($cachePdo = null) + gc_collect_cycles(). Las conexiones a SQL Server ya se cerraban con $pdo = null en el inally de querySafe()/queryProfit() (clase es PDO, no sqlsrv_* directo � la pseudofirma del CTO usaba sqlsrv_close, aqu� el equivalente correcto).

**Verificaci�n (2026-08-10):**
- php -l OK en ejecutar_tool_cli.php y ConnectionWrapper.php; py_compile OK en ra_server.py.
- Runner real: php bin/ejecutar_tool_cli.php consultar_cliente '{"co_cli":"FAR01361"}' ? exit 0, JSON correcto, **0 php.exe vivos tras terminar**.
- Cat�logo __catalogo__ emite (26 tools) y muere.
- **Test anti-zombi: 5/5 ejecuciones seguidas de consultar_cliente OK; 0 procesos php a los 5s y a los 35s.**

**Decisiones con criterio (documentadas):**
- El candado no se aplica plano a python_*/hermes_chat: sus subprocesos largos son OROJESTACI�N leg�tima (visi�n OCR hasta 90s, Hermes hasta 300s), NO consultas a Profit; matarlos a 30s romper�a la funci�n. Se les dio 300s y el watchdog los respeta hasta 300s.
- declare(ticks=1) tras declare(strict_types=1) en el mismo archivo es v�lido (verificado por php -l y ejecuci�n real); se evitaron tick + strict_types combinados en una sola sentencia (fatal de PHP).

**Pendiente:** recrear la tarea si se formatea el equipo o se cambia la ruta del proyecto. El arn�s temporal 	est_cli_runner.php/.bat permanece en %TEMP% (no forma parte del repo).

---

### v4.15 — Fase 2.4: reconexi�n de TODO el stack PHP a la BD de pruebas PRUEB25 (2026-08-11)

**Objetivo (orden aprobada por el CEO):** ninguna conexi�n PHP a SQL Server puede apuntar a CRISTM25; todas pasan a la BD de pruebas PRUEB25 (mismo host 192.168.4.20:1433, credenciales profit/profit, SOLO cambia el nombre de BD, confirmado por el CTO). Estandarizar a ConnectionWrapper donde sea posible, candado anti-zombi en los scripts CLI y documentar el mapa de conexiones.

**Tarea 1 — Auditor�a (mapa aprobado):**
- Conexiones migrables a ConnectionWrapper (SELECT-only): 20 tools + helpers (ConnectionWrapper.php, BaseAdapter.php, conectar_profit_read.php, AlmacenDbTrait.php, ProfitStockHelper.php y tools de Almacen/Auditoria/Compras/Despacho).
- Conexiones directas NO migrables, con justificaci�n: `ConciliarFacturaTool` (UPDATE en transacci�n con beginTransaction/rollback — el wrapper rechaza escrituras), `BaseAdapter`/`TecnologiaAdapter` (usa `EXEC sp_IA_Obtener_Metricas` — el wrapper bloquea EXEC), `SqlServerIaLogger` (INSERT de log), `conectar_profit_read.php` (helper de lectura web).
- Fuera de alcance: `legacy_visor/{registro,lista}.php` (SQL Server ProfitPlus, stub de plantilla) y `chequeo/registro.php` (MySQL legacy barquisimeto).

**Tarea 2 — Reemplazo CRISTM25→PRUEB25 (28 archivos):**
- 4 conectores funcionales + 24 archivos v�a replaceAll (tools, adaptadores, controllers, webhook, arn�s bin/test_*.php). Incluye comentarios y footers de cards ("Fuente: Profit PRUEB25 (NOLOCK)").
- Verificaci�n: `rg "CRISTM25|CRIST25" --glob "*.php"` = **0 resultados**; `rg "sqlsrv_connect"` = **0 resultados** (toda conexi�n pasa por PDO ODBC / ConnectionWrapper).

**Tarea 3 — Migraci�n a wrapper documentada:** 20+ puntos ya usaban ConnectionWrapper; los 4 directos quedan justificados y apuntando a PRUEB25 (default en c�digo, env-overridables PROFIT_SQL_*).

**Tarea 4 — Candado anti-zombi en scripts CLI:** `bin/ejecutar_tool_cli.php` ya lo ten�a (v4.14). A�adido el mismo candado (declare(ticks=1), set_time_limit default 300s override ARA_CLI_MAX_S tope 600, memory_limit 128M, register_tick_function + register_shutdown_function) a los 5 arneses: test_adapters_suite.php, test_nvidia_brain.php, test_tecnologia_adapter.php, test_notas_tiempo_real_tools.php, test_whatsapp_webhook.php.

**Tarea 5 — Verificaci�n (en vivo):**
- `php -l` 28/28 OK (herramienta C:\tools\php\php.exe, PHP 8.3.33).
- Smoke real contra PRUEB25: `consultar_cliente {busqueda}` (245ms, exit 0), `buscar_inventario {co_art}` (497ms, exit 0) — conexi�n y lectura OK.
- Arneses: test_whatsapp_webhook 10/10 PASS (tras a�adir `require ConnectionWrapper.php` al arn�s — defecto preexistente del arn�s, no del reemplazo); test_adapters_suite 3/4 PASS (falla solo Tecnologia: SP sp_IA_Obtener_Metricas ausente en PRUEB25); test_notas_tiempo_real_tools 11/15 PASS (resto: drift preexistente del arn�s llamando GestionSuperEsteroideSearchTool::derivarUbicacion que ya no existe + notas de ejemplo ausentes en PRUEB25).
- **0 procesos php.exe vivos tras todas las pruebas** (candado anti-zombi OK).

**Pendientes / riesgos:**
- `sp_IA_Obtener_Metricas` NO existe en PRUEB25 (el script est� en `app/Services/NvidiaBrain/database/sp_IA_Obtener_Metricas.sql`): el adaptador de Tecnolog�a falla hasta que el SP se cree en la BD de pruebas (o se ajuste el criterio de aceptaci�n).
- Los tools con datos de ejemplo (notas/cliente) devuelven "no encontrado" en PRUEB25 si el dato no existe en la copia — comportamiento esperado de una BD de pruebas.
- En runtime, las env PROFIT_SQL_NAME/PROFIT_DB_NAME del servidor podr�an pisar el default PRUEB25; verificar que el servidor de producci�n no tenga esas env fijadas a CRISTM25.
- Proceso externo concurrente (sync GHA) sigue editando archivos: antes de commit, re-verificar con rg/php -l.
- Drift preexistente en test_notas_tiempo_real_tools.php: llama a `GestionSuperEsteroideSearchTool::derivarUbicacion()` (est�tico) que ya no existe en la tool (metodo renombrado/eliminado) — reparar el arn�s en una orden aparte.

### v4.16 — Fase 2.5: hardening de conexiones SQL (T2 query timeout 30s / T3 circuit breaker / T4 prohibir CRISTM25 / T5 mantenimiento SQL) (2026-08-11)

**Objetivo (orden aprobada):** blindar las conexiones a Profit contra queries colgadas y SP inexistentes: timeout de query de 30s en ConnectionWrapper, circuit breaker para SP inexistente (SQLSTATE 2812), validaci�n de entorno que proh�be CRISTM25, y guion SQL de monitoreo/matado de conexiones dormidas.

**T2 — Query timeout 30s (nativo):**
- Backend nativo ODBC adoptado en esta m�quina (PHP 8.3.33 ZTS, solo pdo_odbc; sin pdo_sqlsrv; sin permisos admin para instalar ODBC 18, EXIT=1603). `odbc_setoption($conn, 1, 0, segundos)` (SQL_ATTR_QUERY_TIMEOUT a nivel conexi�n) es el �nico mecanismo que corta `WAITFOR DELAY` en el driver legacy.
- `ConnectionWrapper` migrado a dispatcher `ejecutar()` → ruta `ejecutarOdbc()` (odbc_connect con `Connection Timeout=` parametrizado, `odbc_setoption(conn,1,0,$queryTimeout)`, `SET LOCK_TIMEOUT ms`, `odbc_prepare/odbc_execute`, `odbc_fetch_array`, cierre en finally) y ruta `ejecutarSqlSrv()` (producci�n, PDO::SQLSRV_ATTR_QUERY_TIMEOUT).
- Constructor separa `$timeoutS` (login, env `PROFIT_CONNECT_TIMEOUT`, default 5s) de `$queryTimeout` (env `PROFIT_QUERY_TIMEOUT`, default **30s**, tope 600s); p�blicos `setQueryTimeout()/getQueryTimeout()`. `traducirBindings()` convierte `:c`→`?` solo para listas asociativas.
- `ConnectionWrapperException` (PDOException): verificado en vivo en `bin/test_fase25_wrapper.php` — WAITFOR 35s → excepci�n a los 30.0s, SQLSTATE S1T00, mensaje "Query timeout after 30s".

**T3 — Circuit breaker SP inexistente (2812):**
- `ConnectionWrapper`: `spBloqueado()`, `registrarFalloSP()`, `resetFalloSP()`; 3 fallos SQLSTATE 2812 en ventana 60s → circuito abierto 300s (constantes `CB_*`). Bug corregido: primera vez arranca `primera`=now (antes `ahora - 0 > 60` reseteaba siempre).
- `TecnologiaAdapter` integra `ConnectionWrapper` (lazy `circuitBreaker()`), fail-fast `circuitoAbierto()` antes de ejecutar, registra fallo en catch solo si `esSpInexistente()` (2812), `resetFalloSP()` tras �xito. Verificado en `bin/test_fase25_tecnologia_circuit.php`: llamadas 1-3 = 2.2/1.5/1.2ms success=false (2812), llamada 4 = 0ms fail-fast.
- Requires de `ConnectionWrapper`/`ConnectionWrapperException` a�adidos en `bin/test_tecnologia_adapter.php`, `bin/test_adapters_suite.php`, `public/api/metricas.php`.

**T4 — Prohibici�n de CRISTM25:**
- `EnvironmentException` (RuntimeException). `ConnectionWrapper::validarEntorno()` lanza si la BD resuelta es CRISTM25 (culpable = PROFIT_SQL_NAME o PROFIT_DB_NAME).
- `bin/verify_env.php`: extensiones cr�ticas (pdo_odbc|pdo_sqlsrv + odbc + curl/mbstring/json), valida no-CRISTM25, imprime config resuelta y queryTimeout; exit 0/1/2/500. Verificado: normal `== OK: entorno SQL seguro (Fase 2.5) ==` (resuelve PRUEB25); forzando `PROFIT_SQL_NAME=CRISTM25` → exit 1 "Entorno prohibido: PROFIT_SQL_NAME=CRISTM25".
- `.env` l�nea 17 tiene `PROFIT_DB_NAME=CRISTM25` pero el runtime resuelve PRUEB25 porque la prioridad es PROFIT_SQL_NAME → PROFIT_DB_NAME y las env reales del proceso prevalecen (getenv) — documentado, no es un bug.

**T5 — Mantenimiento SQL (para el DBA):**
- `database/fase25_maintenance.sql` (idempotente): tabla `log_Fase25_Kills` (auditor�a BC), `sp_Fase25_MonitorConnections @top=100`, `sp_Fase25_KillSleeping @max_sleep_s=600 @excluir_login=NULL` (cursor, audita antes de KILL, TRY/CATCH ignora SPIDs que mueran entre medias), `sp_Fase25_AlertThreshold @umbral=25`. NO se ejecuta desde PHP.

**Arneses nuevos (candado anti-zombi + exit 0/1/2):** `bin/test_fase25_wrapper.php` (T2+T3 sobre ConnectionWrapper), `bin/test_fase25_tecnologia_circuit.php` (T3 integrado adapter), `bin/verify_env.php` (T4).

**Verificaci�n (en vivo):**
- `php -l` OK: ConnectionWrapper.php, ConnectionWrapperException.php, EnvironmentException.php, TecnologiaAdapter.php, 3 arneses, metricas.php.
- `bin/verify_env.php` normal: OK (PRUEB25, queryTimeout 30s); caso negativo CRISTM25: exit 1.
- Regresi�n: test_whatsapp_webhook 10/10 PASS; test_adapters_suite 3/4 PASS (Tecnologia sigue FAIL pre-existente: sp_IA_Obtener_Metricas ausente en PRUEB25, ahora con circuit breaker); test_notas_tiempo_real_tools 11/15 PASS (mismos 4 FAIL de drift preexistente: derivarUbicacion/derivarValidacion + notas de ejemplo ausentes — sin regresi�n por el backend ODBC).
- Smoke real: `consultar_cliente` (66ms, 0 clientes), `buscar_inventario` (0 encontrados) contra PRUEB25 v�a backend ODBC — bindings `:c` OK.
- **0 procesos php.exe vivos tras todas las pruebas** (candado anti-zombi OK).

**Pendientes / riesgos:**
- Opcional (no bloqueante): punto de entrada p�blico de `validarEntorno()` en `bin/ejecutar_tool_cli.php` (fail-fast en arranque CLI) y `public/api/metricas.php` (antes de conectar).
- `sp_IA_Obtener_Metricas` sigue ausente en PRUEB25: TecnologiaAdapter falla hasta que se cree el SP (ahora sin spam: circuit breaker lo apaga 300s tras 3 intentos).
- Ejecutar `fase25_maintenance.sql` en la BD por el DBA (crear SPs de monitoreo/kill); los daemons de vigilancia (watchdog sistema) siguen como respaldo final.
- Proceso externo concurrente (sync GHA) sigue editando archivos: re-verificar con rg/php -l antes de commit.

### v4.17 — Ajuste de Adaptadores (PRUEB25/Profit): 11 tools + envelope v4.17 + carteras/ficha/flujo/ciclo de vida (2026-08-11)

**Objetivo (orden aprobada):** ajustar los adaptadores PHP de los departamentos al ERP PRUEB25 (Profit, SQL Server, SELECT-only/NOLOCK) y al envelope estándar v4.17; reforzar cartera de facturas, consultas de cliente, compras priorizadas y ciclo de vida de notas. Sin endpoints HTTP nuevos; verificación vía arnés CLI.

**Envelope estándar v4.17 (`app/Services/NvidiaBrain/Tools/Common/Envelope.php`, NUEVO):**
- `Envelope::ok($data,$message)` → `{success,data,message,timestamp}` (timestamp ISO-8601). `Envelope::vacio($message='Sin registros')` → success=true, data=[], message "Sin registros". `Envelope::error($message,$codigo)` → success=false. `Envelope::conflicto($message)` → código `HTTP_409_CONFLICTO`. `Envelope::conCard($data,$message,$card,$ok=true)` conserva `card` (Markdown v4.13) y `ok` (retrocompat). `Envelope::resumen($exito,$mensaje,$us)` añade `tiempo_us` y `ok`.
- Contrato `getParameters()` de los 11 adaptadores validado como JSON Schema en el arnés (11/11 válidos).

**AD1 — `ConsultarNotaTool`:** envelope v4.17 + card Markdown; `normalizarEstado()` (Procesado/Modificado/Pendiente/raw); `nombre_cliente`; `lineas[]` con co_art/descripcion/cantidad/precio_unitario/total_linea; claves previas conservadas (retrocompat).

**AD2 — `BuscarInventarioTool`:** búsqueda por `co_art` (exacto → fallback `LIKE 'X%'`) con filtros `q`/`co_lin`/`co_subl`/`solo_disponibles`; item con keys: `co_art, art_des, principio_activo, vencimiento, stock_actual, stock_comprometido, stock_disponible (=max(0, stock_actual - stock_comprometido)), co_lin, co_subl, precio_base, ultima_fecha_movimiento`; sin resultados → success=true + `data.productos=[]` + "Sin registros" + card. `seleccionar()` y `formatAsCard()` actualizados a las claves nuevas.

**AD3 — `DetectorErroresNotaTool`:** `metricas_del_dia` = `{total_notas_hasta_momento, errores_duplicados, errores_impresion, estado_escaneo, ultima_nota_procesada, timestamp_escaneo, duplicados[], pendientes_impresion[]}`; query del día `CAST(fecha AS DATE)=CURDATE() LIMIT 2000`; `pendientesImpresion()` vía `app_log_impresiones` con NOT EXISTS; `estado` en el SELECT (bug corregido).

**AD4 — `MonitorModificacionesEliminacionesTool`:** capa 0 = logs `app_log_notas`/`app_log_eliminaciones` (MySQL legacy); fallback tabla traza → indicios indirectos; data con `fuente` (`app_logs`/`tabla_traza`/`indicios_indirectos`), `modificaciones[]`, `eliminaciones[]`, `eventos[]` (vista de compat: num_nota, tipo_evento, timestamp, usuario_modificador, antes, despues, motivo, fuente — exigida por `test_notas_tiempo_real_tools.php`), `total`, `advertencias`, `metric_us`. Helpers `leerAppLogs()`, `camposDelDetalle()`, `tarjetaEventos()`.

**AD5 — `ConsultarSaldoClienteTool`:** `cliente['descuento_unico']` (float|null, siempre presente; columna Profit reales: `desc_glob`/`desc_ppago` en `clientes`); `cartera['facturas'][]` = `{doc_num, fecha_factura, monto_original_bs, tasa_dia, monto_usd, dias_vencidos, saldo_restante_bs, tasa_estimada}`; `cartera['saldo_pendiente_usd']`; `tasaDelDia()` lee `saTasaHistorico` (candidatas: tasa_historico/tasa_hist/saTasaCambio) TOP 1 `fecha <= actual`, `tasa_estimada` si fechaTasa≠fecha; `diasVencidos()` en PHP; card con DTO y facturas en USD. Bug arreglado en vivo: `array_filter` eliminaba `descuento_unico` cuando era null → ahora la clave se reinserta explícitamente.

**AD6 — `TrazabilidadVidaUtilNotaTool`:** `UMBRALES_MIN = ['Pendiente'=>60, 'Procesado'=>90, 'Modificado'=>30]`; `construirCicloVida($timeline)` → `ciclo_vida[]` `{estado (Pendiente/Procesado vía estadoDeFase), fase, fecha, usuario, tiempo_en_estado_min}`, `tiempo_total_ciclo_min`, `alertas[]` DEMORA con mensaje sprintf; card con TIEMPO CICLO + alertas. Claves previas `timeline/deltas/items/estado_actual/estado_final` conservadas.

**AD7 — `ConsultarClienteTool`:** ahora usa `AlmacenDbTrait`; `direccion` resuelta dinámica (direccion/direc1/direccion1/dir1/direccion_fiscal), `telefonos[]` vía `listaTelefonos()`, `saldo_actual`, `credito_disponible=max(0,limite-saldo)`; `ultimasConsultas()` lee `app_log_consultas` con corte `fechaServidor()` (GETDATE SQL Server) −2 meses; registro best-effort de la consulta actual; `advertencias[]`. Salida añade: status, co_cli, telefono, telefonos, direccion, credito_disponible, ultimas_consultas, advertencias, ok.

**AD8 — `FlujoNotasTiempoRealTool`:** `flujoDelDia()` → `{fecha_consulta, hora_ultima_actualizacion, notas_del_dia, total_items_despachados, total_monto_despachado_bs, ultima_nota, tendencia (subiendo/bajando/estable), por_hora[][hora,cantidad]}`; procesada = estado null o contiene "PROCES"; integrado en retorno normal y vacío.

**AD9 — `PythonComprasSkillTool`:** data con `priorizados` `{alta, media, baja, total}` + `prioridad_alta/media/baja` + `fecha_consulta`; sub-resultado `skill` (Python) conservado (no rompe contrato previo); helpers `resumenComprasPriorizado()` (tablas st_almac/existencia/art/inventario/stock; defaults stock_minimo=10, stock_maximo=50; prioridad alta si disponible<=0 o ratio<1.5, media si <3, baja resto; `sugerido_comprar=max(0,round(max-stock))`), `rotacion30d()` (reng_nde/reng_ndd + notas/saNotaEntrega/repNotaEntrega/nota con `DATEADD(day,-30,GETDATE())`), `tarjetaPriorizados()`, `aUtf8()`. Fix en vivo: helper propio `micro()` (la clase no usa AlmacenDbTrait).

**AD10 — `RendimientoPreparadoresSmartAssignTool`:** `operadoresDelDia()` estático (testeable sin BD) → `operadores_del_dia[]` `{id,nombre,area,depto,ops,ops_pendientes,tiempo_promedio,tipo}`, `cuellos_de_botella[]` (ops_pendientes>20, con recomendacion de redistribuir) y `alerta_rendimiento[]` (tiempo_promedio>30, tipo RENDIMIENTO, mensaje); data añade esos + `fecha_consulta`; card muestra cuellos/alertas.

**BD local `app_` (MySQL legacy, barquisimeto):** `database/v4_17_app_tablas.sql` (idempotente) — `app_log_notas`, `app_log_eliminaciones`, `app_log_impresiones`, `app_log_consultas`, `app_notas_gestion`, `app_notas_gestion_items`. GestionNotasTool persiste ahí (CRUD Pendiente + auditoría; `garantizarTablas()` con CREATE IF NOT EXISTS).

**Arnés nuevo:** `bin/test_v4_17_adaptadores.php` — candado anti-zombi (declare(ticks=1), ARA_CLI_MAX_S=300), exige pdo_odbc+mbstring (exit 500), JSON `{suite 'adaptadores-v4.17', summary, results}`, 22 casos. **Resultado en vivo: 22/22 PASS** (16.4s). Diagnóstico real SOLO LECTURA: `consultar_nota(72000051)` degrade honesto (nota no existe), `detector_errores_nota` 1 analizada, `flujo_notas_tiempo_real` flujo_del_dia=0/estable, `monitor` fuente=indicios_indirectos (20 mods), `trazabilidad` ciclo 0, `rendimiento` operadores 0, `python_compras_skill` priorizados 0, `consultar_saldo_cliente` cli FAR01361 facturas 0/saldo 0 + descuento_unico presente, `consultar_cliente` FAR01361 credito 77378.61 + 1 consulta logueada. Guards offline PASS en 5 tools; lógica pura AD10/AD3 PASS.

**Verificación:**
- `php -l` OK (C:\tools\php\php.exe) en los 10 tools + Envelope + arnés.
- `bin/test_v4_17_adaptadores.php` → 22/22 PASS.
- Regresión `bin/test_notas_tiempo_real_tools.php` → 11/15 PASS (mismos 4 FAIL preexistentes de drift, NO causados por v4.17: notas-8/9 `GestionSuperEsteroideSearchTool::derivarUbicacion/derivarValidacion` métodos que nunca existieron en el archivo actual; notas-10/12 exigen `items!==[]`/`encontrada===true` para nota 72000051 que no existe en el ERP — degradación honesta, no regresión de código). Monitor (`fuente`/`eventos`) y detector/flujo/rendimiento siguen PASS.
- **0 procesos php.exe vivos tras las pruebas.**

**Pendientes / riesgos:**
- `sp_IA_Obtener_Metricas` sigue ausente en PRUEB25 (TecnologiaAdapter FAIL pre-existente, circuit breaker lo apaga).
- Proceso externo concurrente (sync GHA) sigue editando archivos: re-verificar con rg/php -l antes de commit.
- Lista de campos Profit utilizados y script curl de verificación documentados en `FASE417_ADAPTADORES.md`.

### v4.18 — Paquete GB10 (corregido) + skill caveman + fix vigilar_datos.py + auditoría anti-zombi (2026-08-11)

**Objetivo (directiva del usuario):** dejar todo listo en el repo para que, cuando llegue la estación NVIDIA GB10 (comprada, aún en tránsito, para IA local), sea "solo montar, instalar y correr". El mandato original (`PROMPT_CLAUDE_CODE_GB10.md`, auditado por otra IA) asumía una arquitectura de 3 capas MySQL→MySQL→SQLite con `PRUEB25` como MySQL en `192.168.4.148:3306` — **incorrecto**: se corrigió contra la arquitectura real ya vigente (v4.15-v4.17).

**Corrección de arquitectura (`gb10/README.md`, NUEVO):**
- CAPA 1 (remota, solo lectura): `PRUEB25` es SQL Server (`192.168.4.20:1433`, user `profit`, espejo de pruebas de `CRISTM25`) + MySQL `barquisimeto` (`192.168.4.148:3306`, legacy `rep_not`/`gestion`/`asignaciones_preparacion`) — dos motores distintos, no uno.
- CAPA 2 (mirror local en GB10): SQL Server local (mirror de tablas PRUEB25) + MySQL local (mirror de barquisimeto), mismos nombres de tabla/columna para que `ConnectionWrapper` solo cambie de host por env.
- CAPA 3 (`ara_llm.db` SQLite): sin cambios respecto al mandato original, eso sí estaba correcto.
- Paquete `gb10/` creado (nada ejecutado contra BD real, solo staging): `.env.gb10.example` (plantilla separada, no toca el `.env` de producción), `requirements_gb10.txt` (pymysql, python-dotenv), `init_ara_llm.py`, `mirror_sqlserver_pruebas25.py` y `mirror_mysql_barquisimeto.py` (mismo patrón de conexión corta + NOLOCK + reintentos que `vigilar_datos.py`), `sync_cron.py`, `validate_3capas.py`.

**Tablas SQL Server (`PRUEB25`) verificadas en vivo, no adivinadas:** `bin/listar_tablas_pruebas25.php` (NUEVO, solo lectura, `INFORMATION_SCHEMA.TABLES` vía `ConnectionWrapper::querySafe`) listó las 231 tablas reales. Cruzado contra lo que los 25 tools de NvidiaBrain consultan hoy (comentarios de código + `resolverTabla()`), la lista confirmada para espejar es: `art, clientes, factura, st_almac, not_ent, reng_nde, reng_fac`. MySQL `barquisimeto`: `rep_not, gestion, asignaciones_preparacion` (ya confirmado desde v4.5). Candidatos de discovery que nunca calzan contra PRUEB25 real (fallback inofensivo en `resolverTabla()`, no rompen nada): `InventarioDrogueria`, `ClientesDrogueria`, `saCliente`, `saStock`, `existencia`, `articulos`.

**Bug encontrado (no corregido, fuera del mandato pedido — solo reportado):** `app/Services/NvidiaBrain/Tools/Recepcion/ConciliarFacturaTool.php` tiene `const TABLA = 'FacturasDrogueria'` hardcodeado (sin lista de candidatos como los demás tools) — esa tabla **no existe** en PRUEB25 real. La tabla real es `factura`. Esto rompe el tool contra PRUEB25 en producción hoy, independiente de la GB10.

**Fix aplicado — `ara/ARA_Brain/vigilar_datos.py`:** el daemon caía por default a `PROFIT_DB_NAME=CRISTM25` (la migración v4.15 solo había tocado el stack PHP, no este daemon Python). Corregido: ahora prioriza `PROFIT_SQL_*` sobre `PROFIT_DB_*` igual que `ConnectionWrapper.php`, default `PRUEB25`.

**Auditoría anti-zombi de conexiones PHP (a pedido del usuario, "no estar loopiando como un fantasma en el servidor .148"):** 19 archivos con conexión DB directa (`new PDO(`/`mysqli_connect(`/`odbc_connect(`) fuera de `ConnectionWrapper` revisados. Resultado: **0** `ATTR_PERSISTENT=true`, **0** `allow_persistent=On`, **0** `mysqli_pconnect`; 2 archivos con `while(true)` (`PythonSkillExecutor.php`, `HermesChatTool.php`) ambos con guard `$tope`/timeout que rompe el loop, sin riesgo de fantasma. 17/19 archivos siguen el patrón anti-zombi completo (`null`/`close()`/`finally`/`gc_collect_cycles`); 2 sin cierre explícito (`app/Core/conectar_profit_read.php`, `ConciliarFacturaTool.php`) — riesgo bajo (PDO no persistente, variable local, PHP la libera sola al terminar el request) pero rompen la consistencia del patrón que sí siguen `chequeo/registro.php`, `legacy_visor/*` y `bin/actualizar_nota_hotfix.php`.

**Skill instalado:** `caveman` (`.claude/skills/caveman/SKILL.md`) — modo de comunicación comprimido, repo `JuliusBrussee/caveman` verificado (MIT, legítimo) antes de instalar; se copió solo el `SKILL.md` (sin ejecutar el instalador Node que trae proxy/engine/binarios no solicitados).

**Verificación en vivo de `barquisimeto` (MySQL), solo lectura:** `INFORMATION_SCHEMA.TABLES` confirmó 14 tablas reales: `rep_not`, `gestion`, `asignaciones_preparacion`, `app_log_notas`, `app_log_eliminaciones`, `app_log_impresiones`, `app_log_consultas`, `usuarios`, `cajas_embalaje`, `devoluciones`, `disponibilidad_trabajadores`, `puntajes`, `recepciones`, `traslados`. Confirma que `usuarios` sí existe (aclara duda pendiente) y que `TABLAS_TRAZA` de `MonitorModificacionesEliminacionesTool` (`auditoria_notas`, `log_modificaciones`, etc.) no calza con ninguna — siempre cae a `indicios_indirectos`, coincide con lo ya visto en el arnés v4.17, no es bug nuevo. Tablas sin usar aún por ningún tool: `cajas_embalaje`, `devoluciones`, `disponibilidad_trabajadores`, `puntajes`, `recepciones`, `traslados` (candidatas para futuros adaptadores).

**Fixes aplicados la misma noche (aprobados por el usuario):**
- `ConciliarFacturaTool.php`: `const TABLA = 'FacturasDrogueria'` (inexistente) → `const TABLAS = ['FacturasDrogueria', 'factura']` resuelto vía `ConnectionWrapper::resolverTabla()`, igual que el resto de los tools; toda referencia a la tabla fija se cambió a la variable resuelta `$tabla`. Transacción de escritura envuelta en `finally { $pdo = null; gc_collect_cycles(); }` (candado anti-zombi). Verificado en vivo con `bin/ejecutar_tool_cli.php conciliar_factura`: ya resuelve `factura` correctamente y avanza a `resolverColumnas()` (encuentra `fact_num`/`status`/`num_control`; **columna de monto aún sin candidato que calce** — pendiente real para cuando se trabaje ese adaptador, no bug de esta corrección).
- `conectar_profit_read.php` no tiene estado propio (factory sin conexión persistente); se añadió `finally { $pdo = null; gc_collect_cycles(); }` en sus 2 llamadores reales: `public/api/metricas.php` y `WhatsAppWebhookController::procesarMensaje()` (este último solo cierra si la conexión la abrió el propio método — respeta la inyectada por constructor, que maneja su dueño).
- Regresión: `bin/test_v4_17_adaptadores.php` → **22/22 PASS** tras los 3 cambios, sin romper nada. `php -l` limpio en los 3 archivos.

**Pendientes para la próxima sesión (según el usuario):**
- Crear más adaptadores y su lógica de negocio por departamento.
- Completar `MIRROR_TABLES_SQLSERVER` en `gb10/.env.gb10.example` a medida que se creen más adaptadores (cada tool nuevo puede sumar tablas PRUEB25 no listadas aún).
- Nada de lo anterior se ejecutó contra bases de datos reales de forma destructiva; `gb10/` queda en staging hasta que llegue el hardware.

### v4.19 — `sp_IA_Obtener_Metricas` localizado en CRISTM25 + candidatos reales de columna en `factura` (2026-08-12)

**Objetivo (a pedido del usuario):** confirmar si `sp_IA_Obtener_Metricas` vive en CRISTM25 (no en PRUEB25) y encontrar el candidato de columna correcto para el monto y la referencia bancaria de `ConciliarFacturaTool`, con consulta segura solo lectura y candado anti-zombi.

**PRUEB25 inaccesible durante esta verificación:** en mitad de la sesión, `ConnectionWrapper` empezó a fallar con `Cannot open database "PRUEB25" requested by the login. The login failed.` — de forma consistente en varios reintentos espaciados, no fue un timeout puntual. Mismas credenciales (`profit`/mismo host `.20`) sí conectan sin problema a CRISTM25, así que no es un problema de red ni de usuario: es la base `PRUEB25` puntualmente inaccesible (posible restore/mantenimiento del lado del DBA). No se insistió con reintentos en loop (candado anti-zombi). **Pendiente: reconfirmar contra PRUEB25 en cuanto vuelva a responder.**

**`sp_IA_Obtener_Metricas`:** diagnóstico manual solo lectura, SIN pasar por `ConnectionWrapper` (que bloquea CRISTM25 a propósito para evitar escrituras accidentales de la app en producción) — script puntual con guard local anti-escritura (regex bloquea INSERT/UPDATE/DELETE/DROP/ALTER/EXEC/TRUNCATE/MERGE), `PDO::ATTR_TIMEOUT=8`, conexión no persistente, `finally { $pdo = null; gc_collect_cycles(); }`. Resultado: **`sp_IA_Obtener_Metricas` SÍ existe en CRISTM25** (`INFORMATION_SCHEMA.ROUTINES`, `ROUTINE_TYPE=PROCEDURE`). Nunca se replicó al mirror de pruebas PRUEB25 — de ahí el FAIL persistente de `TecnologiaAdapter`. Decisión de negocio pendiente: crear el SP también en PRUEB25, o ajustar `TecnologiaAdapter` para no depender de él ahí.

**Columnas reales de `factura` (80 columnas, listadas en CRISTM25 como referencia porque PRUEB25 estaba caído — mismo esquema al ser espejo):**
- **Monto:** el candidato real es `tot_neto` (decimal). El código tenía `total_neto` (guión bajo mal puesto) — nunca calzaba. Agregado como primer candidato: `['tot_neto', 'total_neto', 'monto', 'total', 'importe', 'monto_total']`.
- **Referencia bancaria — hallazgo de integridad de datos:** ninguno de `referencia/referencia_bancaria/ref_bancaria` existe, así que el código caía en `num_control` (int) — que es el **número de control FISCAL real de la factura**, un campo de negocio del ERP, no un campo libre. Conciliar una factura habría sobrescrito el número de control fiscal con la referencia bancaria, corrompiendo un dato contable real. Se sacó `num_control` de los candidatos; el candidato seguro es `campo1` (varchar, campo genérico sin uso fiscal — mismo patrón que `campo7` en `art` para ubicación). Candidatos ahora: `['referencia', 'referencia_bancaria', 'ref_bancaria', 'campo1']`.
- `estado` sigue resolviendo a `status` (correcto, es literalmente el campo de estado del ERP). `fecha` sigue sin candidato real (`fecha_conciliacion` y similares no existen) — degrada a no guardar fecha, sin riesgo de corromper nada, se deja así.

**Cambio aplicado:** `app/Services/NvidiaBrain/Tools/Recepcion/ConciliarFacturaTool.php`, `const CAMPOS`. `php -l` limpio.

**Regresión:** `bin/test_v4_17_adaptadores.php` → 21/22 PASS (1 FAIL: `v417-22 consultar_cliente`, causado por el mismo corte de PRUEB25 de arriba, no por este cambio — varios otros casos degradaron igual por la misma causa). Sin PRUEB25 disponible no se pudo probar en vivo el UPDATE de conciliación con las columnas nuevas — **pendiente probar en cuanto PRUEB25 vuelva**.

**Pendientes:**
- Reconfirmar `tot_neto`/`campo1` contra PRUEB25 real (se resolvieron contra CRISTM25 por la caída puntual) antes de dar el fix por cerrado del todo.
- Decidir si `sp_IA_Obtener_Metricas` se crea también en PRUEB25.
- ~~Investigar con el DBA por qué PRUEB25 rechazó el login~~ — **causa raíz encontrada, ver v4.22.**

### v4.22 — Causa raíz confirmada de la caída de PRUEB25: no es lock ni proceso zombi, la base no existe (2026-08-12)

**Objetivo (a pedido del usuario):** identificar el proceso/consulta "culpable" que tenía bloqueada PRUEB25, asumiendo un lock o loop fantasma del lado de la app.

**Diagnóstico (`bin/diagnostico_bloqueo_pruebas25.php`, NUEVO, solo lectura, mismo candado anti-zombi manual que v4.19):** como PRUEB25 rechaza el login directamente, no se puede consultar "desde adentro". Las DMV de SQL Server (`sys.dm_exec_sessions`, `sys.dm_tran_locks`, `sys.dm_exec_requests`) son de **alcance servidor completo**, así que se consultaron conectado a CRISTM25 (mismo servidor `.20`, sí responde), filtrando por `DB_ID('PRUEB25')`.

**Resultado — descartado lock/zombi de la app:**
- 0 sesiones, 0 locks, 0 requests activos contra PRUEB25 en el servidor. Nada de nuestro lado la tiene tomada.
- **`PRUEB25` no aparece en `sys.databases` del servidor.** El error "Cannot open database" no era de permisos ni de lock — la base literalmente no existe en el servidor en este momento.

**Causa raíz probable:** `CRISTM25` figura creada/restaurada **hoy a las 00:45** (`sys.databases.create_date`), y aparecieron bases nuevas ajenas al proyecto ARA en el mismo servidor (`MasterProfit`, `MOTOS`, `VEHICULO`, `demo`, `DWConfiguration/DWDiagnostics/DWQueue` — estas 3 últimas son metadata de SQL Server Utility Control Point), todas creadas en las últimas ~13h. Encaja con el propósito ya documentado de `PRUEB25` ("espejo de pruebas, copia de CRISTM25", ver `gb10/README.md`): lo más probable es un job de refresh/restore de PRUEB25 desde CRISTM25 (drop + restore) en curso o caído a mitad — no un incidente de la aplicación.

**Se descartó que fuera servidor equivocado:** `@@SERVERNAME`/`MachineName` = `PROFITSERVER` (el de siempre), y `CRISTM25` tiene datos reales e íntegros (626.564 facturas, 12.159 artículos, 4.246 clientes, 634.469 notas de entrega) — no es una base vacía ni de otro entorno.

**Acción recomendada:** escalar al DBA para confirmar si el refresh de PRUEB25 sigue en curso o se cayó a mitad; no hay nada que "liberar" del lado de la aplicación (0 sesiones que matar). `bin/diagnostico_bloqueo_pruebas25.php` queda como herramienta reusable para la próxima vez que se repita.

### v4.20 — Auditoría de conexiones de los 27 adaptadores + hotfix de pooling propagado (2026-08-12)

**Objetivo (a pedido del usuario):** revisar los adaptadores creados y diseñar una tarjeta que confirme, por cada uno, que la conexión está verificada y que ninguno queda en loop ni en modo fantasma (zombi) en el servidor.

**Hallazgo real, mismo patrón del bug histórico "SPID dormido de KICKSERVER":** el HOTFIX v4.17.1 endureció `ConnectionWrapper::conectarProfit()` con `ConnectionPooling=0` (DSN sqlsrv) y `PDO::ATTR_PERSISTENT => false` explícito — pero ese hotfix **nunca se propagó** a los 6 conectores PDO directos del repo (los que no pasan por `ConnectionWrapper`):
- SQL Server: `ConciliarFacturaTool::conectar()`, `app/Core/conectar_profit_read.php`.
- MySQL (`conectarMySQL()`, método duplicado idéntico en 4 archivos): `BultoCerradoRouterTool`, `StockSurtidoPrioritarioTool`, `DiscrepanciaTrasladosTool`, `ReporteQuiebresComprasTool`.

Sin `ConnectionPooling=0`/`ATTR_PERSISTENT=false` explícito, el driver ODBC/sqlsrv puede dejar el SPID pooled en el servidor aunque PHP ya haya destruido el objeto PDO (el riesgo real de "modo fantasma" no es el modelo de objetos de PHP — eso se limpia solo — es el pooling a nivel de driver). **Los 6 archivos corregidos** con el mismo patrón que `ConnectionWrapper`. `php -l` limpio en los 6; regresión `bin/test_v4_17_adaptadores.php` → 21/22 PASS (mismo FAIL preexistente por el corte de PRUEB25, sin relación con este cambio).

**Tarjeta de verificación (`bin/auditar_adaptadores.php`, NUEVO):** script reusable de análisis estático (no abre ninguna conexión real) que clasifica los 27 adaptadores por motor (`ConnectionWrapper` / PDO SQL Server directo / PDO MySQL directo / Python subprocess / sin conexión), si el pooling quedó desactivado, cómo cierra la conexión, y si tiene loops sin guard de timeout. Emite una tarjeta `CardBuilder` (respeta el límite de 20 líneas/80 columnas del estándar de tarjetas del chat) + modo `--json` con el detalle completo. **Resultado tras el fix: 27/27 adaptadores, 0 con pooling inseguro, 0 con loop sin guard, 15 usando `ConnectionWrapper` (cierre automático por destructor).**

**Contexto adicional (no relacionado, visto de paso):** `ara/ARA_Brain/data/watchdog_zombis.log` revisado — daemon anti-zombi corriendo cada 5 min desde el 10/08, **0 zombis en 138 registros**, incluye el corte de PRUEB25 de hoy sin generar ninguno. Confirma que el problema de PRUEB25 es de la BD, no de procesos PHP colgados localmente.

### v4.21 — Staging del entorno GDX: motor de inferencia, hardware, auth, Postgres (2026-08-12)

**Objetivo (a pedido del usuario):** dejar preparado, sin activar nada, el terreno para cuando llegue la estación GDX (NVIDIA): swap de Ollama a vLLM/Triton, detección de hardware para reemplazar el hardcoding actual de recursos limitados, auth ligera para endpoints internos, y considerar SQLite→PostgreSQL. Todo en `gb10/`, nada importado por código activo, nada tocado en Ollama hoy. Se usó el venv que ya existía (`ara/venv`), no se creó uno nuevo.

- **Motor actual confirmado:** Ollama, `http://localhost:11434`, consumido desde `NvidiaBrainClient.php` (PHP) y `chat_routes.py`/`ara_vision.py` (Python).
- **`gb10/inference/engine_config.py` + `README.md`:** abstracción Ollama/vLLM/Triton resuelta por env `INFERENCE_ENGINE` (default `ollama`, sin cambio de comportamiento). Los 3 motores se asumen compatibles OpenAI Chat Completions (`/v1/chat/completions`) para que el swap futuro sea cambiar env, no reescribir el cliente — pendiente confirmar si `NvidiaBrainClient.php` ya usa ese endpoint compatible o el nativo `/api/chat` de Ollama antes de migrar de verdad.
- **`gb10/hardware_detect.py`:** detecta GPU NVIDIA vía `nvidia-smi` (sin dependencias nuevas). Reemplazará el hardcoding de recursos limitados en `ara_server.py` (ThreadPoolExecutor, límites de conexión) cuando llegue la GDX — no tocado.
- **`gb10/auth/token_auth.py`:** bearer estático (default, cero dependencias) o JWT opcional (`PyJWT`, agregado comentado a `requirements_gb10.txt`) para endpoints internos servicio-a-servicio de la GDX. No reemplaza `CustomerAuthenticator.php` (autenticación de clientes finales, dominio distinto).
- **`gb10/POSTGRES_MIGRACION.md`:** documento de consideración, no decisión. Recomendación: no migrar `ara_llm.db` antes de tener carga real en la GDX — migrar sin evidencia de `database is locked` resuelve un problema hipotético.
- **`.env.gb10.example`** ampliado con las variables nuevas (`INFERENCE_ENGINE`, `VLLM_*`, `TRITON_*`, `GDX_AUTH_MODE`, `GDX_AUTH_TOKEN`, `GDX_JWT_SECRET`, `PG_*`), todas comentadas salvo los defaults seguros.
- **Verificación:** `py_compile` limpio en los 3 `.py` nuevos vía `ara/venv/Scripts/python.exe`; `hardware_detect.py` corrido en esta máquina de desarrollo → reporta correctamente "sin GPU" (8 cores CPU), confirma que el detector funciona sin falsos positivos antes de tener hardware real.

**Pendientes:**
- Confirmar si `NvidiaBrainClient.php`/`chat_routes.py` ya hablan el protocolo OpenAI-compatible antes de migrar el motor.
- Nada de esto se activa hasta tener la GDX físicamente y decidir vLLM vs Triton con benchmarks reales.

### v4.23 — Bug real: el autochequeo nativo (MySQL directo) nunca actualizaba `gestion` (2026-08-12)

**Objetivo (a pedido del usuario, con evidencia real — captura del visor + log de consola):** la nota 72163346 quedó marcada como CHEQUEADA/VERIFICADA en el log de consola, pero el visor (`legacy_visor/registro.php`) seguía mostrando "Registro de Chequeo" vacío, "Numero de Chequeador"=0 y "Hora del Registro" (chequeo)=`0000-00-00 00:00:00`.

**Causa raíz:** `registrar_autochequeo_php()` (`preparacion/infrastructure/adapters/legacy_http_adapter.py:614`) tiene una vía rápida "MySQL directo" (`LegacyMySQLConnector.confirmar_chequeo()`, v3.30) que, si tiene éxito, **corta el flujo y nunca llega al HTTP real** (`chequeo/registro.php`, líneas 674-685). El problema: `confirmar_chequeo()` hasta ahora **solo actualizaba `rep_not.estatus`** (y encima con el valor `'CHEQUEADA'`, que es el minoritario — el propio `chequeo/registro.php` documenta que 731/732 filas reales usan `'CHEQUEO'`) e intentaba una bitácora (`log_chequeo`/`chequeos`) que **no existe** en el esquema real de `barquisimeto` (confirmado en v4.20: 14 tablas reales, ninguna de esas) — de ahí el "(sin bitácora)" en el log. **Nunca tocaba `gestion`**, que es la tabla que el visor realmente lee para el bloque de chequeo (`verifi_cheq`/`num_cheq`/`hora2`/`numeroMesa`, documentado desde v4.5). Como la vía "MySQL directo" reportaba éxito, el flujo se cortaba ahí y jamás llegaba al HTTP que sí escribe `gestion`.

**Fix — `preparacion/infrastructure/adapters/legacy_mysql_sync.py`, `LegacyMySQLConnector.confirmar_chequeo()` reescrito completo:** replica ahora, en una sola transacción, exactamente lo que hace `chequeo/registro.php::registrar_estado_legacy()`:
1. `UPDATE rep_not SET estatus = 'CHEQUEO'` (valor corregido, ya no `'CHEQUEADA'`).
2. Resuelve el `responsable` (puede llegar `'03'`) contra `usuarios` (`numero`/`id`/`nombre`) para obtener el `numero` canónico sin ceros a la izquierda — nuevo método `_resolver_usuario_numero()` — sin esto `gestion.num_cheq` no enlaza con la cuenta de puntos de la web legacy.
3. `UPDATE` (o `INSERT` si la fila no existe todavía) del bloque de chequeo en `gestion`: `verifi_cheq='VERIFICADA'`, `num_cheq`, `tip_pre='CHEQUEADOR'`, `ubicacion2='CHEQUEO'`, `hora2`, `numeroMesa`, `cant_items` — nuevo método `_actualizar_gestion_chequeo()`, con el mismo criterio defensivo del PHP (columnas por candidatos vía `INFORMATION_SCHEMA`, defaults NOT NULL en el INSERT, reintento en carrera de duplicado).
- `legacy_http_adapter.py::registrar_autochequeo_php()` actualizado para pasar `mesa`/`hora`/`total_items` (antes hardcodeado `total_items=0`) al conector.
- `bin/test_legacy_mysql_sync.py` actualizado (imprimía `tabla_log`, ya no existe en la respuesta; ahora `tabla_gestion`).

**Nota aparte, no relacionada con el bug de `gestion` (v3.19, ya documentada en el código):** el log del usuario también mostraba "Renglones sincronizados parcialmente... 2 pendientes" — eso es `ProfitRenglonesSync` (SQL Server, `reng_nde`) funcionando **como está diseñado**: cuando llegan cantidades reales escaneadas por ítem, refleja el progreso real en vez de marcar todo "completo" a la fuerza (evita falsos positivos). No es parte de este bug ni se tocó.

**Verificación:** `py_compile` limpio; `bin/test_legacy_mysql_sync.py` en vivo (dry-run, sin escritura) contra la MySQL real de `barquisimeto` → `tabla_nota=rep_not`, **`tabla_gestion=gestion`** resuelta correctamente, exit 0. **No se ejecutó con `--confirmar`** (eso escribiría en producción) — queda pendiente que el usuario lo valide con una nota real en el flujo normal de la app antes de darlo por cerrado del todo.

**Pendiente:**
- ~~Validar en producción con una nota real~~ — **validado, ver v4.24.**
- `registrar_chequeo_legacy()` (chequeo MANUAL, ==3 ítems) usa un camino distinto (`confirmar_despacho` + HTTP) — no se tocó, no reportado como afectado por el usuario, pero queda como candidato a revisar si aparece el mismo síntoma ahí.

### v4.24 — Fix v4.23 validado en producción + bug nuevo: chequeador resuelto a la persona equivocada (2026-08-12)

**Validación del fix v4.23 (reinicio de servidor + nota real del usuario):** nota 72163448, autochequeo >3 ítems. El visor ya muestra "Registro de Chequeo"=VERIFICADA, "Numero de Chequeador"=8, "Hora del Registro" (chequeo)=2026-08-12 10:31:15 — `gestion` se está escribiendo correctamente. El fix v4.23 queda confirmado en producción.

**Bug nuevo reportado por el usuario:** en la regla de autochequeo >3 ítems (mismo operador prepara y "chequea" automático, sin mesa real), el chequeador registrado debe ser la MISMA persona que el preparador — pero el visor mostraba "Numero de Preparador"=9 y "Numero de Chequeador"=8, personas distintas.

**Causa raíz (confirmada en vivo, solo lectura, `gestion` + `usuarios` de `barquisimeto`):** `numero` e `id` son columnas INDEPENDIENTES en `usuarios` — `numero='9'` es MIGUEL CAMPOS (`id=10`), pero `id=9` es PEDRO VIZCAYA (`numero='8'`). El identificador que llega a `_resolver_usuario_numero()`/`resolver_usuario_numero()` (el mismo `preparador_id` con el que ya se escribió `gestion.num_prep=9`) se resolvía con `WHERE numero=? OR id=? OR nombre=? LIMIT 1` **sin `ORDER BY`**: con dos usuarios reales distintos calzando por columnas distintas (MIGUEL por `numero`, PEDRO por `id`), MySQL devolvía cualquiera de los dos según el orden interno de acceso — en este caso PEDRO (`id=9`), no MIGUEL (`numero=9`, el preparador real).

**Fix — mismo patrón en los dos lugares que implementan esta resolución:**
- `preparacion/infrastructure/adapters/legacy_mysql_sync.py::_resolver_usuario_numero()` (la vía nativa, la que corrió esta vez).
- `chequeo/registro.php::resolver_usuario_numero()` (la vía HTTP de respaldo — mismo bug exacto, corregido por consistencia aunque no fue la que se ejecutó).

En ambos: primero un match EXACTO por `numero` (la columna con la que el resto del flujo ya trabaja — `num_prep` se escribe por `numero`, no por `id`); solo si no hay ningún usuario con ese `numero` se cae al fallback por `id`/`nombre`. Elimina la ambigüedad de raíz en vez de parchear el síntoma.

**Verificación:** `php -l` limpio en `chequeo/registro.php`; `py_compile` limpio en `legacy_mysql_sync.py`; `bin/test_legacy_mysql_sync.py` dry-run → exit 0, `tabla_gestion=gestion` resuelta. No se corrió `--confirmar` (escribiría en producción) — pendiente que el usuario confirme con la próxima nota real que preparador y chequeador ya coinciden en el autochequeo >3 ítems.

### v4.25 — Módulo de rutas: validador de sede, notas de crédito reales, rendimiento, bug de encoding en el auto-registro embalaje→ruta (2026-08-12)

**Objetivo (a pedido del usuario):** mejorar conexión/tiempos de respuesta del módulo de rutas (`rutas/`), filtrar por sede (BQTO/SC) antes de cargar la ruta, verificar el auto-registro embalaje→ruta con una mini prueba, y verificar notas de crédito contra la BD real (no por texto).

**Notas de crédito — tabla real confirmada en vivo:** `dev_cli` (SQL Server, PRUEB25/CRISTM25). Columnas: `fact_num` (número de la NC), `nc_num` (factura que afecta), `co_cli`, `descrip` (motivo), `co_tran` (código de transporte/ruta — mismo campo en `not_ent`/`factura`/`dev_cli`), `saldo`/`saldoNCR`. `dev_cli.nombre` viene vacío — el nombre real se resuelve con JOIN a `clientes.cli_des`. Antes el módulo solo "adivinaba" NOTA_CREDITO buscando `'NC-'` en el texto, sin confirmar contra la BD.

**Implementado:**
- `ProfitFacturador.verificar_nota_credito(numero)` — consulta `dev_cli` + JOIN `clientes`, retorna existencia real + cliente + descripción + `co_tran`. Probado en vivo: nota `72001032` → `LAS COSAS DEL MEDICO, C.A`, motivo real, `co_tran=03`. Nota inexistente → `existe:false` limpio.
- `RouteService.verificar_nota_credito()` + endpoint `GET /api/rutas/verificar_nota_credito?numero=...`.
- Formato de series A/B (SC/solo-factura): confirmado en vivo que el prefijo de letra **no vive en la columna SQL** `fact_num` (son números puros) — es convención del código de barras impreso, no de la BD.
- Rendimiento (alcance acordado con el usuario: solo optimizaciones seguras, sin paralelizar sesiones HTTP por el riesgo de que el visor legacy no soporte logins concurrentes): `LegacyRouteAdapter._fetch_pedidos_sub_ruta` ya no repite el POST de login si la sesión sigue autenticada en la misma sub-ruta (ahorra un round-trip completo en re-consultas); `HTTPAdapter` con pool/keep-alive explícito en la sesión HTTP.
- Validador de sede + mini prueba de cotejamiento embalaje→ruta: ver v4.26 (el primero se corrigió en caliente por un incidente real durante la prueba en producción).

### v4.26 — INCIDENTE EN VIVO: el filtro de sede bloqueaba notas reales de BQTO + bug de encoding descubierto por la mini prueba (2026-08-12)

**Contexto:** v4.25 implementó un validador de sede que filtraba los ítems de la ruta por umbral de número de nota (`> 5.000.000` → BQTO, `<= 5.000.000` → SC — mismo umbral que `AlmacenDbTrait::resolver_almacen_picking`, usado para picking/inventario en Almacén). Al probar en operación real, la ruta CARACAS con `sede=BQTO` reportó `RUTA_SIN_ITEMS_SEDE` **excluyendo 30 notas reales de BQTO** — el umbral de picking/inventario NO aplica igual al número que expone `lista.php` (columna "Nota" del visor de DESPACHO, HTML-scraped) como al `num_nota` de `not_ent`/SQL Server. Se asumió mal que era el mismo campo/convención.

**Corrección inmediata (para no bloquear despacho real):** el filtro de sede se desactivó como bloqueo — ya NO excluye ítems; solo imprime en el log cuántos ítems "calzarían" con el umbral, a título informativo, hasta calibrar correctamente el campo real de sede. `RouteService.iniciar_ruta()` sigue exigiendo `sede` (el validador en sí queda), pero no descarta nada por ahora.

**Candidato real para el filtro correcto (sin confirmar todavía):** `co_tran` (código de transporte), presente en `not_ent`/`factura`/`dev_cli` de Profit — mismo campo que ya se usa en `verificar_nota_credito`. Pendiente cruzar valores reales de `co_tran` con sede conocida (ej. confirmar si `co_tran='03'` visto en las pruebas de hoy corresponde a BQTO o a S/C) antes de reactivar cualquier filtro que descarte ítems.

**Validador de sede en el frontend (a pedido explícito del usuario, "agregamos después de pulsar iniciar ruta en qué sede va a iniciar"):** `templates/index.html::iniciarRutaHex()` — antes de llamar a `/api/rutas/iniciar`, se abre un diálogo SweetAlert2 (mismo patrón que el resto de la app) preguntando SC/BQTO, con la sede del perfil del usuario como sugerencia; el operador puede cambiarla por ruta. Se cancela el inicio si el operador no confirma.

**Bug de encoding real, encontrado por la mini prueba de cotejamiento (no simulado — surgió corriendo el flujo):** 3 `print()` con el carácter `→` en `route_service.py` (`vincular_nota_a_rutagrama_activa`, `on_embalaje_finalizado`, `despachar_factura_macro`) y 1 en `legacy_route_adapter.py` lanzaban `UnicodeEncodeError` en consola Windows cp1252 — el crash interrumpía la función **antes** de llegar a `guardar_nota_embalada()`/`guardar_macro_ruta()`: el ítem quedaba `'embalada'` en memoria pero el registro **nunca se guardaba en BD**. Mismo patrón de bug que ya se blindó en `vigilar_datos.py`/`legacy_mysql_sync.py`/`profit_renglones_sync.py`, nunca aplicado a este módulo. Corregido: helper `_print_seguro()` (mismo criterio try/reconfigure/ascii-replace) + los 4 prints cambiados a flecha ASCII `->`.

**Mini prueba de cotejamiento (fakes en memoria, sin tocar red ni BD real):** simula `iniciar_ruta` → evento `embalaje.finalizado` → verifica que el ítem pasa a `estado_ara='embalada'` con sus bultos reales SIN escaneo manual, que se persiste con `estado=EMBALADO_LISTO_PARA_DESPACHO`, y que `despachar_por_factura` reconoce la nota como ya embalada (1 solo escaneo, sin re-contar bultos). **Los 6 pasos pasan limpio** tras el fix de encoding (antes del fix, fallaba silenciosamente en el paso de persistencia).

**Pendiente:**
- ~~Calibrar el campo/umbral real de sede~~ — **resuelto, ver v4.27.**
- Confirmar con el usuario tras la prueba en operaciones real con la ruta pequeña.

### v4.27 — Umbral de sede calibrado con la fuente oficial de Profit: 72.000.000, no 5.000.000 (2026-08-12)

**Objetivo (a pedido del usuario: "CALIBRALO"):** encontrar el campo/umbral REAL que distingue notas BQTO de SC en el visor de despacho, tras el incidente de v4.26.

**Fuente encontrada (solo lectura, en vivo):** tabla `almacen` de Profit — es el control OFICIAL de numeración por serie del ERP (una fila por `co_alma`, con `num_fac_ini`/`num_fac_fin` y rangos por tipo de documento: `fact_f1..5`, `nde_f1..4`, `nc_f1..4`, `nd_f1..4`). No es una tabla de configuración lateral: es de donde Profit mismo saca los números al crear documentos.

```
co_alma='01' SAN CRISTOBAL:   num_fac_ini=0        num_fac_fin=71999999
co_alma='02' BARQUISIMETO:    num_fac_ini=72000000 num_fac_fin=99999999
```

**Confirma exactamente lo que el usuario dijo desde el mensaje original** ("las notas 72000000 son las de bqto") — el error fue mío en v4.25: reusé el umbral `5.000.000` de `AlmacenDbTrait::resolver_almacen_picking` (un propósito distinto, picking/inventario) en vez de verificar el umbral real de numeración de documentos. Ese fue precisamente el bug que causó el incidente de v4.26 (30 notas reales de BQTO excluidas).

**Corrección:** `RouteService.UMBRAL_SEDE_NOTA = 72_000_000` (antes `5_000_000`); comparación `BQTO: numero >= umbral`, `SC: numero < umbral` (antes `>`/`<=` con el umbral viejo). Filtro **reactivado como bloqueo** (ya no es solo informativo — la fuente ahora es autoritativa, no una suposición). Test unitario actualizado con casos reales vistos en vivo esta sesión (`72163501`, `72001032`, `10927`, bordes exactos `71999999`/`72000000`) — **10/10 PASS**.

**Pendiente:** confirmar con el usuario en la próxima prueba de operaciones que la ruta CARACAS con `sede=BQTO` ya trae las 30 notas reales.

### v4.28 — SEGUNDO incidente en vivo: el umbral 72.000.000 tampoco sirve para lista.php; el filtro numérico queda desactivado hasta aclarar la regla real (2026-08-12)

**Contexto:** al reintentar la ruta CARACAS con `sede=BQTO` tras v4.27, siguió el mismo error `RUTA_SIN_ITEMS_SEDE` (30 notas excluidas). El usuario pidió consultar `rep_not` directamente para entender por qué.

**Hallazgo (solo lectura, en vivo):** `rep_not` (MySQL `.148`/`barquisimeto`) es **100% BQTO** — 164.805 filas, `cod_nota` SIEMPRE 8 dígitos empezando en 72 (rango real `72000537`–`72163588`, sin una sola excepción). Se buscaron los 4 números exactos que la ruta CARACAS mostraba como "de BQTO" (`488711`, `401384`, `470001`, `489394`) — **ninguno existe en `rep_not`**. No es que el umbral estuviera mal calculado: esos números simplemente no son del mismo espacio de numeración que `rep_not`/Profit. El número que devuelve `lista.php` (HTML-scraped del visor) para esos ítems de CARACAS no tiene relación directa ni con el umbral de picking (5M) ni con el de `almacen` de Profit (72M).

**Hipótesis (sin confirmar, para discutir con el usuario):** "sede" en el módulo de DESPACHO puede no ser una propiedad de cada nota individual (como si lo es en Almacén/picking) — puede ser un dato del **turno/operador** (desde qué almacén físico sale el camión hoy), independiente del número de documento. Filtrar ítems de una ruta por número de nota sería, en ese caso, la premisa equivocada desde el principio para este módulo específico.

**Acción:** el filtro numérico se desactivó por segunda vez (`RouteService.iniciar_ruta`, ya no descarta ítems — solo imprime en log cuántos "calzarían" a título informativo). El validador de sede (el diálogo del frontend) se mantiene, pero por ahora es puramente informativo/de sesión, no filtra contenido.

**Pendiente (bloqueante para reactivar cualquier filtro):**
- Aclarar con el usuario qué significa realmente "sede" para el módulo de despacho: ¿es una propiedad de la nota (como en Almacén) o del turno/operador?
- Si es propiedad de la nota: identificar el campo/tabla real que la visor `lista.php` sí expone para cada ítem de "Notas por cargar" (no se ha encontrado ninguno confiable todavía — ni número de nota, ni `co_tran`, ni `almacen` de Profit calzan con lo que devuelve el HTML del visor de despacho).
- **NO reintentar un tercer umbral sin verificar contra números reales primero** — van dos incidentes en producción por la misma causa (adivinar antes de verificar).

### v4.29 — Resuelto: NO era bug, el filtro 72.000.000 estaba correcto (2026-08-12)

**Aclaración del usuario:** confirmó explícitamente que "sede" SÍ es una propiedad real de cada nota (no del turno/operador), y que las 30 notas de la ruta CARACAS que parecían "de BQTO" en el reclamo original **eran en realidad de SC** — esa ruta no tenía notas de BQTO en ese momento. El filtro de v4.27 (umbral `72.000.000`, fuente: tabla `almacen` de Profit) estaba correcto desde el principio; lo que el usuario interpretó como "las de BQTO no cargan" era en realidad el filtro funcionando bien sobre una ruta que legítimamente no tenía ítems de esa sede en ese momento.

**Confirmación final en vivo:** el usuario verificó que "todas las notas de Barquisimeto empiezan por 72" — coincide 100% con lo encontrado en `rep_not` (164.805 filas, sin una sola excepción fuera del rango `72xxxxxx`).

**Estado final:** `RouteService.iniciar_ruta()` — filtro de sede reactivado como bloqueo, `UMBRAL_SEDE_NOTA = 72_000_000`, `BQTO: numero >= umbral`, `SC: numero < umbral`. Mensaje de error cuando una ruta no tiene ítems de la sede seleccionada ahora aclara "en este momento" (es un estado válido — la ruta puede simplemente no tener notas de esa sede hoy, no es un error del sistema).

**Lección para futuras sesiones:** dos falsas alarmas (v4.26, v4.28) por interpretar "0 resultados tras filtrar" como bug del filtro en vez de como posible estado real de los datos — verificar primero si el dato de referencia del usuario es correcto antes de asumir que el código está mal.

### v4.30 — Bug real encontrado: el diálogo de sede no mandaba lo que el operador seleccionaba (2026-08-12)

**Reporte del usuario (con evidencia de Network del navegador):** al seleccionar "SC" en el diálogo de sede, la consulta a `/api/rutas/iniciar` salía con `sede=BQTO` — el filtro entonces rechazaba rutas reales de SC pensando que se pedía BQTO.

**Causa:** el diálogo (`templates/index.html::iniciarRutaHex()`, agregado en v4.26) usaba `Swal.fire({input:'radio', inputOptions:{BQTO,SC}, inputValue:'BQTO'})`. El input tipo `radio` de SweetAlert2 devolvía el `inputValue` preseleccionado en vez del que el operador clicaba — bug de serialización del componente, no del filtro de sede (que estaba funcionando correctamente todo este tiempo).

**Fix:** se reemplazó el input `radio` por 2 botones fijos (`confirmButtonText`/`denyButtonText`, `isConfirmed`/`isDenied`) — cada botón resuelve a un valor literal (`'BQTO'` o `'SC'`), sin depender de ningún serializado de input. Elimina la clase de bug por completo, no solo el síntoma.

**Retrospectiva de la sesión completa (v4.25-v4.30):** el umbral de sede (72.000.000, tabla `almacen` de Profit) fue correcto desde v4.27. Los 3 "incidentes" posteriores (v4.28 aparente, y este de v4.30) fueron: (1) un caso real de una ruta sin notas de esa sede — dato correcto, mal interpretado como bug — y (2) un bug real pero en la CAPA EQUIVOCADA (frontend, no el filtro backend). Lección: cuando el backend está verificado contra datos reales y el síntoma persiste, revisar la capa de transporte (qué llega realmente al backend) antes de re-litigar la lógica que ya se confirmó correcta.

**Pendiente:** confirmar con el usuario que, tras este fix, seleccionar SC/BQTO en el diálogo carga la ruta correcta.

### v4.31 — Cierre de la saga de sede: el filtro por número de nota nunca fue el mecanismo correcto (evidencia del sistema legacy real) (2026-08-12)

**Evidencia decisiva aportada por el usuario:** dos archivos reales del sistema legacy (`legacy_visor/RUTA_VISOR/index.php` y `lista.php`, un sistema PHP separado del `192.168.4.148:8000/visor/` que usa `LegacyRouteAdapter`, titulado "Carga de rutas S/C"). Confirman dos cosas:
1. **Una ruta de reparto normal mezcla notas BQTO y SC** — el `lista.php` real mostró una ruta con 3 notas `72xxxxxx` (BQTO) y 1 nota `471093` (SC) en la MISMA tabla, sin separar. Origen del pedido (qué almacén lo registró) y zona de entrega (a qué ciudad va) son independientes: cualquier almacén puede despachar hacia cualquier zona.
2. **Existen 2 rutas ESPECIALES de transferencia entre almacenes**, distintas de las rutas de reparto normales: `<option value="barquisimeto1/BQTO-SC">ENVIOS BARQUISIMETO</option>` y `<option value="barquisimeto2/SC-BQTO">ENVIOS S/C</option>` — mueven mercancía entre almacenes, no reparten a clientes.

**Conclusión:** el filtro de notas por número/sede (v4.25-v4.29) estaba conceptualmente equivocado desde el planteamiento para rutas de reparto normales — no era un problema de calibración de umbral (72.000.000 SÍ era el umbral numérico correcto para distinguir origen BQTO/SC de una nota individual, confirmado en v4.27/v4.29), sino que **rutas normales no deben filtrarse por ese origen en absoluto**.

**Cambios (a pedido explícito del usuario, las 2 opciones):**
1. **`RouteService.iniciar_ruta()`**: se quitó el filtro/descarte de ítems por sede. `sede` se conserva como dato de la sesión (queda en la respuesta), ya no excluye notas. El umbral `UMBRAL_SEDE_NOTA=72_000_000` y `_item_pertenece_a_sede()` se conservan sin cambios (siguen siendo correctos como clasificador informativo, por ejemplo para reportes), solo se dejó de usar para bloquear.
2. **Rutas especiales "ENVÍOS ENTRE ALMACENES" agregadas al catálogo:**
   - `catalogo_rutas.py`: nueva regla de clasificación `("ENVIOS",) → "ENVÍOS ENTRE ALMACENES"`, evaluada PRIMERO (antes de la regla `"BARQUISIMETO"`) para que "ENVIOS BARQUISIMETO" no se mezcle con las rutas geográficas de Barquisimeto.
   - `legacy_route_adapter.py`: `MAPA_MACRO_SUBRUTAS["ENVÍOS ENTRE ALMACENES"] = ["ENVIOS BARQUISIMETO", "ENVIOS S/C"]` (catálogo estático de respaldo); `RUTAS_ESPECIALES_RAW` con los `value=` exactos (`barquisimeto1/BQTO-SC` / `barquisimeto2/SC-BQTO`) sembrados en `_raw_por_sub_ruta` desde `__init__` y preservados con merge (no reemplazo) tras cada fetch dinámico del catálogo — así el login al visor (`iniciar_guia_legacy`) siempre resuelve el value correcto, con o sin conexión al `index.php` real.

**Verificación:** `py_compile` limpio en los 3 archivos; test nuevo (`test_rutas_especiales.py`, 4 casos) confirma: catálogo estático incluye la macro nueva, clasificación dinámica separa "ENVIOS BARQUISIMETO" de "BARQUISIMETO", el adapter sin fetch dinámico ya resuelve los raw values, y `_opciones_consulta` entrega el value correcto para el login. Mini prueba de cotejamiento (v4.25) re-verificada con la nueva expectativa (sede ya no descarta notas) — 6/6 pasos OK.

**Retrospectiva completa de la saga (v4.25-v4.31):** 3 rondas de "incidente en producción" antes de llegar a la causa raíz real. Lección más importante de toda la sesión: cuando el usuario reporta un síntoma que contradice una premisa ya verificada con datos reales, la pregunta correcta no es "¿qué umbral ajusto?" sino "¿es correcta la premisa completa?" — pedir evidencia directa del sistema real (como estos 2 archivos PHP) resolvió en un intercambio lo que 3 rondas de recalibración numérica no pudieron.

### v4.32 — Arquitectura final aclarada: el visor legacy es nativamente S/C; BQTO ya tiene su propio sistema (Macro-Rutas Multi-Sede) construido y conectado (2026-08-12)

**Verificación en vivo (GET real a `http://192.168.4.148:8000/visor/index.php`, solo lectura):** confirma que es el MISMO sistema que `legacy_visor/RUTA_VISOR` — título real de la página: **"Carga de rutas S/C"**. No existe un segundo servidor/IP para BQTO en ningún lugar del repo (se revisaron todas las IPs `192.168.4.*` referenciadas: solo `.148` MySQL/visor y `.20` SQL Server Profit). Las 2 rutas especiales de transferencia (`ENVIOS BARQUISIMETO`/`ENVIOS S/C`, ver v4.31) se confirmaron presentes en el `<select>` real en vivo.

**Conclusión (confirmada con el usuario):** la lista "Por Escanear" del módulo REALIZAR RUTA viene 100% del scrape de este visor legacy, que es nativo de S/C — estructuralmente **nunca** va a reflejar bien el pendiente real de BQTO, no es un bug corregible ahí. Sin embargo, el frontend YA tiene conectado un sistema paralelo diseñado específicamente para ambas sedes: las **Macro-Rutas Multi-Sede** (`RouteService.get_or_create_macro_ruta_activa`/`despachar_factura_macro`/`cerrar_macro_ruta`, alimentadas por el evento `embalaje.finalizado`, sin depender del PHP legacy) — visible en la misma pantalla como el chip "🧭 Macro-Ruta" y ya priorizado en el escaneo de factura (`_rutaHexEscanear` intenta la Macro-Ruta primero, cae al legacy 1-1 solo si no la encuentra ahí). Ya validado end-to-end por la mini prueba de cotejamiento de v4.25 (6/6 pasos).

**Cambio aplicado:** nota informativa agregada en `templates/index.html` (columna "Por Escanear") aclarando que esa lista es del sistema legacy S/C y que las notas BQTO embaladas se despachan por la Macro-Ruta, no por ahí — evita que se siga interpretando como un bug cada vez que una ruta no muestra pendientes BQTO.

**Pendiente:** el usuario eligió seguir usando el sistema Macro-Rutas Multi-Sede ya construido (no reconstruir nada nuevo). Falta validar en operación real que el flujo completo (embalar nota BQTO → ver el chip Macro-Ruta subir → escanear factura → despacho directo) funciona igual de bien que en la mini prueba simulada.

### v4.33 — El campo real de sede: `co_sucu` (encontrado por el usuario), confirmado en vivo (2026-08-12)

**Hallazgo del usuario:** la columna real y directa para sede es `co_sucu` (código de sucursal) — presente en `not_ent`, `factura` y `dev_cli` (la vimos pasar en varias consultas de esta sesión sin notar su importancia). `01` = San Cristóbal, `02` = Barquisimeto. A diferencia del umbral por número de nota (72.000.000, útil pero indirecto) o de `co_alma`/`co_sucu` de la tabla `almacen` (que describe rangos de numeración, no el dato de cada fila), `co_sucu` es el campo **directo, por documento**, exactamente lo que se necesitaba desde el principio.

**Verificado en vivo (solo lectura) contra las notas ya usadas en esta sesión:**
- `not_ent`: `72150694` → `co_sucu='02'`, `72163501` → `co_sucu='02'` (BQTO, coincide).
- `dev_cli`: `72001032` → `co_sucu='02'` (BQTO, coincide), `10927` → `co_sucu='01'` (SC, coincide).
- Distribución real completa de `not_ent`: `01`=471.054 filas, `02`=163.495 filas.

**Estado:** documentado como la fuente autoritativa definitiva para futuros usos (reportes, verificación de notas de crédito, cualquier clasificación real por sede). **No se rewireó el filtro del módulo de rutas con esto todavía** — v4.31/v4.32 ya establecieron que las rutas de despacho normales no deben filtrarse por sede en absoluto (el visor legacy mezcla ambas por diseño). `co_sucu` solo sería útil ahí como enriquecimiento informativo por nota (requeriría una consulta extra a Profit por cada ítem del scrape, ya que `lista.php` no expone ese campo) — pendiente decidir si vale la pena esa consulta extra o si con el estado actual (sin filtro, Macro-Ruta como mecanismo real de BQTO) es suficiente.

### v4.34 — `co_sucu` conectado al filtro del módulo de rutas (a pedido urgente del usuario, "esperan por nosotros") (2026-08-12)

**Objetivo:** el usuario pidió activar `co_sucu` ya mismo como filtro real para poder generar rutagramas de BQTO — reemplazando el enfoque de v4.31/v4.32 (sin filtro) por uno con dato verdadero por documento.

**Implementado:**
- `ProfitFacturador.sedes_por_notas(notas)` (NUEVO) — consulta en lote `co_sucu` en `not_ent`/`factura`/`dev_cli` (la primera tabla donde aparece la nota gana), mapea `'01'→'SC'`, `'02'→'BQTO'`. Best-effort, degrada a `{}` si Profit no responde. Probado en vivo: 4/4 notas conocidas resueltas correctamente en 0.06s.
- `ItemRuta.sede_origen` (NUEVO campo del dominio) — sede real del documento, vacío si no se pudo resolver (nunca se adivina).
- `RouteService.iniciar_ruta()` — enriquece cada ítem con `sede_origen` (mismo batch de la sesión, junto al ya existente `num_fact_por_notas`) y **filtra por sede real** cuando se indica `sede`: se conservan los ítems que coinciden con la sede pedida **y** los que no se pudieron resolver (revisión manual, nunca se descartan por falta de dato) — solo se excluyen los que Profit confirma que son de la OTRA sede.

**Verificación en vivo (solo lectura):**
- `sedes_por_notas` contra Profit real: 4/4 correcto (mismas notas de v4.33).
- Ruta real `CARACAS` con `sede=BQTO`: 31/31 notas resueltas por `co_sucu`, **0 son BQTO en este momento** (confirma, con el dato real ahora, lo que las sesiones anteriores ya sugerían: ese visor no tiene pendientes BQTO ahora mismo — no es un bug, es el estado real de la cola).
- Ruta `BARQUISIMETO`/`BARQUISIMETO CAPITAL`: 0 pedidos pendientes en el visor legacy en este momento (cualquier sub-ruta probada). Consistente con v4.32: el despacho real de BQTO pasa por la Macro-Ruta (alimentada por embalaje), no por esta cola del visor S/C.
- Test unitario (`test_cotejamiento_embalaje_ruta.py`) actualizado con el filtro real — 6/6 pasos OK.

**Nota aparte (no arreglada, fuera de alcance de este pedido):** el catálogo dinámico de sub-rutas (`fetch_catalogo_rutas_legacy`) fue inconsistente entre corridas (47 rutas en una prueba, 2 en otra, mismos segundos de diferencia) — y los nombres de sub-ruta del `MAPA_MACRO_SUBRUTAS` estático para "BARQUISIMETO" (`BARQUISIMETO CENTRO/ESTE/OESTE`, `CABUDARE`) no calzan con los nombres reales vistos en vivo (`BARQUISIMETO - YARACUY`, `BARQUISIMETO CAPITAL`) — parecen nombres de ejemplo nunca verificados contra el sistema real. No bloqueó esta tarea (el filtro por `co_sucu` funciona igual de bien pase lo que pase con el catálogo), pero puede estar contribuyendo a que ciertas rutas BQTO no se encuentren bien. **Resuelto en v4.35, ver abajo — no era una nota aparte, era el problema real.**

### v4.35 — El catálogo del visor es volátil en tiempo real: se combina estático+dinámico en vez de reemplazar, y se corrigen los nombres de ruta inventados (2026-08-12)

**Reporte del usuario:** tras v4.34, la pantalla REALIZAR RUTA solo mostraba "ENVÍOS ENTRE ALMACENES" — todas las demás rutas (CARACAS, PORTUGUESA, etc.) habían "desaparecido".

**Diagnóstico (verificado con un GET 100% nuevo, sin sesión, directo por PHP puro — no por nuestro adaptador):** el `<select name="ruta">` real de `192.168.4.148:8000/visor/index.php` pasó de **47 opciones** (cuando se revisó horas antes) a **solo 2** (las especiales) en la misma sesión de trabajo — tamaño de la respuesta HTML cayó de 12.324 a 5.597 bytes. Confirmado que NO es un bug de nuestro código: una consulta PHP pura, sin ningún estado nuestro, obtuvo el mismo resultado. El catálogo del visor legacy es **volátil en tiempo real**, probablemente solo lista rutas con pedidos pendientes en ese instante exacto.

**Causa del síntoma:** `RouteService.get_catalogo_rutas()` tenía una regla todo-o-nada: si el fetch dinámico devolvía *algo* (aunque fueran solo 2 rutas), se usaba SOLO eso, descartando por completo el catálogo estático con las 14 zonas geográficas conocidas.

**Fix:**
1. `get_catalogo_rutas()` ahora **combina** estático + dinámico (el estático siempre aporta las zonas conocidas así el visor esté en un momento de baja actividad; el dinámico solo agrega/actualiza sub-rutas encima, nunca reemplaza).
2. `MAPA_MACRO_SUBRUTAS` (catálogo estático) reescrito de cero con los **45 nombres reales** confirmados en vivo contra el `<select>` real (antes tenía nombres inventados que nunca existieron: `BARQUISIMETO CENTRO/ESTE/OESTE`, `CABUDARE`, una macro `VALENCIA` completa que no existe en el sistema real). Ahora 12 macros geográficas reales (ARAGUA, CARACAS, BARQUISIMETO, CARABOBO, BARINAS, PORTUGUESA, TRUJILLO, ZULIA, MERIDA, FALCON, APURE, FRONTERA/TACHIRA) + "RUTAS ESPECIALES/OTROS" + "ENVÍOS ENTRE ALMACENES" (v4.31).

**Verificación:** `py_compile` limpio; `listar_rutas_disponibles()` en vivo → **14 macro-rutas** visibles (antes solo 1, "ENVÍOS ENTRE ALMACENES") aunque el fetch dinámico real seguía devolviendo solo 2 en el momento de la prueba — confirma que el merge funciona. Tests de v4.31 (`test_rutas_especiales.py`) siguen pasando 4/4 con el catálogo corregido.

**Causa raíz real del catálogo caído a 2 rutas (confirmada por el usuario):** SQL Server de Profit se cayó de nuevo (mismo patrón que el incidente de v4.22, misma sesión) — el visor legacy consulta Profit para armar la lista de rutas con pedidos pendientes; sin Profit disponible, solo sobreviven las 2 rutas especiales "ENVIOS BARQUISIMETO"/"ENVIOS S/C" (presumiblemente no dependen de esa consulta, o quedaron en caché del lado del visor). Confirma que nunca hubo ningún archivo editado ni tocado por nosotros en `192.168.4.148:8000` — es 100% infraestructura (disponibilidad de SQL Server), consistente con v4.22. El fix de v4.35 (combinar catálogo estático + dinámico) sigue siendo la mitigación correcta del lado de ARA: aunque Profit se caiga, el operador sigue viendo las 14 rutas conocidas en vez de quedarse solo con 2.

**Kill switch ejecutado (a pedido del usuario):** `bin/sql_kill_switch.php` (herramienta ya existente, blindada — solo mata sesiones `host_name='KICKSERVER'`, `program_name LIKE '%Apache%'`, `status='sleeping'` >10 min, nunca a sí mismo). Dry-run encontró 4 candidatos reales (SPID 128, 174, 175, 179); ejecutado en real → 4/4 eliminados, 0 fallos. PRUEB25 volvió a responder (232 tablas) tras el kill. El catálogo del visor NO se recuperó inmediatamente — diagnóstico posterior (`bin/diagnostico_bloqueo_pruebas25.php`) reveló la causa real: el servidor SQL está en medio de una **restauración masiva en vivo** (20 bases de datos con `create_date` de HOY, `CRISTM25` recreada a las 12:40:39 sin la tabla `dbo.clientes` todavía) — no hay nada más que limpiar de nuestro lado, es una operación del DBA en curso.

### v4.36 — Auditoría anti-zombi de TODAS las conexiones Python a Profit (a pedido explícito: "arregla esto y otros módulos") (2026-08-12)

**Hallazgo:** `ProfitFacturador` no era el único módulo con conexión pyodbc cacheada indefinidamente sin cerrar entre llamadas — se encontraron **2 más** con el mismo patrón, uno de ellos el más usado de toda la app:

- `ProfitFacturador` (`rutas/infrastructure/profit_facturador.py`) — usado por el módulo de rutas (ya identificado antes de este pedido).
- `ProfitRenglonesSync` (`preparacion/infrastructure/adapters/profit_renglones_sync.py`) — singleton a nivel de módulo en `ara_server.py:172`, usado en CADA autochequeo (>3 ítems). Tenía reintento-en-caliente ante error de conexión (no se rompía), pero igual mantenía un SPID abierto indefinidamente en reposo.
- `ProfitSQLAdapter` (`preparacion/infrastructure/adapters/profit_sql_adapter.py`) — **DOS singletons** (`_prep_repo`, `_prep_repo_sc`, líneas 180/192 de `ara_server.py`), usado en cada picking de notas S/C. Este ni siquiera tenía método `cerrar()` — nadie podía cerrarlo aunque quisiera.

**Fix aplicado en los 4 archivos:**
1. `pyodbc.pooling = False` justo después de cada `import pyodbc` local — candado a nivel de ODBC Driver Manager, equivalente Python exacto del `ConnectionPooling=0` que ya protege el lado PHP (`ConnectionWrapper`). Aplicado también a `vigilar_datos.py` (que ya tenía conexión corta por ciclo, pero le faltaba este candado a nivel de driver).
2. `ProfitSQLAdapter`: se le agregó el método `cerrar()` que no tenía (cierra todas las conexiones cacheadas por base de datos).
3. Los métodos públicos de los 3 adaptadores (`verificar_nota_sc_existe`, `get_nota_sc_por_numero`, `num_fact_por_notas`, `sedes_por_notas`, `verificar_nota_credito`, `disponible`, `estado_escaneo_nota`, `marcar_renglones_cargados`) ahora cierran la conexión en un `finally` al terminar — nunca queda una conexión viva esperando a la siguiente llamada, sin importar si el método tuvo éxito o falló.

**Verificación:** `py_compile` limpio en los 4 archivos. Pruebas en vivo (solo lectura):
- `ProfitSQLAdapter.verificar_nota_sc_existe("467959")` → `True`, `len(adapter._conns) == 0` tras la llamada (confirma cierre).
- `ProfitRenglonesSync.estado_escaneo_nota("72163533")` → `sync._conn is None` tras la llamada (confirma cierre).
- `sedes_por_notas` re-probado: 3/4 (antes 4/4) — investigado, **no es regresión**: se confirmó por una vía de consulta completamente distinta (`ConnectionWrapper` contra PRUEB25) que la nota `72163501` simplemente no devuelve fila ahora mismo — coincide con la restauración en vivo del servidor documentada arriba, no con el cambio de código.
- Mini prueba de cotejamiento (v4.25) y catálogo combinado (v4.35) — ambas siguen pasando sin cambios.

**Alcance no cubierto (mismo patrón, menor prioridad — no se tocó):** `ConnectionWrapper.php` (PHP) ya tenía este candado desde v4.17.1; el resto de conectores PHP directos ya se blindaron en v4.20/v4.25. No se encontraron más singletons Python con conexión persistente fuera de los 3 corregidos.

### v4.37 — Rutagrama impreso rediseñado (formato A4 de referencia) + campos ricos de cierre (2026-08-12)

**Objetivo (a pedido del usuario, con plantilla HTML de referencia de Crist Medicals C.A. como base):** rediseñar el documento impreso de REPOLAB para que coincida con el formato A4 corporativo real, y completar los datos de cierre de rutagrama (ID ayudante, chofer, credenciales del vehículo) que el visor legacy exige.

**Verificado primero (no se tocó, ya estaba bien):** el cierre real (`LegacyRouteAdapter.confirmar_registro()`, `POST registro.php {rg, ayudantes, chofer, carro, registro:'Guardar'}`) ya envía exactamente `ayudantes`/`chofer`/`carro` — los mismos 3 campos que el visor legacy exige para cerrar. Esa parte ya estaba completa, tal como sospechaba el usuario.

**El hueco real estaba en el documento IMPRESO**, que solo mostraba nota/factura/paquetes/razón social/firma genérica — sin el nivel de detalle corporativo (IDs, cédulas, tipo de pedido, credencial completa del vehículo) que muestra la plantilla de referencia.

**Cambios:**
1. `rutas/domain/models.py` — `DatosVehiculo` extendido con campos **exclusivos para impresión** (nunca se envían al cierre real del visor legacy, que solo usa `ayudantes`/`chofer`/`carro`): `responsable_id/nombre`, `chofer_id/cedula`, `ayudante_id/nombre/cedula`, `vehiculo_descripcion/placa/intt`, `hora_prog_salida`. `ItemRuta` gana `peso` (float, default 0.0 — sin fuente de dato real confirmada todavía, se imprime como "—" en vez de inventar un número).
2. `rutas/infrastructure/web/repolab_printer.js` — reescrito completo: encabezado con logo/RIF de Crist Medicals, ficha operativa (responsable/chofer/ayudante con ID+cédula, vehículo con placa+INTT, ruta asignada, hora programada/real), leyenda e insignias de tipo de pedido (🟢 Solo Factura / 🟡 Factura+NC / 🟣 Solo NC, clasificadas desde `tipo_documento`/`has_credit_notes` — nunca adivinadas), tabla con columna Peso, totales consolidados, nota de auditoría, 3 firmas (responsable/chofer/ayudante) con ID y cédula.
3. `route_router.py` (`/api/rutas_hex/finalizar`) — acepta los campos nuevos opcionales del POST.
4. `route_service.py` (`finalizar_y_dividir_ruta`) — la respuesta incluye `datos_vehiculo` completo (con los campos de impresión) para que el frontend se lo pase tal cual a `imprimirRepolab`.
5. `templates/index.html` — formulario de cierre con sección plegable opcional "Datos para el rutagrama impreso"; tras finalizar, pregunta si se quiere imprimir de una vez y llama a `imprimirRepolab` con los datos capturados.

**Verificación:** `py_compile` limpio en los 3 archivos Python; `node -c` limpio en el JS; prueba de renderizado con datos de ejemplo (Node) — genera 17.573 caracteres de HTML, incluye correctamente el N° de rutagrama, las 3 insignias de tipo (FAC/FAC+NC/SOLO NC), cédula del chofer, y muestra "—" (no un número inventado) cuando el peso no tiene dato real. `DatosVehiculo.model_dump()` probado con los campos nuevos — OK.

**Pendiente:** ~~no hay fuente de dato real confirmada para `peso` por nota~~ — **resuelto, ver v4.38.**

### v4.38 — Peso real por nota conectado (reng_nde × art.peso) (2026-08-12)

**Objetivo (a pedido del usuario):** encontrar la fuente real del peso por nota, con consulta solo lectura si el servidor estaba activo.

**Investigación en vivo (solo lectura):** ninguna de `not_ent`/`factura`/`reng_nde`/`dev_cli` tiene columna de peso directa. Sí la tiene **`art.peso`** (peso unitario en Kg por artículo) — confirmado con datos reales en 10.042/11.425 artículos (~88%, ej. `AMP00001` CEFEPIME = 0.018 Kg). El peso de una nota se calcula sumando, por cada renglón en `reng_nde` (cantidad surtida `stotal_art` si es > 0, si no la solicitada `total_art`) × `art.peso` del mismo `co_art`. Verificado con nota real `72110888`: 1 renglón, `MISC0155`, cant=6, peso_u=0.097 → **0.582 Kg**, calculado a mano y luego confirmado igual por código.

**Implementado:** `ProfitFacturador.pesos_por_notas(notas)` — 2 consultas en lote (renglones de las notas, luego pesos unitarios de los `co_art` únicos encontrados), degrada a `{}` si Profit no responde o si el peso del artículo no está cargado (nunca inventa un número). Mismo candado anti-zombi de v4.36 (`finally: self.cerrar()`). Conectado en `RouteService.iniciar_ruta()`: cada ítem se enriquece con `it.peso` real (0.0 si no hay dato, el rutagrama lo imprime como "—").

### v4.39 — Bandeja de mensajes: chat interno par-a-par entre todos los usuarios, ARA - Intelligent anclado (2026-08-12)

**Reporte del usuario:** el panel de contactos no crea un chat nuevo; pidió que todos los usuarios creados (con su ID) queden en la lista de contactos y puedan comunicarse entre sí, dejando el chat de ARA - Intelligent anclado como única excepción.

**Diagnóstico:** el esquema `contactos`/`conversaciones` (`chat_schema.sql`) fue diseñado para un inbox tipo soporte al cliente — `conversaciones.UNIQUE(contacto_id)` fuerza **un solo hilo por contacto**, pensado para "un negocio conversando con N clientes externos por WhatsApp", no para chat privado entre pares de empleados. El frontend (`startConversation()` en `templates/index.html`) solo *buscaba* una conversación existente por nombre vía `GET /api/chat/conversaciones?q=`; si no la encontraba, mostraba un `alert` y se detenía — nunca creaba nada, de ahí "no crea un chat nuevo". Además `POST /api/chat/enviar` sí creaba contacto+conversación, pero indexado por `telefono` único: si dos empleados distintos le escribían al mismo destinatario, sus mensajes cairían en el mismo hilo mezclados (todos con `remitente='agente'`, sin distinguir emisor).

**Decisión de arquitectura (confirmada con el usuario):** chat privado par-a-par (1 hilo aislado por pareja de usuarios), no un canal único compartido por destinatario.

**Fix — Backend (`ara/ARA_Brain/data/chat_schema.sql`, `chat_routes.py`):**
1. `conversaciones` gana `usuario_a_id`/`usuario_b_id` (TEXT, orden alfabético canónico) y `contacto_id` pasa a nullable — SQLite trata múltiples `NULL` como valores distintos en `UNIQUE`, así que los hilos de contacto (bot/WhatsApp) y los hilos internos conviven en la misma tabla sin chocar. Migración `_migrar_conversaciones_pairwise()` idempotente (detecta la columna vía `PRAGMA table_info`, reconstruye la tabla solo si faltan las columnas nuevas) para no romper instalaciones ya existentes.
2. `_obtener_o_crear_conversacion_usuarios(a, b)` — resuelve el par ordenado alfabéticamente, así A→B y B→A siempre caen en la MISMA fila (verificado).
3. Nuevo `POST /api/chat/conversacion/iniciar` — crea o resuelve el hilo entre `usuario_origen_id`/`usuario_destino_id` sin necesidad de enviar un mensaje primero (reemplaza el `alert` muerto del frontend).
4. `POST /api/chat/enviar` — nueva rama de resolución por `usuario_origen_id`+`usuario_destino_id` (además de `conversacion_id` y `telefono` legado); `remitente` sigue siendo `'agente'` para ambos lados de un chat interno (checa `IN('cliente','sistema','agente')` sin tocar), la identidad real de quien escribió cada mensaje se preserva en `sender_id`.
5. `GET /api/chat/conversaciones?usuario_actual_id=` — ahora hace `UNION` de dos ramas: (a) hilos de contacto, siempre globales (así ARA - Intelligent queda igual para todos, anclado); (b) hilos internos donde participa `usuario_actual_id`, con `JOIN usuarios` para resolver el nombre del otro participante. Sin el parámetro, se comporta exactamente igual que antes (compatibilidad con llamadas viejas).

**Fix — Frontend (`templates/index.html`):**
1. Login persiste `usuario_ara_id` (antes solo se guardaba el nombre en `localStorage`, el ID real vivía solo en `window.usuarioId` y se perdía al refrescar F5) + `getUsuarioIdActivo()`.
2. `startConversation()` reescrito: llama a `/api/chat/conversacion/iniciar` y abre el chat directo — ya no hay callejón sin salida.
3. `abrirNuevoChatOriginalFallback()` (picker de "nuevo chat", ya leía `/api/usuarios/get_all` — la lista de TODOS los usuarios con su ID ya existía) — el filtro para excluirse a uno mismo pasó de comparar por nombre a comparar por ID (evita colisión si dos usuarios comparten nombre).
4. `loadMessages()` — la burbuja "mía" dejó de decidirse por `remitente==='agente'` (ambos lados de un chat interno son `'agente'`, así que antes TODO se veía como "mío" para los dos) y ahora compara `sender_id` contra mi propio ID.
5. `sendMessage()` — `sender_id` ahora envía el ID real (antes el nombre), consistente con el nuevo criterio de "es mío".
6. `renderChatList()`/`updateGlobalUnreadBadge()` — pasan `usuario_actual_id`; la lista se reordena en cliente para que **ARA - Intelligent quede siempre primero**, sin tocar su lógica (única excepción pedida explícitamente por el usuario).

**Verificación:** `py_compile` limpio en `chat_routes.py`; `chat_schema.sql` validado ejecutándolo en una BD SQLite real en memoria (el error que mostró el linter de la IDE era un falso positivo — dialecto T-SQL, no SQLite). Prueba de flujo completo contra una BD SQLite temporal aislada (nunca tocó `proyecto_ara.db` real) con Flask test client: usuario A inicia chat con B → 1 sola fila creada; B inicia chat con A (orden invertido) → resuelve la MISMA fila; A y B envían mensajes → 2 mensajes con `sender_id` distintos en el historial; `GET /conversaciones?usuario_actual_id=A` incluye el hilo con B + ARA - Intelligent anclado; un tercer usuario sin chats solo ve el bot; segunda corrida de la migración no revienta (idempotente) — 8/8 pasos OK.

### v4.40 — Estabilización de la bandeja tras despliegue real: 4 bugs encontrados en producción (2026-08-12)

Tras activar v4.39 contra la BD real (con datos de producción ya existentes, no una BD vacía como en las pruebas), el propio uso en vivo destapó 4 fallos que las pruebas aisladas no cubrían:

1. **Migración reventaba el arranque del servidor** (`sqlite3.OperationalError: no such column: usuario_a_id`): `chat_schema.sql` traía los `CREATE INDEX ... ON conversaciones (usuario_a_id/usuario_b_id)` en el mismo script que el `CREATE TABLE IF NOT EXISTS` — como la tabla YA existía (esquema viejo), el `CREATE TABLE` se saltaba entero y los índices intentaban crearse sobre columnas que la migración real (`_migrar_conversaciones_pairwise`, que corre después) todavía no había agregado. Fix: esos 2 índices se sacaron de `chat_schema.sql` y se crean en Python, en `init_chat_tables()`, DESPUÉS de invocar la migración — momento en que las columnas están garantizadas (recién creadas o migradas). Reproducido con una BD que simula el estado real (1 contacto + 1 conversación vieja) antes y después del fix — 100% verificado, dato viejo preservado.
2. **`openChat` crasheaba al crear un chat nuevo** (`Cannot read properties of null (reading 'style')` en consola): la pantalla "Iniciar Nueva Conversación" reemplazaba TODO `#module-content`, borrando el layout base del chat (`chat-placeholder-view`, `chat-active-container`, etc.) que `openChat()` necesita en el DOM. Fix: `startConversation()` llama a `renderModuloBandeja()` (reconstruye el layout) ANTES de `openChat()`.
3. **Las horas de los mensajes no coincidían con la hora real** (evidencia visual: reloj del sistema marcaba 4:14 p.m., burbuja del mensaje decía 08:14 p.m. — exactamente 4h de diferencia, VET es UTC-4): SQLite guarda `CURRENT_TIMESTAMP` en UTC como texto plano `"YYYY-MM-DD HH:MM:SS"` sin sufijo `Z`; el navegador interpreta ese formato sin sufijo como HORA LOCAL, no UTC, así que cada timestamp se mostraba desplazado por el offset de zona horaria. Fix: `formatChatTime()` ahora normaliza el string a ISO-UTC explícito (`ts.replace(' ','T')+'Z'`) antes de construir el `Date`, para las dos cosas que lo usan (hora de cada burbuja y `fecha_actualizacion` de la lista de chats).
4. **Envío de multimedia (foto/audio/archivo) estaba roto de raíz**: `sendMessage()` mandaba `contenido: text` donde `text = fileData.name` (solo el NOMBRE del archivo) — el `fileData.base64` real (los bytes de la imagen/audio) nunca se enviaba al backend. El mensaje se guardaba, pero `<img src="foto.png">` jamás podía cargar nada porque `foto.png` no es una URL ni un data-URI. Fix: `contenido` ahora manda `fileData.base64` (el data-URI completo) cuando hay archivo adjunto. De paso, el mapeo de `tipo` tenía a `fileType==='audio'` cayendo en `'archivo'` en vez de `'audio'` (perdía el reproductor `<audio>` dedicado y caía al link de descarga genérico) — corregido para mapear los 3 tipos reales (`imagen`/`audio`/`archivo`) 1 a 1.

**Verificación:** `py_compile` limpio; sintaxis JS del `<script>` completo de `index.html` validada con `node -c` sobre el bloque extraído; prueba end-to-end reproduciendo la BD real pre-v4.39 (bug 1) — sin error, datos preservados; `MAX_CONTENT_LENGTH` de Flask confirmado en 32MB (suficiente para adjuntos base64 típicos, no fue necesario tocarlo).

### v4.41 — Módulo nuevo: Atención al Cliente (WhatsApp Meta Cloud API multi-agente) (2026-08-12)

**Pedido del usuario:** un módulo casi idéntico a la Bandeja pero 100% para atención al cliente, con Meta API para conectar un número de WhatsApp Business único por usuario/agente, clientes identificados por co_cli + últimos 4 dígitos del teléfono, y respuestas reales (no inventadas) sobre pedidos activos, deudas y saldo usando el adaptador y las skills ya construidas.

**Lo que ya existía (verificado antes de escribir nada nuevo):** el proyecto YA tenía una base real y funcional para esto, construida en una sesión anterior — `WhatsappClienteAdapter.php` (autentica al cliente y restringe cada consulta SOLO a su propio `co_cli`, imposible consultar a otro cliente), `CustomerAuthenticator.php` (RIF/cédula/co_cli + últimos 4 dígitos del teléfono, exactamente el esquema de autenticación que pidió el usuario), y `WhatsAppWebhookController.php` (webhook multi-proveedor con verificación de Meta Cloud API ya implementada). Lo que NO existía: (a) el concepto de "un número por agente" — era un solo canal compartido para todo el negocio; (b) una bandeja/UI donde un agente humano viera y respondiera esas conversaciones; (c) conexión a "pedidos activos" (solo se consultaba saldo).

**Decisiones de arquitectura (confirmadas con el usuario antes de escribir código, dado el alcance/riesgo de tocar credenciales de mensajería a clientes reales):**
1. Sin credenciales de Meta todavía → todo el módulo se construye funcional en **modo simulado** (números `SIM-<usuario_id>`, sin llamadas reales a Meta), listo para recibir credenciales reales sin cambiar código.
2. **1 sola WABA/App de Meta con varios números** (no una App independiente por agente) — más simple de administrar, un solo webhook.
3. "Pedidos activos/deudas/saldo" → el usuario señaló que ya estaba resuelto en `ConsultarClienteTool.php` (documentos pendientes) + `ConsultarSaldoClienteTool.php` (saldo/cartera), no hacía falta un tool nuevo.

**Construido:**
1. `ara/ARA_Brain/data/atencion_cliente_schema.sql` — 3 tablas nuevas en la MISMA BD (`proyecto_ara.db`) que la Bandeja, pero dominio separado a propósito: `meta_numeros` (`phone_number_id` ↔ `usuario_id` del agente, `modo_simulado`), `atencion_conversaciones` (1 hilo por par `co_cli`+`phone_number_id`, columna `modo` IA/agente), `atencion_mensajes` (remitente `cliente`/`ia`/`agente`).
2. `ara/ARA_Brain/atencion_cliente_routes.py` — módulo Flask nuevo (mismo patrón que `chat_routes.py`): listar/asignar números, listar conversaciones por agente, historial, marcar leído, **tomar/soltar control** (el agente humano puede tomar un hilo para que la IA deje de autoresponder, y devolverlo), enviar respuesta manual, long-poll. `_enviar_a_meta()` hace el POST real a `https://graph.facebook.com/v20.0/{phone_number_id}/messages` si hay `WHATSAPP_ACCESS_TOKEN` configurado y el número no es simulado; si no, solo lo registra (best-effort, nunca rompe el flujo del agente).
3. `app/Services/NvidiaBrain/Adapters/AtencionClienteBridge.php` — puente PHP→SQLite (mismo archivo `proyecto_ara.db`, sin llamada HTTP intermedia entre los dos stacks, mismo patrón ya usado en el resto del proyecto): resuelve el agente dueño de un `phone_number_id`, busca/crea la conversación, registra mensajes de cliente e IA.
4. `WhatsAppWebhookController.php` — `extraerMensaje()` ahora también extrae `metadata.phone_number_id` del payload de Meta (única forma de saber a qué agente pertenece el mensaje con "1 WABA/varios números"); `handlePost()` usa el puente para registrar el mensaje entrante y, si un agente ya tomó el control (`modo='agente'`), **la IA NO autoresponde** — el mensaje queda encolado para que el humano lo conteste desde la bandeja. Todo el bloque del puente está en `try/catch` sin abortar el flujo si falla (tabla no migrada, número desconocido, etc.) — el webhook sigue funcionando exactamente igual que antes para los casos no cubiertos.
5. `WhatsappClienteAdapter.php` — ahora además de `ConsultarSaldoClienteTool` llama a `ConsultarClienteTool` (best-effort) para traer los documentos/pedidos pendientes, y los incluye tanto en el prompt del LLM como en el fallback determinista — nunca inventa montos ni números de documento, solo usa lo que devuelven las dos tools reales.
6. `templates/index.html` — módulo nuevo "Atención al Cliente" (botón propio en el menú, permiso `atencion_cliente` propio, casi idéntico en forma a la Bandeja): lista de conversaciones por agente con badge IA/TÚ, asignación de número (real o simulado) desde la UI, burbujas con remitente cliente/IA/agente, botón "Tomar control"/"Devolver a IA".

**Verificación:** `py_compile` limpio en los 2 archivos Python nuevos; `php -l` limpio en los 4 archivos PHP tocados/creados; se corrió la suite existente `bin/test_whatsapp_webhook.php` completa para confirmar que las nuevas clases (`AtencionClienteBridge`, `ConsultarClienteTool` y sus dependencias `AlmacenDbTrait`/`ConnectionWrapper`) cargan sin errores fatales — las únicas 4 fallas de esa suite son por PRUEB25 rechazando el login (`Cannot open database "PRUEB25"`), el mismo problema intermitente de conectividad ya documentado en v4.22/v4.35/v4.36, no una regresión introducida aquí; sintaxis JS completa de `index.html` validada con `node -c`; prueba end-to-end propia (`test_atencion_cliente.py`) contra una BD SQLite temporal aislada — asignar número simulado, mensaje entrante crea conversación en modo IA, agente responde y el modo pasa a 'agente' automáticamente (`envio_meta.simulado=true`, confirma que no intenta pegarle a Meta sin token real), tomar/soltar control, historial en orden cronológico correcto — 8/8 pasos OK.

**Pendiente (fuera de alcance de hoy, requiere que el usuario tenga las credenciales reales):** cuando haya WABA/token real de Meta, solo hace falta (a) poner `WHATSAPP_ACCESS_TOKEN`/`WHATSAPP_WABA_ID` en `.env`, (b) asignar el `phone_number_id` real de cada agente desde el botón de la UI (deja de ser modo simulado automáticamente) — no requiere ningún cambio de código adicional. Envío de multimedia (imagen/audio) a Meta real todavía no implementado en `_enviar_a_meta` (queda explícitamente `tipo_no_soportado_aun`), solo texto por ahora.

### v4.42 — Bandeja: HTML del menú roto (tags sin cerrar), visor de imágenes in-app y mini grabadora de audio (2026-08-12)

**Reporte inicial del usuario:** al marcar "Atención al Cliente" en Gestión de Usuarios el checkbox no aparecía en el formulario de permisos. Investigado: el checkbox SÍ estaba en el archivo correcto (`ara/ARA_Brain/templates/index.html`, confirmado que es el que Flask sirve por defecto); la causa real es operativa, no de código — el proceso de `ara_server.py` cachea la plantilla compilada en memoria y no relee el archivo hasta reiniciarse. Se le indicó al usuario reiniciar el servidor + refresco forzado del navegador.

**Encontrado de paso (sin relación con el reporte, revisando el bloque del menú principal):** el HTML de los botones del menú tenía tags `<button>` mal cerrados desde antes de esta sesión — `<button onclick="logout()">` nunca cerraba con `</button>` (cerraba el `<div>` contenedor en su lugar), y el botón "Reportes" abría OTRO `<button>` en vez de cerrarse, dejando un `</button>` huérfano al final del menú. Los navegadores lo auto-corrigen la mayoría de las veces (por eso no se notaba), pero es HTML inválido. Corregido: cada `<button>` cierra con su `</button>` correspondiente.

**Pedido explícito del usuario (bandeja interna, con captura de pantalla):**
1. Un visor de imágenes DENTRO de la app (no abrir pestaña nueva) al tocar el mensaje con la imagen adjunta.
2. Cambiar el botón "Audio" de la bandeja: en vez de abrir el selector de archivos del sistema, debía abrir una mini grabadora de audio en el navegador (aprovechando que el entorno corre en HTTPS), dejando "Archivo" como el único botón que sigue abriendo el selector del sistema.

**Implementado (`templates/index.html`):**
1. `abrirVisorImagen(src)` — overlay modal a pantalla completa (fondo oscuro, imagen centrada, botón ✕, click fuera para cerrar) en vez de `window.open(this.src)`. Corrección de seguimiento tras la primera versión: el usuario aclaró que el click debía activarse al tocar TODA la burbuja del mensaje, no solo el thumbnail exacto — se movió el `onclick` de la etiqueta `<img>` a la burbuja completa (mejor para pantallas táctiles), quitando el `onclick` duplicado del `<img>` para que el evento no se dispare dos veces por el burbujeo (bubbling) del click.
2. Mini grabadora de audio con `MediaRecorder`/`getUserMedia`: el botón "Audio" ahora llama a `iniciarGrabacionAudio()` (pide permiso de micrófono, arranca la grabación, muestra una barra roja con punto pulsante + cronómetro `MM:SS` reemplazando la fila de adjuntos). Dos botones en esa barra: cancelar (basura, descarta sin enviar) y enviar (detiene, arma el `Blob` de audio, lo convierte a base64 y reutiliza el pipeline `sendMessage(fileData, 'audio')` ya arreglado en v4.40). Si el navegador no soporta `MediaRecorder` o el contexto no es seguro (HTTPS/localhost), muestra un aviso claro en vez de fallar en silencio. Se eliminó el `<input type="file" id="input-audio">` (ya no aplica, el audio ya no se selecciona del sistema).

**Verificación:** sintaxis JS completa de `index.html` validada con `node -c` sobre el bloque `<script>` extraído, en cada paso (tras el fix del menú, tras el visor, y tras la grabadora) — sin errores en ninguna de las 3 validaciones.

**Cierre de esta ronda — panel de stickers:** botón "Stickers" nuevo junto a Foto/Audio/Archivo, abre un panel con una cuadrícula de 32 emoji grandes (`CATALOGO_STICKERS`). Al elegir uno, reutiliza exactamente el mismo `sendMessage()` de texto (sin tipo/tabla nueva — evita migrar el esquema). Del lado de lectura, `loadMessages()` detecta si un mensaje de tipo texto es "solo emoji" (≤3 caracteres Unicode, sin letras/números) y lo renderiza a 40px sin el bloque de texto normal, para que se vea como sticker en vez de mensaje de texto plano. Sintaxis JS validada de nuevo tras el cambio, sin errores.

### v4.43 — Badges de no-leídos + fix real de las flechitas de leído, y tarjeta de perfil individual (foto + descripción) (2026-08-12)

**Pedido 1 — "burbuja contadora de mensajes sin leer y las flechitas de enviado/recibido/leído":** al revisar, la Bandeja interna ya tenía ambas cosas en el código, pero con dos huecos reales:
1. El botón del menú "Atención al Cliente" tenía el `<span>` del badge en el HTML pero **nada lo actualizaba nunca** — quedaba oculto siempre. Igual pasaba con el badge global de Bandeja: solo se refrescaba mientras había un chat abierto con polling activo, nunca al entrar recién al sistema.
2. **Bug real en las flechitas de leído para chat interno par-a-par (v4.39):** `chat_marcar_leido()` en `chat_routes.py` solo hacía `UPDATE mensajes SET estado='leido' WHERE remitente='cliente'` — válido para el bot/WhatsApp externo, pero en un chat entre dos usuarios internos AMBOS lados mandan con `remitente='agente'` (nunca `'cliente'`), así que ese `WHERE` nunca encontraba nada que actualizar: el check azul de "leído" **nunca podía aparecer** en ningún chat interno, sin importar cuántas veces se abriera la conversación.

**Fix:**
1. `chat_marcar_leido()` ahora acepta `usuario_actual_id` en el body/query y, además del `UPDATE` original (contactos externos), corre un segundo `UPDATE mensajes SET estado='leido' WHERE remitente='agente' AND sender_id != ? AND sender_id IS NOT NULL` — marca como leído lo que mandó "el otro", nunca lo que mandé yo mismo. `openChat()` en el frontend ahora manda ese parámetro al marcar como leído.
2. `iniciarBadgesGlobales()` — nuevo `setInterval` de 20s arrancado justo después del login (independiente de qué pantalla esté abierta) que refresca tanto el badge de Bandeja como el de Atención al Cliente (`updateAtencionUnreadBadge()`, nueva función, mismo patrón que `updateGlobalUnreadBadge()`).
3. `acLoadMessages()` (Atención al Cliente) ganó las mismas flechitas ✓/✓✓/✓✓ azul que ya tenía la Bandeja interna (antes no las tenía en absoluto).

**Verificación:** prueba dedicada (`test_leido_pairwise.py`) que reproduce EXACTAMENTE el bug (confirma que sin `usuario_actual_id` el estado se queda pegado en `'enviado'` para siempre) y luego confirma el fix (con `usuario_actual_id` pasa a `'leido'`), más un caso de que nadie puede marcar su propio mensaje como leído — 4/4 pasos OK. `py_compile` y `node -c` limpios.

**Pedido 2 — "panel para editar tu propia tarjeta de visualización individual (foto y descripción única) que se muestren en los demás chats":** la tabla `usuarios` (SQLite, `proyecto_ara.db`) ganó columnas `foto_perfil`/`descripcion` (migración idempotente en `ara_server.py`, mismo patrón que las migraciones RBAC/rutas ya existentes). Nuevo endpoint `POST /api/usuarios/perfil` (guarda ambos campos, descripción con tope de 200 caracteres) y `GET /api/usuarios/get_all` ahora los incluye (`fotoPerfil`/`descripcion`).

El panel "Ajustes de Mensajería" (antes solo guardaba un avatar cosmético LOCAL en `localStorage`, invisible para los demás) ahora carga y guarda el perfil REAL contra el servidor: subir foto y escribir descripción quedan visibles para cualquiera que abra un chat contigo. Esa foto/descripción ahora aparece en 3 lugares más: el picker "Iniciar Nueva Conversación", la lista de conversaciones de la Bandeja, y el encabezado de un chat abierto — donde además tocar el avatar abre un modal "ver perfil" (`verPerfilUsuario()`) con foto grande, nombre y descripción completa. Caché de usuarios (`window._usuariosCache`) compartida entre todas estas vistas, invalidada automáticamente cuando el propio usuario actualiza su foto o descripción para que se refresque sin recargar la página.

**Verificación:** prueba dedicada (`test_perfil_usuario.py`) — migración de columnas, guardar perfil, releerlo de vuelta, y confirmar que un usuario sin perfil configurado todavía no rompe nada (defaults vacíos) — 4/4 pasos OK. `py_compile` en `ara_server.py` y `node -c` sobre el `<script>` completo de `index.html`, ambos limpios.

### v4.44 — Retención de 7 días: mensajes temporales SOLO en la Bandeja interna (2026-08-12)

**Pedido del usuario:** que la Bandeja funcione con mensajes temporales — si hoy es martes, ya no deberían quedar mensajes del martes de la semana pasada. Explícitamente solo para ese módulo (no para Atención al Cliente, que necesita conservar el historial de conversación con clientes reales).

**Implementado (`chat_routes.py`):**
1. `_purgar_mensajes_bandeja_antiguos()` — `DELETE FROM mensajes WHERE timestamp < datetime('now', '-7 days')` (tabla `mensajes` de la Bandeja únicamente; `atencion_mensajes` del módulo de Atención al Cliente queda intacto a propósito). Tras borrar, recalcula `ultimo_mensaje` de cada conversación afectada a partir del mensaje más reciente que sobrevive (o lo deja vacío si no queda ninguno) — así la vista previa del listado nunca muestra el texto de un mensaje que ya no existe.
2. Como el proceso no tiene un scheduler/cron real, se engancha de forma automática y "gratis" en el endpoint más usado (`GET /api/chat/conversaciones`, se llama cada vez que se abre o refresca la Bandeja) vía `_purgar_mensajes_bandeja_si_toca()`, con un throttle en memoria de 6 horas para no ejecutar el `DELETE` en cada request. También corre una vez al iniciar el servidor (`init_chat_tables()`).
3. Nuevo índice `idx_mensajes_timestamp` para que el filtro por fecha sea barato incluso con muchos mensajes acumulados.

**Verificación:** prueba dedicada (`test_retencion_bandeja.py`) sobre BD SQLite temporal aislada — conversación con 2 mensajes de hace 10 días + 1 de hace 3 días: tras la purga solo sobrevive el de 3 días, `ultimo_mensaje` se recalcula correctamente; una conversación de Atención al Cliente con un mensaje de hace 30 días queda intacta (retención exclusiva de Bandeja confirmada); el throttle de 6 horas confirmado (una segunda llamada inmediata no vuelve a ejecutar el `DELETE`) — 4/4 pasos OK. `py_compile` limpio.

### v4.45 — ARA Bot: cruce real movimientos_preparador ↔ notas_entrega (no fine-tuning) (2026-08-12)

**Pedido del usuario:** "entrenar" a qwen2.5-coder:3b (Ollama local) con las tablas SQLite, empezando por `movimientos_preparador` cruzada con `notas_entrega` por `nota_id = notas_entrega.id`.

**Aclarado con el usuario antes de tocar código (decisión con costo/alcance muy distinto):** no hay infraestructura de fine-tuning en este proyecto (LoRA/QLoRA, dataset curado, GPU dedicada) — reentrenar los pesos del modelo es un proyecto aparte, no algo resoluble en una sesión de chat. Se optó por lo que el usuario confirmó como la opción real y útil: mejorar la función que ya usa el bot para responder sobre movimientos, conectándola de verdad a los datos reales (mismo patrón "nunca inventar" de todo el proyecto).

**El hueco real:** `_consultar_movimientos_para_bot()` en `chat_routes.py` ignoraba por completo si el usuario preguntaba por una nota específica — siempre devolvía los últimos 15 movimientos GLOBALES de toda la base, sin filtrar ni cruzar con `notas_entrega`. Preguntar "¿qué sacó el preparador en la nota X?" nunca traía datos de esa nota en particular.

**Fix:**
1. La función ahora detecta un número de nota en el mensaje (`\d{6,10}`) y, si lo encuentra, resuelve primero `notas_entrega.id` a partir de `numero_nota` (el cruce real es contra el `id` interno, NO contra `numero_nota` directo — así lo indicó el usuario) y filtra los movimientos con `JOIN movimientos_preparador m ON m.nota_id = n.id WHERE n.id = ?`, en orden cronológico.
2. Si el número de nota no existe en `notas_entrega`, devuelve vacío explícitamente — nunca inventa movimientos de una nota que no se pudo verificar.
3. Si no hay número de nota en el mensaje (pregunta genérica de auditoría), se mantiene el fallback de "últimos 15", pero ahora enriquecido con `numero_nota`/`cliente` de cada movimiento (antes solo traía el artículo, sin poder decir de qué nota era).
4. System prompt del bot (`_procesar_mensaje_ara_bot`) — nueva instrucción explícita: cuando el contexto trae "DATOS TRAZABILIDAD (movimientos_preparador cruzado con notas_entrega)", responder SOLO con esos datos reales verificados y decir explícitamente si no hay movimientos, nunca inventar preparador/artículo/cantidad.
5. **Corrección del usuario:** el RESPONSABLE real de la nota es `notas_entrega.preparador_id` — NO el campo `usuario` de cada movimiento individual, que en escaneos automáticos suele venir `'sistema'` (confirmado con datos reales: la nota de prueba trae `usuario='sistema'` en sus movimientos pero `preparador_id=22`, la persona real). El encabezado de la respuesta ahora expone explícitamente "Responsable (preparador_id): X", y el system prompt distingue ambos campos para que el LLM nunca presente `'sistema'` como si fuera el preparador.

**Segunda tabla conectada (a pedido del usuario, mismo patrón): `reportes_ubicacion`** — log real de reubicaciones físicas de artículos entre puestos del almacén (`desde`/`hacia`/`usuario`/`fecha`), independiente de notas (no tiene `nota_id`, se filtra por `co_art`). Nueva `_consultar_reubicaciones_para_bot()`: detecta un código de artículo en el mensaje (regex `[A-Z]{2,6}\d{3,6}`, ej. `MQ01222`) y filtra SOLO sus reubicaciones reales; si no hay artículo en el mensaje, cae al fallback de "últimas 15" reubicaciones globales; si el artículo mencionado no tiene reubicaciones registradas, devuelve vacío explícito (nunca muestra traslados de OTRO artículo como si fueran del preguntado). Instrucción nueva en el system prompt del bot (punto 7) para que use solo estos datos reales y nunca invente un traslado. Insertada en la cadena de intención justo después de movimientos/antes de la búsqueda de stock.

**Verificación (datos reales, solo lectura):** artículo real `MQ01222` → 1 reubicación real (`6MQ34-P1 → 6MQ34-P2`, `wilber silva`, `2026-05-15`); artículo inexistente `ZZ99999` → vacío (no muestra reubicaciones ajenas); pregunta genérica de reubicaciones sin artículo → últimas 15; mensaje normal no dispara esta ruta — 4/4 OK. `py_compile` limpio.

### v4.46 — Reportes/Discrepancias: tarjetas y detalle reflejan la regla de negocio real (faltante/sobrante/verificado), se quita el alert() plano (2026-08-12)

**Reporte del usuario (con capturas):** en "Reportes y Estadísticas → Discrepancias de Stock", TODAS las tarjetas decían "⚠️ Discrepancia" genérico, sin importar si el artículo realmente tenía novedad o estaba verificado. Al tocar una tarjeta, se abría un `alert()` nativo del navegador (la "mini ventana web" fea) en vez de una tarjeta propia con colores.

**Diagnóstico:** la generación real del reporte (`ara_server.py`, auditoría de inventario) SÍ calcula correctamente la regla de negocio por artículo — `estado ∈ {'OK','FALTA','SOBRA'}` según `fisico` vs `teorico` — y ya la guarda en el JSON (`REP-*.json`, campo `detalles[].estado`). Pero el endpoint `/api/reportes/discrepancias` (el que alimenta el LISTADO de tarjetas) descartaba ese campo al armar la respuesta y solo mandaba `"discrepancia": true` fijo para toda fila — por eso el frontend no tenía forma de distinguir faltante de sobrante y usaba la misma etiqueta genérica siempre. El detalle (`/api/reportes/detalle/<id>`) sí devolvía el `estado` real de cada fila, pero `verDetalleReporte()` lo ignoraba y solo concatenaba el texto en un `alert()`.

**Fix:**
1. `/api/reportes/discrepancias` (`ara_server.py`) — ahora incluye `estado` (`FALTA`/`SOBRA`, nunca `OK` porque esas ya se filtran antes) y `stock_teorico` en cada fila.
2. Tarjetas del listado (`renderModuloReportes`) — leen `rep.estado` real: `FALTA` → borde/fondo ámbar, "⚠️ Faltante (físico/teórico)"; `SOBRA` → borde/fondo rojo, "🔥 Sobrante (físico/teórico)". Ya no existe una tarjeta que diga "Discrepancia" sin decir cuál fue la novedad real.
3. `verDetalleReporte()` — se quitó el `alert()` de raíz. Ahora abre `mostrarModalDetalleAuditoria(rep)`, un modal propio (mismo patrón visual que `verDetalleMovimientoProfit`, ya existente para Trazabilidad) que colorea cada fila según su `estado` real: verde "VERIFICADO", ámbar "FALTANTE", rojo "SOBRANTE" — con un resumen arriba (ej. "✅ 4 verificado(s) · ⚠️ 1 faltante(s)"). El caso de movimiento de reubicación (`rep.hacia` presente) reutiliza directamente el modal de Trazabilidad ya existente en vez de duplicar código.

**Verificación:** `py_compile` en `ara_server.py` y `node -c` sobre el `<script>` completo, ambos limpios. Contra los 63 archivos `REP-*.json` reales del proyecto: 57 discrepancias reales totales, 22 `FALTA` y 7 `SOBRA` distintas confirmadas con sus valores `teorico`/`fisico` reales (ej. `CR000181`: teórico 119, físico 118 → FALTA; `CR000333C`: teórico 4, físico 6 → SOBRA) — la clasificación por artículo ya estaba bien calculada en el origen, el fix fue no perderla en el camino hasta la UI.

**Verificación (datos reales, solo lectura, sin modificar nada):** se ubicó la nota real con más movimientos en la BD viva (`72158679`, 42 movimientos) y se probó contra ella directamente — (1) preguntar por esa nota trae exactamente sus 42 movimientos, todos correctamente atados a `numero_nota='72158679'`; (2) preguntar por una nota inexistente (`99999999`) devuelve vacío, no inventa nada; (3) pregunta genérica de auditoría sin número de nota sigue trayendo los últimos 15, ahora con `numero_nota`/`cliente` enriquecidos; (4) un mensaje sin palabras de auditoría no dispara esta ruta (no interfiere con otros flujos del bot) — 4/4 OK. `py_compile` limpio.

**Verificación:** probado en vivo contra Profit real — 3/3 notas (`72110888`→0.582, `72110886`→0.471, `72110885`→0.3 Kg), coincide exacto con el cálculo manual. Mini prueba de cotejamiento (v4.25) re-verificada con el nuevo método en el fake — 6/6 OK. Prueba end-to-end contra una ruta real (CARACAS) no se pudo completar: el visor legacy (`192.168.4.148:8000`) está caído en este momento (timeout de conexión) — no relacionado con este cambio, Profit (`.20`) sí respondió bien en la prueba directa.

### v4.47 — Incidente real de bloqueo en cascada (SQL Server .20) + mitigación automática (2026-08-12)

**Reporte del usuario:** "profit se pegó" — Profit Plus dejó de responder para varios operadores. Diagnóstico en vivo (nuevos scripts de solo lectura contra las DMV de SQL Server, `bin/diagnostico_conexiones_sospechosas.php`, `bin/diagnostico_seguridad_servidor.php`, `bin/diagnostico_bloqueo_servidor_completo.php`): **no fue un ataque ni nada de este proyecto** — se auditó xp_cmdshell (deshabilitado), servidores enlazados (ninguno), triggers de servidor (ninguno), SQL Agent jobs (solo el del sistema) y logins (todos conocidos) — superficie de seguridad limpia.

**Causa raíz real encontrada:** una app Node.js (`node-mssql`, host `Developer`/`192.168.4.23`, la máquina de control central) abre transacciones (`BEGIN TRANSACTION` implícito en updates a `Sucursales`/consultas a `almacen`) y **no siempre llega al `COMMIT`/`ROLLBACK`** — la sesión queda `sleeping` pero SQL Server retiene el lock igual. Efecto cascada real observado: `SPID 60` bloqueó a `SPID 216`, que a su vez bloqueó otras 10 sesiones (COMP-003, COMP-005, DESKTOP-UJCOGVR, DESKTOP-9C37A11, DESKTOP-DOTRJ0H, DESKTOP-2NA3KJ7, DESKTOP-IV0QEA7, PROFITSERVER\Administrador), algunas esperando más de 130 segundos. Resuelto manualmente esa noche en varias rondas (`KILL` iterativo de cada bloqueador raíz hasta que la cadena quedó en 0) — patrón se repitió 2 veces más la misma noche (más SPIDs de `node-mssql`, una vez también apareció `programa: Python` desde la misma máquina).

**Mitigación automática implementada (a pedido explícito del usuario, "para que no genere más bug ni colas innecesarias"):**
1. `bin/sql_kill_switch.php` ampliado con una SEGUNDA regla, independiente de la original (KICKSERVER/Apache sleeping >10min): **BLOQUEADOR ACTIVO** — mata cualquier SPID que sea, verificado en el momento, el `blocking_session_id` real de otra sesión con más de 15 segundos de espera. No filtra por host ni programa (a diferencia de la regla KICKSERVER) — reacciona al comportamiento dañino real, nunca toca contención normal/momentánea (un lock de &lt;15s se resuelve solo, no se mata). Log ahora incluye el motivo de cada kill (`KICKSERVER_APACHE_10MIN` / `BLOQUEADOR_ACTIVO_15S`).
2. `bin/watchdog_bloqueos_sql.ps1` (nuevo) — wrapper que ejecuta el kill switch y loguea en `ara/ARA_Brain/data/watchdog_bloqueos_sql.log`, mismo patrón que el watchdog de zombis PHP ya existente (`watchdog_mata_zombis.ps1`/`ARAWatchdogZombis`).
3. **Tarea programada `ARAWatchdogBloqueosSQL`** registrada en el Programador de Tareas de Windows — cada 2 minutos (más seguido que el de zombis PHP, cada 5 min, porque un bloqueo activo daña a otros usuarios en segundos, no en minutos), mismo usuario/nivel de permisos que la tarea existente.

**Verificación:** `php -l` limpio; probado en `--dry-run` (0 falsos positivos en estado normal); tarea disparada manualmente una vez — **corrió y mató 3 SPIDs bloqueadores reales en el primer disparo** (confirma que el patrón seguía activo y que la mitigación funciona de verdad, no solo en teoría). `LastTaskResult: 0` (éxito).

**Pendiente (fuera del alcance de ARA, requiere a quien mantenga la app Node.js en `192.168.4.23`):** el arreglo real es en el origen — envolver cada `BEGIN TRANSACTION` en `try/catch/finally` con `ROLLBACK` explícito en el camino de error, y considerar `SET XACT_ABORT ON` como red de seguridad. La mitigación automática de ARA es un parche que contiene el daño, no la solución de fondo.

### v4.48 — Auditoría de seguridad "llamados sin seguridad" + whitelist de tablas en el skill SQL del bot (2026-08-13)

**Pedido del usuario:** revisión simple de todo el código de ARA buscando llamados SQL sin seguridad (concatenación en vez de parámetros preparados) o ejecución de comandos con entrada de usuario sin sanitizar.

**Resultado de la auditoría (agente Explore, cobertura completa de Tools/Adapters/Controllers/Core PHP + ara/ARA_Brain, preparacion, rutas, bin en Python):** el proyecto sigue consistentemente el patrón seguro establecido — `?`/`:nombre` con parámetros reales en Python (`conn.execute(sql, params)`) y `PDO::prepare()`/`ConnectionWrapper::querySafe()` en PHP. Ningún caso de inyección SQL explotable ni de ejecución de comandos de sistema con input de usuario sin sanitizar (los `proc_open`/`subprocess` existentes pasan argumentos como arrays o vía JSON por stdin, nunca por shell).

**Único hallazgo, riesgo bajo:** `ara/ARA_Brain/db_query_tool.py::ejecutar_consulta_sql_read_only()` — el skill ReAct del bot (consultas en lenguaje natural sobre `proyecto_ara.db`) ejecuta el SQL completo generado por el LLM tal cual, sin parametrizar. Ya estaba blindado en capas (conexión SQLite `mode=ro` real a nivel de motor, `PRAGMA query_only=ON`, exige `SELECT` inicial, bloquea `;` y una lista negra de palabras como `INSERT`/`DROP`/`ATTACH`), pero una lista negra de palabras es en principio bordeable con creatividad (`UNION SELECT ... FROM sqlite_master` u otra tabla no prevista) para leer datos que no debería, aunque nunca pudiera escribir nada.

**Fix:** nueva función `_tablas_referenciadas()` extrae por regex todas las tablas mencionadas tras `FROM`/`JOIN` (incluye listas separadas por coma). Antes de ejecutar cualquier consulta, se compara contra el esquema real conocido (`obtener_esquema_bd()`, ya excluye `sqlite_%`) — si aparece CUALQUIER tabla fuera de esa whitelist, la consulta se rechaza con detalle explícito. Cierra la vía de fuga sin importar cómo se redacte el SQL (a diferencia de la lista negra de palabras, que depende de anticipar cada truco posible).

**Verificación:** consultas legítimas (`SELECT` simple, `JOIN` real entre `notas_entrega`/`usuarios`, `SELECT` sin `FROM`) siguen funcionando igual que antes; intento de leer `sqlite_master` → rechazado explícitamente; `UNION SELECT` contra una tabla inventada/no listada → rechazado explícitamente — 5/5 pruebas OK. `py_compile` limpio.

### v4.49 — Dashboard en tiempo real del watchdog (puerto 5005) + regla KICKSERVER desactivada a pedido (2026-08-13)

**Pedido del usuario:** una mini página en tiempo real (HTML + su propio `.py`, puerto 5005) que analice `watchdog_bloqueos_sql.log` + `logs/kill_switch_*.txt` para identificar culpables/víctimas con detalle.

**Construido:**
1. `ara/ARA_Brain/watchdog_dashboard_server.py` (Flask, puerto 5005, independiente del servidor principal en 5000) — parsea ambos logs con regex, cruza por SPID para rankear "culpables recurrentes" (mismo SPID matado varias veces = señal real de proceso que sigue reabriendo la misma conexión fantasma), detecta huecos de monitoreo (>6 min sin registro), y agrega un panel "en vivo" con una consulta real de solo lectura al servidor SQL (mismo patrón anti-zombi que los diagnósticos PHP) para ver bloqueos activos ahora mismo con host/IP reales — se degrada solo si el servidor no responde, sin romper el resto del dashboard.
2. `ara/ARA_Brain/templates/watchdog_dashboard.html` — se auto-actualiza cada 5s vía `fetch`, sin recargar la página. Tarjetas resumen, ranking de culpables, panel en vivo, huecos, línea de tiempo de los últimos 300 eventos.

**Hallazgo real revelado por el propio dashboard al probarlo:** de 42 kills históricos, **36 fueron `KICKSERVER_APACHE_10MIN`** (el problema original de v4.17.1, fantasmas de Apache) y solo 6 `BLOQUEADOR_ACTIVO_15S` (el incidente Node.js de v4.47, ya resuelto y sin repetirse desde entonces). El panel en vivo, al probarlo, cazó un bloqueo real distinto a los anteriores: `SPID` desde host `PROCESOS-MARIO` (192.168.4.56, programa "JVT PEDIDOS") — confirma que el panel funciona con datos reales, no solo históricos.

**Pedido de seguimiento del usuario, tras ver ese desglose:** dejar de matar sesiones de KICKSERVER/Apache. `bin/sql_kill_switch.php` — la regla 2a (KICKSERVER) se desactivó con un flag (`$kS_matarKickserver = false`) en vez de borrar el código, para poder reactivarla fácilmente si el problema original vuelve. Solo queda activa la regla de bloqueador activo genérico (`BLOQUEADOR_ACTIVO_15S`).

**Verificación:** servidor levantado real en `localhost:5005`, las 3 rutas (`/`, `/api/analisis`, `/api/vivo`) responden 200 con datos reales — confirmó el hueco de 246 min ya encontrado manualmente y el desglose de motivos. Tras desactivar KICKSERVER, `--dry-run` del kill switch da 0 candidatos (correcto: sin KICKSERVER y sin ningún bloqueador activo por encima de 15s en ese momento). `php -l` limpio.

### v4.50 — Dashboard: filtros por SPID/host/IP/motivo/fecha + captura de host/IP responsable en cada kill (2026-08-13)

**Pedido del usuario:** mejorar el dashboard con filtros de SPIDs históricos por IP y nombre del escritorio responsable.

**El hueco real:** `bin/sql_kill_switch.php` nunca capturaba host/IP/login/programa al detectar o matar un candidato — el log solo tenía `SPID | segundos | motivo`, sin ninguna forma de saber DE DÓNDE vino cada bloqueo históricamente (el panel "en vivo" sí lo mostraba, pero solo para el momento exacto en que se consultaba, no quedaba guardado).

**Fix:**
1. `bin/sql_kill_switch.php` — las dos consultas de candidatos (KICKSERVER y BLOQUEADOR ACTIVO) ahora hacen `JOIN`/`LEFT JOIN` contra `sys.dm_exec_sessions`/`sys.dm_exec_connections` para traer `host_name`, `login_name`, `program_name` y `client_net_address` de cada candidato. El formato del log gana 4 campos nuevos al final de la línea (`| host X | ip Y | login Z | programa W`) — las líneas viejas sin estos campos siguen siendo válidas (compatibilidad hacia atrás).
2. `watchdog_dashboard_server.py` — el parser de `kill_switch_*.txt` ahora extrae esos 4 campos nuevos (opcionales vía regex, `''` si la línea es del formato viejo). `_armar_analisis()` acepta un diccionario de filtros (`spid`, `host`, `ip`, `motivo`, `desde`, `hasta`, todos combinables con AND) que se aplican sobre el detalle antes de construir el ranking de culpables, el desglose por motivo y la línea de tiempo. Nuevos `ranking_hosts`/`ranking_ips` (top 15 por conteo de kills). Endpoint `/api/analisis` ahora lee esos filtros de query params.
3. `templates/watchdog_dashboard.html` — panel de filtros (SPID, host, IP, motivo con dropdown, rango de fecha), tabla de culpables con columnas Host/IP/Programa nuevas, y dos mini-rankings (top hosts / top IPs) — cualquier celda de host/IP/fila de ranking es cliqueable y aplica ese filtro al instante. Sigue auto-actualizándose cada 5s respetando los filtros activos.

**Verificación:** prueba con datos sintéticos (línea vieja sin host/IP + líneas nuevas con host/IP) confirma que el parser no rompe con ningún formato y que filtrar por host agrupa correctamente al SPID reciclado con sus 2 apariciones. Servidor reiniciado y probado real por HTTP: `/api/analisis?spid=157` filtra correctamente, `filtros_aplicados` en la respuesta refleja lo pedido. `py_compile`/`php -l` limpios.

**Ampliación (mismo día, pedido de seguimiento):** la línea de tiempo también debía mostrar de dónde vino cada SPID y con qué programa. Como `watchdog_bloqueos_sql.log` (el que arma la línea de tiempo) solo trae SPIDs por evento, sin host/IP propios, se agregó `_indice_por_spid()` + `_origen_mas_cercano()`: correlaciona cada SPID de un evento "KILL" contra el detalle de `kill_switch_*.txt` por SPID + la coincidencia de fecha MÁS CERCANA dentro de una ventana de 15s (los dos logs los escribe la misma ejecución pero con un par de segundos de diferencia entre el wrapper PowerShell y el script PHP, así que un match exacto de timestamp fallaría). Cada evento de la línea de tiempo gana un campo `origenes` (host/IP/programa/motivo por SPID); en el HTML, los kills de antes de este cambio se etiquetan explícitamente como "origen no registrado" en vez de mostrar un vacío ambiguo.

**Verificación:** prueba sintética con timestamps desfasados 2s entre los dos logs (patrón real observado) — la correlación encuentra el origen correcto igual. Servidor real por HTTP confirma que los eventos de antes del cambio muestran `origenes` vacíos correctamente etiquetados (no rompe nada), listo para que los próximos kills reales traigan los datos completos.

**Ampliación (mismo día): mini-gráfico de pulso en vivo, estilo ticker.** Nueva sección "Pulso del servidor" — línea SVG que crece a la derecha con cada muestreo de `/api/vivo` (cada 5s, mismo ciclo de polling ya existente), mostrando cuántos bloqueadores hay activos en cada instante. Buffer de hasta 60 puntos (5 min) en memoria del navegador — se reinicia al recargar la página a propósito (es un pulso en vivo, no un histórico; el histórico ya lo cubre la línea de tiempo). Un hueco real (servidor sin responder) se dibuja como un CORTE en la línea, nunca como una caída falsa a cero — se distingue `valor: null` de `valor: 0`. Sigue la guía de la skill de visualización de datos: una sola serie (sin necesidad de leyenda, el título ya la nombra), trazo fino de 2px con extremos redondeados, grid recesivo, punto final destacado con halo, y una capa de hover con crosshair + tooltip mostrando hora y valor exacto del punto más cercano al cursor.

**Verificación:** `node -c` sobre el `<script>` completo del dashboard, limpio. Servidor reiniciado y confirmado por HTTP que la página sirve el nuevo bloque del gráfico.

### v4.54 — Consultar Nota: embalador + nombres reales + fix de mislabeling "API caído" (2026-08-24)

**Reporte del usuario:** revisar en `rep_notas` cuáles de las últimas 10 notas pasadas por picking no quedaron registradas en `gestion.php` (MySQL legacy).

**Hallazgo real en `ConsultarNotaTool.php` (`gestionEnLegacy()`):** el mapeo solo cubría preparador y chequeador — el **embalador estaba completamente ausente** (columnas reales `num_emb`/`verifi_emb`/`hora3` nunca se leían), y los tres campos de operador se mostraban como número crudo (`num_prep=39`) en vez del nombre real, sin cruzar contra la tabla `usuarios` del legacy. Verificado contra la nota real `72165989`: preparador y chequeador eran la misma persona (ENDER TORREZ), embalador LUIS RODRIGUEZ — el tool no podía mostrar esto último.

**Fix:** `gestionEnLegacy()` reescrito para mapear las tres etapas completas + `resolverNombresOperadores()` (nueva función, cruza número→nombre contra `usuarios` del MySQL legacy, una sola query con `IN (...)` para los 3 números a la vez).

**Segundo hallazgo, distinto (reportado por el usuario como "API caído" pero era un bug de etiquetado, no una caída real):** `ConsultarNotaTool` marcaba el origen como "(API caído)" tanto si la API remota realmente no respondía como si respondía bien pero simplemente no encontraba coincidencias — dos situaciones completamente distintas con el mismo mensaje. Fix: nueva propiedad `private bool $apiRespondio`, puesta en `true` en cualquier respuesta válida de la API (incluso una lista vacía) dentro de `buscarNota()`; el label de origen ahora chequea ese flag antes de asumir caída.

**Verificación:** probado contra la nota real 72165989 (ENDER TORREZ prep+chq, LUIS RODRIGUEZ emb) — las 3 etapas y los 3 nombres reales aparecen correctamente. `php -l` limpio.

### v4.55 — Kill switch: 30s de tolerancia para procesos "JVT PEDIDOS" (2026-08-24)

**Pedido del usuario, verificado en vivo antes de tocar código:** los procesos de "JVT PEDIDOS" (ejecutivos de venta, PCs `EJECUTIVO-*`/`VENT-*`/`DESKTOP-*`) necesitan más margen que el resto porque sus transacciones de pedido tardan naturalmente más — confirmado contra `sys.dm_exec_sessions` en vivo que `program_name = 'JVT PEDIDOS'` es el valor real (no se adivinó).

**Fix:** `bin/sql_kill_switch.php`, regla `BLOQUEADOR_ACTIVO_15S` — el umbral de espera pasa de 15000ms fijo a `CASE WHEN bs.program_name LIKE '%JVT%' THEN 30000 ELSE 15000 END`, uniendo contra `sys.dm_exec_sessions bs` para leer el `program_name` del bloqueador antes de decidir el umbral.

**Verificación:** `php -l` limpio; `--dry-run` no afectado (mismo comportamiento para el resto de programas).

### v4.56 — Nuevos reportes globales: facturas vencidas + clientes sin pedido semanal (2026-08-24)

**Construidos, con alcance acotado por el usuario vía preguntas explícitas (clientes frecuentes solamente, ventana rodante de 7 días):**
- `ClientesFacturasVencidasTool.php` (Auditoria) — facturas con saldo pendiente pasado su `fec_venc`, verificado contra datos reales de PRUEB25.
- `ClientesSinPedidoSemanaTool.php` (Auditoria) — clientes frecuentes sin ningún pedido en los últimos 7 días rodantes.

**Verificación:** ambas probadas contra datos reales de Profit (PRUEB25). `php -l` limpio. Reglas de activación en lenguaje natural agregadas a `chat_routes.py::_detectar_intencion_skill()`.

### v4.57 — Bulto Cerrado Router: modo proactivo (recomendar ANTES de que falte stock) (2026-08-24)

**Pedido del usuario:** un modo nuevo, distinto del ya existente `stock_surtido_prioritario` (confirmado como algo separado vía pregunta explícita) — que recomiende qué mover de bulto cerrado a picking basándose en volumen + rotación, **antes** de que el stock de picking se agote, no como reacción a que ya se agotó.

**Fix:** `BultoCerradoRouterTool.php` gana `modoProactivo()` — se activa automáticamente cuando el tool se llama sin el parámetro `items` — que consulta `st_almac`/`art` en Profit, arma candidatos vía `candidatosProactivosDesdeProfit()` y les asigna un score de urgencia según rotación reciente vs. stock actual en picking.

**Verificación:** probado contra datos reales de Profit. `php -l` limpio.

### v4.58 — Progreso de rutas: pivot arquitectónico al visor legacy real (2026-08-24)

**Reporte del usuario, con captura:** "listar progreso de ruta" mostraba 0% en todas las rutas, números de cajas inexactos (Caracas no tenía 140 cajas) y a las rutas de la noche les faltaban macro-rutas.

**Primer intento (insuficiente):** se cambió el % por conteo de cajas escaneadas, pero seguía leyendo de `sesiones_ruta_activa` (SQLite local de ARA).

**Corrección explícita y contundente del usuario, en mayúsculas:** *"REY CONSULTA EN EL VISOR LEGACY NO LA DE NOSOTROS SOMOS UN ESPEJO"* — ARA's `sesiones_ruta_activa` es un espejo que puede acumular sesiones de varios días desactualizadas; la fuente de verdad real es el visor legacy (MySQL, `192.168.4.148`).

**Fix real (pivot completo, no un parche):** `RouteService.progreso_ruta_macro()`/`progreso_todas_rutas()` reescritos para conectar DIRECTO al visor MySQL (`_conectar_visor_mysql()`, pymysql), agregando `cargado` + `detalle`/`escanear` por macro-ruta (resuelta vía `_macro_de_ruta_cruda()`), devolviendo `cajas_escaneadas` real (paquetes vs escaneados) en vez de la vieja matriz de porcentaje 0-0/1-0/1-1. `por_sede` queda `{}` (limitación documentada: el visor no tiene columna de sede). `ListarProgresoRutasTool.php`/`RutaProgresoTool.php` actualizados para mostrar `"{cajas_escaneadas}/{total_items} cajas escaneadas"` en vez de porcentaje.

**Verificación:** probado contra el visor MySQL real. `py_compile`/`php -l` limpios.

### v4.59 — ARA Intelligent: rediseño de entrada (buscador centrado, adjuntos OCR, dictado, animación "pensando") (2026-08-24)

**Pedido del usuario (con referencia visual tipo Gemini):** buscador centrado que se corre hacia abajo tras la primera pregunta pero vuelve al centro en un chat nuevo, botón "+" para adjuntos/multimedia, botón de audio al otro extremo SOLO para dictado en tiempo real (Web Speech API), y una tool nueva para extraer campos de una factura fotografiada (JPG/PNG/PDF) contra una API OCR externa propia del usuario (documentación completa provista).

**Construido:**
1. `templates/ara_inteligente.html` — barra centrada (`margin:0 auto`), botón `#btn-adjuntar` (input file oculto, whitelist de extensiones confirmada con el usuario), botón `#btn-microfono` (`alternarDictado()`/`crearReconocedorVoz()` vía `webkitSpeechRecognition`).
2. `POST /api/ara_inteligente/adjuntar` (`ara_inteligente_routes.py`) — multipart, llama la skill `extraer_campos_factura_ocr`.
3. `ExtraerCamposFacturaOcrTool.php` (Recepcion, nueva) — wrapper `CURLFile` multipart hacia la API OCR externa del usuario.
4. Indicador "pensando" mejorado: frases rotativas (`FRASES_PENSANDO`) en vez de un texto fijo, dando sensación de varias etapas (pensando/consultando/cruzando datos).

**Verificación:** `node -c` sobre el `<script>` completo, limpio. `php -l` limpio.

### v4.60 — Integración SSO con el CRM CristMedicals: build inicial + iteraciones reales en producción (2026-08-24)

**Pedido del usuario, con documentación completa provista (`INTEGRACION_SSO.md`):** que ARA Intelligent se autentique con el CRM propio de un compañero — login único, sin volver a pedir credenciales si el usuario ya inició sesión en el CRM.

**Construido (`sso_erp_routes.py`, nuevo):** verificación JWT HS256 (`PyJWT`) contra `ERP_SSO_SECRET`, con `issuer='cristmedicals-erp'`. Tablas SQLite `sso_usuarios`, `sso_codigos_temp` (código de un solo uso, 30s TTL — el JWT y el perfil NUNCA llegan a la URL final visible en el navegador), `sso_config` (captura el `project_id` real de cada JWT verificado). Endpoints `GET /auth/sso`, `GET /api/sso/consumir`, `GET /api/sso/project_id`.

**Iteraciones reales tras problemas reportados en producción, cada una con causa raíz distinta:**
1. El callback del ERP en Auth Central abría ARA Warehouse en vez de la IA — se agregó una redirección de seguridad en `/` (`ara_server.py`) que detecta `?token=` y redirige a `/auth/sso?token=...`.
2. **Corrección explícita del usuario:** tras tomar la sesión del CRM, ARA NO debía redirigir de vuelta al portal del CRM para volver a loguearse — debía crear la sesión de ARA directamente con esos datos. Implementado el chequeo silencioso (`intentarSsoSilencioso()`).
3. El fetch directo con `credentials:'include'` cruzando de origen (ARA en IP/trycloudflare.com → portal en `cristmedicals.com`) siempre fallaba por CORS — el portal solo permite ese patrón para subdominios reales `*.cristmedicals.com`, por diseño de seguridad de ellos. Se implementó un respaldo vía iframe oculto + `postMessage` (`chequeoSilenciosoViaIframe()`), pendiente de una páginita del lado del CRM (`sso-silent-frame.html`) con `frame-ancestors` habilitado para el origen de ARA.
4. **Resuelto de raíz más adelante (ver v4.63):** al conseguir un subdominio real `ara.cristmedicals.com`, el fetch directo (`chequeoSilenciosoViaFetchDirecto()`) volvió a ser el camino principal — el iframe quedó solo como respaldo automático.
5. **404 real, dos variantes distintas, ambas con causa raíz confirmada en producción:** Auth Central tenía el callback registrado apuntando a `/sso` (sin el prefijo `/auth/`) en una variante, y a `https://ara.cristmedicals.com/ara-inteligente/auth/sso` (tratando la URL de la app como base y concatenando `/auth/sso`) en otra. En vez de depender de que se corrigiera la config del ERP, se agregaron dos rutas alias (`/sso`, `/ara-inteligente/auth/sso`) que redirigen al callback canónico — ambas probadas en vivo con `curl` antes de darlas por resueltas.
6. **Incidente de seguridad real:** Auth Central mandó una vez el valor de `ERP_SSO_SECRET` (el secreto compartido) directamente como si fuera el `token` — rotación inmediata del secreto comprometido, luego restaurado al valor correcto que el admin del CRM confirmó como el vigente.

**Verificación:** cada variante de URL probada en vivo con `curl` (200/302/401 según correspondía) contra el servidor real antes de reportar como resuelta — no se dio por cerrado ningún punto solo por lectura de código.

### v4.61 — Nuevo microservicio independiente: ARA Coder (agente ReAct + auto-síntesis de skills, puerto 8010) (2026-08-24)

**Pedido del usuario, con especificación completa provista en dos documentos:** un servicio 100% aparte de ARA Warehouse (proceso Python propio, puerto 8010, corre con el Python global de la máquina — NO el venv de ARA, a propósito, para quedar desacoplado) que responda preguntas en lenguaje natural sobre el código/datos del proyecto vía un loop ReAct real (razonar → llamar herramienta → repetir), y que aprenda solo: cuando resuelve una pregunta con una consulta SQL exitosa, sintetiza una tool PHP real (`AgentToolInterface` completo, parametrizada — nunca con los valores literales de la pregunta que la originó) directo en `Tools/AutoGeneradas/` (nuevo departamento agregado a la whitelist de `ToolRegistry.php`), para que el motor "Automático" de ARA Warehouse la descubra y la use sola, sin volver a pasar por el loop del agente.

**Construido (`C:\ara_coder_service\`, íntegro):** `server.py` (Flask + CORS), `core/agent_loop.py` (loop ReAct, pool de LLM NIM→Ollama→GB10, síntesis de skills), `core/tool_registry.py`, `tools/system_tools.py`/`db_tools.py`/`network_tools.py`, `config.py`. Tools iniciales: exploración de archivos/código (whitelisteada a `ARA_PROYECT` + `ara_coder_service`), SQL de solo lectura contra el SQLite de ARA y contra Profit (SQL Server, SIEMPRE PRUEB25), HTTP local/LAN.

**Verificación:** `php -l`/`py_compile` limpios en cada componente. Primeras skills generadas y verificadas contra el motor Automático real (`RutaGramaNotasDAOTool.php`, `FacturaPendienteTool.php`).

### v4.62 — ARA Coder: bugs reales de producción encontrados y corregidos en vivo (2026-08-24)

Serie de fallos reales descubiertos probando ARA Coder en producción el mismo día del build, cada uno diagnosticado contra evidencia real (nunca asumido) antes de corregir:

1. **CORS bloqueaba ARA Intelligent → ARA Coder** (orígenes/puertos distintos, aunque misma máquina) — `flask_cors.CORS` + respaldo manual en `after_request`.
2. **Gestión de procesos de Windows, error repetido ~4 veces:** `Get-CimInstance Win32_Process.CommandLine` NO incluye el `-WorkingDirectory` usado al lanzar el proceso — filtros por substring de carpeta nunca matchean, y un filtro amplio (`-match 'server\.py'`) una vez mató por accidente `ara_server.py` (el proceso principal de ARA Intelligent), requiriendo reinicio de emergencia. Fix adoptado para el resto de la sesión: filtrar siempre por `ExecutablePath -eq <ruta exacta del python.exe>`.
3. **Calidad de modelo:** el modelo inicial (8B) repetía la misma consulta fallida 5-6 veces sin explorar el esquema. Se probaron 4 candidatos NIM reales con tool-calling real; se adoptó `deepseek-ai/deepseek-v4-flash-0731` como principal — y más tarde, al detectarse que ese modelo empezó a colgarse sin responder NUNCA (no era 429, simplemente nunca contestaba) en pruebas repetidas en vivo, se invirtió el orden: `meta/llama-3.1-70b-instruct` pasó a ser el principal (respondía limpio en ~1s), `deepseek` quedó de respaldo.
4. **Crash de serialización JSON:** columnas `DATETIME`/`DECIMAL` de SQL Server rompían `json.dumps()` sin capturar, y Flask devolvía HTML de error 500 en vez de JSON — el frontend explotaba con `SyntaxError: Unexpected token '<'` (que el usuario inicialmente atribuyó a lentitud de deepseek, no a un crash). Fix en el origen (`_valor_json_seguro()`/`_fila_json_segura()` en `db_tools.py`) + un `@app.errorhandler(Exception)` global en `server.py` como respaldo, para que CUALQUIER excepción no capturada devuelva JSON, nunca HTML.
5. **Bug real de SQL Server:** la tool inyectaba `TOP N` aunque la consulta ya trajera `OFFSET...FETCH` (paginación) — SQL Server rechaza mezclar ambos ("A TOP can not be used in the same query or sub-query as a OFFSET"). Fix: no inyectar `TOP` si ya hay `OFFSET`.
6. **Loop quemando los 8 pasos sin avanzar:** si una tool fallaba, el modelo a veces reintentaba la MISMA llamada (mismo tool + mismos argumentos) hasta agotar el presupuesto completo, con un mensaje final genérico sin explicación. Fix: detección de repetición exacta (corta al toque, mensaje final incluye el último error real) + límite subido de 8 a 12 pasos.
7. **Sin esquema real de SQL Server:** el modelo adivinaba nombres de columna inexistentes (`descuento` cuando la columna real era `desc_glob`/`desc_ppago`) — causa raíz de varios fallos, incluido un "Incorrect syntax near '='" que en realidad era una consulta con un `WHERE` incompleto. Fix: nueva tool `listar_esquema_sql_server` (con o sin tabla puntual, vía `INFORMATION_SCHEMA.COLUMNS`), agregada al system prompt como paso obligatorio antes de adivinar.
8. **Tercera fuente de datos faltante:** no había forma de consultar el MySQL legacy (`192.168.4.148`, gestión real de preparación/chequeo/embalaje). Fix: `ejecutar_consulta_mysql_lectura` + `listar_esquema_mysql` (nuevas tools, pymysql) — el propio system prompt inicial documentaba nombres de columna ADIVINADOS (`numero`, `preparador`) que la tool de esquema reveló como incorrectos (el real es `cd_barr`, `num_prep`/`num_cheq`/`num_emb`); corregido contra el esquema real antes de darlo por bueno.
9. **Síntesis de skills nunca se disparaba para MySQL:** `_intentar_sintetizar_skill()` solo reconocía las dos tools originales (sqlite/sql_server) — al agregar la tool de MySQL nunca se actualizó esa lista, así que las preguntas resueltas contra MySQL jamás generaban skill, en silencio, sin ningún error. Fix: tercera rama agregada, con la ÚLTIMA tool exitosa del turno decidiendo la fuente.
10. **Bug de f-string en la generación de PHP:** el bloque de conexión (SQL Server y MySQL) usaba placeholders tipo `{PROFIT_SQL_HOST_PLACEHOLDER}` dentro de un f-string de Python — se evaluaban como variables reales (inexistentes, `NameError`) en vez de quedar como texto literal para el `.replace()` posterior. Fix: esos bloques pasan a ser strings normales (sin prefijo `f`).
11. **Consultas genuinamente incompletas llegando a la base real:** confirmado con la respuesta cruda de NIM (`finish_reason: "tool_calls"`, sin agotar tokens) que el modelo a veces termina la generación de una consulta justo después de un operador de comparación (`...WHERE tipo_doc = `, sin valor) — no es truncamiento de `max_tokens`, es una falla de generación del modelo en sí. Fix defensivo: `_es_select_seguro()` rechaza cualquier consulta que termine en un operador colgado (`=`, `AND`, `OR`, etc.) ANTES de ejecutarla contra la base real, con un mensaje que le explica al modelo qué le faltó.
12. **Ollama sacado de la cadena de respaldo** a pedido explícito del usuario, mientras la máquina no tenga hardware dedicado para correrlo en tiempos razonables (`qwen2.5-coder:7b` tardaba demasiado y solo sumaba espera antes del mensaje de error).
13. **Esquema visual de herramientas en el chat** (`ara_inteligente.html`, modo ARA Coder) — cada tool usada aparece como una tarjeta animada (ícono girando mientras "corre", resuelto en ✓/✕) antes de la respuesta final, imitando la vista de tool-calls de una terminal de agente.

**Verificación:** cada punto probado en vivo contra los sistemas reales (SQL Server, MySQL, NIM crudo) antes de darlo por corregido — varios de estos bugs se reprodujeron exactamente con la misma pregunta del usuario para confirmar la causa raíz antes de tocar código.

### v4.63 — Watchdog SQL: auth + auditoría de kills fallidos + conector DIP; vulnerabilidad real en ARA Warehouse corregida; puerto movido (2026-08-24)

**Pedido del usuario:** conectar el watchdog anti-bloqueo SQL (puerto 5005) y ARA Warehouse mismo a un panel externo llamado DIP, con niveles de alerta según severidad.

**Hallazgos reales antes de construir (investigación en el código real, no asumida):**
- `watchdog_dashboard_server.py` no tenía NINGUNA autenticación en sus endpoints.
- El caso más crítico a auditar — un candidato que el watchdog intentó matar pero el `KILL` no surtió efecto (proceso zombie que sigue vivo) — se calculaba en `sql_kill_switch.php` (`$kS_fallidos`) pero el wrapper (`watchdog_bloqueos_sql.ps1`) nunca lo escribía a ningún log: se perdía en silencio.
- `C:\DIP\dip.py` ya es un servicio real corriendo en la máquina (no un concepto por diseñar desde cero, como se asumió al principio).
- **Vulnerabilidad real en ARA Warehouse:** `/api/reportes/discrepancias`, `/api/reportes/trazabilidad` y `/api/rutas_hex/reporte-finalizadas` confiaban en `es_admin`/`usuario_activo` mandados por el CLIENTE como query params, sin ninguna verificación server-side — cualquiera podía mandar `es_admin=true` y ver los reportes de todos los operadores. El login (`POST /api/login`) tampoco emitía ningún token: comparaba la contraseña en texto plano y el frontend se limitaba a guardar la respuesta en `localStorage`.

**Fix:**
1. `bin/watchdog_bloqueos_sql.ps1` + `watchdog_dashboard_server.py` — los kills fallidos ahora se loguean y se exponen (`resumen.total_fallos_kill`, `fallos_kill[]`).
2. `watchdog_dashboard_server.py` — `X-API-Key` obligatorio en todo `/api/*` (`WATCHDOG_API_KEY`); el dashboard HTML propio se la inyecta solo.
3. `WatchdogSqlTool.php` (Auditoria, nuevo) — conector PHP hacia el watchdog, mismo patrón `FlaskApiTrait` (extendido para soportar headers).
4. **`auth_sesion.py`** (nuevo, `ara/ARA_Brain/`) — token de sesión firmado (JWT HS256, `ARA_SESSION_SECRET`) emitido por `/api/login` con el `rol`/`permisos` REALES leídos de la BD. `/api/reportes/discrepancias`, `/api/reportes/trazabilidad` y `/api/rutas_hex/reporte-finalizadas` ya NO confían en `es_admin` del cliente — lo derivan del token verificado server-side (mismo criterio que ya usaba el frontend: `rol==='admin' OR rol==='supervisor' OR permisos.includes('*')`, replicado exacto para no degradar a los supervisores). Excepción documentada: la consulta por `co_art` en trazabilidad es un lookup de solo lectura server-a-server (tool de supervisión), sin sesión de usuario de por medio — queda exenta a propósito. `DIP_SERVICE_KEY` (header `X-API-Key`) agregado como vía alterna para conectores server-a-server que no pueden loguearse como un operador humano.
5. **Puerto de `ara_server.py` movido de 5000 a 4050** (con paso intermedio por 5050) — hallazgo real: `PC-NVR.exe` (cliente de cámaras/DVR de la máquina, puertos 554/37777/5000) también tenía tomado el puerto 5000, causando que algunas peticiones HTTP terminaran en el proceso equivocado (que no entiende HTTP y nunca responde) — explicaba varios de los colgados intermitentes atribuidos erróneamente a Proton VPN. Puerto configurable vía `ARA_SERVER_PORT`; el frontend usa rutas relativas (`ARA_SERVER = ""`) así que no requirió ningún cambio; los Tools PHP ya leían `ARA_ERP_URL` como override, solo hizo falta setear esa variable.
6. **Watchdog de auto-recuperación** (`bin/watchdog_ara_server.ps1`, nuevo, se corre a mano — no como tarea programada, a pedido del usuario mientras el proyecto sigue en pruebas activas) — health-check HTTP real cada 30s (con `curl.exe`, no `Invoke-WebRequest`, que da falsos negativos contra Waitress); reinicia solo tras 2 fallos seguidos. Cubre un bug real de Waitress en Windows: el socket loopback interno ("trigger") puede morir a mitad de ejecución (probablemente por reconfiguración de red al togglear una VPN), dejando el proceso vivo pero incapaz de completar ninguna respuesta nunca más — no hay forma de repararlo sin reiniciar el proceso completo.

**Verificación:** cada endpoint probado en vivo con y sin la clave/token correspondiente (`200` vs `401`) tras cada cambio — incluida la confirmación explícita de que sin `X-API-Key`/token, `es_admin=true` en la URL ya NO alcanza para ver los reportes de todos. `php -l`/`py_compile` limpios en todos los archivos tocados.

### v4.64 — SSO del CRM extendido a ARA Warehouse (antes solo cubría ARA Intelligent) (2026-08-25)

**Pedido del usuario:** que ARA Warehouse deje de exigir su login propio cuando el usuario llega desde el CRM (CristMedicals), reutilizando el mismo secreto/mecanismo SSO ya construido en v4.60 para ARA Intelligent — sin romper el login propio, que queda como respaldo.

**Rol real del CRM verificado en vivo (192.168.4.23, MySQL, `root` sin contraseña — hallazgo de seguridad ya documentado, ver memoria `servidores-mysql-lan`), solo lectura:** tabla `pharma_erp.roles` — 12 roles reales, por departamento, no por nivel de acceso: `MERCADEO, COMPRAS, SUPERVICiON, VENTAS, TECNOLOGIA, AUDITORIA, OPERACIONES, VEHICULOS, MOTOS, FINANZAS, Administrador (is_system=1, "Acceso total al sistema"), GERENCIA ("Acceso a reportes y aprobaciones")`. Ninguno mapea 1 a 1 con el modelo de Warehouse (admin/supervisor/operario) — mapeo de negocio confirmado con el usuario: `Administrador` → `admin`; `SUPERVICiON`/`GERENCIA` → `supervisor`; el resto (roles departamentales) → `operario`. Comparación case-insensitive a propósito (`SUPERVICiON` tiene mayúsculas inconsistentes en la BD real). La sede (SC/BQTO) no vive en `roles` — confirmado con el usuario que ya viaja en `metadata.sede` del JWT del ERP, igual que el resto del payload SSO.

**Construido (`sso_erp_routes.py`):**
- `_mapear_rol_warehouse(roles_crm)` — aplica el mapeo de arriba.
- `_verificar_jwt_erp(token)` — helper compartido, extraído del `/auth/sso` original (v4.60) para no duplicar la verificación de firma/exp/iss en la nueva ruta.
- `GET /auth/sso/warehouse` (nuevo) — mismo JWT del ERP, pero mapea rol/sede a Warehouse y emite un token de sesión de Warehouse real (`auth_sesion.emitir_token_sesion`, el mismo mecanismo de v4.63) en vez de guardar el perfil crudo del CRM. Redirige a `/?sso=<código>`.
- `GET /api/sso/consumir_warehouse` (nuevo) — devuelve `{status, token, user:{id,nombre,rol,permisos,color,sede,isRouteResponsible}}`, la misma forma exacta que ya devuelve `/api/login`, para que el frontend no necesite saber si la sesión vino de SSO o de login propio.
- `sso_codigos_temp` gana columna `destino` (`'inteligente'` | `'warehouse'`, migración idempotente vía `ALTER TABLE` con `try/except OperationalError`) — evita que un código generado para un flujo se consuma por error en el otro (cada consumidor filtra por su propio `destino`); el flujo original de ARA Intelligent (`/auth/sso`, `/api/sso/consumir`) queda sin cambios de comportamiento, solo marcado explícito con `destino='inteligente'`.

**Frontend (`templates/index.html`):** el `DOMContentLoaded` ahora revisa `?sso=<código>` en la URL antes de intentar restaurar sesión de `localStorage`; si está, lo canjea (`consumirSsoWarehouse()`), limpia el parámetro de la URL de inmediato (`history.replaceState`, para que un F5 nunca reintente un código ya consumido de un solo uso) y entra directo al menú — mismos pasos que `login()` (guarda sesión, aplica permisos, arranca badges). Si no hay `?sso=`, cae al flujo de siempre (`restaurarSesionUsuario()` → pantalla de login). El login propio (`/api/login`, id+password) no se tocó — sigue siendo el respaldo si alguien entra directo sin pasar por el CRM.

**Verificación:** `python -c "import ast; ast.parse(...)"` limpio en `sso_erp_routes.py`; import real del módulo con el mismo `sys.path` que arma `ara_server.py` (proyecto raíz + `ARA_Brain`) — sin errores, confirma que no hay import circular con `auth_sesion`/`rutas.application.user_service`; `_mapear_rol_warehouse` probado en vivo con los 12 casos reales (`Administrador→admin`, `SUPERVICiON→supervisor`, `GERENCIA→supervisor`, roles departamentales→`operario`, lista vacía→`operario`, combinación `['VENTAS','Administrador']`→`admin`) — todos correctos. `node --check` limpio en el bloque JS nuevo de `index.html`.

**Despliegue real (misma noche):** se encontraron **4 procesos `ara_server.py` corriendo a la vez** (2 pares, arrancados en momentos distintos del día) — mismo patrón de proceso duplicado ya visto en la sesión (nadie mataba el anterior antes de relanzar manualmente, memoria `servicios-manuales-en-desarrollo`). Solo un PID tenía el puerto 4050 real; ese PID corría el código de ANTES de esta entrada, así que `/auth/sso/warehouse` daba 404 en vivo aunque el código ya estaba escrito y verificado. **Los 4 procesos se mataron y se relanzó uno limpio** (`bin/iniciar_ara_server.ps1`) — confirmado con `curl` real: `/auth/sso/warehouse` pasó de 404 a 302 (redirect correcto ante token inválido de prueba), `/api/health` 200, 1 sola instancia en el puerto 4050 tras el relanzamiento.

**Primera prueba real end-to-end (JWT real del CRM):** el canje SÍ funcionó (token consumido, sesión creada), pero el usuario reportó que el dominio raíz `ara.cristmedicals.com` (acceso "normal", no un botón específico) empezó a redirigir a ARA Warehouse en vez de a `/ara-inteligente` (el chat) — **causa confirmada: Auth Central tiene 2 slots de callback configurables, y el usuario había reapuntado el ÚNICO botón/entrada que antes usaba "ARA Intelligent" hacia `/auth/sso/warehouse`, en vez de crear una entrada NUEVA y separada para Warehouse.** No es un bug de este código — nada de `sso_erp_routes.py`/`index.html` se tocó para resolverlo. Corrección puramente de configuración del CRM, indicada al usuario: dejar el callback de "ARA Intelligent" en `/auth/sso` (como estaba) y usar el segundo slot, nuevo, para "ARA Warehouse" apuntando a `/auth/sso/warehouse` — dos destinos separados, no uno reemplazando al otro.

**Confirmación de arquitectura (usuario):** `portal.cristmedicals.com` (login "CristMedicals CRM v1.0", credenciales MasterProfit) **es el mismo Auth Central** que ya manda el JWT verificado en `sso_erp_routes.py` — coincide exactamente con `CRM_ORIGIN` ya hardcodeado en `ara_inteligente.html` desde v4.60. No hay un segundo sistema de login distinto: la terminología "CRM" del usuario y "ERP/Auth Central" del código son la misma cosa.

**Verificado (no había que construir nada):** "que cada usuario del CRM tenga su conversación aparte con el agente IA" ya funcionaba desde v4.39/v4.60 sin cambios adicionales — `ara_inteligente_routes.py::ara_inteligente_chats` filtra TODO server-side por `usuario_id` (`WHERE usuario_id = ?`, no es un filtro de cliente), y `usuarioId` en el frontend sale de `ara_sesion_usuario.id`, que para un login SSO ya es `sso_<employee_id>` (prefijo puesto a propósito en v4.60 para nunca chocar con los IDs numéricos del login local). Cada empleado real del CRM ya cae en su propio hilo aislado, automático.

**Gap real encontrado — SSO silencioso faltaba en Warehouse:** el usuario reportó que entrar directo a Warehouse (sin pasar por un link con `?sso=`) igual pedía login, o en algún caso mostraba el portal del CRM anidado — porque el mecanismo de "chequeo silencioso" (`intentarSsoSilencioso()`, `chequeoSilenciosoViaFetchDirecto()`/`chequeoSilenciosoViaIframe()` contra `portal.cristmedicals.com/api/central-auth/session-check` con `credentials:'include'`, construido para ARA Intelligent en v4.60/v4.63) nunca se portó a Warehouse — `index.html` solo sabía canjear un código `?sso=` que alguien más generaba, nunca preguntaba por su cuenta si ya había sesión activa en el portal.

**Fix — `templates/index.html`:** las mismas 3 funciones (`chequeoSilenciosoViaFetchDirecto`, `chequeoSilenciosoViaIframe`, y `intentarSsoSilenciosoWarehouse` — copia de `intentarSsoSilencioso()` con el único cambio real: redirige a `/auth/sso/warehouse?token=...` en vez de `/auth/sso?token=...`) portadas tal cual. `DOMContentLoaded` ahora sigue el mismo orden que `ara_inteligente.html`: 1) canjear `?sso=` si vino de un link, 2) restaurar sesión de `localStorage` si existe, 3) recién si ninguna de las dos aplica, intentar el chequeo silencioso contra el portal — solo si eso también falla se muestra el login propio (que sigue de respaldo, sin tocar).

**Despliegue:** Flask/Waitress no recarga templates solos fuera de modo debug — confirmado con `curl` que el HTML servido seguía sin la función nueva tras editar el archivo. Mismo proceso de relanzamiento limpio que la vez anterior (matar el par de PIDs actual, verificar puerto 4050 libre, `bin/iniciar_ara_server.ps1`) — confirmado con `curl` que el HTML servido ya trae `intentarSsoSilenciosoWarehouse`, 1 sola instancia en el puerto tras el relanzamiento.

**Diagnóstico en vivo del chequeo silencioso (con credenciales reales, solo lectura, autorizado explícitamente por el usuario):** sin navegador con JS disponible del lado de la IA, se replicó el flujo a nivel HTTP crudo (`curl`) para diagnosticar por qué `session-check` seguía fallando tras el relanzamiento:
- Login real contra `POST portal.cristmedicals.com/api/auth/login` (usuario `200`, rol `Administrador`) — **rate limit real detectado** (`Ratelimit-Limit: 30/900s` en el login, mensaje explícito "N intentos restantes" en cada 401): primer intento con clave mal transcrita (espacio faltante) devolvió 401, **se frenó de inmediato sin reintentar a ciegas** y se le pidió confirmación exacta al usuario antes de un segundo intento, para no arriesgar bloqueo de una cuenta real.
- Con la clave correcta: login 200 OK, cookie `cm_session` (JWT propio del CRM, con `Path` restringido exactamente a `/api/central-auth/session-check` — no es el mismo JWT que verifica `/auth/sso`, son dos tokens de propósitos distintos).
- `GET /api/central-auth/session-check?project_id=...` con esa cookie → `{"logueado":true}`, **sin campo `token`**, en dos llamadas idénticas (mismo `Etag`, con y sin header `Origin` de ARA) — descarta que fuera problema de CORS o de caché del lado nuestro; el endpoint real, en una consulta fresca y con sesión válida confirmada, no incluye el JWT que `chequeoSilenciosoViaFetchDirecto()` necesita para completar el pipeline.
- Conclusión: el contrato real de `session-check` no coincide con lo que asume el código (`data.logueado && data.token` en la misma respuesta) — sin documentación actualizada de Auth Central no se pudo determinar el parámetro faltante sin seguir probando a ciegas contra producción (mismo criterio de no explorar más allá de lo verificable sin arriesgar el rate limit real). Quedó pendiente para el administrador de Auth Central.
- Cookies/tokens capturados durante la prueba se borraron del disco al terminar (no quedó ninguna credencial de sesión real persistida).

**Corrección de rumbo (a pedido explícito del usuario, misma noche):** el chequeo silencioso portado arriba (`chequeoSilenciosoViaFetchDirecto`, `chequeoSilenciosoViaIframe`, `intentarSsoSilenciosoWarehouse`) **se revirtió por completo** de `templates/index.html` — el usuario decidió que el comportamiento automático/sin-click debe seguir siendo exclusivo de ARA Intelligent (como ya era desde v4.60), no de Warehouse. `index.html` vuelve a depender solo de: 1) canjear `?sso=<código>` si alguien clickeó un link real de "ARA Warehouse" (`/auth/sso/warehouse` sigue intacto del lado del server), 2) restaurar sesión de `localStorage`, 3) login propio como respaldo — sin ningún intento de adivinar/auto-tomar sesión del portal por su cuenta. Relanzado y confirmado con `curl` que el HTML servido ya no trae `intentarSsoSilenciosoWarehouse`.

**Pendiente:**
- Preguntarle a quien mantiene Auth Central qué parámetro exacto hace falta para que `session-check` devuelva `token` (hipótesis: ahora que hay 2 destinos registrados — ARA Intelligent y ARA Warehouse — puede necesitar un identificador de cuál de los dos pedís). No bloqueante para Warehouse tras el revert de este punto; sigue siendo relevante para el silencioso de ARA Intelligent si algún día empieza a fallar igual ahí.
- Confirmar con el usuario que, con los 2 slots de Auth Central corregidos (cada botón a su ruta), el flujo explícito de Warehouse (clic real → `?sso=` → sesión) funciona de punta a punta.

### v4.65 — Reloj del servidor del CRM desincronizado (causa raíz real del "expirado" persistente) + selector de departamento en ARA Intelligent + fix real de borrado de chats (2026-08-26)

**El misterio de `sso_error=expirado` resuelto con evidencia, no con suposición:** el usuario reportó que el SSO seguía fallando incluso reintentando rápido, sin pausar en DevTools. Comparación de 3 fuentes de hora reales, todas consultadas en el mismo instante:

| Fuente | Hora UTC |
|---|---|
| Reloj de Windows de la máquina donde corre ARA | 14:37 |
| Google (referencia externa) | 14:37 |
| Servidor de `portal.cristmedicals.com` (header `Date` real) | 08:38 |

El reloj de ARA coincide exacto con Google; el del CRM está **~6 horas atrasado**, de forma consistente en 3 mediciones separadas en minutos distintos (no es drift random, es un offset fijo — más compatible con una zona horaria mal etiquetada como UTC en el header `Date` que con un NTP desincronizado). Como el JWT del ERP vive 60s, cualquier token que emita el CRM con su reloj llega a ARA "vencido hace 6 horas" en el instante mismo de emitirse — no importaba la velocidad del usuario, era un problema de reloj, no de código, de caché, ni de CORS. Comunicado al administrador del CRM con la tabla de evidencia; tras un primer ajuste el desfase seguía igual (~5h58m, 3 chequeos de verificación) — pendiente de que el fix pegue en el servidor real (no solo en un reloj local).

**Una vez corregido:** el login del CRM funcionó de punta a punta — sesión de Warehouse armada en vivo (`Panel de Control`, `Op: TECNOLOGIA CRISTMEDICALS (operario)`). Pero el request real mostró `/auth/sso/warehouse?token=...` — confirma que el botón "CRIST.AI"/"ARA Intelligent" de Auth Central sigue apuntando al callback de Warehouse en vez del de ara-inteligente (mismo pendiente de configuración de los "2 slots" ya indicado antes, ahora confirmado con tráfico real en vez de teoría).

**Selector de departamento en ARA Intelligent (`templates/ara_inteligente.html`), a pedido del usuario:** nuevo `<select id="selector-departamento">` en la barra inferior (junto al selector de motor), opciones = los 5 departamentos reales de `ToolRegistry.php` (`Almacen, Auditoria, Compras, Despacho, Recepcion`; se excluyeron `Orquestador/Common/AutoGeneradas` del whitelist real por no ser departamentos de cara al usuario). Por defecto ("Sin departamento") no cambia nada — quedan las 4 sugerencias genéricas de siempre. Al elegir un departamento, `cargarCatalogoTools()` trae el catálogo real (`GET /api/tools/catalogo`, ya existente desde v4.4, cacheado en memoria tras la primera carga) y `actualizarSugerenciasPorDepartamento()` renderiza hasta 4 tools reales de ese departamento como chips (nombre humanizado como label, `data-prompt` con la descripción real de la tool para mandar un prompt útil al click, no el nombre técnico) — nunca tools inventadas.

**Bug real de borrado de chats encontrado (no específico de usuarios del CRM en el código — el usuario lo reportó así, pero la causa real afecta a cualquier usuario; documentado el hallazgo real igual):**
1. `ara_inteligente_routes.py::_conectar()` nunca activaba `PRAGMA foreign_keys = ON` — SQLite no enforça foreign keys por default, así que el `ON DELETE CASCADE` ya declarado en el esquema de `ara_inteligente_mensajes` nunca corría: borrar un chat dejaba sus mensajes huérfanos en la tabla (nunca visibles, pero nunca liberados).
2. El `DELETE /api/ara_inteligente/chats/<id>` no verificaba dueño (`usuario_id`) ni filas afectadas — cualquiera podía borrar el chat de cualquier otro usuario con solo el `id`, y si el `chat_id` no calzaba con ninguna fila (ej. lista desincronizada tras un F5), igual respondía `{"status":"success"}` sin borrar nada — el chat "reaparecía" al recargar sin ningún error visible, exactamente el síntoma reportado.

**Fix:** `PRAGMA foreign_keys = ON` agregado a `_conectar()`. El DELETE ahora exige `usuario_id` (400 si falta), borra con `WHERE id = ? AND usuario_id = ?`, y responde 404 explícito si no se borró ninguna fila (dueño equivocado o chat inexistente) — nunca más un `success` falso. `eliminarChat()` en el frontend manda `usuario_id` y ahora sí revisa la respuesta antes de refrescar la lista, avisando con `alert()` si falla en vez de tragárselo en silencio.

**Verificación:** `node --check` limpio en los 3 bloques JS nuevos/tocados (`preguntaSugerida`+sugerencias por departamento, `eliminarChat`); `python -c "ast.parse(...)"` limpio en `ara_inteligente_routes.py`. Relanzado limpio (mismo patrón de matar PIDs + `bin/iniciar_ara_server.ps1`), confirmado en vivo con `curl`: `selector-departamento` presente en el HTML servido, `GET /api/tools/catalogo` 200, `DELETE .../chats/999999` sin `usuario_id` → 400 `"Falta usuario_id"` (guard nuevo funcionando). 1 sola instancia en el puerto 4050 tras el relanzamiento.

**Pendiente:**
- Confirmar que el ajuste de reloj del CRM realmente pegó en el servidor real (última medición seguía ~5h58m atrasada).
- Corregir en Auth Central el callback de "CRIST.AI"/"ARA Intelligent" de `/auth/sso/warehouse` a `/auth/sso`.
- Confirmar con el usuario que el bug de borrado de chats desapareció específicamente para sus usuarios del CRM (la causa encontrada es general, no específica de CRM — si el síntoma persiste solo ahí después de este fix, hace falta reproducirlo con pasos exactos para seguir investigando qué es distinto en ese caso).

### v4.66 — ARA Coder inalcanzable para usuarios remotos del CRM: "localhost" del navegador no es "localhost" del server (2026-08-26)

**Reporte del usuario:** en modo "ARA Coder" (selector de motor), preguntar por deuda de un cliente devolvía `Error de conexión: TypeError: Failed to fetch` — interpretado inicialmente como que ARA Coder "no consulta el CRM", pero la causa real era de conectividad, nunca llegaba a ejecutar ninguna consulta.

**Causa raíz:** `ara_inteligente.html` tenía `ARA_CODER_URL = "http://localhost:8010"` hardcodeado, con el navegador llamando DIRECTO a esa URL — diseño documentado desde v4.61 a propósito ("corre con el Python global de la máquina... a propósito, para quedar desacoplado", "este modo únicamente funciona si quien lo usa está en la misma máquina donde corre el servicio"). Válido mientras solo se probaba desde la máquina de desarrollo; roto por diseño para cualquier usuario del CRM, cuyo navegador corre en SU PROPIA computadora — `localhost:8010` ahí no apunta a nada, nunca al server real donde vive ARA Coder.

**Fix — proxy servidor-a-servidor:** `ara_inteligente_routes.py` gana `POST /api/ara_inteligente/ara_coder_proxy` — reenvía el body tal cual a `http://127.0.0.1:8010/api/coder/chat` (ARA Coder bindea en `0.0.0.0:8010`, mismo host que `ara_server.py`, así que el proxy sí lo alcanza de verdad) con `timeout=120` (loop ReAct de hasta 12 pasos, v4.62) y devuelve la respuesta de ARA Coder tal cual (mismo `Content-Type`/status). `ara_inteligente.html` cambia su único fetch a ARA Coder para pasar por `${ARA_SERVER}/api/ara_inteligente/ara_coder_proxy` (mismo origen, sin CORS ni localhost del cliente de por medio) — se eliminó la constante `ARA_CODER_URL` ya sin uso.

**Verificación:** `python -c "ast.parse(...)"` limpio; `requests` confirmado instalado en el venv real de `ARA_Brain` (2.33.1, ya en `ara/requirements.txt`); `node --check` limpio en el `<script>` completo de `ara_inteligente.html` (no solo el bloque tocado, dado el historial de ediciones de la noche). Relanzado limpio, **probado en vivo de punta a punta**: `POST /api/ara_inteligente/ara_coder_proxy {"mensaje":"hola..."}` → `200 {"status":"success","respuesta":"Hola.","modelo":"DeepSeek",...}` — la cadena navegador→ARA Intelligent→ARA Coder funciona real, no solo en teoría. 1 sola instancia en el puerto 4050 tras el relanzamiento.

**Pendiente:**
- Confirmar con el usuario que, con la conectividad ya resuelta, la consulta real de deuda/facturas pendientes de un cliente responde con datos (no se tocó la lógica de negocio de ARA Coder en sí — si sigue sin mostrar nada con la conexión ya funcionando, el problema está en la lógica del agente/skills de ARA Coder, no en el transporte).

### v4.67 — "Automático" nunca reconocía preguntas de deuda; skill auto-generada por ARA Coder no enganchada a propósito (2026-08-26)

**Reporte del usuario:** con la conectividad de ARA Coder ya resuelta (v4.66), preguntó "que deuda tiene el cliente 31770848" en modo Automático — ARA Coder (vía su loop ReAct completo, 383s) SÍ encontró la deuda real y hasta generó una skill nueva (`obtener_deuda_cliente`, `Tools/AutoGeneradas/DeudaClienteTool.php`), pero el modo "Automático" (más rápido, sin pasar por el loop del agente) no la usaba ni encontraba nada para ese tipo de pregunta.

**Causa raíz:** `chat_routes.py::_detectar_intencion_skill()` es una lista de reglas **fija y curada a mano** ("33 tools" documentadas), no un descubrimiento dinámico contra el catálogo real de `ToolRegistry.php` — cualquier skill nueva (auto-generada o no) es invisible para "Automático" hasta que alguien agregue la regla explícita. La regla existente más cercana (#17, saldo/cartera de cliente → `consultar_saldo_cliente`) nunca incluyó la palabra "deuda" en su lista de raíces — ni ella ni ninguna otra regla la contenía, así que la frase nunca disparaba nada, con o sin la skill nueva de ARA Coder.

**Se evaluó enganchar la skill auto-generada directo, mismo criterio del diseño original (v4.61) — descartado por dos razones reales, no hipotéticas:**
1. `DeudaClienteTool.php` tiene la conexión a SQL Server con credenciales `profit`/`profit` **hardcodeadas** inline y **sin cerrar** (`$pdo` nunca se pone en `null`, sin `finally`) — exactamente el patrón anti-zombi que se vino corrigiendo toda la sesión en otros 8+ archivos (v4.18, v4.20, v4.64). Regenerarla no lo arregla: el problema es el patrón de generación de ARA Coder, no esta instancia puntual.
2. La skill exige `co_cli` exacto sin resolución de identificador (RIF/cédula/código parcial) — el propio comentario del archivo dice "NO editar a mano — se regenera sola", así que parchearla a mano se pierde en la próxima síntesis de ARA Coder.

**Fix aplicado — `chat_routes.py`, regla #17:** se agregó `'deuda'` a la lista de raíces de la regla existente (ya endurecida, ya resuelve RIF/cédula/código desde v4.17) en vez de cablear la skill nueva. `'debe'` sola NO se agregó a propósito (ya existe como frase compuesta "cuanto debe" en la misma regla) — como raíz suelta habría matcheado por prefijo "debería", "deber", etc. (`_raiz()` arma `debe\w*`), disparando la skill con preguntas que no son de deuda.

**Verificación real, no solo unitaria:** `POST /api/ara_inteligente/preguntar` con la pregunta exacta del usuario (chat de prueba descartable, `usuario_id=test_diag`, borrado al final con el fix de v4.65 ya confirmando su propio arreglo de paso) → `200`, `modelo: "Tool: consultar_saldo_cliente"`, **mismo cliente (JONAIBER QUIÑONEZ) y mismo total ($26.464,59) que había encontrado ARA Coder por su cuenta** — confirma consistencia entre los dos motores, no solo que "algo respondió". `python -c "ast.parse(...)"` limpio. Relanzado limpio, 1 sola instancia en el puerto 4050.

**Pendiente:**
- Decisión de diseño más amplia, no resuelta hoy: el mecanismo de auto-síntesis de ARA Coder (v4.61) asumía que sus skills nuevas quedarían disponibles para "Automático" solo por existir en `Tools/AutoGeneradas/` — en la práctica, `_detectar_intencion_skill()` nunca las va a encontrar sin una regla manual nueva por cada una. Si se quiere que el auto-descubrimiento sea real, hace falta o bien generar también la regla de intención (no solo el PHP), o cambiar `_detectar_intencion_skill()` por algo dinámico — ninguna de las dos se hizo hoy, se resolvió puntual el caso reportado.
- El patrón de credenciales hardcodeadas + sin cierre de conexión en las skills que sintetiza ARA Coder es genérico (afecta a cualquier skill futura, no solo `DeudaClienteTool`) — vale la pena revisar el generador de código en `C:\ara_coder_service\` para que emita el mismo patrón `ConnectionWrapper`/`finally{$pdo=null;gc_collect_cycles();}` que usa el resto del repo, en vez de arreglar cada skill generada una por una.

### v4.68 — Resuelto de raíz: catálogo dinámico de palabras de activación (ARA Coder → ARA Warehouse) + 2 bugs reales encontrados al probarlo por primera vez (2026-08-26)

**Pedido explícito del usuario:** que cuando ARA Coder cree una skill nueva, guarde sus propias palabras de activación en el catálogo, y que "Automático" las lea de ahí — resolviendo de raíz el pendiente dejado en v4.67 (cada skill nueva requería una regla manual). El usuario también pidió de forma explícita los candados anti-zombi en las consultas SQL generadas.

**Construido (`C:\ara_coder_service\core\agent_loop.py`):**
- `_pedir_palabras_activacion(pregunta, descripcion)` (nueva) — mismo patrón que `_pedir_nombre_descriptivo` (v4.62): un LLM call corto que pide 3-5 frases cortas en español, minúsculas, sin acentos. Se guardan en el catálogo como `palabras_activacion` de cada entrada nueva.
- Guard de nombre duplicado contra el propio catálogo (bug real encontrado de paso: `DeudaClienteTool`/id 15 y `ClienteDeudaTool`/id 16 — dos preguntas de deuda distintas generaron dos clases con nombre de archivo diferente pero el MISMO `getName()` — `obtener_deuda_cliente` × 2 — el chequeo de duplicado existente solo miraba `tool_registry.TOOLS_DISPONIBLES`, nunca el catálogo propio. Ahora bumpea con sufijo numérico si `nombre_funcion` ya existe en el catálogo).
- Candado anti-zombi en el template de código generado (`_generar_php_tool`): `$pdo = null;` antes del `try`, `finally { $pdo = null; gc_collect_cycles(); }` al final — mismo patrón que el resto del repo (v4.18/v4.20/v4.64), a pedido explícito del usuario ("que las consultas al sql tengan los candados de seguridad para no quedar huérfanos").

**Construido (`ara/ARA_Brain/chat_routes.py`):** `_detectar_intencion_skill_autogenerada(t)` (nueva) — fallback dinámico, se consulta como ÚLTIMO recurso después de agotar las reglas fijas existentes (para no arriesgar que una palabra de activación mal elegida por el LLM le gane a una regla ya probada). Lee `generated_skills/catalog.json` de ARA Coder directo del disco (mismo host, `ARA_CODER_CATALOGO_PATH` configurable por env). Solo considera entradas con `aprobada: true` (aprobación manual vía `/api/skills/approve` de ARA Coder — mismo criterio de v4.63, "ningún otro módulo debe confiar en una skill sin revisar"; le da un uso real por primera vez a ese flag, que hasta hoy nadie consultaba). Extracción de argumento genérica y conservadora: 0 parámetros dispara directo, 1 parámetro exige encontrar un número de 4-10 dígitos en el mensaje, 2+ parámetros se descarta (mismo criterio "mejor no disparar que disparar con datos incompletos" que ya regía las reglas fijas).

**Bug real encontrado al probarlo por primera vez en vivo (nunca antes se había ejecutado código de una skill auto-generada, porque nada las disparaba):** `new PDO(...)` sin calificar dentro de `namespace App\Services\NvidiaBrain\Tools\AutoGeneradas` — PHP busca la clase `PDO` DENTRO de ese namespace en vez de la global, error real: `Class "...\AutoGeneradas\PDO" not found`. Afectaba a las 17 skills ya generadas (dormidas hasta ahora, nunca ejecutadas) y a cualquier skill futura. Fix: `use PDO;` agregado al template de generación — mismo patrón que ya usan las tools manuales del repo (`ConciliarFacturaTool.php`, etc.).

**Verificación real de punta a punta, no solo unitaria:**
1. Skill de prueba generada vía ARA Coder real (`obtener_articulo_por_codigo_2`) → confirmado `palabras_activacion` poblado correctamente en el catálogo, y el guard de nombre duplicado funcionando (sufijo `_2` automático).
2. `php -l` limpio en el PHP generado; aprobada vía `POST /api/skills/approve`.
3. `POST /api/ara_inteligente/preguntar` con "consultar articulo 47259" (frase JAMÁS escrita en ninguna regla de `chat_routes.py`) → `200`, `modelo: "Tool: obtener_articulo_por_codigo_2"` — la skill se disparó SOLA, sin ninguna regla manual, exactamente el objetivo.
4. Primer intento reveló el bug de `PDO` sin calificar (`Class ... PDO not found`); tras el fix del generador + parche puntual del archivo de prueba, segundo intento → `200 {"ok":true,"filas":[],"total":0}` — ejecución limpia real contra PRUEB25 (0 filas porque el código de prueba no existe, esperado).
5. Artefactos de la prueba borrados al terminar (skill, entrada de catálogo, chats de prueba) — no quedó nada de diagnóstico en producción.

**Ambos servicios relanzados** (ARA Coder sin script propio — se lanzó directo con el mismo `python.exe`/cwd que ya tenía corriendo; `ara_server.py` con `bin/iniciar_ara_server.ps1`, mismo patrón de matar PIDs antes de relanzar). 1 sola instancia de cada uno confirmada en sus puertos (8010 y 4050) al final.

**A pedido del usuario, de paso:** timeout del proxy `ara_coder_proxy` (v4.66) subido de 120s a 180s — el loop ReAct completo puede tardar varios minutos en preguntas complejas (383s visto en v4.66/v4.67), 120s se quedaba corto para el caso real, no solo el de prueba.

**Pendiente:**
- Las 17 skills ya generadas ANTES de hoy no tienen `palabras_activacion` — no se les generó retroactivamente (el pedido era "cuando cree una tool nueva", hacia adelante). Si se quiere que las viejas también se auto-detecten, hay que correrles `_pedir_palabras_activacion` a mano o re-generarlas.
- El patrón de credenciales embebidas en el PHP generado (tomadas de env AL MOMENTO de generar, no releídas en runtime como el resto del repo) sigue sin resolver — si el password de PRUEB25 rota, cada skill ya generada queda con el valor viejo hasta regenerarse. No se tocó hoy (fuera del pedido puntual de activación + anti-zombi).

### v4.69 — Primera prueba real del catálogo dinámico: 2 causas concretas de por qué seguía fallando, ninguna era "Automático roto" (2026-08-26)

**Reporte del usuario, con instrucción explícita de no adivinar:** "traeme 5 clientes inactivos" en ARA Coder funcionó y generó `ClientesInactivosTool.php` (id 19, "pendiente de aprobación"). La MISMA pregunta en modo "Automático" (chat_routes.py) devolvió "No encontré productos..." — el usuario pidió diagnosticar con evidencia quién falló exactamente antes de tocar nada, dejando claro que si no había otro culpable, entonces era código.

**Causa #1 (diseño, no bug):** la entrada 19 del catálogo tenía `"aprobada": false` — el candado de aprobación manual (v4.63/v4.68, `/api/skills/approve`) funcionó exactamente como debía: "Automático" no confía en una skill sin revisar. Confirmado leyendo el JSON real, no asumido.

**Causa #2 (bug real, código de esta misma sesión — v4.68):** aunque se hubiera aprobado, la detección dinámica igual habría fallado. El único parámetro de esta skill es `inactivo` (el FLAG fijo que define qué es la skill, `=1`, no algo que el usuario menciona) — la heurística de extracción de v4.68 solo buscaba un número de 4-10 dígitos en el mensaje, y el único número en "traeme **5** clientes inactivos" es de 1 dígito (y además es un LIMIT, no el filtro). Nunca iba a matchear, aprobada o no.

**Fix — raíz real, no parche puntual:** `_parametrizar_query()` (`agent_loop.py`) ahora captura también `valor_original` (el literal con el que ARA Coder armó la consulta la primera vez) en cada parámetro del catálogo. `_detectar_intencion_skill_autogenerada()` (`chat_routes.py`) cae a ese valor cuando no encuentra un número que calce en el mensaje — mejor un default real (con el que la skill se probó y funcionó) que no disparar nada. Cubre exactamente la clase de skill que v4.68 no contemplaba: filtros estructurales/flags fijos, no solo identificadores que el usuario escribe.

**Bug de despliegue encontrado de paso:** `ClientesInactivosTool.php` se había generado en la ventana entre dos relanzamientos de ARA Coder de v4.68 — tenía el candado anti-zombi (`finally`) pero NO el `use PDO;` (el fix de namespace se aplicó y se relanzó unos minutos después). Parcheado puntual para la prueba; el generador ya emite ambos fixes juntos para cualquier skill nueva de ahora en adelante.

**Verificación real, reproduciendo la pregunta EXACTA del usuario:** entrada 19 actualizada con `valor_original: "1"` y `aprobada: true`, `ClientesInactivosTool.php` parcheado con `use PDO;`, `php -l` limpio. `POST /api/ara_inteligente/preguntar` con "traeme 5 clientes inactivos" (chat de prueba descartable) → `200`, `modelo: "Tool: obtener_clientes_inactivos"`, **los mismos 5 clientes exactos que ARA Coder había encontrado originalmente** (FAR01361, 10146415, 10150838, 10155915, 10166545). Ambos servicios relanzados limpio (matar PIDs + relanzar), 1 instancia de cada uno confirmada en sus puertos. Artefactos de prueba (chat) borrados al terminar.

### v4.70 — Skills auto-generadas devolvían JSON crudo al chat en vez de tarjeta (a pedido explícito del usuario) (2026-08-26)

**Reporte del usuario, con captura real:** la respuesta de `obtener_clientes_inactivos` (v4.69) llegaba al chat como el JSON crudo completo (`{"success":true,"tool":...,"resultado":{"content":"{\"ok\":true,\"filas\":[...]}"}}`) en vez de una tarjeta legible como el resto de las tools. Pidió que TODA respuesta, sin importar el motor, salga como tarjeta limpia y esquematizada.

**Causa:** `_ejecutar_skill_auto()` (`chat_routes.py`, sin cambios, ya preparado desde antes) ya sabe desenvolver `resultado.resultado.content` y usar `inner['card']` si existe — pero el template de código PHP que genera ARA Coder (`_generar_php_tool`) nunca construía ese campo `'card'`, solo devolvía `{'ok','filas','total'}` crudo. Sin `'card'`, cae al último recurso: volcar el JSON completo tal cual (`json.dumps(resultado)`). El propio docblock de `CardBuilder.php` ya documentaba la regla que se estaba incumpliendo: *"Nunca devuelve JSON crudo al usuario: execute() siempre expone 'card'."*

**Fix — en el generador, no parche por skill (`C:\ara_coder_service\core\agent_loop.py::_generar_php_tool`):**
- `use App\Services\NvidiaBrain\Tools\Common\CardBuilder;` + `require_once __DIR__ . '/../Common/CardBuilder.php';` agregados al template.
- Se probó primero `CardBuilder::resumir()` (helper genérico ya existente en el propio `CardBuilder.php`, pensado para payloads arbitrarios) — funcionó pero perdía información real: al recibir filas como arrays asociativos, solo mostraba el PRIMER campo de cada fila (`reset($sub)`) y truncaba a 3 de 5 resultados — se veían los códigos de cliente pero no los nombres.
- Reemplazado por una tabla real usando `CardBuilder::fila()` (ya existía, sin usar hasta hoy en ningún generador): fila de encabezados con `array_keys($filas[0])`, hasta 11 filas de datos completas (todas las columnas), aviso de "+N fila(s) más" si sobran, footer con la fuente real (`$fuente_footer`, variable que ya existía en el código desde v4.61 pero **nunca se había usado** en ningún lado del template hasta hoy).

**Verificación real:** `ClientesInactivosTool.php` (mismo archivo de prueba de v4.69) parcheado con el mismo patrón y reprobado con la pregunta exacta del usuario — la tarjeta final sale con header, sección "Resultados (5)", fila de encabezados (`co_cli | cli_des | inactivo`), las 5 filas completas con nombre real de cada cliente, separador y footer con la fuente — formato equivalente al de las tools manuales (`consultar_saldo_cliente`, etc.). `php -l` limpio, `python -c "ast.parse(...)"` limpio en `agent_loop.py`. Ambos servicios relanzados limpio, 1 instancia de cada uno confirmada en sus puertos (8010/4050) — el generador ya emite el `card` para cualquier skill nueva de ahora en adelante, sin tocar nada por fuera de `agent_loop.py`.

**Pendiente:** igual que v4.68, las skills generadas ANTES de este fix no tienen `card` en su `execute()` — si se quiere que las viejas también devuelvan tarjeta, hay que parchearlas a mano o esperar a que ARA Coder las regenere solo (pregunta similar hecha de nuevo).

### v4.71 — 3 bugs reales más en el catálogo dinámico, encontrados y debatidos ANTES de tocar código (2026-08-26)

**Contexto — el usuario puso una regla explícita para esta sesión:** tras el bug de v4.69/v4.70, pidió expresamente NO parchear cada síntoma a mano ("no quiero que lo hagas tú manual... resuelves ese problema pero al crear otro sigue pasando") — encontrar la causa raíz real y **debatir la solución antes de implementar**. Esta entrada documenta ese proceso: diagnóstico → propuesta → confirmación → fix, para cada uno de 3 bugs reales encontrados probando la skill `FacturaServiceTool` (id 20, "trae la ultima factura del cliente FAR01680", generada en v4.69 para un cliente FAR01361 distinto).

**Bug #1 — el literal de un `LIKE` nunca se parametrizaba:** `_parametrizar_query()` (`agent_loop.py`) solo reconocía comparaciones con `=` — una consulta con `co_cli LIKE '%FAR01361%'` no calzaba con esa regex, así que la skill "parametrizada" en realidad tenía el cliente de la pregunta original hardcodeado PARA SIEMPRE (`parametros: []`, `query_parametrizada` idéntica a `query_original`). Fix acordado y aplicado: `_RE_LIKE` (regex nueva) reconoce `columna LIKE 'patrón'`, guarda si el patrón tenía `%` al inicio/final (`like_prefijo`/`like_sufijo`) y arma `columna LIKE :columna`; el codegen PHP reconstruye el mismo patrón al bindear (`'%' . $valor . '%'`, etc.) — quien llama la skill solo aporta el término real, nunca los símbolos `%`.

**Bug #2 — las palabras de activación en plural nunca calzaban con mensajes en singular:** el usuario preguntó "trae la ultima **factura**" (singular); las `palabras_activacion` que generó el LLM eran todas plurales ("ultimas facturas", "facturas de cliente") — `_tiene()` exige la frase literal completa, y "ultimas" (7 letras) nunca puede matchear "ultima" (6 letras, más corta) por diseño de esa función. Fix acordado y aplicado: `_tiene_activacion()` (nueva, solo para este catálogo dinámico — `_tiene()`/`_raiz()` global NO se tocaron, para no arriesgar las reglas fijas ya probadas) singulariza cada palabra de la frase antes de armar el patrón, preservando el orden — la raíz más corta deja que el `\w*` de siempre cubra tanto singular como plural real.

**Bug #3 — el más serio: con la skill ya disparando, devolvía el CLIENTE EQUIVOCADO en silencio:** una vez arreglado #2, la skill sí se disparaba pero el único parámetro (`co_cli`) exigía un número puro (`\d{4,10}`) para extraerlo del mensaje — "FAR01680" es alfanumérico, nunca calzaba (sin límite de palabra entre letra y dígito), así que caía al `valor_original` de respaldo (`"FAR01361"`, el cliente que originó la skill) y respondía con datos reales pero de la persona equivocada, tarjeta limpia y creíble, sin ningún aviso de error. Confirmado en vivo antes de tocar nada: pregunta "facturas de cliente FAR01680" → repondió la factura de FAR01361. Fix acordado y aplicado en dos partes: (a) `_RE_IDENTIFICADOR` (regex nueva) reconoce códigos alfanuméricos (`[A-Za-z]{1,6}\d{3,10}`) además de números puros; (b) el respaldo a `valor_original` ahora se **prohíbe explícitamente** para parámetros que vienen de un `LIKE` (`like_prefijo`/`like_sufijo`) — esos son búsquedas reales que el usuario nombra cada vez, nunca un flag fijo; sin un valor nuevo en el mensaje, la skill simplemente no dispara (mejor no responder que mentir con datos de otra entidad). El respaldo sigue vivo solo para columnas de igualdad (`=`, ej. `inactivo=1`), donde reusar el valor original SÍ es correcto porque define qué es la skill.

**A pedido explícito del usuario, además:**
- Auto-aprobación ampliada: antes solo `len(parametros) <= 1`; ahora también se auto-aprueba si la consulta que originó la skill **ya trajo resultados reales** (`total_filas_originales > 0`, capturado del trace de la ejecución exitosa que disparó la síntesis) — no es lo mismo que "no tiró error SQL" (una consulta sin resultados también pasa eso). Con 2+ parámetros la extracción genérica igual nunca dispara sola (sigue exigiéndose 0 o 1 param para eso), así que ampliar este criterio no abre ningún riesgo nuevo de auto-disparo con datos incompletos.
- Timeout del proxy `ara_coder_proxy` subido de 180s a 220s (`ara_inteligente_routes.py`).

**Verificación real, reproduciendo la pregunta EXACTA del usuario (no una variante facilitada):** skill vieja rota (id 20, hardcodeada) borrada; regenerada desde cero con el generador ya arreglado → catálogo confirma `query_parametrizada` con `LIKE :co_cli` real, `like_prefijo`/`like_sufijo` ambos `true`, `aprobada: true` automático. `POST /api/ara_inteligente/preguntar` con "trae la ultima factura del cliente FAR01680" (chat de prueba descartable) → `200`, `modelo: "Tool: obtener_facturas_cliente"`, **5 facturas, TODAS con `co_cli: FAR01680`** (el cliente correcto, no el de entrenamiento). `python -c "ast.parse(...)"` limpio en `agent_loop.py`/`chat_routes.py`/`ara_inteligente_routes.py`. Ambos servicios relanzados limpio, 1 instancia de cada uno confirmada en sus puertos. Artefactos de prueba borrados al terminar.

**Pendiente:** las skills generadas antes de hoy (incluida la primera versión rota de `FacturaServiceTool`) no se revisaron una por una buscando el mismo patrón de `LIKE` hardcodeado — si alguna vieja llegara a aprobarse manualmente sin regenerarse, tendría el mismo problema del Bug #1.

### v4.72 — Animación de "procesando" (canvas de partículas) para Automático y ARA Coder (2026-08-26)

**Pedido del usuario:** reemplazar el indicador de espera (3 puntitos rebotando) por una animación tipo canvas de partículas conectadas — mandó un snippet de referencia (fondo oscuro, partículas cian con líneas de conexión). Pidió que aplique a los dos motores, "Automático" y "ARA Coder".

**Implementado (`templates/ara_inteligente.html`):** `mostrarEscribiendo()` — la función que ya arrancan AMBOS motores al enviar una pregunta (antes de la rama `if (modoSeleccionado === 'ara_coder')`), así que un solo cambio ahí cubre los dos sin duplicar nada — ahora crea un `<canvas>` en vez de los 3 puntitos, con `_iniciarCanvasPensando()` (nueva): 34 partículas, colores tomados de `--accent1/2/3` (los mismos que ya usaban los puntitos, no una paleta inventada) en vez del cian del snippet de referencia, para no desentonar con el resto de la interfaz. Se conserva el texto rotativo "pensando…" debajo, igual que antes.

**Candado anti-fuga (hallazgo propio, aplicado sin que hiciera falta que el usuario lo pidiera — mismo criterio anti-zombi de toda la sesión, esta vez del lado del navegador):** el snippet de referencia no tenía forma de parar el loop de `requestAnimationFrame` — cada canvas nuevo habría quedado animando en segundo plano para siempre, incluso después de que `quitarEscribiendo()` lo saca del DOM. Se agregó un chequeo (`if (!document.body.contains(canvas)) return;`) al inicio de cada frame: en cuanto el canvas deja de existir en el documento, el loop se detiene solo — sin temporizador ni bandera global que mantener.

**Verificación:** `node --check` limpio en el `<script>` completo del archivo (no solo el bloque nuevo). Relanzado limpio (mismo patrón de matar PIDs + relanzar), confirmado con `curl` que el HTML servido ya trae el canvas nuevo. 1 sola instancia de cada servicio en sus puertos al final.

**Ajuste el mismo día (a pedido del usuario, con captura real mostrando el problema):** la animación de partículas quedaba encapsulada en `.msg.bot` + `.burbuja` (avatar + tarjeta de chat con fondo/borde) — "se ve feo y no armoniza". Reemplazada por completo:
- Animación nueva: onda holográfica (4 capas de ondas senoidales moduladas por una envolvente, snippet provisto por el usuario — se completó la parte final que venía cortada: cierre del loop, `requestAnimationFrame`, medición inicial). Colores otra vez tomados de `--accent1/2/3` en vez del cian/índigo del snippet, por la misma razón de antes.
- `mostrarEscribiendo()` ya NO arma `.msg.bot > .msg-fila > .avatar + .burbuja` — el indicador (`#indicador-escribiendo`) es un `<div>` suelto directo en `#hilo`, sin clase de mensaje ni avatar; el canvas (200px alto, ancho completo) pinta su propio fondo oscuro cada frame, con el texto rotativo "pensando…" debajo, centrado, sin caja alrededor.
- Mismo candado anti-fuga aplicado de nuevo (`if (!document.body.contains(canvas)) return;`) — el snippet nuevo tampoco traía forma de parar el loop.
- Verificado: `node --check` limpio en el script completo; `curl` sobre el HTML servido confirma que `div.id = 'indicador-escribiendo'` ya no lleva `className`. Relanzado limpio, 1 instancia sana en el puerto 4050.

### v4.73 — Prioridad máxima: "Automático" caía a un mensaje falso ("no encontré productos") en preguntas que nunca fueron sobre productos — 3 causas reales encadenadas (2026-08-26)

**Directiva explícita del usuario:** "el problema no es que si valide o no valide la skill... configuremos bien y ahora quiero darle prioridad máxima hasta que no se arregle... no vamos a continuar." Se pausó todo lo demás hasta resolver esto de raíz.

**Causa #1 — el conector de `_tiene_activacion()` (v4.71) rompía justo lo que decía arreglar:** al descartar palabras cortas ("de") para armar el patrón de activación, el conector entre palabras significativas quedó exigiendo adyacencia exacta (`\s+`, cero holgura) — "nota **de** entrega" (mensaje real, con "de" en el medio) nunca calzaba contra el patrón "nota[espacio]entrega" que resultaba de haber descartado esa misma palabra. Confirmado en vivo con un script de diagnóstico aislado (`_tiene_activacion` devolvía `False` para "nota de entrega" contra la frase "nota de entrega") antes de tocar nada. Fix: el conector ahora tolera hasta 3 palabras de relleno entre cada palabra significativa (`(?:\s+\S+){0,3}?\s+`) — cubre tanto la palabra corta descartada como inserciones reales del usuario.

**Causa #2 — una skill (`NotaEntrega2345`, id 26) respondía a una pregunta distinta a la que decía responder:** ARA Coder sintetizó la skill a partir de la ÚLTIMA consulta de una cadena de varios pasos (factura → Profit → nota asociada → `gestion WHERE cd_barr = 414446`) — pero 414446 es el número de NOTA, no el de la factura 350580 que originó la pregunta. La skill quedó parametrizada sobre `cd_barr` (nota), mientras que el usuario siempre va a escribir un número de FACTURA — nunca iba a poder resolver una factura nueva. A pedido del usuario: **borrada del catálogo y del disco** en vez de reaprobarla o dejarla como referencia. (Quedó pendiente, sin resolver hoy: una señal barata para detectar este patrón en el generador — si `valor_original` no aparece literal en `pregunta_origen`, es señal de valor derivado — el usuario pidió posponer ese debate y enfocarse en la causa raíz de por qué "Automático" no respondía nada útil.)

**Causa #3 — la más grande, la que explica todo el síntoma real: NVIDIA dio de baja el modelo de NIM A MEDIA SESIÓN, hoy mismo:** diagnosticado en vivo (no asumido) con una llamada aislada a `_llamar_nim_ara_bot` — las 6 API keys del pool devolvían HTTP 410 con `meta/llama-3.1-8b-instruct`. Se probó cambiar al modelo que `ara_coder_service` tenía documentado como "probado y funcionando" (`meta/llama-3.1-70b-instruct`) — **también 410**, con el mensaje real de NVIDIA: *"The model 'meta/llama-3.1-70b-instruct' has reached its end of life on 2026-08-26T09:00:00Z and is no longer available."* Confirmado que afecta a los DOS servicios (`chat_routes.py` Y `agent_loop.py` de ARA Coder) — no es un bug de este repo, es un proveedor externo retirando modelos en tiempo real. El modelo de respaldo por NIM (`deepseek-v4-flash` vía el mismo gateway `integrate.api.nvidia.com`) también cuelga (25s de timeout, mismo problema ya documentado desde el 24/08 en ARA Coder) — sin nada más, cada mensaje sin skill exacta terminaba cayendo al fallback SQL crudo (que además mentía "no encontré PRODUCTOS" incluso en preguntas de facturas/notas, ver abajo).

**Fix de la causa #3 — `chat_routes.py` gana el mismo respaldo real que ya tenía ARA Coder, no un parche nuevo:** `_llamar_deepseek_para_bot()` (nueva) llama a la API OFICIAL de DeepSeek directo (`api.deepseek.com`, NO por el gateway de NIM que cuelga) — misma cuenta/key que `ara_coder_service/config.py` ya usa, confirmada con saldo real y respondiendo en vivo hoy. Insertada en la cascada entre NIM (ahora falla rápido, ~2.5s las 6 keys) y Ollama local (`phi3`, mucho más débil — `num_predict=50`, `num_ctx=512`) — así "Automático" tiene un modelo capaz de verdad como segunda opción real, no solo el modelo local limitado.

**De paso, mensaje de último recurso corregido:** `_formatear_fallback_sql()` (solo se usa si NIM, DeepSeek Y Ollama fallan los 3 — cada vez más raro) decía siempre "no encontré PRODUCTOS", incluso para preguntas de facturas/notas/clientes donde nunca se intentó ninguna búsqueda de inventario — mentía sobre lo que en realidad había pasado. Ahora es un mensaje genérico y honesto.

**Verificación real, reproduciendo la pregunta EXACTA que falló (no una variante facilitada):** "ahora quiero que busques la nota de entrega de esta factura 80007967" → antes: `modelo: "Búsqueda SQL directa"`, 26.7s, "No encontré productos que coincidan..." (falso). Después: `modelo: "DeepSeek"`, 11.4s, respuesta real y honesta reconociendo que no encontró la nota vinculada y ofreciendo alternativas. Regresión de las 2 skills que ya funcionaban (`consultar_saldo_cliente` para deuda, `obtener_facturas_cliente` para FAR01680) — **ambas siguen enganchando correctamente**, sin romper nada de lo ya arreglado hoy. `python -c "ast.parse(...)"` limpio. Relanzado limpio, 1 instancia de cada servicio confirmada en sus puertos. Chats de prueba borrados al terminar.

**Pendiente (explícitamente pospuesto por el usuario, no descartado):**
- La señal "valor_original no aparece en pregunta_origen" para detectar skills con parámetro derivado (causa #2) — no implementada hoy.
- Vigilar si NVIDIA agrega un modelo NIM válido nuevo al catálogo — mientras tanto, DeepSeek directo es el respaldo real tanto acá como en ARA Coder.

### v4.74 — El LLM de respaldo inventaba haber ejecutado una skill que nunca corrió (2026-08-26)

**Reporte del usuario, con la respuesta real:** con el fix de v4.73 ya funcionando (DeepSeek respondiendo en vez del fallback SQL crudo), la respuesta decía *"voy a ejecutar la skill correspondiente... **[Ejecutando /consultar_nota con parámetro: 80007967]**... No se encontró una nota de entrega asociada al número 80007967"* — el usuario notó que 80007967 es el número de FACTURA, no de nota, y sospechó que "no lee bien o no sabe cuál buscar".

**Confirmado en vivo, no asumido:** `_detectar_intencion_skill()` devuelve `None` para ese mensaje exacto — ninguna skill real se ejecutó jamás. Todo el texto sobre "ejecutando /consultar_nota" y su resultado negativo es **100% inventado por el LLM** — nunca hubo ninguna consulta real a `not_ent` ni a ninguna otra tabla.

**Causa raíz:** el punto 4 del `system_ctx` (prompt del sistema) instruía textualmente: *"ARA debe ejecutar la skill correspondiente... NUNCA respondas 'no tengo información'... sin intentar primero la skill correspondiente."* Esa instrucción tenía sentido en un contexto con tool-calling real, pero en esta rama el LLM NO tiene ninguna forma de ejecutar nada — solo genera texto, y la detección de skills YA se intentó (y falló) por código ANTES de siquiera llegar a esta llamada. Pedirle a un modelo sin herramientas reales que "nunca diga que no tiene información sin intentar la skill" garantiza que va a fingir haberlo intentado — es la única forma que tiene de "cumplir" una instrucción que no puede cumplir de verdad.

**Fix — reescrito el punto 4 del prompt:** ahora dice explícitamente que las skills automáticas YA se intentaron por código antes de esta respuesta, que si el LLM está generando el mensaje es porque NINGUNA coincidió, que nunca debe decir ni insinuar que "va a ejecutar"/"ejecutó" algo, y que si el CONTEXTO no trae el dato pedido debe decirlo con honestidad en vez de inventar un resultado (positivo o negativo).

**Verificación real, misma pregunta exacta:** → `modelo: "DeepSeek"`, respuesta ahora honesta: *"No tengo una forma automática de resolver eso todavía con los datos que tengo: en el contexto no aparece ninguna nota de entrega asociada a la factura 80007967... ¿Querés que busque por otro dato?"* — sin ninguna mención de haber ejecutado nada. `python -c "ast.parse(...)"` limpio. Relanzado limpio, 1 instancia sana en el puerto 4050. Chat de prueba borrado al terminar.

**Reporte aparte del usuario, sin confirmar todavía (pendiente):** "la animación no aparece" (la onda holográfica de v4.72). Revisado todo lo verificable server-side — HTML/JS servido en vivo confirmado correcto (sintaxis válida, sin duplicados de `mostrarEscribiendo`/`_iniciarCanvasPensando`, sin colisión de IDs, `#hilo` se desoculta antes de invocar el indicador, sin headers de caché del lado del server que expliquen una versión vieja server-side) — no se encontró causa server-side. Hipótesis más probable: caché del navegador (pestaña abierta desde antes de v4.72, nunca recargada) — se le pidió al usuario un hard-refresh (Ctrl+Shift+R) y, si persiste, detalles de en qué modo lo probó y qué ve exactamente en el espacio del indicador. Superado por el rediseño de v4.76 antes de que se confirmara si estaba resuelto.

### v4.75 — Red de seguridad contra literales sin parametrizar + borrado de 2 skills inseguras + backfill de 14 skills dormidas seguras (2026-08-26)

**Contexto:** revisando el catálogo completo de ARA Coder a pedido del usuario ("vamos a integrarlo. Eliminar las que están malas y las que sí sirven"), se encontraron 2 skills marcadas `aprobada: true` que en realidad tenían un `WHERE co_cli IN ('FAR00181', 'FAR00856', 'FAR00763')` con la lista de clientes literal, sin parametrizar — siempre devolvían el mismo subconjunto fijo de clientes sin importar la pregunta real. `_parametrizar_query()` (en `agent_loop.py` de ARA Coder) solo sabía reconocer columnas comparadas con `=` y (desde v4.69) `LIKE` — un `IN (...)` o un rango de fechas (`fec_emis >= '...'`) pasaban de largo intactos, y como el código interpretaba `parametros=[]` como "trivialmente segura, sin nada que llenar", quedaban auto-aprobadas con datos hardcodeados disfrazados de consulta genérica.

**Fix — red de seguridad general, no un parche puntual para `IN`:** nueva variable `tiene_literal_sin_parametrizar = bool(re.search(r"'[^']*'", query_parametrizada))` — después de correr todos los reemplazos conocidos (`=`, `LIKE`), si queda CUALQUIER literal entre comillas sin parametrizar (sea `IN`, rango de fecha, o un patrón futuro que todavía no se contempla explícitamente), la skill NO se auto-aprueba, sin importar cuántos parámetros reales tenga. Criterio final: `aprobada = (len(parametros) <= 1 or total_filas_originales > 0) and not tiene_literal_sin_parametrizar`.

**Borradas, a pedido explícito del usuario:** `ClienteFacturaPendienteTool.php` y `ClienteFacturaPendiente2Tool.php` (ids 24 y 25 del catálogo) — las dos con la lista de clientes hardcodeada descrita arriba, sin forma de arreglarse sin resintetizar la skill desde cero.

**Backfill de 14 skills dormidas ya seguras (ids 1, 3, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 18):** existían en el catálogo desde antes de esta sesión pero nunca habían pasado por el generador de hoy — les faltaba `use PDO;` (bug de namespace de v4.68/v4.69), el candado anti-zombi (`finally { $pdo = null; gc_collect_cycles(); }`), la salida en tarjeta vía `CardBuilder`, y `palabras_activacion` reales (las tenían vacías o ausentes, por lo que "Automático" nunca podía encontrarlas). Regeneradas una por una con el generador actual (`_generar_php_tool`) y re-consultadas al LLM por sus `palabras_activacion` (`_pedir_palabras_activacion`). Resultado: 14/14 regeneradas sin error de sintaxis PHP (`php -l` limpio en las 14), 13 quedaron `aprobada: true` automáticamente; la id 9 quedó `aprobada: false` porque tiene 2 parámetros reales y su consulta original no trajo filas — sigue en el catálogo con palabras de activación y el resto del hardening, pero no se auto-dispara sola hasta revisarla a mano.

**Verificación real:** `php -l` limpio en las 14 (vía PowerShell, `C:\tools\php\php.exe`). Inspección directa de `DeudaClienteTool.php` y `ArticuloServiceTool.php` confirma `use PDO;`, candado `finally`, y card de `CardBuilder`. Ambos servicios relanzados limpio (se encontraron 2 instancias viejas de `ara_server.py` corriendo a la vez desde antes — mismo síntoma de siempre, mismo fix: matar todos los PID y relanzar uno solo), 1 sola instancia LISTEN confirmada en 4050 y 8010. Prueba end-to-end de una skill YA existente (`consultar_saldo_cliente`, regla estática) confirma que sigue respondiendo bien — sin regresión. Prueba end-to-end de una skill SOLO del catálogo dinámico (`obtener_articulo_por_codigo`, backfilleada hoy) con "validar articulo FAR12345" → `modelo: "Tool: obtener_articulo_por_codigo"`, 144ms, tarjeta de `CardBuilder` con "Sin resultados" (código inventado, comportamiento esperado) — confirma que la detección dinámica de catálogo (v4.68) sí encuentra y ejecuta skills backfilleadas, extrae el identificador alfanumérico (v4.69) y responde con la tarjeta nueva (v4.70), todo en una sola pasada real.

**Pendiente:** la id 9 (2 parámetros, no auto-aprobada) queda para revisión manual si se quiere activar. El catálogo aún puede tener otras skills viejas, previas a hoy, con el mismo patrón `IN`/rango sin parametrizar que nunca se auditaron una por una — el chequeo nuevo (`tiene_literal_sin_parametrizar`) solo corre cuando una skill se sintetiza o regenera, no retroactivamente sobre todo el catálogo existente.

### v4.76 — Tercer rediseño de la animación de "procesando": transparente, compacta, 3 capas cian/índigo/violeta (2026-08-26)

**Pedido del usuario:** reemplazar la onda holográfica de v4.72 (200px, fondo oscuro propio) por una versión más liviana — mandó un snippet de referencia: transparente de verdad (sin fondo pintado, deja ver el fondo real del chat detrás), 60px de alto, 3 capas de onda (no 4) en cian/índigo/violeta (`rgba(0,242,254,0.95)`, `rgba(79,70,229,0.95)`, `rgba(157,78,221,0.95)`), con listener de `resize` para reajustar el canvas si cambia el tamaño de la ventana.

**Implementado (`templates/ara_inteligente.html`):** `#indicador-escribiendo canvas` bajó de `height: 200px` a `height: 60px`, con `background: transparent` explícito; se quitaron `border-radius`/`overflow: hidden` del contenedor (ya no hace falta recortar nada, no hay fondo propio que desborde). `_iniciarCanvasPensando()` reescrita: `ctx.clearRect()` en vez de `ctx.fillStyle + ctx.fillRect()` (transparencia real, no un rectángulo oscuro simulándola), 3 capas con los colores y parámetros exactos del snippet (`freq = 0.009 + i*0.003`, `amp = 18 - i*3`, `offset = i*(Math.PI/3)`).

**Dos correcciones silenciosas sobre el snippet del usuario, mismo criterio anti-fuga de siempre:**
- El snippet llamaba `ctx.scale(dpr, dpr)` dentro de la función de resize — como `scale()` es acumulativo (no absoluto), cada `resize` sucesivo iba a multiplicar la escala sobre la anterior en vez de fijarla, deformando el canvas más y más con cada cambio de tamaño de ventana. Cambiado a `ctx.setTransform(dpr, 0, 0, dpr, 0, 0)` (ya usado en la versión anterior), que fija la escala en vez de acumularla.
- El snippet agregaba el listener de `resize` a `window` sin nunca quitarlo — cada mensaje nuevo (`mostrarEscribiendo()` se llama una vez por pregunta) habría dejado un listener más acumulado en `window` para siempre, incluso después de que el canvas ya se sacó del DOM. Se agregó el mismo chequeo anti-zombi ya usado en el loop de dibujo (`if (!document.body.contains(canvas))`) al inicio de la función de resize: si el canvas ya no existe, se auto-remueve (`window.removeEventListener('resize', resize)`) antes de salir.

**Verificación:** `node --check` limpio en el `<script>` completo. `curl` sobre el HTML servido en vivo confirma la presencia de `rgba(0, 242, 254` y `height: 60px`. Relanzado limpio junto con el resto de los cambios de esta ronda (v4.75), 1 sola instancia sana en el puerto 4050.

### v4.77 — Skills auto-generadas con 2+ parámetros del MISMO valor (ej. "id o número") nunca disparaban en Automático (2026-08-26)

**Reporte del usuario, con captura real:** preguntó a ARA Coder "buscame quien es el operador numero 22 o id 22" → respondió bien y generó una skill nueva (`BuscarOperadorTool.php`, "ya activa para Automático"). Acto seguido probó "buscar operador 22" directo en Automático → cayó al genérico "No encontré información sobre el operador 22... mi base de datos solo contiene información de productos y stock" (falso: la skill sí existía y las palabras de activación calzaban exacto con el mensaje).

**Causa raíz confirmada en el catálogo (`generated_skills/catalog.json`, id 26):** la consulta que ARA Coder sintetizó fue `WHERE id = 22 OR numero = '22'` — un mismo valor de búsqueda (22) repetido en dos columnas, que `_parametrizar_query()` parametrizó correctamente como DOS parámetros distintos (`id`, `numero`), ambos con el mismo `valor_original: "22"`. `_detectar_intencion_skill_autogenerada()` (`chat_routes.py`), sin embargo, tenía una regla explícita para descartar cualquier skill de 2 o más parámetros ("no tiene forma genérica confiable de completarse"), sin distinguir este caso: la skill jamás iba a disparar sola, aunque `palabras_activacion` calzara perfecto.

**Fix:** cuando una skill tiene 2+ parámetros, ahora se revisa si TODOS comparten el mismo `valor_original` — si es así, no son N datos distintos que haga falta pedirle al usuario, es UN dato repetido en varias columnas (patrón "buscar por id o por código", común). En ese caso se extrae un identificador nuevo del mensaje (mismo `_RE_IDENTIFICADOR` del caso de 1 parámetro) y se usa para llenar TODOS los parámetros a la vez; sin identificador nuevo, cae al `valor_original` compartido (mismo criterio ya usado para 1 parámetro, con la misma excepción de nunca hacerlo si algún parámetro viene de un `LIKE`). Si los parámetros tienen valores de entrenamiento DISTINTOS entre sí (un caso genuino de múltiples datos independientes, ej. rango de fechas), se sigue descartando igual que antes — no cambia nada ahí.

**Verificación real, misma pregunta exacta que falló:** "buscar operador 22" (chat de prueba descartable) → `200`, `modelo: "Tool: buscar_operador"`, tarjeta con 2 filas (ID 22 = LUIS AGUILAR, número 22 = SANDER PEREZ) — el mismo resultado que había dado ARA Coder manualmente. Regresión: "cuanto debe el cliente FAR00181" sigue enganchando `consultar_saldo_cliente` sin cambios. `python -c "ast.parse(...)"` limpio. Relanzado limpio, 1 instancia sana en el puerto 4050. Chats de prueba borrados al terminar.

### v4.78 — ARA Warehouse: HTTP 401 silencioso en Reportes/Trazabilidad tras 12h de sesión abierta (2026-08-28)

**Reporte del usuario, con captura real:** el módulo "Reportes y Estadísticas → Logs de Relocalización/Rutas" mostraba `Error: HTTP 401` al filtrar, con la consola marcando `Failed to load resource ... status of 401` en `/api/reportes/trazabilidad` — pese a que la sesión se veía activa ("Sesión restaurada automáticamente: JONAIBER QUIÑONEZ", permisos aplicados, menú funcionando normal).

**Descartado primero, en vivo, antes de tocar nada:** se armó un script de diagnóstico que importa `auth_sesion.py` en un proceso nuevo (mismo cwd/venv que usa el server) — el secreto `ARA_SESSION_SECRET` SÍ está configurado (64 caracteres) y un token acuñado en el momento se verifica correctamente en el mismo proceso. Más aún: se acuñó un token nuevo y se llamó al servidor REAL en vivo con `curl` → `200 OK`. Esto descarta de raíz cualquier problema de configuración del server (secreto ausente, issuer roto, endpoint caído) — el mecanismo de JWT de sesión funciona perfecto hoy.

**Causa raíz real:** el token de sesión (`auth_sesion.py`, agregado el 24/08 junto con el candado RBAC de estos endpoints) vence a las 12 horas (`SESION_TTL_SEGUNDOS`). Pero `restaurarSesionUsuario()` (`index.html`) — la función que salta directo al menú en cada F5 leyendo `localStorage` — NUNCA revisa si ese token venció; solo lee el perfil (`ara_sesion_usuario`, sin fecha de vencimiento) y listo. Resultado: una sesión que queda abierta más de un turno (12h+) sigue "viéndose" logueada — menú, permisos, todo renderiza bien porque nada de eso depende del token — pero CUALQUIER endpoint que sí lo verifica (`/api/reportes/trazabilidad`, `/api/reportes/discrepancias`, `/api/rutas_hex/reporte-finalizadas`) empieza a devolver 401 en silencio, sin que el usuario tenga ninguna pista de qué pasó ni cómo arreglarlo (más allá de adivinar que hay que desloguearse a mano).

**Hallazgo adicional durante la revisión (no reportado por el usuario, mismo patrón, mismo archivo):** el header `Authorization: Bearer ...` estaba duplicado idéntico en 5 lugares distintos de `index.html` (los 3 de `/api/rutas_hex/reporte-finalizadas` ni siquiera chequeaban `res.ok` — un 401 ahí caía silencioso a "No hay sub-rutas finalizadas", mintiendo sobre la causa real).

**Fix — un solo lugar, no 5 parches:** dos funciones nuevas junto a `logout()`: `manejarSesionExpirada()` (limpia `ara_token`/`ara_sesion_usuario` y manda al login con un aviso claro — automática, sin `confirm()`, porque la decide el server, no el usuario) y `fetchConSesion(url, opciones)` (agrega el Bearer una sola vez y detecta el 401 ahí mismo). Los 5 call sites (`renderReportesRutaHex`, `_rutaHexImprimir`, `_rutaHexImprimirSeleccion`, trazabilidad y discrepancias en `aplicarFiltrosHistorial`) ahora usan `fetchConSesion(url)` en vez de repetir el header a mano.

**Verificación:** `node --check` limpio en el `<script>` completo de `index.html`. `curl` sobre el HTML servido en vivo confirma `function fetchConSesion` y `function manejarSesionExpirada` presentes. Relanzado limpio, 1 instancia sana en el puerto 4050.

**Pendiente/nota para el usuario:** el fix hace que la próxima vez que esto pase, el sistema mande solo al login con un aviso claro en vez de un error críptico — no cambia el TTL de 12h en sí (queda igual, a propósito, es la ventana de turno). Si preferís una sesión que dure más (varios días sin re-loguear) o un refresco automático del token en vez de forzar login de nuevo, es un cambio de criterio a discutir, no algo que se decidió acá.

### v4.79 — Picking: la secuencia guiada de Kokoro TTS narraba la hoja completa por temporizador, sin esperar a que el usuario terminara cada artículo (2026-08-28)

**Reporte del usuario:** "en el modulo de piking kokoro tts no es muy preciso en dictado pq dicta la hoja completa... debemos dictar hasta que el usuario pase al otro articulo pq te dicta uno y va con el otro aunque uno no haya sido completado."

**Causa raíz confirmada leyendo el código:** `_siguienteEnSecuencia()` (`index.html`, secuencia "🎙️ COMENZAR PICKING") narraba el artículo actual y armaba `setTimeout(_siguienteEnSecuencia, 9000)` — avanzaba al siguiente SIEMPRE a los 9 segundos exactos, sin ninguna relación con si el usuario ya había escaneado/confirmado ese artículo o no. Un artículo que tardara más de 9s (buscarlo en el estante, cantidad múltiple, etc.) quedaba narrado y abandonado mientras Kokoro seguía leyendo el resto de la hoja.

**Fix — la secuencia deja de depender del tiempo, pasa a depender de eventos reales de surtido:**
- `_siguienteEnSecuencia()` ya NO arma ningún temporizador de avance automático — narra el artículo actual y espera.
- Nueva `_avanzarSecuenciaSiCorresponde(item)`, llamada desde los 4 puntos donde el código ya confirma un surtido real (`procesarPickingScan` — escaneo físico, `procesarPicking` — teclado virtual, `confirmarCantidadManual`, `confirmarCantidadItem` — cantidad manual por tarjeta): si el artículo recién completado es justo el que la secuencia está narrando en ese momento Y ya alcanzó la cantidad requerida, recién ahí dispara el siguiente (con 1.4s de margen para no pisar el audio de "Artículo completado" con la narración del que sigue). Si el usuario completa un artículo DISTINTO al que se está narrando (fuera de orden), la secuencia no se mueve — sigue esperando el actual, tal como pidió el usuario.
- Nueva `_itemYaCompleto(item)` (helper compartido) y skip automático en `_siguienteEnSecuencia()`: si al avanzar el siguiente artículo de la cola YA estaba completo (el usuario lo resolvió fuera de orden mientras la secuencia esperaba otro), lo salta en vez de narrarlo de nuevo innecesariamente.
- Nueva `_reaplicarResaltoSecuencia()`, llamada al final de `renderPickingList()`: el resalto visual (borde azul) del artículo que se está narrando se perdía en cada re-render de la lista (que reconstruye todo el HTML en cada escaneo) porque solo se aplicaba una vez, al narrar — ahora se reaplica después de cada render mientras la secuencia sigue activa.

**Verificación:** `node --check` limpio en el `<script>` completo. `curl` sobre el HTML servido en vivo confirma `_avanzarSecuenciaSiCorresponde` presente y el viejo `setTimeout(_siguienteEnSecuencia, 9000)` ya ausente. Relanzado limpio, 1 instancia sana en el puerto 4050.

**Pendiente:** verificación real en el navegador con un pedido de picking real (requiere escanear/confirmar artículos físicamente) — no se probó end-to-end con un escaneo real todavía, solo se confirmó la lógica y la sintaxis.

### v4.80 — GB10 se muda a repo propio: `C:\PROYECTOS\GDX SPARK GB10` (2026-08-31)

**Pedido del usuario:** la GDX Spark llega el lunes; el trabajo sobre esa estación (interfaz de control propia, servicios en tiempo real, monitoreo de conexiones) ya no encaja como subcarpeta de `ARA_PROYECT` — pidió mudar `gb10/` a un repo propio en `C:\PROYECTOS\GDX SPARK GB10`, reorganizado en la estructura de 8 carpetas que ya había armado (`APIS/APP/CONFIG/DATA/DLLS/PY_CACHE/SERVICIOS/TEMPLATES`).

**Decisión consultada al usuario:** ¿versionar el repo nuevo con git desde el día uno, o dejarlo sin versionar por ahora? Eligió iniciar git ahí mismo — código que va a controlar hardware real necesita historial desde el arranque.

**Ejecutado:**
- `git init` local en `C:\PROYECTOS\GDX SPARK GB10` (sin remoto, no se pidió push).
- Los 13 archivos reales de `gb10/` (scripts, docs, `.env.gb10.example`, `requirements_gb10.txt`) movidos y reorganizados: `SERVICIOS/` (hardware_detect, init_ara_llm, ambos mirrors, sync_cron, validate_3capas, stress_test), `APIS/token_auth.py`, `APP/inference/` (engine_config.py + README), `CONFIG/.env.example` (renombrado desde `.env.gb10.example`), `requirements.txt` (renombrado desde `requirements_gb10.txt`, ahora standalone — se le agregaron Flask/requests/pyodbc que antes venían de `ara/requirements.txt`), y 3 docs (`README.md`, `PLAN_MODELOS_FP4_ARA_CODER.md`, `POSTGRES_MIGRACION.md`) en la raíz del repo nuevo. `__pycache__/` NO se copió (bytecode, se regenera solo).
- Los 5 scripts que llamaban `load_dotenv(".env.gb10")` (ruta relativa, dependiente del cwd) corregidos a resolver `CONFIG/.env` como ruta absoluta desde la ubicación del propio script (`Path(__file__).resolve().parent.parent`) — con la reorganización de carpetas, la ruta relativa vieja ya no habría encontrado el archivo. Mismo fix para el default de `LLM_DB_PATH` (antes `./ara_llm.db`, ahora resuelve `DATA/ara_llm.db` absoluto).
- Todas las referencias cruzadas en docs/comentarios (`gb10/requirements_gb10.txt` → `requirements.txt`, `gb10/auth/` → `APIS/`, `gb10/inference/` → `APP/inference/`, `.env.gb10` → `CONFIG/.env`) actualizadas — 8 archivos tocados.
- **`ara_server.py::_estado_gb10()` (ARA_PROYECT) desacoplado:** tenía un `from gb10.inference.engine_config import MODEL_ROUTES` — import de Python ENTRE los dos repos, que dejó de ser válido al separar los proyectos (habría roto silenciosamente `/api/health`, aunque ya estaba envuelto en `try/except` así que no habría tumbado el server, solo degradado el reporte). Reescrito para leer únicamente `VISION_INFERENCE_URL`/`REASONING_INFERENCE_URL`/`AUDIO_INFERENCE_URL` por env — cero dependencia de la estructura interna del otro repo. Si en el futuro hace falta más detalle, debe pedirse por HTTP a la interfaz de control del repo nuevo, nunca por import.
- `gb10/` (ahora vacía) eliminada de `ARA_PROYECT`; `git status` la muestra como archivos borrados, sin commitear (no se commiteó nada en `ARA_PROYECT` — solo se hizo el commit inicial en el repo nuevo, que sí lo pidió el usuario al elegir versionarlo desde ya).
- Carpetas antes vacías (`DATA/`, `DLLS/`, `TEMPLATES/`, `PY_CACHE/`) documentadas con un `README.md` corto cada una explicando su propósito, más `.gitignore` del repo nuevo (excluye `CONFIG/.env` real, `__pycache__`, contenido runtime de `DATA/`).

**Verificación real:** `py_compile`/`ast.parse` limpio en los 9 `.py` movidos y en `ara_server.py` tras el desacople. `ara_server.py` relanzado limpio; `curl /api/health` en vivo confirma que el campo `gb10` sigue respondiendo bien (`no_configurado` para los 3, como antes) sin el import cruzado. Commit inicial creado en el repo nuevo (`ec9a81a`, 21 archivos).

**Pendiente (explícito, no se construyó hoy):** el "cuerpo" de la interfaz propia de control (`APP/` + `TEMPLATES/`) — el usuario quiere una gestión más estructurada y controlada que la interfaz web local que ya trae la GDX Spark de fábrica, pero el detalle de qué construir primero (panel de estado de las 3 capas, qué servicios están vivos, auth vía `APIS/token_auth.py`, etc.) no se definió todavía. `DLLS/` también queda vacía a propósito — sin la GDX físicamente en sitio no hay nada real que ajustar ahí.

**Continuación el mismo día:** el cuerpo de la interfaz de control se construyó — 4 paneles (tráfico HTTP/HTTPS, consumo de tokens/tiempos + memoria por programa, capacidad de LLMs/agentes, GPU/VRAM en vivo), probados en vivo end-to-end. Detalle completo del historial de esa parte queda documentado en el propio repo nuevo (`C:\PROYECTOS\GDX SPARK GB10\README.md`), no acá — este archivo (`PROJECT_CONTEXT.md`) sigue siendo el changelog de `ARA_PROYECT`, y GB10 ya dejó de vivir acá.

**Segunda continuación el mismo día:** el usuario mandó un mandato técnico para la interfaz de USUARIO (chat streaming, ARA Coder, DIP/OCR) — distinta del panel de monitoreo de arriba. Antes de construir, se consultaron 2 decisiones con costo real de deshacer (framework frontend, lenguaje del proxy) — el usuario eligió las opciones recomendadas: Svelte+Vite y FastAPI+Python. Se construyeron `FRONTEND/` y `PROXY/` (Hermes Router) en el repo nuevo, con 2 bugs reales encontrados y corregidos probando en vivo (bundle de Monaco sin lazy-load, y un falso positivo de "vLLM alcanzable" contra cualquier servicio en el puerto). Detalle completo en `C:\PROYECTOS\GDX SPARK GB10\README.md`.

**Tercera continuación el mismo día:** se instaló Playwright+Chromium y se verificó la interfaz en navegador real (streaming visible, diff viewer con Monaco renderizando, 0 errores de consola) — guardado como `npm run test:smoke`, reutilizable. Después, al configurar el proxy: se encontró que el diseño original ("esquema de BD por rol") no aplica — PRUEB25 verificado en vivo tiene un solo esquema SQL (`dbo`). Corregido a espejar la matriz RBAC real que ya usa `ARA_PROYECT` en producción (`RolePermissionManager.php`: roles ALMACEN/VENTAS/LOGISTICA/FINANZAS/TECNOLOGIA/CLIENTE con sus tools permitidos), probado en vivo end-to-end.

**Cuarta continuación el mismo día:** el usuario mandó un mockup HTML completo ("NVIDIA Founders Edition Studio") pidiendo adaptarlo y conectarlo a datos reales donde se pudiera. Rediseño completo del frontend en componentes Svelte reales (header de telemetría, sidebar, 4 tabs, selector de temas), conectado a los backends ya construidos — GPU real (se extendió `hardware_detect.py` con potencia/reloj/ventilador), tok/s y TTFT medidos de verdad del stream, sin ningún `alert()` ni dato inventado. Tailwind y las fuentes pasaron a self-hosted (nada de CDN — la GDX es una estación edge sin internet garantizado). Probado en navegador real con Playwright, 0 errores de consola. Detalle completo en `C:\PROYECTOS\GDX SPARK GB10\README.md`.

**Quinta continuación el mismo día:** pidió una pestaña de Benchmarks (rendimiento por consulta + cola/flujo en tiempo real). Encontrado antes de construirla: el tráfico de chat/visión no se estaba registrando en ningún lado (el hook existía, nadie lo llamaba) y no había cola real — cualquier cantidad de peticiones corría sin límite. Se agregó una cola real (semáforo asyncio, `PROXY/cola.py`) que limita concurrencia de verdad, y se conectó el registro real de cada consulta (tokens, duración, éxito/fallo). Probado con 6 peticiones concurrentes contra un cupo de 4: la cola real mostró 4 en ejecución / 2 esperando a mitad de camino.

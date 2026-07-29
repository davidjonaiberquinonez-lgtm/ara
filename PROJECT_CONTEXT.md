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

### 🔜 Pendientes (post-v2.0)

### 🔜 Pendientes (post-Fase 2)

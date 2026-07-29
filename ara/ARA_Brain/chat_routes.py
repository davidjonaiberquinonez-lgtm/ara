# -*- coding: utf-8 -*-
"""
Módulo de BANDEJA DE MENSAJES — Sistema ARA

Endpointsbajo el prefijo /api/chat/:
    GET  /api/chat/conversaciones                        -> lista chats ordenados
    GET  /api/chat/conversacion/<int:conv_id>/mensajes   -> historial paginado
    POST /api/chat/enviar                                -> envía msg de agente
    POST /api/chat/webhook                               -> webhook gateway WA/TG
    POST /api/chat/conversacion/<int:conv_id>/leer       -> marca no-leídos=0
    GET  /api/chat/poll?since=<ts>&conv_id=<id>           -> long-poll ligero
"""
import os
import time
import json
import sqlite3
import traceback
import requests
import threading
from datetime import datetime
from threading import Lock

from flask import request, jsonify, g, Response


# =============================================================================
# CONFIGURACIÓN DE BASE DE DATOS
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, 'data', 'proyecto_ara.db')

# Lock para evitar concurrencia en operaciones de escritura
_DB_LOCK = Lock()

# =============================================================================
# CONFIGURACIÓN DEL ASISTENTE ARA - Intelligent
# =============================================================================
ARA_BOT_TELEFONO = 'ara_bot'
ARA_BOT_NOMBRE   = 'ARA - Intelligent'

# NVIDIA NIM (Cloud) — descomenta y asigna tu API key
NVIDIA_API_KEY = "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"
NVIDIA_NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL    = "deepseek-ai/deepseek-v4-flash"

# Ollama local (fallback si NVIDIA NIM no está disponible)
OLLAMA_URL       = "http://127.0.0.1:11434/api/generate"


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _err(msg: str, code: int = 400):
    return jsonify({"status": "error", "mensaje": msg}), code


def _ok(payload: dict, code: int = 200):
    payload.setdefault("status", "success")
    return jsonify(payload), code


# =============================================================================
# INICIALIZACIÓN DE TABLAS (idempotente)
# =============================================================================
def init_chat_tables():
    sql_path = os.path.join(BASE_DIR, 'data', 'chat_schema.sql')
    conn = _get_db()
    try:
        with open(sql_path, 'r', encoding='utf-8') as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()


# =============================================================================
# UTILIDADES DE CONTEXTO
# =============================================================================
def init_ara_bot():
    """Crea el contacto de ARA - Intelligent si no existe, con su conversación."""
    conn = _get_db()
    try:
        contacto = _obtener_o_crear_contacto(ARA_BOT_TELEFONO, ARA_BOT_NOMBRE, conn)
        _obtener_o_crear_conversacion(contacto['id'], conn)
    finally:
        conn.close()


def _es_ara_bot(contacto_id, conn: sqlite3.Connection = None) -> bool:
    """Verifica si un contacto es el bot ARA - Intelligent."""
    close_conn = False
    if conn is None:
        conn = _get_db()
        close_conn = True
    try:
        row = conn.execute(
            "SELECT telefono FROM contactos WHERE id = ?", (contacto_id,)
        ).fetchone()
        return bool(row and row['telefono'] == ARA_BOT_TELEFONO)
    finally:
        if close_conn:
            conn.close()


def _consultar_stock_para_bot(texto: str) -> list:
    """
    Busca en stock_maestro productos que coincidan con el texto del mensaje.

    Estrategia de 3 pasos:
      A) Coincidencia EXACTA del texto completo como código/código_barra.
      B) Busca filas que contengan TODAS las palabras clave (AND implícito).
      C) Busca por la PALABRA MÁS LARGA (la más significativa).
      D) Si no hay nada → retorna lista vacía (no datos aleatorios).
    """
    STOP_WORDS = {'dame', 'el', 'la', 'los', 'las', 'del', 'un', 'una',
                  'stock', 'codigo', 'código', 'ubicacion', 'ubicación',
                  'producto', 'para', 'por', 'con', 'que', 'como', 'mas',
                  'más', 'precio', 'valor', 'cuanto', 'cuánto', 'hay',
                  ' Dame', 'me', 'de', 'en', 'al', 'su', 'se', 'no',
                  'es', 'lo', 'le', 'da', 'informacion', 'información',
                  'entonces', 'buscame', 'tus', 'base', 'datos', 'porfa',
                  'porfavor', 'favor', 'mira', 'ver', 'dime', 'tienen',
                  'existencia'}

    texto_limpio = texto.strip().upper()
    palabras = [t for t in texto_limpio.split()
                if len(t) > 2 and t not in STOP_WORDS]

    conn = _get_db()
    try:
        # --- PASO A: Coincidencia exacta como código o código_barra ---
        rows = conn.execute("""
            SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado, campo7
            FROM stock_maestro
            WHERE codigo = ? OR codigo_barra = ?
            LIMIT 3
        """, (texto_limpio, texto_limpio)).fetchall()
        if rows:
            return [dict(r) for r in rows]

        if not palabras:
            return []

        # --- PASO B: Filas que contengan TODAS las palabras clave ---
        condiciones = []
        params = []
        for pal in palabras:
            condiciones.append("(UPPER(codigo) LIKE ? OR UPPER(codigo_barra) LIKE ? OR UPPER(descripcion) LIKE ?)")
            params.extend([f'%{pal}%', f'%{pal}%', f'%{pal}%'])
        where_and = " AND ".join(condiciones)
        query = f"""
            SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado, campo7
            FROM stock_maestro
            WHERE {where_and}
            LIMIT 6
        """
        rows = conn.execute(query, params).fetchall()
        if rows:
            return [dict(r) for r in rows]

        # --- PASO C: Buscar solo por la palabra más larga (la más significativa) ---
        palabra_fuerte = max(palabras, key=len)
        like = f'%{palabra_fuerte}%'
        rows = conn.execute("""
            SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado, campo7
            FROM stock_maestro
            WHERE UPPER(codigo) LIKE ?
               OR UPPER(codigo_barra) LIKE ?
               OR UPPER(descripcion) LIKE ?
            LIMIT 6
        """, (like, like, like)).fetchall()
        if rows:
            return [dict(r) for r in rows]

        # --- PASO D: Sin resultados → lista vacía (nunca datos aleatorios) ---
        return []
    finally:
        conn.close()


def _consultar_movimientos_para_bot(texto: str) -> list:
    """Busca en movimientos_preparador si la consulta menciona 'movimiento', 'nota' o 'llevó'."""
    palabras_auditoria = ['movimiento', 'movimientos', 'auditar', 'auditoría',
                          'auditoria', 'llevó', 'llevo', 'nota', 'quién',
                          'quien', 'quien llevo', 'se llevó', 'rastrear',
                          'trazabilidad', 'preparó', 'preparo', 'preparador']
    texto_lower = texto.lower().strip()
    if not any(p in texto_lower for p in palabras_auditoria):
        return []
    conn = _get_db()
    try:
        rows = conn.execute("""
            SELECT * FROM movimientos_preparador
            ORDER BY timestamp DESC LIMIT 15
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _es_consulta_metricas(texto: str) -> bool:
    """Detecta si el mensaje pide métricas globales (totales, SKUs, resumen)."""
    palabras_clave = ['cuantos', 'cuántos', 'total', 'sku', 'skus', 'resumen', 'productos']
    texto_limpio = texto.lower().strip()
    return any(p in texto_limpio for p in palabras_clave)


def _consultar_metricas_globales() -> str:
    """Ejecuta agregaciones SQL y devuelve un string con el resumen global."""
    conn = _get_db()
    try:
        total_skus = conn.execute(
            "SELECT COUNT(DISTINCT codigo) FROM stock_maestro"
        ).fetchone()[0] or 0

        total_stock = conn.execute(
            "SELECT SUM(stock_maestro) FROM stock_maestro"
        ).fetchone()[0] or 0

        total_bulto = conn.execute(
            "SELECT SUM(stock_bulto_cerrado) FROM stock_maestro"
        ).fetchone()[0] or 0

        return (
            f"INFORMACIÓN GLOBAL DE INVENTARIO: "
            f"SKUs únicos en catálogo: {total_skus} | "
            f"Total de unidades/bultos registrados: {total_stock} | "
            f"Stock en bulto cerrado: {total_bulto}"
        )
    finally:
        conn.close()


def _llamar_nim_ara_bot(system_ctx: str, user_msg: str, timeout: int = 10) -> str:
    """Llama a NVIDIA NIM (cloud) con API compatible OpenAI.
    Retorna la respuesta textual o None si falla."""
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": NVIDIA_MODEL,
        "messages": [
            {"role": "system", "content": system_ctx},
            {"role": "user", "content": user_msg}
        ],
        "temperature": 0.1,
        "max_tokens": 80,
        "stream": False
    }
    try:
        resp = requests.post(NVIDIA_NIM_URL, json=payload, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        else:
            print(f"[ARA Bot] NVIDIA NIM respondió con status {resp.status_code}: {resp.text[:200]}")
            return None
    except requests.exceptions.Timeout:
        print("⚠️ [NVIDIA NIM TIMEOUT]: La API en la nube tardó más de 10s.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"🔴 [NVIDIA NIM ERROR]: {e}")
        return None
    except Exception as e:
        print(f"[ARA Bot] Error inesperado en NVIDIA NIM: {e}")
        return None


def _llamar_ollama_para_bot(prompt: str, timeout: int = 15) -> str:
    """Fallback local: llama a phi3:latest vía Ollama si NVIDIA NIM falla."""
    payload = {
        "model": "phi3:latest",
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": 50,
            "temperature": 0.1,
            "num_ctx": 512
        }
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        if resp.status_code == 200:
            return resp.json().get('response', '').strip()
        else:
            print(f"[ARA Bot] Ollama respondió con status {resp.status_code}")
            return None
    except requests.exceptions.Timeout:
        print("⚠️ [OLLAMA TIMEOUT]: El modelo tardó más de 15s en responder.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"🔴 [OLLAMA ERROR]: No se pudo conectar con Ollama ({e})")
        return None
    except Exception as e:
        print(f"[ARA Bot] Error inesperado llamando a Ollama: {e}")
        return None


def _formatear_fallback_sql(resultados: list, pregunta: str) -> str:
    """Respuesta de respaldo con datos SQL cuando Ollama falla."""
    if not resultados:
        return (f"🤖 *{ARA_BOT_NOMBRE}*: No encontré productos que coincidan "
                f"con \"{pregunta}\" en el sistema. Verifica que el código o "
                f"nombre sea correcto.")
    lines = [f"🤖 *{ARA_BOT_NOMBRE}*: Encontré estos datos en el sistema:"]
    for p in resultados:
        lines.append(
            f"• *{p.get('codigo', 'N/A')}* — {p.get('descripcion', 'Sin descripción')}\n"
            f"  Stock: {p.get('stock_maestro', 0)} unds | "
            f"Bulto cerrado: {p.get('stock_bulto_cerrado', 0)} | "
            f"Ubicación: {p.get('campo7', 'N/A')}"
        )
    return "\n\n".join(lines)


def _procesar_mensaje_ara_bot(mensaje_usuario: str) -> dict:
    """
    Flujo principal del bot con detección de intención:
    • Métricas globales (totales / SKUs) → consultas de agregación SQL
    • Búsqueda de producto → consulta LIKE en stock_maestro
    • Siempre intenta Ollama primero; si falla, responde con fallback SQL directo
    """
    print(f"[ARA Bot] Procesando: {mensaje_usuario}")

    # 1. Detectar intención: métricas globales vs búsqueda de producto
    es_metricas = _es_consulta_metricas(mensaje_usuario)

    if es_metricas:
        contexto_sql = _consultar_metricas_globales()
        resultados = []
    else:
        # Intentar trazabilidad de movimientos primero (auditoría / notas)
        resultados_mov = _consultar_movimientos_para_bot(mensaje_usuario)
        if resultados_mov:
            partes = ["DATOS TRAZABILIDAD (movimientos_preparador):"]
            for m in resultados_mov[:10]:
                partes.append(
                    f"- {m.get('accion','')} | Art: {m.get('co_art','')} "
                    f"({m.get('descripcion','')}) | Cant: {m.get('cantidad',0)} "
                    f"| Usuario: {m.get('usuario','')} | Fecha: {str(m.get('timestamp',''))[:19]}"
                )
            contexto_sql = "\n".join(partes)
            resultados = []
        else:
            resultados = _consultar_stock_para_bot(mensaje_usuario)
        if resultados:
            partes = []
            if len(resultados) > 3:
                partes.append("NOTA: Hay múltiples variantes para este producto en el almacén.")
            for p in resultados:
                partes.append(
                    f"- Código: {p.get('codigo')} | Desc: {p.get('descripcion')} | "
                    f"Stock: {p.get('stock_maestro', 0)} | "
                    f"Bulto cerrado: {p.get('stock_bulto_cerrado', 0)} | "
                    f"Ubicación: {p.get('campo7', 'N/A')}"
                )
            contexto_sql = "\n".join(partes)
        else:
            contexto_sql = (f"DATOS ALMACÉN: No se encontraron productos que "
                            f"coincidan con la búsqueda en la base de datos.")

    # 2. Recortar contexto SQL para evitar prompts largos (>350 chars)
    contexto_sql_limpio = contexto_sql[:350]

    # 3. System context y user message para la API (OpenAI-compatible)
    system_ctx = (
        "Eres ARA, asistente inteligente de almacén e inventario.\n"
        "INSTRUCCIONES CLAVE DE RESPUESTA:\n"
        "1. Si en los DATOS ALMACÉN encuentras 1, 2 o 3 productos exactos, "
        "responde directamente con su código, descripción, stock y ubicación.\n"
        "2. SI ENCUENTRAS MÁS DE 3 OPCIONES del mismo producto (ejemplo: "
        "varias presentaciones o laboratorios): NO des la lista completa con "
        "stocks. En su lugar, responde de forma educada preguntando al usuario "
        "qué especificación necesita.\n"
        "   Ejemplo de respuesta: \"Tengo varias opciones de [Producto] "
        "disponibles. ¿De qué laboratorio (ej: VITALIS, DISTRILAB) o qué "
        "concentración/presentación (ej: 4MG/1ML o 8MG/2ML) necesitas?\"\n"
        "3. Mantén respuestas cortas, profesionales y amigables."
    )
    user_msg = (
        f"[CONTEXTO DE INVENTARIO ARA]:\n{contexto_sql_limpio}\n\n"
        f"[PREGUNTA DEL USUARIO]:\n{mensaje_usuario}"
    )

    # 4. Intentar NVIDIA NIM (cloud, ~1-3s)
    respuesta_ia = _llamar_nim_ara_bot(system_ctx, user_msg)
    if respuesta_ia:
        return {"tipo": "ia", "contenido": f"🤖 *{ARA_BOT_NOMBRE}*:\n{respuesta_ia}"}

    # 5. Fallback Ollama local (si NVIDIA no está disponible)
    prompt_ollama = f"{system_ctx}\n\n{user_msg}"
    respuesta_ollama = _llamar_ollama_para_bot(prompt_ollama)
    if respuesta_ollama:
        return {"tipo": "ia", "contenido": f"🤖 *{ARA_BOT_NOMBRE}*:\n{respuesta_ollama}"}

    # 6. Fallback SQL formateado según tipo de consulta
    if es_metricas:
        fallback = f"🤖 *{ARA_BOT_NOMBRE}*:\n{contexto_sql}"
    else:
        fallback = _formatear_fallback_sql(resultados, mensaje_usuario)
    return {"tipo": "fallback", "contenido": fallback}


def _procesar_respuesta_ara_bot_async(app, conv_id: int, mensaje_usuario: str):
    """Ejecuta el procesamiento del bot en un hilo separado para no bloquear
    el hilo principal de Waitress. Guarda la respuesta en la BD y el frontend
    la recoge vía /api/chat/poll."""
    with app.app_context():
        try:
            print(f"[ARA Bot Async] Procesando mensaje en hilo separado...")
            resultado = _procesar_mensaje_ara_bot(mensaje_usuario)
            if resultado and resultado.get('contenido'):
                with _DB_LOCK:
                    conn = _get_db()
                    try:
                        conn.execute(
                            """INSERT INTO mensajes
                                (conversacion_id, remitente, tipo, contenido, sender_id, estado)
                                VALUES (?, 'sistema', 'texto', ?, ?, 'entregado')""",
                            (conv_id, resultado['contenido'], ARA_BOT_NOMBRE)
                        )
                        _actualizar_conversacion(conv_id, resultado['contenido'],
                                                 incrementar_unread=False, conn=conn)
                        conn.commit()
                    finally:
                        conn.close()
                print(f"[ARA Bot Async] Respuesta guardada en BD para conversación {conv_id}")
        except Exception:
            print(f"[ARA Bot Async] Error en hilo secundario:")
            traceback.print_exc()


def _obtener_o_crear_contacto(telefono: str, nombre: str = None, conn: sqlite3.Connection = None) -> dict:
    """Busca un contacto por teléfono; si no existe lo crea. Devuelve dict fila."""
    telefono = (telefono or "").strip()
    if not telefono:
        raise ValueError("El teléfono es obligatorio")
    close_conn = False
    if conn is None:
        conn = _get_db()
        close_conn = True
    try:
        row = conn.execute(
            "SELECT * FROM contactos WHERE telefono = ?", (telefono,)
        ).fetchone()
        if row:
            return dict(row)
        cur = conn.execute(
            "INSERT INTO contactos (nombre, telefono) VALUES (?, ?)",
            (nombre or telefono, telefono)
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM contactos WHERE id = ?", (cur.lastrowid,)
        ).fetchone())
    finally:
        if close_conn:
            conn.close()


def _obtener_o_crear_conversacion(contacto_id: int, conn: sqlite3.Connection = None) -> dict:
    close_conn = False
    if conn is None:
        conn = _get_db()
        close_conn = True
    try:
        row = conn.execute(
            "SELECT * FROM conversaciones WHERE contacto_id = ?",
            (contacto_id,)
        ).fetchone()
        if row:
            return dict(row)
        cur = conn.execute(
            "INSERT INTO conversaciones (contacto_id) VALUES (?)",
            (contacto_id,)
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM conversaciones WHERE id = ?", (cur.lastrowid,)
        ).fetchone())
    finally:
        if close_conn:
            conn.close()


def _actualizar_conversacion(conv_id: int, ultimo_msg: str,
                             incrementar_unread: bool = False,
                             conn: sqlite3.Connection = None):
    close_conn = False
    if conn is None:
        conn = _get_db()
        close_conn = True
    try:
        if incrementar_unread:
            conn.execute(
                """UPDATE conversaciones
                       SET ultimo_mensaje  = ?,
                           fecha_actualizacion = CURRENT_TIMESTAMP,
                           unread_count = unread_count + 1
                     WHERE id = ?""",
                (ultimo_msg[:200], conv_id)
            )
        else:
            conn.execute(
                """UPDATE conversaciones
                       SET ultimo_mensaje      = ?,
                           fecha_actualizacion = CURRENT_TIMESTAMP
                     WHERE id = ?""",
                (ultimo_msg[:200], conv_id)
            )
        conn.commit()
    finally:
        if close_conn:
            conn.close()


# =============================================================================
# REGISTRO DE RUTAS
# =============================================================================
def register_chat_routes(app):

    # ----- 1) LISTAR CONVERSACIONES -------------------------------
    @app.route('/api/chat/conversaciones', methods=['GET'])
    def chat_listar_conversaciones():
        try:
            q      = (request.args.get('q') or '').strip().lower()
            limite = min(int(request.args.get('limit', 50)), 200)
            conn = _get_db()
            try:
                if q:
                    rows = conn.execute(
                        """SELECT c.id, c.contacto_id, ct.nombre,
                                  ct.telefono, ct.foto_url,
                                  c.ultimo_mensaje,
                                  c.fecha_actualizacion,
                                  c.unread_count
                             FROM conversaciones c
                             JOIN contactos ct ON ct.id = c.contacto_id
                            WHERE LOWER(ct.nombre)    LIKE ?
                               OR LOWER(ct.telefono) LIKE ?
                            ORDER BY c.fecha_actualizacion DESC
                            LIMIT ?""",
                        (f'%{q}%', f'%{q}%', limite)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT c.id, c.contacto_id, ct.nombre,
                                  ct.telefono, ct.foto_url,
                                  c.ultimo_mensaje,
                                  c.fecha_actualizacion,
                                  c.unread_count
                             FROM conversaciones c
                             JOIN contactos ct ON ct.id = c.contacto_id
                            ORDER BY c.fecha_actualizacion DESC
                            LIMIT ?""",
                        (limite,)
                    ).fetchall()
            finally:
                conn.close()

            data = [dict(r) for r in rows]
            return _ok({"conversaciones": data, "total": len(data)})
        except Exception as e:
            return _err(f"Error listando conversaciones: {e}", 500)


    # ----- 2) HISTORIAL PAGINADO ---------------------------------
    @app.route('/api/chat/conversacion/<int:conv_id>/mensajes', methods=['GET'])
    def chat_historial(conv_id: int):
        try:
            limite  = min(int(request.args.get('limit',  50)), 200)
            before  = request.args.get('before_id', type=int)  # cursor paginación

            conn = _get_db()
            try:
                # Verifica existencia de la conversación
                exists = conn.execute(
                    "SELECT 1 FROM conversaciones WHERE id = ?", (conv_id,)
                ).fetchone()
                if not exists:
                    return _err("Conversación inexistente", 404)

                if before:
                    rows = conn.execute(
                        """SELECT * FROM mensajes
                            WHERE conversacion_id = ? AND id < ?
                            ORDER BY id DESC LIMIT ?""",
                        (conv_id, before, limite)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT * FROM mensajes
                            WHERE conversacion_id = ?
                            ORDER BY id DESC LIMIT ?""",
                        (conv_id, limite)
                    ).fetchall()
            finally:
                conn.close()

            # Se devuelven en orden cronológico ascendente (más viejo -> más nuevo)
            mensajes = [dict(r) for r in rows][::-1]
            has_more = len(mensajes) >= limite
            return _ok({
                "conversacion_id": conv_id,
                "mensajes":   mensajes,
                "has_more":   has_more,
                "next_before_id": mensajes[0]["id"] if mensajes and has_more else None,
            })
        except Exception as e:
            return _err(f"Error obteniendo historial: {e}", 500)


    # ----- 3) MARCAR COMO LEÍDO ----------------------------------
    @app.route('/api/chat/conversacion/<int:conv_id>/leer', methods=['POST'])
    def chat_marcar_leido(conv_id: int):
        try:
            conn = _get_db()
            try:
                res = conn.execute(
                    """UPDATE conversaciones SET unread_count = 0
                        WHERE id = ?""",
                    (conv_id,)
                )
                # Marcar mensajes del cliente como leídos
                conn.execute(
                    """UPDATE mensajes SET estado = 'leido'
                        WHERE conversacion_id = ? AND remitente = 'cliente'""",
                    (conv_id,)
                )
                conn.commit()
                if res.rowcount == 0:
                    return _err("Conversación inexistente", 404)
            finally:
                conn.close()
            return _ok({"conversacion_id": conv_id})
        except Exception as e:
            return _err(f"Error marcando leído: {e}", 500)


    # ----- 4) ENVIAR MENSAJE (AGENTE / SISTEMA) ------------------
    @app.route('/api/chat/enviar', methods=['POST'])
    def chat_enviar():
        try:
            data = request.get_json(silent=True) or request.form
            if isinstance(data, dict):
                conv_id    = data.get('conversacion_id')
                telefono   = data.get('telefono') or data.get('to')
                remitente  = (data.get('remitente') or 'agente').strip().lower()
                tipo       = (data.get('tipo') or 'texto').strip().lower()
                contenido  = (data.get('contenido') or data.get('text') or '').strip()
                sender_id  = data.get('sender_id') or data.get('from') or 'agente'
            else:
                conv_id = telefono = remitente = tipo = contenido = sender_id = None

            if remitente not in ('agente', 'sistema'):
                return _err("Para webhook entrante use /api/chat/webhook", 400)
            if tipo not in ('texto', 'imagen', 'archivo', 'audio'):
                return _err(f"Tipo inválido: {tipo}", 400)
            if not contenido and tipo == 'texto':
                return _err("Contenido vacío", 400)

            with _DB_LOCK:
                conn = _get_db()
                try:
                    # Resolver conversación por ID directo o por teléfono
                    if conv_id:
                        row = conn.execute(
                            "SELECT id, contacto_id FROM conversaciones WHERE id = ?",
                            (conv_id,)
                        ).fetchone()
                        if not row:
                            return _err("Conversación inexistente", 404)
                    else:
                        if not telefono:
                            return _err("Se requiere conversacion_id o telefono", 400)
                        contacto = _obtener_o_crear_contacto(telefono, data.get('nombre'), conn)
                        conv     = _obtener_o_crear_conversacion(contacto['id'], conn)
                        row      = conv

                    conv_id = row['id']
                    contacto_id = row['contacto_id']

                    # Guardar mensaje del usuario
                    cur = conn.execute(
                        """INSERT INTO mensajes
                            (conversacion_id, remitente, tipo, contenido, sender_id, estado)
                            VALUES (?, ?, ?, ?, ?, 'enviado')""",
                        (conv_id, remitente, tipo, contenido, sender_id)
                    )
                    msg_id = cur.lastrowid
                    _actualizar_conversacion(conv_id, contenido, incrementar_unread=False, conn=conn)
                    conn.commit()
                    msg = dict(conn.execute(
                        "SELECT * FROM mensajes WHERE id = ?", (msg_id,)
                    ).fetchone())
                finally:
                    conn.close()

            # ============================================================
            # INTERCEPTOR ARA - Intelligent (ASÍNCRONO)
            # Si el destinatario es el bot, lanzamos un hilo en segundo
            # plano para que el endpoint responda INMEDIATAMENTE sin
            # bloquear a Waitress.
            # El frontend recogerá la respuesta del bot vía /api/chat/poll
            # ============================================================
            if contacto_id is not None and _es_ara_bot(contacto_id):
                _app = app
                t = threading.Thread(
                    target=_procesar_respuesta_ara_bot_async,
                    args=(_app, conv_id, contenido),
                    daemon=True
                )
                t.start()

            return _ok({"mensaje": msg}, 201)
        except ValueError as e:
            return _err(str(e), 400)
        except Exception as e:
            print("❌ ERROR EN ENVIAR MENSAJE:")
            traceback.print_exc()
            return _err(f"Error enviando mensaje: {e}", 500)


    # ----- 5) WEBHOOK ENTRANTE (WHATSAPP / TELEGRAM) --------------
    @app.route('/api/chat/webhook', methods=['POST'])
    def chat_webhook():
        """Recibe mensajes entrantes desde el gateway (whatsapp_bot.js) o TG."""
        try:
            data = request.get_json(silent=True) or request.form
            telefono  = (data.get('usuario') or data.get('telefono') or data.get('from') or '').strip()
            contenido = (data.get('mensaje')  or data.get('contenido') or data.get('text') or '').strip()
            tipo      = (data.get('tipo')    or 'texto').strip().lower()
            nombre    = data.get('nombre')

            if not telefono or not contenido:
                return _err("Se requiere 'usuario/telefono' y 'mensaje/contenido'", 400)

            with _DB_LOCK:
                conn = _get_db()
                try:
                    contacto = _obtener_o_crear_contacto(telefono, nombre, conn)
                    conv     = _obtener_o_crear_conversacion(contacto['id'], conn)
                    cur = conn.execute(
                        """INSERT INTO mensajes
                            (conversacion_id, remitente, tipo, contenido, sender_id, estado)
                            VALUES (?, 'cliente', ?, ?, ?, 'entregado')""",
                        (conv['id'], tipo, contenido, telefono)
                    )
                    msg_id = cur.lastrowid
                    _actualizar_conversacion(conv['id'], contenido, incrementar_unread=True, conn=conn)
                    conn.commit()
                    msg = dict(conn.execute(
                        "SELECT * FROM mensajes WHERE id = ?", (msg_id,)
                    ).fetchone())
                finally:
                    conn.close()

            return _ok({"mensaje": msg, "conversacion_id": conv['id']}, 201)
        except ValueError as e:
            return _err(str(e), 400)
        except Exception as e:
            return _err(f"Error procesando webhook: {e}", 500)


    # ----- 6) LONG-POLL LIGERO -----------------------------------
    @app.route('/api/chat/poll', methods=['GET'])
    def chat_poll():
        """
        Long-polling corto (máx ~10s) para nuevos mensajes en una conversación.
        Parámetros:
            conv_id       : id de conversación
            since_id      : último id de mensaje visto por el cliente (opcional)
            timeout       : segundos máx de espera (default 10, máx 10)
        Devuelve inmediatamente si hay mensajes nuevos con id > since_id.
        """
        try:
            conv_id   = request.args.get('conv_id',   type=int)
            since_id  = request.args.get('since_id',  type=int, default=0)
            timeout_s = min(request.args.get('timeout', type=int, default=10), 10)

            if conv_id is None:
                return _err("Parámetro 'conv_id' obligatorio", 400)

            deadline = time.time() + timeout_s
            while time.time() < deadline:
                conn = _get_db()
                try:
                    row_conv = conn.execute(
                        "SELECT unread_count, ultimo_mensaje, fecha_actualizacion FROM conversaciones WHERE id = ?",
                        (conv_id,)
                    ).fetchone()
                    if not row_conv:
                        return _err("Conversación inexistente", 404)

                    new_msgs = conn.execute(
                        """SELECT * FROM mensajes
                            WHERE conversacion_id = ? AND id > ?
                            ORDER BY id ASC""",
                        (conv_id, since_id)
                    ).fetchall()
                finally:
                    conn.close()

                if new_msgs:
                    msgs = [dict(r) for r in new_msgs]
                    return _ok({
                        "conversacion_id": conv_id,
                        "messages": msgs,
                        "last_id": msgs[-1]["id"],
                        "unread_count": row_conv["unread_count"],
                    })

                time.sleep(1.5)  # espera activa corta

            # Timeout: nada nuevo
            return _ok({
                "conversacion_id": conv_id,
                "messages": [],
                "last_id": since_id,
                "unread_count": 0,
                "timeout": True,
            })
        except Exception as e:
            return _err(f"Error en poll: {e}", 500)

    # Inicializar contacto ARA - Intelligent si no existe
    init_ara_bot()

    # eslint-disable-next-line
    app.logger.info("Rutas de mensajería /api/chat/* registradas")
    return app

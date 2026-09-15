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
import re
import time
import json
import sqlite3
import traceback
import unicodedata
import requests
import threading
from datetime import datetime
from threading import Lock

from flask import request, jsonify, g, Response

import push_notif


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

# NVIDIA NIM (Cloud) — key heredada + pool VERIFICADO del visor (ara_vision)
NVIDIA_API_KEY = "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"
NVIDIA_NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
# Bug real detectado en vivo (26/08): "meta/llama-3.1-8b-instruct" devolvía
# HTTP 410 (Gone) en las 6 API keys del pool. Se probó cambiar al modelo
# que ARA Coder tenía documentado como funcionando (llama-3.1-70b-instruct)
# y TAMBIÉN dio 410 — mensaje real de NVIDIA: "The model
# 'meta/llama-3.1-70b-instruct' has reached its end of life on
# 2026-08-26T09:00:00Z and is no longer available." NVIDIA dio de baja
# ambos modelos HOY MISMO, a media sesión — no es bug de código, es un
# proveedor externo. El modelo de respaldo por NIM (deepseek-v4-flash vía
# integrate.api.nvidia.com) también cuelga (25s timeout, problema ya
# documentado desde el 24/08 en ARA Coder). Se deja 70b configurado (por si
# NVIDIA lo reactiva o agrega uno nuevo) pero el pool falla rápido ahora
# (~2.5s las 6 keys) y cae al respaldo real de abajo.
NVIDIA_MODEL    = "meta/llama-3.1-70b-instruct"

# DeepSeek API oficial directa (platform.deepseek.com, NO por el gateway de
# NIM que cuelga) — mismo mecanismo y misma cuenta que ya usa ARA Coder
# (C:\ara_coder_service\config.py), confirmada con saldo y respondiendo en
# vivo hoy (26/08, ~1-2s reales). Se agrega como segundo intento, antes de
# Ollama local (phi3, mucho más débil) — para que "Automático" tenga un
# modelo capaz de verdad mientras NIM siga muerto.
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# Ollama local (fallback si NVIDIA NIM no está disponible)
OLLAMA_URL       = "http://127.0.0.1:11434/api/generate"


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Fix real (31/08): sin esto, busy_timeout queda en el default de
    # SQLite (0ms) — bajo escritura concurrente real de otros módulos que
    # escriben el MISMO archivo (visor, notas, rutas), esta conexión podía
    # tirar "database is locked" de una en vez de esperar. Mismo valor que
    # ya usa get_db_connection() en ara_server.py (ara_server.py:423) —
    # atencion_cliente_routes.py hereda el fix gratis al reusar este _get_db.
    conn.execute("PRAGMA busy_timeout = 5000")
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
        _migrar_conversaciones_pairwise(conn)
        # Recién aquí es seguro: usuario_a_id/usuario_b_id ya existen, sea
        # porque la tabla se creó de cero con ellas o porque la migración
        # las agregó a una tabla vieja.
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_conversaciones_usuario_a
                ON conversaciones (usuario_a_id);
            CREATE INDEX IF NOT EXISTS idx_conversaciones_usuario_b
                ON conversaciones (usuario_b_id);
            CREATE INDEX IF NOT EXISTS idx_mensajes_timestamp
                ON mensajes (timestamp);
        """)
        conn.commit()
        _purgar_mensajes_bandeja_antiguos(conn)
    finally:
        conn.close()


# =============================================================================
# RETENCIÓN: mensajes temporales — SOLO Bandeja interna (tabla `mensajes`)
# =============================================================================
# A propósito, esto NO toca `atencion_mensajes` (módulo Atención al Cliente):
# ese historial es evidencia de conversación con clientes reales y debe
# conservarse; la Bandeja interna es mensajería de operación diaria entre
# el personal, sin ese requisito.
DIAS_RETENCION_BANDEJA = 7
_ultima_purga_bandeja = 0.0
_INTERVALO_MIN_PURGA_S = 6 * 3600  # no purgar más de 1 vez cada 6h


def _purgar_mensajes_bandeja_antiguos(conn: sqlite3.Connection = None):
    """Borra mensajes de la Bandeja con más de DIAS_RETENCION_BANDEJA días.
    Recalcula ultimo_mensaje/fecha_actualizacion de las conversaciones cuyo
    mensaje más reciente quedó purgado, para que la vista previa del listado
    no se quede mostrando un texto que ya no existe."""
    close_conn = False
    if conn is None:
        conn = _get_db()
        close_conn = True
    try:
        limite = f"-{DIAS_RETENCION_BANDEJA} days"
        afectadas = [r['conversacion_id'] for r in conn.execute(
            "SELECT DISTINCT conversacion_id FROM mensajes "
            "WHERE timestamp < datetime('now', ?)", (limite,)
        ).fetchall()]
        if not afectadas:
            return

        cur = conn.execute(
            "DELETE FROM mensajes WHERE timestamp < datetime('now', ?)", (limite,)
        )
        borrados = cur.rowcount

        for conv_id in afectadas:
            ultimo = conn.execute(
                "SELECT contenido, timestamp FROM mensajes "
                "WHERE conversacion_id = ? ORDER BY id DESC LIMIT 1",
                (conv_id,)
            ).fetchone()
            if ultimo:
                conn.execute(
                    "UPDATE conversaciones SET ultimo_mensaje = ? WHERE id = ?",
                    (ultimo['contenido'][:200], conv_id)
                )
            else:
                conn.execute(
                    "UPDATE conversaciones SET ultimo_mensaje = '' WHERE id = ?",
                    (conv_id,)
                )
        conn.commit()
        print(f"[Bandeja] Retención de {DIAS_RETENCION_BANDEJA} días: "
              f"{borrados} mensaje(s) purgado(s) en {len(afectadas)} conversación(es).")
    finally:
        if close_conn:
            conn.close()


def _purgar_mensajes_bandeja_si_toca():
    """Versión auto-throttled: como no hay un scheduler/cron en este proceso,
    se engancha en endpoints de uso frecuente (listar conversaciones) pero
    sin ejecutar el DELETE en cada request — máximo 1 vez cada 6h."""
    global _ultima_purga_bandeja
    ahora = time.time()
    if ahora - _ultima_purga_bandeja < _INTERVALO_MIN_PURGA_S:
        return
    _ultima_purga_bandeja = ahora
    try:
        _purgar_mensajes_bandeja_antiguos()
    except Exception as e:
        print(f"[Bandeja] Error en purga automática (no bloquea): {e}")


def _migrar_conversaciones_pairwise(conn: sqlite3.Connection):
    """Migración idempotente para BD ya existentes creadas antes del chat
    interno par-a-par: agrega usuario_a_id/usuario_b_id y vuelve contacto_id
    opcional (necesario para que un hilo pueda ser 'entre dos usuarios' y no
    solo 'con un contacto'). SQLite no permite ALTER de una NOT NULL/UNIQUE
    existente, así que se reconstruye la tabla solo si faltan las columnas."""
    columnas = {row['name'] for row in conn.execute("PRAGMA table_info(conversaciones)")}
    if 'usuario_a_id' in columnas:
        return  # ya migrada

    conn.executescript("""
        PRAGMA foreign_keys = OFF;

        CREATE TABLE conversaciones_new (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            contacto_id       INTEGER,
            usuario_a_id      TEXT,
            usuario_b_id      TEXT,
            ultimo_mensaje    TEXT    DEFAULT '',
            fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            unread_count      INTEGER DEFAULT 0,
            UNIQUE (contacto_id),
            UNIQUE (usuario_a_id, usuario_b_id),
            FOREIGN KEY (contacto_id) REFERENCES contactos (id) ON DELETE CASCADE
        );

        INSERT INTO conversaciones_new
            (id, contacto_id, ultimo_mensaje, fecha_actualizacion, unread_count)
        SELECT id, contacto_id, ultimo_mensaje, fecha_actualizacion, unread_count
          FROM conversaciones;

        DROP TABLE conversaciones;
        ALTER TABLE conversaciones_new RENAME TO conversaciones;

        CREATE INDEX IF NOT EXISTS idx_conversaciones_fecha
            ON conversaciones (fecha_actualizacion DESC);
        CREATE INDEX IF NOT EXISTS idx_conversaciones_usuario_a
            ON conversaciones (usuario_a_id);
        CREATE INDEX IF NOT EXISTS idx_conversaciones_usuario_b
            ON conversaciones (usuario_b_id);

        PRAGMA foreign_keys = ON;
    """)
    conn.commit()


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


_RE_NOTA_EN_TEXTO = re.compile(r'\b\d{6,10}\b')
_RE_CO_ART_EN_TEXTO = re.compile(r'\b[A-Z]{2,6}\d{3,6}\b')


def _consultar_reubicaciones_para_bot(texto: str) -> list:
    """Busca en reportes_ubicacion si la consulta menciona reubicación/traslado
    de artículo entre puestos del almacén (desde/hacia) — trazabilidad física,
    independiente de notas (no tiene nota_id, se filtra por código de artículo).
    """
    palabras_ubicacion = ['reubic', 'reubicó', 'reubico', 'movió el', 'movio el',
                          'cambiaron de puesto', 'cambio de puesto', 'traslad',
                          'de qué puesto', 'de que puesto', 'a qué puesto',
                          'a que puesto', 'ubicación anterior', 'ubicacion anterior',
                          'de dónde a dónde', 'de donde a donde']
    texto_lower = texto.lower().strip()
    if not any(p in texto_lower for p in palabras_ubicacion):
        return []

    conn = _get_db()
    try:
        match_art = _RE_CO_ART_EN_TEXTO.search(texto.upper())
        if match_art:
            co_art = match_art.group(0)
            rows = conn.execute(
                "SELECT * FROM reportes_ubicacion WHERE co_art = ? "
                "ORDER BY fecha DESC LIMIT 15",
                (co_art,)
            ).fetchall()
            return [dict(r) for r in rows]

        rows = conn.execute(
            "SELECT * FROM reportes_ubicacion ORDER BY fecha DESC LIMIT 15"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[ARA Bot] Error consultando reportes_ubicacion: {e}")
        return []
    finally:
        conn.close()


def _consultar_movimientos_para_bot(texto: str) -> list:
    """Busca en movimientos_preparador si la consulta menciona 'movimiento',
    'nota', 'llevó', etc. — qué sacó realmente un preparador.

    Cruce real (a pedido explícito, 2026-08-12): `movimientos_preparador.nota_id`
    coteja contra `notas_entrega.id` (la PK interna, NO el número de nota
    visible) — nunca `numero_nota` directo. Si el mensaje trae un número de
    nota (6-10 dígitos), primero se resuelve su `notas_entrega.id` y se
    filtran SOLO los movimientos de esa nota puntual (antes se devolvían los
    últimos 15 globales sin importar de qué nota se preguntara). Si no hay
    número de nota en el mensaje, se mantiene el fallback de "últimos 15" pero
    ahora enriquecido con el número de nota/cliente de cada movimiento.
    """
    palabras_auditoria = ['movimiento', 'movimientos', 'auditar', 'auditoría',
                          'auditoria', 'llevó', 'llevo', 'nota', 'quién',
                          'quien', 'quien llevo', 'se llevó', 'rastrear',
                          'trazabilidad', 'preparó', 'preparo', 'preparador']
    texto_lower = texto.lower().strip()
    if not any(p in texto_lower for p in palabras_auditoria):
        return []

    conn = _get_db()
    try:
        match_nota = _RE_NOTA_EN_TEXTO.search(texto)
        if match_nota:
            numero_nota = match_nota.group(0)
            nota = conn.execute(
                "SELECT id, numero_nota, cliente, preparador_id, estado "
                "FROM notas_entrega WHERE numero_nota = ?",
                (numero_nota,)
            ).fetchone()
            if nota is None:
                # Número de nota mencionado pero no existe en notas_entrega:
                # nunca inventar movimientos de una nota que no se pudo
                # verificar — mejor devolver vacío (cae al flujo normal).
                return []
            rows = conn.execute(
                "SELECT m.*, n.numero_nota, n.cliente, n.preparador_id, n.estado "
                "FROM movimientos_preparador m "
                "JOIN notas_entrega n ON m.nota_id = n.id "
                "WHERE n.id = ? ORDER BY m.timestamp ASC",
                (nota['id'],)
            ).fetchall()
            return [dict(r) for r in rows]

        rows = conn.execute("""
            SELECT m.*, n.numero_nota, n.cliente
              FROM movimientos_preparador m
              LEFT JOIN notas_entrega n ON m.nota_id = n.id
             ORDER BY m.timestamp DESC LIMIT 15
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[ARA Bot] Error consultando movimientos_preparador: {e}")
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


def _llamar_nim_ara_bot(system_ctx: str, user_msg: str, historial_mensajes: list = None, timeout: int = 10) -> str:
    """Llama a NVIDIA NIM (cloud) con API compatible OpenAI.
    Retorna la respuesta textual o None si falla.

    `historial_mensajes` (opcional): turnos previos de ESTA conversación
    ([{"role": "user"/"assistant", "content": ...}], más viejo primero) —
    bug real reportado por el usuario (02/09): sin esto, cada pregunta se
    mandaba SOLA (solo system+pregunta actual), así que un "sí, esa misma"
    después de una respuesta anterior no tenía forma de saber a qué se
    refería "esa misma". Se inserta ENTRE el system prompt y la pregunta
    actual, nunca reemplaza a ninguno de los dos."""
    def _keys_pool():
        try:
            import ara_vision
            extras = list(getattr(ara_vision, "NVIDIA_KEYS", []) or [])
        except Exception:
            extras = []
        vistas = set()
        pool = []
        for k in extras + [NVIDIA_API_KEY]:
            if k and k not in vistas:
                vistas.add(k)
                pool.append(k)
        return pool

    pool = _keys_pool()
    for api_key in pool:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        mensajes = [{"role": "system", "content": system_ctx}]
        if historial_mensajes:
            mensajes.extend(historial_mensajes)
        mensajes.append({"role": "user", "content": user_msg})
        payload = {
            "model": NVIDIA_MODEL,
            "messages": mensajes,
            "temperature": 0.1,
            "max_tokens": 400,
            "stream": False
        }
        try:
            resp = requests.post(NVIDIA_NIM_URL, json=payload, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            print(f"[ARA Bot] NVIDIA NIM status {resp.status_code} con key {api_key[:10]}... Rotando.")
        except requests.exceptions.Timeout:
            print("⚠️ [NVIDIA NIM TIMEOUT]: La API en la nube tardó más de 10s.")
        except requests.exceptions.RequestException as e:
            print(f"🔴 [NVIDIA NIM ERROR]: {e}")
        except Exception as e:
            print(f"[ARA Bot] Error inesperado en NVIDIA NIM: {e}")
    return None


def _llamar_deepseek_para_bot(system_ctx: str, user_msg: str, historial_mensajes: list = None, timeout: int = 25) -> str:
    """DeepSeek API oficial directa — respaldo real mientras NIM esté
    muerto (26/08, ver nota junto a NVIDIA_MODEL). Formato OpenAI-compatible,
    misma forma que NIM. `historial_mensajes`: ver docstring de
    _llamar_nim_ara_bot — mismo criterio, mismo bug real corregido."""
    if not DEEPSEEK_API_KEY:
        return None
    try:
        mensajes = [{"role": "system", "content": system_ctx}]
        if historial_mensajes:
            mensajes.extend(historial_mensajes)
        mensajes.append({"role": "user", "content": user_msg})
        payload = {
            "model": DEEPSEEK_MODEL,
            "messages": mensajes,
            "temperature": 0.1,
            "max_tokens": 400,
        }
        resp = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        print(f"[ARA Bot] DeepSeek respondió HTTP {resp.status_code}: {resp.text[:200]}")
    except requests.exceptions.Timeout:
        print("⚠️ [DEEPSEEK TIMEOUT]: La API tardó más de 25s.")
    except requests.exceptions.RequestException as e:
        print(f"🔴 [DEEPSEEK ERROR]: {e}")
    except Exception as e:
        print(f"[ARA Bot] Error inesperado en DeepSeek: {e}")
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


# =============================================================================
# RECONOCIMIENTO DE INTENCIONES → ACTIVACIÓN AUTOMÁTICA DE SKILLS (v4.11,
# ampliado v4.56)
#
# REGLA ABSOLUTA: si la intención coincide, la skill se EJECUTA vía el runner
# PHP antes de cualquier respuesta LLM. Solo si la skill falla se cae al flujo
# conversacional; ARA jamás dice "no tengo información" sin intentar la skill.
#
# BUG CORREGIDO (v4.56, reportado en vivo): antes cada verbo se listaba con
# UNA sola conjugación literal (ej. "consulta" pero no "consultar"), así que
# "consultar la nota" (infinitivo) no activaba la skill aunque "consulta la
# nota" (imperativo) sí — el \b de cierre de palabra chocaba con la "r"
# final. Ahora los verbos se listan por RAÍZ (ver _raiz()) y cualquier
# conjugación (infinitivo, imperativo, gerundio...) calza igual.
#
# ALCANCE (v4.56): se cubren las tools de SOLO LECTURA con parámetros
# simples (código/texto/número). Quedan FUERA de la activación por texto
# libre — solo se disparan con /comando explícito — las que escriben datos
# (gestion_notas, cierre_transaccional, conciliar_factura), las que piden un
# array/objeto estructurado que no se puede extraer de una frase
# (crear_reporte_archivo, y el MODO de enrutamiento por ítems de
# bulto_cerrado_router — su modo proactivo, sin ítems, SÍ se dispara por
# frase, ver regla 16a-bis), las que reciben imagen/OCR
# (leer_voucher_ocr, python_recepcion_skill, recepcion_factura_vision), los
# superconectores python_*_skill (su parámetro 'accion' puede incluir
# operaciones de bloqueo/escritura — elegir mal por texto libre es riesgoso)
# y hermes_chat (Motor PRO, activación explícita por diseño).
# =============================================================================


def _normalizar_acentos(texto: str) -> str:
    """Quita tildes para el matching ('métricas'→'metricas', 'cuánto'→'cuanto')."""
    return unicodedata.normalize('NFD', texto).encode('ascii', 'ignore').decode('ascii')


def _raiz(*raices: str) -> str:
    """(?:raiz1\\w*|raiz2\\w*|...) — cubre CUALQUIER conjugación de un verbo
    (infinitivo, imperativo, presente, gerundio) a partir de su raíz, sin
    enumerar cada forma a mano. Ver nota del bug corregido más arriba."""
    return '(?:' + '|'.join(re.escape(r) + r'\w*' for r in raices) + ')'


def _tiene(t: str, *raices: str) -> bool:
    """True si CUALQUIER conjugación de alguna de las raíces aparece en t."""
    return re.search(r'\b' + _raiz(*raices) + r'\b', t, re.IGNORECASE) is not None


def _extraer_num_nota(t: str):
    m = re.search(r'\b(\d{6,10})\b', t)
    return m.group(1) if m else None


def _extraer_co_art(t: str):
    """Código de artículo Profit: 1-4 letras + 3-8 dígitos (ej. MD01707,
    AMP00226), opcionalmente con letras finales."""
    m = re.search(r'\b([A-Za-z]{1,4}\d{3,8}[A-Za-z]?)\b', t)
    return m.group(1).upper() if m else None


def _extraer_umbral_minutos(t: str):
    m = re.search(r'\b(\d{1,4})\s*min', t)
    return int(m.group(1)) if m else None


def _extraer_sede(t: str):
    if re.search(r'\b(san\s*cristobal|sc)\b', t, re.IGNORECASE):
        return 'sc'
    if re.search(r'\b(barquisimeto|bqto)\b', t, re.IGNORECASE):
        return 'bqto'
    return None


def _extraer_tras(t: str, *raices_o_palabras: str) -> str:
    """Texto que queda tras la última ocurrencia de cualquiera de las
    raíces/palabras dadas (para 'busca el cliente ACME' → 'ACME')."""
    t = t.strip().strip('?¿.!').strip()
    patron = _raiz(*raices_o_palabras)
    matches = list(re.finditer(r'\b' + patron + r'\b', t, re.IGNORECASE))
    if not matches:
        return t.strip()[:60]
    resto = t[matches[-1].end():].strip()
    resto = re.sub(r'^(?:de|del|la|el|para)\s+', '', resto, flags=re.IGNORECASE)
    return resto.strip()[:60]


# Frases que disparan la skill de inventario/stock (lista de frases
# completas — se mantiene explícita porque mezcla verbo+objeto en un solo
# giro idiomático, no un verbo suelto).
_RE_INVENTARIO = re.compile(
    r'\b(?:busca el producto|buscar producto|busca el articulo|buscar articulo|'
    r'hay stock|cuanto\s+(?:\w+\s+)?'
    r'(?:queda|hay)|cuanto stock|queda stock|revisa(?:r)? (?:el )?inventario|'
    r'precio o vencimiento|precio de|vencimiento de|existencia de|cuanto hay de)\b',
    re.IGNORECASE | re.UNICODE,
)
_PREFIJOS_INVENTARIO = [
    'busca el producto', 'buscar producto', 'busca el articulo', 'buscar articulo',
    'revisa el inventario de', 'revisa el inventario',
    'revisa inventario de', 'revisa inventario', 'revisar el inventario de', 'revisar el inventario',
    'revisar inventario de', 'revisar inventario', 'hay stock de', 'hay stock',
    'queda stock de', 'queda stock', 'precio o vencimiento de', 'precio de',
    'vencimiento de', 'existencia de', 'cuanto hay de', 'cuanto hay',
    'cuanto stock de', 'cuanto stock', 'cuanto',
]
_SUFIJOS_INVENTARIO = ['queda', 'hay', 'existe', 'tienes', 'stock']


def _extraer_termino_busqueda(texto: str) -> str:
    """Extrae el término de producto del mensaje de inventario (v4.11)."""
    t = texto.strip().strip('?¿.!').strip()
    for prefijo in _PREFIJOS_INVENTARIO:
        idx = t.lower().find(prefijo)
        if idx >= 0:
            t = t[idx + len(prefijo):].strip()
            break
    t = re.sub(r'\s*\b(?:' + '|'.join(_SUFIJOS_INVENTARIO) + r')\s*$', '', t)
    return t.strip()[:60]


# ── Skills auto-generadas por ARA Coder (v4.61, servicio aparte, mismo
# host, puerto 8010) — catálogo dinámico, resuelve de raíz el bug real de
# v4.67 (una skill nueva quedaba invisible para 'Automático' hasta agregar
# a mano una regla acá). Cada skill nueva ya guarda sus propias
# 'palabras_activacion' (v4.68, agent_loop.py::_pedir_palabras_activacion) —
# _detectar_intencion_skill() las consulta como ÚLTIMO recurso, después de
# agotar las reglas fijas de arriba, para no arriesgar que una palabra de
# activación mal elegida por el LLM le gane a una regla ya probada. Solo se
# usan skills con 'aprobada: true' (aprobación manual, /api/skills/approve
# de ARA Coder) — mismo criterio de v4.63: ningún otro módulo debe confiar
# en una skill sin revisar.
_RUTA_CATALOGO_ARA_CODER = os.environ.get(
    "ARA_CODER_CATALOGO_PATH",
    r"C:\ara_coder_service\generated_skills\catalog.json",
)


def _cargar_catalogo_ara_coder() -> list:
    """Lectura best-effort: si ARA Coder nunca corrió o el archivo no
    existe, no rompe nada — simplemente no hay fallback dinámico."""
    try:
        with open(_RUTA_CATALOGO_ARA_CODER, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# Identificador alfanumérico real (ej. FAR01680, MD04668) o número puro
# (ej. 72160754). Bug real (26/08, "facturas de cliente FAR01680"): el
# regex viejo (\d{4,10}) exige SOLO dígitos — "FAR01680" nunca calzaba
# porque no hay límite de palabra entre la letra y el dígito, así que
# ningún código con prefijo de letras se detectaba jamás.
_RE_IDENTIFICADOR = re.compile(r'\b([A-Za-z]{1,6}\d{3,10}|\d{4,10})\b')

# Prefijo corto en frases tipo "que empiecen con 72 millones" / "empieza con
# 72" (28/08): _RE_IDENTIFICADOR exige 4+ dígitos a propósito (para no
# dispararse con cualquier número corto suelto del mensaje) — un prefijo de
# 1-3 dígitos como "72" jamás calza ahí. Este patrón es DELIBERADAMENTE más
# angosto: solo dispara pegado a la frase "empiez* con", nunca con un número
# corto aislado en cualquier otra parte del mensaje — así no compite en
# alcance con _RE_IDENTIFICADOR ni arriesga disparar una skill por un número
# random. Se usa SOLO como respaldo para parámetros like_sufijo (valor + '%'
# = "empieza con"), nunca para like_prefijo ('%' + valor = "termina en").
_RE_PREFIJO_EMPIEZA_CON = re.compile(r'\bempiez\w*\s+con\s+(\d{1,10})\b', re.IGNORECASE)


def _extraer_prefijo_variable(t: str, param: dict):
    """Extrae el valor real para un parámetro VARIABLE (like_prefijo/
    like_sufijo) del mensaje: primero el identificador general, y si el
    parámetro es un prefijo (like_sufijo) y no hubo match, intenta la frase
    corta 'empiez* con N'. Nunca reusa `valor_original` acá — sería mentir
    con el dato de otra consulta."""
    m = _RE_IDENTIFICADOR.search(t)
    if m:
        return m.group(1)
    if param.get('like_sufijo'):
        m = _RE_PREFIJO_EMPIEZA_CON.search(t)
        if m:
            return m.group(1)
    return None


def _normalizar_singular(palabra: str) -> str:
    """Quita una 's' final simple (heurística liviana, no gramática real) —
    'facturas' → 'factura'. Palabras cortas (<=4) se dejan intactas para no
    romper 'mas'/'dos'/'tres' ni similares."""
    if len(palabra) > 4 and palabra.endswith('s'):
        return palabra[:-1]
    return palabra


def _tiene_activacion(t: str, palabras_activacion: list) -> bool:
    """Como _tiene(), pero tolera singular/plural palabra por palabra sin
    tocar _tiene()/_raiz() global (esas ya están probadas por las reglas
    fijas de arriba — cualquier cambio ahí arriesga romperlas). Bug real
    (26/08): las frases que arma el LLM salen casi siempre en plural
    ('facturas de cliente'), y _tiene() exige la frase literal completa —
    'la ultima factura' (singular) del usuario nunca calzaba. Cada palabra
    de la frase se reduce a su raíz singular (más corta, así el \\w* de
    _raiz() se estira para cubrir plural o singular real) manteniendo el
    ORDEN de la frase — no se dispersa a bag-of-words.

    Bug real #2 (26/08, "nota de entrega de esta factura X" contra la frase
    'nota de entrega'): las palabras cortas (ej. 'de') se descartan al
    armar el patrón, pero el conector entre palabras exigía adyacencia
    exacta (\\s+) — 'nota' pegado a 'entrega' sin hueco. El mensaje real
    SÍ tiene 'de' en el medio (la palabra que se descartó), así que nunca
    calzaba. El conector ahora tolera hasta 3 palabras de relleno entre
    cada palabra significativa (cubre tanto la palabra corta descartada
    como inserciones reales del usuario, ej. 'de ESTA factura').

    Bug real #3 (28/08, "cuantas notas de la sede de bqto estan ya
    embaladas son todas las notas de hoy que empiezan con 72 millones"):
    esta MISMA frase, la que generó la skill originalmente, no volvía a
    activarla — entre 'notas' y 'embaladas' el mensaje real tiene 7
    palabras de relleno ('de la sede de bqto estan ya'), muy por encima
    del tope de 3. Mensajes reales largos/enredados meten más relleno del
    previsto. Se sube el tope a 10 (sigue exigiendo TODAS las palabras
    significativas, EN ORDEN — no se pasa a bag-of-words, eso ya se
    decidió explícitamente que no) — cubre este caso real con margen sin
    aflojar la exigencia de orden que evita falsos positivos."""
    conector = r'(?:\s+\S+){0,10}?\s+'
    for frase in palabras_activacion:
        palabras = [p for p in frase.split() if len(p) > 2]
        if not palabras:
            continue
        patron = conector.join(re.escape(_normalizar_singular(p)) + r'\w*' for p in palabras)
        if re.search(r'\b' + patron + r'\b', t, re.IGNORECASE):
            return True
    return False


def _detectar_intencion_skill_autogenerada(t: str):
    """Revisa el catálogo real de skills sintetizadas por ARA Coder. Solo
    dispara cuando puede extraer un identificador con confianza (mismo
    criterio que el resto de esta función: mejor no disparar la skill que
    dispararla con datos incompletos) — 0 parámetros dispara directo, 1
    parámetro exige encontrar un identificador real en el mensaje, 2+
    parámetros solo dispara si TODOS comparten el mismo valor_original
    (mismo dato repetido en varias columnas vía OR, no N datos distintos);
    en cualquier otro caso de 2+ parámetros se descarta."""
    catalogo = _cargar_catalogo_ara_coder()
    for entrada in catalogo:
        if not entrada.get("aprobada"):
            continue
        palabras = entrada.get("palabras_activacion") or []
        if not palabras or not _tiene_activacion(t, palabras):
            continue
        parametros = entrada.get("parametros") or []
        nombre_funcion = entrada.get("nombre_funcion")
        if not nombre_funcion:
            continue
        if len(parametros) == 0:
            return {'skill': nombre_funcion, 'arguments': {}}
        if len(parametros) == 1:
            param = parametros[0]
            valor_extraido = _extraer_prefijo_variable(t, param)
            if valor_extraido:
                return {'skill': nombre_funcion, 'arguments': {param['nombre']: valor_extraido}}
            # Bug real (26/08, "facturas de cliente FAR01680" devolvió la
            # factura de FAR01361 — el cliente que originó la skill, no el
            # pedido): un parámetro de un LIKE es una búsqueda real que el
            # usuario nombra cada vez — jamás reusar el valor de
            # entrenamiento ahí, sería mentir con datos de otra entidad.
            # El respaldo de valor_original queda SOLO para columnas de
            # igualdad (flags/estados fijos, ej. inactivo=1), donde
            # reusarlo es correcto porque define QUÉ ES la skill.
            if not (param.get('like_prefijo') or param.get('like_sufijo')):
                valor_original = param.get('valor_original')
                if valor_original is not None:
                    return {'skill': nombre_funcion, 'arguments': {param['nombre']: valor_original}}
            continue
        # 2+ parámetros: en general no hay forma confiable de completarlos
        # todos desde un solo mensaje corto — pero hay un caso real y común
        # que SÍ es seguro: cuando la consulta original busca el MISMO
        # valor en varias columnas con OR (ej. "id = 22 OR numero = '22'",
        # buscar por id o por código indistintamente). Ahí los N parámetros
        # comparten el mismo valor_original — no son N datos distintos que
        # el usuario tendría que dar, es UN dato repetido. Bug real (26/08,
        # "buscar operador 22" tras generar BuscarOperadorTool con params
        # id+numero): la skill nunca disparaba en Automático pese a tener
        # palabras_activacion exactas, porque esta función cortaba en seco
        # ante cualquier skill de 2+ parámetros sin distinguir este caso.
        valores_originales = {p.get('valor_original') for p in parametros}
        if len(valores_originales) == 1 and not any(
            p.get('like_prefijo') or p.get('like_sufijo') for p in parametros
        ):
            m = _RE_IDENTIFICADOR.search(t)
            if m:
                valor = m.group(1)
                return {'skill': nombre_funcion, 'arguments': {p['nombre']: valor for p in parametros}}
            valor_original = next(iter(valores_originales))
            if valor_original is not None:
                return {'skill': nombre_funcion, 'arguments': {p['nombre']: valor_original for p in parametros}}
            continue

        # Caso mixto (28/08): parámetros FIJOS (sin like_prefijo/like_sufijo —
        # flags/estados que definen QUÉ ES la skill, ej. verifi_emb =
        # 'VERIFICADA') + UN solo parámetro VARIABLE (con like_prefijo o
        # like_sufijo — la búsqueda real que el usuario da cada vez, ej. el
        # prefijo de nota). Bug real (28/08, "ContadorNotasEmbaladas": cd_barr
        # LIKE '72%' + verifi_emb='VERIFICADA' fijo): como los 2 valores de
        # entrenamiento eran distintos entre sí, el chequeo de "mismo valor"
        # de arriba nunca aplicaba y esta función se rendía siempre — el
        # usuario preguntaba lo mismo y terminaba generando una skill
        # duplicada (ContadorNotasEmbaladas2Tool.php) en vez de reusar la que
        # ya existía. Los fijos se reusan tal cual (misma razón que el caso
        # de 1 parámetro de arriba: un flag/estado fijo define qué es la
        # skill, no es un dato que el usuario deba repetir); el variable se
        # extrae fresco del mensaje — nunca se reusa un valor de
        # entrenamiento en el parámetro de búsqueda real.
        variables = [p for p in parametros if p.get('like_prefijo') or p.get('like_sufijo')]
        fijos = [p for p in parametros if not (p.get('like_prefijo') or p.get('like_sufijo'))]
        if len(variables) == 1 and fijos:
            valor_extraido = _extraer_prefijo_variable(t, variables[0])
            if valor_extraido:
                argumentos = {p['nombre']: p.get('valor_original') for p in fijos}
                argumentos[variables[0]['nombre']] = valor_extraido
                if all(v is not None for v in argumentos.values()):
                    return {'skill': nombre_funcion, 'arguments': argumentos}
    return None


def _detectar_intencion_skill(mensaje: str):
    """Mapea el mensaje natural a una skill del catálogo (33 tools; ver
    ALCANCE arriba para las excluidas). Devuelve {"skill": str,
    "arguments": dict} o None si no hay intención operativa clara
    (conversación simple: saludos, preguntas generales) o si falta un dato
    requerido para armar los argumentos (mejor no disparar la skill que
    dispararla con datos incompletos)."""
    t = _normalizar_acentos(mensaje or '').strip()
    if not t or t.startswith('/'):
        return None
    # BUG REAL detectado en vivo (v4.56): los chequeos de palabra suelta
    # ('nota' in t, 'articulo' in t, etc.) son comparación de substring
    # PLANA de Python — sensible a mayúsculas, a diferencia de _tiene()
    # (que sí usa regex con IGNORECASE). Con el usuario escribiendo TODO
    # EN MAYÚSCULAS (frecuente en este proyecto), "NOTA" nunca calzaba con
    # 'nota' in t y la skill nunca se disparaba, sin ningún error visible.
    # t_lower es SOLO para detectar intención; las funciones _extraer_*
    # siguen recibiendo el t original para no perder mayúsculas reales en
    # nombres de ruta/cliente extraídos.
    t_lower = t.lower()

    # 1) Métricas globales / productividad del día
    if _tiene(t, 'como van las metric', 'rendimiento global', 'top de client',
              'metricas de hoy', 'productividad del dia') or \
       re.search(r'\bcomo\s+va(?:n)?\s+(?:l[oa]s?\s+)?tiempo', t, re.IGNORECASE):
        return {'skill': 'gestion_super_esteroide_search', 'arguments': {'num_nota': 'global'}}

    # 2) Tiempos/traza/trazabilidad de UNA nota puntual
    if re.search(r'\b(?:tiempo|traza|trazabilidad)\w*\s+de\s+(?:la\s+)?nota\b', t, re.IGNORECASE):
        num = _extraer_num_nota(t)
        if num:
            return {'skill': 'gestion_super_esteroide_search', 'arguments': {'num_nota': num}}

    # 3) Ciclo de vida / timeline 360° de una nota (más específico que "traza")
    if _tiene(t, 'ciclo de vida', 'vida util') and 'nota' in t_lower:
        num = _extraer_num_nota(t)
        if num:
            return {'skill': 'trazabilidad_vida_util_nota', 'arguments': {'num_nota': num}}

    # 4) Qué se modificó/eliminó en una nota
    if (_tiene(t, 'modific', 'elimin') and 'nota' in t_lower) or _tiene(t, 'que se modifico', 'que se elimino'):
        args = {'limite': 50}
        num = _extraer_num_nota(t)
        if num:
            args['num_nota'] = num
        return {'skill': 'monitor_modificaciones_eliminaciones', 'arguments': args}

    # 5) Detectar errores en una nota (duplicados, cantidades en cero...)
    if _tiene(t, 'detect', 'verific') and _tiene(t, 'error') and 'nota' in t_lower:
        args = {'limite': 100}
        num = _extraer_num_nota(t)
        if num:
            args['num_nota'] = num
        return {'skill': 'detector_errores_nota', 'arguments': args}

    # 6) Rendimiento / mejor preparador para asignar
    if _tiene(t, 'rendimiento', 'desempen') and 'preparador' in t_lower:
        return {'skill': 'rendimiento_preparadores_smart_assign', 'arguments': {}}
    if _tiene(t, 'quien prepara mejor', 'mejor preparador', 'a quien le asigno'):
        num = _extraer_num_nota(t)
        return {'skill': 'rendimiento_preparadores_smart_assign',
                'arguments': ({'num_nota': num} if num else {})}

    # 7) Flujo de notas en tiempo real / cómo van las notas (por almacén)
    if _tiene(t, 'flujo de nota') or (_tiene(t, 'como van') and 'nota' in t_lower):
        args = {}
        sede = _extraer_sede(t)
        if sede:
            args['sede'] = sede
        return {'skill': 'flujo_notas_tiempo_real', 'arguments': args}

    # 8) Consulta de nota de entrega (verbos explícitos + número)
    if _tiene(t, 'consult', 'busc', 'revis', 'muestr', 'mostr', 'chequ',
              'dame los datos de', 'dame datos de', 'que paso con', 'que ocurrio con',
              'que hay con', 'dime que paso con') and 'nota' in t_lower:
        num = _extraer_num_nota(t)
        if num:
            return {'skill': 'consultar_nota', 'arguments': {'num_nota': num}}

    # 9) Nota desnuda: cadena aislada de 7-8 números (ej. 72160754)
    if re.fullmatch(r'\d{7,8}', t):
        return {'skill': 'consultar_nota', 'arguments': {'num_nota': t}}

    # 10) Progreso/estado de rutas ("cómo va la ruta CARACAS", "estado de la
    # ruta ZULIA", "estado de las rutas" sin nombre → resumen de todas).
    if (_tiene(t, 'progreso', 'avanc', 'estado') or _tiene(t, 'como va', 'como esta', 'como van')) and 'ruta' in t_lower:
        # Plural o sin nombre específico ("las rutas", "todas las rutas",
        # "rutas activas") → listado completo en vez de exigir un nombre.
        if _tiene(t, 'las rutas', 'todas las rutas', 'rutas activas') or re.search(r'\brutas\b', t_lower):
            return {'skill': 'listar_progreso_rutas', 'arguments': {}}
        nombre_ruta = _extraer_tras(t, 'ruta')
        if nombre_ruta:
            return {'skill': 'progreso_ruta', 'arguments': {'ruta_macro': nombre_ruta}}

    # 11) Operadores inactivos / parados
    if _tiene(t, 'inactiv', 'parad', 'atasc') and ('operador' in t_lower or 'preparador' in t_lower):
        args = {}
        umbral = _extraer_umbral_minutos(t)
        if umbral:
            args['umbral_minutos'] = umbral
        return {'skill': 'operador_inactividad', 'arguments': args}

    # 12) Supervisión de artículo (timeline de movimientos) — antes de
    #     quiebre/auditoría/ubicación para no competir por la misma palabra
    #     "articulo"; requiere verbo de supervisión explícito.
    if _tiene(t, 'supervis', 'investig') and ('articulo' in t_lower or 'producto' in t_lower):
        co_art = _extraer_co_art(t)
        if co_art:
            return {'skill': 'supervision_articulo', 'arguments': {'co_art': co_art}}

    # 13) Cambios de ubicación física de un artículo. Verbo OPCIONAL (v4.56):
    #     "cambió/movió/reubicó de ubicación" dispara igual que la simple
    #     frase sustantiva "ubicación del artículo X" sin ningún verbo —
    #     ambas formas son igual de naturales y la segunda no calzaba antes.
    if 'ubicacion' in t_lower:
        co_art = _extraer_co_art(t)
        if co_art:
            return {'skill': 'cambios_ubicacion_articulo', 'arguments': {'co_art': co_art}}

    # 14) Quiebre de UN artículo puntual (con código) vs. reporte masivo (sin código)
    if 'quiebre' in t_lower:
        co_art = _extraer_co_art(t)
        if co_art:
            return {'skill': 'quiebre_articulo', 'arguments': {'co_art': co_art}}
        return {'skill': 'reporte_quiebres_compras', 'arguments': {}}

    # 15) Auditoría / discrepancia de UN artículo puntual vs. reporte masivo
    if _tiene(t, 'audit', 'discrepan', 'cuadr') and ('articulo' in t_lower or 'deposito' in t_lower or 'entre sedes' in t_lower or 'traslado' in t_lower):
        co_art = _extraer_co_art(t)
        if co_art:
            return {'skill': 'auditoria_articulo', 'arguments': {'co_art': co_art}}
        return {'skill': 'discrepancia_traslados', 'arguments': {}}

    # 16) Cola de surtido prioritario (requiere sede explícita: parámetro obligatorio)
    if _tiene(t, 'surtido prioritario', 'que se puede surtir', 'cola de surtido'):
        sede = _extraer_sede(t)
        if sede:
            return {'skill': 'stock_surtido_prioritario', 'arguments': {'almacen': sede}}

    # 16a-bis) bulto_cerrado_router queda EXCLUIDA del auto-trigger general
    # (ver ALCANCE arriba) porque su modo de enrutamiento por ítems necesita
    # un array estructurado que no se puede extraer de texto libre. PERO su
    # modo proactivo (sin "items": qué bajar del bulto cerrado ANTES de que
    # falte, por volumen+rotación) no recibe ningún array — es seguro
    # dispararlo por frase, siempre y cuando NUNCA se le arme 'items' aquí.
    if _tiene(t, 'bajar del bulto cerrado', 'bajar de bulto cerrado', 'que bajar del deposito',
              'que sacar del deposito', 'anticipar bulto cerrado', 'adelantar bulto cerrado'):
        sede = _extraer_sede(t) or 'sc'
        return {'skill': 'bulto_cerrado_router', 'arguments': {'almacen': sede}}

    # 16b) Facturas/notas pendientes o vencidas de UN cliente (antes de la
    # regla de saldo/cartera general: más específica, prioridad si el
    # usuario menciona explícitamente facturas/notas). No exige la palabra
    # "cliente" en el texto — "facturas vencidas de FAR01680" ya trae el
    # código como identificador inequívoco, exigir "cliente" ahí de más
    # tapaba el caso más común (código sin la palabra explícita).
    #
    # 16b-bis) Lista GLOBAL de clientes con facturas vencidas (plural/general,
    # sin código de cliente puntual) — chequeada ANTES para no perderla contra
    # la regla de un cliente específico. Nunca incluye "por vencer", solo
    # vencidas ya (mismo alcance que la tool).
    if _tiene(t, 'clientes con factura', 'clientes vencid', 'clientes moros', 'clientes deudor',
              'quien me debe', 'quienes me deben', 'lista de deudores'):
        return {'skill': 'clientes_facturas_vencidas', 'arguments': {}}
    if _tiene(t, 'factura', 'notas') and _tiene(t, 'pendient', 'vencid', 'cobrar', 'pagar'):
        co_cli = _extraer_co_art(t) or (_extraer_tras(t, 'cliente', 'de') if 'client' in t_lower else None)
        if co_cli:
            args = {'co_cli': co_cli}
            if 'vencid' in t_lower:
                args['solo_vencidas'] = True
            return {'skill': 'consultar_facturas_cliente', 'arguments': args}
        if 'client' in t_lower and _tiene(t, 'vencid'):
            # "facturas vencidas" + "clientes" (plural) sin código puntual:
            # es el reporte global, no una consulta de un cliente sin datos.
            return {'skill': 'clientes_facturas_vencidas', 'arguments': {}}

    # 16c) Clientes frecuentes que NO han pedido en los últimos 7 días
    # (caída de compra, no clientes dormidos de siempre).
    if _tiene(t, 'sin pedido', 'no han pedido', 'no ha pedido', 'dejaron de pedir',
              'dejaron de comprar', 'no estan comprando', 'no compran') and 'client' in t_lower:
        return {'skill': 'clientes_sin_pedido_semana', 'arguments': {}}

    # 17) Saldo / cartera / deuda de un cliente
    # BUG real reportado en vivo (26/08): "que deuda tiene el cliente X" no
    # disparaba NADA — 'deuda' nunca estuvo en la lista de raíces, ni acá ni
    # en ninguna otra regla. ARA Coder sintetizó su propia tool
    # (obtener_deuda_cliente, Tools/AutoGeneradas/) para resolverlo por su
    # cuenta, pero esa skill NO se enganchó acá a propósito: tiene
    # credenciales SQL Server hardcodeadas y sin cerrar la conexión (mismos
    # bugs anti-zombi corregidos toda esta sesión en otros archivos), y no
    # resuelve el identificador de cliente (exige co_cli exacto) — se prefirió
    # apuntar 'deuda' a consultar_saldo_cliente, ya endurecida (v4.17) y con
    # resolución real de RIF/cédula/código.
    if _tiene(t, 'saldo', 'cuanto debe', 'estado de cuenta', 'cartera', 'deuda') and 'client' in t_lower:
        ident = _extraer_tras(t, 'cliente', 'de')
        if ident:
            return {'skill': 'consultar_saldo_cliente', 'arguments': {'identificador_cliente': ident}}

    # 18) Perfil/datos generales de un cliente (después de saldo, más genérico)
    if _tiene(t, 'consult', 'busc', 'dame los datos de', 'perfil de') and 'client' in t_lower:
        identificador = _extraer_tras(t, 'cliente')
        if identificador:
            # consultar_cliente busca por co_cli (código exacto/parcial), por
            # busqueda (razón social, LIKE) O por telefono — son consultas
            # DISTINTAS en SQL (ver ConsultarClienteTool::buscarEnProfit).
            # Antes SIEMPRE se mandaba como 'busqueda', así que un código real
            # ("12345", "FAR01361") se buscaba como substring dentro del
            # NOMBRE del cliente y no encontraba nada. Un teléfono ("+58...",
            # puros dígitos largos) tampoco es un co_cli — mandarlo así
            # rompía con error SQL de truncamiento (co_cli es una columna
            # angosta) y el mensaje de error del driver, con tildes en
            # CP1252, hacía crashear el json_encode aguas arriba.
            solo_digitos = re.sub(r'\D', '', identificador)
            if identificador.startswith('+') or len(solo_digitos) >= 9:
                clave = 'telefono'
            elif ' ' not in identificador:
                clave = 'co_cli'
            else:
                clave = 'busqueda'
            return {'skill': 'consultar_cliente', 'arguments': {clave: identificador}}

    # 19) Inventario / stock / producto (última: la más genérica, para no
    #     tapar las intenciones específicas de arriba)
    if _RE_INVENTARIO.search(t):
        termino = _extraer_termino_busqueda(t)
        if termino:
            # BUG REAL detectado en vivo (18/08): "busca el articulo MD04668"
            # SIEMPRE se mandaba como 'busqueda' (BuscarInventarioTool solo
            # busca eso contra el NOMBRE/descripción del producto — un código
            # nunca aparece ahí, así que un artículo real con stock daba "Sin
            # resultados"). Si el término tiene forma de código (sin espacios,
            # patrón letras+dígitos), se manda como 'co_art' — que sí hace
            # match exacto contra la columna de código.
            co_art = _extraer_co_art(termino)
            if co_art and co_art.upper() == termino.upper().replace(' ', ''):
                return {'skill': 'buscar_inventario', 'arguments': {'co_art': co_art}}
            return {'skill': 'buscar_inventario', 'arguments': {'busqueda': termino}}

    # Último recurso: catálogo dinámico de skills auto-generadas (v4.68) —
    # solo si ninguna regla fija de arriba matcheó.
    return _detectar_intencion_skill_autogenerada(t)


def _ejecutar_skill_auto(skill: str, arguments: dict):
    """Ejecuta la skill vía el runner PHP (mismo camino que /api/tools/ejecutar).

    Devuelve el dict del runner si tuvo éxito; None si falló (en ese caso el
    flujo cae al LLM, cumpliendo la regla de "intentar primero la skill").
    """
    try:
        from ara_server import _ejecutar_runner_tools  # import local: evita ciclo
    except Exception as e:
        print(f"[ARA Bot] runner de tools no disponible: {e}", flush=True)
        return None
    try:
        resultado = _ejecutar_runner_tools(
            [
                skill,
                json.dumps(arguments, ensure_ascii=False),
                json.dumps(
                    {'usuario': 'bot', 'rol': 'sistema', 'modulo': 'chat_intencion_v4_11'},
                    ensure_ascii=False,
                ),
            ],
            timeout_s=60,
        )
    except Exception as e:
        print(f"[ARA Bot] Skill auto /{skill} falló (se continúa con LLM): {e}", flush=True)
        return None
    if not isinstance(resultado, dict) or not resultado.get('success'):
        print(f"[ARA Bot] Skill auto /{skill} sin éxito (se continúa con LLM): "
              f"{str(resultado)[:200]}", flush=True)
        return None
    # La card vive anidada en resultado.resultado.content (JSON string del
    # runner): extraerla para que el mensaje del bot sea la tarjeta legible.
    try:
        contenido = resultado.get('resultado', {}).get('content', '')
        if isinstance(contenido, str) and contenido.strip().startswith('{'):
            inner = json.loads(contenido)
            card_inner = (inner.get('card')
                          or inner.get('respuesta')
                          or inner.get('mensaje')
                          or inner.get('error')
                          or inner.get('status'))
            if card_inner:
                resultado['card'] = card_inner
    except Exception:
        pass
    if not resultado.get('card'):
        resultado['card'] = (resultado.get('mensaje')
                             or json.dumps(resultado, ensure_ascii=False))
    return resultado


def _formatear_fallback_sql(resultados: list, pregunta: str) -> str:
    """Respuesta de respaldo con datos SQL cuando NIM/DeepSeek/Ollama fallan
    los 3 (raro, pero posible). Antes decía "no encontré PRODUCTOS" siempre
    — engañoso para preguntas que nunca fueron sobre productos (facturas,
    notas, clientes): el bot nunca intentó una búsqueda de inventario ahí,
    así que afirmarlo era mentir sobre lo que en realidad pasó. Mensaje
    genérico y honesto en su lugar."""
    if not resultados:
        return (f"🤖 *{ARA_BOT_NOMBRE}*: No pude resolver \"{pregunta}\" con las "
                f"herramientas disponibles en este momento. Si buscabas un "
                f"producto, verificá el código o nombre; si era otra consulta "
                f"(nota, factura, cliente), probá reformularla con más "
                f"detalle o el número exacto.")
    lines = [f"🤖 *{ARA_BOT_NOMBRE}*: Encontré estos datos en el sistema:"]
    for p in resultados:
        lines.append(
            f"• *{p.get('codigo', 'N/A')}* — {p.get('descripcion', 'Sin descripción')}\n"
            f"  Stock: {p.get('stock_maestro', 0)} unds | "
            f"Bulto cerrado: {p.get('stock_bulto_cerrado', 0)} | "
            f"Ubicación: {p.get('campo7', 'N/A')}"
        )
    return "\n\n".join(lines)


def _procesar_mensaje_ara_bot(mensaje_usuario: str, historial: list = None) -> dict:
    """
    Flujo principal del bot con detección de intención:
    • Métricas globales (totales / SKUs) → consultas de agregación SQL
    • Búsqueda de producto → consulta LIKE en stock_maestro
    • Siempre intenta Ollama primero; si falla, responde con fallback SQL directo

    `historial` (opcional): turnos previos de ESTA conversación
    ([{"role": "user"/"assistant", "content": ...}], más viejo primero) — se
    le pasa tal cual a los proveedores de IA (NIM/DeepSeek) que sí soportan
    contexto multi-turno; las skills/SQL de arriba son de un solo turno por
    naturaleza y no lo necesitan.
    """
    print(f"[ARA Bot] Procesando: {mensaje_usuario}")

    # 0. RECONOCIMIENTO DE INTENCIONES → SKILL (v4.11): la REGLA ABSOLUTA exige
    #    ejecutar la skill mapeada ANTES de cualquier respuesta LLM. Solo si la
    #    skill falla (runner/ERP caído) se cae al flujo conversacional de abajo.
    intencion = _detectar_intencion_skill(mensaje_usuario)
    if intencion:
        skill = intencion['skill']
        print(f"[ARA Bot] Intención detectada → skill /{skill} "
              f"{json.dumps(intencion['arguments'], ensure_ascii=False)}", flush=True)
        resultado_skill = _ejecutar_skill_auto(skill, intencion['arguments'])
        if resultado_skill:
            card = (resultado_skill.get('card')
                    or resultado_skill.get('mensaje')
                    or json.dumps(resultado_skill, ensure_ascii=False))
            print(f"[ARA Bot] Respuesta de la skill /{skill} lista ({len(card)} chars).",
                  flush=True)
            return {"tipo": "skill", "contenido": f"🤖 *{ARA_BOT_NOMBRE}*:\n{card}", "skill": skill, "modelo": f"Tool: {skill}"}

    # 1. Detectar intención: métricas globales vs búsqueda de producto
    es_metricas = _es_consulta_metricas(mensaje_usuario)

    if es_metricas:
        contexto_sql = _consultar_metricas_globales()
        resultados = []
    else:
        # Intentar trazabilidad de movimientos primero (auditoría / notas)
        resultados_mov = _consultar_movimientos_para_bot(mensaje_usuario)
        if resultados_mov:
            nota_ref = resultados_mov[0].get('numero_nota', '')
            cliente_ref = resultados_mov[0].get('cliente', '')
            # El RESPONSABLE real de la nota es notas_entrega.preparador_id —
            # no el 'usuario' de cada movimiento individual, que en escaneos
            # automáticos suele venir como 'sistema' (no es una persona).
            preparador_ref = resultados_mov[0].get('preparador_id', '')
            encabezado = "DATOS TRAZABILIDAD (movimientos_preparador cruzado con notas_entrega):"
            if nota_ref:
                encabezado += f" Nota {nota_ref}"
                if preparador_ref:
                    encabezado += f" — Responsable (preparador_id): {preparador_ref}"
                if cliente_ref:
                    encabezado += f" — Cliente: {cliente_ref}"
            partes = [encabezado]
            for m in resultados_mov[:10]:
                partes.append(
                    f"- {m.get('accion','')} | Art: {m.get('co_art','')} "
                    f"({m.get('descripcion','')}) | Cant: {m.get('cantidad',0)} "
                    f"| Usuario: {m.get('usuario','')} | Fecha: {str(m.get('timestamp',''))[:19]}"
                )
            contexto_sql = "\n".join(partes)
            resultados = []
        else:
            resultados_ubic = _consultar_reubicaciones_para_bot(mensaje_usuario)
            if resultados_ubic:
                art_ref = resultados_ubic[0].get('co_art', '')
                encabezado_ubic = "DATOS REUBICACIÓN (reportes_ubicacion):"
                if art_ref:
                    encabezado_ubic += f" Artículo {art_ref}"
                partes_ubic = [encabezado_ubic]
                for r in resultados_ubic[:10]:
                    partes_ubic.append(
                        f"- Art: {r.get('co_art','')} | De: {r.get('desde','')} "
                        f"→ A: {r.get('hacia','')} | Usuario: {r.get('usuario','')} "
                        f"| Fecha: {str(r.get('fecha',''))[:19]}"
                    )
                contexto_sql = "\n".join(partes_ubic)
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

    # 1.5 Skill SQL ReAct (solo lectura): si las búsquedas fijas no dieron
    #     resultados, el LLM construye un SELECT libre sobre el esquema local
    #     (notas, preparadores, puntos, reubicaciones, etc.).
    skill_sql_activo = False
    if (not es_metricas and not resultados_mov and not resultados
            and contexto_sql.startswith("DATOS ALMACÉN: No se encontraron")):
        try:
            from db_query_tool import (
                es_consulta_sql_operativa,
                generar_select_desde_mensaje,
                ejecutar_consulta_sql_read_only,
                formatear_resultados_para_prompt,
            )
            if es_consulta_sql_operativa(mensaje_usuario):
                gen = generar_select_desde_mensaje(mensaje_usuario)
                if gen.get("status") == "ok":
                    res_sql = ejecutar_consulta_sql_read_only(gen["sql"])
                    bloque = formatear_resultados_para_prompt(res_sql)
                    if bloque:
                        contexto_sql = bloque
                        skill_sql_activo = True
                        print(
                            f"[ARA Bot] Skill SQL: "
                            f"{res_sql.get('total_filas', 0)} filas",
                            flush=True,
                        )
        except Exception as e:
            print(f"[ARA Bot] Skill SQL omitido: {e}", flush=True)

    # 2. Recortar contexto SQL para evitar prompts largos (350 chars; hasta
    #    1500 cuando el skill SQL inyectó resultados reales)
    contexto_sql_limpio = contexto_sql[:1500 if skill_sql_activo else 350]

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
        "3. Mantén respuestas cortas, profesionales y amigables.\n"
        "4. Las skills automáticas (consultar_nota, buscar_inventario, etc.) ya "
        "se intentaron ANTES de esta respuesta, por código — si estás generando "
        "este mensaje es porque NINGUNA coincidió con la pregunta. NUNCA digas "
        "ni insinúes que 'vas a ejecutar', 'estás ejecutando' o 'ejecutaste' una "
        "skill o herramienta: no podés hacerlo desde acá, solo generás texto, y "
        "afirmarlo es inventar una acción que no ocurrió. Si el CONTEXTO no trae "
        "el dato real que te piden (ej. piden la nota de una factura y no hay "
        "ninguna nota en el CONTEXTO), decilo con honestidad ('no tengo una "
        "forma automática de resolver eso todavía con los datos que tengo') — "
        "nunca inventes un resultado, ni positivo ni negativo, que no esté "
        "literalmente en el CONTEXTO.\n"
        "5. Si el CONTEXTO trae 'DATOS TRAZABILIDAD (movimientos_preparador cruzado "
        "con notas_entrega)', esos son los movimientos REALES y VERIFICADOS de esa "
        "nota puntual (ya cotejados nota_id=notas_entrega.id, nunca inventados). "
        "Responde SOLO con lo que aparece ahí: qué artículo, cantidad, acción y "
        "usuario/preparador. Si no hay movimientos en el contexto, di explícitamente "
        "que no hay movimientos registrados para esa nota — nunca inventes un "
        "preparador, artículo o cantidad que no esté en los datos.\n"
        "6. Si preguntan quién es el RESPONSABLE o quién PREPARÓ la nota, usa el "
        "valor 'Responsable (preparador_id)' del encabezado — esa es la persona "
        "real asignada a la nota. El campo 'Usuario' de cada línea de movimiento "
        "es quien ejecutó ESE movimiento puntual y con frecuencia es 'sistema' "
        "(escaneo automático, no una persona) — nunca lo confundas con el "
        "responsable ni lo presentes como si fuera el preparador.\n"
        "7. Si el CONTEXTO trae 'DATOS REUBICACIÓN (reportes_ubicacion)', son "
        "movimientos FÍSICOS reales de un artículo entre puestos del almacén "
        "(De → A, quién y cuándo), independientes de cualquier nota. Responde "
        "solo con esos traslados reales; si no hay reubicaciones en el contexto, "
        "dilo explícitamente en vez de inventar un traslado."
    )
    user_msg = (
        f"[CONTEXTO DE INVENTARIO ARA]:\n{contexto_sql_limpio}\n\n"
        f"[PREGUNTA DEL USUARIO]:\n{mensaje_usuario}"
    )

    # 4. Intentar NVIDIA NIM (cloud, ~1-3s)
    respuesta_ia = _llamar_nim_ara_bot(system_ctx, user_msg, historial_mensajes=historial)
    if respuesta_ia:
        return {"tipo": "ia", "contenido": f"🤖 *{ARA_BOT_NOMBRE}*:\n{respuesta_ia}", "modelo": NVIDIA_MODEL}

    # 4.5 Fallback DeepSeek directo (26/08: NIM sin modelos vivos hoy —
    #     ver nota junto a NVIDIA_MODEL). Va ANTES que Ollama porque es un
    #     modelo real capaz, no el phi3 local limitado (num_predict=50).
    respuesta_deepseek = _llamar_deepseek_para_bot(system_ctx, user_msg, historial_mensajes=historial)
    if respuesta_deepseek:
        return {"tipo": "ia", "contenido": f"🤖 *{ARA_BOT_NOMBRE}*:\n{respuesta_deepseek}", "modelo": "DeepSeek"}

    # 5. Fallback Ollama local (si NVIDIA y DeepSeek no están disponibles)
    # Sin historial acá a propósito: num_ctx=512 (ver _llamar_ollama_para_bot)
    # ya es apenas suficiente para system+pregunta actual con phi3 — meterle
    # turnos previos lo desbordaría y empeoraría la respuesta, no la mejoraría.
    prompt_ollama = f"{system_ctx}\n\n{user_msg}"
    respuesta_ollama = _llamar_ollama_para_bot(prompt_ollama)
    if respuesta_ollama:
        return {"tipo": "ia", "contenido": f"🤖 *{ARA_BOT_NOMBRE}*:\n{respuesta_ollama}", "modelo": "Ollama (local)"}

    # 6. Fallback SQL formateado según tipo de consulta
    if es_metricas:
        fallback = f"🤖 *{ARA_BOT_NOMBRE}*:\n{contexto_sql}"
    else:
        fallback = _formatear_fallback_sql(resultados, mensaje_usuario)
    return {"tipo": "fallback", "contenido": fallback, "modelo": "Búsqueda SQL directa"}


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


def _obtener_o_crear_conversacion_usuarios(usuario_x_id: str, usuario_y_id: str,
                                            conn: sqlite3.Connection = None) -> dict:
    """Resuelve (o crea) el hilo privado par-a-par entre dos usuarios internos.
    Orden canónico alfabético para que A→B y B→A caigan en la MISMA fila."""
    usuario_x_id = str(usuario_x_id or "").strip()
    usuario_y_id = str(usuario_y_id or "").strip()
    if not usuario_x_id or not usuario_y_id:
        raise ValueError("Se requieren ambos usuarios (origen y destino)")
    if usuario_x_id == usuario_y_id:
        raise ValueError("Un usuario no puede iniciar un chat consigo mismo")

    usuario_a_id, usuario_b_id = sorted([usuario_x_id, usuario_y_id])

    close_conn = False
    if conn is None:
        conn = _get_db()
        close_conn = True
    try:
        row = conn.execute(
            "SELECT * FROM conversaciones WHERE usuario_a_id = ? AND usuario_b_id = ?",
            (usuario_a_id, usuario_b_id)
        ).fetchone()
        if row:
            return dict(row)
        cur = conn.execute(
            "INSERT INTO conversaciones (usuario_a_id, usuario_b_id) VALUES (?, ?)",
            (usuario_a_id, usuario_b_id)
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM conversaciones WHERE id = ?", (cur.lastrowid,)
        ).fetchone())
    finally:
        if close_conn:
            conn.close()


def _nombre_usuario(usuario_id: str, conn: sqlite3.Connection = None) -> str:
    """Busca el nombre real en la tabla `usuarios` (id de login), con
    fallback al propio id si no se encuentra (usuario borrado, etc.)."""
    close_conn = False
    if conn is None:
        conn = _get_db()
        close_conn = True
    try:
        row = conn.execute(
            "SELECT nombre FROM usuarios WHERE id = ?", (usuario_id,)
        ).fetchone()
        return row['nombre'] if row else usuario_id
    except Exception:
        return usuario_id
    finally:
        if close_conn:
            conn.close()


def _notificar_push_nuevo_mensaje_worker(conv_id: int, sender_id: str,
                                          contenido: str, tipo: str) -> None:
    """Cuerpo real de la notificación — corre en un hilo aparte (ver
    notificar_push_nuevo_mensaje_async), nunca en el hilo de la request.

    Perf real (31/08): antes esto corría SÍNCRONO dentro de /api/chat/enviar
    (recibía la `conn` de la request y hacía el webpush() bloqueante ahí
    mismo) — cada mensaje de chat de TODA la app pagaba la latencia de una
    llamada HTTP a Google/Mozilla antes de poder responderle al usuario que
    lo mandó. Ahora abre su PROPIA conexión corta (nunca comparte la `conn`
    de la request entre hilos — sqlite3 no es thread-safe así) y corre en
    background; el endpoint ya respondió mucho antes de que esto termine.

    Best-effort total — cualquier fallo se traga acá, nunca debe tumbar el
    envío del mensaje real (que para este punto ya se respondió igual).
    Solo aplica a conversaciones usuario_a_id/usuario_b_id (chat interno
    real entre 2 personas) — las de `contacto_id` son bot/externo y no
    tienen un usuario interno puntual al que avisarle."""
    conn = None
    try:
        conn = _get_db()
        fila = conn.execute(
            "SELECT usuario_a_id, usuario_b_id FROM conversaciones WHERE id = ?",
            (conv_id,)
        ).fetchone()
        if not fila or not fila['usuario_a_id'] or not fila['usuario_b_id']:
            return  # conversación de bot/contacto externo, no de usuario a usuario

        sender_id = str(sender_id or "")
        destinatario = (
            fila['usuario_b_id'] if str(fila['usuario_a_id']) == sender_id
            else fila['usuario_a_id']
        )
        if not destinatario or str(destinatario) == sender_id:
            return

        remitente_nombre = _nombre_usuario(sender_id, conn)
        cuerpo = contenido.strip() if tipo == 'texto' else f"Te envió un archivo ({tipo})"
        if len(cuerpo) > 120:
            cuerpo = cuerpo[:117] + "..."

        push_notif.enviar_push_usuario(
            str(destinatario),
            titulo=f"💬 {remitente_nombre}",
            cuerpo=cuerpo or "Nuevo mensaje",
            url="/",
        )
    except Exception as e:
        print(f"[PUSH] No se pudo notificar mensaje nuevo (conv {conv_id}): {e}", flush=True)
    finally:
        if conn is not None:
            conn.close()


def notificar_push_nuevo_mensaje_async(conv_id: int, sender_id: str,
                                        contenido: str, tipo: str) -> None:
    """Dispara _notificar_push_nuevo_mensaje_worker en un hilo daemon y
    vuelve al toque — mismo patrón ya usado acá mismo para la respuesta
    async del bot (_procesar_respuesta_ara_bot_async)."""
    threading.Thread(
        target=_notificar_push_nuevo_mensaje_worker,
        args=(conv_id, sender_id, contenido, tipo),
        daemon=True,
    ).start()


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
            _purgar_mensajes_bandeja_si_toca()
            q      = (request.args.get('q') or '').strip().lower()
            limite = min(int(request.args.get('limit', 50)), 200)
            usuario_actual_id = (request.args.get('usuario_actual_id') or '').strip()
            conn = _get_db()
            try:
                # --- Rama A: hilos "de contacto" (bot ARA + WhatsApp/TG) ---
                # Siempre globales/compartidos — así el chat de ARA queda
                # anclado igual para todos, sin depender de usuario_actual_id.
                filtro_q = "WHERE LOWER(ct.nombre) LIKE ? OR LOWER(ct.telefono) LIKE ?" if q else ""
                params_a = [f'%{q}%', f'%{q}%'] if q else []
                rows_a = conn.execute(
                    f"""SELECT c.id, c.contacto_id, ct.nombre,
                               ct.telefono, ct.foto_url,
                               c.ultimo_mensaje, c.fecha_actualizacion, c.unread_count,
                               NULL AS otro_usuario_id
                          FROM conversaciones c
                          JOIN contactos ct ON ct.id = c.contacto_id
                         {filtro_q}""",
                    params_a
                ).fetchall()

                rows_b = []
                if usuario_actual_id:
                    # --- Rama B: hilos internos par-a-par donde participo ---
                    filtro_q_b = "AND LOWER(u.nombre) LIKE ?" if q else ""
                    params_b = [usuario_actual_id, usuario_actual_id, usuario_actual_id]
                    if q:
                        params_b.append(f'%{q}%')
                    rows_b = conn.execute(
                        f"""SELECT c.id, NULL AS contacto_id, u.nombre,
                                   u.id AS telefono, '' AS foto_url,
                                   c.ultimo_mensaje, c.fecha_actualizacion, c.unread_count,
                                   u.id AS otro_usuario_id
                              FROM conversaciones c
                              JOIN usuarios u
                                ON u.id = (CASE WHEN c.usuario_a_id = ?
                                                 THEN c.usuario_b_id ELSE c.usuario_a_id END)
                             WHERE (c.usuario_a_id = ? OR c.usuario_b_id = ?)
                               {filtro_q_b}""",
                        params_b
                    ).fetchall()
            finally:
                conn.close()

            data = [dict(r) for r in rows_a] + [dict(r) for r in rows_b]
            data.sort(key=lambda c: c['fecha_actualizacion'] or '', reverse=True)
            data = data[:limite]
            return _ok({"conversaciones": data, "total": len(data)})
        except Exception as e:
            return _err(f"Error listando conversaciones: {e}", 500)


    # ----- 1b) INICIAR/RESOLVER CHAT INTERNO ENTRE 2 USUARIOS -----
    @app.route('/api/chat/conversacion/iniciar', methods=['POST'])
    def chat_iniciar_conversacion_interna():
        try:
            data = request.get_json(silent=True) or request.form
            usuario_origen_id  = (data.get('usuario_origen_id')  or '').strip()
            usuario_destino_id = (data.get('usuario_destino_id') or '').strip()
            if not usuario_origen_id or not usuario_destino_id:
                return _err("Se requieren usuario_origen_id y usuario_destino_id", 400)

            with _DB_LOCK:
                conn = _get_db()
                try:
                    conv = _obtener_o_crear_conversacion_usuarios(
                        usuario_origen_id, usuario_destino_id, conn
                    )
                    otro_id = (conv['usuario_b_id'] if conv['usuario_a_id'] == usuario_origen_id
                               else conv['usuario_a_id'])
                    nombre_otro = _nombre_usuario(otro_id, conn)
                finally:
                    conn.close()

            return _ok({
                "conversacion": conv,
                "otro_usuario_id": otro_id,
                "nombre": nombre_otro,
            })
        except ValueError as e:
            return _err(str(e), 400)
        except Exception as e:
            return _err(f"Error iniciando conversación: {e}", 500)


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
            data = request.get_json(silent=True) or {}
            usuario_actual_id = str(
                data.get('usuario_actual_id') or request.args.get('usuario_actual_id') or ''
            ).strip()

            conn = _get_db()
            try:
                res = conn.execute(
                    """UPDATE conversaciones SET unread_count = 0
                        WHERE id = ?""",
                    (conv_id,)
                )
                # Contactos externos (bot/WhatsApp): el otro lado siempre
                # manda con remitente='cliente'.
                conn.execute(
                    """UPDATE mensajes SET estado = 'leido'
                        WHERE conversacion_id = ? AND remitente = 'cliente'""",
                    (conv_id,)
                )
                # Chat interno par-a-par (v4.39): AMBOS lados mandan con
                # remitente='agente', así que "el mensaje del otro" se
                # identifica por sender_id != quien está marcando como
                # leído — sin esto las flechitas de leído nunca cambian
                # de color en un chat entre dos usuarios internos.
                if usuario_actual_id:
                    conn.execute(
                        """UPDATE mensajes SET estado = 'leido'
                            WHERE conversacion_id = ? AND remitente = 'agente'
                              AND sender_id IS NOT NULL AND sender_id != ''
                              AND sender_id != ?""",
                        (conv_id, usuario_actual_id)
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
                usuario_origen_id  = data.get('usuario_origen_id')
                usuario_destino_id = data.get('usuario_destino_id')
                remitente  = (data.get('remitente') or 'agente').strip().lower()
                tipo       = (data.get('tipo') or 'texto').strip().lower()
                contenido  = (data.get('contenido') or data.get('text') or '').strip()
                sender_id  = data.get('sender_id') or data.get('from') or usuario_origen_id or 'agente'
            else:
                conv_id = telefono = usuario_origen_id = usuario_destino_id = None
                remitente = tipo = contenido = sender_id = None

            if remitente not in ('agente', 'sistema'):
                return _err("Para webhook entrante use /api/chat/webhook", 400)
            if tipo not in ('texto', 'imagen', 'archivo', 'audio'):
                return _err(f"Tipo inválido: {tipo}", 400)
            if not contenido and tipo == 'texto':
                return _err("Contenido vacío", 400)

            with _DB_LOCK:
                conn = _get_db()
                try:
                    # Resolver conversación: por ID directo, chat interno
                    # usuario-a-usuario, o (legado) por teléfono de contacto.
                    if conv_id:
                        row = conn.execute(
                            "SELECT id, contacto_id FROM conversaciones WHERE id = ?",
                            (conv_id,)
                        ).fetchone()
                        if not row:
                            return _err("Conversación inexistente", 404)
                    elif usuario_origen_id and usuario_destino_id:
                        row = _obtener_o_crear_conversacion_usuarios(
                            usuario_origen_id, usuario_destino_id, conn
                        )
                    else:
                        if not telefono:
                            return _err("Se requiere conversacion_id, (usuario_origen_id + "
                                        "usuario_destino_id) o telefono", 400)
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
                    # Push (28/08, a pedido del usuario; async desde 31/08 —
                    # ver nota de perf en notificar_push_nuevo_mensaje_async)
                    notificar_push_nuevo_mensaje_async(conv_id, sender_id, contenido, tipo)
                finally:
                    conn.close()

            # ============================================================
            # INTERCEPTOR ARA - Intelligent (ASÍNCRONO)
            # Si el destinatario es el bot, lanzamos un hilo en segundo
            # plano para que el endpoint responda INMEDIATAMENTE sin
            # bloquear a Waitress.
            # El frontend recogerá la respuesta del bot vía /api/chat/poll
            #
            # AISLAMIENTO DE COMANDOS DE SKILL (v4.6): el LLM nativo JAMÁS se
            # ejecuta para contenido que empiece con '/' (comandos de skill,
            # que corren por el puente CLI de tools) ni para mensajes
            # 'sistema' (p.ej. resultados de skills persistidos por el
            # frontend): impide que el bot "revierta" el comando al flujo de
            # chat regular respondiendo el texto plano.
            # ============================================================
            if (
                contacto_id is not None
                and _es_ara_bot(contacto_id)
                and remitente == 'agente'
                and not contenido.startswith('/')
            ):
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

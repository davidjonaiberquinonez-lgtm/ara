# -*- coding: utf-8 -*-
"""
Módulo de ATENCIÓN AL CLIENTE (WhatsApp Meta Cloud API) — Proyecto ARA

Casi idéntico en forma a chat_routes.py (bandeja interna), pero el otro lado
de cada conversación es un CLIENTE EXTERNO ya autenticado por co_cli (vía
CustomerAuthenticator.php / WhatsappClienteAdapter.php, en PHP) — no un
usuario interno del sistema.

Arquitectura de números (decidida con el usuario, 2026-08-12):
  - UNA sola WABA/App de Meta para todo ARA (WHATSAPP_ACCESS_TOKEN/
    WHATSAPP_WABA_ID en .env, usados solo del lado PHP del webhook).
  - Cada AGENTE (usuario interno) tiene asignado un phone_number_id propio
    en la tabla `meta_numeros`. Sin credenciales reales de Meta todavía:
    se puede asignar un número "SIM-<usuario_id>" con modo_simulado=1 y
    todo el flujo (recibir, ver en bandeja, responder) funciona igual,
    solo que el envío real a Meta se omite (log en vez de POST real).

Puente con PHP: `WhatsAppWebhookController.php` recibe el mensaje entrante
de Meta y escribe DIRECTO en las tablas `atencion_conversaciones`/
`atencion_mensajes` de esta misma BD SQLite (mismo archivo proyecto_ara.db)
— no hay llamada HTTP intermedia entre PHP y este módulo Python.

Endpoints bajo /api/atencion/:
    GET  /api/atencion/numeros                              -> lista números Meta
    POST /api/atencion/numeros/asignar                       -> asigna/crea número a un agente
    GET  /api/atencion/conversaciones?usuario_id=<id>        -> conversaciones del agente
    GET  /api/atencion/conversacion/<id>/mensajes            -> historial paginado
    POST /api/atencion/conversacion/<id>/leer                -> marca no-leídos=0
    POST /api/atencion/conversacion/<id>/tomar                -> agente humano toma el hilo
    POST /api/atencion/conversacion/<id>/soltar                -> devuelve el hilo a la IA
    POST /api/atencion/enviar                                 -> agente humano responde
    GET  /api/atencion/poll?conv_id=<id>&since_id=<id>        -> long-poll ligero
"""
import os
import sqlite3
import time
from threading import Lock

from flask import request

# Reutiliza los mismos helpers/DB_PATH que chat_routes.py (misma BD).
from chat_routes import _get_db, _err, _ok

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DB_LOCK = Lock()


# =============================================================================
# INICIALIZACIÓN DE TABLAS (idempotente)
# =============================================================================
def init_atencion_tables():
    sql_path = os.path.join(BASE_DIR, 'data', 'atencion_cliente_schema.sql')
    conn = _get_db()
    try:
        with open(sql_path, 'r', encoding='utf-8') as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()


# =============================================================================
# UTILIDADES
# =============================================================================
def _numero_de_agente(usuario_id: str, conn: sqlite3.Connection = None) -> dict:
    """Devuelve la fila de meta_numeros del agente, o None si no tiene número
    asignado todavía (debe asignarse uno, aunque sea simulado, antes de recibir
    conversaciones)."""
    close_conn = False
    if conn is None:
        conn = _get_db()
        close_conn = True
    try:
        row = conn.execute(
            "SELECT * FROM meta_numeros WHERE usuario_id = ? AND activo = 1",
            (str(usuario_id),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        if close_conn:
            conn.close()


def _actualizar_atencion_conversacion(conv_id: int, ultimo_msg: str,
                                       incrementar_unread: bool = False,
                                       conn: sqlite3.Connection = None):
    close_conn = False
    if conn is None:
        conn = _get_db()
        close_conn = True
    try:
        if incrementar_unread:
            conn.execute(
                """UPDATE atencion_conversaciones
                       SET ultimo_mensaje = ?, fecha_actualizacion = CURRENT_TIMESTAMP,
                           unread_count = unread_count + 1
                     WHERE id = ?""",
                (ultimo_msg[:200], conv_id)
            )
        else:
            conn.execute(
                """UPDATE atencion_conversaciones
                       SET ultimo_mensaje = ?, fecha_actualizacion = CURRENT_TIMESTAMP
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
def register_atencion_routes(app):

    # ----- 1) NÚMEROS META (ADMIN) --------------------------------
    @app.route('/api/atencion/numeros', methods=['GET'])
    def atencion_listar_numeros():
        try:
            conn = _get_db()
            try:
                rows = conn.execute(
                    "SELECT * FROM meta_numeros ORDER BY fecha_asignado DESC"
                ).fetchall()
            finally:
                conn.close()
            return _ok({"numeros": [dict(r) for r in rows]})
        except Exception as e:
            return _err(f"Error listando números: {e}", 500)

    @app.route('/api/atencion/numeros/asignar', methods=['POST'])
    def atencion_asignar_numero():
        """Asigna un número de WhatsApp Business a un agente. Si no se manda
        phone_number_id (aún no hay credenciales reales de Meta), se genera
        uno simulado 'SIM-<usuario_id>' y modo_simulado=1 — el flujo completo
        funciona igual, solo que no se envía nada real a Meta."""
        try:
            data = request.get_json(silent=True) or request.form
            usuario_id = str(data.get('usuario_id') or '').strip()
            if not usuario_id:
                return _err("Se requiere usuario_id", 400)

            phone_number_id = str(data.get('phone_number_id') or '').strip()
            numero_visible = str(data.get('numero_visible') or '').strip()
            simulado = 1 if not phone_number_id else 0
            if not phone_number_id:
                phone_number_id = f"SIM-{usuario_id}"

            with _DB_LOCK:
                conn = _get_db()
                try:
                    conn.execute(
                        """INSERT INTO meta_numeros
                               (phone_number_id, numero_visible, usuario_id, modo_simulado)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(usuario_id) DO UPDATE SET
                               phone_number_id = excluded.phone_number_id,
                               numero_visible  = excluded.numero_visible,
                               modo_simulado   = excluded.modo_simulado,
                               activo = 1""",
                        (phone_number_id, numero_visible, usuario_id, simulado)
                    )
                    conn.commit()
                    row = conn.execute(
                        "SELECT * FROM meta_numeros WHERE usuario_id = ?", (usuario_id,)
                    ).fetchone()
                finally:
                    conn.close()

            return _ok({"numero": dict(row)}, 201)
        except sqlite3.IntegrityError as e:
            return _err(f"Ese phone_number_id ya está asignado a otro agente: {e}", 409)
        except Exception as e:
            return _err(f"Error asignando número: {e}", 500)

    # ----- 2) LISTAR CONVERSACIONES DE UN AGENTE -------------------
    @app.route('/api/atencion/conversaciones', methods=['GET'])
    def atencion_listar_conversaciones():
        try:
            usuario_id = (request.args.get('usuario_id') or '').strip()
            limite = min(int(request.args.get('limit', 50)), 200)
            if not usuario_id:
                return _err("Parámetro usuario_id obligatorio", 400)

            conn = _get_db()
            try:
                numero = _numero_de_agente(usuario_id, conn)
                if numero is None:
                    return _ok({"conversaciones": [], "total": 0,
                                 "aviso": "Este agente todavía no tiene un número de "
                                          "WhatsApp Business asignado."})
                rows = conn.execute(
                    """SELECT * FROM atencion_conversaciones
                        WHERE phone_number_id = ?
                        ORDER BY fecha_actualizacion DESC LIMIT ?""",
                    (numero['phone_number_id'], limite)
                ).fetchall()
            finally:
                conn.close()

            return _ok({"conversaciones": [dict(r) for r in rows],
                         "total": len(rows), "numero_agente": numero})
        except Exception as e:
            return _err(f"Error listando conversaciones: {e}", 500)

    # ----- 3) HISTORIAL PAGINADO -----------------------------------
    @app.route('/api/atencion/conversacion/<int:conv_id>/mensajes', methods=['GET'])
    def atencion_historial(conv_id: int):
        try:
            limite = min(int(request.args.get('limit', 50)), 200)
            before = request.args.get('before_id', type=int)

            conn = _get_db()
            try:
                exists = conn.execute(
                    "SELECT 1 FROM atencion_conversaciones WHERE id = ?", (conv_id,)
                ).fetchone()
                if not exists:
                    return _err("Conversación inexistente", 404)

                if before:
                    rows = conn.execute(
                        """SELECT * FROM atencion_mensajes
                            WHERE conversacion_id = ? AND id < ?
                            ORDER BY id DESC LIMIT ?""",
                        (conv_id, before, limite)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT * FROM atencion_mensajes
                            WHERE conversacion_id = ?
                            ORDER BY id DESC LIMIT ?""",
                        (conv_id, limite)
                    ).fetchall()
            finally:
                conn.close()

            mensajes = [dict(r) for r in rows][::-1]
            has_more = len(mensajes) >= limite
            return _ok({
                "conversacion_id": conv_id,
                "mensajes": mensajes,
                "has_more": has_more,
                "next_before_id": mensajes[0]["id"] if mensajes and has_more else None,
            })
        except Exception as e:
            return _err(f"Error obteniendo historial: {e}", 500)

    # ----- 4) MARCAR LEÍDO ------------------------------------------
    @app.route('/api/atencion/conversacion/<int:conv_id>/leer', methods=['POST'])
    def atencion_marcar_leido(conv_id: int):
        try:
            conn = _get_db()
            try:
                res = conn.execute(
                    "UPDATE atencion_conversaciones SET unread_count = 0 WHERE id = ?",
                    (conv_id,)
                )
                conn.execute(
                    """UPDATE atencion_mensajes SET estado = 'leido'
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

    # ----- 5) TOMAR / SOLTAR CONTROL (humano vs IA) -----------------
    @app.route('/api/atencion/conversacion/<int:conv_id>/tomar', methods=['POST'])
    def atencion_tomar_control(conv_id: int):
        """El agente humano toma el hilo: la IA deja de autoresponder en esta
        conversación hasta que el agente lo suelte de nuevo."""
        try:
            conn = _get_db()
            try:
                res = conn.execute(
                    "UPDATE atencion_conversaciones SET modo = 'agente' WHERE id = ?",
                    (conv_id,)
                )
                conn.commit()
                if res.rowcount == 0:
                    return _err("Conversación inexistente", 404)
            finally:
                conn.close()
            return _ok({"conversacion_id": conv_id, "modo": "agente"})
        except Exception as e:
            return _err(f"Error tomando control: {e}", 500)

    @app.route('/api/atencion/conversacion/<int:conv_id>/soltar', methods=['POST'])
    def atencion_soltar_control(conv_id: int):
        try:
            conn = _get_db()
            try:
                res = conn.execute(
                    "UPDATE atencion_conversaciones SET modo = 'ia' WHERE id = ?",
                    (conv_id,)
                )
                conn.commit()
                if res.rowcount == 0:
                    return _err("Conversación inexistente", 404)
            finally:
                conn.close()
            return _ok({"conversacion_id": conv_id, "modo": "ia"})
        except Exception as e:
            return _err(f"Error soltando control: {e}", 500)

    # ----- 6) ENVIAR RESPUESTA (AGENTE HUMANO) -----------------------
    @app.route('/api/atencion/enviar', methods=['POST'])
    def atencion_enviar():
        """Respuesta manual de un agente humano. Al enviar, la conversación
        pasa automáticamente a modo='agente' (deja de autoresponder la IA)."""
        try:
            data = request.get_json(silent=True) or request.form
            conv_id = data.get('conversacion_id')
            tipo = (data.get('tipo') or 'texto').strip().lower()
            contenido = (data.get('contenido') or '').strip()
            sender_id = data.get('sender_id') or 'agente'

            if not conv_id:
                return _err("Se requiere conversacion_id", 400)
            if tipo not in ('texto', 'imagen', 'archivo', 'audio'):
                return _err(f"Tipo inválido: {tipo}", 400)
            if not contenido:
                return _err("Contenido vacío", 400)

            with _DB_LOCK:
                conn = _get_db()
                try:
                    conv = conn.execute(
                        "SELECT * FROM atencion_conversaciones WHERE id = ?", (conv_id,)
                    ).fetchone()
                    if not conv:
                        return _err("Conversación inexistente", 404)
                    conv = dict(conv)

                    cur = conn.execute(
                        """INSERT INTO atencion_mensajes
                               (conversacion_id, remitente, tipo, contenido, sender_id, estado)
                           VALUES (?, 'agente', ?, ?, ?, 'enviado')""",
                        (conv_id, tipo, contenido, sender_id)
                    )
                    msg_id = cur.lastrowid
                    conn.execute(
                        "UPDATE atencion_conversaciones SET modo = 'agente' WHERE id = ?",
                        (conv_id,)
                    )
                    _actualizar_atencion_conversacion(conv_id, contenido,
                                                       incrementar_unread=False, conn=conn)
                    conn.commit()
                    msg = dict(conn.execute(
                        "SELECT * FROM atencion_mensajes WHERE id = ?", (msg_id,)
                    ).fetchone())
                    numero = conn.execute(
                        "SELECT * FROM meta_numeros WHERE phone_number_id = ?",
                        (conv['phone_number_id'],)
                    ).fetchone()
                    numero = dict(numero) if numero else None
                finally:
                    conn.close()

            # Envío real a Meta (o log en modo simulado) — best-effort, nunca
            # bloquea la respuesta al agente si Meta no responde a tiempo.
            envio = _enviar_a_meta(numero, conv['telefono_cliente'], contenido, tipo)

            return _ok({"mensaje": msg, "envio_meta": envio}, 201)
        except Exception as e:
            return _err(f"Error enviando respuesta: {e}", 500)

    # ----- 7) LONG-POLL LIGERO --------------------------------------
    @app.route('/api/atencion/poll', methods=['GET'])
    def atencion_poll():
        try:
            conv_id = request.args.get('conv_id', type=int)
            since_id = request.args.get('since_id', type=int, default=0)
            timeout_s = min(request.args.get('timeout', type=int, default=10), 10)

            if conv_id is None:
                return _err("Parámetro 'conv_id' obligatorio", 400)

            deadline = time.time() + timeout_s
            while time.time() < deadline:
                conn = _get_db()
                try:
                    row_conv = conn.execute(
                        "SELECT unread_count FROM atencion_conversaciones WHERE id = ?",
                        (conv_id,)
                    ).fetchone()
                    if not row_conv:
                        return _err("Conversación inexistente", 404)
                    new_msgs = conn.execute(
                        """SELECT * FROM atencion_mensajes
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
                time.sleep(1.5)

            return _ok({
                "conversacion_id": conv_id, "messages": [],
                "last_id": since_id, "unread_count": 0, "timeout": True,
            })
        except Exception as e:
            return _err(f"Error en poll: {e}", 500)

    app.logger.info("Rutas de atención al cliente /api/atencion/* registradas")
    return app


def _enviar_a_meta(numero: dict, telefono_destino: str, contenido: str, tipo: str) -> dict:
    """Envía el mensaje saliente a Meta Cloud API (Graph API) si el número del
    agente tiene credenciales reales; en modo simulado (o si falta el número)
    solo lo registra y devuelve success=False sin reventar el flujo — el
    agente ya vio su respuesta guardada, esto es best-effort adicional."""
    if numero is None:
        return {"enviado": False, "motivo": "agente sin número asignado"}
    if numero.get('modo_simulado'):
        print(f"[Atención Cliente][SIMULADO] Se habría enviado a {telefono_destino} "
              f"desde {numero['phone_number_id']}: {contenido[:80]}")
        return {"enviado": False, "motivo": "modo_simulado", "simulado": True}

    token = os.environ.get('WHATSAPP_ACCESS_TOKEN', '').strip()
    if not token:
        print(f"[Atención Cliente] WHATSAPP_ACCESS_TOKEN no configurado; "
              f"mensaje a {telefono_destino} no enviado a Meta.")
        return {"enviado": False, "motivo": "sin_token_configurado"}

    if tipo != 'texto':
        # v1: solo texto por Graph API real; multimedia queda para cuando
        # haya credenciales reales y se valide el flujo de subida de medios.
        return {"enviado": False, "motivo": "tipo_no_soportado_aun"}

    try:
        import requests
        url = f"https://graph.facebook.com/v20.0/{numero['phone_number_id']}/messages"
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "to": telefono_destino,
                "type": "text",
                "text": {"body": contenido},
            },
            timeout=8,
        )
        if resp.status_code == 200:
            return {"enviado": True, "meta_id": resp.json().get('messages', [{}])[0].get('id')}
        return {"enviado": False, "motivo": f"meta_status_{resp.status_code}",
                "detalle": resp.text[:300]}
    except Exception as e:
        return {"enviado": False, "motivo": f"error: {e}"}

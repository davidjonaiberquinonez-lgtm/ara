# -*- coding: utf-8 -*-
"""ARA-Intelligent — página de chat corporativa (uso interno, con login).

Reutiliza el mismo motor que ya usa el chat interno de ARA
(chat_routes._procesar_mensaje_ara_bot: detección de intención → tools reales
→ fallback SQL → LLM), sin duplicar esa lógica. Lo nuevo aquí:
  - Una página propia (/ara-inteligente), estilo Gemini con animaciones.
  - Chats reales por usuario (como Gemini/ChatGPT): historial a la izquierda,
    nombrados por fecha, hasta 3 anclados, botón "Nuevo chat". SIEMPRE arranca
    en un chat nuevo (privacidad: el propio usuario decide abrir uno viejo
    desde su historial, nunca se auto-carga la última conversación).
  - Cada respuesta guarda cuánto tardó (duracion_ms) y qué "modelo" respondió
    (nombre de la tool si fue una skill, o el modelo de IA/fallback SQL).
"""
import os
import sqlite3
import time
import uuid
from datetime import datetime

from flask import jsonify, render_template, request
from werkzeug.utils import secure_filename

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ARA_DB_PATH", os.path.join(_BASE_DIR, "data", "proyecto_ara.db"))
_ADJUNTOS_DIR = os.path.join(_BASE_DIR, "data", "adjuntos_ocr")

MAX_ANCLADOS = 3

# Extensiones aceptadas por la API OCR de retenciones del usuario (GET
# /api/estado confirmado en vivo, 2026-08-21): jpg/jpeg/png/webp/bmp/pdf.
EXTENSIONES_OCR_VALIDAS = {"bmp", "jpeg", "jpg", "pdf", "png", "webp"}
TAMANO_MAX_ADJUNTO_BYTES = 20 * 1024 * 1024


def _conectar() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def _migrar() -> None:
    conn = _conectar()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ara_inteligente_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id TEXT NOT NULL,
                titulo TEXT NOT NULL,
                anclado INTEGER NOT NULL DEFAULT 0,
                creado_en DATETIME DEFAULT (datetime('now','localtime')),
                actualizado_en DATETIME DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS ara_inteligente_mensajes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL REFERENCES ara_inteligente_chats(id) ON DELETE CASCADE,
                rol TEXT NOT NULL,
                contenido TEXT NOT NULL,
                modelo TEXT,
                duracion_ms INTEGER,
                fecha DATETIME DEFAULT (datetime('now','localtime'))
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _titulo_por_fecha() -> str:
    """Nombre placeholder al crear el chat (antes del primer mensaje real) —
    se reemplaza por _titulo_desde_mensaje() en cuanto llega la primera
    pregunta (a pedido: el nombre del chat es el mensaje, no la fecha)."""
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    ahora = datetime.now()
    return f"{ahora.day} {meses[ahora.month - 1]}, {ahora.strftime('%I:%M %p').lstrip('0')}"


def _titulo_desde_mensaje(pregunta: str) -> str:
    """Título del chat = primera parte del primer mensaje del usuario,
    recortado a un tamaño legible para el sidebar."""
    limpio = " ".join(str(pregunta or "").split())
    if len(limpio) <= 42:
        return limpio or _titulo_por_fecha()
    return limpio[:42].rstrip() + "…"


def register_ara_inteligente_routes(app):
    _migrar()

    @app.route("/ara-inteligente")
    def ara_inteligente_pagina():
        return render_template("ara_inteligente.html")

    # ── Chats ────────────────────────────────────────────────────────────
    @app.route("/api/ara_inteligente/chats", methods=["GET"])
    def ara_inteligente_listar_chats():
        usuario_id = (request.args.get("usuario_id") or "").strip()
        if not usuario_id:
            return jsonify({"status": "error", "mensaje": "Falta usuario_id"}), 400
        conn = _conectar()
        try:
            filas = conn.execute(
                "SELECT * FROM ara_inteligente_chats WHERE usuario_id = ? "
                "ORDER BY anclado DESC, actualizado_en DESC LIMIT 100",
                (usuario_id,),
            ).fetchall()
        finally:
            conn.close()
        return jsonify({"status": "success", "chats": [dict(f) for f in filas]})

    @app.route("/api/ara_inteligente/chats/nuevo", methods=["POST"])
    def ara_inteligente_nuevo_chat():
        data = request.get_json(silent=True) or {}
        usuario_id = str(data.get("usuario_id") or "").strip()
        if not usuario_id:
            return jsonify({"status": "error", "mensaje": "Falta usuario_id"}), 400
        conn = _conectar()
        try:
            cur = conn.execute(
                "INSERT INTO ara_inteligente_chats (usuario_id, titulo) VALUES (?, ?)",
                (usuario_id, _titulo_por_fecha()),
            )
            conn.commit()
            chat_id = cur.lastrowid
        finally:
            conn.close()
        return jsonify({"status": "success", "chat_id": chat_id, "titulo": _titulo_por_fecha()})

    @app.route("/api/ara_inteligente/chats/<int:chat_id>/anclar", methods=["POST"])
    def ara_inteligente_anclar_chat(chat_id: int):
        """Alterna anclado (máx. 3 anclados por usuario a la vez)."""
        conn = _conectar()
        try:
            fila = conn.execute(
                "SELECT usuario_id, anclado FROM ara_inteligente_chats WHERE id = ?", (chat_id,)
            ).fetchone()
            if not fila:
                return jsonify({"status": "error", "mensaje": "Chat no encontrado"}), 404
            nuevo_valor = 0 if fila["anclado"] else 1
            if nuevo_valor:
                total_anclados = conn.execute(
                    "SELECT COUNT(*) FROM ara_inteligente_chats WHERE usuario_id = ? AND anclado = 1",
                    (fila["usuario_id"],),
                ).fetchone()[0]
                if total_anclados >= MAX_ANCLADOS:
                    return jsonify({
                        "status": "error",
                        "codigo_error": "LIMITE_ANCLADOS",
                        "mensaje": f"Ya tenés {MAX_ANCLADOS} chats anclados — desanclá uno primero.",
                    }), 400
            conn.execute("UPDATE ara_inteligente_chats SET anclado = ? WHERE id = ?", (nuevo_valor, chat_id))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"status": "success", "anclado": bool(nuevo_valor)})

    @app.route("/api/ara_inteligente/chats/<int:chat_id>", methods=["DELETE"])
    def ara_inteligente_eliminar_chat(chat_id: int):
        conn = _conectar()
        try:
            conn.execute("DELETE FROM ara_inteligente_chats WHERE id = ?", (chat_id,))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"status": "success"})

    @app.route("/api/ara_inteligente/chats/<int:chat_id>/mensajes", methods=["GET"])
    def ara_inteligente_mensajes_chat(chat_id: int):
        conn = _conectar()
        try:
            filas = conn.execute(
                "SELECT * FROM ara_inteligente_mensajes WHERE chat_id = ? ORDER BY id ASC",
                (chat_id,),
            ).fetchall()
        finally:
            conn.close()
        return jsonify({"status": "success", "mensajes": [dict(f) for f in filas]})

    # ── Preguntar (dentro de un chat) ────────────────────────────────────
    @app.route("/api/ara_inteligente/preguntar", methods=["POST"])
    def ara_inteligente_preguntar():
        data = request.get_json(silent=True) or {}
        pregunta = str(data.get("mensaje") or "").strip()
        usuario_id = str(data.get("usuario_id") or "").strip()
        chat_id = data.get("chat_id")
        if not pregunta:
            return jsonify({"status": "error", "mensaje": "Falta el mensaje"}), 400
        if not chat_id:
            return jsonify({"status": "error", "mensaje": "Falta chat_id"}), 400

        t0 = time.perf_counter()
        modelo = "desconocido"
        try:
            from chat_routes import _procesar_mensaje_ara_bot  # import local: evita ciclo
            resultado = _procesar_mensaje_ara_bot(pregunta)
            respuesta = (
                resultado.get("contenido", "")
                if isinstance(resultado, dict)
                else str(resultado)
            )
            modelo = (resultado.get("modelo") if isinstance(resultado, dict) else None) or modelo
        except Exception as e:
            import traceback
            traceback.print_exc()
            respuesta = f"⚠️ No se pudo procesar la consulta: {e}"
            modelo = "error"
        duracion_ms = int((time.perf_counter() - t0) * 1000)

        titulo_nuevo = None
        try:
            conn = _conectar()
            try:
                # Título del chat = primer mensaje (a pedido: ya no por fecha) —
                # solo se fija UNA vez, en el primer mensaje real del chat.
                ya_tiene_mensajes = conn.execute(
                    "SELECT COUNT(*) FROM ara_inteligente_mensajes WHERE chat_id = ?", (chat_id,)
                ).fetchone()[0]
                if ya_tiene_mensajes == 0:
                    titulo_nuevo = _titulo_desde_mensaje(pregunta)
                    conn.execute(
                        "UPDATE ara_inteligente_chats SET titulo = ? WHERE id = ?",
                        (titulo_nuevo, chat_id),
                    )

                conn.execute(
                    "INSERT INTO ara_inteligente_mensajes (chat_id, rol, contenido) VALUES (?, 'usuario', ?)",
                    (chat_id, pregunta),
                )
                conn.execute(
                    "INSERT INTO ara_inteligente_mensajes (chat_id, rol, contenido, modelo, duracion_ms) "
                    "VALUES (?, 'ara', ?, ?, ?)",
                    (chat_id, respuesta, modelo, duracion_ms),
                )
                conn.execute(
                    "UPDATE ara_inteligente_chats SET actualizado_en = datetime('now','localtime') WHERE id = ?",
                    (chat_id,),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print(f"[ARA-Inteligente] No se pudo guardar el historial: {e}")

        return jsonify({
            "status": "success",
            "respuesta": respuesta,
            "modelo": modelo,
            "duracion_ms": duracion_ms,
            "titulo_chat": titulo_nuevo,
        })

    # ── Persistencia de respuestas de ARA Coder (servicio externo, puerto
    # 8010) ──────────────────────────────────────────────────────────────
    # BUG real reportado por el usuario (24/08): en modo "ARA Coder" el
    # navegador llama DIRECTO a localhost:8010 (ver enviarPregunta() en
    # ara_inteligente.html) y ese servicio es 100% independiente — no
    # conoce ni escribe en la BD de chats de ARA Intelligent. Resultado:
    # las respuestas de ARA Coder nunca quedaban guardadas y desaparecían
    # al recargar la página. Este endpoint solo GUARDA un turno ya resuelto
    # (no vuelve a consultar ningún modelo) para que el historial persista
    # igual que con el resto de los modelos.
    @app.route("/api/ara_inteligente/guardar_externo", methods=["POST"])
    def ara_inteligente_guardar_externo():
        data = request.get_json(silent=True) or {}
        pregunta = str(data.get("mensaje") or "").strip()
        respuesta = str(data.get("respuesta") or "").strip()
        modelo = str(data.get("modelo") or "ARA Coder").strip()
        duracion_ms = data.get("duracion_ms")
        chat_id = data.get("chat_id")
        if not pregunta or not respuesta:
            return jsonify({"status": "error", "mensaje": "Falta 'mensaje' o 'respuesta'"}), 400
        if not chat_id:
            return jsonify({"status": "error", "mensaje": "Falta chat_id"}), 400

        titulo_nuevo = None
        try:
            conn = _conectar()
            try:
                ya_tiene_mensajes = conn.execute(
                    "SELECT COUNT(*) FROM ara_inteligente_mensajes WHERE chat_id = ?", (chat_id,)
                ).fetchone()[0]
                if ya_tiene_mensajes == 0:
                    titulo_nuevo = _titulo_desde_mensaje(pregunta)
                    conn.execute(
                        "UPDATE ara_inteligente_chats SET titulo = ? WHERE id = ?",
                        (titulo_nuevo, chat_id),
                    )
                conn.execute(
                    "INSERT INTO ara_inteligente_mensajes (chat_id, rol, contenido) VALUES (?, 'usuario', ?)",
                    (chat_id, pregunta),
                )
                conn.execute(
                    "INSERT INTO ara_inteligente_mensajes (chat_id, rol, contenido, modelo, duracion_ms) "
                    "VALUES (?, 'ara', ?, ?, ?)",
                    (chat_id, respuesta, modelo, duracion_ms),
                )
                conn.execute(
                    "UPDATE ara_inteligente_chats SET actualizado_en = datetime('now','localtime') WHERE id = ?",
                    (chat_id,),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print(f"[ARA-Inteligente] No se pudo guardar el historial de ARA Coder: {e}")
            return jsonify({"status": "error", "mensaje": str(e)}), 500

        return jsonify({"status": "success", "titulo_chat": titulo_nuevo})

    # ── Adjuntos (botón "+"): OCR de comprobantes/facturas ──────────────
    @app.route("/api/ara_inteligente/adjuntar", methods=["POST"])
    def ara_inteligente_adjuntar():
        """Recibe un archivo (JPG/PNG/WebP/BMP/PDF) del botón "+" del chat,
        lo guarda temporalmente y llama a la tool extraer_campos_factura_ocr
        (puente hacia la API OCR de retenciones del usuario, puerto 5031).
        Guarda el intercambio en el historial igual que un mensaje normal
        (usuario: "📎 archivo.pdf", ara: la tarjeta con los campos)."""
        archivo = request.files.get("archivo")
        usuario_id = str(request.form.get("usuario_id") or "").strip()
        chat_id = request.form.get("chat_id")
        if archivo is None or archivo.filename == "":
            return jsonify({"status": "error", "mensaje": "Falta el archivo (campo 'archivo')."}), 400
        if not usuario_id:
            return jsonify({"status": "error", "mensaje": "Falta usuario_id"}), 400
        if not chat_id:
            return jsonify({"status": "error", "mensaje": "Falta chat_id"}), 400

        nombre_seguro = secure_filename(archivo.filename) or "adjunto"
        ext = nombre_seguro.rsplit(".", 1)[-1].lower() if "." in nombre_seguro else ""
        if ext not in EXTENSIONES_OCR_VALIDAS:
            return jsonify({
                "status": "error",
                "mensaje": f"Formato no soportado ({ext or 'desconocido'}). Use: "
                           + ", ".join(sorted(EXTENSIONES_OCR_VALIDAS)),
            }), 400

        os.makedirs(_ADJUNTOS_DIR, exist_ok=True)
        ruta_guardada = os.path.join(_ADJUNTOS_DIR, f"{uuid.uuid4().hex}_{nombre_seguro}")
        archivo.save(ruta_guardada)
        tamano = os.path.getsize(ruta_guardada)
        if tamano > TAMANO_MAX_ADJUNTO_BYTES:
            os.remove(ruta_guardada)
            return jsonify({"status": "error", "mensaje": "El archivo supera el tamaño máximo permitido (20 MB)."}), 400

        t0 = time.perf_counter()
        pregunta_registrada = f"📎 {nombre_seguro}"
        try:
            from chat_routes import _ejecutar_skill_auto  # import local: evita ciclo
            resultado_skill = _ejecutar_skill_auto("extraer_campos_factura_ocr", {"ruta_archivo": ruta_guardada})
            if resultado_skill and resultado_skill.get("card"):
                respuesta = f"🤖 *ARA*:\n{resultado_skill['card']}"
            else:
                respuesta = ("⚠️ No se pudo leer el archivo con el OCR de retenciones "
                             "(revisa que el servicio esté arriba en el puerto 5031).")
            modelo = "Tool: extraer_campos_factura_ocr"
        except Exception as e:
            import traceback
            traceback.print_exc()
            respuesta = f"⚠️ No se pudo procesar el adjunto: {e}"
            modelo = "error"
        duracion_ms = int((time.perf_counter() - t0) * 1000)

        titulo_nuevo = None
        try:
            conn = _conectar()
            try:
                ya_tiene_mensajes = conn.execute(
                    "SELECT COUNT(*) FROM ara_inteligente_mensajes WHERE chat_id = ?", (chat_id,)
                ).fetchone()[0]
                if ya_tiene_mensajes == 0:
                    titulo_nuevo = _titulo_desde_mensaje(pregunta_registrada)
                    conn.execute(
                        "UPDATE ara_inteligente_chats SET titulo = ? WHERE id = ?",
                        (titulo_nuevo, chat_id),
                    )
                conn.execute(
                    "INSERT INTO ara_inteligente_mensajes (chat_id, rol, contenido) VALUES (?, 'usuario', ?)",
                    (chat_id, pregunta_registrada),
                )
                conn.execute(
                    "INSERT INTO ara_inteligente_mensajes (chat_id, rol, contenido, modelo, duracion_ms) "
                    "VALUES (?, 'ara', ?, ?, ?)",
                    (chat_id, respuesta, modelo, duracion_ms),
                )
                conn.execute(
                    "UPDATE ara_inteligente_chats SET actualizado_en = datetime('now','localtime') WHERE id = ?",
                    (chat_id,),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print(f"[ARA-Inteligente] No se pudo guardar el historial del adjunto: {e}")

        return jsonify({
            "status": "success",
            "respuesta": respuesta,
            "modelo": modelo,
            "duracion_ms": duracion_ms,
            "titulo_chat": titulo_nuevo,
        })

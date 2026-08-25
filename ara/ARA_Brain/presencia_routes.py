# -*- coding: utf-8 -*-
"""Presencia de usuarios en tiempo real — quién está conectado y en qué módulo.

Estado en memoria (no SQLite): es efímero a propósito — perderlo en un
reinicio del servidor es correcto (no queremos "usuarios fantasma"
sobreviviendo a un restart). El frontend manda un latido cada 20s desde
CUALQUIER pantalla con sesión activa (mismo intervalo que ya usan los badges
de mensajes no leídos, ver iniciarBadgesGlobales() en index.html).

Endpoints:
    POST /api/presencia/latido       -> registra/actualiza el latido del usuario
    GET  /api/presencia/conectados   -> lista de usuarios con latido reciente
"""
import threading
import time

from flask import jsonify, request

# Sin latido en este tiempo, se considera desconectado (3x el intervalo de
# 20s del frontend: tolera una pérdida de red puntual sin parpadear la lista).
_TTL_CONECTADO_S = 60

_LOCK = threading.Lock()
_PRESENCIA = {}  # usuario_id -> {"usuario_id", "nombre", "rol", "modulo", "ultimo_latido"}


def register_presencia_routes(app):
    @app.route('/api/presencia/latido', methods=['POST'])
    def presencia_latido():
        data = request.get_json(silent=True) or {}
        usuario_id = str(data.get('usuario_id') or '').strip()
        if not usuario_id:
            return jsonify({"status": "error", "mensaje": "Falta usuario_id"}), 400
        nombre = str(data.get('nombre') or usuario_id).strip()
        rol = str(data.get('rol') or '').strip()
        modulo = str(data.get('modulo') or 'Menú Principal').strip()
        with _LOCK:
            _PRESENCIA[usuario_id] = {
                "usuario_id": usuario_id,
                "nombre": nombre,
                "rol": rol,
                "modulo": modulo,
                "ultimo_latido": time.time(),
            }
        return jsonify({"status": "ok"})

    @app.route('/api/presencia/conectados', methods=['GET'])
    def presencia_conectados():
        ahora = time.time()
        with _LOCK:
            activos = [
                {**v, "segundos_inactivo": round(ahora - v["ultimo_latido"], 1)}
                for v in _PRESENCIA.values()
                if ahora - v["ultimo_latido"] <= _TTL_CONECTADO_S
            ]
        activos.sort(key=lambda u: u["ultimo_latido"], reverse=True)
        return jsonify({"status": "success", "conectados": activos, "total": len(activos)})

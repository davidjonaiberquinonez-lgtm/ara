# -*- coding: utf-8 -*-
"""
API local de consulta de clientes — para que OTROS proyectos (en esta misma
PC o red local) consulten la ficha de un cliente de Profit sin tener que
conocer la tool PHP interna ni el runner CLI.

Es un envoltorio HTTP sobre la tool existente `consultar_cliente`
(app/Services/NvidiaBrain/Tools/Despacho/ConsultarClienteTool.php): busca por
código exacto (co_cli), por nombre/razón social (busqueda) o por teléfono
(telefono, tolera +58/espacios/guiones) contra Profit SQL directo.

Endpoints:
  GET /api/cliente/consulta         → busca y devuelve la ficha (o candidatos).
  GET /api/cliente/estado           → health-check.

Sin autenticación (uso local/red local, mismo criterio que vision_ocr_route.py
y las demás APIs locales de este proyecto). Si en el futuro este endpoint
necesita exponerse fuera de la red local, agregar un header X-ARA-Api-Key
validado contra una env var antes de eso.
"""
import json
import os

from flask import jsonify, request


def register_cliente_routes(app):
    @app.route('/api/cliente/estado', methods=['GET'])
    def cliente_estado():
        """Health-check: confirma que el endpoint y el runner PHP están vivos."""
        return jsonify({
            'status': 'ok',
            'endpoint': '/api/cliente/consulta',
            'metodo': 'GET',
            'parametros': {
                'co_cli': 'Código exacto o parcial del cliente (ej. FAR01680).',
                'busqueda': 'Texto parcial de la razón social (ej. FARMACIA BOTIMARKET).',
                'telefono': 'Número de teléfono (con o sin +58/espacios/guiones).',
            },
            'nota': 'Enviar solo UNO de los 3 parámetros por consulta.',
        })

    @app.route('/api/cliente/consulta', methods=['GET'])
    def cliente_consulta():
        """Busca un cliente por co_cli, busqueda (nombre) o telefono.

        Ejemplos:
          GET /api/cliente/consulta?co_cli=FAR01680
          GET /api/cliente/consulta?busqueda=FARMACIA%20BOTIMARKET
          GET /api/cliente/consulta?telefono=+584124955227

        Respuesta — ficha completa (cuando co_cli resuelve a UN cliente):
          {"ok": true, "co_cli": "...", "razon_social": "...", "rif": "...",
           "telefono": "...", "telefonos": [...], "direccion": "...",
           "email": "...", "limite_credito": N, "saldo": N, "saldo_actual": N,
           "credito_disponible": N, "vendedor": "...", "inactivo": bool,
           "estado": "...", "documentos_pendientes": [...],
           "saldo_total_pendiente": N, "card": "..."}
        Respuesta — modo búsqueda (por busqueda/telefono, varios candidatos):
          {"ok": true, "es_busqueda": true, "mensaje": "...",
           "clientes": [{"co_cli": ..., "razon_social": ..., ...}, ...]}
        Error:
          {"ok": false, "error": "mensaje"}
        """
        co_cli = (request.args.get('co_cli') or '').strip()
        busqueda = (request.args.get('busqueda') or '').strip()
        telefono = (request.args.get('telefono') or '').strip()

        if not co_cli and not busqueda and not telefono:
            return jsonify({
                'ok': False,
                'error': 'Debe enviar uno de los parámetros: co_cli, busqueda o telefono.',
            }), 400

        argumentos = {}
        if co_cli:
            argumentos['co_cli'] = co_cli
        elif busqueda:
            argumentos['busqueda'] = busqueda
        else:
            argumentos['telefono'] = telefono

        try:
            from ara_server import _ejecutar_runner_tools  # import local: evita ciclo
            resultado = _ejecutar_runner_tools(
                [
                    'consultar_cliente',
                    json.dumps(argumentos, ensure_ascii=False),
                    json.dumps({'usuario': 'api_externa', 'rol': 'sistema', 'modulo': 'cliente_api'}, ensure_ascii=False),
                ],
                timeout_s=30,
            )
        except Exception as e:
            return jsonify({'ok': False, 'error': f'No se pudo ejecutar la consulta: {e}'}), 500

        if not isinstance(resultado, dict) or not resultado.get('success'):
            mensaje = (resultado or {}).get('mensaje') or (resultado or {}).get('error') or 'El runner de la tool falló.'
            return jsonify({'ok': False, 'error': mensaje}), 502

        contenido = resultado.get('resultado', {}).get('content', '')
        try:
            payload = json.loads(contenido) if isinstance(contenido, str) else contenido
        except Exception:
            return jsonify({'ok': False, 'error': 'Respuesta de la tool no interpretable como JSON.'}), 502

        codigo_http = 200 if payload.get('ok') else 404
        return jsonify(payload), codigo_http

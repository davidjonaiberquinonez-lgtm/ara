import traceback

from flask import jsonify, request

from ...application.preparation_service import PreparationService


def register_preparation_routes(app, service: PreparationService):
    @app.route("/api/preparacion_hex/nota", methods=["GET"])
    def prep_hex_consulta_nota():
        """Escaneo de código de barras: consulta la nota en la fuente externa y
        la crea en la BD local si no existe (estado 'preparando')."""
        codigo = (request.args.get("codigoBarra") or "").strip()
        if not codigo:
            return jsonify({
                "status": "error",
                "mensaje": "Falta el parámetro codigoBarra",
            }), 400
        try:
            return jsonify(service.procesar_escaneo_nota(codigo))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/preparacion_hex/finalizar", methods=["POST"])
    def prep_hex_finalizar():
        """Finaliza la preparación: actualiza BD local con la regla de negocio
        (>3 items → autochequeo extendido con validación de cantidades;
        ==3 → preparada/Chequeo; <=2 → chequeada/Mesa) y notifica a Legacy.

        JSON acepta también: autochequeo (bool), monto (float), items_chequeados
        (list[{co_art, chequeada}]) enviados por chequeo/registro.php."""
        data = request.get_json(silent=True) or {}
        codigo = str(data.get("codigoBarra") or "").strip()
        preparador = str(data.get("preparador") or data.get("numeroPreparador") or "").strip()
        if not codigo or not preparador:
            return jsonify({
                "status": "error",
                "mensaje": "Faltan codigoBarra y/o preparador (JSON: {'codigoBarra':..., 'preparador':...})",
            }), 400

        autochequeo = bool(data.get("autochequeo") is True or data.get("autochequeo") == "true")
        try:
            monto = float(data.get("monto") or 0)
        except (TypeError, ValueError):
            monto = 0.0
        items_chequeados = data.get("items_chequeados") or data.get("items")
        if not isinstance(items_chequeados, list):
            items_chequeados = None

        try:
            return jsonify(service.finalizar_preparacion(
                codigo, preparador,
                autochequeo=autochequeo,
                monto=monto,
                items_chequeados=items_chequeados,
            ))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

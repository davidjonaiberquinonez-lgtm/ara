import os
import traceback

from flask import jsonify, request, send_file

from auth_sesion import verificar_token_sesion
from ...application.route_service import RouteService
from ...application.user_service import (
    SEDES_VALIDAS,
    get_current_user_sede,
    normalizar_sede,
)
from ...domain.models import DatosVehiculo
from ...domain.ports import IncompleteScanError

_PRINTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repolab_printer.js")


def register_routes_hex_routes(app, service: RouteService):
    @app.route("/api/rutas/catalogo", methods=["GET"])
    def rutas_catalogo():
        """Catálogo dinámico Macro-Rutas → sub-rutas (parseado de /visor/index.php)."""
        try:
            return jsonify(service.get_catalogo_rutas())
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas/disponibles", methods=["GET"])
    def rutas_disponibles():
        """Macro-rutas activas disponibles para despacho (selector dinámico)."""
        try:
            return jsonify(service.listar_rutas_disponibles())
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas/progreso", methods=["GET"])
    def rutas_progreso():
        """Progreso agregado de una Macro-Ruta por nombre (?ruta_macro=X),
        sumando todas las sesiones activas de cualquier operador sobre ella."""
        ruta_macro = request.args.get("ruta_macro", "")
        try:
            return jsonify(service.progreso_ruta_macro(ruta_macro))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas/progreso_todas", methods=["GET"])
    def rutas_progreso_todas():
        """Resumen de TODAS las Macro-Rutas con sesión activa ahora mismo —
        para responder por chat "el estado de las rutas" sin necesitar el
        nombre exacto de cada una."""
        try:
            return jsonify(service.progreso_todas_rutas())
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas/iniciar", methods=["POST"])
    def rutas_iniciar():
        """Inicia sesión de despacho: el usuario activo selecciona la ruta y queda como responsable.

        Validador de sede (v4.25): ANTES de cargar la ruta se exige indicar en
        qué sede se va a realizar (SC | BQTO) — la carga solo trae las notas
        de esa sede (umbral por número de nota, ver RouteService.UMBRAL_SEDE_NOTA).
        Sin `sede` explícita se intenta resolver del perfil del usuario
        (`get_current_user_sede`); si tampoco hay perfil, se bloquea con
        SEDE_NO_SELECCIONADA para que el frontend pregunte al operador.
        """
        data = request.get_json(silent=True) or {}
        user_id = str(data.get("usuario_id") or data.get("user_id") or "").strip()
        ruta_macro = str(data.get("ruta_macro") or "").strip()
        if not user_id:
            return jsonify({"status": "error", "mensaje": "Falta usuario_id"}), 400
        if not ruta_macro:
            return jsonify({
                "status": "error",
                "mensaje": "Falta ruta_macro: seleccione la ruta a despachar.",
                "codigo_error": "RUTA_NO_SELECCIONADA",
            }), 400
        sede = normalizar_sede(data.get("sede")) or normalizar_sede(
            get_current_user_sede(request)
        )
        if not sede:
            return jsonify({
                "status": "error",
                "mensaje": "Falta indicar la sede (SC o BQTO) donde se va a realizar la ruta.",
                "codigo_error": "SEDE_NO_SELECCIONADA",
                "sedes_disponibles": SEDES_VALIDAS,
            }), 400
        try:
            return jsonify(service.iniciar_ruta(user_id, ruta_macro, sede=sede))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas/clasificar_lote", methods=["POST"])
    def rutas_clasificar_lote():
        """Bot de clasificación: asigna cada nota escaneada del lote a su sub-ruta."""
        data = request.get_json(silent=True) or {}
        user_id = str(data.get("usuario_id") or data.get("user_id") or "").strip()
        if not user_id:
            return jsonify({"status": "error", "mensaje": "Falta user_id"}), 400
        try:
            return jsonify(service.clasificar_lote(user_id))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas/verificar_factura", methods=["POST"])
    def rutas_verificar_factura():
        """Cotejo 1-1: verifica la factura contra la sub-ruta asignada por el bot."""
        data = request.get_json(silent=True) or {}
        user_id = str(data.get("usuario_id") or data.get("user_id") or "").strip()
        factura_num = str(data.get("factura_num") or data.get("barcode") or "").strip()
        sub_ruta = data.get("sub_ruta")
        if not user_id or not factura_num:
            return jsonify({"status": "error", "mensaje": "Faltan user_id y/o factura_num"}), 400
        try:
            return jsonify(service.verificar_factura(user_id, factura_num, sub_ruta))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas_hex/mis-items", methods=["GET"])
    def rutas_hex_mis_items():
        """Carga la ruta macro del usuario y sus cajas/facturas con semáforo."""
        user_id = (request.args.get("user_id") or request.args.get("usuario") or "").strip()
        if not user_id:
            return jsonify({"status": "error", "mensaje": "Falta user_id"}), 400
        try:
            return jsonify(service.cargar_ruta_macro_usuario(user_id))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas_hex/escanear", methods=["POST"])
    def rutas_hex_escanear():
        """Marca caja o factura como escaneada (1:1) en la sesión activa."""
        data = request.get_json(silent=True) or {}
        user_id = str(data.get("user_id") or "").strip()
        barcode = str(data.get("barcode") or "").strip()
        tipo = str(data.get("tipo_escaneo") or "caja").strip()
        if not user_id or not barcode:
            return jsonify({"status": "error", "mensaje": "Faltan user_id y/o barcode"}), 400
        try:
            return jsonify(service.procesar_escaneo(user_id, barcode, tipo))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas_hex/deshacer_escaneo", methods=["POST"])
    def rutas_hex_deshacer_escaneo():
        """Deshace un escaneo hecho por error: vuelve el ítem a 0-0 (pendiente)."""
        data = request.get_json(silent=True) or {}
        user_id = str(data.get("user_id") or "").strip()
        nota_num = str(data.get("nota_num") or "").strip()
        if not user_id or not nota_num:
            return jsonify({"status": "error", "mensaje": "Faltan user_id y/o nota_num"}), 400
        try:
            return jsonify(service.deshacer_escaneo(user_id, nota_num))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas/despachar_factura", methods=["POST"])
    def rutas_despachar_factura():
        """Escaneo ÚNICO de la Factura / Nota de Crédito: marca la nota y TODOS
        sus bultos como DESPACHADO en un solo paso (requiere embalaje previo)."""
        data = request.get_json(silent=True) or {}
        user_id = str(data.get("user_id") or data.get("usuario_id") or "").strip()
        barcode = str(data.get("barcode") or data.get("factura_num") or "").strip()
        if not user_id or not barcode:
            return jsonify({"status": "error", "mensaje": "Faltan user_id y/o barcode"}), 400
        try:
            return jsonify(service.despachar_por_factura(user_id, barcode))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas_hex/finalizar", methods=["POST"])
    def rutas_hex_finalizar():
        """Finaliza la ruta: bloqueo estricto de escaneo, divide en sub-rutas y guarda."""
        data = request.get_json(silent=True) or {}
        user_id = str(data.get("user_id") or "").strip()
        if not user_id:
            return jsonify({"status": "error", "mensaje": "Falta user_id"}), 400
        datos_vehiculo = DatosVehiculo(
            ayudantes=str(data.get("ayudantes") or "").strip(),
            chofer=str(data.get("chofer") or "").strip(),
            carro=str(data.get("carro") or data.get("vehiculo") or "").strip(),
            clave_visor=str(data.get("clave_visor") or "").strip(),
            # Campos adicionales SOLO para el rutagrama impreso (v4.37) —
            # opcionales, nunca se envían al cierre real del visor legacy
            # (ese solo usa ayudantes/chofer/carro, arriba).
            responsable_id=str(data.get("responsable_id") or user_id).strip(),
            responsable_nombre=str(data.get("responsable_nombre") or "").strip(),
            chofer_id=str(data.get("chofer_id") or "").strip(),
            chofer_cedula=str(data.get("chofer_cedula") or "").strip(),
            ayudante_id=str(data.get("ayudante_id") or "").strip(),
            ayudante_nombre=str(data.get("ayudante_nombre") or "").strip(),
            ayudante_cedula=str(data.get("ayudante_cedula") or "").strip(),
            vehiculo_descripcion=str(data.get("vehiculo_descripcion") or "").strip(),
            vehiculo_placa=str(data.get("vehiculo_placa") or "").strip(),
            vehiculo_intt=str(data.get("vehiculo_intt") or "").strip(),
            hora_prog_salida=str(data.get("hora_prog_salida") or "").strip(),
        )
        if not datos_vehiculo.chofer or not datos_vehiculo.carro:
            return jsonify({
                "status": "error",
                "mensaje": "Nombre del conductor y credenciales del vehículo son obligatorios.",
            }), 400
        try:
            return jsonify(service.finalizar_y_dividir_ruta(user_id, datos_vehiculo))
        except IncompleteScanError as e:
            return jsonify({
                "status": "error",
                "codigo_error": "ESCANEO_INCOMPLETO",
                "mensaje": e.mensaje,
                "faltan_cajas": e.faltan_cajas,
                "faltan_facturas": e.faltan_facturas,
                "faltan_verificar": e.faltan_verificar,
            }), 409
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas_hex/reporte-finalizadas", methods=["GET"])
    def rutas_hex_reporte():
        """Sub-rutas finalizadas, listas para REPOLAB.

        Query params: user_id, es_admin, usuario_filtro
        - es_admin=false o no se envía: solo las sub-rutas de user_id (como antes).
        - es_admin=true: auditoría de TODOS los operadores (usuario_filtro opcional
          para acotar a uno solo; "Todos"/vacío = todos). Mismo criterio RBAC que
          /api/reportes/discrepancias y /api/reportes/trazabilidad.
        """
        # BUG DE SEGURIDAD real corregido (24/08): es_admin (y, de paso,
        # user_id cuando no es admin) venían del query param sin verificar
        # nada — cualquiera podía mandar es_admin=true para auditoría de
        # TODOS los operadores, o pasar el user_id de otro operador para ver
        # sus rutas finalizadas. Ahora se derivan del token de sesión
        # firmado en /api/login (mismo criterio que /api/reportes/*).
        sesion = verificar_token_sesion(request)
        if sesion is None:
            return jsonify({"status": "error", "mensaje": "Sesión inválida o expirada. Iniciá sesión de nuevo."}), 401
        es_admin = sesion['es_admin']
        user_id = (sesion['id'] if not es_admin
                   else (request.args.get("user_id") or request.args.get("usuario") or "").strip())
        usuario_filtro = (request.args.get("usuario_filtro") or "").strip()
        if not user_id and not es_admin:
            return jsonify({"status": "error", "mensaje": "Falta user_id"}), 400
        try:
            return jsonify(service.get_reportes_finalizadas(user_id, es_admin=es_admin, usuario_filtro=usuario_filtro))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas/verificar_nota_credito", methods=["GET"])
    def rutas_verificar_nota_credito():
        """Verifica una nota de crédito contra la BD real de Profit (dev_cli).

        GET /api/rutas/verificar_nota_credito?numero=72001032
        Trae existencia real + cliente + descripción/motivo + co_tran (ruta/zona).
        """
        numero = (request.args.get("numero") or request.args.get("nota") or "").strip()
        if not numero:
            return jsonify({"status": "error", "mensaje": "Falta el parámetro numero."}), 400
        try:
            return jsonify(service.verificar_nota_credito(numero))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    # ── Macro-Rutas Multi-Sede (embalaje → despacho de facturas → cierre) ──
    @app.route("/api/rutas/macro/estado", methods=["GET"])
    def rutas_macro_estado():
        """Estado de la Macro-Ruta ACTIVA de la sede del usuario en sesión."""
        try:
            sede = get_current_user_sede(request)
            return jsonify(service.get_estado_macro(sede))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas/macro/despachar_factura", methods=["POST"])
    def rutas_macro_despachar_factura():
        """Despacho en Macro-Ruta: escaneo EXCLUSIVO de la Factura.

        Marca la nota en la Macro-Ruta ACTIVA de la sede como DESPACHADO y
        vincula la totalidad de sus bultos sin pedir escaneo por caja."""
        data = request.get_json(silent=True) or {}
        codigo = str(data.get("barcode") or data.get("factura_num") or data.get("num_factura") or "").strip()
        if not codigo:
            return jsonify({"status": "error", "mensaje": "Falta la factura a escanear"}), 400
        try:
            sede = get_current_user_sede(request)
            return jsonify(service.despachar_factura_macro(sede, codigo))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas/macro/cerrar", methods=["POST"])
    def rutas_macro_cerrar():
        """Cierra la Macro-Ruta ACTIVA de la sede y divide automáticamente los
        Rutagramas/Sub-rutas por zona de destino (solo ítems DESPACHADOS)."""
        data = request.get_json(silent=True) or {}
        try:
            sede = get_current_user_sede(request)
            datos_vehiculo = DatosVehiculo(
                ayudantes=str(data.get("ayudantes") or "").strip(),
                chofer=str(data.get("chofer") or "").strip(),
                carro=str(data.get("carro") or data.get("vehiculo") or "").strip(),
                clave_visor=str(data.get("clave_visor") or "").strip(),
            )
            return jsonify(service.cerrar_macro_ruta(sede, datos_vehiculo))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/rutas/macro/listar", methods=["GET"])
    def rutas_macro_listar():
        """Histórico de Macro-Rutas (filtros opcionales ?sede= & ?estado=)."""
        try:
            sede = get_current_user_sede(request)
            estado = (request.args or {}).get("estado") or ""
            registros = service._repositorio.get_macro_rutas(
                sede_id=sede, estado=estado or None
            )
            return jsonify({
                "status": "success",
                "sede_id": sede,
                "macros": registros,
            })
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/static/repolab_printer.js")
    def servir_repolab_printer():
        """Sirve el renderizador de impresión REPOLAB al frontend."""
        if not os.path.exists(_PRINTER_PATH):
            return jsonify({"status": "error", "mensaje": "repolab_printer.js no encontrado"}), 404
        return send_file(_PRINTER_PATH, mimetype="application/javascript")

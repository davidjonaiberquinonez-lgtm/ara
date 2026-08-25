import traceback
from datetime import datetime

from flask import jsonify, request

from ...application.user_service import get_current_user_sede
from ...infrastructure.embalaje_store import EmbalajeLocalStore
from ...infrastructure.legacy_route_adapter import LegacyRouteAdapter

# Evento emitido al finalizar el embalaje de una nota (cierre de bulto).
EVENTO_EMBALAJE_FINALIZADO = "embalaje.finalizado"


def register_embalaje_routes(app, adapter: LegacyRouteAdapter, local_store=None, event_bus=None, mysql_sync=None):
    """Endpoints del Módulo de Embalaje → POST directo al PHP legacy + BD local.

    Consulta y registro se envían directamente a
    actualizar_nota_embalaje.php (la consulta prevalece sobre lista.php
    cuando la nota no existe en la BD local). Cada embalaje confirmado en el
    visor se persiste también en proyecto_ara.db (movimientos_preparador +
    log_puntos) para el Dashboard en tiempo real.

    `mysql_sync` (LegacyMySQLConnector, opcional): además del POST al PHP
    legacy, confirma el embalaje DIRECTO en la BD MySQL del legacy
    (`rep_not.estatus='EMBALADA'` + bloque de embalaje en `gestion` —
    verifi_emb/num_emb/tip_emb/ubicacion3/hora3/num_mesa_emba/cant_items).
    Es un COMPLEMENTO best-effort a la llamada legacy, no un reemplazo: el
    usuario reportó que el registro en `gestion` (puntos del embalador) no
    siempre queda vía el PHP legacy, así que ARA lo garantiza por su cuenta.
    Nunca bloquea ni rompe el flujo si la BD MySQL no responde.

    Al marcar la nota como FINALIZADA (embalada), se emite el evento
    'embalaje.finalizado' hacia el bus interno para que el servicio de Rutas
    vincule la nota empacada (con su total de bultos) al Rutagrama activo.
    """
    if local_store is None:
        local_store = EmbalajeLocalStore()

    @app.route("/api/embalaje/consultar", methods=["POST"])
    def embalaje_consultar():
        data = request.get_json(silent=True) or {}
        codigo = str(data.get("codigo_barra") or data.get("codigoBarra") or "").strip()
        if not codigo:
            return jsonify({"status": "error", "mensaje": "Falta codigo_barra"}), 400
        try:
            return jsonify(adapter.consultar_nota_embalaje(codigo))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

    @app.route("/api/embalaje/registrar", methods=["POST"])
    def embalaje_registrar():
        data = request.get_json(silent=True) or {}
        codigo = str(data.get("codigo_barra") or data.get("codigoBarra") or "").strip()
        if not codigo:
            return jsonify({"status": "error", "mensaje": "Falta codigo_barra"}), 400
        if not str(data.get("numero_embalador") or "").strip():
            return jsonify({"status": "error", "mensaje": "Falta numero_embalador"}), 400
        try:
            # 0. Recalcula la cantidad REAL de ítems (renglones/artículos
            #    DISTINTOS de la nota — mismo criterio "puntos por renglón"
            #    que usan Picking/Chequeo/Inventario en el dashboard, NO la
            #    suma de unidades) contando los `articulos` de la consulta al
            #    visor. NO se confía en `notaInfo.cantidad_items` del PHP
            #    legacy: es un valor roto/constante en "1" sin importar cuántos
            #    artículos tenga la nota (verificado en vivo, 2026-08-20, nota
            #    72167149: 20 artículos reales, notaInfo.cantidad_items="1").
            #    Si la consulta falla o no trae artículos, se cae al valor que
            #    mandó el frontend (nunca bloquea el registro por esto).
            cant_items_frontend = data.get("cant_items")
            cant_items_final = cant_items_frontend
            try:
                consulta = adapter.consultar_nota_embalaje(codigo)
                articulos = [a for a in (consulta.get("articulos") or []) if isinstance(a, dict)]
                if articulos:
                    cant_items_final = len(articulos)
            except Exception as e:
                traceback.print_exc()
                print(f"[Embalaje] No se pudo recalcular cant_items real (se usa el del frontend): {e}")

            # 1. Confirmación en el visor PHP legacy (POST directo)
            res_legacy = adapter.registrar_embalaje(
                codigo_barra=codigo,
                numero_embalador=data.get("numero_embalador"),
                numero_mesa=data.get("numero_mesa"),
                cant_items=cant_items_final,
                cant_cajas=data.get("cant_cajas"),
            )

            # 2. Persistencia local (movimiento + puntos) tras confirmación
            #    exitosa. Si el visor ya la tenía registrada, el anti-duplicado
            #    local evita insertar de nuevo; el flujo nunca se rompe.
            res_local = local_store.registrar_embalaje_local(
                codigo_barra=codigo,
                usuario_id=data.get("numero_embalador"),
                numero_mesa=data.get("numero_mesa"),
                cant_items=cant_items_final,
                cant_cajas=data.get("cant_cajas"),
            )

            # 2b. Confirmación DIRECTA en la BD MySQL del legacy (best-effort,
            #     complementa al PHP legacy — nunca lo reemplaza ni bloquea el
            #     flujo si la BD no responde). Garantiza rep_not.estatus =
            #     'EMBALADA' + los puntos del embalador en `gestion` aunque el
            #     PHP legacy no los haya guardado.
            res_mysql = None
            if mysql_sync is not None:
                try:
                    res_mysql = mysql_sync.confirmar_embalaje(
                        nota=codigo,
                        responsable=data.get("numero_embalador"),
                        total_items=int(str(cant_items_final or 0) or 0),
                        mesa=data.get("numero_mesa") or "0",
                    )
                except Exception as e:
                    traceback.print_exc()
                    res_mysql = {"status": "error", "confirmado": False, "aviso_legacy": str(e)}

            # 3. EVENTO hacia el bus de Rutas: la nota FINALIZADA queda vinculada
            #    automáticamente a la Macro-Ruta ACTIVA de la sede del usuario en
            #    sesión, con su conteo real de bultos (elimina el re-escaneo
            #    manual en despacho). No bloquea el flujo si el bus falla.
            vinculacion = None
            if event_bus is not None:
                try:
                    sede_id = get_current_user_sede(request)
                    evento_embalaje = {
                        "num_nota": codigo,
                        "num_factura": data.get("num_factura") or "",
                        "co_cli": data.get("co_cli") or "",
                        "razon_social": data.get("cliente") or data.get("razon_social") or "",
                        "total_bultos": data.get("cant_cajas") or 0,
                        "sede_id": sede_id,
                        "operador_embalaje": data.get("numero_embalador") or "",
                        "timestamp": datetime.now().isoformat(),
                    }
                    resultados = event_bus.emitir(EVENTO_EMBALAJE_FINALIZADO, evento_embalaje)
                    for r in resultados:
                        if isinstance(r, dict):
                            vinculacion = r
                            break
                except Exception as e:
                    traceback.print_exc()
                    print(f"[Embalaje] Evento al rutagrama falló (no bloqueante): {e}")

            respuesta = dict(res_legacy)
            respuesta["puntos_ganados"] = res_local.get("puntos_ganados", 0.0)
            respuesta["puntos_totales"] = res_local.get("puntos_totales", 0.0)
            respuesta["mysql_directo"] = res_mysql
            respuesta["registro_local"] = res_local
            respuesta["vinculacion_rutagrama"] = vinculacion

            if res_legacy.get("ok"):
                respuesta["message"] = (
                    "Embalaje guardado en legacy y BD local."
                    + (f" +{respuesta['puntos_ganados']:.0f} Puntos" if respuesta["puntos_ganados"] else "")
                )
            elif res_local.get("duplicado") and res_legacy.get("ya_embalada"):
                respuesta["message"] = res_legacy.get("mensaje", "")
            return jsonify(respuesta)
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "mensaje": str(e)}), 500

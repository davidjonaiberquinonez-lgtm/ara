import traceback
from datetime import datetime
from typing import Optional

from ..domain.models import (
    ItemNota,
    NotaAlreadyPreparedError,
    NotaNotFoundError,
    NotaNotVerifiedError,
    NotaPreparacion,
)
from ..domain.notas_sc import es_nota_san_cristobal, resolver_almacen_picking
from ..domain.ports import NotaLocalStorePort, PreparationRepositoryPort

# Estados de la BD local (CHECK constraint de notas_entrega en notas_hexagonal.py)
ESTADO_EN_PREPARACION = "preparando"
ESTADO_PENDIENTE_CHEQUEO = "preparada"  # total_items == UMBRAL → módulo de Chequeo manual
ESTADO_PENDIENTE_MESA = "chequeada"     # total_items != UMBRAL (<=2 o >3) → Mesa / Embalaje (autochequeo)
UMBRAL_CHEQUEO = 3

# Regla de negocio (discriminación > 3 ítems): una nota con MÁS de 3 renglones
# entra al pipeline de AUTOCHEQUEO extendido con validación estricta de las
# cantidades (solicitada vs. chequeada/escaneada) y registra la transacción
# tanto en el PHP (chequeo/registro.php) como en la BD local (log_puntos).

ESTADOS_TERMINALES_LOCAL = {
    "preparada", "chequeada", "embalada", "entregada", "devuelta", "completada",
}

# Idempotencia del picking: si la nota ya llegó a 'chequeada' o un estado
# posterior (embalada/entregada/devuelta/completada), la confirmación ya fue
# procesada. No se trata como error: el servidor responde HTTP 200 con
# status='already_processed' para que el frontend redirija limpio a la lista.
ESTADOS_PICKING_YA_PROCESADO = {
    "chequeada", "embalada", "entregada", "devuelta", "completada",
}


def _imagen_url_chequeo(co_art: str) -> str:
    """URL del PNG real por co_art: MISMA regla del módulo de chequeo.

    El chequeo (notas_hexagonal._enriquecer_items_chequeo → obtener_url_imagen)
    coteja el co_art (strip + upper) y retorna la foto real del artículo desde
    el CDN de Crist Medicals. El picking usa exactamente ese mismo mecanismo,
    sin rutas estáticas ni simuladas. co_art vacío → '' (el frontend aplica el
    placeholder).
    """
    co = str(co_art or "").strip().upper()
    if not co:
        return ""
    return f"https://imagenes.cristmedicals.com/imagenes-v3/imagenes/{co}.jpg"


class PreparationService:
    def __init__(
        self,
        repositorio: PreparationRepositoryPort,
        local_store: NotaLocalStorePort,
        repositorio_sc: Optional[PreparationRepositoryPort] = None,
    ):
        self._repositorio = repositorio
        self._local_store = local_store
        # Fuente SQL directa de notas S/C (CRISTM25): se consulta cuando el
        # código trae serie 'A...' o cuando el Legacy no encontró la nota.
        self._repositorio_sc = repositorio_sc

    # ── Escaneo de código de barras ──────────────────────────────────────────
    def _cargar_nota_externa(self, codigo: str) -> tuple:
        """Resuelve la nota en la fuente externa. Retorna (NotaPreparacion, origen, almacen).

        Discriminación automática de almacén por número de nota
        (resolver_almacen_picking):
          - num_nota >  5000000 → BARQUISIMETO: Legacy PHP (rep_not BQTO).
          - num_nota <= 5000000 → SAN CRISTÓBAL: CRISTM25 directo (Almacenes '01'/'02').
        El alias visual ('A0467959') se conserva como codigo_nota para el
        operador y para el re-escaneo.
        """
        almacen = resolver_almacen_picking(codigo)
        if almacen["origen"] == "BARQUISIMETO":
            nota_ext = self._repositorio.get_nota_by_barcode(almacen["num_nota"])
            return nota_ext, "BARQUISIMETO", almacen
        if self._repositorio_sc is None:
            raise NotaNotFoundError(
                codigo,
                f"La nota {codigo} es de SAN CRISTÓBAL (num_nota <= 5000000) pero la "
                "fuente SQL directa (CRISTM25) no está configurada.",
            )
        nota_sc = self._repositorio_sc.get_nota_sc_por_numero(almacen["num_nota"])
        nota_sc.codigo_nota = codigo
        return nota_sc, "SAN_CRISTOBAL", almacen

    def procesar_escaneo_nota(self, barcode: str) -> dict:
        codigo = str(barcode or "").strip()
        if not codigo:
            return {"status": "error", "mensaje": "Ingrese un código de barras válido."}

        # a. Consulta la información de la nota mediante el Adaptador
        try:
            nota_ext, origen, _almacen = self._cargar_nota_externa(codigo)
        except NotaNotFoundError as e:
            return {"status": "error", "mensaje": str(e), "codigo_error": "NOTA_NO_ENCONTRADA"}
        except NotaNotVerifiedError as e:
            return {"status": "error", "mensaje": str(e), "codigo_error": "NOTA_NO_VERIFICADA"}
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "mensaje": f"Error consultando la fuente de notas: {e}",
                "codigo_error": "ERROR_EXTERNO",
            }

        # b. Verifica si la nota ya existe en la BD local
        existe = self._local_store.find_by_barcode(codigo)
        if existe:
            estado_local = existe.get("estado", "")
            if estado_local in ESTADOS_TERMINALES_LOCAL:
                return {
                    "status": "error",
                    "mensaje": f"La nota {codigo} ya fue procesada (estado: {estado_local}).",
                    "codigo_error": "NOTA_YA_PREPARADA",
                    "estado_bd": estado_local,
                }
            if estado_local == ESTADO_EN_PREPARACION:
                resultado = self._formatear_nota_local(existe)
                resultado["nota"]["nota_reabierta"] = True
                return resultado

        # c. No existe: crea e inserta la nota + renglones en estado EN_PREPARACION
        creada = self._local_store.create_nota(
            nota_ext, estado=ESTADO_EN_PREPARACION, almacen_origen=origen
        )
        self._local_store.insert_items(creada["id"], nota_ext)
        self._local_store.insert_movimiento(
            creada["id"], nota_ext, usuario="sistema", accion="escaneo",
            origen="pendiente", destino=ESTADO_EN_PREPARACION,
        )

        return self._formatear_nota(nota_ext, creada["id"], estado=ESTADO_EN_PREPARACION)

    # ── Finalización de preparación / chequeo ────────────────────────────────
    def finalizar_preparacion(
        self,
        codigo_barra: str,
        preparador: str,
        autochequeo: bool = False,
        monto: float = 0.0,
        items_chequeados: Optional[list] = None,
    ) -> dict:
        """Finaliza la nota discriminando el umbral de ítems.

        - total_items == UMBRAL_CHEQUEO   → 'preparada' (Chequeo manual).
        - total_items <  UMBRAL_CHEQUEO   → 'chequeada' fast-track (autochequeo).
        - total_items >  UMBRAL_CHEQUEO   → 'chequeada' AUTOCHEQUEO EXTENDIDO:
          valida la integridad de cantidades solicitada vs. chequeada y registra
          la transacción en PHP (chequeo/registro.php) y en log_puntos.

        'autochequeo' permite cerrar una nota >3 ítems que ya estaba en
        'preparada' (origen: formulario PHP de chequeo). 'items_chequeados' trae
        las cantidades escaneadas reales (claves co_art/codigo + chequeada/escaneada).
        """
        codigo = str(codigo_barra or "").strip()
        preparador = str(preparador or "").strip()
        if not codigo or not preparador:
            return {"status": "error", "mensaje": "Faltan codigoBarra y/o preparador."}

        registro = self._local_store.find_by_barcode(codigo)
        if not registro:
            return {
                "status": "error",
                "mensaje": f"La nota {codigo} no existe en la BD local. Escanéela primero.",
                "codigo_error": "NOTA_NO_ENCONTRADA",
            }

        total_items = int(registro.get("items_count") or len(registro.get("items", [])) or 0)
        # Discriminación del umbral: nota con MÁS de 3 ítems
        es_nota_mayor_3 = total_items > UMBRAL_CHEQUEO

        estado_actual = registro.get("estado", "")
        # IDEMPOTENCIA: la nota ya fue procesada por el picking (estado
        # 'chequeada' o posterior). No es un error de validación: se responde
        # éxito para que el frontend redirija limpiamente al listado.
        if estado_actual in ESTADOS_PICKING_YA_PROCESADO:
            return {
                "status": "already_processed",
                "mensaje": "La nota ya fue procesada exitosamente.",
                "estado_bd": estado_actual,
                "total_items": total_items,
            }
        # Autochequeo extendido: permite cerrar desde 'preparada' cuando la nota
        # supera los 3 ítems y el proceso llega marcado como autochequeo (PHP).
        permitir_cierre_autochequeo = (
            es_nota_mayor_3 and autochequeo and estado_actual == ESTADO_PENDIENTE_CHEQUEO
        )
        if estado_actual != ESTADO_EN_PREPARACION and not permitir_cierre_autochequeo:
            return {
                "status": "error",
                "mensaje": f"La nota {codigo} no está en preparación (estado: {estado_actual}).",
                "codigo_error": "NOTA_YA_PREPARADA",
                "estado_bd": estado_actual,
            }

        # b. REGLA DE NEGOCIO (discriminación > 3 ítems):
        #    >  3 items → AUTOCHEQUEO extendido + validación estricta de cantidades
        #    == 3 items → Chequeo manual (preparada / módulo de Chequeo)
        #    <  3 items → auto-chequeo fast-track: 'chequeada' + despacho + doble puntos
        validacion_integridad = {"ok": True, "discrepancias": []}
        if es_nota_mayor_3:
            validacion_integridad = self._validar_integridad_cantidades(
                registro, items_chequeados
            )
            estado_destino = ESTADO_PENDIENTE_MESA
            siguiente_modulo = "PENDIENTE_MESA"
            fasttrack = True
        elif total_items >= UMBRAL_CHEQUEO:
            estado_destino = ESTADO_PENDIENTE_CHEQUEO
            siguiente_modulo = "PENDIENTE_CHEQUEO"
            fasttrack = False
        else:
            estado_destino = ESTADO_PENDIENTE_MESA
            siguiente_modulo = "PENDIENTE_MESA"
            fasttrack = True

        # ── GATE DE INTEGRIDAD 1:1 (cierre transaccional) ─────────────────────
        # Una nota con MÁS de 3 ítems NO se cierra ni se envía al Legacy cuando
        # la sumatoria solicitada/escaneada no coincide ítem por ítem: en vez de
        # desviarla a revisión con falsos positivos de "Faltan artículos por
        # cargar", la nota PERMANECE en su estado intermedio real y el reporte
        # vuelve al operador para completar el escaneo.
        if es_nota_mayor_3 and not validacion_integridad["ok"]:
            return {
                "status": "error",
                "codigo_error": "NOTA_INCOMPLETA",
                "mensaje": (
                    f"La nota {codigo} tiene "
                    f"{len(validacion_integridad['discrepancias'])} renglón(es) sin "
                    "coincidir solicitada/escaneada. Complete el escaneo antes de "
                    "cerrar (la nota NO fue enviada a revisión)."
                ),
                "discrepancias": validacion_integridad["discrepancias"],
                "estado_bd": estado_actual,
                "total_items": total_items,
                "autochequeo": es_nota_mayor_3 or bool(autochequeo),
            }

        # Renglones escaneados reales (para el sync por ítem en reng_nde):
        #  1) los que trae el formulario PHP de chequeo (items_chequeados);
        #  2) fallback: lo persistido en la BD local (cantidad_preparada).
        renglones_escaneados = self._normalizar_renglones_escaneados(
            items_chequeados, registro
        )

        # a. Actualiza el registro en la BD local (estado + preparador + trazabilidad)
        #    preparador_id = id real del operador en usuarios (nunca '0'/'null'/'admin1')
        preparador_id = self._local_store.resolver_id_usuario(preparador) or preparador
        res_finalize = self._local_store.finalize_nota(registro["id"], estado_destino, preparador_id)
        estado_persistido = res_finalize.get("estado", estado_destino)
        nota_local = self._registro_a_nota(registro)
        self._local_store.insert_movimiento(
            registro["id"], nota_local, usuario=preparador_id, accion="preparar",
            origen=estado_actual, destino=estado_persistido,
        )

        # c. Notifica al endpoint Legacy/Profit actualizando el número de preparador
        #    y, en Fast-Track, registra el despacho en /visor/registro.php y el
        #    AUTOCHEQUEO real en chequeo/registro.php (gestion.php).
        #    Notas S/C (almacen_origen='SAN_CRISTOBAL') NO existen en el rep_not
        #    de Barquisimeto: su preparación es 100% local y no se notifica al
        #    Legacy (la serie 'A' puede no venir si se escaneó solo el número).
        es_sc = es_nota_san_cristobal(codigo) or (
            str(registro.get("almacen_origen") or "") == "SAN_CRISTOBAL"
        )
        legacy_synced = True
        aviso_legacy = None
        res_registro = None
        res_autochequeo = None
        if not es_sc:
            # c.1 Notifica al Legacy el número de preparador (assign_preparer).
            #     Si el Legacy tarda más de 30s (timeout tras reintento) o está
            #     fuera de línea, NO se tumba la transacción local: la nota ya
            #     quedó guardada en ARA (finalize_nota + puntos). El evento se
            #     registra y la respuesta expone legacy_synced=false.
            try:
                self._repositorio.assign_preparer(codigo, preparador_id, total_items)
            except NotaNotVerifiedError as e:
                legacy_synced = False
                aviso_legacy = "Preparación guardada localmente. Pendiente sincronización con Legacy."
                print(
                    f"[PreparationService] ⚠️ Legacy fuera de tiempo ({codigo}): "
                    f"la preparación local ya fue guardada. Detalle: {e}"
                )
            except Exception as e:
                legacy_synced = False
                aviso_legacy = f"No se pudo notificar al sistema Legacy/Profit: {e}"
                print(f"[PreparationService] {aviso_legacy}")
                traceback.print_exc()
            else:
                # c.2 Solo si el preparador se asignó en el Legacy, continuar con
                #     el Fast-Track: despacho en /visor/registro.php y AUTOCHEQUEO
                #     real en chequeo/registro.php (ambos ya best-effort).
                if fasttrack:
                    res_registro = self._repositorio.registrar_despacho_legacy(
                        codigo, preparador_id, total_items,
                        renglones_escaneados=renglones_escaneados,
                    )
                    if res_registro and res_registro.get("aviso_legacy"):
                        aviso_legacy = res_registro["aviso_legacy"]
                        print(f"[PreparationService] Aviso Legacy (registro despacho): {aviso_legacy}")
                    # Chequeo Automático (< 3 ítems): sincroniza el autochequeo con el
                    # PHP real de chequeo (Mesa 0 + hora actual; timeout corto interno;
                    # nunca bloquea ni rompe).
                    res_autochequeo = self._repositorio.registrar_autochequeo_php(
                        codigo,
                        preparador_id,
                        mesa="0",
                        hora=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        renglones_escaneados=renglones_escaneados,
                    )
                    if res_autochequeo and res_autochequeo.get("aviso_legacy"):
                        print(
                            f"[PreparationService] Aviso Legacy (autochequeo registro.php): "
                            f"{res_autochequeo['aviso_legacy']}"
                        )
        else:
            print(f"[PreparationService] Nota S/C {codigo}: sin notificación al Legacy (rep_not BQTO).")

        # d. Registra puntos de PREPARACIÓN en log_puntos (1.0 pt/renglón, anti-duplicado)
        resultado_puntos = self._local_store.registrar_puntos_preparacion(
            preparador_id, codigo, total_items
        )

        # e. Fast-Track: doble puntuación — suma puntos de CHEQUEO (1.0 pt/renglón)
        resultado_puntos_chequeo = {"registrado": False, "puntos": 0.0, "motivo": "Sin fast-track"}
        if fasttrack:
            resultado_puntos_chequeo = self._local_store.registrar_puntos_chequeo(
                preparador_id, codigo, total_items
            )

        # f. Estado de advertencia informativa cuando el Legacy no pudo ser
        #    notificado (timeout/fuera de línea): la preparación local en ARA ya
        #    quedó guardada, el cliente recibe success con legacy_synced=false.
        mensaje_final = (
            f"Nota {codigo} finalizada: {total_items} items → {siguiente_modulo}"
        )
        if not legacy_synced:
            mensaje_final = "Preparación guardada localmente. Pendiente sincronización con Legacy"

        return {
            "status": "success",
            "mensaje": mensaje_final,
            "legacy_synced": legacy_synced,
            "siguiente_modulo": siguiente_modulo,
            "estado_bd": estado_persistido,
            "preparador_id": preparador_id,
            "total_items": total_items,
            "preparador": preparador,
            "fasttrack": fasttrack,
            "auto_chequeada": fasttrack,
            "es_nota_mayor_3": es_nota_mayor_3,
            "autochequeo": es_nota_mayor_3 or bool(autochequeo),
            "integridad_ok": validacion_integridad["ok"],
            "discrepancias": validacion_integridad["discrepancias"],
            "renglones_escaneados": renglones_escaneados,
            "monto": round(float(monto or 0), 2),
            "aviso_legacy": aviso_legacy,
            "registro_despacho": res_registro,
            "registro_autochequeo": res_autochequeo,
            "puntos": resultado_puntos.get("puntos", 0.0),
            "puntos_registrados": resultado_puntos.get("registrado", False),
            "puntos_chequeo": resultado_puntos_chequeo.get("puntos", 0.0),
            "puntos_chequeo_registrados": resultado_puntos_chequeo.get("registrado", False),
        }

    @staticmethod
    def _normalizar_renglones_escaneados(
        items_chequeados: Optional[list],
        registro: dict,
    ) -> list:
        """Normaliza las cantidades escaneadas reales a [{co_art, cantidad}].

        Fuente 1: items_chequeados del formulario PHP de chequeo (claves
        co_art/codigo y chequeada/escaneada/cantidad). Fuente 2 (fallback):
        cantidad_preparada persistida en la BD local por ítem. Se usa para
        sincronizar reng_nde POR ÍTEM (estado intermedio fiel al escaneo).
        """
        escaneadas: dict = {}
        for it in (items_chequeados or []):
            if not isinstance(it, dict):
                continue
            co = str(it.get("co_art") or it.get("codigo") or it.get("cod") or "").strip().upper()
            if not co:
                continue
            try:
                escaneadas[co] = float(
                    it.get("chequeada") or it.get("escaneada") or it.get("cantidad") or 0
                )
            except (TypeError, ValueError):
                escaneadas[co] = 0.0

        if not escaneadas:
            for i in registro.get("items", []):
                co = str(i.get("co_art") or "").strip().upper()
                if not co:
                    continue
                try:
                    cant = float(i.get("cantidad_preparada") or i.get("escaneada") or 0)
                except (TypeError, ValueError):
                    cant = 0.0
                escaneadas[co] = cant

        return [
            {"co_art": co, "cantidad": cant}
            for co, cant in escaneadas.items()
        ]

    def _validar_integridad_cantidades(
        self,
        registro: dict,
        items_chequeados: Optional[list] = None,
    ) -> dict:
        """Valida ítem por ítem la coincidencia cantidad solicitada vs. chequeada.

        - 'items_chequeados' (opcional) trae las cantidades escaneadas reales
          desde el formulario PHP de chequeo (claves co_art/codigo y
          chequeada/escaneada/cantidad).
        - Sin items_chequeados se usa 'cantidad_preparada' persistida en
          detalle_nota (0 si la nota aún no fue chequeada → FALTANTE).
        - NO bloquea el flujo: el reporte se expone en la respuesta para que el
          supervisor corrija o apruebe.
        """
        escaneadas: dict = {}
        for it in (items_chequeados or []):
            if not isinstance(it, dict):
                continue
            co = str(it.get("co_art") or it.get("codigo") or it.get("cod") or "").strip().upper()
            if not co:
                continue
            try:
                escaneadas[co] = float(
                    it.get("chequeada") or it.get("escaneada") or it.get("cantidad") or 0
                )
            except (TypeError, ValueError):
                escaneadas[co] = 0.0

        discrepancias = []
        for i in registro.get("items", []):
            co = str(i.get("co_art") or "").strip().upper()
            des = str(i.get("descripcion") or "")
            try:
                solicitada = float(i.get("cantidad_solicitada") or 0)
            except (TypeError, ValueError):
                solicitada = 0.0

            chequeada = escaneadas.get(co)
            if chequeada is None:
                try:
                    chequeada = float(i.get("cantidad_preparada") or i.get("escaneada") or 0)
                except (TypeError, ValueError):
                    chequeada = 0.0

            if abs(solicitada - chequeada) < 1e-9:
                continue
            discrepancias.append({
                "co_art": co or str(i.get("co_art") or ""),
                "descripcion": des,
                "solicitada": solicitada,
                "chequeada": chequeada,
                "diferencia": round(chequeada - solicitada, 4),
                "tipo": "EXCESO" if chequeada > solicitada else "FALTANTE",
            })
        return {"ok": not discrepancias, "discrepancias": discrepancias}

    # ── Formateo de respuestas ───────────────────────────────────────────────
    @staticmethod
    def _estatus_item(solicitada: float, escaneada: float) -> str:
        """Estatus operativo del ítem: PENDIENTE | ESCANEADO | COMPLETO."""
        if escaneada >= solicitada and solicitada > 0:
            return "COMPLETO"
        if escaneada > 0:
            return "ESCANEADO"
        return "PENDIENTE"

    @staticmethod
    def _etiqueta_almacen(origen: str) -> str:
        """Metadato del visor: 'SAN_CRISTOBAL (01/02)' o 'BARQUISIMETO (04)'."""
        if str(origen or "") == "SAN_CRISTOBAL":
            return "SAN_CRISTOBAL (01/02)"
        return "BARQUISIMETO (04)"
    def _formatear_nota(self, nota: NotaPreparacion, nota_id: int, estado: str) -> dict:
        items = [
            {
                "co_art": it.codigo_art,
                "descripcion": it.descripcion,
                "cantidad": it.cantidad,
                "unidad": it.unidad,
                "campo7": it.campo7 or "",
                "reng_nd": it.reng_nd,
                "co_alma": it.co_alma or "01",
            }
            for it in nota.items
        ]
        self._local_store.enriquecer_con_stock(items)
        # DTO de cada tarjeta de picking: descripción real (o fallback legible),
        # imagen CDN por co_art (MISMO mecanismo del módulo de chequeo:
        # obtener_url_imagen en notas_hexagonal/_enriquecer_items_chequeo) y
        # almacén de despacho (co_alma).
        for it in items:
            co = str(it.get("co_art") or "").strip()
            if not it.get("descripcion"):
                it["descripcion"] = f"ARTÍCULO {co}" if co else "SIN DESCRIPCIÓN"
            # La URL del PNG real por co_art se construye SIEMPRE con la misma
            # regla del chequeo (CDN cristmedicals), nunca con rutas estáticas.
            it["imagen_url"] = _imagen_url_chequeo(co)
        # Notas S/C: la ubicación REAL viene en campo7 de CRISTM25, no en el
        # stock_maestro de Barquisimeto (que guardaría una ubicación BQTO).
        origen_sc = (
            nota.almacen_origen == "SAN_CRISTOBAL"
            or es_nota_san_cristobal(nota.codigo_nota)
        )
        if origen_sc:
            for it in items:
                if it["campo7"]:
                    it["ubicacion"] = it["campo7"]
        return {
            "status": "success",
            "nota": {
                "id": nota_id,
                "codigo_nota": nota.codigo_nota,
                "codigo_cliente": nota.codigo_cliente,
                "nombre_cliente": nota.nombre_cliente,
                "estado": estado,
                "almacen_origen": self._etiqueta_almacen(nota.almacen_origen),
                "items": [
                    {
                        "codigo_art": it["co_art"],
                        "descripcion": it["descripcion"],
                        "cantidad": float(it["cantidad"]),
                        "cantidad_requerida": float(it["cantidad"]),
                        "cantidad_escaneada": float(it.get("cantidad_escaneada", 0)),
                        "estatus": it.get("estatus") or self._estatus_item(
                            float(it["cantidad"]), float(it.get("cantidad_escaneada", 0))
                        ),
                        "unidad": it["unidad"],
                        "ubicacion": it["ubicacion"],
                        "campo7": it["campo7"] or "",
                        "co_alma": it["co_alma"],
                        "imagen_url": it["imagen_url"],
                        "codigo_barra": it["codigo_barra"],
                        "reng_nd": it["reng_nd"],
                    }
                    for it in items
                ],
            },
        }

    def _formatear_nota_local(self, registro: dict) -> dict:
        items = [
            {
                "co_art": i.get("co_art", ""),
                "descripcion": i.get("descripcion", ""),
                "cantidad": i.get("cantidad_solicitada", 0),
                "cantidad_escaneada": i.get("cantidad_preparada", 0),
                "unidad": i.get("unidad_medida", "UND"),
                "campo7": i.get("campo7") or "",
                "reng_nd": i.get("reng_num") or 0,
                "co_alma": i.get("co_alma") or "01",
            }
            for i in registro.get("items", [])
        ]
        self._local_store.enriquecer_con_stock(items)
        for it in items:
            co = str(it.get("co_art") or "").strip()
            if not it.get("descripcion"):
                it["descripcion"] = f"ARTÍCULO {co}" if co else "SIN DESCRIPCIÓN"
            it["imagen_url"] = _imagen_url_chequeo(co)
        es_sc = (
            str(registro.get("almacen_origen") or "") == "SAN_CRISTOBAL"
            or es_nota_san_cristobal(str(registro.get("numero_nota") or ""))
        )
        if es_sc:
            for it in items:
                if it["campo7"]:
                    it["ubicacion"] = it["campo7"]
        return {
            "status": "success",
            "nota": {
                "id": registro.get("id"),
                "codigo_nota": registro.get("numero_nota", ""),
                "codigo_cliente": str(registro.get("co_cli") or registro.get("codigo_cliente") or ""),
                "nombre_cliente": registro.get("cliente", ""),
                "estado": registro.get("estado", ""),
                "almacen_origen": self._etiqueta_almacen(registro.get("almacen_origen")),
                "items": [
                    {
                        "codigo_art": it["co_art"],
                        "descripcion": it["descripcion"],
                        "cantidad": float(it["cantidad"]),
                        "cantidad_requerida": float(it["cantidad"]),
                        "cantidad_escaneada": float(it["cantidad_escaneada"] or 0),
                        "estatus": self._estatus_item(
                            float(it["cantidad"]), float(it["cantidad_escaneada"] or 0)
                        ),
                        "unidad": it["unidad"],
                        "ubicacion": it["ubicacion"],
                        "campo7": it["campo7"] or "",
                        "co_alma": it["co_alma"],
                        "imagen_url": it["imagen_url"],
                        "codigo_barra": it["codigo_barra"],
                        "reng_nd": it["reng_nd"],
                    }
                    for it in items
                ],
            },
        }

    @staticmethod
    def _registro_a_nota(registro: dict) -> NotaPreparacion:
        items = []
        for i in registro.get("items", []):
            try:
                cant = float(i.get("cantidad_solicitada") or 0)
            except (TypeError, ValueError):
                cant = 0.0
            items.append(
                ItemNota(
                    codigo_art=str(i.get("co_art") or ""),
                    descripcion=str(i.get("descripcion") or ""),
                    cantidad=max(cant, 0.0),
                    campo7=str(i.get("campo7") or ""),
                    reng_nd=int(i.get("reng_num") or 0),
                )
            )
        return NotaPreparacion(
            codigo_nota=str(registro.get("numero_nota") or ""),
            codigo_cliente=str(registro.get("co_cli") or registro.get("codigo_cliente") or ""),
            nombre_cliente=str(registro.get("cliente") or ""),
            items=items,
            almacen_origen=str(registro.get("almacen_origen") or ""),
        )

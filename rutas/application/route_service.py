import json
import os
import re
import time
import traceback
from datetime import datetime
from typing import Dict, List, Optional

from ..domain.models import (
    DatosVehiculo,
    ESTADO_ITEM_DESPACHADO,
    ESTADO_ITEM_PENDIENTE,
    ESTADO_MACRO_ACTIVA,
    ESTADO_MACRO_CERRADA,
    ItemMacroRuta,
    ItemRuta,
    MacroRuta,
    SubRutaFinalizada,
)
from ..domain.ports import IncompleteScanError, RouteLegacyPort, RouteRepositoryPort
from .event_bus import get_event_bus
from .user_service import SEDES_VALIDAS, get_current_user_sede, normalizar_sede


def _normalizar_codigo_serie(codigo: str) -> str:
    """Códigos impresos con letra de serie + primer dígito reemplazando el
    prefijo real '72' de la nota (verificado en vivo 2026-08-20 contra
    `rep_not` con DOS letras distintas — la regla es general, no específica
    de una letra):
      - 'A2154234' -> real '72154234' (EMBALADA)
      - 'B0068080' -> real '72068080' (PREPARACION, zona Trujillo)
    En ambos casos se quitan los primeros 2 caracteres (letra + 1 dígito) y
    se reemplazan por '72', dejando los últimos 6 dígitos intactos — NO es
    "la letra sustituye un solo dígito" (esa primera hipótesis quedó
    descartada con el caso 'B', que también reemplaza el dígito siguiente).
    También cubre notas de crédito impresas SIN letra, con '71' en vez de
    '72' al inicio (ej. '71104093' -> real '72104093', EMBALADA — verificado
    en vivo 2026-08-20, mismo día que los casos con letra: un dígito
    distinto, el resto igual).

    Si el código no calza NINGUNO de esos patrones exactos, se devuelve
    igual — nunca se inventa una sustitución para un formato no verificado."""
    codigo = str(codigo or "").strip().upper()
    m = re.match(r"^[A-Z](\d{7})$", codigo)
    if m:
        return "72" + m.group(1)[1:]
    m = re.match(r"^71(\d{6})$", codigo)
    if m:
        return "72" + m.group(1)
    return codigo


def _print_seguro(texto: str) -> None:
    """print() a prueba de consola Windows cp1252 (v4.25).

    BUG real encontrado en vivo: los prints con '→' en los handlers del
    evento 'embalaje.finalizado' (vincular_nota_a_rutagrama_activa,
    on_embalaje_finalizado, despachar_factura_macro) lanzaban
    UnicodeEncodeError en consola cp1252 — el error interrumpía la función
    ANTES de llegar a la persistencia (guardar_nota_embalada/
    guardar_macro_ruta): el ítem quedaba 'embalada' en memoria pero el
    registro NUNCA se guardaba en BD. Mismo patrón de blindaje que ya usan
    vigilar_datos.py / legacy_mysql_sync.py / profit_renglones_sync.py.
    """
    try:
        print(texto, flush=True)
    except UnicodeEncodeError:
        import sys
        stdout = sys.stdout
        if stdout is not None and hasattr(stdout, "reconfigure"):
            try:
                stdout.reconfigure(errors="replace")
                print(texto, flush=True)
                return
            except Exception:
                pass
        print(texto.encode("ascii", errors="replace").decode("ascii"), flush=True)

# Evento emitido por la capa de Embalaje al cerrar un bulto/nota como FINALIZADO.
EVENTO_EMBALAJE_FINALIZADO = "embalaje.finalizado"

# Estados del flujo embalaje → despacho (rutagrama_notas).
ESTADO_EMBALADO_LISTO = "EMBALADO_LISTO_PARA_DESPACHO"
ESTADO_DESPACHADO = "DESPACHADO"


# ---------------------------------------------------------------------------
# Cotejamiento estricto Pedido ↔ Factura (Profit ERP)
#
# Regla SQL espejo (scripts/auditar_notas_facturas.js):
#   WHERE (num_nota = @nota OR num_fac = @factura) AND status = 'T'
#         AND status NOT LIKE '%P%'
# - 'T' (Totalizado/Facturado) es la ÚNICA llave válida para cotejar.
# - Cualquier estado/tipo que contenga 'P' (Presupuesto/Pendiente/Pedido no
#   procesado) se OMITE: esos documentos no pueden despacharse ni cotejarse.
# ---------------------------------------------------------------------------

def es_estado_cotejo_valido(estado: Optional[str]) -> bool:
    """True si el documento puede cotejarse (estado 'T' y sin 'P').

    Solo aplica el filtro estricto a códigos cortos de Profit (1-3 caracteres,
    ej: 'T', 'P', 'S/P'); las etiquetas largas del visor ('Procesada',
    'Despachada') se consideran válidas para no romper el flujo actual.
    """
    s = str(estado or "").strip().upper()
    if not s:
        return True
    if len(s) <= 3:
        return not ("P" in s and "T" not in s)
    return s != "S/P"


def estado_cotejo_item(item: ItemRuta) -> str:
    """Estado a evaluar del ítem: raw del visor, luego estado ARA local."""
    return str(getattr(item, "estado_legacy", "") or item.estado_ara or "")


class RouteService:
    """Capa de aplicación del módulo REALIZAR RUTA (arquitectura hexagonal simétrica).
    Flujo rediseñado (lote unificado):
      1. listar_rutas_disponibles → el operador elige la macro-ruta (SIN bloqueo por
         ruta pre-asignada en Gestión de Usuarios).
      2. iniciar_ruta(usuario_id, ruta_macro) → el usuario activo queda registrado
         como 'responsable' dinámico de la sesión en memoria (patrón
         _posiciones_choferes de ara_server.py).
      3. Escaneo continuo unificado: todas las sub-rutas juntas, una sola lectura
         "para adelante" (caja → 1-0, factura → 1-1).
      4. clasificar_lote → bot: asigna automáticamente cada nota escaneada a su
         sub-ruta (consulta sub_rutas_de_macro + derivación).
      5. verificar_factura → cotejo 1-1 contra la sub-ruta asignada por el bot.
      6. finalizar_y_dividir_ruta → BLOQUEO si queda algún ítem fuera de 1-1.
    """

    def __init__(
        self,
        repositorio: RouteRepositoryPort,
        adapter_legacy: RouteLegacyPort,
        facturador=None,
        adapter_legacy_bqto: Optional[RouteLegacyPort] = None,
        mysql_sync=None,
    ):
        self._repositorio = repositorio
        self._legacy = adapter_legacy
        # Conector MySQL nativo (LegacyMySQLConnector, opcional): al finalizar
        # una ruta, registra los puntos de responsable/ayudante DIRECTO en
        # `puntajes` (tipo='CARGAR RUTA') — misma tabla real que ya usa el
        # visor legacy viejo para esto, verificada en vivo, NUNCA inventada.
        # Best-effort: si no se inyecta o la BD no responde, el cierre de
        # ruta sigue funcionando igual, solo sin ese registro de puntos.
        self._mysql_sync = mysql_sync
        # Factura REAL desde Profit (CRISTM25). Inyección opcional para pruebas;
        # por defecto se crea perezosamente y degrada a {} si Profit no responde.
        if facturador is None:
            from ..infrastructure.profit_facturador import ProfitFacturador

            facturador = ProfitFacturador()
        self._facturador = facturador
        # Adaptador BQTO (v4.53): el mismo visor legacy, pero consultado vía
        # el puente HTTP que corre en una máquina de la red de BQTO (ver
        # bin/puente_visor_bqto.ps1) — el Apache filtra los datos según la IP
        # de origen de quien consulta (verificado en vivo), así que la ÚNICA
        # forma de traer los pedidos reales de BQTO es que la conexión salga
        # físicamente desde esa red. Inyección opcional para pruebas; por
        # defecto se crea perezosamente contra ARA_PUENTE_BQTO_URL.
        if adapter_legacy_bqto is None:
            import os

            from ..infrastructure.legacy_route_adapter import LegacyRouteAdapter

            puente_url = os.environ.get("ARA_PUENTE_BQTO_URL", "http://100.116.126.99:5099")
            adapter_legacy_bqto = LegacyRouteAdapter(
                puente_url=puente_url,
                responsable_visor="9/MIGUEL CAMPOS",
            )
        self._legacy_bqto = adapter_legacy_bqto
        self._sesiones: Dict[str, dict] = {}
        # Suscripción al bus de eventos: el cierre de embalaje dispara la
        # vinculación automática de la nota empacada al rutagrama activo.
        get_event_bus().suscribir(
            EVENTO_EMBALAJE_FINALIZADO, self.vincular_nota_a_rutagrama_activa
        )
        # Arquitectura de Macro-Rutas Multi-Sede: el mismo evento alimenta la
        # Macro-Ruta ACTIVA de la sede del usuario ('SC' | 'BQTO').
        get_event_bus().suscribir(
            EVENTO_EMBALAJE_FINALIZADO, self.on_embalaje_finalizado
        )

    # ── 0. Catálogo dinámico de rutas ─────────────────────────────────────────
    def get_catalogo_rutas(self) -> dict:
        """Catálogo dinámico Macro-Rutas → sub-rutas parseadas de /visor/index.php.

        Si la consulta HTTP falla o no está disponible, usa el catálogo estático
        de respaldo (mismas Macro-Rutas basadas en las sub-rutas conocidas).
        """
        # v4.35: el catálogo dinámico es VOLÁTIL en tiempo real — se comprobó
        # en vivo que el <select> de index.php puede pasar de 47 opciones a
        # solo 2 (las rutas especiales) en cuestión de minutos, probablemente
        # porque el visor legacy solo lista rutas con pedidos pendientes en
        # ese instante. Reemplazar el catálogo completo por lo dinámico
        # (todo-o-nada, como antes) hacía desaparecer las zonas geográficas
        # conocidas cada vez que el servidor pasaba por un momento de poca
        # actividad. Ahora se COMBINAN: el estático siempre aporta las zonas
        # conocidas (aunque estén en 0 pendientes ahora mismo — el operador
        # las ve igual), el dinámico aporta/actualiza sub-rutas reales y
        # cualquier ruta especial nueva que no esté en el estático.
        catalogo_estatico = self._catalogo_fallback()
        catalogo_dinamico = self._catalogo_dinamico()
        catalogo = {k: list(v) for k, v in catalogo_estatico.items()}
        for macro, subs in catalogo_dinamico.items():
            existentes = catalogo.setdefault(macro, [])
            for sub in subs:
                if sub not in existentes:
                    existentes.append(sub)
        if not catalogo:
            return {
                "status": "error",
                "mensaje": "No se pudo obtener el catálogo de rutas.",
                "codigo_error": "SIN_CATALOGO",
            }
        return {
            "status": "success",
            "catalogo": catalogo,
            "total_macro_rutas": len(catalogo),
        }

    def _catalogo_dinamico(self) -> dict:
        """Catálogo vivo desde index.php agrupado simétricamente por Macro-Ruta."""
        try:
            pares = self._legacy.fetch_catalogo_rutas_legacy()
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] Catálogo dinámico falló: {e}")
            return {}
        nombres = [nombre for _, nombre in pares or []]
        if not nombres:
            return {}
        from ..domain.catalogo_rutas import agrupar_en_macro_rutas
        return agrupar_en_macro_rutas(nombres)

    def _catalogo_fallback(self) -> dict:
        """Respaldo estático provisto por el adaptador (sin HTTP)."""
        try:
            return self._legacy.catalogo_estatico()
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] Catálogo estático falló: {e}")
            return {}

    def listar_rutas_disponibles(self) -> dict:
        """Macro-rutas activas que el operador puede seleccionar (selector dinámico)."""
        resp = self.get_catalogo_rutas()
        if resp["status"] != "success":
            return resp
        rutas = list(resp["catalogo"].keys())
        if not rutas:
            return {
                "status": "error",
                "mensaje": "No hay rutas disponibles para despacho.",
                "codigo_error": "SIN_RUTAS",
            }
        return {"status": "success", "rutas_disponibles": rutas, "total": len(rutas)}

    # ── 0b. Umbral de sede por número de nota (fuente autoritativa: Profit) ──
    # Calibrado en vivo (v4.27) contra la tabla `almacen` de Profit — control
    # OFICIAL de numeración por serie, no una suposición:
    #   co_alma='01' SAN CRISTOBAL: num_fac_ini=0        num_fac_fin=71999999
    #   co_alma='02' BARQUISIMETO:  num_fac_ini=72000000 num_fac_fin=99999999
    # El umbral real es 72.000.000 (no 5.000.000 — ese era de otra tabla,
    # error de v4.25 al reusar el umbral de AlmacenDbTrait::
    # resolver_almacen_picking para un propósito distinto). El prefijo de
    # serie (A para S/C, B para SOLO_FACTURA manual) es convención del código
    # de barras impreso, NO existe en la columna fact_num de Profit.
    UMBRAL_SEDE_NOTA = 72_000_000

    @staticmethod
    def _numero_nota(codigo: str) -> Optional[int]:
        """Parte numérica pura de una nota/factura (tolera prefijos 'A'/'B')."""
        digitos = re.sub(r"\D", "", str(codigo or ""))
        return int(digitos) if digitos else None

    @classmethod
    def _item_pertenece_a_sede(cls, item: ItemRuta, sede: str) -> bool:
        """True si el ítem (por nota o factura) cae del lado del umbral de la sede."""
        numero = cls._numero_nota(item.nota_num) or cls._numero_nota(item.factura_num)
        if numero is None:
            return True  # sin número parseable: no se descarta, se deja para revisión manual
        if sede == "BQTO":
            return numero >= cls.UMBRAL_SEDE_NOTA
        if sede == "SC":
            return numero < cls.UMBRAL_SEDE_NOTA
        return True

    # ── Persistencia de sesión (v4.53) ────────────────────────────────────────
    # El progreso de escaneo vivía SOLO en self._sesiones (RAM del proceso) —
    # un reinicio del servidor lo borraba todo sin aviso. Ahora cada mutación
    # (escanear caja/factura, deshacer, clasificar lote) también persiste un
    # snapshot completo en SQLite (sesiones_ruta_activa); iniciar_ruta lo
    # recupera si el proceso se reinició (mismo mecanismo que ya usaba para
    # sobrevivir un F5 de página con el proceso vivo, ahora también sobrevive
    # un reinicio real del servidor).
    @staticmethod
    def _serializar_sesion(sesion: dict) -> str:
        payload = {
            "user_id": sesion["user_id"],
            "ruta_macro": sesion["ruta_macro"],
            "sede": sesion.get("sede", ""),
            "responsable": sesion.get("responsable", sesion["user_id"]),
            "rutagrama": sesion.get("rutagrama", ""),
            "sub_rutagramas": sesion.get("sub_rutagramas") or {},
            "items": [it.model_dump() for it in sesion["items"]],
            "scanned_cajas": list(sesion["scanned_cajas"]),
            "scanned_facturas": list(sesion["scanned_facturas"]),
            "total_cajas": sesion.get("total_cajas", 0),
            "total_facturas": sesion.get("total_facturas", 0),
            "total_notas_credito": sesion.get("total_notas_credito", 0),
            "total_solo_facturas": sesion.get("total_solo_facturas", 0),
            "clasificacion_lote": sesion.get("clasificacion_lote"),
            "lote_clasificado": sesion.get("lote_clasificado", False),
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _deserializar_sesion(datos: dict) -> dict:
        return {
            "user_id": datos["user_id"],
            "ruta_macro": datos["ruta_macro"],
            "sede": datos.get("sede", ""),
            "responsable": datos.get("responsable", datos["user_id"]),
            "rutagrama": datos.get("rutagrama", ""),
            "sub_rutagramas": datos.get("sub_rutagramas") or {},
            "items": [ItemRuta(**it) for it in datos.get("items", [])],
            "scanned_cajas": set(datos.get("scanned_cajas", [])),
            "scanned_facturas": set(datos.get("scanned_facturas", [])),
            "total_cajas": datos.get("total_cajas", 0),
            "total_facturas": datos.get("total_facturas", 0),
            "total_notas_credito": datos.get("total_notas_credito", 0),
            "total_solo_facturas": datos.get("total_solo_facturas", 0),
            "clasificacion_lote": datos.get("clasificacion_lote"),
            "lote_clasificado": datos.get("lote_clasificado", False),
        }

    def _persistir_sesion(self, sesion: dict, sede: str = "") -> None:
        """Best-effort: nunca rompe el flujo de escaneo si SQLite falla."""
        try:
            self._repositorio.guardar_sesion_ruta(
                sesion["user_id"], sesion["ruta_macro"], sede or sesion.get("sede", ""),
                self._serializar_sesion(sesion),
            )
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] No se pudo persistir la sesión de ruta: {e}")

    def _cargar_sesion_persistida(self, user_id: str) -> Optional[dict]:
        try:
            fila = self._repositorio.cargar_sesion_ruta(user_id)
            if not fila:
                return None
            return self._deserializar_sesion(json.loads(fila["datos_json"]))
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] No se pudo recuperar la sesión persistida: {e}")
            return None

    # ── 1. Inicio de sesión de ruta ───────────────────────────────────────────
    def iniciar_ruta(self, user_id: str, ruta_macro: str, sede: Optional[str] = None) -> dict:
        """Inicia la sesión de despacho SIN bloqueo por asignación rígida.

        Cualquier usuario activo selecciona la macro-ruta a despachar y queda
        registrado como 'responsable' dinámico de la sesión.

        `sede` ('SC' | 'BQTO', opcional): si se indica, filtra los ítems de la
        ruta por el umbral de número de nota de esa sede ANTES de cargar la
        sesión — el validador de sede (router) lo exige en el flujo principal;
        aquí queda opcional para no romper `cargar_ruta_macro_usuario` (ruta
        pre-asignada, sin selector de sede).
        """
        user_id = str(user_id or "").strip()
        ruta_macro = str(ruta_macro or "").strip()
        sede_norm = normalizar_sede(sede)
        if not user_id:
            return {"status": "error", "mensaje": "Falta el ID del usuario."}
        if not ruta_macro:
            return {
                "status": "error",
                "mensaje": "Falta la ruta a despachar: selecciónela en el selector de rutas.",
                "codigo_error": "RUTA_NO_SELECCIONADA",
            }

        # Fuente de ítems por sede (v4.53): MISMO visor legacy para ambas
        # sedes — el Apache filtra los datos según la IP de origen de quien
        # consulta (verificado en vivo: la misma ruta física trae solo notas
        # S/C consultada desde S/C, pero notas S/C + BQTO mezcladas
        # consultada desde la red de BQTO). BQTO usa `self._legacy_bqto`
        # (vía puente HTTP en una máquina de esa red); el filtro de sede
        # real más abajo (co_sucu vía Profit) se encarga de quedarse solo
        # con las notas BQTO del conjunto mezclado.
        try:
            legacy = self._legacy_bqto if sede_norm == "BQTO" else self._legacy
            items = legacy.fetch_ruta_macro(ruta_macro)
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "mensaje": f"Error consultando la ruta {ruta_macro} en el sistema Legacy: {e}",
                "codigo_error": "ERROR_LEGACY",
            }

        if not items:
            # Sin mock local: si la fuente no devuelve pedidos reales, la ruta
            # queda sin ítems (0 cajas reales) y se registra en logs.
            print(f"[RouteService] La ruta '{ruta_macro}' no devolvió pedidos "
                  f"reales ({'Profit BQTO' if sede_norm == 'BQTO' else 'visor'}, 0 ítems).")
            return {
                "status": "error",
                "mensaje": f"La ruta {ruta_macro} no devolvió cajas/facturas.",
                "codigo_error": "RUTA_SIN_ITEMS",
            }

        # Enriquecer cada item con el estado ARA local (semáforo) y normalizar
        # la factura REAL desde Profit (JOIN not_ent <-> reng_fac <-> factura):
        # num_fact = factura totalizada; None si la nota no tiene factura
        # (NUNCA se iguala factura = nota).
        notas = [it.nota_num for it in items if it.nota_num and it.tipo_documento == "PEDIDO"]
        mapa_facturas: Dict[str, List[str]] = {}
        if notas:
            try:
                mapa_facturas = self._facturador.num_fact_por_notas(notas)
            except Exception as e:
                traceback.print_exc()
                print(f"[RouteService] Profit no disponible para num_fact: {e}")

        # Sede real por documento (v4.33): co_sucu en Profit (not_ent/factura/
        # dev_cli), '01'=SC '02'=BQTO — dato directo por nota, NO una
        # inferencia por rango de número. Reemplaza el umbral 72.000.000
        # (v4.25-v4.29, retirado como filtro en v4.31 por ser indirecto) como
        # mecanismo de filtrado real.
        codigos_para_sede = list(dict.fromkeys(
            [it.nota_num for it in items if it.nota_num]
            + [it.factura_num for it in items if it.factura_num]
        ))
        mapa_sedes: Dict[str, str] = {}
        if codigos_para_sede:
            try:
                mapa_sedes = self._facturador.sedes_por_notas(codigos_para_sede)
            except Exception as e:
                traceback.print_exc()
                print(f"[RouteService] Profit no disponible para sedes_por_notas: {e}")

        # Peso real por nota (v4.38): reng_nde × art.peso — ver
        # ProfitFacturador.pesos_por_notas. Notas sin dato real quedan en 0.0
        # (el rutagrama impreso lo muestra como "—", nunca se inventa un peso).
        mapa_pesos: Dict[str, float] = {}
        notas_pedido = [it.nota_num for it in items if it.nota_num]
        if notas_pedido:
            try:
                mapa_pesos = self._facturador.pesos_por_notas(notas_pedido)
            except Exception as e:
                traceback.print_exc()
                print(f"[RouteService] Profit no disponible para pesos_por_notas: {e}")

        for it in items:
            it.sede_origen = mapa_sedes.get(it.nota_num) or mapa_sedes.get(it.factura_num) or ""
            it.peso = mapa_pesos.get(it.nota_num, 0.0)
            it.estado_ara = self._repositorio.get_estado_ara(it.factura_num or it.nota_num)
            # Bultos y estado heredados del cierre de embalaje (rutagrama_notas):
            # la nota ya empacada llega al rutagrama con su total REAL de bultos.
            try:
                reg = self._repositorio.buscar_nota_rutagrama(it.nota_num or it.factura_num)
            except Exception as e:
                traceback.print_exc()
                reg = None
            if reg:
                it.paquetes = int(reg.get("total_bultos") or it.paquetes or 0)
                if reg.get("estado") == ESTADO_EMBALADO_LISTO:
                    it.estado_ara = "embalada"
            if not it.factura_num:
                it.factura_num = it.nota_num  # factura == nota cuando el legacy no la separa
            validas = mapa_facturas.get(it.nota_num) or []
            if validas:
                it.num_fact = validas[0]
            elif it.tipo_documento == "SOLO_FACTURA":
                it.num_fact = it.factura_num or None
            else:
                it.num_fact = None
            self._clasificar_item(it)

        if sede_norm:
            # BUG REAL de negocio detectado en vivo (18/08, a pedido): este
            # filtro (v4.33) descartaba items cuya sede real (co_sucu de
            # Profit) no coincidía con la sede operativa del chofer — ej. una
            # nota de San Cristóbal (co_sucu='01') mezclada en una ruta física
            # de Aragua (zona BQTO) quedaba fuera del rutagrama. Confirmado
            # contra el visor legacy real: esas notas SÍ están físicamente en
            # esa ruta (envíos entre sede) y el camión de BQTO SÍ las entrega
            # — descartarlas dejaba cajas reales sin cargar. Ya NO se
            # descartan items por sede: `sede_origen` se conserva en cada item
            # (queda tageado para referencia/reportes) pero nunca excluye del
            # rutagrama. total_otra_sede es solo informativo.
            total_antes = len(items)
            total_otra_sede = sum(1 for it in items if it.sede_origen and it.sede_origen != sede_norm)
            if total_otra_sede > 0:
                print(f"[RouteService] Ruta '{ruta_macro}' (sede {sede_norm}): "
                      f"{total_otra_sede}/{total_antes} ítems son de la otra sede "
                      f"(co_sucu real) pero se CONSERVAN — envíos entre sede.")

        # Si ya hay sesión activa para el usuario (F5 / recarga), conservar el
        # avance. Si el proceso se reinició (sin sesión en RAM), se intenta
        # recuperar el último snapshot persistido en SQLite (v4.53) — mismo
        # mecanismo, fuente distinta.
        sesion_previa = self._sesiones.get(user_id)
        if not sesion_previa:
            sesion_previa = self._cargar_sesion_persistida(user_id)
        if sesion_previa and sesion_previa.get("ruta_macro") == ruta_macro:
            previos = {i.nota_num: i for i in sesion_previa["items"]}
            for it in items:
                prev = previos.get(it.nota_num)
                if prev:
                    it.scan_lote = prev.scan_lote
                    it.verificado = prev.verificado
                    it.scanned_caja = prev.scanned_caja
                    it.scanned_factura = prev.scanned_factura
            scanned_cajas = sesion_previa["scanned_cajas"]
            scanned_facturas = sesion_previa["scanned_facturas"]
            clasificacion_lote = sesion_previa.get("clasificacion_lote")
            lote_clasificado = sesion_previa.get("lote_clasificado", False)
            rutagrama_previo = sesion_previa.get("rutagrama") or ""
            sub_rutagramas_previos = sesion_previa.get("sub_rutagramas") or {}
        else:
            scanned_cajas = set()
            scanned_facturas = set()
            clasificacion_lote = None
            lote_clasificado = False
            rutagrama_previo = ""
            sub_rutagramas_previos = {}

        # Rutagrama Macro de la consolidación + Sub-Rutagramas individuales por
        # sub-ruta (correlativos: RGT-87813-1, RGT-87813-2, ...). Si se está
        # recuperando una sesión previa (F5 o reinicio del servidor), se
        # reutiliza el mismo rutagrama en vez de generar uno nuevo — para que
        # el operador vea el mismo código de rutagrama activo, no uno distinto
        # cada vez que la sesión se recarga.
        rutagrama = rutagrama_previo or f"RGT-{int(time.time()) % 100000:05d}"
        sub_rutagramas = sub_rutagramas_previos or {
            sub: f"{rutagrama}-{n}"
            for n, sub in enumerate(
                sorted({it.sub_ruta or "SIN SUB-RUTA" for it in items}), start=1)
        }

        total_facturas = len({i.factura_num or i.nota_num for i in items})
        total_cajas = sum(1 for i in items if not i.is_invoice_only)
        total_notas_credito = sum(1 for i in items if i.has_credit_notes)
        total_solo_facturas = sum(1 for i in items if i.is_invoice_only)
        self._sesiones[user_id] = {
            "user_id": user_id,
            "ruta_macro": ruta_macro,
            "sede": sede_norm or "",
            "responsable": user_id,  # responsable dinámico de la sesión
            "rutagrama": rutagrama,
            "sub_rutagramas": sub_rutagramas,
            "items": items,
            "scanned_cajas": scanned_cajas,
            "scanned_facturas": scanned_facturas,
            "total_cajas": total_cajas,
            "total_facturas": total_facturas,
            "total_notas_credito": total_notas_credito,
            "total_solo_facturas": total_solo_facturas,
            "clasificacion_lote": clasificacion_lote,
            "lote_clasificado": lote_clasificado,
        }

        for it in items:
            it.sub_rutagrama_id = sub_rutagramas[it.sub_ruta or "SIN SUB-RUTA"]

        self._persistir_sesion(self._sesiones[user_id], sede_norm or "")

        return {
            "status": "success",
            "ruta_macro": ruta_macro,
            "sede": sede_norm,
            "origen": "legacy",
            "responsable": user_id,
            "rutagrama": rutagrama,
            "sub_rutagramas": sub_rutagramas,
            "total_cajas": total_cajas,
            "total_facturas": total_facturas,
            "total_notas_credito": total_notas_credito,
            "total_solo_facturas": total_solo_facturas,
            "has_credit_notes": total_notas_credito > 0,
            "has_invoice_only": total_solo_facturas > 0,
            "total_sub_rutas": len(self._resumen_sub_rutas(items, sub_rutagramas)),
            "sub_rutas": self._resumen_sub_rutas(items, sub_rutagramas),
            "items": [self._item_dict(it) for it in items],
        }

    # ── 1a. Vinculación automática embalaje → rutagrama (Event Dispatcher) ────
    @staticmethod
    def _misma_nota(a: str, b: str) -> bool:
        """Comparación tolerante de números de nota/factura.

        Primero intenta igualdad exacta (incluida la normalización de letra de
        serie 'A2154234' -> '72154234', ver _normalizar_codigo_serie — sin
        esto, ese código NUNCA calzaba: el digit-only de 'A2154234' da
        '2154234', mientras que la nota real es '72154234', dígitos
        completamente distintos, no solo un prefijo). Si ambos tienen dígitos,
        compara también solo la parte numérica (tolera 'A0467959' ↔ '467959'
        para los prefijos que SÍ son un simple recorte, no una sustitución).
        """
        a, b = str(a or "").strip(), str(b or "").strip()
        if not a or not b:
            return False
        if a == b:
            return True
        if _normalizar_codigo_serie(a) == _normalizar_codigo_serie(b):
            return True
        dig_a = re.sub(r"\D", "", a)
        dig_b = re.sub(r"\D", "", b)
        return bool(dig_a and dig_b) and dig_a.lstrip("0") == dig_b.lstrip("0")

    def _legacy_para_sesion(self, sesion: dict) -> RouteLegacyPort:
        """Adaptador legacy correcto para la sede de la sesión (BQTO usa el
        puente `self._legacy_bqto`, S/C usa `self._legacy` directo).

        BUG real corregido 2026-08-20: `confirmar_registro()` se llamaba
        SIEMPRE contra `self._legacy` en escanear/despachar/finalizar,
        ignorando la sede — pero `iniciar_ruta()` SÍ arma la sesión con
        `self._legacy_bqto` para rutas BQTO (el Apache del visor filtra el
        catálogo de sub-rutas según la IP de origen: solo el puente que corre
        físicamente en la red de BQTO ve el <select> completo). Como
        `self._legacy` (directo, IP de esta máquina) nunca tiene poblado el
        mapeo sub_ruta->código raw de las sub-rutas BQTO, `confirmar_registro`
        terminaba autenticando el visor con el NOMBRE de la sub-ruta en vez
        de su código real — y el visor rechazaba la nota como "no pertenece
        a la ruta" para esas, en silencio (ver fix del bucle de finalizar).
        Caso real: de 3 sub-rutas de TRUJILLO (sede BQTO), solo 1 quedó
        registrada en listado.php porque las otras 2 usaban el adaptador
        equivocado.
        """
        sede = str((sesion or {}).get("sede") or "").strip().upper()
        return self._legacy_bqto if sede == "BQTO" else self._legacy

    def vincular_nota_a_rutagrama_activa(self, evento: str, payload: dict) -> dict:
        """Handler del evento 'embalaje.finalizado'.

        Vincula la nota empacada (con su total_bultos real) al Rutagrama activo
        que la contenga: busca en todas las sesiones de despacho en memoria el
        ítem de la nota (por nota_num / factura / num_fact), actualiza su
        conteo de bultos y lo deja en estado EMBALADO_LISTO_PARA_DESPACHO, y
        persiste el registro en rutagrama_notas (idempotente por num_nota).

        Si no hay sesión activa (la ruta aún no se inició), el registro queda
        persistido en BD y se enriquece al iniciar la ruta más adelante.
        """
        data = payload or {}
        num_nota = str(data.get("num_nota") or data.get("numero_nota") or "").strip()
        if not num_nota:
            return {
                "status": "error",
                "mensaje": "Evento de embalaje sin num_nota.",
                "vinculada": False,
            }
        total_bultos = int(data.get("total_bultos") or data.get("cant_cajas") or 0)

        # 1. Ruta ACTIVA correspondiente: sesión en memoria que contenga la nota.
        ruta_encontrada = None
        for sesion in self._sesiones.values():
            for it in sesion["items"]:
                if self._misma_nota(it.nota_num, num_nota) or self._misma_nota(
                    it.factura_num or it.num_fact or "", num_nota
                ):
                    ruta_encontrada = (sesion, it)
                    break
            if ruta_encontrada:
                break

        registro = {
            "num_nota": num_nota,
            "factura_num": ruta_encontrada[1].factura_num if ruta_encontrada else "",
            "co_cli": data.get("co_cli") or (
                ruta_encontrada[1].codigo_cliente if ruta_encontrada else ""
            ),
            "razon_social": data.get("razon_social") or (
                ruta_encontrada[1].razon_social if ruta_encontrada else ""
            ),
            "total_bultos": total_bultos,
            "operador_embalaje": data.get("operador_embalaje") or data.get("usuario") or "",
        }

        # 2. Sesión activa → actualiza el ítem del rutagrama en memoria.
        if ruta_encontrada:
            sesion, item = ruta_encontrada
            item.paquetes = total_bultos
            item.estado_ara = "embalada"
            registro["ruta_macro"] = sesion.get("ruta_macro") or ""
            registro["sub_ruta"] = item.sub_ruta or ""
            registro["rutagrama"] = sesion.get("rutagrama") or ""
            _print_seguro(
                f"[RouteService] Nota {num_nota} vinculada a la ruta activa "
                f"'{sesion.get('ruta_macro')}' ({item.sub_ruta}) con "
                f"{total_bultos} bulto(s) -> {ESTADO_EMBALADO_LISTO}"
            )

        # 3. Persistencia local idempotente (nunca rompe el flujo si falla).
        try:
            guardado = self._repositorio.guardar_nota_embalada(registro)
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] No se pudo persistir la nota embalada {num_nota}: {e}")
            guardado = None

        if guardado:
            return {
                "status": "success",
                "vinculada": guardado.get("estado") == ESTADO_EMBALADO_LISTO,
                "num_nota": num_nota,
                "total_bultos": guardado.get("total_bultos") or total_bultos,
                "ruta_macro": registro.get("ruta_macro") or guardado.get("ruta_macro") or "",
                "sub_ruta": registro.get("sub_ruta") or guardado.get("sub_ruta") or "",
                "rutagrama": registro.get("rutagrama") or guardado.get("rutagrama") or "",
                "estado": guardado.get("estado") or ESTADO_EMBALADO_LISTO,
                "mensaje": (
                    f"Nota {num_nota} ya {('lista para despacho' if guardado.get('estado') == ESTADO_EMBALADO_LISTO else 'DESPACHADA')} "
                    f"con {guardado.get('total_bultos') or total_bultos} bulto(s)."
                ),
            }
        return {
            "status": "error",
            "vinculada": False,
            "num_nota": num_nota,
            "mensaje": "No se pudo persistir la vinculación al rutagrama.",
        }

    def despachar_por_factura(self, user_id: str, barcode: str) -> dict:
        """Escaneo ÚNICO de la Factura / Nota de Crédito en despacho.

        Busca la nota en la ruta activa, valida su estado de embalaje
        (EMBALADO_LISTO_PARA_DESPACHO) y la marca —junto con TODOS sus bultos—
        como DESPACHADO en un solo paso (matriz 1-1), sin re-escaneo de cada
        bulto. Compatibilidad: si la nota no tiene registro de embalaje ARA,
        se comporta como la verificación tradicional 1-1.
        """
        sesion = self._sesiones.get(str(user_id or "").strip())
        if not sesion:
            return {
                "status": "error",
                "mensaje": "No hay ruta activa. Inicie la ruta desde el selector.",
                "codigo_error": "SIN_SESION",
            }

        barcode = str(barcode or "").strip()
        if not barcode:
            return {"status": "error", "mensaje": "Código de barras vacío."}

        # FIX v4.53 — BUG real detectado en vivo: el visor legacy no tiene un
        # código de barras de factura separado, así que `factura_num` se
        # copia de `nota_num` para los PEDIDO normales. Antes este matching
        # también aceptaba `i.nota_num == barcode`, así que escanear la CAJA
        # (el código de la nota) a este endpoint de DESPACHO POR FACTURA
        # igual encontraba coincidencia y despachaba directo a 1-1,
        # saltándose el paso 1-0 — pasaba en ambas sedes. Ahora: un PEDIDO
        # normal solo coincide aquí por `num_fact` (factura REAL resuelta de
        # Profit, un número distinto de la nota); SOLO_FACTURA (sin nota
        # previa) sigue coincidiendo por `factura_num`, que ahí SÍ es su
        # identificador real.
        coincidencias = [
            i for i in sesion["items"]
            if (i.tipo_documento == "SOLO_FACTURA" and self._misma_nota(i.factura_num, barcode))
            or self._misma_nota(i.num_fact or "", barcode)
        ]
        if not coincidencias:
            return {
                "status": "error",
                "mensaje": f"El código {barcode} no pertenece a la ruta {sesion['ruta_macro']}.",
                "codigo_error": "BARCODE_NO_PERTENECE",
            }

        # COTEJAMIENTO ESTRICTO (Profit): solo documentos Totalizados ('T').
        if not any(es_estado_cotejo_valido(estado_cotejo_item(i)) for i in coincidencias):
            return {
                "status": "error",
                "mensaje": (f"El documento {barcode} está en estado 'P' (Presupuesto/"
                            f"Pendiente). Solo se cotejan documentos Totalizados ('T')."),
                "codigo_error": "COTEJO_ESTADO_P",
            }

        item = coincidencias[0]
        nota_num = item.nota_num or item.factura_num or barcode

        # BUG real detectado en vivo (v4.53): un re-escaneo del mismo código
        # (doble lectura del lector, o el operador repite el código a mano)
        # pasaba silencioso como 'success' otra vez — el backend ya sabía
        # que `item.verificado` era True, pero no bloqueaba ni avisaba
        # distinto, así que "solo metes una [factura] y pasa todo en ok" sin
        # ninguna señal de que ya estaba escaneada. Ahora se bloquea con un
        # código de error explícito ANTES de tocar nada más.
        if item.verificado:
            return {
                "status": "error",
                "codigo_error": "YA_ESCANEADA",
                "mensaje": f"La nota {nota_num} (código {barcode}) YA fue escaneada antes en esta sesión.",
                "nota": nota_num,
                "factura": barcode,
                "matriz_estado": item.matriz_estado,
                "sub_ruta": item.sub_ruta,
            }

        # Registro de embalaje local (heredado directamente del cierre de
        # embalaje): valida el estado y aporta el conteo REAL de bultos.
        registro = None
        try:
            registro = self._repositorio.buscar_nota_rutagrama(nota_num)
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] rutagrama_notas no disponible: {e}")

        if registro and registro.get("estado") == ESTADO_EMBALADO_LISTO:
            try:
                self._repositorio.marcar_nota_despachada(
                    nota_num, despachado_por=str(user_id or "").strip()
                )
            except Exception as e:
                traceback.print_exc()
                print(f"[RouteService] No se pudo marcar {nota_num} como despachada: {e}")
            item.paquetes = int(registro.get("total_bultos") or item.paquetes or 0)
            despachado = True
            bultos = item.paquetes
        else:
            # Compatibilidad: nota sin embalaje ARA registrado → verificación
            # tradicional 1-1 (el conteo de bultos queda como lo trajo el visor).
            despachado = False
            bultos = item.paquetes or 0

        ya_verificado = item.verificado
        item.verificado = True
        item.scan_lote = True
        item.scanned_factura = True
        item.scanned_caja = True  # la factura arrastra TODOS sus bultos
        sesion["scanned_facturas"].add(barcode)
        sesion["scanned_cajas"].add(barcode)

        # Confirmación en el sistema Legacy (no bloqueante si falla)
        try:
            self._legacy_para_sesion(sesion).confirmar_registro(barcode, "factura")
        except Exception as e:
            print(f"[RouteService] Aviso: no se pudo confirmar en Legacy: {e}")
            traceback.print_exc()

        self._persistir_sesion(sesion)

        return {
            "status": "success",
            "factura": barcode,
            "nota": nota_num,
            "total_bultos": bultos,
            "estado": ESTADO_DESPACHADO if despachado else "1-1_VERIFICADA",
            "despachado": despachado,
            "ya_verificado": ya_verificado,
            "matriz_estado": item.matriz_estado,
            "sub_ruta": item.sub_ruta,
            "pendientes_verificar": len(self._pendientes(sesion)),
            "mensaje": (
                f"Nota {nota_num} DESPACHADA con {bultos} bulto(s) en un solo escaneo."
                if despachado
                else f"Factura {barcode} verificada → 1-1 ({bultos} bulto(s))."
            ),
        }

    # ── 1b. Compatibilidad: carga por ruta pre-asignada (desacoplada) ─────────
    def cargar_ruta_macro_usuario(self, user_id: str) -> dict:
        """Carga la ruta del usuario SIN lanzar el bloqueo estricto anterior.

        Si el usuario no tiene ruta pre-asignada en Gestión de Usuarios, NO se
        lanza el error de asignación: se responde con el selector de rutas
        disponibles para que el operador elija la ruta a despachar.
        """
        user_id = str(user_id or "").strip()
        if not user_id:
            return {"status": "error", "mensaje": "Falta el ID del usuario."}

        ruta_asignada = self._repositorio.get_ruta_asignada(user_id)
        if not ruta_asignada:
            return {
                "status": "error",
                "mensaje": "El usuario no tiene ruta pre-asignada: seleccione la ruta a despachar.",
                "codigo_error": "SELECCIONAR_RUTA",
                "rutas_disponibles": self._rutas_legacy_safe(),
            }

        return self.iniciar_ruta(user_id, ruta_asignada)

    def _rutas_legacy_safe(self) -> List[str]:
        try:
            return self._legacy.rutas_disponibles()
        except Exception as e:
            traceback.print_exc()
            return []

    @staticmethod
    def _clasificar_item(it: ItemRuta) -> None:
        """Clasifica el tipo de documento del ítem (idempotente).

        - SOLO_FACTURA: sin nota de venta previa (factura sola) → se coteja
          directo a 1-1 con su factura; omite el conteo de cajas físicas.
        - NOTA_CREDITO: marcadores NC-/NC nnn, nota de crédito o devolución.
        - PEDIDO: caso general.
        """
        if it.tipo_documento in ("NOTA_CREDITO", "SOLO_FACTURA"):
            it.is_invoice_only = 1 if it.tipo_documento == "SOLO_FACTURA" else it.is_invoice_only
            it.has_credit_notes = 1 if it.tipo_documento == "NOTA_CREDITO" else it.has_credit_notes
            return
        nota = str(it.nota_num or "").strip()
        factura = str(it.factura_num or "").strip()
        if not nota and factura:
            it.tipo_documento = "SOLO_FACTURA"
            it.is_invoice_only = 1
            it.has_credit_notes = 0
            return
        marcador = f"{nota} {factura} {it.razon_social or ''}".upper()
        if (
            "NOTA DE CREDITO" in marcador
            or "NOTA CREDITO" in marcador
            or re.search(r"\bNC[-\s]?\d", marcador)
            or "DEVOLUCION" in marcador
            or "DEVOLUCIÓN" in marcador
        ):
            it.tipo_documento = "NOTA_CREDITO"
            it.has_credit_notes = 1
            it.is_invoice_only = 0
            return
        it.tipo_documento = "PEDIDO"
        it.is_invoice_only = 0
        it.has_credit_notes = 0

    # ── 2. Escaneo continuo unificado (matriz 0-0 → 1-0 → 1-1) ────────────────
    def procesar_escaneo(self, user_id: str, barcode: str, tipo_escaneo: str) -> dict:
        """Escaneo "para adelante" sobre el lote completo de la ruta.

        - tipo 'caja': marca scan_lote → estado 1-0 (caja leída).
        - tipo 'factura': cotejo directo → verificado + scan_lote → estado 1-1.
        Las sub-rutas NO se separan en pantalla: todo va a un buffer general.
        """
        sesion = self._sesiones.get(str(user_id or "").strip())
        if not sesion:
            return {
                "status": "error",
                "mensaje": "No hay ruta activa. Inicie la ruta desde el selector.",
                "codigo_error": "SIN_SESION",
            }

        tipo = (tipo_escaneo or "caja").strip().lower()
        barcode = str(barcode or "").strip()
        if not barcode:
            return {"status": "error", "mensaje": "Código de barras vacío."}
        # Tolera códigos impresos con letra de serie (ej. 'A2154234' -> '72154234',
        # ver _normalizar_codigo_serie) — se prueba el código tal cual Y su
        # normalizado, sin descartar ningún ítem real por un simple prefijo de imprenta.
        barcode_norm = _normalizar_codigo_serie(barcode)
        codigos_validos = {barcode, barcode_norm}

        if tipo == "caja":
            coincidencias = [i for i in sesion["items"] if i.nota_num in codigos_validos]
            if not coincidencias:
                # SOLO_FACTURA: no hay nota previa; su factura se coteja directo a 1-1
                solos = [i for i in sesion["items"]
                         if i.is_invoice_only and i.factura_num in codigos_validos]
                if solos:
                    tipo = "factura"
                    coincidencias = solos
                    clave_set, campo = sesion["scanned_facturas"], "scanned_factura"
                else:
                    clave_set, campo = sesion["scanned_cajas"], "scanned_caja"
            else:
                clave_set, campo = sesion["scanned_cajas"], "scanned_caja"
        elif tipo == "factura":
            # FIX v4.53: ver despachar_por_factura — un PEDIDO normal solo
            # coincide por `num_fact` real; `factura_num` (copia de la nota
            # cuando no hay código de factura separado) solo cuenta para
            # SOLO_FACTURA, donde sí es el identificador real.
            coincidencias = [
                i for i in sesion["items"]
                if (i.num_fact and i.num_fact in codigos_validos)
                or (i.is_invoice_only and i.factura_num in codigos_validos)
            ]
            clave_set, campo = sesion["scanned_facturas"], "scanned_factura"
        else:
            return {"status": "error", "mensaje": "tipo_escaneo debe ser 'caja' o 'factura'."}

        if not coincidencias:
            return {
                "status": "error",
                "mensaje": f"El código {barcode} no pertenece a la ruta {sesion['ruta_macro']}.",
                "codigo_error": "BARCODE_NO_PERTENECE",
            }

        # COTEJAMIENTO ESTRICTO (Profit): se omite cualquier documento con
        # estado 'P' (Presupuesto/Pendiente/Pedido no procesado); solo se
        # cotejan documentos 'T' (Totalizado/Facturado).
        if not any(es_estado_cotejo_valido(estado_cotejo_item(i)) for i in coincidencias):
            return {
                "status": "error",
                "mensaje": (f"El documento {barcode} está en estado 'P' (Presupuesto/"
                            f"Pendiente). Solo se cotejan documentos Totalizados ('T')."),
                "codigo_error": "COTEJO_ESTADO_P",
            }

        ya_escaneado = barcode_norm in clave_set or barcode in clave_set
        if ya_escaneado:
            # Mismo bug de re-escaneo silencioso detectado en vivo (v4.53)
            # para despachar_por_factura: doble lectura del lector (o
            # código repetido a mano) pasaba como 'success' de nuevo sin
            # ninguna señal real de que ya estaba escaneado. Se bloquea acá
            # también por consistencia con el resto de los caminos de escaneo.
            return {
                "status": "error",
                "codigo_error": "YA_ESCANEADA",
                "mensaje": f"El código {barcode} YA fue escaneado antes en esta sesión ({tipo}).",
                "tipo": tipo,
                "barcode": barcode,
                "matriz_estado": coincidencias[0].matriz_estado,
            }
        for i in coincidencias:
            setattr(i, campo, True)
            i.scan_lote = True           # matriz: 1-0
            if tipo == "factura":
                i.verificado = True      # matriz: 1-1
        clave_set.add(barcode_norm)

        # Confirmación en el sistema Legacy (no bloqueante si falla)
        try:
            self._legacy_para_sesion(sesion).confirmar_registro(barcode, tipo)
        except Exception as e:
            print(f"[RouteService] Aviso: no se pudo confirmar en Legacy: {e}")
            traceback.print_exc()

        self._persistir_sesion(sesion)

        return {
            "status": "success",
            "tipo": tipo,
            "barcode": barcode,
            "ya_escaneado": ya_escaneado,
            "matriz_estado": coincidencias[0].matriz_estado,
            "cajas_escaneadas": len(sesion["scanned_cajas"]),
            "total_cajas": sesion["total_cajas"],
            "facturas_escaneadas": len(sesion["scanned_facturas"]),
            "total_facturas": sesion["total_facturas"],
            "pendientes_verificar": len(self._pendientes(sesion)),
        }

    # ── 2a-bis. Deshacer un escaneo por error (a pedido, v4.53) ────────────────
    def deshacer_escaneo(self, user_id: str, nota_num: str) -> dict:
        """Revierte un ítem escaneado por error de vuelta a '0-0' (pendiente).

        Identifica el ítem por `nota_num` (identificador estable mostrado en
        la tarjeta), no por el código exacto que se escaneó — así funciona
        sin importar si se despachó por factura, por caja o por macro-ruta.
        Best-effort: no falla si el ítem ya estaba pendiente."""
        sesion = self._sesiones.get(str(user_id or "").strip())
        if not sesion:
            return {
                "status": "error",
                "mensaje": "No hay ruta activa. Inicie la ruta desde el selector.",
                "codigo_error": "SIN_SESION",
            }

        nota_num = str(nota_num or "").strip()
        if not nota_num:
            return {"status": "error", "mensaje": "Falta el número de nota."}

        item = next((i for i in sesion["items"] if i.nota_num == nota_num), None)
        if item is None:
            return {
                "status": "error",
                "mensaje": f"La nota {nota_num} no pertenece a la ruta {sesion['ruta_macro']}.",
                "codigo_error": "BARCODE_NO_PERTENECE",
            }

        estaba_en = item.matriz_estado
        item.verificado = False
        item.scan_lote = False
        item.scanned_caja = False
        item.scanned_factura = False
        for clave_set in (sesion["scanned_cajas"], sesion["scanned_facturas"]):
            clave_set.discard(item.nota_num)
            clave_set.discard(item.factura_num)
            if item.num_fact:
                clave_set.discard(item.num_fact)

        _print_seguro(
            f"[RouteService] Deshecho escaneo de nota {nota_num} "
            f"(estaba {estaba_en} -> 0-0) por {user_id}"
        )
        self._persistir_sesion(sesion)
        return {
            "status": "success",
            "nota_num": nota_num,
            "estaba_en": estaba_en,
            "matriz_estado": item.matriz_estado,
            "pendientes_verificar": len(self._pendientes(sesion)),
            "mensaje": f"Nota {nota_num} vuelta a 0-0 (pendiente).",
        }

    # ── 2b. Cotejo de factura (1-0 → 1-1) contra la sub-ruta del bot ──────────
    def verificar_factura(self, user_id: str, factura_num: str, sub_ruta: Optional[str] = None) -> dict:
        """Consulta de cotejo: verifica la factura contra su sub-ruta asignada por el bot."""
        sesion = self._sesiones.get(str(user_id or "").strip())
        if not sesion:
            return {
                "status": "error",
                "mensaje": "No hay ruta activa. Inicie la ruta desde el selector.",
                "codigo_error": "SIN_SESION",
            }

        factura_num = str(factura_num or "").strip()
        if not factura_num:
            return {"status": "error", "mensaje": "Falta el número de factura."}

        # FIX v4.53: ver despachar_por_factura — un PEDIDO normal solo
        # coincide por `num_fact` real; `factura_num` (copia de la nota
        # cuando no hay código de factura separado) solo cuenta para
        # SOLO_FACTURA, donde sí es el identificador real.
        coincidencias = [
            i for i in sesion["items"]
            if (i.num_fact and i.num_fact == factura_num)
            or (i.is_invoice_only and i.factura_num == factura_num)
        ]
        if not coincidencias:
            return {
                "status": "error",
                "mensaje": f"La factura {factura_num} no pertenece a la ruta {sesion['ruta_macro']}.",
                "codigo_error": "BARCODE_NO_PERTENECE",
            }

        # COTEJAMIENTO ESTRICTO (Profit): omite 'P', exige 'T' (Totalizado).
        if not any(es_estado_cotejo_valido(estado_cotejo_item(i)) for i in coincidencias):
            return {
                "status": "error",
                "mensaje": (f"La factura {factura_num} está en estado 'P' (Presupuesto/"
                            f"Pendiente). Solo se cotejan facturas Totalizadas ('T')."),
                "codigo_error": "COTEJO_ESTADO_P",
            }

        item = coincidencias[0]
        if item.verificado:
            return {
                "status": "error",
                "codigo_error": "YA_ESCANEADA",
                "mensaje": f"La factura {factura_num} YA fue escaneada antes en esta sesión.",
                "matriz_estado": item.matriz_estado,
                "sub_ruta": item.sub_ruta,
            }
        sub_esperada = str(sub_ruta or "").strip()
        if sub_esperada and item.sub_ruta != sub_esperada:
            return {
                "status": "error",
                "mensaje": f"La factura {factura_num} fue asignada por el bot a la sub-ruta "
                           f"'{item.sub_ruta}', no a '{sub_esperada}'.",
                "codigo_error": "COINCIDENCIA_SUB_RUTA",
                "sub_ruta_asignada": item.sub_ruta,
            }

        ya_verificado = item.verificado
        item.verificado = True
        item.scan_lote = True
        item.scanned_factura = True
        sesion["scanned_facturas"].add(factura_num)

        # Confirmación en el sistema Legacy (no bloqueante si falla)
        try:
            self._legacy_para_sesion(sesion).confirmar_registro(factura_num, "factura")
        except Exception as e:
            print(f"[RouteService] Aviso: no se pudo confirmar en Legacy: {e}")
            traceback.print_exc()

        self._persistir_sesion(sesion)

        return {
            "status": "success",
            "factura_num": factura_num,
            "sub_ruta": item.sub_ruta,
            "ya_verificado": ya_verificado,
            "matriz_estado": item.matriz_estado,
            "pendientes_verificar": len(self._pendientes(sesion)),
        }

    # ── 3. Bot de clasificación del lote ──────────────────────────────────────
    def clasificar_lote(self, user_id: str) -> dict:
        """Bot: asigna automáticamente cada nota escaneada del lote a su sub-ruta.

        Consulta las sub-rutas asociadas a la macro-ruta (sub_rutas_de_macro) y
        agrupa cada pedido. Si alguna nota llegó sin sub-ruta (ruta sin mapa),
        la deriva por coincidencia de nombre; si no hay coincidencia queda en
        'SIN SUB-RUTA' para revisión manual.
        """
        sesion = self._sesiones.get(str(user_id or "").strip())
        if not sesion:
            return {
                "status": "error",
                "mensaje": "No hay ruta activa. Inicie la ruta desde el selector.",
                "codigo_error": "SIN_SESION",
            }

        macro = sesion["ruta_macro"]
        sub_rutas_macro = self._sub_rutas_legacy(macro)
        clasificacion: Dict[str, dict] = {}
        sin_sub_ruta: List[str] = []

        for it in sesion["items"]:
            if not it.scan_lote:
                continue  # el bot solo clasifica lo escaneado en el lote
            sub = it.sub_ruta
            if not sub:
                sub = self._derivar_sub_ruta(macro, it.nota_num or it.factura_num, sub_rutas_macro)
                if sub:
                    it.sub_ruta = sub
            if not sub:
                sin_sub_ruta.append(it.nota_num or it.factura_num)
                sub = "SIN SUB-RUTA"
            grupo = clasificacion.setdefault(sub, {"total": 0, "notas": []})
            grupo["total"] += 1
            grupo["notas"].append(it.nota_num or it.factura_num)

        sesion["clasificacion_lote"] = clasificacion
        sesion["lote_clasificado"] = True
        self._persistir_sesion(sesion)

        sub_rutas_sin_items = [
            sr for sr in sub_rutas_macro
            if sr not in clasificacion
        ]

        total_escaneadas = sum(g["total"] for g in clasificacion.values())
        return {
            "status": "success",
            "ruta_macro": macro,
            "lote_clasificado": True,
            "total_escaneadas": total_escaneadas,
            "total_lote": sesion["total_cajas"],
            "clasificacion": clasificacion,
            "sub_rutas_sin_items": sub_rutas_sin_items,
            "sin_sub_ruta": sin_sub_ruta,
            "pendientes_verificar": len(self._pendientes(sesion)),
        }

    def _sub_rutas_legacy(self, macro: str) -> List[str]:
        try:
            return self._legacy.sub_rutas_de_macro(macro)
        except Exception as e:
            traceback.print_exc()
            return []

    @staticmethod
    def _derivar_sub_ruta(macro: str, codigo: str, sub_rutas_macro: List[str]) -> str:
        """Derivación automática: nombre de sub-ruta presente en la nota (case-insensitive)."""
        codigo_upper = str(codigo or "").upper()
        for sr in sub_rutas_macro:
            if sr and sr.upper() in codigo_upper:
                return sr
        return ""

    # ── 4. Segmentación por Sub-Rutagramas ────────────────────────────────────
    def segmentar_sub_rutagramas(self, user_id: str) -> dict:
        """Segmenta la sesión de despacho activa por sub-ruta.

        Calcula, para cada Sub-Rutagrama individual, totales independientes de
        cajas (excluye SOLO_FACTURA), facturas, notas de crédito y estado de la
        matriz. La suma de ítems de los segmentos coincide exactamente con el
        total de la Macro-Ruta.
        """
        sesion = self._sesiones.get(str(user_id or "").strip())
        if not sesion:
            return {
                "status": "error",
                "mensaje": "No hay ruta activa. Inicie la ruta desde el selector.",
                "codigo_error": "SIN_SESION",
            }

        sub_rutagramas = sesion.get("sub_rutagramas") or {}
        grupos: Dict[str, List[ItemRuta]] = {}
        for it in sesion["items"]:
            grupos.setdefault(it.sub_ruta or "SIN SUB-RUTA", []).append(it)

        desglose = []
        for nombre, its in sorted(grupos.items()):
            desglose.append({
                "sub_ruta": nombre,
                "sub_rutagrama": sub_rutagramas.get(nombre)
                or f"{sesion['rutagrama']}-{desglose.__len__() + 1:02d}",
                "total_items": len(its),
                "total_cajas": sum(1 for i in its if not i.is_invoice_only),
                "total_facturas": len({i.factura_num or i.nota_num for i in its}),
                "total_notas_credito": sum(1 for i in its if i.has_credit_notes),
                "total_solo_facturas": sum(1 for i in its if i.is_invoice_only),
                "n00": sum(1 for i in its if i.matriz_estado == "0-0"),
                "n10": sum(1 for i in its if i.matriz_estado == "1-0"),
                "n11": sum(1 for i in its if i.matriz_estado == "1-1"),
                "items": [self._item_dict(i) for i in its],
            })

        return {
            "status": "success",
            "rutagrama_padre": sesion["rutagrama"],
            "ruta_macro": sesion["ruta_macro"],
            "total_items": len(sesion["items"]),
            "total_sub_rutagramas": len(desglose),
            "sub_rutagramas": desglose,
        }

    # ── 4b. Progreso agregado de una Macro-Ruta por NOMBRE ─────────────────────
    #
    # FUENTE (a pedido explícito del usuario, 2026-08-21): el progreso YA NO
    # se lee de `sesiones_ruta_activa` (SQLite local de ARA) — esa tabla es
    # solo un espejo del trabajo en curso de ESTE proceso y quedaba con
    # sesiones de días distintos sin cerrar sumándose como si fueran una sola
    # ("ARA no es la fuente, somos un espejo — consulta el visor legacy").
    # Ahora se lee EN VIVO de la base MySQL `visor` (192.168.4.148), que es
    # donde el sistema real registra qué camión está EN PROCESO de carga
    # (`cargado.estatus = 'A'`) y cuántas cajas lleva escaneadas (`detalle`,
    # paquetes vs. escaneados por id_ca). El código de ruta de `cargado` es
    # numérico (ej. '14') salvo las filas 'region:NOMBRE' que ARA siembra al
    # abrir una macro-ruta; el numérico se traduce a nombre real vía el
    # catálogo en vivo del visor (fetch_catalogo_rutas_legacy(), el mismo
    # mecanismo que ya usa el sistema para registrar cierres) y se clasifica
    # con catalogo_rutas.clasificar_macro_ruta().
    #
    # LIMITACIÓN conocida: `cargado` no tiene columna de sede (San Cristóbal /
    # Barquisimeto) — el visor es un sistema único compartido, no distingue
    # sede de origen. El desglose por_sede queda vacío con esta fuente.
    def _conectar_visor_mysql(self):
        import pymysql
        return pymysql.connect(
            host=os.environ.get("MYSQL_HOST", "192.168.4.148"),
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ.get("MYSQL_USER", "jonaiber"),
            password=os.environ.get("MYSQL_PASSWORD", "Crist2026."),
            database=os.environ.get("MYSQL_DATABASE_VISOR", "visor"),
            charset="utf8mb4",
            connect_timeout=8,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _macro_de_ruta_cruda(self, codigo_a_nombre: Dict[str, str], ruta_cruda: str) -> str:
        from ..domain.catalogo_rutas import clasificar_macro_ruta

        ruta_cruda = str(ruta_cruda or "").strip()
        if ruta_cruda.upper().startswith("REGION:"):
            return clasificar_macro_ruta(ruta_cruda[len("region:"):].strip())
        nombre = codigo_a_nombre.get(ruta_cruda)
        if nombre is None:
            # Código sin traducción en el catálogo del visor: se clasifica
            # por el código crudo (mejor esfuerzo, nunca se descarta el dato).
            return clasificar_macro_ruta(ruta_cruda)
        return clasificar_macro_ruta(nombre)

    def _progreso_desde_visor(self, filtro_macro: Optional[str] = None) -> dict:
        """Agrega, por Macro-Ruta, los camiones EN PROCESO (`cargado.estatus
        = 'A'`) del visor real, con sus cajas (`detalle.paquetes` vs.
        `detalle.escaneados`). Si `filtro_macro` viene, agrega SOLO esa
        macro-ruta (comparación case-insensitive contra el nombre clasificado)."""
        try:
            pares = self._legacy.fetch_catalogo_rutas_legacy()
        except Exception:
            pares = []
        codigo_a_nombre = {str(c).strip(): n for c, n in pares}

        try:
            conn = self._conectar_visor_mysql()
        except Exception as e:
            return {"status": "error", "mensaje": f"No se pudo conectar al visor (MySQL 192.168.4.148/visor): {e}"}
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, ruta, conductor, vehiculo, fecha FROM cargado WHERE estatus = 'A'")
                cargados = cur.fetchall()
                detalle_por_id: Dict[int, dict] = {}
                if cargados:
                    ids = [c["id"] for c in cargados]
                    marcas = ",".join(["%s"] * len(ids))
                    cur.execute(
                        "SELECT id_ca, SUM(paquetes) AS total_paquetes, SUM(escaneados) AS total_escaneados "
                        f"FROM detalle WHERE id_ca IN ({marcas}) GROUP BY id_ca",
                        ids,
                    )
                    detalle_por_id = {r["id_ca"]: r for r in cur.fetchall()}
        except Exception as e:
            return {"status": "error", "mensaje": f"Error consultando el visor: {e}"}
        finally:
            conn.close()

        filtro_norm = filtro_macro.strip().upper() if filtro_macro else None
        agregados: Dict[str, dict] = {}
        for c in cargados:
            macro = self._macro_de_ruta_cruda(codigo_a_nombre, c["ruta"])
            if filtro_norm and macro.strip().upper() != filtro_norm:
                continue
            det = detalle_por_id.get(c["id"]) or {}
            total_paquetes = int(det.get("total_paquetes") or 0)
            total_escaneados = int(det.get("total_escaneados") or 0)
            acc = agregados.setdefault(macro, {"total_items": 0, "cajas_escaneadas": 0, "cargas": []})
            acc["total_items"] += total_paquetes
            acc["cajas_escaneadas"] += total_escaneados
            acc["cargas"].append({
                "id_carga": c["id"],
                "conductor": (c.get("conductor") or "").strip(),
                "vehiculo": (c.get("vehiculo") or "").strip(),
                "fecha": str(c.get("fecha") or ""),
                "total_items": total_paquetes,
                "cajas_escaneadas": total_escaneados,
            })
        return {"status": "success", "rutas_macro": agregados}

    def progreso_ruta_macro(self, ruta_macro: str) -> dict:
        """Progreso REAL (visor legacy) de una macro-ruta por nombre: camiones
        EN PROCESO de carga ahora mismo (estatus='A') y cuántas cajas llevan
        escaneadas hasta el momento."""
        ruta_macro = str(ruta_macro or "").strip()
        if not ruta_macro:
            return {"status": "error", "mensaje": "Falta el nombre de la ruta.", "codigo_error": "SIN_RUTA"}

        resultado = self._progreso_desde_visor(filtro_macro=ruta_macro)
        if resultado.get("status") != "success":
            return resultado

        datos = None
        for nombre_macro, acc in resultado["rutas_macro"].items():
            if nombre_macro.strip().upper() == ruta_macro.strip().upper():
                datos = acc
                break
        if datos is None:
            return {
                "status": "error",
                "mensaje": f"No hay ningún camión en proceso de carga (visor, estatus 'A') sobre la ruta '{ruta_macro}'.",
                "codigo_error": "SIN_SESION",
            }

        total_items = datos["total_items"]
        cajas = datos["cajas_escaneadas"]
        return {
            "status": "success",
            "ruta_macro": ruta_macro,
            "total_operadores": len(datos["cargas"]),
            "total_items": total_items,
            "n00": max(0, total_items - cajas),
            "n10": cajas,
            "n11": 0,
            "cajas_escaneadas": cajas,
            "porcentaje_completado": round(cajas / total_items * 100, 1) if total_items else 0.0,
            "operadores": datos["cargas"],
            # El visor no distingue sede de origen en `cargado` — sin desglose.
            "por_sede": {},
        }

    # Turno de despacho por Macro-Ruta (decisión de negocio del usuario,
    # 2026-08-20): agrupa el resumen de "cómo van las rutas" en mañana/noche,
    # nombres EXACTOS de REGLAS_MACRO_RUTAS (catalogo_rutas.py) — "TACHIRA"
    # real es "FRONTERA / TACHIRA" en el catálogo, no una macro-ruta aparte.
    TURNO_MANANA = ("CARACAS", "ARAGUA", "CARABOBO", "ZULIA", "TRUJILLO", "PORTUGUESA")
    TURNO_NOCHE = ("MERIDA", "FRONTERA / TACHIRA", "APURE", "BARINAS", "BARQUISIMETO", "FALCON")

    @classmethod
    def _turno_de_ruta(cls, ruta_macro: str) -> str:
        nombre = str(ruta_macro or "").strip().upper()
        if nombre in cls.TURNO_MANANA:
            return "mañana"
        if nombre in cls.TURNO_NOCHE:
            return "noche"
        return "sin_turno"

    def progreso_todas_rutas(self) -> dict:
        """Resumen de TODAS las Macro-Rutas con al menos un camión EN PROCESO
        de carga ahora mismo (visor legacy real, no el espejo local), agrupado
        por turno (mañana/noche) según la decisión de negocio del usuario."""
        resultado = self._progreso_desde_visor()
        if resultado.get("status") != "success":
            return resultado

        rutas_macro = resultado["rutas_macro"]
        if not rutas_macro:
            return {
                "status": "success",
                "total_rutas": 0,
                "rutas": [],
                "turnos": {"mañana": [], "noche": [], "sin_turno": []},
                "mensaje": "No hay ningún camión en proceso de carga en el visor en este momento.",
            }

        rutas = []
        for nombre_macro, datos in rutas_macro.items():
            total_items = datos["total_items"]
            cajas = datos["cajas_escaneadas"]
            rutas.append({
                "ruta_macro": nombre_macro,
                "turno": self._turno_de_ruta(nombre_macro),
                "total_operadores": len(datos["cargas"]),
                "total_items": total_items,
                "sin_escanear_0_0": max(0, total_items - cajas),
                "caja_escaneada_1_0": cajas,
                "verificado_1_1": 0,
                "cajas_escaneadas": cajas,
                "porcentaje_completado": round(cajas / total_items * 100, 1) if total_items else 0.0,
                "por_sede": {},
            })
        rutas.sort(key=lambda r: r["cajas_escaneadas"], reverse=True)
        turnos = {"mañana": [], "noche": [], "sin_turno": []}
        for r in rutas:
            turnos[r["turno"]].append(r)
        return {
            "status": "success",
            "turnos": turnos,
            "total_rutas": len(rutas),
            "rutas": rutas,
        }

    # ── 4a. Finalización + división automática por sub-ruta ───────────────────
    def finalizar_y_dividir_ruta(self, user_id: str, datos_vehiculo: DatosVehiculo) -> dict:
        sesion = self._sesiones.get(str(user_id or "").strip())
        if not sesion:
            return {
                "status": "error",
                "mensaje": "No hay ruta activa. Inicie la ruta desde el selector.",
                "codigo_error": "SIN_SESION",
            }

        # CIERRE PARCIAL (a pedido del usuario, 2026-08-20): antes esto
        # bloqueaba el cierre completo si quedaba CUALQUIER ítem en 0-0/1-0
        # (ver IncompleteScanError, ya no se lanza). Ahora se cierran SOLO
        # los ítems que ya llegaron a 1-1 (verificados); los que sigan en
        # 0-0/1-0 quedan en la MISMA sesión activa (no se borra), para que
        # el chofer los complete en un cierre posterior — no se pierden ni
        # se fuerzan a cerrar sin haber sido escaneados de verdad.
        items_a_cerrar = [it for it in sesion["items"] if it.matriz_estado == "1-1"]
        items_pendientes = [it for it in sesion["items"] if it.matriz_estado != "1-1"]
        if not items_a_cerrar:
            return {
                "status": "error",
                "mensaje": "No hay ninguna nota verificada (1-1) todavía — escanee al menos una antes de cerrar.",
                "codigo_error": "NADA_VERIFICADO",
            }

        # División automática: agrupar por sub_ruta individual, asignando a cada
        # una su Sub-Rutagrama independiente (RGT-87813-1, RGT-87813-2, ...).
        sub_rutagramas = sesion.get("sub_rutagramas") or {}
        rutagrama_padre = sesion.get("rutagrama") or f"G-{int(time.time())}"
        grupos: Dict[str, List[ItemRuta]] = {}
        for it in items_a_cerrar:
            grupos.setdefault(it.sub_ruta or "SIN SUB-RUTA", []).append(it)

        guia_base = f"G-{int(time.time())}"
        subrutas: List[SubRutaFinalizada] = []
        for n, (nombre_subruta, items) in enumerate(sorted(grupos.items()), start=1):
            sub_rutagrama = sub_rutagramas.get(nombre_subruta) \
                or f"{rutagrama_padre}-{n:02d}"
            for it in items:
                it.sub_rutagrama_id = sub_rutagrama
            subrutas.append(
                SubRutaFinalizada(
                    guia=f"{guia_base}-{n:02d}",
                    rutagrama_padre=rutagrama_padre,
                    sub_rutagrama=sub_rutagrama,
                    sub_ruta=nombre_subruta,
                    items=items,
                    datos_vehiculo=datos_vehiculo,
                    responsable_id=str(user_id).strip(),
                )
            )

        guardadas = self._repositorio.save_subrutas(subrutas)
        uid_limpio = str(user_id).strip()
        if items_pendientes:
            # Cierre PARCIAL: la sesión sigue viva con las notas que quedaron
            # en 0-0/1-0 (normalmente las que salieron después del corte de
            # la tarde) — el chofer las cierra después con "Refrescar" +
            # un nuevo FINALIZAR CIERRE DE RUTA, sin perder nada.
            sesion["items"] = items_pendientes
            self._sesiones[uid_limpio] = sesion
            self._persistir_sesion(sesion, sesion.get("sede", ""))
        else:
            del self._sesiones[uid_limpio]  # todo quedó cerrado, la sesión ya no tiene nada pendiente
            try:
                self._repositorio.borrar_sesion_ruta(user_id)
            except Exception as e:
                traceback.print_exc()
                print(f"[RouteService] No se pudo borrar la sesión persistida de {user_id}: {e}")

        # Registro de cierre de despacho en Legacy (no bloqueante si falla).
        # Se itera por cada Sub-Rutagrama individual (nunca un bloque masivo).
        #
        # BUG real corregido 2026-08-20: este bucle llamaba a
        # confirmar_registro() por cada sub-ruta pero NUNCA revisaba el
        # resultado — esa función NUNCA lanza excepción en sus fallos de
        # negocio (NOTA_NO_PERTENECE, CLAVE_RECHAZADA, REGISTRO_RECHAZADO,
        # SIN_GUIA: todos vuelven como dict {"status":"error",...}, no como
        # raise), así que el try/except de aquí nunca los agarraba. Caso real
        # detectado en vivo: de 3 sub-rutas (TRUJILLO - PARAMOS, TRUJILLO ALTA
        # - PARTE BAJA, TRUJILLO BAJA 1) solo la ÚLTIMA quedó registrada en el
        # visor legacy (listado.php) — las otras 2 fallaron en silencio y ARA
        # reportó el cierre como éxito total igual. Ahora se registra el
        # resultado de CADA sub-ruta y se avisa cuáles fallaron.
        legacy_cierre = self._legacy_para_sesion(sesion)
        avisos_legacy: List[str] = []
        for sr in subrutas:
            try:
                res_sr = legacy_cierre.confirmar_registro(
                    sr.items[0].nota_num or sr.items[0].factura_num,
                    "despacho",
                    guia=sr.guia,
                    sub_ruta=sr.sub_ruta,
                    rutagrama_padre=sr.rutagrama_padre,
                    sub_rutagrama=sr.sub_rutagrama,
                    nota=",".join(i.nota_num for i in sr.items),
                    ayudantes=datos_vehiculo.ayudantes,
                    chofer=datos_vehiculo.chofer,
                    carro=datos_vehiculo.carro,
                    vehiculo=datos_vehiculo.carro,
                    clave_visor=datos_vehiculo.clave_visor,
                )
                if not isinstance(res_sr, dict) or res_sr.get("status") not in ("ok", "success"):
                    detalle = (res_sr or {}).get("mensaje", "sin detalle") if isinstance(res_sr, dict) else "sin detalle"
                    aviso = f"{sr.sub_rutagrama} ({sr.sub_ruta}): {detalle}"
                    avisos_legacy.append(aviso)
                    print(f"[RouteService] Legacy NO registró {aviso}")
            except Exception as e:
                aviso = f"{sr.sub_rutagrama} ({sr.sub_ruta}): excepción {e}"
                avisos_legacy.append(aviso)
                print(f"[RouteService] {aviso}")
                traceback.print_exc()
        aviso_legacy = (
            f"{len(avisos_legacy)} de {len(subrutas)} sub-ruta(s) NO quedaron registradas en el visor legacy: "
            + " | ".join(avisos_legacy)
        ) if avisos_legacy else None

        # Puntos de ruta (responsable + ayudante, MISMOS puntos para ambos —
        # confirmado contra `puntajes` real) — best-effort, no bloqueante.
        # Una fila por Sub-Rutagrama cerrado, 1 pt/nota de esa sub-ruta.
        for sr in subrutas:
            referencia = sr.sub_rutagrama or sr.guia
            ayudante = datos_vehiculo.ayudante_id or datos_vehiculo.ayudantes
            if self._mysql_sync is not None:
                try:
                    self._mysql_sync.confirmar_puntos_ruta(
                        referencia=referencia,
                        responsable=str(user_id).strip(),
                        total_notas=len(sr.items),
                        ayudante=ayudante,
                    )
                except Exception as e:
                    traceback.print_exc()
                    print(f"[RouteService] Puntos de ruta (MySQL) fallaron para {referencia}: {e}")
            try:
                self._repositorio.registrar_puntos_ruta(
                    sub_rutagrama=referencia,
                    responsable_id=str(user_id).strip(),
                    ayudante_id=ayudante,
                    total_notas=len(sr.items),
                )
            except Exception as e:
                traceback.print_exc()
                print(f"[RouteService] Puntos de ruta (local) fallaron para {referencia}: {e}")

        total_notas = sum(len(s["items"]) for s in guardadas)
        total_paquetes = sum(i.get("paquetes", 0) for s in guardadas for i in s["items"])
        mensaje = f"Ruta finalizada: {len(guardadas)} sub-ruta(s) generadas ({total_notas} nota(s) cerrada(s))"
        if items_pendientes:
            mensaje += f" — {len(items_pendientes)} nota(s) quedaron pendientes (0-0/1-0) para un próximo cierre."
        return {
            "status": "success",
            "mensaje": mensaje,
            "subrutas": guardadas,
            "total_subrutas": len(guardadas),
            "total_notas": total_notas,
            "total_paquetes": total_paquetes,
            "total_pendientes_sin_cerrar": len(items_pendientes),
            "aviso_legacy": aviso_legacy,
            "listo_para_repolab": True,
            # v4.37: datos_vehiculo completo (incluye los campos solo-impresión:
            # cédulas, placa, INTT, etc.) para que el frontend se lo pase tal
            # cual a repolab_printer.js — save_subrutas() puede no persistir
            # todos los campos nuevos, esto garantiza que lleguen igual.
            "datos_vehiculo": datos_vehiculo.model_dump(),
        }

    # ── 5. Reportes para REPOLAB ──────────────────────────────────────────────
    def get_reportes_finalizadas(self, user_id: str, es_admin: bool = False, usuario_filtro: str = "") -> dict:
        """Sub-rutas finalizadas para REPOLAB. `es_admin=True` audita TODOS los
        operadores (opcionalmente acotado a `usuario_filtro`); si no, solo
        muestra las del propio `user_id` — mismo criterio RBAC que el resto
        de reportes (discrepancias/trazabilidad, ver ara_server.py)."""
        if es_admin:
            registros = self._repositorio.get_rutas_finalizadas_todas(usuario_filtro)
        else:
            registros = self._repositorio.get_rutas_finalizadas_by_user(user_id)
        return {
            "status": "success",
            "total_subrutas": len(registros),
            "subrutas": registros,
        }

    # ── 5b. Verificación real de notas de crédito (dev_cli, Profit) ──────────
    def verificar_nota_credito(self, numero: str) -> dict:
        """Verifica una nota de crédito contra la BD real (dev_cli), no por texto.

        Antes, el módulo solo "adivinaba" NOTA_CREDITO buscando 'NC-' en el
        texto del pedido/cliente; esto consulta la tabla real de Profit y
        trae nombre del cliente, descripción/motivo y ruta (co_tran).
        Degrada a {"existe": false, ...} si Profit no responde (nunca lanza).
        """
        numero = str(numero or "").strip()
        if not numero:
            return {"status": "error", "mensaje": "Falta el número de nota de crédito."}
        try:
            res = self._facturador.verificar_nota_credito(numero)
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "mensaje": f"No se pudo verificar la nota de crédito: {e}",
                "codigo_error": "ERROR_PROFIT",
            }
        if not res.get("existe"):
            return {
                "status": "error" if res.get("error") else "success",
                "existe": False,
                "numero": numero,
                "mensaje": res.get("error") or f"La nota {numero} no existe como nota de crédito en Profit.",
            }
        return {"status": "success", "existe": True, "numero": numero, **res}

    # ── 6. MACRO-RUTAS MULTI-SEDE (embalaje → despacho → división por zona) ──
    # La sesión del usuario determina la sede ('SC' | 'BQTO'). El embalaje
    # alimenta la Macro-Ruta ACTIVA de la sede. El despacho escanea SOLO
    # Facturas. El cierre divide automáticamente los Rutagramas por zona.

    @staticmethod
    def _generar_id_macro(sede_id: str, fecha: Optional[str] = None) -> str:
        """ID de Macro-Ruta: 'MACRO-<SEDE>-<YYYYMMDD>'."""
        fecha = str(fecha or datetime.now().strftime("%Y%m%d")).strip()
        return f"MACRO-{str(sede_id or '').strip().upper()}-{fecha}"

    def _resolve_zona(self, co_cli: str, razon_social: str, num_nota: str = "") -> str:
        """Zona de destino del ítem empacado (resolución multi-fuente).

        Orden:
          1. Registro previo en rutagrama_notas (sub_ruta heredada del cierre).
          2. Catálogo Legacy (sub-rutas reales del visor): coincidencia por
             tokens del nombre del cliente o del código de la nota.
          3. Sin coincidencia → '' (se agrupará en 'SIN ZONA').
        """
        try:
            if num_nota:
                previo = self._repositorio.buscar_nota_rutagrama(num_nota)
                sub = (previo or {}).get("sub_ruta") or ""
                if sub:
                    return str(sub).strip()
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] sub_ruta previa no disponible: {e}")

        cliente = str(razon_social or "").upper()
        codigo = str(co_cli or "").upper()
        nota_upper = str(num_nota or "").upper()
        for macro, subs in (self._catalogo_zonas() or {}).items():
            for sub in subs or []:
                nombre = str(sub or "").upper()
                if not nombre:
                    continue
                if nombre in cliente or nombre in nota_upper:
                    return str(sub).strip()
                tokens = [t for t in nombre.split() if len(t) >= 3]
                if tokens and any(t in cliente for t in tokens):
                    return str(sub).strip()
                if codigo and (codigo in nombre or nombre in codigo):
                    return str(sub).strip()
        return ""

    def _catalogo_zonas(self) -> dict:
        """Macro-Rutas → sub-rutas (catálogo dinámico con respaldo estático)."""
        try:
            catalogo = self._catalogo_dinamico()
            if catalogo:
                return catalogo
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] Catálogo dinámico para zonas falló: {e}")
        try:
            return self._legacy.catalogo_estatico()
        except Exception:
            return {}

    def get_or_create_macro_ruta_activa(self, sede_id: str) -> MacroRuta:
        """Macro-Ruta ACTIVA de la sede; la crea (persistida) si no existe."""
        sede = normalizar_sede(sede_id) or "BQTO"
        try:
            reg = self._repositorio.get_macro_ruta_activa(sede)
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] Error leyendo macro activa {sede}: {e}")
            reg = None
        if reg:
            macro = MacroRuta(
                id=reg["id"],
                sede_id=reg["sede_id"],
                fecha=reg["fecha"],
                estado=reg["estado"],
                notas=[
                    ItemMacroRuta(
                        num_nota=n.get("num_nota") or "",
                        num_factura=n.get("num_factura") or None,
                        co_cli=n.get("co_cli") or "",
                        razon_social=n.get("razon_social") or "",
                        total_bultos=int(n.get("total_bultos") or 0),
                        estado_despacho=n.get("estado_despacho") or ESTADO_ITEM_PENDIENTE,
                        zona_destino=n.get("zona_destino") or "",
                        operador_embalaje=n.get("operador_embalaje") or "",
                        fecha_embalaje=n.get("fecha_embalaje"),
                        fecha_despacho=n.get("fecha_despacho"),
                        despachado_por=n.get("despachado_por") or "",
                    )
                    for n in (reg.get("notas") or [])
                ],
            )
            return macro

        base = self._generar_id_macro(sede)
        macro_id = base
        n = 2
        try:
            while self._repositorio.get_macro_ruta(macro_id) is not None:
                macro_id = f"{base}-{n}"
                n += 1
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] Error sondeando ids de macro {sede}: {e}")
            macro_id = base

        macro = MacroRuta(
            id=macro_id,
            sede_id=sede,
            fecha=datetime.now().strftime("%Y%m%d"),
            estado=ESTADO_MACRO_ACTIVA,
        )
        try:
            self._repositorio.guardar_macro_ruta(macro.model_dump())
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] No se pudo crear la macro {macro.id}: {e}")
        return macro

    def on_embalaje_finalizado(self, evento: str, data: dict) -> dict:
        """Handler del evento 'embalaje.finalizado' → Macro-Ruta de la sede.

        Alimenta la Macro-Ruta ACTIVA de la sede del usuario en sesión con el
        ítem empacado (idempotente por num_nota, sin perder el DESPACHADO).
        """
        data = data or {}
        num_nota = str(data.get("num_nota") or data.get("numero_nota") or "").strip()
        if not num_nota:
            return {
                "status": "error",
                "mensaje": "Evento de embalaje sin num_nota.",
                "vinculada": False,
            }
        sede = normalizar_sede(data.get("sede_id")) or "BQTO"
        macro = self.get_or_create_macro_ruta_activa(sede)
        zona = str(data.get("zona_destino") or "").strip() or self._resolve_zona(
            co_cli=str(data.get("co_cli") or ""),
            razon_social=str(data.get("razon_social") or ""),
            num_nota=num_nota,
        )
        macro.agregar_o_actualizar_nota(
            num_nota=num_nota,
            num_factura=data.get("num_factura") or None,
            co_cli=data.get("co_cli") or "",
            razon_social=data.get("razon_social") or "",
            total_bultos=data.get("total_bultos") or 0,
            zona_destino=zona,
            operador_embalaje=data.get("operador_embalaje") or "",
        )
        guardado = None
        try:
            guardado = self._repositorio.guardar_macro_ruta(macro.model_dump())
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] No se pudo guardar la macro {macro.id}: {e}")
        _print_seguro(
            f"[RouteService] MacroRuta {macro.id} ({sede}): nota {num_nota} "
            f"alimentada con {int(data.get('total_bultos') or 0)} bulto(s) "
            f"-> zona '{zona or 'SIN ZONA'}'"
        )
        return {
            "status": "success",
            "vinculada": guardado is not None,
            "macro_id": macro.id,
            "sede_id": sede,
            "num_nota": num_nota,
            "total_bultos": int(data.get("total_bultos") or 0),
            "zona_destino": zona,
            "mensaje": f"Nota {num_nota} alimentó la Macro-Ruta {macro.id}.",
        }

    def get_estado_macro(self, sede_id: str) -> dict:
        """Estado de la Macro-Ruta ACTIVA de la sede para el indicador de UI."""
        sede = normalizar_sede(sede_id) or "BQTO"
        try:
            reg = self._repositorio.get_macro_ruta_activa(sede)
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "mensaje": f"No se pudo leer la macro activa: {e}",
            }
        if not reg:
            return {
                "status": "success",
                "sede_id": sede,
                "sede_nombre": SEDES_VALIDAS.get(sede, sede),
                "macro_id": None,
                "estado": None,
                "pendientes": 0,
                "despachados": 0,
                "total_bultos": 0,
                "notas": [],
            }
        notas = reg.get("notas") or []
        return {
            "status": "success",
            "sede_id": sede,
            "sede_nombre": SEDES_VALIDAS.get(sede, sede),
            "macro_id": reg.get("id"),
            "fecha": reg.get("fecha"),
            "estado": reg.get("estado"),
            "pendientes": sum(
                1 for n in notas
                if n.get("estado_despacho") != ESTADO_ITEM_DESPACHADO
            ),
            "despachados": sum(
                1 for n in notas
                if n.get("estado_despacho") == ESTADO_ITEM_DESPACHADO
            ),
            "total_notas": len(notas),
            "total_bultos": sum(int(n.get("total_bultos") or 0) for n in notas),
            # Lista completa (no solo el agregado): el visor de REALIZAR RUTA la
            # necesita para mostrar, por sub-ruta/zona, las notas que llegaron
            # por auto-vinculación de embalaje y que NUNCA aparecen en `items`
            # (lista legacy de lista.php) — sin esto el chofer no tenía forma
            # de VER esas notas en la pantalla de su ruta, aunque ya estuvieran
            # registradas en la Macro-Ruta (bug real reportado 2026-08-20).
            "notas": notas,
        }

    def despachar_factura_macro(self, sede_id: str, num_factura_o_nc: str) -> dict:
        """DESPACHO EN MACRO-RUTA: escaneo EXCLUSIVO de Facturas.

        Obtiene la Macro-Ruta ACTIVA de la sede, busca la nota cuyo
        `num_factura` (o `num_nota`) coincida con el código escaneado y la
        marca como DESPACHADO vinculando TODOS sus bultos sin escaneo por caja.
        """
        sede = normalizar_sede(sede_id) or "BQTO"
        codigo = str(num_factura_o_nc or "").strip()
        if not codigo:
            return {"status": "error", "mensaje": "Código de factura vacío."}
        macro = self.get_or_create_macro_ruta_activa(sede)
        item = macro.buscar_por_codigo(codigo)
        if item is None:
            return {
                "status": "error",
                "mensaje": (
                    f"La factura {codigo} no está en la Macro-Ruta {macro.id} "
                    f"({SEDES_VALIDAS.get(sede)})."
                ),
                "codigo_error": "NOTA_NO_ENCONTRADA",
                "macro_id": macro.id,
                "sede_id": sede,
            }
        if item.estado_despacho == ESTADO_ITEM_DESPACHADO:
            return {
                "status": "error",
                "mensaje": (
                    f"La nota {item.num_nota} (factura {codigo}) ya fue "
                    f"DESPACHADA con {item.total_bultos} bulto(s)."
                ),
                "codigo_error": "YA_DESPACHADA",
                "macro_id": macro.id,
                "num_nota": item.num_nota,
                "num_factura": item.num_factura,
                "cliente": item.razon_social,
                "bultos_despachados": item.total_bultos,
            }

        item.estado_despacho = ESTADO_ITEM_DESPACHADO
        try:
            self._repositorio.marcar_item_macro_despachado(
                macro.id, item.num_nota, despachado_por=""
            )
            self._repositorio.guardar_macro_ruta(macro.model_dump())
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] No se pudo persistir despacho macro {macro.id}: {e}")

        _print_seguro(
            f"[RouteService] MacroRuta {macro.id} ({sede}): factura {codigo} "
            f"DESPACHADA -> nota {item.num_nota}, {item.total_bultos} bulto(s)"
        )
        pendientes = sum(
            1 for n in macro.notas if n.estado_despacho != ESTADO_ITEM_DESPACHADO
        )
        return {
            "status": "success",
            "num_factura": codigo,
            "num_nota": item.num_nota,
            "cliente": item.razon_social,
            "zona_destino": item.zona_destino,
            "bultos_despachados": item.total_bultos,
            "macro_id": macro.id,
            "sede_id": sede,
            "pendientes": pendientes,
            "mensaje": (
                f"Factura {codigo} DESPACHADA: nota {item.num_nota} con "
                f"{item.total_bultos} bulto(s) vinculados automáticamente."
            ),
        }

    def cerrar_macro_ruta(self, sede_id: str, datos_vehiculo: Optional[DatosVehiculo] = None) -> dict:
        """CIERRE DE MACRO-RUTA: divide y genera los Rutagramas por zona.

        1. Recupera la Macro-Ruta ACTIVA de la sede.
        2. Filtra SOLO los ítems con estado_despacho == 'DESPACHADO'.
        3. Agrupa por zona_destino (sub-ruta) → crea un Rutagrama por zona.
        4. Guarda cada Rutagrama (sub_rutas_finalizadas) y marca la Macro-Ruta
           como CERRADA. Retorna el resumen de Rutagramas con sus totales.
        """
        sede = normalizar_sede(sede_id) or "BQTO"
        macro = self.get_or_create_macro_ruta_activa(sede)
        despachadas = [
            it for it in macro.notas
            if it.estado_despacho == ESTADO_ITEM_DESPACHADO
        ]
        if not despachadas:
            return {
                "status": "error",
                "mensaje": (
                    f"No hay facturas DESPACHADAS en {macro.id} ({SEDES_VALIDAS.get(sede)}). "
                    "Escanee las facturas en el módulo de despacho antes de cerrar."
                ),
                "codigo_error": "SIN_DESPACHADAS",
                "macro_id": macro.id,
            }

        datos_vehiculo = datos_vehiculo or DatosVehiculo()
        guia_base = f"G-{int(time.time())}"
        rutagrama_padre = macro.id
        grupos: Dict[str, List[ItemMacroRuta]] = {}
        for it in despachadas:
            grupos.setdefault(it.zona_destino or "SIN ZONA", []).append(it)

        subrutas: List[SubRutaFinalizada] = []
        for n, (zona, items) in enumerate(sorted(grupos.items()), start=1):
            sub_rutagrama = f"{rutagrama_padre}-{n:02d}"
            subrutas.append(
                SubRutaFinalizada(
                    guia=f"{guia_base}-{n:02d}",
                    rutagrama_padre=rutagrama_padre,
                    sub_rutagrama=sub_rutagrama,
                    sub_ruta=zona,
                    items=[
                        ItemRuta(
                            nota_num=it.num_nota,
                            factura_num=it.num_factura or "",
                            num_fact=it.num_factura,
                            codigo_cliente=it.co_cli,
                            razon_social=it.razon_social,
                            paquetes=it.total_bultos,
                            sub_ruta=zona,
                            ruta_macro=macro.id,
                            estado_ara=ESTADO_ITEM_DESPACHADO.lower(),
                            verificado=True,
                            scan_lote=True,
                            scanned_factura=True,
                        )
                        for it in items
                    ],
                    datos_vehiculo=datos_vehiculo,
                    responsable_id=str(datos_vehiculo.chofer or "").strip() or "",
                )
            )

        guardadas = []
        try:
            guardadas = self._repositorio.save_subrutas(subrutas)
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] No se pudieron guardar los rutagramas: {e}")

        # Puente hacia el visor Legacy (192.168.4.148:8000): sin esto, el
        # Rutagrama de la Macro-Ruta queda 100% local (SQLite) y jamás
        # aparece en listado.php — la app de choferes solo puede re-escanear
        # cajas/facturas contra despachos que existan ahí. No bloqueante
        # (igual que en finalizar_ruta): si el visor falla, el Rutagrama
        # local ya quedó guardado y se puede reintentar el registro después.
        legacy = self._legacy_bqto if sede == "BQTO" else self._legacy
        aviso_legacy = None
        try:
            for sr in subrutas:
                legacy.confirmar_registro(
                    sr.items[0].nota_num or sr.items[0].factura_num,
                    "despacho",
                    guia=sr.guia,
                    sub_ruta=sr.sub_ruta,
                    rutagrama_padre=sr.rutagrama_padre,
                    sub_rutagrama=sr.sub_rutagrama,
                    nota=",".join(i.nota_num for i in sr.items),
                    ayudantes=datos_vehiculo.ayudantes,
                    chofer=datos_vehiculo.chofer,
                    carro=datos_vehiculo.carro,
                    vehiculo=datos_vehiculo.carro,
                    clave_visor=datos_vehiculo.clave_visor,
                )
        except Exception as e:
            aviso_legacy = f"No se pudo registrar el despacho en Legacy: {e}"
            print(f"[RouteService] {aviso_legacy}")
            traceback.print_exc()

        macro.estado = ESTADO_MACRO_CERRADA
        try:
            self._repositorio.guardar_macro_ruta(macro.model_dump())
        except Exception as e:
            traceback.print_exc()
            print(f"[RouteService] No se pudo cerrar la macro {macro.id}: {e}")

        resumen = []
        for sr in guardadas:
            total_bultos = sum(
                int(i.get("paquetes") or 0) for i in sr.get("items") or []
            )
            resumen.append({
                "id": sr.get("id"),
                "guia": sr.get("guia"),
                "rutagrama_padre": sr.get("rutagrama_padre"),
                "sub_rutagrama": sr.get("sub_rutagrama"),
                "zona": sr.get("sub_ruta"),
                "total_notas": sr.get("total_notas", len(sr.get("items") or [])),
                "total_bultos": total_bultos,
                "chofer": sr.get("chofer"),
                "items": sr.get("items") or [],
            })

        total_notas = sum(r["total_notas"] for r in resumen)
        total_bultos = sum(r["total_bultos"] for r in resumen)
        return {
            "status": "success",
            "mensaje": (
                f"Macro-Ruta {macro.id} CERRADA: {len(resumen)} rutagrama(s) "
                f"generado(s) por zona ({total_notas} notas, {total_bultos} bultos)."
            ),
            "macro_id": macro.id,
            "sede_id": sede,
            "sede_nombre": SEDES_VALIDAS.get(sede, sede),
            "total_rutagramas": len(resumen),
            "total_notas": total_notas,
            "total_bultos": total_bultos,
            "rutagramas": resumen,
            "listo_para_repolab": True,
            "aviso_legacy": aviso_legacy,
        }


    # ── Helpers de la matriz de estados ───────────────────────────────────────
    def _pendientes(self, sesion: dict) -> List[ItemRuta]:
        """Ítems en estado 0-0 o 1-0 (falta verificación → 1-1)."""
        return [i for i in sesion["items"] if not (i.scan_lote and i.verificado)]

    @staticmethod
    def _item_dict(it: ItemRuta) -> dict:
        d = it.model_dump()
        d["matriz_estado"] = it.matriz_estado
        d["estado_cotejo"] = it.matriz_estado
        d["nota"] = it.nota_num
        d["factura"] = it.factura_num
        d["num_fact"] = it.num_fact or None
        d["num_fac"] = it.num_fact or None
        return d

    @staticmethod
    def _resumen_sub_rutas(items: List[ItemRuta],
                           sub_rutagramas: Optional[Dict[str, str]] = None) -> List[dict]:
        """Resumen por sub-ruta con conteo de estados de la matriz (0-0 / 1-0 / 1-1)
        y el Sub-Rutagrama individual asignado."""
        grupos: Dict[str, List[ItemRuta]] = {}
        for it in items:
            grupos.setdefault(it.sub_ruta or "SIN SUB-RUTA", []).append(it)
        resumen = []
        for nombre, its in sorted(grupos.items()):
            n00 = sum(1 for i in its if i.matriz_estado == "0-0")
            n10 = sum(1 for i in its if i.matriz_estado == "1-0")
            n11 = sum(1 for i in its if i.matriz_estado == "1-1")
            resumen.append({
                "sub_ruta": nombre,
                "sub_rutagrama": (sub_rutagramas or {}).get(nombre) or "",
                "total": len(its),
                "n00": n00,
                "n10": n10,
                "n11": n11,
            })
        return resumen

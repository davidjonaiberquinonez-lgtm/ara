import json
import re
import time
import traceback
from html.parser import HTMLParser
from typing import List, Optional, Tuple

import requests as req_lib

from ..domain.models import ItemRuta
from ..domain.ports import RouteLegacyPort
from preparacion.domain.notas_sc import es_nota_san_cristobal

DEFAULT_LEGACY_URL = "http://192.168.4.148:8000"
PATH_LISTA = "/visor/lista.php"
PATH_REGISTRO = "/visor/registro.php"
PATH_INDEX = "/visor/index.php"
PATH_EMBALAJE = "/gestion_produc_bqmt/actualizar_nota_embalaje.php"

# El visor tarda en confirmar un registro de embalaje (>12s en producción);
# usar un timeout amplio para no marcar error cuando el PHP sigue procesando.
TIMEOUT_EMBALAJE = 90

# TTL del catálogo dinámico: éxito 300s; si index.php falla se cachea [] por
# este mismo periodo para no golpear el visor en cada petición del frontend.
_TTL_CATALOGO = 300
_TIMEOUT_CATALOGO = 8

# Mapa estático de traducción Macro-Ruta → Sub-Rutas reales del sistema Legacy.
# lista.php NO acepta parámetros de catálogo: requiere el NOMBRE EXACTO de la
# sub-ruta para devolver ítems. Al solicitar "CARACAS" se consultan todas las
# sub-rutas reales de la zona y se consolidan sus pedidos.
#
# v4.35: reemplazados los nombres inventados/nunca verificados (p.ej.
# "BARQUISIMETO CENTRO/ESTE/OESTE", "CABUDARE", macro "VALENCIA" completa —
# ninguno existe en el sistema real) por los 45 nombres reales confirmados en
# vivo contra `<select name="ruta">` de index.php (192.168.4.148:8000/visor/,
# mismo sistema que legacy_visor/RUTA_VISOR). Es el catálogo BASE que siempre
# se muestra (v4.35: se combina con lo que traiga el fetch dinámico, nunca lo
# reemplaza — ver RouteService.get_catalogo_rutas), aunque el visor esté en un
# momento de pocos pedidos pendientes.
MAPA_MACRO_SUBRUTAS = {
    "ARAGUA": [
        "ARAGUA - ZARAZA",
        "ARAGUA CAPITAL",
    ],
    "CARACAS": [
        "CARACAS - ALTA LA GUAIRA",
        "CARACAS - BAJA CHARALLAVE",
        "CARACAS - ESTE HIGUEROTE",
    ],
    "BARQUISIMETO": [
        "BARQUISIMETO - YARACUY",
        "BARQUISIMETO CAPITAL",
    ],
    "CARABOBO": [
        "CARABOBO - ALTA PUERTO CABELLO",
        "CARABOBO - BAJA NIRGUA",
        "CARABOBO - CAPITAL TACARIGUA",
    ],
    "BARINAS": [
        "Barinas / Andes 1",
        "BARINAS CAPITAL ALTA 2",
        "BARINAS CAPITAL BAJA",
    ],
    "PORTUGUESA": [
        "PORTUGUESA ALTA - CAPITAL",
        "PORTUGUESA ALTA - COJEDES",
        "PORTUGUESA BAJA - BISCUCUY",
        "PORTUGUESA BAJA - CAPITAL",
    ],
    "TRUJILLO": [
        "TRUJILLO - PARAMOS",
        "TRUJILLO ALTA - PARTE BAJA",
        "TRUJILLO BAJA 1",
    ],
    "ZULIA": [
        "ZULIA - CAPITAL",
        "ZULIA FRON MACHIQUES",
        "ZULIA TRU CD OJEDA",
    ],
    "MERIDA": [
        "MERIDA MONTAÑA - CAPITAL Y PARTE ALTA",
        "MERIDA MONTAÑA - EJIDO Y PARTE BAJA",
        "MERIDA PLANO - ARAPUEY",
        "MERIDA PLANO - CASIGUA",
    ],
    "FALCON": [
        "FALCON -  PUNTO FIJO",
        "FALCON - VELA DE CORO",
    ],
    "APURE": [
        "APURE CAPITAL 2",
        "APURE RUTA 1",
    ],
    "FRONTERA / TACHIRA": [
        "(FORANEO) FRONTERA",
        "(FORANEO) PANAMERICANA",
        "FRONTERA",
        "Fuera Del Tachira",
        "PANAMERICANA",
        "SANTA ANA (TACHIRA)",
        "TACHIRA SITIOS FORANEOS SIN VENDEDOR",
    ],
    "RUTAS ESPECIALES / OTROS": [
        "CASA DE REPRESENTACION (COMPRAS)",
        "INMEDIATO S/C",
        "INTERNO (CRISTMEDICALS)",
        "LLANO",
        "NACIONAL (COMPRAS)",
        "PEDIDOS DE OFICINA",
        "PLAZA un despacho por dia",
    ],
    # Transferencia entre almacenes (v4.31) — del sistema legacy real
    # (legacy_visor/RUTA_VISOR/index.php), NO son rutas de reparto a
    # clientes: mueven mercancía de un almacén al otro.
    "ENVÍOS ENTRE ALMACENES": [
        "ENVIOS BARQUISIMETO",
        "ENVIOS S/C",
    ],
}

# Valores 'raw' (value= del <option>) de las rutas especiales de transferencia
# entre almacenes — verificados en vivo contra legacy_visor/RUTA_VISOR/index.php
# (v4.31). Fijos porque el catálogo estático no tiene código de ruta propio
# (MAPA_MACRO_SUBRUTAS no lleva 'raw'), y sin el value exacto el login al
# visor (iniciar_guia_legacy) fallaría al no reconocer el nombre limpio.
RUTAS_ESPECIALES_RAW = {
    "ENVIOS BARQUISIMETO": "barquisimeto1/BQTO-SC",
    "ENVIOS S/C": "barquisimeto2/SC-BQTO",
}

CLAVES_ITEM = {
    "nota_num": "nota_num",
    "numero_nota": "nota_num",
    "nota": "nota_num",
    "codigoBarra": "nota_num",
    "factura_num": "factura_num",
    "factura": "factura_num",
    "num_fact": "factura_num",
    "sub_ruta": "sub_ruta",
    "subruta": "sub_ruta",
    "ruta": "sub_ruta",
    "codigo_cliente": "codigo_cliente",
    "codigo_cli": "codigo_cliente",
    "co_cli": "codigo_cliente",
    "codigo": "codigo_cliente",
    "razon_social": "razon_social",
    "descripcion": "razon_social",
    "cliente": "razon_social",
    "cli_des": "razon_social",
    "nombre_cliente": "razon_social",
    "estado_ara": "estado_ara",
    "estado": "estado_ara",
    "estado_legacy": "estado_legacy",
    "estado_raw": "estado_legacy",
    "status": "estado_legacy",
    "tipo_documento": "tipo_documento",
    "tipo_doc": "tipo_documento",
    "paquetes": "paquetes",
    "bultos": "paquetes",
    "cajas": "paquetes",
    "cantidad": "paquetes",
    "peso": "peso",
    "fecha_creacion": "fecha_creacion",
    "creada": "fecha_creacion",
    "fecha": "fecha_creacion",
}


class _TablasListaHTML(HTMLParser):
    """Extrae las filas (lista de celdas) de TODAS las <table> de lista.php.

    Tolerante al HTML sucio del visor: tags erróneos como <sapn> se tratan como
    texto suelto de la celda y no rompen la estructura; se ignora el contenido
    de <script>/<style>.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tablas: List[dict] = []
        self._tabla: Optional[dict] = None
        self._fila: Optional[List[str]] = None
        self._fila_con_th = False
        self._celda: Optional[List[str]] = None
        self._en_script_style = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style"):
            self._en_script_style += 1
        if self._en_script_style:
            return
        if tag == "table":
            self._tabla = {"header": [], "filas": []}
        elif tag == "tr":
            self._fila = []
            self._fila_con_th = False
        elif tag == "th":
            self._fila_con_th = True
            self._celda = []
        elif tag == "td":
            self._celda = []
        elif tag == "br":
            if self._celda is not None:
                self._celda.append(" ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style"):
            if self._en_script_style:
                self._en_script_style -= 1
            return
        if self._en_script_style:
            return
        if tag in ("td", "th"):
            if self._celda is not None and self._fila is not None:
                texto = " ".join("".join(self._celda).split())
                self._fila.append(texto)
            self._celda = None
        elif tag == "tr":
            if self._fila is not None and self._tabla is not None:
                self._tabla["filas"].append((self._fila, self._fila_con_th))
            self._fila = None
        elif tag == "table":
            if self._tabla is not None:
                if self._tabla["filas"]:
                    fila0, con_th = self._tabla["filas"][0]
                    if con_th:
                        self._tabla["header"] = fila0
                self.tablas.append(self._tabla)
            self._tabla = None

    def handle_data(self, data):
        if self._en_script_style or self._celda is None:
            return
        self._celda.append(data)


class LegacyRouteAdapter(RouteLegacyPort):
    def __init__(
        self,
        base_url: str = DEFAULT_LEGACY_URL,
        timeout: int = 15,
        responsable_visor: str = "22/JONAIBER QUIÑONEZ",
        clave_visor: str = "",
        puente_url: Optional[str] = None,
    ):
        # AMPLIADO v4.53 — `puente_url`: el Apache de DEFAULT_LEGACY_URL
        # filtra los datos según la IP de origen de quien consulta
        # (verificado en vivo): la MISMA ruta física (ej. "CARABOBO - ALTA
        # PUERTO CABELLO") trae solo notas S/C consultada desde la red de
        # S/C, pero trae notas S/C + notas BQTO reales (72163xxx) mezcladas
        # consultada desde una máquina de la red de BQTO — es la fuente real
        # de rutas para BQTO (no existe en ningún otro lado, ni Profit ni un
        # sistema propio de BQTO). `puente_url` apunta a un mini-servidor
        # HTTP (bin/puente_visor_bqto.ps1) corriendo en una máquina de la red
        # de BQTO, que reenvía la consulta localmente allá y devuelve el HTML
        # ya autenticado — así el fetch de datos sale con la IP de origen
        # correcta sin que ARA necesite estar físicamente en esa red. El
        # catálogo de rutas (fetch_catalogo_rutas_legacy) es idéntico desde
        # cualquier IP (verificado), así que solo el paso de DATOS
        # (_fetch_pedidos_sub_ruta) usa el puente; el resto del adaptador
        # sigue igual.
        self._puente_url = (puente_url or "").rstrip("/") or None
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # Sesión HTTP persistente (requests.Session): mantiene PHPSESSID viva
        # entre el POST de inicio de guía (index.php) y las consultas/cierres.
        # lista.php exige PHPSESSID y devuelve los pedidos de la ruta del
        # login (ignora ?ruta=). La guía es estable por ruta.
        self._http = req_lib.Session()
        # Conexión persistente (v4.25): fuerza keep-alive explícito y reintento
        # 0 a nivel de socket (los reintentos de aplicación ya los maneja
        # _get/_post) — evita que una conexión TCP se abra de nuevo en cada
        # request secuencial a la misma sub-ruta.
        _adaptador = req_lib.adapters.HTTPAdapter(
            pool_connections=1, pool_maxsize=4, max_retries=0
        )
        self._http.mount("http://", _adaptador)
        self._http.mount("https://", _adaptador)
        self._http.headers.update({"Connection": "keep-alive"})
        # Responsable por defecto del visor (valor del select del operador).
        self._responsable_visor = str(responsable_visor or "22/JONAIBER QUIÑONEZ").strip()
        # Clave de seguridad del visor para registrar cierres de despacho
        # (registro.php). Solo se envía en el POST; nunca se imprime ni loguea.
        self._clave_visor = str(clave_visor or "").strip()
        self._cookies: dict = {}
        self._autenticado = False
        self._sesion_ruta = ""
        self._cache_catalogo: Optional[List[Tuple[str, str]]] = None
        self._cache_catalogo_ts = 0.0
        # Sembrado con las rutas especiales de transferencia (v4.31): fijo,
        # nunca depende de que el fetch dinámico las traiga.
        self._raw_por_sub_ruta: dict = dict(RUTAS_ESPECIALES_RAW)

    @property
    def puente_url(self) -> Optional[str]:
        """URL del puente BQTO configurado (o None) — expuesta públicamente
        para que el código de arranque (ara_server.py) pueda loguearla sin
        acceder al atributo privado `_puente_url` a través del tipo abstracto
        `RouteLegacyPort` (que no lo declara — Pylance marcaba esto como
        reportAttributeAccessIssue)."""
        return self._puente_url

    # ── HTTP ──────────────────────────────────────────────────────────────────
    def _get(self, path: str, params: dict, timeout: Optional[float] = None) -> str:
        url = f"{self._base_url}{path}"
        print(f"[LegacyRoute] GET {url} params={params}")
        for _intento in range(2):
            try:
                resp = self._http.get(
                    url,
                    params=params,
                    timeout=timeout or self._timeout,
                )
            except Exception as e:
                traceback.print_exc()
                raise ConnectionError(f"No se pudo conectar con /visor/: {e}")
            if resp.status_code != 200:
                raise ConnectionError(
                    f"/visor/ respondió HTTP {resp.status_code}: {resp.text[:200]}")
            if "Error al ingresar" not in resp.text:
                return resp.text
            if not self._autenticado:
                # Sesión nunca establecida: el llamador decide con qué ruta
                # autenticarse (lista.php ignora ?ruta= y usa la ruta del login).
                return resp.text
            # Sesión expirada: renovarla con la misma ruta ligada y reintentar.
            self.iniciar_guia_legacy(self._sesion_ruta or "1")
        return resp.text

    def _post(self, path: str, datos: dict, timeout: Optional[float] = None) -> str:
        url = f"{self._base_url}{path}"
        # La clave del visor nunca se imprime ni loguea.
        print(f"[LegacyRoute] POST {url} datos="
              f"{ {k: v for k, v in datos.items() if k != 'clave'} }")
        for _intento in range(2):
            try:
                resp = self._http.post(
                    url,
                    data=datos,
                    timeout=timeout or self._timeout,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except Exception as e:
                traceback.print_exc()
                raise ConnectionError(f"No se pudo conectar con /visor/: {e}")
            if resp.status_code != 200:
                raise ConnectionError(
                    f"/visor/ respondió HTTP {resp.status_code}: {resp.text[:200]}")
            if "Error al ingresar" not in resp.text:
                return resp.text
            if not self._autenticado:
                return resp.text
            self.iniciar_guia_legacy(self._sesion_ruta or "1")
        return resp.text

    def iniciar_guia_legacy(self, option_value_ruta: str, responsable: str = None,
                            session: Optional["req_lib.Session"] = None) -> bool:
        """Inicializa la guía del visor en la sesión PHP (POST index.php).

        El visor desplegado exige sesión: sin este POST, lista.php devuelve la
        página 'Error al ingresar' y no hay guía activa. Se envía el valor
        exacto del option (option_value_ruta) del catálogo con el botón
        'Empezar' (consulta). La guía es estable por ruta: repetir el login
        no genera registros. La sesión queda persistida en `self._http`
        (requests.Session) o en `session` si se provee una.
        """
        opcion = str(option_value_ruta or "").strip() or "1"
        http = session or self._http
        datos = {
            "responsable": str(responsable or "").strip()
            or self._responsable_visor or "22/JONAIBER QUIÑONEZ",
            "ruta": opcion,
            "consulta": "Empezar",
        }
        url = f"{self._base_url}{PATH_INDEX}"
        print(f"[LegacyRoute] POST {url} datos={datos}")
        try:
            resp = http.post(
                url,
                data=datos,
                timeout=self._timeout,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception as e:
            traceback.print_exc()
            raise ConnectionError(f"No se pudo iniciar la guía en /visor/: {e}")
        if resp.status_code != 200:
            raise ConnectionError(
                f"/visor/ respondió HTTP {resp.status_code} al iniciar la guía")
        if "Error al ingresar" in resp.text:
            print("[LegacyRoute] El visor rechazó el inicio de guía "
                  "(Error al ingresar).")
            return False
        self._cookies = dict(resp.cookies)
        self._autenticado = True
        self._sesion_ruta = opcion
        print("[LegacyRoute] Guía inicializada en la sesión PHP (Empezar).")
        return True

    def _autenticar_visor(self, ruta: str) -> None:
        """Compatibilidad: establece la sesión del visor para la sub-ruta.

        Equivale a iniciar_guia_legacy: POST index.php con el valor raw del
        catálogo (o el nombre) y consulta 'Empezar'.
        """
        self.iniciar_guia_legacy(ruta)

    # ── Parsing tolerante ─────────────────────────────────────────────────────
    @staticmethod
    def _parse_respuesta(texto: str) -> Optional[list]:
        """Extrae la lista de items desde JSON o texto plano 'campo: valor'."""
        texto = texto.strip()
        if not texto:
            return None

        try:
            obj = json.loads(texto)
        except (json.JSONDecodeError, ValueError):
            obj = None

        if isinstance(obj, dict):
            lista = (
                obj.get("items")
                or obj.get("data")
                or obj.get("cajas")
                or obj.get("resultado")
                or []
            )
            if isinstance(lista, dict):
                lista = [lista]
            if isinstance(lista, list):
                return [it for it in lista if isinstance(it, dict)]
        elif isinstance(obj, list):
            return [it for it in obj if isinstance(it, dict)]

        # Texto plano
        items: List[dict] = []
        item_actual: dict = {}
        for linea in texto.splitlines():
            linea = linea.strip().rstrip(",").strip("\"'")
            if not linea:
                continue
            for sep in (":", "="):
                if sep in linea:
                    clave, _, valor = linea.partition(sep)
                    clave = clave.strip().strip("\"'").strip().lower()
                    valor = valor.strip().strip("\"'").strip()
                    if clave == "nota_num" or clave in ("numero_nota", "nota", "codigobarra"):
                        if item_actual:
                            items.append(item_actual)
                        item_actual = {"nota_num": valor}
                    elif clave in ("factura_num", "factura", "num_fact"):
                        item_actual["factura_num"] = valor
                    elif clave in ("sub_ruta", "subruta", "ruta"):
                        item_actual["sub_ruta"] = valor
                    elif clave in ("razon_social", "cliente", "cli_des", "nombre_cliente"):
                        item_actual["razon_social"] = valor
                    elif clave in ("estado_ara", "estado"):
                        item_actual["estado_ara"] = valor
                    elif clave in ("paquetes", "bultos", "cajas", "cantidad"):
                        item_actual["paquetes"] = valor
                    break
        if item_actual:
            items.append(item_actual)
        return items if items else None

    # ── Embalaje (actualizar_nota_embalaje.php) ──────────────────────────────
    @staticmethod
    def _parsear_json_embalaje(texto: str) -> Optional[dict]:
        """Intenta leer el JSON del PHP de embalaje; None si no es JSON."""
        if not texto:
            return None
        try:
            obj = json.loads(texto)
        except (json.JSONDecodeError, ValueError):
            return None
        return obj if isinstance(obj, dict) else None

    def consultar_nota_embalaje(self, codigo_barra: str) -> dict:
        """Consulta una nota de embalaje por su código de barras.

        POST directo a actualizar_nota_embalaje.php (sin pasar por lista.php):
        esa consulta prevalece cuando la nota no figura en la BD local.

        Respuesta del visor (JSON):
            {"articulos": [{co_art, art_des, total_art}],
             "notaInfo": {cantidad_items, co_cli, cli_des, seg_des}}
        Retorno estable: {status, encontrada, notaInfo, articulos, mensaje}
        """
        codigo = str(codigo_barra or "").strip()
        if not codigo:
            return {"status": "error", "mensaje": "Código de barras vacío."}
        try:
            texto = self._post(PATH_EMBALAJE, {"codigoBarra": codigo})
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "mensaje": f"No se pudo consultar el visor de embalaje: {e}",
                "codigo_error": "ERROR_LEGACY",
            }

        obj = self._parsear_json_embalaje(texto)
        if obj is not None:
            nota_info = obj.get("notaInfo")
            articulos = obj.get("articulos")
            if isinstance(nota_info, dict) and isinstance(articulos, list):
                nota_limpia = {
                    k: str(v).strip() if isinstance(v, str) else v
                    for k, v in nota_info.items()
                }
                arts_limpios = [
                    {
                        "co_art": str(a.get("co_art") or "").strip(),
                        "art_des": str(a.get("art_des") or "").strip(),
                        "total_art": str(a.get("total_art") or "").strip(),
                    }
                    for a in articulos
                    if isinstance(a, dict)
                ]
                return {
                    "status": "success",
                    "encontrada": True,
                    "notaInfo": nota_limpia,
                    "articulos": arts_limpios,
                    "mensaje": "Nota encontrada en el visor de embalaje.",
                }
            mensaje = str(obj.get("message") or obj.get("mensaje") or "").strip()
            if "ya fue registrada" in mensaje.lower() or "ya registrada" in mensaje.lower():
                return {
                    "status": "error",
                    "encontrada": False,
                    "ya_embalada": True,
                    "mensaje": mensaje or "La nota ya fue registrada en el proceso de Embalaje.",
                }
            return {
                "status": "error",
                "encontrada": False,
                "ya_embalada": False,
                "mensaje": mensaje or "El visor no devolvió datos de la nota.",
            }

        # Fallback: texto plano con campos 'clave: valor'
        items = self._parse_respuesta(texto) or []
        if items:
            primero = items[0]
            return {
                "status": "success",
                "encontrada": True,
                "notaInfo": {
                    "cantidad_items": str(len(items)),
                    "co_cli": primero.get("codigo_cliente") or "",
                    "cli_des": primero.get("razon_social") or "",
                    "seg_des": "",
                },
                "articulos": [
                    {
                        "co_art": it.get("factura_num") or it.get("nota_num") or "",
                        "art_des": it.get("razon_social") or "",
                        "total_art": it.get("paquetes") or "",
                    }
                    for it in items
                ],
                "mensaje": "Nota encontrada (texto plano).",
            }
        bajo = texto.lower()
        if "no existe" in bajo or "no encontrada" in bajo or "not found" in bajo:
            return {
                "status": "error",
                "encontrada": False,
                "mensaje": f"La nota \"{codigo}\" no existe en el visor de embalaje.",
            }
        return {
            "status": "error",
            "encontrada": False,
            "mensaje": f"Respuesta inesperada del visor: {texto[:200]}",
        }

    def registrar_embalaje(self, codigo_barra: str, numero_embalador: str,
                           numero_mesa: str, cant_items: str, cant_cajas: str) -> dict:
        """Registra el embalaje de una nota en el visor (POST directo).

        El PHP tarda en confirmar (timeout amplio); responde JSON:
            {"success": true,  "message": "..."}
            {"success": false, "message": "La nota ya fue registrada..."}
        Retorno estable: {status, ok, ya_embalada, mensaje}
        """
        codigo = str(codigo_barra or "").strip()
        if not codigo:
            return {"status": "error", "ok": False, "mensaje": "Falta el código de barras."}
        payload = {
            "codigoBarra": codigo,
            "numeroEmbalador": str(numero_embalador or "").strip(),
            "numeroMesa": str(numero_mesa or "").strip(),
            "cant_items": str(cant_items or "").strip(),
            "cant_cajas": str(cant_cajas or "").strip(),
        }
        try:
            texto = self._post(PATH_EMBALAJE, payload, timeout=TIMEOUT_EMBALAJE)
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "ok": False,
                "mensaje": f"No se pudo registrar en el visor de embalaje: {e}",
                "codigo_error": "ERROR_LEGACY",
            }

        obj = self._parsear_json_embalaje(texto)
        if obj is not None:
            ok = bool(obj.get("success"))
            mensaje = str(obj.get("message") or obj.get("mensaje") or "").strip()
            bajo = mensaje.lower()
            ya_embalada = "ya fue registrada" in bajo or "ya registrada" in bajo
            return {
                "status": "success" if ok else "error",
                "ok": ok,
                "ya_embalada": ya_embalada,
                "mensaje": mensaje or ("Embalaje registrado." if ok else "El visor rechazó el registro."),
            }

        # Fallback: texto plano / HTML sin JSON
        bajo = texto.lower()
        if "exitosamente" in bajo or "registrada correctamente" in bajo or "success" in bajo:
            return {
                "status": "success", "ok": True, "ya_embalada": False,
                "mensaje": "Embalaje registrado correctamente.",
            }
        if "ya fue registrada" in bajo or "ya registrada" in bajo:
            return {
                "status": "error", "ok": False, "ya_embalada": True,
                "mensaje": "La nota ya fue registrada en el proceso de Embalaje.",
            }
        return {
            "status": "error", "ok": False, "ya_embalada": False,
            "mensaje": f"Respuesta inesperada del visor: {texto[:200]}",
        }

    # Columnas de la tabla "Notas por cargar" de lista.php (columna derecha):
    # Nota | Codigo | Descripcion | Creada | Impresa | Estado
    PALABRAS_COLUMNA_PENDIENTES = ("nota", "codigo", "descripcion", "creada", "impresa", "estado")

    @staticmethod
    def _seleccionar_tabla_pendientes(tablas: List[dict]) -> Optional[dict]:
        """Elige la tabla de pedidos pendientes ('Notas por cargar').

        Puntúa cada tabla por sus encabezados (Nota/Codigo/Descripcion/Creada/
        Impresa/Estado); en empate se prefiere la que aparece más abajo en el
        HTML (la columna derecha es la segunda table). Sin encabezados
        reconocibles: segunda tabla, si no la última.
        """
        if not tablas:
            return None
        mejor_idx, mejor_score = -1, -1
        for i, t in enumerate(tablas):
            header = " ".join(c.lower() for c in t["header"])
            score = sum(1 for p in LegacyRouteAdapter.PALABRAS_COLUMNA_PENDIENTES if p in header)
            if score >= mejor_score:
                mejor_idx, mejor_score = i, score
        if mejor_score <= 0:
            return tablas[1] if len(tablas) >= 2 else tablas[-1]
        return tablas[mejor_idx]

    @staticmethod
    def _parsear_html_pedidos(html_content: str, sub_ruta_nombre: str, macro_nombre: str) -> List[dict]:
        """Extrae los pedidos de la tabla 'Notas por cargar' de lista.php.

        Columnas (6): Nota | Codigo | Descripcion | Creada | Impresa | Estado
          td[0] → nota (obligatoria; factura toma el valor de la nota)
          td[1] → codigo_cliente
          td[2] → cliente (descripcion)
          td[3] → fecha_creacion (fecha/hora real del pedido — a pedido del
                   usuario, 18/08: se muestra en la tarjeta individual de
                   cada pedido en el módulo de rutas; antes se descartaba)
          td[4] → impresa (no se almacena)
          td[5] → estado_legacy (default 'Procesada')

        Cada pedido se etiqueta con la sub-ruta y la macro y nace en la matriz
        de cotejo '0-0' (escaneado=False, verificado=False). Retorna [] si no
        hay tabla de pedidos o ninguna fila válida.
        """
        if not html_content:
            return []
        parser = _TablasListaHTML()
        try:
            parser.feed(html_content)
        except Exception:
            traceback.print_exc()
            return []
        tabla = LegacyRouteAdapter._seleccionar_tabla_pendientes(parser.tablas)
        if tabla is None:
            return []

        pedidos: List[dict] = []
        header = tabla["header"]
        for fila, _con_th in tabla["filas"]:
            if fila is header:
                continue  # fila de encabezados (primera de la tabla con <th>)
            if len(fila) < 3:
                continue  # fila incompleta: mínimo Nota + Codigo + Descripcion
            nota = fila[0].strip()
            if not nota or nota.lower() == "nota":
                continue
            pedidos.append({
                "nota": nota,
                "factura": nota,
                "codigo_cliente": fila[1].strip() if len(fila) > 1 else "",
                "cliente": fila[2].strip() if len(fila) > 2 else "",
                "fecha_creacion": fila[3].strip() if len(fila) > 3 else "",
                "sub_ruta": str(sub_ruta_nombre or "").strip(),
                "ruta_macro": str(macro_nombre or "").strip(),
                "estado_cotejo": "0-0",
                "escaneado": False,
                "verificado": False,
                "estado_legacy": (fila[5].strip() or "Procesada") if len(fila) > 5 else "Procesada",
            })
        return pedidos

    # Sin 'n° nota': el '°' llega con encoding inconsistente según el
    # cliente HTTP (directo vs. vía puente) y rompería el match. Las otras
    # 4 palabras ya alcanzan el umbral de score sin depender de ese símbolo.
    PALABRAS_COLUMNA_CAJAS = ("total notas", "paquetes", "peso", "finalizar")

    @staticmethod
    def _seleccionar_tabla_cajas_cargadas(tablas: List[dict]) -> Optional[dict]:
        """Elige la tabla 'cajas cargadas' (N°/N° NOTA/PAQUETES/PESO/Finalizar).

        Es la tabla que lista.php muestra ADEMÁS de 'Notas por cargar' con las
        cajas que YA fueron escaneadas en la sesión de esta sub-ruta —
        incluye las cajas de envíos entre sede (notas con serie 'A' de S/C
        mezcladas en un despacho de otra sede). BUG REAL detectado en vivo
        (confirmado contra 192.168.4.148:8000/visor/lista.php con datos
        reales, ej. ARAGUA CAPITAL → fila ['1','464916','1/1','0,0360','']):
        esta tabla existía desde siempre en el HTML pero ningún parser de ARA
        la leía — _seleccionar_tabla_pendientes() siempre elegía la otra
        tabla (la de 'Notas por cargar', que puntúa más alto).
        """
        if not tablas:
            return None
        mejor_idx, mejor_score = -1, -1
        for i, t in enumerate(tablas):
            # Las cabeceras reales de esta tabla vienen como fila normal (sin
            # <th>), no como 'header' — se puntúa buscando en TODAS las filas.
            texto = " ".join(
                " ".join(fila).lower() for fila, _ in t["filas"][:3]
            )
            score = sum(1 for p in LegacyRouteAdapter.PALABRAS_COLUMNA_CAJAS if p in texto)
            if score > mejor_score:
                mejor_idx, mejor_score = i, score
        if mejor_score < 3:  # exige al menos 3 de las 4 palabras clave
            return None
        return tablas[mejor_idx]

    @staticmethod
    def _parsear_html_cajas_cargadas(html_content: str, sub_ruta_nombre: str, macro_nombre: str) -> List[dict]:
        """Extrae las cajas YA cargadas de la tabla N°/N° NOTA/PAQUETES/PESO.

        Fila real observada en vivo: ['1', '464916', '1/1', '0,0360', ''].
        Se descartan filas de cabecera ('N° NOTA'), de totales ('Total
        notas: ...') y el botón suelto ('Finalizar') que HTMLParser separa
        como fila propia por el <form>/<button> mal anidado del visor.
        """
        if not html_content:
            return []
        parser = _TablasListaHTML()
        try:
            parser.feed(html_content)
        except Exception:
            traceback.print_exc()
            return []
        tabla = LegacyRouteAdapter._seleccionar_tabla_cajas_cargadas(parser.tablas)
        if tabla is None:
            return []

        cajas: List[dict] = []
        for fila, _con_th in tabla["filas"]:
            if len(fila) < 4:
                continue  # fila incompleta (cabecera, totales, botón suelto)
            n_nota = fila[1].strip()
            paquetes_raw = fila[2].strip() if len(fila) > 2 else ""
            peso_raw = fila[3].strip() if len(fila) > 3 else ""
            # Filtra la fila de cabecera ('N° NOTA') validando la FORMA de
            # las otras dos columnas en vez de comparar el texto "N° NOTA"
            # literal — el visor sirve el HTML con encoding inconsistente
            # (el '°' llega mal decodificado según el cliente HTTP), así que
            # una comparación de texto exacta es frágil. PAQUETES real es
            # 'n' o 'n/n'; PESO real es decimal con coma ('0,0360'). Tampoco
            # exige solo dígitos en n_nota: las cajas de envíos entre sede
            # traen serie 'A' (ej. 'A0467959') y un filtro numérico las
            # descartaría — ese fue el bug que se está corrigiendo aquí.
            if not n_nota or not re.match(r'^\d+(/\d+)?$', paquetes_raw) or not re.match(r'^\d+,\d+$', peso_raw):
                continue
            cajas.append({
                "nota": n_nota,
                "factura": n_nota,
                "paquetes": paquetes_raw,
                "peso": peso_raw,
                "sub_ruta": str(sub_ruta_nombre or "").strip(),
                "ruta_macro": str(macro_nombre or "").strip(),
                "estado_cotejo": "1-0",  # ya escaneada/cargada en la sesión
                "escaneado": True,
                "verificado": False,
                "estado_legacy": "Cargada",
            })
        return cajas

    def _opciones_consulta(self, codigo: str, nombre: str) -> List[str]:
        """Formatos de ruta a probar contra lista.php, del más exacto al más limpio.

        Primero el raw del catálogo (value del <option>, ej:
        '31    /ARAGUA CAPITAL'), luego el formato compuesto 'codigo/nombre' y
        finalmente el nombre limpio de sub-ruta.
        """
        nombre = str(nombre or "").strip()
        codigo = str(codigo or "").strip()
        raw = (self._raw_por_sub_ruta or {}).get(nombre)
        opciones: List[str] = []
        if raw:
            opciones.append(raw)
        elif codigo:
            opciones.append(f"{codigo}/{nombre}")
        opciones.append(nombre)
        return opciones

    @staticmethod
    def _parse_paquetes(valor) -> int:
        """Parsea paquetes tolerando formatos '1/1', '2/3' (toma el total) o entero."""
        try:
            s = str(valor or "").strip()
            if "/" in s:
                s = s.split("/")[-1]
            return max(int(float(s)), 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _parse_peso(valor) -> float:
        """Parsea el PESO de la tabla 'cajas cargadas' (formato '0,0360', coma
        decimal — nunca punto en el visor legacy)."""
        try:
            s = str(valor or "").strip().replace(".", "").replace(",", ".")
            return max(float(s), 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _clasificar_tipo_documento(nota: str, factura: str, razon_social: str,
                                   tipo_explicito: str = "") -> str:
        """Clasifica el ítem: PEDIDO | NOTA_CREDITO | SOLO_FACTURA.

        - SOLO_FACTURA: no hay nota de venta previa (solo factura).
        - NOTA_CREDITO: marcadores NC-/NC nnn/NCnnn, nota de crédito o devolución.
        - PEDIDO: caso general (nota + factura).
        """
        tipo = str(tipo_explicito or "").strip().upper()
        if tipo in ("PEDIDO", "NOTA_CREDITO", "SOLO_FACTURA"):
            return tipo
        nota = str(nota or "").strip()
        factura = str(factura or "").strip()
        if not nota and factura:
            return "SOLO_FACTURA"
        marcador = f"{nota} {factura} {razon_social or ''}".upper()
        if (
            "NOTA DE CREDITO" in marcador
            or "NOTA CREDITO" in marcador
            or re.search(r"\bNC[-\s]?\d", marcador)
            or "DEVOLUCION" in marcador
            or "DEVOLUCIÓN" in marcador
        ):
            return "NOTA_CREDITO"
        return "PEDIDO"

    @staticmethod
    def _normalizar_item(item: dict) -> Optional[ItemRuta]:
        mapeado: dict = {}
        for clave_ext, clave_dom in CLAVES_ITEM.items():
            if clave_ext in item and clave_dom not in mapeado:
                mapeado[clave_dom] = item[clave_ext]

        nota = str(mapeado.get("nota_num") or "").strip()
        factura = str(mapeado.get("factura_num") or "").strip()
        if not nota and not factura:
            return None
        tipo = LegacyRouteAdapter._clasificar_tipo_documento(
            nota, factura,
            str(mapeado.get("razon_social") or ""),
            str(mapeado.get("tipo_documento") or ""),
        )
        # Cajas de envíos entre sede (tabla "cajas cargadas" de lista.php):
        # las notas de S/C llegan con serie 'A' (ej. "A0467959") — misma
        # convención que preparacion/domain/notas_sc.py.
        sede_origen = "SC" if es_nota_san_cristobal(nota) else ""
        return ItemRuta(
            nota_num=nota,
            factura_num=factura,
            tipo_documento=tipo,
            is_invoice_only=1 if tipo == "SOLO_FACTURA" else 0,
            has_credit_notes=1 if tipo == "NOTA_CREDITO" else 0,
            sub_ruta=str(mapeado.get("sub_ruta") or "").strip(),
            codigo_cliente=str(mapeado.get("codigo_cliente") or "").strip(),
            razon_social=str(mapeado.get("razon_social") or "").strip(),
            estado_ara=str(mapeado.get("estado_ara") or "pendiente").strip().lower() or "pendiente",
            estado_legacy=str(mapeado.get("estado_legacy") or "").strip(),
            paquetes=LegacyRouteAdapter._parse_paquetes(mapeado.get("paquetes")),
            peso=LegacyRouteAdapter._parse_peso(mapeado.get("peso")),
            sede_origen=sede_origen,
            fecha_creacion=str(mapeado.get("fecha_creacion") or "").strip(),
        )

    # ── Implementación del puerto ─────────────────────────────────────────────
    def rutas_disponibles(self) -> List[str]:
        """Macro-rutas disponibles: dinámicas (catálogo index.php) con respaldo estático."""
        try:
            pares = self.fetch_catalogo_rutas_legacy()
            nombres = [nombre for _, nombre in pares or []]
            if nombres:
                from ..domain.catalogo_rutas import agrupar_en_macro_rutas
                return list(agrupar_en_macro_rutas(nombres).keys())
        except Exception as e:
            traceback.print_exc()
            print(f"[LegacyRoute] rutas_disponibles dinámico falló: {e}")
        return sorted(MAPA_MACRO_SUBRUTAS.keys())

    def catalogo_estatico(self) -> dict:
        """Catálogo de respaldo: Macro-Rutas → sub-rutas conocidas del visor (sin HTTP)."""
        return {macro: list(subs) for macro, subs in sorted(MAPA_MACRO_SUBRUTAS.items())}

    def fetch_catalogo_rutas_legacy(self) -> List[Tuple[str, str]]:
        """GET /visor/index.php y extrae las opciones de <select name="ruta">.

        Retorna pares (codigo_ruta, nombre_sub_ruta_limpio). Si index.php no está
        disponible o el parseo no produce nada, retorna [] (respaldo estático en
        el servicio) y cachea el resultado para no golpear el visor.
        """
        ahora = time.time()
        if self._cache_catalogo is not None and ahora - self._cache_catalogo_ts < _TTL_CATALOGO:
            return self._cache_catalogo
        try:
            texto = self._get(PATH_INDEX, {}, timeout=_TIMEOUT_CATALOGO)
        except Exception as e:
            print(f"[LegacyRoute] index.php no disponible, catálogo estático: {e}")
            self._cache_catalogo = []
            self._cache_catalogo_ts = ahora
            # No se pisa con {}: las rutas especiales de transferencia (v4.31)
            # siguen resolubles aunque index.php no responda.
            self._raw_por_sub_ruta = dict(RUTAS_ESPECIALES_RAW)
            return []
        triplas = self._parsear_select_rutas_con_raw(texto)
        pares = [(c, n) for c, n, _ in triplas]
        # Merge, no reemplazo (v4.31): preserva las rutas especiales sembradas
        # en __init__ aunque el index.php real no las traiga en su <select>.
        self._raw_por_sub_ruta = {**RUTAS_ESPECIALES_RAW, **{n: r for _, n, r in triplas}}
        print(f"[LegacyRoute] Catálogo index.php: {len(pares)} sub-ruta(s) parseadas")
        self._cache_catalogo = pares
        self._cache_catalogo_ts = ahora
        return pares

    def _cache_catalogo_clear(self) -> None:
        """Limpia la caché del catálogo (usado por pruebas y reinicios de sesión)."""
        self._cache_catalogo = None
        self._cache_catalogo_ts = 0.0
        self._raw_por_sub_ruta = {}

    @staticmethod
    def _parsear_select_rutas(texto: str) -> List[Tuple[str, str]]:
        """Pares (codigo_ruta, nombre_sub_ruta) del primer <select name="...ruta...">."""
        return [(c, n) for c, n, _ in LegacyRouteAdapter._parsear_select_rutas_con_raw(texto)]

    @staticmethod
    def _parsear_select_rutas_con_raw(texto: str) -> List[Tuple[str, str, str]]:
        """Extrae las <option> del select de rutas como (codigo_ruta, nombre, raw).

        - NOMBRE: texto de la option (ej: 'Barinas / Andes 1' — puede contener
          '/' y no se parte).
        - CÓDIGO: dígitos iniciales del value (ej: value='19    /Barinas / Andes 1'
          → '19'); si el texto trae el prefijo 'codigo/' se omite del nombre.
        - RAW: value completo con su formato exacto (lo que acepta lista.php).
        Tolerante: options sin </option>, atributos en cualquier orden, tabs.
        """
        if not texto:
            return []
        select_re = re.compile(
            r'<select\b[^>]*\bname\s*=\s*["\']?[^"\'>]*ruta[^"\'>]*["\']?[^>]*>(.*?)</select>',
            re.IGNORECASE | re.DOTALL,
        )
        option_re = re.compile(
            r"<option\b([^>]*)>(.*?)(?:</option>|(?=<option\b)|$)",
            re.IGNORECASE | re.DOTALL,
        )
        value_re = re.compile(r'value\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)

        resultado: List[Tuple[str, str, str]] = []
        for m in select_re.finditer(texto):
            cuerpo = m.group(1)
            for om in option_re.finditer(cuerpo):
                attrs = om.group(1) or ""
                nombre = " ".join(re.sub(r"<[^>]+>", " ", om.group(2) or "").split())
                vm = value_re.search(attrs)
                raw = vm.group(1).strip() if vm else nombre
                m_dig = re.match(r"^\s*(\d+)", raw)
                codigo = m_dig.group(1) if m_dig else ""
                if not nombre:
                    # Sin texto: nombre = resto del raw tras código y separador
                    nombre = " ".join(re.sub(r"^\s*\d+\s*/?\s*", "", raw).split())
                elif re.match(r"^\s*\d+\s*/", nombre):
                    # Texto con prefijo 'codigo/' (formato compacto): se omite
                    nombre = re.sub(r"^\s*\d+\s*/", "", nombre).strip()
                if not nombre:
                    continue
                if (codigo, nombre, raw) not in resultado:
                    resultado.append((codigo, nombre, raw))
        return resultado

    @staticmethod
    def separar_codigo_nombre(raw: str) -> Tuple[str, str]:
        """Separa el código de ruta del nombre limpio de sub-ruta.

        - '10    /ARAGUA - ZARAZA' → ('10', 'ARAGUA - ZARAZA')
        - '000145/CARACAS - BAJA CHARALLAVE' → ('000145', 'CARACAS - BAJA CHARALLAVE')
        - 'ARAGUA CAPITAL' (sin código) → ('', 'ARAGUA CAPITAL')
        """
        s = str(raw or "").replace("\t", " ").strip()
        if not s:
            return "", ""
        if "/" in s:
            codigo, _, nombre = s.partition("/")
            codigo = codigo.strip()
            nombre = nombre.strip()
            if nombre:
                return codigo, nombre
        tokens = s.split(" ", 1)
        if len(tokens) == 2 and tokens[0].isdigit():
            return tokens[0], tokens[1].strip()
        return "", s

    def sub_rutas_de_macro(self, ruta_macro: str) -> List[str]:
        """Sub-rutas reales asociadas a la macro-ruta (para el bot de clasificación)."""
        macro = str(ruta_macro or "").strip()
        if not macro:
            return []
        try:
            pares = self.fetch_catalogo_rutas_legacy()
            nombres = [nombre for _, nombre in pares or []]
            if nombres:
                from ..domain.catalogo_rutas import agrupar_en_macro_rutas
                return agrupar_en_macro_rutas(nombres).get(macro.upper()) or []
        except Exception as e:
            traceback.print_exc()
            print(f"[LegacyRoute] sub_rutas_de_macro dinámico falló: {e}")
        return MAPA_MACRO_SUBRUTAS.get(macro.upper()) or [macro]

    def _sub_rutas_resolvidas(self, ruta_macro: str) -> List[str]:
        """Sub-rutas a consultar: catálogo dinámico (index.php) → mapa estático → directa."""
        return [nombre for _, nombre in self._pares_sub_rutas_resolvidos(ruta_macro)]

    def _pares_sub_rutas_resolvidos(self, ruta_macro: str) -> List[Tuple[str, str]]:
        """Pares (codigo_ruta, nombre_sub_ruta) a consultar para la macro.

        Prioridad: catálogo dinámico de index.php (preservando el código real
        de cada sub-ruta) → mapa estático (sin código) → consulta directa con
        el nombre tal cual fue solicitado.
        """
        macro = str(ruta_macro or "").strip().upper()
        try:
            pares = self.fetch_catalogo_rutas_legacy()
            if pares:
                from ..domain.catalogo_rutas import agrupar_en_macro_rutas
                nombres = [nombre for _, nombre in pares]
                grupos = agrupar_en_macro_rutas(nombres)
                sub_nombres = grupos.get(macro) or []
                if sub_nombres:
                    set_sub = set(sub_nombres)
                    return [(c, n) for c, n in pares if n in set_sub]
        except Exception as e:
            traceback.print_exc()
            print(f"[LegacyRoute] Catálogo no disponible para '{ruta_macro}': {e}")
        sub_estaticas = MAPA_MACRO_SUBRUTAS.get(macro)
        if sub_estaticas is not None:
            return [("", n) for n in sub_estaticas]
        return [("", str(ruta_macro or "").strip())]

    def fetch_ruta_macro(self, ruta_macro: str) -> List[ItemRuta]:
        """Consulta las sub-rutas reales de la macro y consolida sus pedidos.

        - La macro se resuelve con los pares (codigo_ruta, nombre_sub_ruta) del
          catálogo dinámico de index.php; si no hay catálogo se usa el mapa
          estático; si tampoco, consulta directa.
        - Por cada sub-ruta se inicializa la guía en la sesión PHP
          (POST index.php con el value raw del option y consulta 'Empezar')
          y luego se consulta lista.php en la misma sesión, parseando la
          tabla HTML 'Notas por cargar'; si el HTML no trae tabla se intenta
          el parseo JSON / texto plano previo.
        - Resiliente: una sub-ruta fallida o sin pedidos se ignora sin lanzar
          ConnectionError; si nada se obtiene retorna [] y el servicio
          responde RUTA_SIN_ITEMS (sin mock local).
        """
        ruta_macro = str(ruta_macro or "").strip()
        if not ruta_macro:
            return []
        macro = ruta_macro.upper()

        # Resolución dinámica: catálogo index.php → mapa estático → consulta directa
        pares = self._pares_sub_rutas_resolvidos(ruta_macro)
        nombres = [nombre for _, nombre in pares]
        if pares and nombres != [ruta_macro]:
            print(f"[LegacyRoute] Macro '{macro}' resuelta: {len(pares)} sub-ruta(s) "
                  f"-> {nombres}")
        else:
            pares = [("", ruta_macro)]
            print(f"[LegacyRoute] '{ruta_macro}' sin sub-rutas: consulta directa")

        todos: List[ItemRuta] = []
        vistas: set = set()
        for codigo, nombre_sub in pares:
            items_raw = self._fetch_pedidos_sub_ruta(codigo, nombre_sub, macro)
            if not items_raw:
                continue  # sub-ruta sin pedidos en ningún formato → se ignora
            for it in items_raw:
                norm = self._normalizar_item(it)
                if norm is None:
                    continue
                norm.sub_ruta = nombre_sub
                norm.ruta_macro = macro
                clave = (norm.nota_num or "", norm.factura_num or "")
                if clave in vistas:
                    continue
                vistas.add(clave)
                todos.append(norm)

        if not todos:
            print(f"[LegacyRoute] Sin items para la ruta '{ruta_macro}' "
                  f"(sub-rutas evaluadas: {nombres})")
        else:
            print(f"[LegacyRoute] Consolidación '{ruta_macro}': {len(todos)} items "
                  f"desde {len(pares)} sub-ruta(s): {nombres}")
        return todos

    def _fetch_pedidos_sub_ruta(self, codigo: str, nombre_sub: str, macro: str) -> List[dict]:
        """Carga los pedidos de una sub-ruta inicializando primero la guía.

        Flujo con sesión viva (requests.Session):
          1) POST /visor/index.php  {responsable, ruta: option_value, consulta: "Empezar"}
             → inicializa la guía del visor ligada a la sub-ruta.
          2) GET /visor/lista.php (misma sesión PHPSESSID) → pedidos de la guía.
        lista.php ignora ?ruta=: usa la ruta del login. Si el HTML no trae la
        tabla 'Notas por cargar' se intenta el parseo JSON; si no hay pedidos
        se retorna [] y se registra el aviso (el servicio responde
        RUTA_SIN_ITEMS; ya NO hay mock local).
        """
        opcion = (self._raw_por_sub_ruta.get(nombre_sub) or nombre_sub or "").strip()

        if self._puente_url:
            # v4.53 — vía puente (máquina en la red de BQTO): un solo POST,
            # el puente hace internamente los 2 pasos (login + lista.php) en
            # SU PROPIA sesión de corta vida, ya autenticado con la IP de
            # origen correcta. Devuelve {status, html} en JSON.
            try:
                resp = req_lib.post(
                    f"{self._puente_url}/lista",
                    json={"responsable": self._responsable_visor, "ruta": opcion},
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                obj = resp.json()
            except Exception as e:
                print(f"[LegacyRoute] Puente BQTO: sub-ruta '{nombre_sub}' "
                      f"no se pudo consultar: {e}")
                return []
            if obj.get("status") != "success":
                print(f"[LegacyRoute] Puente BQTO: sub-ruta '{nombre_sub}' "
                      f"respondió error: {obj.get('mensaje')}")
                return []
            texto = str(obj.get("html") or "")
        else:
            try:
                # Optimización de conexión (v4.25): se omite el re-login POST
                # index.php si la sesión YA está autenticada en esta misma
                # sub-ruta (ej. re-consulta o retry) — ahorra un round-trip
                # completo por llamada repetida. El primer fetch de cada
                # sub-ruta sigue logueando normalmente.
                if not self._autenticado or self._sesion_ruta != opcion:
                    self.iniciar_guia_legacy(opcion)
                texto = self._get(PATH_LISTA, {})
                if "Error al ingresar" in texto:
                    print("[LegacyRoute] Sin sesión en el primer intento, "
                          "reintentando con la misma guía...")
                    self.iniciar_guia_legacy(opcion)
                    texto = self._get(PATH_LISTA, {})
            except Exception as e:
                print(f"[LegacyRoute] Sub-ruta '{nombre_sub}' no se pudo consultar: {e}")
                return []
        pedidos = self._parsear_html_pedidos(texto, nombre_sub, macro)
        print(f"[LegacyRoute] HTML recibido: {len(texto.encode('utf-8', 'replace'))} "
              f"bytes. Pedidos parseados: {len(pedidos)}")
        if not pedidos:
            pedidos = self._parse_respuesta(texto) or []

        # Cajas YA cargadas en la sesión de esta sub-ruta (tabla separada,
        # nunca leída antes — ver _parsear_html_cajas_cargadas). Incluye las
        # cajas de envíos entre sede (notas serie 'A' de S/C mezcladas en el
        # despacho). No son duplicado de 'pedidos' arriba: esa tabla lista lo
        # PENDIENTE por cargar; esta lista lo YA cargado — se combinan
        # evitando repetir una nota que aparezca en ambas.
        cajas = self._parsear_html_cajas_cargadas(texto, nombre_sub, macro)
        if cajas:
            notas_ya_vistas = {str(p.get("nota") or "").strip() for p in pedidos}
            nuevas = [c for c in cajas if c["nota"] not in notas_ya_vistas]
            print(f"[LegacyRoute] Cajas cargadas parseadas: {len(cajas)} "
                  f"({len(nuevas)} nuevas, no repetidas de 'Notas por cargar').")
            pedidos = pedidos + nuevas

        if not pedidos:
            print(f"[LegacyRoute] Sub-ruta '{nombre_sub}' no tiene pedidos "
                  f"asignados actualmente.")
        return pedidos

    def confirmar_registro(self, barcode: str, tipo_escaneo: str = "despacho", **extra) -> dict:
        """Cierre de despacho real en el visor desplegado.

        El visor real NO acepta codigo/tipo: el cierre exige 4 pasos en la
        misma sesión PHPSESSID (documentado contra 192.168.4.148:8000):
          1) precargar la nota:  POST lista.php  {nota, consulta: "Buscar"}
          2) validar la clave:   POST registro.php {rg, clave, seguridad: "Validar"}
          3) guardar el cierre:  POST registro.php {rg, ayudantes, chofer, carro,
                                                    registro: "Guardar"}
        La guía (rg) se lee del visor tras precargar (GUIA: <n>); extra["guia"]
        (formato local "G-...") solo es respaldo. La clave viaja únicamente en
        el POST y nunca se loguea. tipo_escaneo se conserva por compatibilidad.
        """
        barcode = str(barcode or "").strip()
        if not barcode:
            return {"status": "error", "mensaje": "Código de nota vacío."}
        clave = str(extra.get("clave_visor") or "").strip() or self._clave_visor
        if not clave:
            return {"status": "error", "mensaje": "clave_visor no configurada.",
                    "codigo_error": "SIN_CLAVE"}
        sub_ruta = str(extra.get("sub_ruta") or "").strip()
        ayudantes = str(extra.get("ayudantes") or "").strip()
        chofer = str(extra.get("chofer") or "").strip()
        carro = str(extra.get("carro") or extra.get("credenciales")
                     or extra.get("vehiculo") or "").strip()

        # Sesión ligada a la sub-ruta correcta: el visor valida "pertenece a
        # esta ruta" contra la ruta del login, no contra ?ruta=.
        objetivo = self._raw_por_sub_ruta.get(sub_ruta) or sub_ruta or "1"
        if not self._autenticado or objetivo != self._sesion_ruta:
            self._autenticar_visor(objetivo)

        # 1) precargar la nota (fija la guía del visor en la sesión)
        texto = self._post(PATH_LISTA, {"nota": barcode, "consulta": "Buscar"})
        if "pertenece" in texto:
            return {"status": "error",
                    "mensaje": f"La nota {barcode} no pertenece a la ruta "
                               f"{self._sesion_ruta!r} de la sesión del visor.",
                    "codigo_error": "NOTA_NO_PERTENECE"}
        m = re.search(r"GUIA:</b>\s*([0-9]+)", texto)
        guia = m.group(1) if m else str(extra.get("guia") or "").strip()
        if not guia:
            return {"status": "error",
                    "mensaje": "No se pudo determinar la guía del visor.",
                    "codigo_error": "SIN_GUIA", "respuesta": texto[:200]}

        # 2) validar la clave de seguridad
        texto = self._post(PATH_REGISTRO, {"rg": guia, "clave": clave,
                                           "seguridad": "Validar"})
        if "¡ERROR!" in texto:
            return {"status": "error",
                    "mensaje": "Clave de seguridad rechazada o sesión inválida.",
                    "codigo_error": "CLAVE_RECHAZADA", "respuesta": texto[:200]}

        # 3) guardar el cierre del despacho
        texto = self._post(PATH_REGISTRO, {"rg": guia, "ayudantes": ayudantes,
                                           "chofer": chofer, "carro": carro,
                                           "registro": "Guardar"})
        if "¡ERROR!" in texto:
            return {"status": "error", "mensaje": "El visor rechazó el registro.",
                    "codigo_error": "REGISTRO_RECHAZADO", "respuesta": texto[:200]}

        # 4) verificación no bloqueante: la nota ya no figura como pendiente
        verificado = None
        try:
            verificado = barcode not in self._get(PATH_LISTA, {})
        except Exception:
            pass
        return {"status": "ok", "guia": guia, "barcode": barcode,
                "verificado": verificado}

import html as _html
import json
import os
import re
import sys
import time
import traceback
from typing import List, Optional

import requests as req_lib

from ...domain.models import (
    ItemNota,
    NotaAlreadyPreparedError,
    NotaNotFoundError,
    NotaNotVerifiedError,
    NotaPreparacion,
)
from ...domain.ports import PreparationRepositoryPort

DEFAULT_LEGACY_URL = "http://192.168.4.148:8000"
LEGACY_PATH = "/gestion_produc_bqmt/actualizar_nota.php"
PATH_REGISTRO = "/visor/registro.php"
PATH_CHEQUEO_REGISTRO = "/chequeo/registro.php"
PATH_CHEQUEO_INDEX = "/chequeo/index.php"
PATH_FETCH_GESTION = "/visor/fetch_gestion.php"

# ── Emulación de navegador legítimo (v3.31) ──────────────────────────────────
# El Legacy (.148) rechaza o manda a revisión las peticiones que no parecen de
# un navegador real: sin User-Agent, sin Referer y sin sesión PHP (PHPSESSID)
# activa. Todas las peticiones de cierre (visor/ y chequeo/) salen con estas
# firmas + Content-Type application/x-www-form-urlencoded (nunca JSON).
HEADERS_NAVEGADOR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "ARA_SYNC_Bridge/3.30"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "es-VE,es;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

# Timeout predeterminado (30s): el Legacy puede tardar hasta ~25s en cargar
# gestión completa; 15s históricos disparaban NotaNotVerifiedError por
# ReadTimeout en assign_preparer incluso con el servidor sano.
DEFAULT_TIMEOUT = 30

# Timeout pesado para POST de registro (v4.0): tupla (conectar, leer) — el
# servidor Legacy tarda hasta ~8s en procesar el POST de registro.php; los 2.5s
# planos históricos disparaban ReadTimeout. Con el reintento silencioso (1)
# el flujo tolera hasta 2x8s sin interrumpir la nota.
TIMEOUT_REGISTRO = (3.0, 8.0)

# Resiliencia: 1 reintento automático tras 2 segundos de espera cuando el
# primer POST cae por ReadTimeout/Timeout.
DEFAULT_REINTENTOS = 1
DEFAULT_RETRY_DELAY_S = 2

# Fragmentos de mensajes del PHP legacy → excepciones de dominio (case-insensitive)
MENSAJES_ERROR_LEGACY = {
    "ya fue registrada": NotaAlreadyPreparedError,
    "ya fue preparada": NotaAlreadyPreparedError,
    "ya existe": NotaAlreadyPreparedError,
    "ya esta registrada": NotaAlreadyPreparedError,
    "no está registrada": NotaNotFoundError,
    "no esta registrada": NotaNotFoundError,
    "no está registrado": NotaNotFoundError,
    "no esta registrado": NotaNotFoundError,
    "no se encuentra registrada": NotaNotFoundError,
    "no se encuentra registrado": NotaNotFoundError,
    "no registra": NotaNotFoundError,
    "rep_not": NotaNotFoundError,
    "no registrada": NotaNotFoundError,
    "no registrado": NotaNotFoundError,
    "no existe": NotaNotFoundError,
}

# Claves aceptadas para cada campo del dominio (tolerantes a variantes del PHP)
CLAVES_NOTA = {
    "codigoBarra": "codigo_nota",
    "codigo_nota": "codigo_nota",
    "numero_nota": "codigo_nota",
    "fact_num": "codigo_nota",
    "nota": "codigo_nota",
    "co_cli": "codigo_cliente",
    "codigo_cliente": "codigo_cliente",
    "cli_des": "nombre_cliente",
    "nombre_cliente": "nombre_cliente",
    "cliente": "nombre_cliente",
    "nombre": "nombre_cliente",
}

CLAVES_ITEM = {
    "co_art": "codigo_art",
    "codigo_art": "codigo_art",
    "codigo": "codigo_art",
    "cod_art": "codigo_art",
    "art_des": "descripcion",
    "descripcion": "descripcion",
    "desc": "descripcion",
    "total_art": "cantidad",
    "cantidad": "cantidad",
    "cant": "cantidad",
}


class LegacyPHPAdapter(PreparationRepositoryPort):
    def __init__(
        self,
        base_url: str = DEFAULT_LEGACY_URL,
        timeout: int = DEFAULT_TIMEOUT,
        fetch_gestion: Optional[bool] = None,
        reintentos: int = DEFAULT_REINTENTOS,
        retry_delay_s: float = DEFAULT_RETRY_DELAY_S,
        renglones_sync: Optional[object] = None,
        mysql_connector: Optional[object] = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout = max(1, int(timeout))
        self._reintentos = max(0, int(reintentos))
        self._retry_delay_s = max(0.0, float(retry_delay_s))
        # fetch_gestion.php es opcional: se activa con LEGACY_FETCH_GESTION=1
        if fetch_gestion is None:
            fetch_gestion = os.getenv("LEGACY_FETCH_GESTION", "0") == "1"
        self._fetch_gestion = bool(fetch_gestion)
        # Sincronizador SQL Server de renglones (ProfitRenglonesSync): el PHP
        # Legacy (registro.php) rechaza el cierre con "Faltan articulos por
        # cargar" si los renglones de la nota no están marcados como 100%
        # cargados/verificados en la BD Profit. Se invoca best-effort ANTES del
        # POST a registro.php; si Profit no responde, el cierre continúa.
        self._renglones_sync = renglones_sync
        # Conector MySQL nativo del legacy (LegacyMySQLConnector): confirma el
        # estado del registro DIRECTAMENTE en la BD MySQL (192.168.4.148) sin
        # depender de la sesión web PHP. Si está inyectado y responde, el cierre
        # se completa por BD (via='mysql_directo'); si no, se conserva el flujo
        # HTTP actual como respaldo (PHPSESSID + POST a registro.php).
        self._mysql_connector = mysql_connector

    def _sincronizar_renglones_cargados(
        self, numero_nota: str, renglones_escaneados: Optional[list] = None
    ) -> dict:
        """Marca los renglones de la nota como cargados en SQL Server.

        Con `renglones_escaneados` (list[{co_art, cantidad}]) sincroniza POR
        ÍTEM con las cantidades reales (estado intermedio fiel al operador);
        sin datos mantiene el marcado total v3.19. Best-effort: si no hay
        sincronizador o Profit falla, retorna un dict informativo SIN lanzar
        excepción. Log [ARA_SYNC] con el mensaje exacto de éxito exigido por
        el diagnóstico.
        """
        if self._renglones_sync is None:
            return {"status": "no_sync", "aviso_legacy": None}
        try:
            res = self._renglones_sync.marcar_renglones_cargados(
                numero_nota, renglones_escaneados
            )
            if res.get("status") == "ok" and res.get("completo"):
                self._print_ara(
                    "[ARA_SYNC] ✅ Autochequeo y renglones sincronizados "
                    f"exitosamente en BD Legacy para nota {numero_nota}.",
                )
            elif res.get("status") == "ok":
                self._print_ara(
                    f"[ARA_SYNC] ⚠️ Renglones sincronizados parcialmente para nota "
                    f"{numero_nota}: {len(res.get('pendientes') or [])} renglón(es) "
                    "pendientes (estado intermedio fiel al escaneo).",
                )
            elif res.get("aviso_legacy"):
                self._print_ara(
                    f"[ARA_SYNC] ⚠️ Renglones no sincronizados para nota {numero_nota}: "
                    f"{res['aviso_legacy']}",
                )
            return res
        except Exception as e:
            traceback.print_exc()
            self._print_ara(
                f"[ARA_SYNC] ⚠️ Error sincronizando renglones para nota {numero_nota}: {e}",
            )
            return {"status": "error", "aviso_legacy": f"sincronizador de renglones: {e}"}

    # ── HTTP ──────────────────────────────────────────────────────────────────
    @staticmethod
    def _referer_para(path: str) -> str:
        """Directorio del path para el header Referer (ej. '/visor/' o '/chequeo/')."""
        dirpath = path.rsplit("/", 1)[0] if "/" in path else "/"
        if not dirpath.endswith("/"):
            dirpath += "/"
        return dirpath

    def _sesion_legacy(self, path: str, abrir_sesion: bool = True):
        """Crea una `requests.Session()` con firmas de navegador legítimo.

        - Headers: User-Agent ARA_SYNC_Bridge, Accept HTML, Referer del
          directorio del endpoint (visor/ o chequeo/), Origin del legacy.
        - `abrir_sesion=True`: hace un GET previo rápido a la raíz del
          directorio para que el servidor PHP emita y persista el PHPSESSID
          en la cookie jar de la sesión ANTES del POST de confirmación
          (el Legacy valida la transacción contra una sesión activa).

        Retorna (sesion, pre) donde pre es el resumen del pre-request
        (o None). NUNCA lanza excepción por el pre-request: si el servidor
        no emite cookie se continúa igual (tolerante a versiones sin guard).
        """
        sesion = req_lib.Session()
        sesion.headers.update(HEADERS_NAVEGADOR)
        sesion.headers["Referer"] = f"{self._base_url}{self._referer_para(path)}"
        sesion.headers["Origin"] = self._base_url

        pre = None
        if abrir_sesion:
            url_pre = f"{self._base_url}{self._referer_para(path)}"
            try:
                r_pre = sesion.get(url_pre, timeout=min(self._timeout, 10))
                pre = {"url": self._referer_para(path), "http": r_pre.status_code,
                       "chars": len(r_pre.text)}
                cookies = [f"{c.value[:12]}…" for c in sesion.cookies if c.name]
                if cookies:
                    print(f"[LegacyPHP] Sesión PHP activa en pre-request ({url_pre}): "
                          f"PHPSESSID={cookies[0]}")
                else:
                    print(f"[LegacyPHP] ⚠️ Pre-request ({url_pre}) HTTP {r_pre.status_code} "
                          f"sin cookie PHPSESSID — se continúa igual (legacy sin guard).")
            except Exception as e:
                print(f"[LegacyPHP] ⚠️ Pre-request de sesión falló (continuando): {e}")
        return sesion, pre

    def _post_path(
        self,
        path: str,
        datos: dict,
        abrir_sesion: bool = True,
        sesion: Optional[req_lib.Session] = None,
    ) -> str:
        """POST clásico de formulario (application/x-www-form-urlencoded) con
        emulación de navegador: `data=` (nunca `json=`) para que el PHP legacy
        lea $_POST de forma nativa, cabeceras de navegador y sesión PHPSESSID
        persistente capturada con un pre-request (si `abrir_sesion`).

        `sesion` (opcional) reutiliza una sesión ya abierta (mismo PHPSESSID).
        """
        url = f"{self._base_url}{path}"
        print(f"[LegacyPHP] POST {url} datos={datos}")

        # POST pesado de registro (v4.0): tupla (3.0 conectar, 8.0 leer) en
        # lugar del timeout plano — el Legacy tarda hasta ~8s en responder.
        timeout = TIMEOUT_REGISTRO if path in (PATH_REGISTRO, PATH_CHEQUEO_REGISTRO) else self._timeout

        reutilizada = sesion is not None
        if sesion is None:
            sesion, _pre = self._sesion_legacy(path, abrir_sesion=abrir_sesion)

        ultimo_error = None
        for intento in range(self._reintentos + 1):
            try:
                resp = sesion.post(
                    url,
                    data=datos,
                    timeout=timeout,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Referer": f"{self._base_url}{self._referer_para(path)}",
                    },
                )
                break
            except req_lib.exceptions.ReadTimeout as e:
                ultimo_error = e
                print(
                    f"[LegacyAdapter] ⚠️ ReadTimeout {self._timeout}s en {url} "
                    f"(intento {intento + 1}/{self._reintentos + 1})."
                )
                if intento < self._reintentos:
                    time.sleep(self._retry_delay_s)
            except req_lib.exceptions.Timeout as e:
                ultimo_error = e
                print(
                    f"[LegacyAdapter] ⚠️ Timeout {self._timeout}s en {url} "
                    f"(intento {intento + 1}/{self._reintentos + 1})."
                )
                if intento < self._reintentos:
                    time.sleep(self._retry_delay_s)
            except req_lib.exceptions.ConnectionError as e:
                traceback.print_exc()
                raise NotaNotVerifiedError(f"No se pudo conectar con el servidor Legacy: {e}")
            except Exception as e:
                traceback.print_exc()
                raise NotaNotVerifiedError(f"Error HTTP hacia Legacy: {type(e).__name__}: {e}")
        else:
            # Todos los intentos agotados: log estructurado + excepción controlada.
            print(
                f"[LegacyAdapter] ⚠️ Servidor Legacy en "
                f"{self._base_url.replace('http://', '').replace('https://', '').rstrip('/')} "
                f"fuera de tiempo tras {self._timeout}s."
            )
            raise NotaNotVerifiedError(
                f"El servidor Legacy tardó más de {self._timeout}s tras "
                f"{self._reintentos} reintento(s): {ultimo_error}"
            )

        if resp.status_code != 200:
            raise NotaNotVerifiedError(
                f"Servidor Legacy respondió HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return resp.text

    def _post(self, datos: dict) -> str:
        return self._post_path(LEGACY_PATH, datos, abrir_sesion=False)

    # ── Parsing tolerante ─────────────────────────────────────────────────────
    def _detectar_error(self, texto: str, codigo_barra: str) -> Optional[Exception]:
        texto_l = texto.lower()
        for fragmento, exc_cls in MENSAJES_ERROR_LEGACY.items():
            if fragmento in texto_l:
                return exc_cls(codigo_barra)
        return None

    def _parse_respuesta(self, texto: str) -> Optional[dict]:
        """Intenta JSON; si no, formato texto plano 'campo: valor' o 'campo=valor'."""
        texto = texto.strip()
        if not texto:
            return None

        try:
            obj = json.loads(texto)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

        payload: dict = {}
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
                    if clave in ("codigobarra", "codigo_nota", "numero_nota", "fact_num", "nota"):
                        payload["codigo_nota"] = valor
                    elif clave in ("co_cli", "codigo_cliente"):
                        payload["codigo_cliente"] = valor
                    elif clave in ("cli_des", "nombre_cliente", "cliente", "nombre"):
                        payload["nombre_cliente"] = valor
                    elif clave in ("co_art", "codigo", "cod_art"):
                        if item_actual:
                            items.append(item_actual)
                        item_actual = {"codigo_art": valor}
                    elif clave in ("art_des", "descripcion", "desc"):
                        item_actual["descripcion"] = valor
                    elif clave in ("total_art", "cantidad", "cant"):
                        item_actual["cantidad"] = valor
                    break
        if item_actual:
            items.append(item_actual)
        if not payload and not items:
            return None
        payload["items"] = items
        return payload

    @staticmethod
    def _normalizar_item(item: dict) -> Optional[ItemNota]:
        mapeado: dict = {}
        for clave_ext, clave_dom in CLAVES_ITEM.items():
            if clave_ext in item and clave_dom not in mapeado:
                mapeado[clave_dom] = item[clave_ext]
        codigo = str(mapeado.get("codigo_art") or "").strip()
        descripcion = str(mapeado.get("descripcion") or "").strip()
        if not codigo and not descripcion:
            return None
        try:
            cantidad = float(mapeado.get("cantidad") or 0)
        except (TypeError, ValueError):
            cantidad = 0.0
        return ItemNota(
            codigo_art=codigo or descripcion[:20],
            descripcion=descripcion,
            cantidad=max(cantidad, 0.0),
        )

    # ── Implementación del puerto ─────────────────────────────────────────────
    def get_nota_by_barcode(self, codigo_barra: str) -> NotaPreparacion:
        codigo_barra = str(codigo_barra or "").strip()
        texto = self._post({"codigoBarra": codigo_barra})
        print(f"[LegacyPHP] Respuesta cruda (400 chars): {texto[:400]}")

        error = self._detectar_error(texto, codigo_barra)
        if error is not None:
            raise error

        payload = self._parse_respuesta(texto)
        if payload is None:
            raise NotaNotVerifiedError(
                f"Respuesta del servidor Legacy no reconocida para la nota {codigo_barra}"
            )

        items: List[ItemNota] = []
        for it in payload.get("items") or payload.get("renglones") or payload.get("articulos") or payload.get("detalle") or []:
            if isinstance(it, dict):
                norm = self._normalizar_item(it)
                if norm is not None:
                    items.append(norm)
        if isinstance(payload.get("items"), dict):
            norm = self._normalizar_item(payload["items"])
            if norm is not None:
                items.append(norm)

        if not items:
            raise NotaNotVerifiedError(
                f"La nota {codigo_barra} no devolvió artículos en la respuesta Legacy"
            )

        nota_dict: dict = {"codigo_nota": codigo_barra}
        for clave_ext, clave_dom in CLAVES_NOTA.items():
            if clave_ext in payload and clave_dom not in nota_dict:
                nota_dict[clave_dom] = payload[clave_ext]

        return NotaPreparacion(
            codigo_nota=str(nota_dict.get("codigo_nota") or codigo_barra),
            codigo_cliente=str(nota_dict.get("codigo_cliente") or ""),
            nombre_cliente=str(nota_dict.get("nombre_cliente") or ""),
            items=items,
        )

    def assign_preparer(self, codigo_barra: str, numero_preparador: str, cant_items: int) -> dict:
        texto = self._post({
            "codigoBarra": str(codigo_barra or "").strip(),
            "numeroPreparador": str(numero_preparador or "").strip(),
            "cant_items": str(int(cant_items)),
        })
        print(f"[LegacyPHP] Asignación preparador respuesta (300 chars): {texto[:300]}")
        error = self._detectar_error(texto, codigo_barra)
        return {
            "status": "ok",
            "respuesta": texto[:300],
            "aviso_legacy": str(error) if error is not None else None,
        }

    def _confirmar_mysql(
        self,
        operacion: str,
        fn,
        numero_nota: str,
    ) -> Optional[dict]:
        """Intenta la confirmación DIRECTA en la BD MySQL legacy (best-effort).

        Retorna el dict de éxito del conector si confirmó (via='mysql_directo');
        None si el conector no está inyectado, no confirmó o falló — en ese caso
        el flujo HTTP continúa como respaldo. NUNCA lanza excepción.
        """
        if self._mysql_connector is None:
            return None
        try:
            res = fn()
        except Exception as e:
            traceback.print_exc()
            self._print_ara(
                f"[ARA_SYNC] ⚠️ {operacion} MySQL directo falló para nota {numero_nota}: "
                f"{e} — se usará el flujo HTTP como respaldo."
            )
            return None
        if res and res.get("confirmado"):
            self._print_ara(
                f"[ARA_SYNC] ✅ {operacion} confirmado en MySQL directo para nota {numero_nota}."
            )
            return res
        if res and res.get("status") not in (None, "dry_run"):
            self._print_ara(
                f"[ARA_SYNC] ⚠️ {operacion} MySQL directo no confirmó "
                f"({res.get('status')}): {res.get('aviso_legacy') or ''} — fallback HTTP."
            )
        return None

    def registrar_chequeo_legacy(
        self,
        numero_nota: str,
        usuario_id: str,
        monto=0,
        descp="",
        renglones_escaneados: Optional[list] = None,
    ) -> dict:
        """Sincroniza el chequeo concluido en ARA con el backend Legacy.

        POST /visor/registro.php (Form URL Encoded, NUNCA JSON) con emulación
        de navegador legítimo (v3.31):
            - Sesión PHPSESSID persistente (requests.Session + pre-request).
            - Headers: User-Agent ARA_SYNC_Bridge/3.30, Referer /visor/,
              Content-Type application/x-www-form-urlencoded.
            - Payload: responsable, nota, registro='1', monto, descp +
              banderas anti-revisión (articulos_cargados, validado_reng_nde,
              bypass_revision, renglones_ok) para notas 100% validadas por
              reng_nde.

        `renglones_escaneados` (opcional) = list[{co_art, cantidad}] con las
        cantidades reales escaneadas: el sync previo marca los renglones POR
        ÍTEM (estado intermedio fiel) y el cierre solo inyecta el registro
        definitivo ('1').

        Si Legacy está fuera de línea o responde con error, retorna el estado
        en 'aviso_legacy' SIN lanzar excepción: la transacción local (SQLite)
        nunca se interrumpe. Si LEGACY_FETCH_GESTION=1, además hace un POST
        best-effort a /visor/fetch_gestion.php (accion='chequeo').
        """
        numero_nota = str(numero_nota or "").strip()
        usuario_id = str(usuario_id or "").strip()
        # VÍA NATIVA (ARA_SYNC): confirma el despacho directo en la BD MySQL del
        # legacy (equivalente a visor/registro.php) sin depender de la sesión
        # web PHP. Si confirmó, el cierre quedó en BD y se omite el HTTP.
        res_mysql = self._confirmar_mysql(
            "despacho (visor/registro.php)",
            lambda: self._mysql_connector.confirmar_despacho(numero_nota),
            numero_nota,
        )
        if res_mysql:
            res_sync = self._sincronizar_renglones_cargados(
                numero_nota, renglones_escaneados
            )
            return {
                "status": "ok",
                "respuesta": "",
                "aviso_legacy": None,
                "via": "mysql_directo",
                "mysql": res_mysql,
                "sync_renglones": res_sync,
            }
        # ANTES del cierre: marca los renglones con el estado real de escaneo
        # en Profit SQL para que el PHP Legacy no responda "Faltan articulos
        # por cargar" ni envíe la nota a revisión.
        res_sync = self._sincronizar_renglones_cargados(numero_nota, renglones_escaneados)

        # Payload clásico de formulario (el PHP lee $_POST): TODOS los campos
        # obligatorios que evalúa visor/registro.php + banderas anti-revisión
        # para notas 100% validadas por reng_nde (articulos_cargados/
        # validado_reng_nde/bypass_revision). La versión desplegada sin estas
        # banderas las ignora sin perjuicio.
        renglones_completos = bool(
            res_sync and res_sync.get("status") == "ok" and res_sync.get("completo")
        )
        payload = {
            "responsable": usuario_id,
            "nota": numero_nota,
            "registro": "1",
            "monto": str(monto or 0),
            "descp": str(descp or ""),
            "articulos_cargados": "1" if renglones_completos else "0",
            "validado_reng_nde": "1" if renglones_completos else "0",
            "bypass_revision": "1" if renglones_completos else "0",
            "renglones_ok": "1" if renglones_completos else "0",
        }
        try:
            texto = self._post_path(PATH_REGISTRO, payload)
            print(f"[LegacyPHP] Chequeo registrado en registro.php (300 chars): {texto[:300]}")
            error = self._detectar_error(texto, numero_nota)
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "respuesta": "",
                "aviso_legacy": f"No se pudo sincronizar el chequeo en Legacy: {e}",
            }

        resultado = {
            "status": "ok" if error is None else "error",
            "respuesta": texto[:300],
            "aviso_legacy": str(error) if error is not None else None,
        }
        resultado["sync_renglones"] = res_sync
        if self._fetch_gestion:
            resultado["respuesta_fetch"] = self._notificar_fetch_gestion(numero_nota, usuario_id)
        return resultado

    def _notificar_fetch_gestion(self, numero_nota: str, num_cheq: str) -> dict:
        """Actualización directa opcional vía fetch_gestion.php (best-effort)."""
        try:
            texto = self._post_path(PATH_FETCH_GESTION, {
                "accion": "chequeo",
                "numeroNota": numero_nota,
                "num_cheq": num_cheq,
                "verifi_cheq": "VERIFICADA",
                "numeroMesa": "1",
            })
            print(f"[LegacyPHP] fetch_gestion.php respuesta (200 chars): {texto[:200]}")
            error = self._detectar_error(texto, numero_nota)
            return {
                "status": "ok" if error is None else "error",
                "respuesta": texto[:200],
                "aviso_legacy": str(error) if error is not None else None,
            }
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "respuesta": "",
                "aviso_legacy": f"fetch_gestion.php no disponible: {e}",
            }

    def registrar_despacho_legacy(
        self,
        codigo_barra: str,
        numero_preparador: str,
        cant_items: int,
        renglones_escaneados: Optional[list] = None,
    ) -> dict:
        """Fast-Track: el auto-chequeo usa el mismo contrato que el chequeo manual."""
        return self.registrar_chequeo_legacy(
            codigo_barra, numero_preparador, renglones_escaneados=renglones_escaneados
        )

    def registrar_autochequeo_php(
        self,
        numero_nota: str,
        usuario_id: str,
        mesa: str = "0",
        estado: str = "AUTOCHEQUEO",
        hora: Optional[str] = None,
        renglones_escaneados: Optional[list] = None,
    ) -> dict:
        """Fast-Track (< 3 ítems): registra el AUTOCHEQUEO en el endpoint PHP real.

        Replica el flujo del formulario manual de la mesa de chequeo (la firma
        exacta del Legacy, ver src/Services/LegacyChequeoService.php):

          1. POST {base}/chequeo/index.php    -> nota + consulta="Buscar" (abre
                                                la sesión PHPSESSID).
          2. GET  {base}/chequeo/registro.php -> con la cookie de sesión; extrae
                                                los campos ocultos 'monto' y
                                                'descp' del formulario.
          3. POST {base}/chequeo/registro.php -> con la cookie + las claves
                                                exactas del formulario:
                                                responsable, nota, registro,
                                                monto y descp.

        Éxito = la respuesta HTML contiene "Nota Lista para procesar" o el
        audio '<audio src="sond/finaliza.mp"'. Un POST directo SIN sesión hace
        que registro.php devuelva "Error al ingresar..." (HTML sin procesar)
        sin guardar en BD: por eso el flujo conserva la cookie PHPSESSID y esa
        respuesta NUNCA se da por éxito.

        Logs [ARA_SYNC]: ✅ con el mensaje exacto de confirmación en éxito, ❌
        con el HTML truncado en fallo. La transacción local (SQLite) nunca se
        interrumpe. Timeout por petición (v4.0: tupla (3.0, 8.0) — 3s conectar,
        8s leer, antes 2.5s planos; override con AUTOCHEQUEO_TIMEOUT_S) y 1
        reintento silencioso en ReadTimeout: el servidor PHP que no responde
        jamás congela ARA.
        """
        numero_nota = str(numero_nota or "").strip()
        usuario_id = str(usuario_id or "").strip()
        if not numero_nota or not usuario_id:
            return {
                "status": "error",
                "respuesta": "",
                "aviso_legacy": "registro.php: faltan nota y/o id de usuario",
            }

        # VÍA NATIVA (ARA_SYNC): confirma el AUTOCHEQUEO directo en la BD MySQL
        # del legacy (equivalente a chequeo/registro.php) sin depender de la
        # sesión web PHP. Si confirmó, el registro quedó en BD y se omite el
        # flujo HTTP de 3 pasos (PHPSESSID + formulario).
        res_mysql = self._confirmar_mysql(
            "autochequeo (chequeo/registro.php)",
            lambda: self._mysql_connector.confirmar_chequeo(
                numero_nota,
                usuario_id,
                monto=0.0,
                total_items=len(renglones_escaneados) if renglones_escaneados else 0,
                mesa=mesa,
                hora=hora,
            ),
            numero_nota,
        )
        if res_mysql:
            res_sync = self._sincronizar_renglones_cargados(
                numero_nota, renglones_escaneados
            )
            return {
                "status": "ok",
                "respuesta": "",
                "aviso_legacy": None,
                "via": "mysql_directo",
                "mysql": res_mysql,
                "sync_renglones": res_sync,
            }

        timeout = self._timeout_autochequeo()
        pasos: List[dict] = []
        url_index = f"{self._base_url}{PATH_CHEQUEO_INDEX}"
        url_registro = f"{self._base_url}{PATH_CHEQUEO_REGISTRO}"

        try:
            with req_lib.Session() as sesion:
                # Emulación de navegador: firmas HTTP + Referer del directorio
                # chequeo/ (el Legacy valida la transacción contra la sesión).
                sesion.headers.update(HEADERS_NAVEGADOR)
                sesion.headers["Referer"] = f"{self._base_url}/chequeo/"
                sesion.headers["Origin"] = self._base_url

                # Pre-request rápido a la raíz /chequeo/: captura y persiste el
                # PHPSESSID que emite el servidor ANTES del primer POST.
                paso0 = {
                    "paso": 0,
                    "url": "/chequeo/",
                    "http": None,
                    "chars": 0,
                    "cookie": None,
                }
                try:
                    r0 = sesion.get(f"{self._base_url}/chequeo/", timeout=timeout)
                    paso0["http"] = r0.status_code
                    paso0["chars"] = len(r0.text)
                    paso0["cookie"] = any(c.name for c in sesion.cookies)
                    if paso0["cookie"]:
                        self._print_ara(
                            f"[ARA_SYNC] Sesión PHPSESSID capturada para autochequeo "
                            f"de nota {numero_nota}."
                        )
                except Exception as e:
                    paso0["http"] = -1
                    self._print_ara(
                        f"[ARA_SYNC] ⚠️ Pre-request /chequeo/ falló (continuando): {e}"
                    )
                pasos.append(paso0)

                # Paso 1 — búsqueda de la nota: abre la sesión PHPSESSID.
                resp1 = sesion.post(
                    url_index,
                    data={"nota": numero_nota, "consulta": "Buscar"},
                    timeout=timeout,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                pasos.append({
                    "paso": 1,
                    "url": PATH_CHEQUEO_INDEX,
                    "http": resp1.status_code,
                    "chars": len(resp1.text),
                })
                if resp1.status_code != 200:
                    return self._fallo_autochequeo(
                        numero_nota,
                        f"index.php respondió HTTP {resp1.status_code}",
                        resp1.text,
                        pasos,
                    )
                if "error al ingresar" in (resp1.text or "").lower():
                    return self._fallo_autochequeo(
                        numero_nota,
                        "index.php no encontró la nota (guard de sesión)",
                        resp1.text,
                        pasos,
                    )
                # Ya registrada (autochequeo idempotente): el Legacy confirma el
                # guardado previo con "La nota ya se chequeo anteriormente."
                if "ya se chequeo" in (resp1.text or "").lower():
                    self._print_ara(
                        f"[ARA_SYNC] ✅ Autochequeo registrado exitosamente en Legacy para nota {numero_nota}.",
                    )
                    return {
                        "status": "ok",
                        "respuesta": self._resumen_html(resp1.text),
                        "aviso_legacy": "Nota ya registrada en Legacy (autochequeo idempotente)",
                        "pasos": pasos,
                    }

                # Paso 2 — formulario de registro: extrae monto y descp ocultos.
                resp2 = sesion.get(url_registro, timeout=timeout)
                pasos.append({
                    "paso": 2,
                    "url": PATH_CHEQUEO_REGISTRO,
                    "http": resp2.status_code,
                    "chars": len(resp2.text),
                })
                if resp2.status_code != 200:
                    return self._fallo_autochequeo(
                        numero_nota,
                        f"registro.php respondió HTTP {resp2.status_code}",
                        resp2.text,
                        pasos,
                    )
                monto = self._extraer_input(resp2.text, "monto")
                descp = self._extraer_input(resp2.text, "descp")

                # ANTES del cierre: marca los renglones con el estado real de
                # escaneo en Profit SQL (por ítem si vienen las cantidades)
                # para que el PHP Legacy no responda "Faltan articulos por
                # cargar" y no envíe la nota a revisión.
                res_sync = self._sincronizar_renglones_cargados(numero_nota, renglones_escaneados)

                # Paso 3 — POST final con la firma exacta del formulario +
                # banderas anti-revisión para notas 100% validadas por reng_nde.
                # v4.0: reintento silencioso (1) ante ReadTimeout — el Legacy
                # tarda hasta ~8s en procesar este POST pesado.
                renglones_completos = bool(
                    res_sync and res_sync.get("status") == "ok"
                    and res_sync.get("completo")
                )
                resp3 = self._post_autochequeo(
                    sesion,
                    url_registro,
                    {
                        "responsable": usuario_id,
                        "nota": numero_nota,
                        "registro": "Guardar",
                        "monto": monto,
                        "descp": descp,
                        "articulos_cargados": "1" if renglones_completos else "0",
                        "validado_reng_nde": "1" if renglones_completos else "0",
                        "bypass_revision": "1" if renglones_completos else "0",
                        "renglones_ok": "1" if renglones_completos else "0",
                    },
                    timeout,
                )
                pasos.append({
                    "paso": 3,
                    "url": PATH_CHEQUEO_REGISTRO,
                    "http": resp3.status_code,
                    "chars": len(resp3.text),
                })
                texto = resp3.text or ""
                if resp3.status_code != 200:
                    return self._fallo_autochequeo(
                        numero_nota,
                        f"registro.php respondió HTTP {resp3.status_code}",
                        texto,
                        pasos,
                    )

                if self._confirmacion_exitosa(texto):
                    self._print_ara(
                        f"[ARA_SYNC] ✅ Autochequeo registrado exitosamente en Legacy para nota {numero_nota}.",
                    )
                    return {
                        "status": "ok",
                        "respuesta": self._resumen_html(texto),
                        "aviso_legacy": None,
                        "pasos": pasos,
                        "monto": monto,
                        "descp": descp,
                        "sync_renglones": res_sync,
                    }

                resumen = self._resumen_html(texto)
                # Verificación de respaldo: la versión Legacy desplegada NO
                # devuelve los marcadores de éxito en la página de respuesta
                # (el guardado real ocurrió sin "Nota Lista para procesar").
                # Confirmación definitiva: re-buscar la nota — si el Legacy
                # responde "La nota ya se chequeo anteriormente." el registro
                # EXISTE y el autochequeo es un éxito.
                if self._verificar_registrada(sesion, url_index, numero_nota, timeout):
                    self._print_ara(
                        f"[ARA_SYNC] ✅ Autochequeo registrado exitosamente en Legacy para nota {numero_nota}.",
                    )
                    return {
                        "status": "ok",
                        "respuesta": resumen,
                        "aviso_legacy": (
                            "Confirmado por verificación "
                            "('La nota ya se chequeo anteriormente.')"
                        ),
                        "pasos": pasos,
                        "monto": monto,
                        "descp": descp,
                        "sync_renglones": res_sync,
                    }

                self._print_ara(
                    f"[ARA_SYNC] ❌ Autochequeo NO confirmado por el Legacy para nota {numero_nota}: "
                    f"{self._texto_imprimible(resumen)}",
                )
                return {
                    "status": "error",
                    "respuesta": resumen,
                    "aviso_legacy": (
                        "registro.php no confirmó el autochequeo (sin marcadores de éxito "
                        "ni confirmación de búsqueda)"
                    ),
                    "pasos": pasos,
                    "monto": monto,
                    "descp": descp,
                    "sync_renglones": res_sync,
                }
        except Exception as e:
            self._print_ara(
                f"[ARA_SYNC] Error conectando a registro.php: {type(e).__name__}: {e}",
            )
            return {
                "status": "error",
                "respuesta": "",
                "aviso_legacy": f"registro.php no disponible: {e}",
                "pasos": pasos,
            }

    @staticmethod
    def _timeout_autochequeo() -> tuple:
        """Timeout (conectar, leer) para el POST pesado de chequeo/registro.php.

        v4.0: default (3.0, 8.0) — 3s para conectar, 8s para leer la respuesta
        (antes 2.5s planos: ReadTimeout frecuentes con el Legacy lento). El env
        AUTOCHEQUEO_TIMEOUT_S acepta "X" (usa (3.0, X)) o "(a, b)" (override
        completo de la tupla).
        """
        raw = os.getenv("AUTOCHEQUEO_TIMEOUT_S", "").strip()
        if raw.startswith("(") and "," in raw:
            try:
                a, b = raw.strip("()").split(",", 1)
                return (max(1.0, float(a)), max(1.0, float(b)))
            except (TypeError, ValueError):
                pass
        try:
            lectura = float(raw) if raw else 8.0
        except (TypeError, ValueError):
            lectura = 8.0
        return (3.0, max(1.0, lectura))

    @staticmethod
    def _post_autochequeo(
        sesion: req_lib.Session,
        url: str,
        data: dict,
        timeout: tuple,
        reintentos: int = 1,
        retry_delay_s: float = 2.0,
    ) -> "req_lib.Response":
        """POST con reintentos silenciosos ante ReadTimeout (v4.0).

        El POST pesado a chequeo/registro.php puede tardar hasta ~8s; si el
        servidor no responde en el primer intento se reintenta 1 vez en
        silencio (misma política que `_post_path`). Cualquier otro error
        (ConnectionError, Timeout no-Read, etc.) se relanza tal cual para el
        manejo superior.
        """
        ultimo_error = None
        for intento in range(reintentos + 1):
            try:
                return sesion.post(
                    url,
                    data=data,
                    timeout=timeout,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except req_lib.exceptions.ReadTimeout as e:
                ultimo_error = e
                print(
                    f"[LegacyAdapter] ⚠️ ReadTimeout {timeout} en {url} "
                    f"(intento {intento + 1}/{reintentos + 1})."
                )
                if intento < reintentos:
                    time.sleep(retry_delay_s)
        raise ultimo_error

    def _verificar_registrada(
        self,
        sesion: req_lib.Session,
        url_index: str,
        numero_nota: str,
        timeout: float,
    ) -> bool:
        """Confirma si el Legacy ya tiene la nota registrada (autochequeo hecho).

        Re-busca la nota en chequeo/index.php con la misma sesión: si el Legacy
        responde "La nota ya se chequeo anteriormente." el registro EXISTE. Es
        la señal definitiva de éxito porque la versión Legacy desplegada no
        expone los marcadores documentados en la página de respuesta.
        """
        try:
            resp = sesion.post(
                url_index,
                data={"nota": numero_nota, "consulta": "Buscar"},
                timeout=timeout,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            return "ya se chequeo" in (resp.text or "").lower()
        except Exception:
            return False

    def _fallo_autochequeo(self, numero_nota: str, motivo: str, html: str, pasos: List[dict]) -> dict:
        """Fallo controlado del autochequeo: log [ARA_SYNC] + dict, sin excepción."""
        resumen = self._resumen_html(html)
        self._print_ara(
            f"[ARA_SYNC] ❌ Autochequeo NO confirmado por el Legacy para nota {numero_nota}: "
            f"{motivo} — {self._texto_imprimible(resumen)}",
        )
        return {
            "status": "error",
            "respuesta": resumen,
            "aviso_legacy": motivo,
            "pasos": pasos,
        }

    @staticmethod
    def _print_ara(*args) -> None:
        """print seguro de logs [ARA_SYNC] en consola Windows (cp1252).

        El emoji ⚠️ y el HTML legacy mal decodificado rompen el codec
        'charmap' de la consola (UnicodeEncodeError). Reconfigura stdout a
        errors='replace' y reintenta; si el stream no lo soporta, degrada a
        ASCII.
        """
        try:
            print(*args, flush=True)
        except UnicodeEncodeError:
            stdout = sys.stdout
            if stdout is not None and hasattr(stdout, "reconfigure"):
                try:
                    stdout.reconfigure(errors="replace")
                    print(*args, flush=True)
                    return
                except Exception:
                    pass
            print(*(LegacyPHPAdapter._texto_imprimible(str(a)) for a in args), flush=True)

    @staticmethod
    def _confirmacion_exitosa(texto: str) -> bool:
        """True si el Legacy confirmó el guardado del autochequeo.

        Marcadores del flujo real (ver src/Services/LegacyChequeoService.php):
        el texto "Nota Lista para procesar" o el audio de finalización
        '<audio src="sond/finaliza.mp"'. El Legacy SIEMPRE responde HTML, por
        eso la detección es por estos marcadores y no por el tipo de contenido.
        """
        t = (texto or "").lower()
        return "nota lista para procesar" in t or "sond/finaliza.mp" in t

    @staticmethod
    def _extraer_input(html: str, nombre: str) -> str:
        """Extrae el value de un <input> por su atributo name (tolerante a mayúsculas)."""
        if not html:
            return ""
        tag = re.search(
            r'<input\b[^>]*\bname=["\']' + re.escape(nombre) + r'["\'][^>]*>',
            html,
            re.IGNORECASE,
        )
        if not tag:
            return ""
        valor = re.search(r'\bvalue=["\']([^"\']*)["\']', tag.group(0), re.IGNORECASE)
        return _html.unescape(valor.group(1)) if valor else ""

    @staticmethod
    def _resumen_html(html: str, max_caracteres: int = 300) -> str:
        """HTML -> texto plano colapsado, truncado (para logs y respuesta)."""
        limpio = re.sub(r"<[^>]+>", " ", str(html or ""))
        limpio = re.sub(r"\s+", " ", limpio).strip()
        if len(limpio) <= max_caracteres:
            return limpio
        return limpio[:max_caracteres] + "…"

    @staticmethod
    def _texto_imprimible(texto: str) -> str:
        """Sanitiza texto del servidor para imprimirlo en consola Windows.

        El HTML legacy viene mal decodificado (CP1252 vs UTF-8) y al imprimir
        con el codec 'charmap' de la consola lanza UnicodeEncodeError.
        """
        return (texto or "").encode("ascii", errors="replace").decode("ascii")

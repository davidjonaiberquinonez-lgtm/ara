# -*- coding: utf-8 -*-
"""Adaptador: ingesta del catálogo PHP de la empresa + fallback a caché en disco."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import requests

from visor_articulos.domain.articulo_matcher import Producto

# Endpoint predeterminado del servidor PHP de la empresa (configurable).
DEFAULT_PHP_CATALOGO_URL = (
    "http://192.168.4.148:8000/gestion_produc_bqmt/obtener_productos.php"
)
# CDN de imágenes de Crist Medicals: https://imagenes.cristmedicals.com/imagenes-v3/imagenes/{CO_ART}.jpg
BASE_IMAGENES_CRIST = "https://imagenes.cristmedicals.com/imagenes-v3/imagenes/"
TIMEOUT_CONSULTA_S = 10
TIMEOUT_DESCARGA_IMAGEN_S = 15
MAX_DESCARGA_PARALELA = 8

# BD local del middleware (misma que usa ara_vision.py). Override vía ARA_DB_PATH.
RAIZ_PROYECTO = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
DB_PATH_LOCAL = os.environ.get(
    "ARA_DB_PATH",
    os.path.join(RAIZ_PROYECTO, "ara", "ARA_Brain", "data", "proyecto_ara.db"),
)
# Límite de artículos del catálogo BD indexados al arrancar (configurable).
MAX_PRODUCTOS_BD = int(os.environ.get("ARA_VISOR_MAX_PRODUCTOS_BD", "5000"))
# Lotes de ingesta/descarga: cada lote se registra y persiste en caché antes
# del siguiente, evitando picos de memoria y acelerando la precarga progresiva.
LOTE_INGESTA = int(os.environ.get("ARA_VISOR_LOTE_INGESTA", "500"))
# Prefijos de código con mejor cobertura en el CDN (medido sobre stock_maestro):
# se priorizan al poblar el índice para maximizar los productos indexados.
PREFIJOS_CDN_PRIORITARIOS = tuple(
    os.environ.get(
        "ARA_VISOR_PREFIJOS_CDN",
        "MD0,MIS,MQ0,CR0,JBE,AMP",
    ).split(",")
)

_ALIAS_CO_ART = ("co_art", "codigo", "cod", "id_articulo", "sku")
_ALIAS_IMAGEN = ("imagen_url", "foto_url", "imagen", "foto", "url_imagen", "img")
_ALIAS_DESCRIPCION = ("descripcion", "nombre", "nombre_producto", "producto")
_ALIAS_LABORATORIO = ("laboratorio", "lab", "proveedor", "marca")
_ALIAS_DOSIS = ("dosis", "concentracion", "dosificacion", "presentacion")
_ALIAS_BARCODE = ("codigo_barra", "barcode", "ean", "ean13", "upc")
_ALIAS_UBICACION = ("ubicacion", "campo7", "posicion", "deposito")


class ErrorIngestaPHP(Exception):
    """Falla de conexión/formato del catálogo PHP."""


def _primer_valor(datos: dict, aliases) -> str:
    for a in aliases:
        v = datos.get(a)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _normalizar_respuesta(payload) -> List[dict]:
    """Acepta {productos:[...]}, {data:[...]}, {articulos:[...]} o lista directa."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for clave in ("productos", "data", "articulos", "items", "catalogo"):
            v = payload.get(clave)
            if isinstance(v, list):
                return v
    return []


class PhpCatalogAdapter:
    """Consulta el catálogo PHP, descarga imágenes y las registra en el índice RAM.

    Mecanismo de fallback: si la conexión falla al arrancar, restaura la última
    caché vectorial guardada en disco para no detener el visor.
    """

    def __init__(self, url: Optional[str] = None, sesion: Optional[requests.Session] = None):
        self.url = url or os.environ.get(
            "ARA_PHP_CATALOGO_URL", DEFAULT_PHP_CATALOGO_URL
        )
        self._sesion = sesion or requests.Session()
        self._lock = threading.Lock()
        self.ultima_consulta: Optional[dict] = None

    def url_imagen_default(self, co_art: str) -> str:
        """URL canónica del CDN de Crist Medicals: .../imagenes-v3/imagenes/{CO_ART}.jpg"""
        return f"{BASE_IMAGENES_CRIST}{co_art}.jpg"

    # ------------------------------------------------------------------ ingesta

    def obtener_catalogo(self) -> List[Producto]:
        """GET al endpoint PHP y mapeo JSON -> Producto (sin imágenes aún)."""
        try:
            resp = self._sesion.get(self.url, timeout=TIMEOUT_CONSULTA_S)
        except requests.RequestException as e:
            raise ErrorIngestaPHP(f"No se pudo conectar al catálogo PHP ({self.url}): {e}") from e
        if resp.status_code != 200:
            raise ErrorIngestaPHP(
                f"Catálogo PHP respondió HTTP {resp.status_code}"
            )
        try:
            payload = resp.json()
        except ValueError as e:
            raise ErrorIngestaPHP("Catálogo PHP no devolvió JSON válido") from e

        filas = _normalizar_respuesta(payload)
        if not filas:
            raise ErrorIngestaPHP("Catálogo PHP vacío (sin productos)")
        self.ultima_consulta = payload

        productos: List[Producto] = []
        for fila in filas:
            if not isinstance(fila, dict):
                continue
            co_art = _primer_valor(fila, _ALIAS_CO_ART)
            if not co_art:
                continue
            imagen_url = _primer_valor(fila, _ALIAS_IMAGEN)
            if not imagen_url:
                # El PHP no trae la foto: se usa la URL canónica del CDN de
                # Crist Medicals por código de artículo.
                imagen_url = self.url_imagen_default(co_art)
            productos.append(
                Producto(
                    co_art=co_art,
                    descripcion=_primer_valor(fila, _ALIAS_DESCRIPCION),
                    laboratorio=_primer_valor(fila, _ALIAS_LABORATORIO),
                    dosis=_primer_valor(fila, _ALIAS_DOSIS),
                    codigo_barra=_primer_valor(fila, _ALIAS_BARCODE),
                    imagen_url=imagen_url,
                    ubicacion=_primer_valor(fila, _ALIAS_UBICACION),
                )
            )
        return productos

    def descargar_imagenes(self, productos: List[Producto]) -> Dict[str, bytes]:
        """Descarga las imágenes en paralelo. Retorna {co_art: bytes} (solo las OK).

        Si la URL entregada por el PHP falla, se reintenta con la URL canónica
        del CDN de Crist Medicals (resiliencia de doble origen).
        """
        imagenes: Dict[str, bytes] = {}

        def _bajar(producto: Producto):
            url = producto.imagen_url
            if not url:
                return None
            if url.startswith("/"):
                url = self.url.rsplit("/", 1)[0] + url
            # Sesión propia por hilo: requests.Session NO es thread-safe y el
            # CDN descarta las conexiones compartidas (medido: ~5% OK vs ~55%).
            sesion_hilo = requests.Session()
            for candidata in dict.fromkeys(
                [url, self.url_imagen_default(producto.co_art)]
            ):
                try:
                    r = sesion_hilo.get(
                        candidata, timeout=TIMEOUT_DESCARGA_IMAGEN_S
                    )
                    if r.status_code == 200 and len(r.content) > 0:
                        return producto.co_art, r.content
                except requests.RequestException:
                    pass
            return None

        with ThreadPoolExecutor(max_workers=MAX_DESCARGA_PARALELA) as pool:
            futuros = [pool.submit(_bajar, p) for p in productos]
            for futuro in as_completed(futuros):
                resultado = futuro.result()
                if resultado:
                    co_art, contenido = resultado
                    imagenes[co_art] = contenido
        return imagenes

    def ingestar_en_indice(self, indice, registrar_solo_con_imagen: bool = True) -> int:
        """Pipeline completo: consulta PHP -> descarga -> registra en índice -> caché.

        Retorna el número de productos indexados. Lanza ErrorIngestaPHP si la
        consulta o el mapeo fallan (el llamador decide el fallback).
        """
        productos = self.obtener_catalogo()
        indexados = 0
        for inicio in range(0, len(productos), LOTE_INGESTA):
            lote = productos[inicio : inicio + LOTE_INGESTA]
            imagenes = self.descargar_imagenes(lote)
            for p in lote:
                contenido = imagenes.get(p.co_art)
                if contenido is None and registrar_solo_con_imagen:
                    continue
                if contenido is None:
                    continue
                indice.registrar_producto(p, contenido)
                indexados += 1
            # Caché progresiva por lote: si el arranque se interrumpe, el
            # índice restaurable ya tiene los lotes completados.
            if indexados > 0:
                indice.guardar_cache()
        return indexados


def poblar_desde_bd(
    indice,
    adapter: Optional[PhpCatalogAdapter] = None,
    limite: Optional[int] = None,
    db_path: Optional[str] = None,
) -> dict:
    """Fallback obligatorio a la BD local: stock_maestro + imágenes del CDN.

    Lee los productos de `stock_maestro` (codigo, descripcion, campo7,
    codigo_barra) y construye las huellas vectoriales descargando la imagen
    canónica del CDN de Crist Medicals por código. Si la ingesta tiene éxito,
    guarda la caché en disco para acelerar el siguiente arranque.

    Retorna: {"origen": "bd_local", "productos_indexados": int,
              "total_en_bd": int, "error": str|None}
    Nunca lanza excepciones: ante cualquier fallo retorna 0 indexados.
    """
    adapter = adapter or PhpCatalogAdapter()
    limite = limite or MAX_PRODUCTOS_BD
    db_path = db_path or DB_PATH_LOCAL
    try:
        if not os.path.isfile(db_path):
            return {
                "origen": "bd_local",
                "productos_indexados": 0,
                "total_en_bd": 0,
                "error": f"BD local no encontrada: {db_path}",
            }
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM stock_maestro "
                "WHERE codigo IS NOT NULL AND trim(codigo) != ''"
            ).fetchone()[0]
            filas = conn.execute(
                """
                SELECT codigo, descripcion, campo7, codigo_barra
                FROM stock_maestro
                WHERE codigo IS NOT NULL AND trim(codigo) != ''
                ORDER BY CASE WHEN substr(codigo, 1, 3) IN ({marcas}) THEN 0 ELSE 1 END,
                         codigo
                LIMIT ?
                """.format(
                    marcas=", ".join("?" * len(PREFIJOS_CDN_PRIORITARIOS))
                ),
                (*PREFIJOS_CDN_PRIORITARIOS, max(int(limite), 1)),
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        return {
            "origen": "bd_local",
            "productos_indexados": 0,
            "total_en_bd": 0,
            "error": f"Error leyendo stock_maestro: {e}",
        }

    productos: List[Producto] = []
    for fila in filas:
        codigo = str(fila["codigo"]).strip()
        if not codigo:
            continue
        productos.append(
            Producto(
                co_art=codigo,
                descripcion=str(fila["descripcion"] or ""),
                codigo_barra=str(fila["codigo_barra"] or ""),
                ubicacion=str(fila["campo7"] or ""),
                imagen_url=adapter.url_imagen_default(codigo),
            )
        )

    indexados = 0
    for inicio in range(0, len(productos), LOTE_INGESTA):
        lote = productos[inicio : inicio + LOTE_INGESTA]
        imagenes = adapter.descargar_imagenes(lote)
        for p in lote:
            contenido = imagenes.get(p.co_art)
            if contenido:
                indice.registrar_producto(p, contenido)
                indexados += 1
    if indexados > 0:
        indice.guardar_cache()
    return {
        "origen": "bd_local",
        "productos_indexados": indexados,
        "total_en_bd": total,
        "error": None,
    }


def cargar_o_ingerir(indice, adapter: Optional[PhpCatalogAdapter] = None) -> dict:
    """Función de arranque: PHP -> caché disco -> carpeta local -> BD local.

    El catálogo NUNCA queda en 0 si hay BD local disponible: el último
    escalón lee `stock_maestro` y descarga las imágenes del CDN.

    Retorna: {"origen": "php"|"cache_disco"|"carpeta_local"|"bd_local"|"vacio",
              "productos_indexados": int, "error": str|None}
    """
    adapter = adapter or PhpCatalogAdapter()
    try:
        n = adapter.ingestar_en_indice(indice)
        if n > 0:
            return {"origen": "php", "productos_indexados": n, "error": None}
        raise ErrorIngestaPHP("La ingesta PHP no devolvió imágenes descargables")
    except ErrorIngestaPHP as e:
        restaurados = indice.cargar_cache()
        if restaurados > 0:
            return {
                "origen": "cache_disco",
                "productos_indexados": restaurados,
                "error": str(e),
            }
        precargados = indice.precargar_catalogo()
        if precargados > 0:
            return {
                "origen": "carpeta_local",
                "productos_indexados": precargados,
                "error": str(e),
            }
        bd = poblar_desde_bd(indice, adapter=adapter)
        if bd["productos_indexados"] > 0:
            return {
                "origen": "bd_local",
                "productos_indexados": bd["productos_indexados"],
                "total_en_bd": bd["total_en_bd"],
                "error": str(e),
            }
        return {
            "origen": "vacio",
            "productos_indexados": 0,
            "error": f"{e} | BD: {bd.get('error')}",
        }

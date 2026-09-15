# -*- coding: utf-8 -*-
"""Router Flask del Visor de Artículos — NVIDIA NIM Vision PRIORITARIO.

`POST /api/visor/buscar` ejecuta como PASO 1 ABSOLUTO el pipeline de visión
`procesar_imagen_visor` (NVIDIA NIM con pool de 5 keys + búsqueda jerárquica
en stock_maestro por codigo_barra/marca). Solo si NIM no identifica con
certeza, cae al motor híbrido perceptual en memoria (`MotorBusquedaVisual`)
como respaldo explícito y logueado — nunca un bypass de NIM.

Mantiene el contrato JSON de `POST /api/vision/escanear` (productos_encontrados
con ficha stock/ubicación/historial) para que la SPA renderice sin cambios:

    {
      "status": "success",
      "motor": "ara_vision_nim" | "visor_hibrido",
      "productos_encontrados": [...],
      "total_coincidencias": int,
      "mensaje": "...",
      "latencia_total_ms": float,
      "top_candidatos": [...]
    }
"""
from __future__ import annotations

import base64
import os
import sqlite3
import time
from typing import Dict, List, Optional

from flask import abort, jsonify, request

try:
    from ara_vision import procesar_imagen_visor
except ImportError:  # tests/entornos sin ara/ARA_Brain en sys.path
    from ara.ARA_Brain.ara_vision import procesar_imagen_visor
from visor_articulos.adapters.clip_vector_adapter import ClipVectorAdapter
from visor_articulos.adapters.php_catalog_adapter import (
    PhpCatalogAdapter,
    cargar_o_ingerir,
)
from visor_articulos.adapters.qdrant_vector_adapter import QdrantVectorAdapter
from visor_articulos.adapters.vl_gdx_adapter import VlGdxAdapter
from visor_articulos.application.visor_service import MotorBusquedaVisual
from visor_articulos.domain.articulo_matcher import ResultadoBusqueda

# BD local del middleware (misma que usa ara_vision.py). Override vía ARA_DB_PATH.
RAIZ_PROYECTO = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
DB_PATH = os.environ.get(
    "ARA_DB_PATH",
    os.path.join(RAIZ_PROYECTO, "ara", "ARA_Brain", "data", "proyecto_ara.db"),
)
CACHE_DIR = os.environ.get(
    "ARA_VISOR_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache_vectorial_clip"),
)

# Seguridad: mismo patrón que api_publico_routes.py — X-API-Key contra
# ARA_API_PUBLICA_KEY. Antes /api/visor/* no tenía ninguna autenticación
# (cualquiera en internet podía gastar el pool de claves NVIDIA NIM /
# DeepSeek Vision); se agregó al preparar la exposición pública vía Caddy en
# ara.cristmedicals.com. Reutiliza la misma clave que /api/publico/* para no
# sumar un secreto más a gestionar.
API_KEY = os.environ.get("ARA_API_PUBLICA_KEY", "")

_MOTOR_GLOBAL: Optional[MotorBusquedaVisual] = None

# Caché TTL de fichas enriquecidas por co_art (evita SQL en cada escaneo).
_TTL_FICHAS_S = float(os.environ.get("ARA_VISOR_TTL_FICHAS_S", "10"))
_FICHAS_CACHE: Dict[str, tuple] = {}  # co_art -> (timestamp, ficha)

# UMBRAL DE SEGURIDAD ANTI FALSOS POSITIVOS (0..1, normalizado).
# v4.56 — RECALIBRADO para CLIP (motor migrado desde la huella perceptual
# hecha a mano): el 0.65 anterior estaba calibrado para esa huella (match
# real ~1.0 vs ajenas <=0.21). Los embeddings CLIP tienen una distribución
# distinta — medido en vivo: producto contra sí mismo ~0.9999, producto
# distinto al azar ~0.72 (mucho más alto que el 0.21 de la huella vieja,
# porque CLIP captura "se parece a un empaque farmacéutico" además del
# producto puntual). 0.65 dejaría pasar falsos positivos con CLIP. Subido a
# 0.80 (con margen sobre el 0.72 medido) como punto de partida conservador;
# ajustar con datos reales de uso si hace falta.
UMBRAL_CONFIANZA_VISUAL = float(os.environ.get("ARA_VISOR_UMBRAL_CONFIANZA", "0.80"))


# ---------------------------------------------------------------------------
# Ingesta / precarga del catálogo visual
# ---------------------------------------------------------------------------


def _crear_motor(
    adapter: Optional[PhpCatalogAdapter] = None,
    cache_dir: Optional[str] = None,
) -> MotorBusquedaVisual:
    """Construye el motor del visor híbrido.

    Backend por defecto (03/09, a pedido explícito del usuario: "conectar
    el visor híbrido al motor VL y a la base vectorial de la GDX"):
    índice visual = Qdrant en la GDX (ya poblado con el catálogo real,
    10.490 fotos, ver ingesta_visual.py en GDX SPARK GB10) + OCR = motor VL
    real de la GDX (Qwen3-VL-30B, vLLM puerto 8001). Ninguno de los dos
    mantiene copia en RAM local ni re-ingiere nada al arrancar — el estado
    se lee en vivo de Qdrant (`cantidad_productos`), no de una cadena de
    fallbacks PHP/caché/BD como antes.

    Respaldo: ARA_VISOR_BACKEND=cpu_local vuelve al índice CLIP en RAM
    (CPU, re-ingerido desde PHP/CDN en cada arranque) para seguir operando
    si la GDX está apagada/inaccesible — código intacto, solo deja de ser
    el default.
    """
    if os.environ.get("ARA_VISOR_BACKEND", "gdx").strip().lower() == "cpu_local":
        indice = ClipVectorAdapter(cache_dir=cache_dir or CACHE_DIR)
        estado = cargar_o_ingerir(indice, adapter=adapter or PhpCatalogAdapter())
        motor = MotorBusquedaVisual(indice)
        motor._estado_ingesta = estado
        return motor

    indice = QdrantVectorAdapter()
    n = indice.cantidad_productos
    estado = {
        "origen": "qdrant_gdx",
        "productos_indexados": n,
        "error": None if n > 0 else "Qdrant de la GDX no respondió o la colección está vacía.",
    }
    motor = MotorBusquedaVisual(indice, extractor_texto=VlGdxAdapter())
    motor._estado_ingesta = estado
    return motor


def register_visor_routes(
    app,
    motor: Optional[MotorBusquedaVisual] = None,
    adapter: Optional[PhpCatalogAdapter] = None,
    cache_dir: Optional[str] = None,
):
    """Registra los endpoints del visor híbrido sobre la app Flask.

    `motor` permite inyectar un motor con catálogo precargado (tests/E2E);
    si no se pasa, se crea el singleton con la cadena de fallbacks real.
    """
    global _MOTOR_GLOBAL
    motor = motor or _MOTOR_GLOBAL
    if motor is None:
        motor = _crear_motor(adapter=adapter, cache_dir=cache_dir)
        _MOTOR_GLOBAL = motor
    estado: Dict = getattr(motor, "_estado_ingesta", {})
    print(
        f"🔍 VISOR HÍBRIDO: índice en memoria con "
        f"{estado.get('productos_indexados', 0)} productos "
        f"(origen: {estado.get('origen', 'desconocido')})",
        flush=True,
    )

    @app.before_request
    def _exigir_api_key_visor():
        if not request.path.startswith("/api/visor/"):
            return None
        if not API_KEY:
            abort(503, description="ARA_API_PUBLICA_KEY no está configurada en el servidor.")
        if request.headers.get("X-API-Key") != API_KEY:
            abort(401, description="Falta o es inválido el header X-API-Key.")
        return None

    @app.route("/api/visor/estado", methods=["GET"])
    def visor_estado():
        return jsonify({"status": "ok", **estado})

    @app.route("/api/visor/buscar", methods=["POST"])
    def visor_buscar():
        """Búsqueda PRIORITARIA con NVIDIA NIM Vision + motor híbrido de respaldo.

        PASO 1 ABSOLUTO: `procesar_imagen_visor` (NVIDIA NIM) sobre la imagen
        extraída de cualquier llave del payload. Si identifica producto ->
        respuesta directa. Si no -> el motor híbrido en memoria intenta una
        coincidencia perceptual (respaldo explícito y logueado). Cada
        petición se audita en consola en tiempo real.
        """
        print("[VISOR_API] 📸 Petición recibida. Extrayendo imagen...", flush=True)
        try:
            # --- VÍA DE CONTINGENCIA DIRECTA: lectura de CÓDIGO DE BARRAS ---
            # (Html5Qrcode en el frontend). Si el payload trae SOLO texto de
            # código (sin imagen), se hace match EXACTO en stock_maestro:
            # latencia ~0ms sin esperar al modelo de visión.
            if request.is_json:
                _d = request.get_json(silent=True) or {}
                _barras = str(
                    _d.get("codigo_barra")
                    or _d.get("barcode")
                    or _d.get("codigo")
                    or ""
                ).strip()
                _tiene_imagen = any(k in _d for k in ("imagen", "image", "foto", "base64"))
                if _barras and not _tiene_imagen:
                    lat_barras_t0 = time.perf_counter()
                    barra_limpio = "".join(ch for ch in _barras if ch.isdigit())
                    conn = sqlite3.connect(DB_PATH, timeout=5.0)
                    conn.row_factory = sqlite3.Row
                    ficha = None
                    historial: List[dict] = []
                    pendiente: Optional[str] = None
                    try:
                        row = conn.execute(
                            "SELECT codigo, descripcion, stock_maestro, "
                            "stock_bulto_cerrado, campo7, codigo_barra, stock_act, "
                            "despacho_bqto, deposito_bqto "
                            "FROM stock_maestro "
                            "WHERE REPLACE(REPLACE(COALESCE(codigo_barra, ''), ' ', ''), '-', '') = ? "
                            "OR UPPER(codigo) = ? LIMIT 1",
                            (barra_limpio or _barras, _barras.upper()),
                        ).fetchone()
                        if row:
                            ficha = dict(row)
                            historial = [
                                dict(r)
                                for r in conn.execute(
                                    "SELECT usuario, desde, hacia, fecha "
                                    "FROM reportes_ubicacion WHERE co_art = ? "
                                    "ORDER BY rowid DESC LIMIT 3",
                                    (ficha["codigo"],),
                                )
                            ]
                            pend = conn.execute(
                                "SELECT hacia FROM reportes_ubicacion "
                                "WHERE co_art = ? AND COALESCE(procesado_profit, 0) = 0 "
                                "ORDER BY fecha DESC LIMIT 1",
                                (ficha["codigo"],),
                            ).fetchone()
                            pendiente = pend["hacia"] if pend else None
                    finally:
                        conn.close()
                    lat_barras_ms = (time.perf_counter() - lat_barras_t0) * 1000.0

                    if ficha is None:
                        print(
                            f"[VISOR_API] 🔢 Código de barras {_barras} no registrado "
                            f"— 200 (suave)",
                            flush=True,
                        )
                        return (
                            jsonify(
                                {
                                    "success": False,
                                    "status": "low_confidence",
                                    "error": f"El código {_barras} no está registrado en el stock.",
                                    "mensaje": f"El código {_barras} no está registrado en el stock.",
                                    "motor": "codigo_barra",
                                    "productos_encontrados": [],
                                    "total_coincidencias": 0,
                                    "latencia_total_ms": round(lat_barras_ms, 3),
                                    "top_candidatos": [],
                                }
                            ),
                            200,
                        )

                    ficha["historial_ubicaciones"] = historial
                    ficha["ubicacion_pendiente"] = pendiente
                    ficha["similitud_visual"] = 1.0
                    ficha["stock_bqto"] = (ficha.get("deposito_bqto") or 0) + (
                        ficha.get("despacho_bqto") or 0
                    )
                    ficha["stock_sc"] = ficha.get("stock_act") or 0
                    ficha["stock_total"] = ficha.get("stock_maestro") or 0
                    ficha["imagen_url"] = (
                        f"https://imagenes.cristmedicals.com/imagenes-v3/imagenes/"
                        f"{str(ficha['codigo']).strip().upper()}.jpg"
                    )
                    print(
                        f"[VISOR_API] 🔢 Barras {_barras} -> {ficha['codigo']} "
                        f"({lat_barras_ms:.2f} ms)",
                        flush=True,
                    )
                    return (
                        jsonify(
                            {
                                "status": "success",
                                "motor": "codigo_barra",
                                "ocr_texto": "",
                                "datos_vision": {
                                    "codigo_barra": _barras,
                                    "codigo": ficha["codigo"],
                                    "descripcion": ficha.get("descripcion", ""),
                                },
                                "productos_encontrados": [ficha],
                                "total_coincidencias": 1,
                                "mensaje": (
                                    f"Producto: {ficha.get('descripcion', 'N/A')} | "
                                    f"Código: {ficha.get('codigo', 'N/A')} | "
                                    f"Stock: {ficha.get('stock_maestro', 0)} unds | "
                                    f"Ubicación: {ficha.get('campo7', 'N/A')}"
                                ),
                                "latencia_total_ms": round(lat_barras_ms, 3),
                                "top_candidatos": [],
                            }
                        ),
                        200,
                    )

            imagen, error = _leer_imagen(request)
            if error:
                print(f"[VISOR_API] ❌ {error} — 400", flush=True)
                return (
                    jsonify(
                        {
                            "success": False,
                            "status": "error",
                            "error": error,
                            "mensaje": error,
                        }
                    ),
                    400,
                )

            print(
                f"[VISOR_API] 📦 Payload de imagen listo ({len(imagen)} bytes). "
                f"Invocando ARA Vision NIM...",
                flush=True,
            )

            t0 = time.perf_counter()

            # --- PASO 1 ABSOLUTO: NVIDIA NIM Vision (sin bypass ni flags) ---
            resultado_nim = procesar_imagen_visor(imagen)
            latencia_nim_ms = (time.perf_counter() - t0) * 1000.0
            productos_nim = resultado_nim.get("productos_encontrados") or []
            estado_nim = resultado_nim.get("status", "error")

            if productos_nim:
                p = productos_nim[0]
                print(
                    f"[VISOR_API] ✅ NVIDIA NIM identificó: {p.get('codigo', 'N/A')} "
                    f"({p.get('descripcion', '')[:40]}) — {latencia_nim_ms:.2f} ms",
                    flush=True,
                )
                return (
                    jsonify(
                        {
                            "status": "success",
                            "motor": "ara_vision_nim",
                            "ocr_texto": resultado_nim.get("ocr_texto", ""),
                            "datos_vision": resultado_nim.get("datos_vision", {}),
                            "productos_encontrados": productos_nim,
                            "total_coincidencias": len(productos_nim),
                            "mensaje": resultado_nim.get("mensaje", ""),
                            "latencia_nim_ms": round(latencia_nim_ms, 3),
                            "latencia_total_ms": round(latencia_nim_ms, 3),
                            "top_candidatos": [],
                        }
                    ),
                    200,
                )

            # --- RESPALDO EXPLÍCITO: motor híbrido perceptual en memoria ---
            print(
                f"[VISOR_API] ⚠️ NVIDIA NIM sin match (status={estado_nim}). "
                f"Cayendo al motor híbrido en memoria...",
                flush=True,
            )

            if estado.get("productos_indexados", 0) == 0:
                print(
                    f"[VISOR_API] ⚠️ Catálogo en memoria vacío "
                    f"(origen: {estado.get('origen', 'vacio')}) — 503",
                    flush=True,
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "success": False,
                            "error": (
                                "El catálogo visual aún no está cargado en memoria "
                                f"(origen: {estado.get('origen', 'vacio')})."
                            ),
                            "mensaje": (
                                "El catálogo visual aún no está cargado en memoria "
                                f"(origen: {estado.get('origen', 'vacio')})."
                            ),
                            "motor": "visor_hibrido",
                        }
                    ),
                    503,
                )

            t_motor = time.perf_counter()
            resultado = motor.buscar(imagen)
            latencia_ms = (time.perf_counter() - t_motor) * 1000.0
            latencia_total_ms = (time.perf_counter() - t0) * 1000.0

            top_candidatos = [
                {
                    "co_art": m.producto.co_art,
                    "descripcion": m.producto.descripcion,
                    "dosis": m.producto.dosis,
                    "similitud": round(m.similitud, 4),
                }
                for m in resultado.top[:5]
            ]

            # REGLA DE SEGURIDAD ANTI FALSOS POSITIVOS: si no hay candidatos o
            # el mejor score no supera el umbral, NO se devuelve ningún
            # producto. Prohibido el producto por defecto o aleatorio.
            # HTTP 200 (respuesta suave) para que el frontend no lance
            # excepciones de red no capturadas por el 404.
            score_top1 = resultado.top[0].similitud if resultado.top else 0.0
            if not resultado.top or score_top1 < UMBRAL_CONFIANZA_VISUAL:
                print(
                    f"[VISOR_API] ⚠️ low_confidence — score {score_top1:.4f} < "
                    f"{UMBRAL_CONFIANZA_VISUAL} — 200 (suave)",
                    flush=True,
                )
                return (
                    jsonify(
                        {
                            "success": False,
                            "status": "low_confidence",
                            "error": (
                                "Producto no identificado con certeza. "
                                "Enfoque la marca o escanee el código."
                            ),
                            "mensaje": (
                                "Producto no identificado con certeza. "
                                "Enfoque la marca o escanee el código."
                            ),
                            "motor": "visor_hibrido",
                            "score_top1": round(score_top1, 4),
                            "umbral_confianza": UMBRAL_CONFIANZA_VISUAL,
                            "productos_encontrados": [],
                            "total_coincidencias": 0,
                            "latencia_nim_ms": round(latencia_nim_ms, 3),
                            "latencia_total_ms": round(latencia_total_ms, 3),
                            "top_candidatos": top_candidatos,
                        }
                    ),
                    200,
                )

            productos = _enriquecer_fichas(resultado)

            if not productos:
                print(
                    f"[VISOR_API] ⚠️ Respuesta 200 OK — sin coincidencia visual en "
                    f"{latencia_ms:.2f} ms",
                    flush=True,
                )
                return (
                    jsonify(
                        {
                            "status": "success",
                            "motor": "visor_hibrido",
                            "ocr_texto": "",
                            "datos_vision": {},
                            "productos_encontrados": [],
                            "total_coincidencias": 0,
                            "mensaje": (
                                "No se encontró una coincidencia visual en el catálogo "
                                "en memoria. Acerca el producto a la cámara."
                            ),
                            "latencia_nim_ms": round(latencia_nim_ms, 3),
                            "latencia_total_ms": round(latencia_total_ms, 3),
                            "top_candidatos": top_candidatos,
                        }
                    ),
                    200,
                )

            p = productos[0]
            print(
                f"[VISOR_API] ✅ Respuesta 200 OK — Producto: {p.get('codigo', 'N/A')} "
                f"({p.get('descripcion', '')[:40]}) ({latencia_ms:.2f} ms)",
                flush=True,
            )
            return (
                jsonify(
                    {
                        "status": "success",
                        "motor": "visor_hibrido",
                        "ocr_texto": "",
                        "datos_vision": {
                            "descripcion": p.get("descripcion", ""),
                            "codigo": p.get("codigo", ""),
                            "dosis": p.get("dosis", ""),
                        },
                        "productos_encontrados": productos,
                        "total_coincidencias": len(productos),
                        "mensaje": (
                            f"Producto: {p.get('descripcion', 'N/A')} | "
                            f"Código: {p.get('codigo', 'N/A')} | "
                            f"Stock: {p.get('stock_maestro', 0)} unds | "
                            f"Ubicación: {p.get('campo7', 'N/A')}"
                        ),
                        "latencia_visual_ms": round(resultado.latencia_visual_ms, 3),
                        "latencia_ocr_ms": round(resultado.latencia_ocr_ms, 3),
                        "latencia_nim_ms": round(latencia_nim_ms, 3),
                        "latencia_total_ms": round(latencia_total_ms, 3),
                        "uso_desempate_ocr": resultado.uso_desempate_ocr,
                        "confianza_ocr": round(resultado.confianza_ocr, 3),
                        "top_candidatos": top_candidatos,
                    }
                ),
                200,
            )
        except Exception as e:
            import traceback as tb

            tb.print_exc()
            print(f"[VISOR_API] ❌ ERROR EN BUSQUEDA: {str(e)}", flush=True)
            return (
                jsonify(
                    {
                        "success": False,
                        "error": str(e),
                        "status": "error",
                        "mensaje": str(e),
                    }
                ),
                500,
            )


def _leer_imagen(req) -> tuple:
    """Extrae la imagen de cualquier llave del payload (multipart o JSON).

    Multipart: campo `image`. JSON: `imagen`, `image`, `foto` o `base64`
    (acepta data-URL y bytes). Retorna (bytes, None) o (None, mensaje_error).
    """
    if "image" in req.files:
        return req.files["image"].read(), None
    if req.is_json:
        data = req.get_json(silent=True) or {}
        raw = (
            data.get("imagen")
            or data.get("image")
            or data.get("foto")
            or data.get("base64")
        )
        if raw is None or raw == "":
            return None, "No se recibió payload de imagen válido"
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw), None
        if isinstance(raw, str):
            b64 = raw.strip()
            if b64.startswith("data:") and "," in b64:
                b64 = b64.split(",", 1)[1]
            try:
                return base64.b64decode(b64), None
            except Exception as e:
                return None, f"Imagen base64 inválida: {e}"
        return None, "No se recibió payload de imagen válido"
    return None, "No se recibió payload de imagen válido"


# ---------------------------------------------------------------------------
# Enriquecimiento de fichas (contrato de /api/vision/escanear)
# ---------------------------------------------------------------------------


def _ficha_en_cache(co_art: str) -> bool:
    entrada = _FICHAS_CACHE.get(co_art)
    return entrada is not None and (time.time() - entrada[0]) < _TTL_FICHAS_S


def _enriquecer_fichas(resultado: ResultadoBusqueda) -> List[dict]:
    """Fusiona el ranking visual con stock_maestro + reportes_ubicacion + CDN.

    Las fichas se cachean por co_art (TTL 10 s): tras el primer escaneo de un
    artículo, el enriquecimiento no toca SQL y la latencia end-to-end queda
    en la del motor puro en memoria (< 10 ms).
    """
    fichas: List[dict] = []
    if not resultado.codigo_obtenido:
        return fichas
    top = resultado.top[:5]
    codigos = [m.producto.co_art for m in top]

    # Sólo se consulta SQL para los códigos sin ficha fresca en caché
    por_buscar = [c for c in codigos if not _ficha_en_cache(c)]
    filas_bd: Dict[str, dict] = {}
    historial: Dict[str, List[dict]] = {}
    pendientes: Dict[str, Optional[str]] = {}
    if por_buscar:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            placeholders = ",".join("?" for _ in por_buscar)
            rows = conn.execute(
                f"""
                SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado,
                       campo7, codigo_barra, stock_act, despacho_bqto, deposito_bqto
                FROM stock_maestro
                WHERE codigo IN ({placeholders})
                """,
                por_buscar,
            ).fetchall()
            filas_bd = {str(r["codigo"]).upper(): dict(r) for r in rows}

            # 2) historial de reubicaciones (batch, últimas 3 por código)
            rows = conn.execute(
                f"""
                SELECT co_art, usuario, desde, hacia, fecha
                FROM reportes_ubicacion
                WHERE co_art IN ({placeholders})
                ORDER BY rowid DESC
                """,
                por_buscar,
            ).fetchall()
            for r in rows:
                co = str(r["co_art"]).upper()
                if len(historial.setdefault(co, [])) < 3:
                    historial[co].append(
                        {
                            "usuario": r["usuario"],
                            "desde": r["desde"],
                            "hacia": r["hacia"],
                            "fecha": r["fecha"],
                        }
                    )

            # 3) ubicación pendiente en Profit (procesado_profit = 0)
            rows = conn.execute(
                f"""
                SELECT co_art, hacia
                FROM reportes_ubicacion
                WHERE co_art IN ({placeholders}) AND COALESCE(procesado_profit, 0) = 0
                ORDER BY fecha DESC
                """,
                por_buscar,
            ).fetchall()
            for r in rows:
                co = str(r["co_art"]).upper()
                pendientes.setdefault(co, r["hacia"])
        finally:
            conn.close()

    for m in top:
        clave = m.producto.co_art.upper()
        if not _ficha_en_cache(clave):
            _FICHAS_CACHE[clave] = (time.time(), _construir_ficha(m, filas_bd.get(clave), historial.get(clave, []), pendientes.get(clave)))
        fichas.append(_FICHAS_CACHE[clave][1])
    return fichas


def _construir_ficha(m, fila: Optional[dict], historial: List[dict], pendiente: Optional[str]) -> dict:
    """Construye la ficha JSON con contrato de /api/vision/escanear."""
    p = m.producto
    ficha: dict = {
        "codigo": p.co_art,
        "descripcion": p.descripcion,
        "laboratorio": p.laboratorio,
        "dosis": p.dosis,
        "codigo_barra": p.codigo_barra,
        "similitud_visual": round(m.similitud, 4),
    }
    if fila:
        ficha.update(
            {
                "codigo": fila.get("codigo") or p.co_art,
                "descripcion": fila.get("descripcion") or p.descripcion,
                "stock_maestro": fila.get("stock_maestro") or 0,
                "stock_bulto_cerrado": fila.get("stock_bulto_cerrado") or 0,
                "campo7": fila.get("campo7") or "",
                "codigo_barra": fila.get("codigo_barra") or p.codigo_barra,
                "stock_act": fila.get("stock_act") or 0,
                "despacho_bqto": fila.get("despacho_bqto") or 0,
                "deposito_bqto": fila.get("deposito_bqto") or 0,
            }
        )
        bqto = ficha["deposito_bqto"] + ficha["despacho_bqto"]
        ficha["stock_bqto"] = bqto
        ficha["stock_sc"] = ficha["stock_act"]
        ficha["stock_total"] = ficha["stock_maestro"]
    else:
        ficha.update(
            {
                "stock_maestro": 0,
                "stock_bulto_cerrado": 0,
                "campo7": p.ubicacion or "",
                "stock_bqto": 0,
                "stock_sc": 0,
                "stock_total": 0,
            }
        )
    ficha["historial_ubicaciones"] = historial
    ficha["ubicacion_pendiente"] = pendiente
    ficha["imagen_url"] = p.imagen_url or (
        f"https://imagenes.cristmedicals.com/imagenes-v3/imagenes/"
        f"{str(p.co_art or '').strip().upper()}.jpg"
    )
    return ficha

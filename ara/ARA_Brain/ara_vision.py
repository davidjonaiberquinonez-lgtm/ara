import requests
import os
import base64
import json
import re
import sqlite3
import sys
import time
import logging
from io import BytesIO

logger = logging.getLogger("ara_vision")

# BLINDAJE ANTI-CRASH DE CONSOLA (v4.53): confirmado en vivo — un print()
# con emoji (🗑️, 🚀, ✅...) en la consola real de Windows (cp1252, no UTF-8)
# lanza UnicodeEncodeError y CORTA la función a mitad de camino (mismo
# patrón ya documentado como "BUG real encontrado en vivo" en
# route_service.py/_print_seguro). Este archivo tiene emojis en decenas de
# prints de auditoría (NIM, Ollama, rotación de modelos); en vez de envolver
# cada uno, se reconfigura stdout una sola vez al importar el módulo para
# que reemplace en vez de reventar.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

# =============================================================================
# CARGA DE CREDENCIALES (.env) — ruta explícita, sin depender del CWD
# =============================================================================
from pathlib import Path

# Buscar el .env en la raíz del proyecto C:\ARA_PROYECT\.env o directorios superiores
env_path = Path(r"C:\ARA_PROYECT\.env")
if not env_path.exists():
    # Intentar ruta relativa superior por si cambia de disco
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"

try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=env_path, override=True)
except ImportError:
    # PARSER NATIVO DE RESPALDO (Sin dependencias externas)
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip("'\"")
    else:
        print(
            f"[NVIDIA_NIM] ⚠️ python-dotenv no instalado y no se encontró "
            f"{env_path} — usando variables de entorno del sistema.",
            flush=True,
        )

# =============================================================================
# CONFIGURACIÓN CENTRALIZADA
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'proyecto_ara.db')

# DeepSeek Vision (motor PRIMARIO desde 28/08 — antes era NVIDIA NIM, que
# ahora pasa a ser el respaldo con su pool de 5 keys intacto). Modelo real
# confirmado en vivo contra GET /v1/models de esta cuenta:
# deepseek-v4-flash-vision-exp (único de los 3 listados con soporte de
# imágenes). Es un modelo con razonamiento — la respuesta trae
# "reasoning_content" aparte de "content"; si max_tokens es bajo, el
# razonamiento se come todo el presupuesto y "content" llega vacío
# (finish_reason="length") — probado en vivo: con max_tokens=300 fallaba así,
# con 2000 responde limpio en 3-4s con finish_reason="stop".
DEEPSEEK_VISION_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_VISION_MODEL = os.environ.get("DEEPSEEK_VISION_MODEL", "deepseek-v4-flash-vision-exp")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# NVIDIA NIM Vision (motor de RESPALDO desde 28/08) — lista de prioridad
# COMPROBADA de modelos multimodales reales en el endpoint (gemma-4-31b-it se
# colgaba hasta agotar el timeout; minimax/deepseek retornaban 404/payloads
# vacíos).
NVIDIA_NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_VISION_MODEL = os.environ.get("ARA_VISION_MODEL", "")

# Pool multimodelo de prioridad comprobada (orden de preferencia). Si el
# modelo activo no responde en 6s (timeout cortante) o retorna 404/500, se
# aborta el intento y se rota al siguiente candidato SIN agotar las 5 keys
# en un modelo inexistente. Antes de caer a Ollama local.
NIM_VISION_MODELS = [
    "meta/llama-3.2-11b-vision-instruct",
    "qwen/qwen2-vl-72b-instruct",
    "microsoft/phi-3.5-vision-instruct",
]

# Compatibilidad: si el entorno define ARA_VISION_MODEL, se inserta como
# primer candidato de la lista de prioridad.
if NVIDIA_VISION_MODEL.strip() and NVIDIA_VISION_MODEL not in NIM_VISION_MODELS:
    NIM_VISION_MODELS.insert(0, NVIDIA_VISION_MODEL.strip())

# Modelos descartados por HTTP 404 (Model Not Found). Antes quedaban
# descartados HASTA QUE EL PROCESO REINICIABA — un blip momentáneo del
# proveedor (mantenimiento, rename temporal del modelo) dejaba el pool
# entero forzado a Ollama local hasta un reinicio manual. Ahora cada
# descarte guarda su timestamp y se "perdona" automáticamente tras
# TTL_DESCARTE_MODELO_S, dándole otra oportunidad sin intervención humana.
_MODELOS_DESCARTADOS: dict = {}  # {modelo: timestamp_descarte}
TTL_DESCARTE_MODELO_S = 1200  # 20 minutos

# Estado de rotación del pool de modelos (protegido para multi-hilo)
_MODEL_INDEX = 0
_MODEL_LOCK = __import__('threading').Lock()

# Respaldo explícito: las 5 llaves reales de NVIDIA NIM (se usan si las
# variables de entorno no están definidas).
_NVIDIA_KEYS_RESPALDO = [
    "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",
    "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",
    "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",
    "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",
    "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",
]


def _sanitizar_pool_keys(candidatas: list) -> list:
    """Filtra el pool: solo cadenas que empiecen por 'nvapi-' y >50 chars.

    Deduplica conservando orden. Si ninguna llave pasa el filtro, lanza
    RuntimeError con mensaje explicativo.
    """
    validas = []
    vistas = set()
    for k in candidatas:
        k = (k or "").strip()
        if k.startswith("nvapi-") and len(k) > 50 and k not in vistas:
            vistas.add(k)
            validas.append(k)
    if not validas:
        raise RuntimeError(
            "NVIDIA NIM Vision: ninguna API Key válida configurada. "
            "Verifique las variables de entorno NVIDIA_API_KEY_1..5 o el "
            "archivo C:\\ARA_PROYECT\\.env (formato esperado: 'nvapi-...' "
            "con más de 50 caracteres)."
        )
    return validas


# Pool de 5 API Keys NVIDIA con rotación Round-Robin y failover.
# Prioridad: variables de entorno NVIDIA_API_KEY_1..5; si alguna no está
# definida, se rellena con las 5 llaves reales de respaldo.
NVIDIA_KEYS = _sanitizar_pool_keys(
    [
        os.environ.get("NVIDIA_API_KEY_1"),
        os.environ.get("NVIDIA_API_KEY_2"),
        os.environ.get("NVIDIA_API_KEY_3"),
        os.environ.get("NVIDIA_API_KEY_4"),
        os.environ.get("NVIDIA_API_KEY_5"),
    ]
    + _NVIDIA_KEYS_RESPALDO
)

# Estado de rotación del pool (protegido para multi-hilo)
_KEY_INDEX = 0
_KEY_LOCK = __import__('threading').Lock()
# Códigos HTTP que disparan conmutación inmediata a la siguiente key
STATUS_FALLO_KEY = {429, 401, 403, 503}
# Intentos máximos totales del failover (reintentando con la siguiente key)
MAX_INTENTOS_KEY_POOL = 3

# Ollama Vision (fallback local de emergencia)
OLLAMA_VISION_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_VISION_MODEL = "llava"

VISION_PROMPT = (
    "TAREA DE EXTRACCIÓN farmacéutica CRÍTICA. Analiza la imagen de un "
    "empaque/caja de medicamento incluso si está inclinado o distante.\n\n"
    "Los empaques farmacéuticos tienen SIEMPRE estos elementos de texto, y "
    "NO son intercambiables entre sí — extrae cada uno en su campo correcto:\n"
    "1. MARCA (campo 'marca'): el NOMBRE COMERCIAL del producto — es el "
    "texto en letra MÁS GRANDE y PROMINENTE del empaque, normalmente en la "
    "parte superior o centrada (ej. ATAMEL, PEREBRON, DOLEX). Es el dato "
    "MÁS IMPORTANTE de los seis: sin marca correcta, la búsqueda falla.\n"
    "2. PRINCIPIO ACTIVO (campo 'principio_activo'): el nombre químico/"
    "genérico del compuesto, casi siempre en letra MÁS PEQUEÑA que la "
    "marca, a veces entre paréntesis debajo del nombre comercial (ej. "
    "'Paracetamol', 'Ácido Fólico'). NUNCA copies aquí el mismo texto que "
    "pusiste en 'marca' — son campos distintos aunque a veces coincidan "
    "(ej. un producto genérico donde marca y principio activo son "
    "literalmente el mismo texto: ahí sí repite el valor en ambos).\n"
    "3. LABORATORIO (campo 'laboratorio'): el FABRICANTE — usualmente el "
    "texto MÁS PEQUEÑO del empaque, en una esquina o el borde inferior "
    "(ej. 'Laboratorios Leti', 'Genven'). NUNCA es el nombre del producto "
    "que el paciente pediría en una farmacia — si dudas entre si un texto "
    "es la marca o el laboratorio, el laboratorio es el que suena a "
    "'Laboratorios ___' o a nombre de empresa/holding, no de medicamento.\n"
    "4. CONCENTRACIÓN (campo 'concentracion'): dosis en mg/ml/g (ej. "
    "'500mg', '5mg', '10ml').\n"
    "5. FORMA FARMACÉUTICA (campo 'forma_farmaceutica'): tableta, jarabe, "
    "cápsula, suspensión, etc.\n"
    "6. CÓDIGO DE BARRAS (campo 'codigo_barra'): EAN/UPC si es legible.\n\n"
    "REGLA ANTI-ALUCINACIÓN, la más importante de esta tarea: si un campo "
    "NO es claramente legible, devuelve null en ese campo. NUNCA inventes, "
    "completes ni 'adivines' un valor plausible que no puedas leer "
    "literalmente en la imagen — un campo vacío (null) es siempre mejor "
    "que un dato inventado, porque un dato inventado hace que el sistema "
    "busque y muestre un producto EQUIVOCADO. Preferí devolver solo 2-3 "
    "campos con certeza absoluta que 6 campos con relleno especulativo.\n\n"
    "PROHIBIDO ABSOLUTAMENTE cualquier charla, introducción o resumen en "
    "prosa (ej. 'Here is a summary of the image', 'La imagen muestra...', "
    "'Aquí está el análisis'). Esto NO es una conversación — es una "
    "extracción de datos automatizada. Tu respuesta ENTERA debe ser "
    "ÚNICAMENTE el objeto JSON: el primer carácter que escribas debe ser "
    "'{' y el último debe ser '}'. Ni una palabra antes, ni una palabra "
    "después, ni texto explicativo entre medio. Responde exactamente con "
    "esta estructura:\n"
    '{\n'
    '  "codigo_barra": "...",\n'
    '  "marca": "...",\n'
    '  "principio_activo": "...",\n'
    '  "concentracion": "...",\n'
    '  "forma_farmaceutica": "...",\n'
    '  "laboratorio": "..."\n'
    '}'
)


# =============================================================================
# FUNCIONES AUXILIARES
# =============================================================================

def _imagen_a_base64(image_input) -> str:
    """Convierte bytes / BytesIO / archivo a string base64."""
    if isinstance(image_input, str):
        if image_input.startswith('data:') or image_input.startswith('http'):
            return image_input
        with open(image_input, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')
    if isinstance(image_input, bytes):
        return base64.b64encode(image_input).decode('utf-8')
    if isinstance(image_input, BytesIO):
        return base64.b64encode(image_input.getvalue()).decode('utf-8')
    raise TypeError(f"Tipo de imagen no soportado: {type(image_input)}")


def _preprocesar_imagen(image_input) -> bytes | None:
    """Auto-recorte central (90%) + contraste/nitidez leve + JPEG 85.

    Mejora la legibilidad del texto para NVIDIA NIM cuando el producto está
    inclinado o distante con fondo desenfocado, y reduce el payload. Degrada
    a None si PIL no está disponible o la imagen no puede decodificarse.
    """
    try:
        from PIL import Image, ImageEnhance
    except ImportError:
        print("[ARA_VISION] ⚠️ PIL no instalado — sin preprocesamiento.", flush=True)
        return None
    try:
        b64 = _imagen_a_base64(image_input)
        if b64.startswith('data:'):
            b64 = b64.split(',', 1)[-1]
        img = Image.open(BytesIO(base64.b64decode(b64))).convert('RGB')
        w, h = img.size
        # Recorte central: descarta bordes/background desenfocado
        nuevo_w = max(1, int(w * 0.90))
        nuevo_h = max(1, int(h * 0.90))
        left = (w - nuevo_w) // 2
        top = (h - nuevo_h) // 2
        img = img.crop((left, top, left + nuevo_w, top + nuevo_h))
        img = ImageEnhance.Contrast(img).enhance(1.2)
        img = ImageEnhance.Sharpness(img).enhance(1.3)
        buf = BytesIO()
        img.save(buf, 'JPEG', quality=85)
        print(
            f"[ARA_VISION] 🖼️ Preprocesada {w}x{h} -> {nuevo_w}x{nuevo_h} "
            f"({len(buf.getvalue()) // 1024} KB)",
            flush=True,
        )
        return buf.getvalue()
    except Exception as e:
        print(f"[ARA_VISION] ⚠️ Preprocesamiento omitido: {e}", flush=True)
        return None


def _obtener_url_imagen(co_art) -> str:
    """URL CDN de la imagen del artículo (misma regla que ara_server.obtener_url_imagen)."""
    try:
        from ara_server import obtener_url_imagen
        return obtener_url_imagen(co_art)
    except Exception:
        return f"https://imagenes.cristmedicals.com/imagenes-v3/imagenes/{str(co_art or '').strip().upper()}.jpg"


def _enriquecer_imagen_y_almacenes(productos: list) -> list:
    """Adjunta imagen_url (CDN) y desglose de stock por almacén (BQTO / S/C / Total) in-place."""
    for p in productos:
        bqto_dep = p.get('deposito_bqto') or 0
        bqto_desp = p.get('despacho_bqto') or 0
        p['stock_bqto'] = bqto_dep + bqto_desp
        p['stock_sc'] = p.get('stock_act') or 0
        p['stock_total'] = p.get('stock_maestro') or 0
        p['imagen_url'] = _obtener_url_imagen(p.get('codigo', ''))
    return productos


# =============================================================================
# SCORING JERÁRQUICO ANTI FALSOS POSITIVOS (Ponderación de Extracción)
# =============================================================================

# Penalización multiplicativa cuando la coincidencia es SOLO por laboratorio.
PENALIZACION_LABORATORIO = 0.20
# Piso absoluto: por debajo de 0.50 la coincidencia se descarta (no_coincide).
UMBRAL_DESCARTE_COTEJO = 0.50

# Bandas de score por prioridad (regla de negocio del visor):
#   P1 exacto (codigo_barra / codigo_articulo)         -> 1.0
#   P2 marca + concentracion + forma_farmaceutica      -> 0.85 .. 0.99
#   P3 principio_activo + concentracion                -> 0.65 .. 0.84


def _compactar(texto: str) -> str:
    """Elimina espacios y signos para comparar dosis/presentaciones
    ('500 MG' == '500MG', '10mg/5ml' == '10MG5ML')."""
    return "".join(ch for ch in normalizar_texto(texto) if ch.isalnum())


def _coincide_campo(descripcion_compacta: str, termino) -> bool:
    """True si el término (normalizado) aparece en la descripción compacta."""
    t = _compactar(termino)
    return bool(t) and t in descripcion_compacta


def _penalizar_laboratorio(score: float, solo_laboratorio: bool) -> float:
    """Penalización multiplicativa 0.20 si coincide ÚNICAMENTE en laboratorio.

    Si tras la penalización el score cae por debajo de 0.50, la coincidencia
    se descarta (retorna 0.0 -> 'desconocido / no_coincide').
    """
    if not solo_laboratorio:
        return score
    score = score * PENALIZACION_LABORATORIO
    return 0.0 if score < UMBRAL_DESCARTE_COTEJO else round(score, 4)


def _score_cotejo_jerarquico(
    datos_vision: dict,
    descripcion: str,
    laboratorio_candidato: str = "",
) -> float:
    """Score jerárquico [0,1] entre los campos extraídos por la IA y un candidato.

    Reglas (Ponderación de Extracción):
      P1 (1.0):  codigo_barra/codigo exacto — se resuelve ANTES en SQL.
      P2 (0.85-0.99): marca + concentracion + forma_farmaceutica.
      P3 (0.65-0.84): principio_activo + concentracion.
      P2b (0.80-0.84): marca + concentracion sin forma (queda en banda P3).

    PENALIZACIÓN ESTRICTA: si el candidato coincide ÚNICAMENTE en
    `laboratorio` pero difiere en marca / principio_activo / concentracion,
    el score final se multiplica por 0.20; si el resultado es < 0.50 se
    descarta (retorna 0.0 = no_coincide). El laboratorio NUNCA arrastra el
    score de un producto distinto (ej. MD00001 vs MD00826, ambos BioVenezuela).
    """
    desc_compacta = _compactar(descripcion)

    marca = datos_vision.get("marca") or datos_vision.get("descripcion") or ""
    principio = datos_vision.get("principio_activo") or ""
    concentracion = (
        datos_vision.get("concentracion") or datos_vision.get("dosis") or ""
    )
    forma = datos_vision.get("forma_farmaceutica") or ""
    laboratorio = datos_vision.get("laboratorio") or ""

    m_marca = _coincide_campo(desc_compacta, marca)
    m_principio = _coincide_campo(desc_compacta, principio)
    m_concentracion = _coincide_campo(desc_compacta, concentracion)
    m_forma = _coincide_campo(desc_compacta, forma)
    m_laboratorio = _coincide_campo(desc_compacta, laboratorio) or (
        bool(laboratorio_candidato)
        and _coincide_campo(_compactar(laboratorio_candidato), laboratorio)
    )

    # --- Banda P2: marca + concentración + forma farmacéutica (0.85-0.99) ---
    if m_marca and m_concentracion and m_forma:
        score = 0.85
        if m_principio:
            score += 0.09  # evidencia completa (PA + marca + dosis + forma)
        else:
            score += 0.04  # solo queda 0.05 de bonus por token exacto
        if _compactar(marca) in desc_compacta and _compactar(concentracion) in desc_compacta:
            score += 0.05
        score = min(score, 0.99)
    # --- Banda P2b: marca + concentración (sin forma) -> 0.80-0.84 ---
    elif m_marca and m_concentracion:
        score = 0.80
        if m_principio:
            score += 0.04
    # --- Banda P3: principio activo + concentración (0.65-0.84) ---
    elif m_principio and m_concentracion:
        score = 0.65
        if m_forma:
            score += 0.05
        if m_marca:
            score += 0.04
        if _compactar(principio) in desc_compacta and _compactar(concentracion) in desc_compacta:
            score = min(0.84, score + 0.10)
    # --- Coincidencia débil por marca o principio aislado (0.50-0.64) ---
    elif m_marca:
        score = 0.60
    elif m_principio:
        score = 0.60
    else:
        # Intersección de tokens de los campos extraídos contra la descripción
        tokens = [t for t in (marca, principio, concentracion, forma) if _compactar(t)]
        if tokens:
            aciertos = sum(1 for t in tokens if _compactar(t) in desc_compacta)
            if aciertos > 0:
                score = min(0.79, 0.50 + 0.29 * (aciertos / len(tokens)))
            else:
                score = 0.0
        else:
            score = 0.0

    # --- PENALIZACIÓN ESTRICTA por laboratorio ---
    solo_laboratorio = m_laboratorio and not (m_marca or m_principio or m_concentracion)
    if solo_laboratorio and (marca or principio or concentracion):
        return _penalizar_laboratorio(score, solo_laboratorio=True)

    # NORMALIZACIÓN DE CONFIANZA: marca + dosis con coincidencia EXACTA
    # (compacta) alcanzan siempre score >= 0.75, sin importar la banda.
    if m_marca and m_concentracion:
        score = max(score, 0.75)

    if score < UMBRAL_DESCARTE_COTEJO:
        return 0.0
    return round(score, 4)


def _buscar_producto_sql_vision(datos_vision: dict) -> list:
    """
    Búsqueda jerárquica ANTI FALSOS POSITIVOS (score 0..1, adjuntado como
    `score_busqueda` en cada producto):

      PASO 1 (Prioridad Máxima - Barcode): si 'codigo_barra' tiene MÁS DE 6
         dígitos, coincidencia EXACTA (normalizada) en codigo_barra/codigo.
         Retorna de inmediato con score = 1.0.
      PASO 2/3 (Scoring Jerárquico): pool de candidatos por CUALQUIER campo
         extraído (marca/principio/concentracion/forma/laboratorio) puntuado
         con `_score_cotejo_jerarquico`:
           - P2 (0.85-0.99): marca + concentracion + forma_farmaceutica.
           - P3 (0.65-0.84): principio_activo + concentracion.
           - Penalización estricta: coincidencia ÚNICA en laboratorio (diferen
             en marca/PA/concentracion) -> score x0.20; < 0.50 se descarta.
      PASO 4 (Fallback Tokens): si los campos anteriores vienen nulos/vacíos,
         iterar 'palabras_clave' de la IA con LIKE %TOKEN% y score por
         intersección (2+ tokens en la descripción => score >= 0.85); el OCR
         crudo se cubre en el pipeline con umbral 0.60.

    Normalización: textos IA y descripciones del maestro se normalizan
    (MAYÚSCULAS + sin tildes) antes de puntuar, para que 'Perebrón' coincida
    con 'PEREBRON'. REGLA DE SEGURIDAD: si el mejor score < umbral se retorna
    [] — NUNCA un producto por defecto o aleatorio.
    """
    umbral = float(os.environ.get("ARA_VISION_UMBRAL_CONFIANZA", "0.65"))
    umbral_tokens = 0.60  # fallback de tokens (espec: umbral calibrado)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    def _marcar(rows_puntuados):
        """Adjunta score_busqueda a cada dict y enriquece con imagen/almacenes."""
        for s, r in rows_puntuados:
            r['score_busqueda'] = s
        return _enriquecer_imagen_y_almacenes([r for _, r in rows_puntuados])

    try:
        codigo_barra = (datos_vision.get('codigo_barra') or '').strip().upper()
        codigo       = (datos_vision.get('codigo') or '').strip().upper()

        # --- PASO 1: Código de barras (>6 dígitos) o código interno exacto ---
        barra_limpio = ''.join(ch for ch in codigo_barra if ch.isdigit())
        if len(barra_limpio) > 6:
            rows = conn.execute("""
                SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado,
                       campo7, codigo_barra, stock_act, despacho_bqto, deposito_bqto
                FROM stock_maestro
                WHERE REPLACE(REPLACE(COALESCE(codigo_barra, ''), ' ', ''), '-', '') = ?
                   OR codigo = ?
                LIMIT 3
            """, (barra_limpio, codigo_barra)).fetchall()
            if rows:
                return _marcar([(1.0, dict(r)) for r in rows])

        # Código interno exacto (legacy, sin validación de dígitos)
        if codigo:
            rows = conn.execute("""
                SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado,
                       campo7, codigo_barra, stock_act, despacho_bqto, deposito_bqto
                FROM stock_maestro
                WHERE codigo = ?
                LIMIT 3
            """, (codigo,)).fetchall()
            if rows:
                return _marcar([(1.0, dict(r)) for r in rows])

        # --- PASO 2/3: SCORING JERÁRQUICO (P2 marca+concentracion+forma;
        #     P3 principio_activo+concentracion) con penalización estricta de
        #     laboratorio. Pool de candidatos = cualquier campo extraído por
        #     la IA (incluye laboratorio: el candidato entra al pool pero el
        #     scorer lo descarta si NO difiere en marca/PA/concentracion).
        terminos_busqueda = [
            t
            for t in (
                datos_vision.get("marca"),
                datos_vision.get("descripcion"),
                datos_vision.get("principio_activo"),
                datos_vision.get("concentracion"),
                datos_vision.get("dosis"),
                datos_vision.get("forma_farmaceutica"),
                datos_vision.get("laboratorio"),
            )
            if t
        ]
        terminos_norm = list(dict.fromkeys(normalizar_texto(t) for t in terminos_busqueda))
        terminos_norm = [t for t in terminos_norm if t]

        puntuados = []
        if terminos_norm:
            # Pool de candidatos: el término más LARGO (marca/principio, el más
            # discriminante) lidera el ranking; el resto (dosis/forma) amplía.
            # Así una coincidencia exacta de marca+dosis nunca queda fuera del
            # LIMIT aunque existan miles de descripciones con "500MG".
            primario = max(terminos_norm, key=len)
            condiciones = [
                "UPPER(descripcion) LIKE ?",
                "UPPER(codigo) LIKE ?",
            ]
            params = [f"%{primario}%", f"%{primario}%"]
            for v in terminos_norm:
                if v != primario:
                    condiciones.append("UPPER(descripcion) LIKE ?")
                    params.append(f"%{v}%")
            filas = conn.execute(
                f"""
                SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado,
                       campo7, codigo_barra, stock_act, despacho_bqto, deposito_bqto
                FROM stock_maestro
                WHERE {' OR '.join(condiciones)}
                ORDER BY (UPPER(descripcion) LIKE ?) DESC, (UPPER(codigo) LIKE ?) DESC,
                         LENGTH(descripcion) ASC
                LIMIT 500
                """,
                params + [f"%{primario}%", f"%{primario}%"],
            ).fetchall()
            for r in filas:
                r = dict(r)
                score = _score_cotejo_jerarquico(
                    datos_vision,
                    r.get("descripcion") or "",
                    laboratorio_candidato=r.get("laboratorio", "") or "",
                )
                if score >= umbral:
                    puntuados.append((score, r))
            puntuados.sort(key=lambda x: -x[0])
            if puntuados:
                return _marcar(puntuados[:3])

        # --- PASO 4: FALLBACK POR TOKENS (palabras_clave de la IA) ---
        tokens_clave = [normalizar_texto(t) for t in (datos_vision.get('palabras_clave') or [])]
        tokens_clave = list(dict.fromkeys(t for t in tokens_clave if len(t) >= 3))
        if tokens_clave:
            discriminantes = sorted(tokens_clave, key=len, reverse=True)[:6]
            conds = []
            params = []
            for t in discriminantes:
                conds.append("UPPER(descripcion) LIKE ?")
                params.append(f'%{t}%')
            filas = conn.execute(
                f"""
                SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado,
                       campo7, codigo_barra, stock_act, despacho_bqto, deposito_bqto
                FROM stock_maestro
                WHERE {' OR '.join(conds)}
                LIMIT 30
                """,
                params,
            ).fetchall()
            por_token = []
            for r in filas:
                r = dict(r)
                desc_n = normalizar_texto(r.get('descripcion') or '')
                inter = [t for t in tokens_clave if t in desc_n]
                score = len(inter) / max(len(tokens_clave), 1)
                if len(inter) >= 2:
                    score = max(score, 0.85)  # intersección clara => match fuerte
                por_token.append((score, r))
            por_token.sort(key=lambda x: -x[0])
            aprobados = [par for par in por_token if par[0] >= umbral_tokens]
            if aprobados:
                return _marcar(aprobados[:3])

        return []
    finally:
        conn.close()


def _buscar_por_texto_plano(texto_ocr: str, umbral: float = 0.60) -> list:
    """FALLBACK de texto plano: busca por tokens (>3 chars) del OCR sobre descripcion.

    Se usa cuando el JSON de visión devuelve 'marca' y 'principio_activo'
    vacíos/nulos: el texto crudo del modelo suele contener el nombre del
    producto. Score = proporción de tokens encontrados en la descripción.

    REGLA DE SEGURIDAD: solo se usan candidatos con score >= umbral (0.60);
    nunca un producto por defecto. Retorna lista enriquecida (vacía si no hay
    match claro).
    """
    import re

    STOP_WORDS_OCR = {
        'el', 'la', 'los', 'las', 'de', 'del', 'un', 'una', 'con', 'para',
        'por', 'que', 'y', 'en', 'a', 'al', 'su', 'se', 'no', 'es', 'lo',
        'le', 'como', 'mas', 'stock', 'producto', 'codigo', 'código',
        'presentacion', 'concentracion', 'laboratorio', 'envase', 'caja',
        'cajas', 'tabla', 'tabletas', 'tableta', 'comprimido', 'comprimidos',
        'jarabe', 'ampolla', 'ampollas', 'unidad', 'unidades', 'ml', 'mg',
        'g', 'lote', 'vencimiento', 'fecha', 'usar', 'use', 'via', 'vía',
        'oral', 'inyectable', 'crema', 'unguento', 'solucion', 'suspension',
    }
    tokens = []
    for t in re.findall(r"[A-ZÁÉÍÓÚÑ0-9]{4,}", (texto_ocr or '').upper()):
        if t not in STOP_WORDS_OCR:
            tokens.append(normalizar_texto(t))
    tokens = list(dict.fromkeys(tokens))
    if not tokens:
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # Acotar con los 6 tokens más largos (más discriminativos)
        discriminantes = sorted(tokens, key=len, reverse=True)[:6]
        condiciones = " OR ".join(["UPPER(descripcion) LIKE ?"] * len(discriminantes))
        filas = conn.execute(
            f"""
            SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado,
                   campo7, codigo_barra, stock_act, despacho_bqto, deposito_bqto
            FROM stock_maestro
            WHERE {condiciones}
            LIMIT 30
            """,
            [f'%{t}%' for t in discriminantes],
        ).fetchall()

        puntuados = []
        for r in filas:
            r = dict(r)
            desc = normalizar_texto(r.get('descripcion') or '')
            encontrados = sum(1 for t in tokens if t in desc)
            score = encontrados / len(tokens)
            puntuados.append((score, r))
        puntuados.sort(key=lambda x: -x[0])

        aprobados = [r for s, r in puntuados if s >= umbral]
        if aprobados:
            return _enriquecer_imagen_y_almacenes(aprobados[:3])
        return []
    finally:
        conn.close()


def _adjuntar_historial_ubicaciones(productos: list) -> None:
    """Adjunta historial de reubicaciones (reportes_ubicacion) y ubicacion_pendiente a cada producto in-place."""
    if not productos:
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        for prod in productos:
            codigo = prod.get('codigo', '')
            if not codigo:
                prod['historial_ubicaciones'] = []
                prod['ubicacion_pendiente'] = None
                continue
            rows = conn.execute("""
                SELECT usuario, desde, hacia, fecha
                FROM reportes_ubicacion
                WHERE co_art = ?
                ORDER BY rowid DESC
                LIMIT 3
            """, (codigo,)).fetchall()
            prod['historial_ubicaciones'] = [dict(r) for r in rows]
            # Ubicación pendiente (procesado_profit = 0)
            row_pend = conn.execute("""
                SELECT hacia FROM reportes_ubicacion
                WHERE co_art = ? AND COALESCE(procesado_profit, 0) = 0
                ORDER BY fecha DESC LIMIT 1
            """, (codigo,)).fetchone()
            prod['ubicacion_pendiente'] = row_pend['hacia'] if row_pend else None
    finally:
        conn.close()


def normalizar_texto(texto) -> str:
    """Normaliza para comparación: MAYÚSCULAS + sin tildes ni diacríticos.

    'Perebrón' -> 'PEREBRON', 'ÓXIDO' -> 'OXIDO', 'GULAPER' -> 'GULAPER'.
    Se aplica TANTO a los datos extraídos por la IA como a las descripciones
    del maestro de inventario antes de calcular scores.
    """
    import unicodedata

    t = unicodedata.normalize('NFD', str(texto or ''))
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return t.upper().strip()


def _extraer_json_respuesta(texto: str) -> dict:
    """Extrae y parsea el JSON de la respuesta de la IA vision (robusto).

    1. Remueve bloques de código Markdown (```json ... ```).
    2. Extrae el bloque JSON con regex DOTALL entre llaves.
    3. Si `json.loads()` falla, FALLBACK: captura pares clave-valor sueltos
       (regex) y lista `palabras_clave` de la respuesta cruda.
    Nunca lanza: retorna dict (posiblemente vacío).
    """
    import re

    if not texto:
        return {}
    crudo = str(texto).strip()

    # 1. Quitar bloques de código Markdown
    crudo = re.sub(r'```(?:json)?\s*', '', crudo, flags=re.IGNORECASE)
    crudo = crudo.replace('```', '').strip()

    def _intentar(bloque):
        bloque = bloque.strip()
        if not bloque:
            return None
        try:
            parsed = json.loads(bloque)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return None

    # 2. Primer intento: bloque JSON completo ({...} en crudo)
    m = re.search(r'\{.*\}', crudo, re.DOTALL)
    if m:
        parsed = _intentar(m.group(0))
        if parsed:
            return parsed

    # 2b. FALLBACK real detectado en vivo (v4.53): algunos modelos (ej.
    # meta/llama-3.2-11b-vision-instruct) IGNORAN la instrucción de "responde
    # solo JSON" y contestan en prosa con viñetas markdown, ej.:
    #   "* **Brand Name**: Bionectar\n* **Active Ingredient**: Maca..."
    # El modelo leyó la etiqueta CORRECTAMENTE (verificado contra la foto
    # real) pero el parser JSON no encontraba nada y devolvía {} — la IA no
    # era el problema, el parser sí. Este paso reconoce "Etiqueta: valor"
    # con o sin viñeta/negrita markdown, en inglés o español.
    datos_md = _extraer_campos_markdown(crudo)
    if datos_md:
        return datos_md

    # 2c. FALLBACK real detectado en vivo (v4.53): otras veces el modelo ni
    # siquiera usa viñetas — responde en prosa corrida ("...the brand name
    # "NEUROLAB" in large purple letters..."), a menudo con un preámbulo de
    # charla ("Here is a summary of the image..."). Sin líneas "Campo:
    # valor" que el paso 2b pueda leer. Este paso rescata el valor entre
    # comillas que aparezca poco después de mencionar el campo, sin importar
    # la estructura de la oración.
    datos_prosa = _extraer_campos_prosa_citada(crudo)
    if datos_prosa:
        return datos_prosa

    # 3. FALLBACK: pares clave-valor sueltos + palabras_clave
    datos = {}
    for clave, valor in re.findall(r'"(\w+)":\s*"([^"]*)"', crudo, re.IGNORECASE):
        if clave not in datos or not datos.get(clave):
            datos[clave] = valor.strip()
    if 'palabras_clave' not in datos:
        tokens = re.findall(r'"[A-ZÁÉÍÓÚÑ0-9]{2,}"', crudo)
        tokens = [t.strip('"') for t in tokens]
        if tokens:
            datos['palabras_clave'] = tokens
    return datos


# Alias de nombres de campo tal como los modelos de visión los escriben en
# prosa (inglés o español, con o sin tilde) -> clave canónica del dominio.
_ALIAS_CAMPO_VISION = {
    "marca": ("brand name", "brand", "marca", "nombre comercial",
              "product name", "nombre del producto", "producto"),
    "principio_activo": ("active ingredient", "principio activo",
                          "generic name", "ingrediente activo",
                          "componente activo", "compound"),
    "concentracion": ("concentration", "concentracion", "dose", "dosis",
                       "strength", "dosage", "dosificacion"),
    "forma_farmaceutica": ("formulation", "forma farmaceutica",
                            "dosage form", "form", "presentation",
                            "presentacion"),
    "laboratorio": ("manufacturer", "laboratory", "laboratorio",
                     "made by", "lab", "fabricante", "company"),
    "codigo_barra": ("barcode", "codigo de barras", "ean", "upc", "code",
                      "codigo"),
}


def _extraer_campos_markdown(texto: str) -> dict:
    """Extrae campos de una respuesta en PROSA/markdown (ver comentario en
    _extraer_json_respuesta). Recorre línea por línea buscando 'Etiqueta:
    valor', quitando viñetas ('*', '-', '•') y negrita ('**') markdown antes
    de comparar la etiqueta contra _ALIAS_CAMPO_VISION."""
    import re as _re

    datos: dict = {}
    for linea in texto.splitlines():
        limpio = linea.strip()
        limpio = _re.sub(r'^[\*\-•]+\s*', '', limpio)
        limpio = limpio.replace('**', '').replace('__', '')
        if ':' not in limpio:
            continue
        etiqueta, _, valor = limpio.partition(':')
        valor = valor.strip().strip('*').strip()
        if not valor or valor.lower() in ('n/a', 'null', 'none', 'no legible'):
            continue
        etiqueta_norm = normalizar_texto(etiqueta).lower().strip()
        for campo, alias_tupla in _ALIAS_CAMPO_VISION.items():
            if datos.get(campo):
                continue
            if any(normalizar_texto(a).lower() == etiqueta_norm for a in alias_tupla):
                datos[campo] = valor
                break
    return datos


_PATRON_COMILLAS = re.compile(r'["“]([^"“”]{2,60})["”]')


def _extraer_campos_prosa_citada(texto: str) -> dict:
    """Último recurso: el modelo respondió en prosa libre corrida, sin
    viñetas ni pares 'campo: valor' (ej. '...the brand name "NEUROLAB" in
    large purple letters...'). Busca la palabra clave del campo y toma la
    primera cadena entre comillas que aparezca poco después (ventana de 100
    caracteres) — suficiente para separar 'the brand name is "X"' del resto
    de la oración sin capturar comillas de otra parte del texto."""
    # Bug real detectado en vivo: el alias corto "lab" matcheaba como
    # substring DENTRO de otra palabra ("NEUROLAB" contiene "LAB"), así que
    # "laboratorio" terminaba capturando el nombre de la marca por error.
    # \b\b evita que un alias corto matchee en medio de otra palabra.
    datos: dict = {}
    for campo, alias_tupla in _ALIAS_CAMPO_VISION.items():
        for alias in alias_tupla:
            m_alias = re.search(r'\b' + re.escape(alias) + r'\b', texto, re.IGNORECASE)
            if not m_alias:
                continue
            resto = texto[m_alias.end():m_alias.end() + 100]
            m_valor = _PATRON_COMILLAS.search(resto)
            if m_valor:
                valor = m_valor.group(1).strip()
                if valor:
                    datos[campo] = valor
                    break
    return datos


# =============================================================================
# MOTORES DE VISIÓN
# =============================================================================

def _modelo_activo() -> str | None:
    """Modelo de visión NIM actual según rotación `_MODEL_INDEX` (thread-safe).

    Salta los modelos descartados (HTTP 404), perdonando automáticamente los
    que ya cumplieron TTL_DESCARTE_MODELO_S. Si TODOS siguen descartados,
    retorna None (el caller cae al fallback local).
    """
    with _MODEL_LOCK:
        ahora = time.time()
        # Perdón automático (v4.53): antes un 404 dejaba el modelo fuera
        # hasta reiniciar el proceso — un blip momentáneo del proveedor
        # forzaba Ollama local indefinidamente sin necesidad real.
        vencidos = [
            m for m, ts in _MODELOS_DESCARTADOS.items()
            if ahora - ts >= TTL_DESCARTE_MODELO_S
        ]
        for m in vencidos:
            del _MODELOS_DESCARTADOS[m]
        if len(_MODELOS_DESCARTADOS) >= len(NIM_VISION_MODELS):
            return None
        for paso in range(len(NIM_VISION_MODELS)):
            idx = (_MODEL_INDEX + paso) % len(NIM_VISION_MODELS)
            modelo = NIM_VISION_MODELS[idx]
            if modelo not in _MODELOS_DESCARTADOS:
                return modelo
        return None


def _rotar_modelo():
    """Avanza al siguiente modelo de NIM_VISION_MODELS válido (thread-safe).

    Se dispara ante HTTP 404 (Model Not Found) o timeout: el string del
    modelo no es válido en el endpoint (o no responde) y ninguna key lo va
    a arreglar, así que se conmuta el identificador sin agotar el pool de
    5 keys.
    """
    global _MODEL_INDEX
    # BUG CRÍTICO real detectado en vivo (v4.53): esta función llamaba a
    # _modelo_activo() DENTRO del propio `with _MODEL_LOCK` — como es un
    # threading.Lock() simple (no reentrante), el mismo hilo intentaba
    # re-adquirir un lock que YA tenía → DEADLOCK GARANTIZADO cada vez que
    # un modelo NIM daba timeout. El hilo (y el request del operador) se
    # colgaban para siempre — esto es la causa real de "el visor ya no
    # responde", no el modelo de IA. Fix: soltar el lock ANTES de volver a
    # llamar a _modelo_activo() (que toma su propio lock).
    with _MODEL_LOCK:
        _MODEL_INDEX += 1
    siguiente = _modelo_activo()
    print(f"[NVIDIA_NIM] 🔄 Modelo rotado a: {siguiente}", flush=True)


def _descartar_modelo(modelo: str):
    """Descarta un modelo por TTL_DESCARTE_MODELO_S (thread-safe)."""
    global _MODEL_INDEX
    # Mismo bug de deadlock que _rotar_modelo (ver comentario arriba) — el
    # print original llamaba a _modelo_activo() dentro del `with` ya
    # adquirido. Fix idéntico: soltar el lock antes.
    with _MODEL_LOCK:
        _MODELOS_DESCARTADOS[modelo] = time.time()
        _MODEL_INDEX += 1
    siguiente = _modelo_activo()
    print(
        f"[NVIDIA_NIM] 🗑️ Modelo {modelo} descartado por {TTL_DESCARTE_MODELO_S}s. "
        f"Siguiente: {siguiente}",
        flush=True,
    )


def _llamar_deepseek_vision(base64_img: str, timeout: float = 20.0) -> str | None:
    """Llama a DeepSeek Vision (motor primario desde 28/08).

    Timeout más holgado que NIM (20s vs 6s) porque es un modelo con
    razonamiento interno (reasoning_content) — probado en vivo: 3-4s típico,
    pero sin el pool de 5 keys de NIM para amortiguar una key/cuenta lenta,
    así que no conviene cortar tan agresivo como con NIM. max_tokens alto
    (2000) es necesario: con presupuestos bajos el razonamiento se come todo
    el budget y "content" llega vacío con finish_reason="length" (visto en
    vivo). Si eso pasa igual pese al margen, se trata como fallo y se cae al
    siguiente motor (NIM), nunca se devuelve texto vacío como si fuera válido.
    """
    if not DEEPSEEK_API_KEY:
        return None

    payload = {
        "model": DEEPSEEK_VISION_MODEL,
        "messages": [
            {"role": "system", "content": VISION_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_img}"
                    }}
                ]
            }
        ],
        "max_tokens": 2000,
        "temperature": 0.1,
    }

    print(
        f"[DEEPSEEK_VISION] 🚀 Enviando foto | Modelo: {DEEPSEEK_VISION_MODEL}",
        flush=True,
    )
    t_inicio = time.perf_counter()
    try:
        resp = requests.post(
            DEEPSEEK_VISION_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        latencia_ms = round((time.perf_counter() - t_inicio) * 1000.0, 1)

        if resp.status_code != 200:
            print(
                f"[DEEPSEEK_VISION] ⚠️ HTTP {resp.status_code} en {latencia_ms}ms: "
                f"{resp.text[:150]}",
                flush=True,
            )
            return None

        data = resp.json()
        choice = data["choices"][0]
        texto = (choice.get("message", {}).get("content") or "").strip()
        finish_reason = choice.get("finish_reason")

        if not texto:
            print(
                f"[DEEPSEEK_VISION] ⚠️ content vacío en {latencia_ms}ms "
                f"(finish_reason={finish_reason!r}) — probable razonamiento "
                f"agotó max_tokens. Cayendo al siguiente motor.",
                flush=True,
            )
            return None

        print(
            f"[DEEPSEEK_VISION] ✅ Respuesta exitosa en {latencia_ms}ms "
            f"(finish_reason={finish_reason!r})",
            flush=True,
        )
        return texto

    except requests.exceptions.Timeout:
        print(f"[DEEPSEEK_VISION] ⏱️ Timeout {timeout}s superado.", flush=True)
        return None
    except Exception as e:
        print(f"[DEEPSEEK_VISION] ⚠️ Fallo: {str(e)[:150]}", flush=True)
        return None


def _llamar_nim_vision(base64_img: str, timeout: float = 6.0) -> str | None:
    """Llama a NVIDIA NIM Vision con pool de 5 API Keys, pool multimodelo
    de prioridad comprobada y failover automático.

    Timeout ESTRICTO de conexión/lectura por intento: 6.0s (calibración de
    campo). Si un modelo no responde a tiempo, se aborta al instante y se
    prueba el siguiente candidato => la latencia fotográfica total de ARA
    queda en < 4s en lugar de colgarse en 90s.

    Round-Robin: cada petición parte de `_KEY_INDEX` (rotado proactivamente
    tras cada éxito). Si la key activa responde HTTP 429/401/403/5xx (o
    timeout/red), se conmuta a la siguiente key del pool y se reintenta,
    hasta `MAX_INTENTOS_KEY_POOL` (3) intentos en total. Si responde HTTP
    404 (Model Not Found), se DESCARTA ese modelo para la sesión activa y se
    rota el MODELO dentro de `NIM_VISION_MODELS` (Llama-Vision -> Qwen ->
    Phi-3.5) sin desgastar keys en un modelo inexistente. Cada llamada HTTP
    se audita en consola en tiempo real. Si el pool se agota, retorna None
    (el caller cae al fallback local).
    """
    payload = {
        "model": _modelo_activo(),
        "messages": [
            {"role": "system", "content": VISION_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_img}"
                    }}
                ]
            }
        ],
        "max_tokens": 500,
        "temperature": 0.1
    }

    intentos = 0
    while intentos < MAX_INTENTOS_KEY_POOL:
        if payload["model"] is None:
            print(
                "[NVIDIA_NIM] ❌ Todos los modelos descartados (404 en "
                "sesión activa). Cayendo a fallback local.",
                flush=True,
            )
            break
        intentos += 1
        with _KEY_LOCK:
            idx = _KEY_INDEX % len(NVIDIA_KEYS)
            api_key = NVIDIA_KEYS[idx]

        def _rotar():
            with _KEY_LOCK:
                global _KEY_INDEX
                _KEY_INDEX = (idx + 1) % len(NVIDIA_KEYS)

        # AUDITORÍA OBLIGATORIA: log ANTES de disparar la petición HTTP
        print(
            f"[NVIDIA_NIM] 🚀 Enviando foto | Modelo: {payload['model']} | "
            f"Key [{idx + 1}/{len(NVIDIA_KEYS)}]: "
            f"{api_key[:10]}...{api_key[-4:]}",
            flush=True,
        )

        t_inicio = time.perf_counter()
        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            resp = requests.post(NVIDIA_NIM_URL, json=payload,
                                 headers=headers, timeout=timeout)
            latencia_ms = round((time.perf_counter() - t_inicio) * 1000.0, 1)

            if resp.status_code == 404:
                # Model Not Found: ninguna key arregla el string del modelo.
                # DESCARTAR el modelo para la sesión activa y rotar al
                # siguiente candidato sin agotar el pool de keys.
                print(
                    f"[NVIDIA_NIM] ❌ Model Not Found (404) con "
                    f"{payload['model']} en Key [{idx + 1}]: "
                    f"{resp.text[:150]}",
                    flush=True,
                )
                _descartar_modelo(payload["model"])
                payload["model"] = _modelo_activo()
                _rotar()
                continue

            if resp.status_code == 200:
                # LOG CRUDO DE DEPURACIÓN: respuesta exacta del proveedor para
                # detectar payloads vacíos en silencio (MiniMax VL).
                logger.debug(f"[NIM_MINIMAX_RAW]: {resp.text}")
                print(
                    f"[NVIDIA_NIM] ✅ Respuesta exitosa 200 en {latencia_ms}ms "
                    f"(Key [{idx + 1}])",
                    flush=True,
                )
                texto = resp.json()["choices"][0]["message"]["content"].strip()
                if not texto:
                    print(
                        "[NVIDIA_NIM] ⚠️ Contenido VACÍO en respuesta 200 "
                        f"(raw: {resp.text[:200]}). Rotando a la siguiente key...",
                        flush=True,
                    )
                    _rotar()
                    continue
                _rotar()  # rotación proactiva Round-Robin tras éxito
                return texto

            if resp.status_code in STATUS_FALLO_KEY or resp.status_code >= 500:
                print(
                    f"[NVIDIA_NIM] ⚠️ Fallo en Key [{idx + 1}] "
                    f"(Status {resp.status_code}): {resp.text[:150]}",
                    flush=True,
                )
                _rotar()
                continue

            print(
                f"[NVIDIA_NIM] ⚠️ Fallo en Key [{idx + 1}] "
                f"(Status {resp.status_code}): {resp.text[:150]}",
                flush=True,
            )
            _rotar()
            continue

        except requests.exceptions.Timeout:
            print(
                f"[NVIDIA_NIM] ⏱️ Timeout {timeout}s superado en modelo "
                f"{payload['model']} (Key [{idx + 1}]). Probando siguiente "
                f"candidato...",
                flush=True,
            )
            _rotar_modelo()
            payload["model"] = _modelo_activo()
            _rotar()
            continue

        except Exception as e:
            print(
                f"[NVIDIA_NIM] ⚠️ Fallo en Key [{idx + 1}] "
                f"(Status excepción): {str(e)[:150]}. Rotando...",
                flush=True,
            )
            _rotar()
            continue

    print(
        f"[NVIDIA_NIM] ❌ {MAX_INTENTOS_KEY_POOL} intentos agotados con el pool "
        f"({len(NVIDIA_KEYS)} keys). Cayendo a fallback local.",
        flush=True,
    )
    return None


def _llamar_ollama_vision(base64_img: str, timeout: int = 30) -> str | None:
    """Llama a Ollama LLaVA como fallback local. Retorna texto crudo o None."""
    payload = {
        "model": OLLAMA_VISION_MODEL,
        "prompt": VISION_PROMPT,
        "images": [base64_img],
        "stream": False,
        "options": {"num_predict": 256, "temperature": 0.1}
    }
    try:
        resp = requests.post(OLLAMA_VISION_URL, json=payload, timeout=timeout)
        if resp.status_code == 200:
            return resp.json().get('response', '').strip()
        print(f"[Ollama Vision] HTTP {resp.status_code}")
        return None
    except Exception as e:
        print(f"[Ollama Vision] Error: {e}")
        return None


# =============================================================================
# FUNCIÓN PRINCIPAL
# =============================================================================

def procesar_imagen_visor(image_input) -> dict:
    """
    Pipeline completo de visión + inventario:

    1. Convierte imagen a base64.
    2. OCR/Análisis con NVIDIA NIM Vision (fallback Ollama LLaVA).
    3. Extrae JSON con campos: codigo, descripcion, laboratorio, lote, fecha_vencimiento.
    4. Busca el código o descripción extraídos en stock_maestro.
    5. Une datos de visión + datos de inventario.

    Retorna dict estructurado para el frontend. Orden de motores desde 28/08:
    DeepSeek Vision (primario) → NVIDIA NIM (respaldo, pool de 5 keys) →
    Ollama LLaVA local (último recurso).
    """
    print("[ARA_VISION] 🟢 Ejecutando pipeline de visión (DeepSeek → NIM → Ollama)...", flush=True)

    try:
        # 1. Preprocesar imagen (recorte central + contraste) y convertir a base64
        pre = _preprocesar_imagen(image_input)
        b64 = _imagen_a_base64(pre if pre is not None else image_input)
    except Exception as e:
        return {"status": "error", "mensaje": f"Error leyendo imagen: {e}"}

    # 2. OCR con DeepSeek Vision (primario)
    print("[ARA Vision] Llamando a DeepSeek Vision...")
    texto_ocr = _llamar_deepseek_vision(b64)

    # 3. Fallback a NVIDIA NIM Vision si DeepSeek falla
    if not texto_ocr:
        print("[ARA Vision] Fallback a NVIDIA NIM Vision...")
        texto_ocr = _llamar_nim_vision(b64)

    # 4. Fallback a Ollama LLaVA si NIM también falla
    if not texto_ocr:
        print("[ARA Vision] Fallback a Ollama LLaVA...")
        texto_ocr = _llamar_ollama_vision(b64)

    if not texto_ocr:
        return {
            "status": "error",
            "mensaje": "No se pudo analizar la imagen (DeepSeek, NVIDIA NIM y Ollama no respondieron)."
        }

    # Log de diagnóstico (v4.53): antes no había forma de ver qué respondió
    # REALMENTE el modelo cuando el resultado salía low_confidence — solo se
    # sabía "vino vacío", sin poder distinguir entre "el modelo no vio nada"
    # y "el modelo vio bien pero el parser de JSON falló". Con esto en
    # consola, la próxima vez que pase queda el texto exacto para diagnosticar.
    print(f"[ARA_VISION] 📝 Texto crudo del modelo (primeros 500 chars): "
          f"{texto_ocr[:500]!r}", flush=True)

    # 4. Extraer JSON del texto OCR
    datos_vision = _extraer_json_respuesta(texto_ocr)
    print(f"[ARA_VISION] 🔎 JSON parseado: {datos_vision}", flush=True)

    # 5. Buscar en stock_maestro con algoritmo de prioridad (barcode → AND → fallback)
    productos_bd = _buscar_producto_sql_vision(datos_vision)

    # 5b. FALLBACK DE TEXTO PLANO: si el JSON vino con 'marca' y
    #     'principio_activo' vacíos/nulos, buscar por tokens del texto crudo
    #     del OCR sobre la descripción (solo si hay match claro >= 0.60).
    if not productos_bd:
        marca = (datos_vision.get('marca') or '').strip()
        principio = (datos_vision.get('principio_activo') or '').strip()
        if not marca and not principio:
            print("[ARA Vision] JSON vacío en marca/principio — fallback texto plano OCR...")
            productos_bd = _buscar_por_texto_plano(texto_ocr)

    # 6. Adjuntar historial de reubicaciones de reportes_ubicacion
    _adjuntar_historial_ubicaciones(productos_bd)

    # 7. Ensamblar respuesta
    resultado = {
        "status": "success",
        "ocr_texto": texto_ocr,
        "datos_vision": datos_vision,
        "productos_encontrados": productos_bd,
        "total_coincidencias": len(productos_bd)
    }

    if not productos_bd:
        # REGLA DE SEGURIDAD: sin score que supere el umbral (65/100) NO se
        # devuelve ningún producto — estado low_confidence, nunca aleatorio.
        resultado["status"] = "low_confidence"
        resultado["mensaje"] = (
            "Producto no identificado con certeza. "
            "Enfoque la marca o escanee el código."
        )
    else:
        p = productos_bd[0]
        resultado["mensaje"] = (
            f"Producto: {p.get('descripcion', 'N/A')} | "
            f"Código: {p.get('codigo', 'N/A')} | "
            f"Stock: {p.get('stock_maestro', 0)} unds | "
            f"Ubicación: {p.get('campo7', 'N/A')}"
        )

    return resultado


# Alias de retrocompatibilidad para código legado (ara_server.py línea 8)
investigar_producto_ara = procesar_imagen_visor

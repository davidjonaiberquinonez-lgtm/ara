import requests
import os
import base64
import json
import sqlite3
import traceback
from io import BytesIO

# =============================================================================
# CONFIGURACIÓN CENTRALIZADA
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'proyecto_ara.db')

# NVIDIA NIM Vision (motor primario)
NVIDIA_API_KEY = "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"
NVIDIA_NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_VISION_MODEL = "meta/llama-3.2-11b-vision-instruct"

# Ollama Vision (fallback local)
OLLAMA_VISION_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_VISION_MODEL = "llava"

VISION_PROMPT = (
    'Analiza esta imagen de inventario/medicina. '
    'Busca cualquier código de barras numérico (EAN/UPC) visible en el empaque. '
    'Extrae en formato JSON estricto los siguientes campos si están visibles: '
    '{"codigo_barra": string, "codigo": string, "descripcion": string, '
    '"laboratorio": string, "dosis": string, '
    '"lote": string, "fecha_vencimiento": string}. '
    'Si no detectas un campo, colócalo como null. '
    'Responde ÚNICAMENTE con el JSON sin explicaciones ni markdown.'
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


def _buscar_producto_sql_vision(datos_vision: dict) -> list:
    """
    Busca en stock_maestro con prioridad estricta:
      A) Código de barras o código interno exacto.
      B) AND estricto: nombre + dosis + laboratorio en descripcion.
      C) AND parcial: nombre + dosis.
      D) Palabra más larga como fallback.

    Retorna lista de dicts (vacía si no hay coincidencias).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        codigo_barra = (datos_vision.get('codigo_barra') or '').strip().upper()
        codigo       = (datos_vision.get('codigo') or '').strip().upper()
        desc         = (datos_vision.get('descripcion') or '').strip().upper()
        laboratorio  = (datos_vision.get('laboratorio') or '').strip().upper()
        dosis        = (datos_vision.get('dosis') or '').strip().upper()

        # --- PASO A: Código de barras o código interno exacto ---
        termino_exacto = codigo_barra or codigo
        if termino_exacto:
            rows = conn.execute("""
                SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado,
                       campo7, codigo_barra
                FROM stock_maestro
                WHERE codigo_barra = ? OR codigo = ?
                LIMIT 3
            """, (termino_exacto, termino_exacto)).fetchall()
            if rows:
                return [dict(r) for r in rows]

        # --- PASO B: AND estricto (nombre + dosis + laboratorio) ---
        if desc and dosis and laboratorio:
            condiciones = [
                "UPPER(descripcion) LIKE ?",
                "(UPPER(descripcion) LIKE ? OR UPPER(descripcion) LIKE ?)",
                "UPPER(descripcion) LIKE ?"
            ]
            params = [
                f'%{desc}%',
                f'%{dosis}%', f'%{dosis.replace(" ", "")}%',
                f'%{laboratorio}%'
            ]
            rows = conn.execute("""
                SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado,
                       campo7, codigo_barra
                FROM stock_maestro
                WHERE {}
                LIMIT 5
            """.format(" AND ".join(condiciones)), params).fetchall()
            if rows:
                return [dict(r) for r in rows]

        # --- PASO C: AND parcial (nombre + dosis) ---
        if desc and dosis:
            condiciones = [
                "UPPER(descripcion) LIKE ?",
                "(UPPER(descripcion) LIKE ? OR UPPER(descripcion) LIKE ?)"
            ]
            params = [f'%{desc}%', f'%{dosis}%', f'%{dosis.replace(" ", "")}%']
            if laboratorio:
                condiciones.append("UPPER(descripcion) LIKE ?")
                params.append(f'%{laboratorio}%')
            rows = conn.execute("""
                SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado,
                       campo7, codigo_barra
                FROM stock_maestro
                WHERE {}
                LIMIT 5
            """.format(" AND ".join(condiciones)), params).fetchall()
            if rows:
                return [dict(r) for r in rows]

        # --- PASO D: Palabra más larga como fallback ---
        texto_compuesto = ' '.join(filter(None, [codigo_barra, codigo, desc, dosis, laboratorio]))
        STOP_WORDS = {'dame', 'el', 'la', 'los', 'las', 'del', 'un', 'una',
                      'stock', 'codigo', 'código', 'producto', 'para', 'por',
                      'con', 'que', 'como', 'mas', 'más', 'concentracion',
                      'presentacion', 'laboratorio', 'lote', 'de', 'en', 'al',
                      'su', 'se', 'no', 'es', 'lo', 'le'}
        palabras = [t for t in texto_compuesto.split()
                    if len(t) > 2 and t not in STOP_WORDS]
        if palabras:
            palabra_fuerte = max(palabras, key=len)
            like = f'%{palabra_fuerte}%'
            rows = conn.execute("""
                SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado,
                       campo7, codigo_barra
                FROM stock_maestro
                WHERE UPPER(codigo) LIKE ?
                   OR UPPER(codigo_barra) LIKE ?
                   OR UPPER(descripcion) LIKE ?
                LIMIT 5
            """, (like, like, like)).fetchall()
            if rows:
                return [dict(r) for r in rows]

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


def _extraer_json_respuesta(texto: str) -> dict:
    """Extrae y parsea el JSON de la respuesta de la IA vision."""
    texto = texto.strip()
    if texto.startswith('```'):
        texto = texto.split('\n', 1)[-1]
        texto = texto.rsplit('```', 1)[0]
    texto = texto.strip()
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        for intento in ('{', '['):
            inicio = texto.find(intento)
            if inicio != -1:
                try:
                    return json.loads(texto[inicio:])
                except json.JSONDecodeError:
                    pass
    return {}


# =============================================================================
# MOTORES DE VISIÓN
# =============================================================================

def _llamar_nim_vision(base64_img: str, timeout: int = 15) -> str:
    """Llama a NVIDIA NIM Vision API. Retorna texto crudo o None."""
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": NVIDIA_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_img}"
                    }}
                ]
            }
        ],
        "max_tokens": 256,
        "temperature": 0.1
    }
    try:
        resp = requests.post(NVIDIA_NIM_URL, json=payload,
                             headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        print(f"[NIM Vision] HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    except Exception as e:
        print(f"[NIM Vision] Error: {e}")
        return None


def _llamar_ollama_vision(base64_img: str, timeout: int = 30) -> str:
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

    Retorna dict estructurado para el frontend.
    """
    try:
        # 1. Convertir imagen a base64
        b64 = _imagen_a_base64(image_input)
    except Exception as e:
        return {"status": "error", "mensaje": f"Error leyendo imagen: {e}"}

    # 2. OCR con NVIDIA NIM Vision (primario)
    print("[ARA Vision] Llamando a NVIDIA NIM Vision...")
    texto_ocr = _llamar_nim_vision(b64)

    # 3. Fallback a Ollama LLaVA si NVIDIA falla
    if not texto_ocr:
        print("[ARA Vision] Fallback a Ollama LLaVA...")
        texto_ocr = _llamar_ollama_vision(b64)

    if not texto_ocr:
        return {
            "status": "error",
            "mensaje": "No se pudo analizar la imagen (NVIDIA NIM y Ollama no respondieron)."
        }

    # 4. Extraer JSON del texto OCR
    datos_vision = _extraer_json_respuesta(texto_ocr)

    # 5. Buscar en stock_maestro con algoritmo de prioridad (barcode → AND → fallback)
    productos_bd = _buscar_producto_sql_vision(datos_vision)

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
        terminos = '/'.join(filter(None, [
            datos_vision.get('codigo_barra'),
            datos_vision.get('codigo'),
            datos_vision.get('descripcion')
        ]))
        resultado["mensaje"] = (
            f"Se extrajo '{terminos or 'sin datos'}' de la imagen, "
            f"pero no se encontró en el inventario."
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

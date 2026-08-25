# -*- coding: utf-8 -*-
"""
API local de OCR de vouchers/comprobantes de pago — para que OTROS proyectos
(en esta misma PC o red local) le manden un comprobante a ARA y reciban el
JSON estructurado (banco, referencia, monto, fecha, tipo_pago) sin tener que
reimplementar el OCR ni conocer la tool PHP interna.

Acepta 3 tipos de entrada:
  - Imagen (png/jpg/jpeg/webp/bmp) → envoltorio HTTP sobre la tool existente
    `leer_voucher_ocr` (LeerVoucherOcrTool.php), que solo acepta una RUTA DE
    ARCHIVO en el servidor — un proyecto externo no tiene archivos ahí, así
    que este módulo recibe el archivo (upload o base64), lo guarda en un
    temporal local, invoca la tool vía el runner PHP existente y borra el
    temporal siempre al final.
  - PDF (pdf) → se renderiza la primera página a PNG (PyMuPDF, sin depender
    de binarios externos como poppler) y esa imagen sigue el mismo camino
    de OCR de arriba.
  - Excel (xlsx) → NO pasa por el modelo de visión: ya es dato estructurado.
    Se leen las celdas directo (openpyxl) buscando una fila de encabezado
    con nombres de columna reconocibles (banco/referencia/monto/fecha/tipo
    de pago, en español o inglés) y se devuelve un voucher por cada fila de
    datos.

Endpoints:
  POST /api/vision/voucher         → procesa el archivo y devuelve el JSON.
  GET  /api/vision/voucher/estado  → health-check + config activa (sin secretos).

Sin autenticación (uso local/red local, a pedido). Si en el futuro este
endpoint necesita exponerse fuera de la red local, agregar un header
X-ARA-Api-Key validado contra una env var antes de eso.
"""
import base64
import io
import os
import time
import uuid

from flask import jsonify, request

_EXTENSIONES_IMAGEN = {'png', 'jpg', 'jpeg', 'webp', 'bmp'}
_EXTENSIONES_PDF = {'pdf'}
_EXTENSIONES_EXCEL = {'xlsx'}
_EXTENSIONES_VALIDAS = _EXTENSIONES_IMAGEN | _EXTENSIONES_PDF | _EXTENSIONES_EXCEL
_TAMANO_MAX_BYTES = 15 * 1024 * 1024  # 15 MB, igual que la tool PHP de imagen

_TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'tmp_ocr')

# Encabezados reconocidos por columna (normalizados: minúsculas, sin
# acentos/espacios/guiones) para el modo Excel — variantes ES/EN.
_ENCABEZADOS_EXCEL = {
    'banco': {'banco', 'bank', 'entidadbancaria', 'entidad'},
    'referencia': {'referencia', 'ref', 'nroreferencia', 'numeroreferencia', 'reference', 'refnro'},
    'monto': {'monto', 'importe', 'montopagado', 'amount', 'total', 'valor'},
    'fecha': {'fecha', 'date', 'fechapago'},
    'tipo_pago': {'tipopago', 'tipodepago', 'metododepago', 'formadepago', 'paymenttype', 'metodopago'},
}


def _normalizar_encabezado(texto: str) -> str:
    import unicodedata
    s = str(texto or '').strip().lower()
    s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    return ''.join(ch for ch in s if ch.isalnum())


def _guardar_temporal(binario: bytes, extension: str) -> str:
    os.makedirs(_TMP_DIR, exist_ok=True)
    nombre = f"voucher_{int(time.time())}_{uuid.uuid4().hex[:8]}.{extension}"
    ruta = os.path.join(_TMP_DIR, nombre)
    with open(ruta, 'wb') as f:
        f.write(binario)
    return ruta


def _borrar_temporal(ruta: str) -> None:
    try:
        if ruta and os.path.isfile(ruta):
            os.remove(ruta)
    except OSError as e:
        print(f"[VisionOCR] No se pudo borrar el temporal {ruta}: {e}")


def _pdf_a_png(binario_pdf: bytes) -> bytes:
    """Renderiza la PRIMERA página del PDF a PNG (200 DPI) en memoria."""
    import pymupdf
    doc = pymupdf.open(stream=binario_pdf, filetype='pdf')
    try:
        if doc.page_count < 1:
            raise ValueError('El PDF no tiene páginas.')
        pagina = doc.load_page(0)
        # 200/72 ≈ zoom para ~200 DPI (el PDF nativo es 72 DPI).
        matriz = pymupdf.Matrix(200 / 72, 200 / 72)
        pix = pagina.get_pixmap(matrix=matriz)
        return pix.tobytes('png')
    finally:
        doc.close()


def _ejecutar_ocr_imagen(ruta_temporal: str, idioma: str) -> tuple:
    """Invoca leer_voucher_ocr vía el runner PHP. Retorna (payload_dict, http_code)."""
    import json as _json
    try:
        from ara_server import _ejecutar_runner_tools  # import local: evita ciclo
        resultado = _ejecutar_runner_tools(
            [
                'leer_voucher_ocr',
                _json.dumps({'ruta_imagen': ruta_temporal, 'idioma': idioma}, ensure_ascii=False),
                _json.dumps({'usuario': 'api_externa', 'rol': 'sistema', 'modulo': 'vision_ocr_api'}, ensure_ascii=False),
            ],
            timeout_s=int(os.environ.get('NVIDIA_VISION_TIMEOUT', '90')) + 15,
        )
    except Exception as e:
        return {'success': False, 'error': f'No se pudo ejecutar el OCR: {e}'}, 500

    if not isinstance(resultado, dict) or not resultado.get('success'):
        mensaje = (resultado or {}).get('mensaje') or (resultado or {}).get('error') or 'El runner de la tool falló.'
        return {'success': False, 'error': mensaje}, 502

    contenido = resultado.get('resultado', {}).get('content', '')
    try:
        payload = _json.loads(contenido) if isinstance(contenido, str) else contenido
    except Exception:
        return {'success': False, 'error': 'Respuesta de la tool no interpretable como JSON.'}, 502

    return payload, (200 if payload.get('success') else 422)


def _leer_excel(binario_xlsx: bytes) -> tuple:
    """Lee un .xlsx buscando una fila de encabezado reconocible y devuelve
    {"success": true, "data": {"vouchers": [...]}} — sin OCR, dato directo."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(binario_xlsx), data_only=True, read_only=True)
    except Exception as e:
        return {'success': False, 'error': f'No se pudo leer el Excel: {e}'}, 400

    ws = wb.active
    filas = list(ws.iter_rows(values_only=True))
    wb.close()
    if not filas:
        return {'success': False, 'error': 'El Excel está vacío.'}, 400

    # Busca la primera fila que tenga al menos 2 encabezados reconocidos.
    fila_encabezado_idx = None
    mapa_col_campo = {}
    for i, fila in enumerate(filas[:10]):  # el encabezado real casi siempre está en las primeras 10 filas
        mapa = {}
        for col, celda in enumerate(fila):
            norm = _normalizar_encabezado(celda)
            for campo, variantes in _ENCABEZADOS_EXCEL.items():
                if norm in variantes:
                    mapa[col] = campo
                    break
        if len(mapa) >= 2:
            fila_encabezado_idx = i
            mapa_col_campo = mapa
            break

    if fila_encabezado_idx is None:
        return {
            'success': False,
            'error': ('No se encontró una fila de encabezado reconocible (se buscan columnas '
                      'como banco/referencia/monto/fecha/tipo de pago en las primeras 10 filas).'),
            'primeras_filas': [list(f) for f in filas[:5]],
        }, 422

    vouchers = []
    for fila in filas[fila_encabezado_idx + 1:]:
        if fila is None or all(c is None for c in fila):
            continue
        voucher = {'banco': None, 'referencia': None, 'monto': None, 'fecha': None, 'tipo_pago': None}
        for col, campo in mapa_col_campo.items():
            if col >= len(fila):
                continue
            valor = fila[col]
            if valor is None:
                continue
            if campo == 'monto':
                try:
                    voucher['monto'] = float(valor)
                except (TypeError, ValueError):
                    voucher['monto'] = None
            elif campo == 'fecha':
                voucher['fecha'] = valor.isoformat() if hasattr(valor, 'isoformat') else str(valor)
            else:
                voucher[campo] = str(valor).strip()
        if any(v is not None for v in voucher.values()):
            vouchers.append(voucher)

    if not vouchers:
        return {'success': False, 'error': 'Se encontró el encabezado pero ninguna fila de datos debajo.'}, 422

    return {
        'success': True,
        'data': {
            'vouchers': vouchers,
            'total': len(vouchers),
            'origen': 'excel',
        },
    }, 200


def register_vision_ocr_routes(app):
    @app.route('/api/vision/voucher/estado', methods=['GET'])
    def vision_voucher_estado():
        """Health-check: confirma que el endpoint y el runner PHP están vivos
        y expone la config activa del modelo de visión (sin API keys)."""
        return jsonify({
            'status': 'ok',
            'endpoint': '/api/vision/voucher',
            'metodo': 'POST',
            'formatos_aceptados': {
                'imagen': sorted(_EXTENSIONES_IMAGEN),
                'pdf': sorted(_EXTENSIONES_PDF),
                'excel': sorted(_EXTENSIONES_EXCEL),
            },
            'tamano_max_mb': _TAMANO_MAX_BYTES // (1024 * 1024),
            'proveedor_vision': 'NVIDIA NIM Cloud (primario) — pool de 2 modelos x hasta 5 API keys',
            'modelos_vision_pool': [
                'meta/llama-3.2-11b-vision-instruct',
                'meta/llama-3.2-90b-vision-instruct',
            ],
            'base_url_vision': os.environ.get('NVIDIA_VISION_BASE_URL', 'https://integrate.api.nvidia.com'),
        })

    @app.route('/api/vision/voucher', methods=['POST'])
    def vision_voucher():
        """Recibe un comprobante (imagen, PDF o Excel) y devuelve el JSON.

        Dos formas de mandar el archivo (usa la que sea más cómoda):

        1) multipart/form-data — campo de archivo llamado "imagen" (el nombre
           se mantiene por compatibilidad aunque ahora acepte más formatos).
           curl -F "imagen=@voucher.pdf" http://<esta_pc>:5000/api/vision/voucher

        2) JSON — {"imagen_base64": "<base64 sin encabezado data:>",
                    "extension": "pdf"}  (extension: png|jpg|jpeg|webp|bmp|pdf|xlsx)

        Parámetro opcional (solo aplica a imagen/PDF): "idioma" (es|en, default es).

        Respuesta — imagen/PDF (uno solo por request):
          {"success": true, "data": {"voucher": {...}, "texto_ocr": "...",
                                      "imagen": "...", "modelo": "..."}, "card": "..."}
        Respuesta — Excel (puede traer varias filas):
          {"success": true, "data": {"vouchers": [{...}, ...], "total": N, "origen": "excel"}}
        Error (cualquier formato):
          {"success": false, "error": "mensaje"}
        """
        binario = None
        extension = None
        idioma = 'es'

        if request.content_type and 'multipart/form-data' in request.content_type:
            archivo = request.files.get('imagen')
            if archivo is None or archivo.filename == '':
                return jsonify({'success': False, 'error': 'Falta el archivo "imagen" (multipart/form-data).'}), 400
            binario = archivo.read()
            extension = (archivo.filename.rsplit('.', 1)[-1] if '.' in archivo.filename else '').lower()
            idioma = (request.form.get('idioma') or 'es').lower()
        else:
            body = request.get_json(silent=True) or {}
            b64 = body.get('imagen_base64') or ''
            extension = str(body.get('extension') or '').lower().lstrip('.')
            idioma = str(body.get('idioma') or 'es').lower()
            if not b64:
                return jsonify({
                    'success': False,
                    'error': 'Falta "imagen_base64" en el JSON (o mande multipart/form-data con campo "imagen").',
                }), 400
            if b64.startswith('data:') and ';base64,' in b64:
                b64 = b64.split(';base64,', 1)[1]
            try:
                binario = base64.b64decode(b64, validate=False)
            except Exception:
                return jsonify({'success': False, 'error': 'imagen_base64 no es Base64 válido.'}), 400

        if not binario:
            return jsonify({'success': False, 'error': 'El archivo llegó vacío.'}), 400
        if len(binario) > _TAMANO_MAX_BYTES:
            return jsonify({'success': False, 'error': 'El archivo supera el tamaño máximo permitido (15 MB).'}), 400
        if extension not in _EXTENSIONES_VALIDAS:
            return jsonify({
                'success': False,
                'error': f'Formato no soportado ("{extension}"). Use: PNG, JPEG, WebP, BMP, PDF o XLSX.',
            }), 400
        if idioma not in ('es', 'en'):
            idioma = 'es'

        # ── Excel: dato ya estructurado, sin pasar por el modelo de visión ──
        if extension in _EXTENSIONES_EXCEL:
            payload, codigo = _leer_excel(binario)
            return jsonify(payload), codigo

        # ── PDF: se renderiza la 1ra página a PNG y sigue el camino de imagen ──
        if extension in _EXTENSIONES_PDF:
            try:
                binario = _pdf_a_png(binario)
            except Exception as e:
                return jsonify({'success': False, 'error': f'No se pudo convertir el PDF a imagen: {e}'}), 422
            extension = 'png'

        # ── Imagen (o PDF ya convertido): OCR vía la tool PHP ──
        ruta_temporal = _guardar_temporal(binario, extension)
        try:
            payload, codigo = _ejecutar_ocr_imagen(ruta_temporal, idioma)
        finally:
            _borrar_temporal(ruta_temporal)
        return jsonify(payload), codigo

import asyncio
import base64
import json
import io
import traceback
from typing import Optional

from pydantic import ValidationError

from .domain import NotaEntregaOCR
from .ports import VisionProvider, NotaRepository


def _optimizar_imagen(imagen_bytes: bytes, max_ancho: int = 1280, calidad: int = 85) -> bytes:
    """Redimensiona y comprime la imagen para reducir payload.

    - Escala proporcionalmente si el ancho > max_ancho.
    - Convierte a JPEG con la calidad indicada.
    - Retorna bytes JPEG (o los originales si PIL no está disponible).
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(imagen_bytes))

        if img.mode in ("P", "RGBA"):
            img = img.convert("RGB")

        ancho_orig, alto_orig = img.size
        if ancho_orig > max_ancho:
            factor = max_ancho / ancho_orig
            nuevo_alto = int(alto_orig * factor)
            img = img.resize((max_ancho, nuevo_alto), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=calidad, optimize=True)
        comprimido = buf.getvalue()

        reduccion = (1 - len(comprimido) / len(imagen_bytes)) * 100
        print(
            f"[OCR] Imagen optimizada: {len(imagen_bytes)} → {len(comprimido)} bytes "
            f"({reduccion:.1f}% reducción) | "
            f"dimensión original {ancho_orig}x{alto_orig}"
        )
        return comprimido

    except ImportError:
        print("[OCR] PIL no disponible, usando imagen sin optimizar")
        return imagen_bytes
    except Exception as e:
        print(f"[OCR] Error al optimizar imagen: {e}")
        traceback.print_exc()
        return imagen_bytes


def _imagen_a_base64(imagen_bytes: bytes) -> str:
    return base64.b64encode(imagen_bytes).decode("utf-8")


def _extraer_json(texto: str) -> Optional[dict]:
    texto = texto.strip()
    if texto.startswith("```"):
        for delim in ("```json", "```javascript", "```"):
            if texto.startswith(delim):
                texto = texto[len(delim):]
                break
        texto = texto.rsplit("```", 1)[0].strip()

    decoder = json.JSONDecoder()
    for delimiter in ("{", "["):
        inicio = texto.find(delimiter)
        if inicio != -1:
            try:
                raw = texto[inicio:]
                obj, idx = decoder.raw_decode(raw)
                if isinstance(obj, dict):
                    return obj
                if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                    return obj[0]
            except (json.JSONDecodeError, ValueError, IndexError):
                continue
    return None


def _mapear_campos(datos: dict) -> dict:
    """Normaliza los campos del prompt Profit Plus a nombres del modelo de dominio."""
    mapeado = {
        "numero_nota": datos.get("numero_nota", ""),
        "codigo_cliente": datos.get("rif") or datos.get("codigo_cliente", ""),
        "nombre_cliente": datos.get("cliente") or datos.get("nombre_cliente", ""),
        "observaciones_manuales": datos.get("observaciones_manuales"),
        "confianza_escaneo": float(datos.get("confianza_escaneo", 0.0)),
        "almacen": datos.get("almacen"),
        "rif": datos.get("rif"),
        "domicilio_fiscal": datos.get("domicilio") or datos.get("domicilio_fiscal"),
        "ruta": datos.get("ruta"),
        "zona": datos.get("zona"),
        "vendedor": datos.get("vendedor"),
        "items": [],
    }
    for item in datos.get("items", []):
        mapeado["items"].append({
            "codigo_producto": item.get("codigo") or item.get("codigo_producto", ""),
            "descripcion": item.get("descripcion", ""),
            "cantidad_solicitada": float(item.get("cantidad") or item.get("cantidad_solicitada", 0)),
            "unidad": item.get("unidad", "UND"),
            "ubicacion": item.get("ubicacion"),
        })
    return mapeado


def _validar_respuesta(datos: dict) -> Optional[NotaEntregaOCR]:
    try:
        mapeados = _mapear_campos(datos)
        return NotaEntregaOCR.model_validate(mapeados)
    except ValidationError as e:
        print(f"[OCR] Error de validación Pydantic: {e}")
        traceback.print_exc()
        return None


class OcrNotasService:
    def __init__(
        self,
        vision_primario: VisionProvider,
        vision_fallback: VisionProvider,
        repositorio: NotaRepository,
    ):
        self._vision_primario = vision_primario
        self._vision_fallback = vision_fallback
        self._repositorio = repositorio

    @staticmethod
    def _ahora() -> str:
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    async def procesar(
        self,
        imagen_bytes: bytes,
        verificar_stock: bool = True,
        registrar_en_bd: bool = True,
    ) -> dict:
        print(f"[OCR] procesar: imagen original = {len(imagen_bytes)} bytes, "
              f"fecha_hora = {self._ahora()}")
        errores = {}

        imagen_opt = _optimizar_imagen(imagen_bytes)
        b64 = _imagen_a_base64(imagen_opt)

        texto_respuesta, error_nvidia = await self._vision_primario.analizar(b64)
        origen = "NVIDIA_NIM_VISION" if texto_respuesta else None
        if error_nvidia:
            errores["nvidia_nim_request"] = error_nvidia

        if not texto_respuesta:
            texto_respuesta, error_ollama = await self._vision_fallback.analizar(b64)
            origen = "MINI_ARA_LOCAL" if texto_respuesta else None
            if error_ollama:
                errores["ollama_fallback"] = error_ollama

        if not texto_respuesta:
            resultado = {
                "status": "error",
                "ocr": None,
                "mensaje": "No se pudo analizar la imagen (NVIDIA y Ollama no disponibles)",
                "origen_procesamiento": "MANUAL_FALLBACK",
            }
            if errores:
                resultado["errores"] = errores
            return resultado

        datos_json = _extraer_json(texto_respuesta)
        print(f"[OCR] JSON extraído del modelo: {json.dumps(datos_json, ensure_ascii=False)[:500]}" if datos_json else "[OCR] No se pudo extraer JSON de la respuesta del modelo")
        if not datos_json:
            resultado = {
                "status": "error",
                "ocr": None,
                "mensaje": "La IA no devolvió JSON válido",
                "texto_crudo": texto_respuesta[:500],
                "origen_procesamiento": origen,
            }
            if errores:
                resultado["errores"] = errores
            return resultado

        ocr_validado = _validar_respuesta(datos_json)
        if not ocr_validado:
            resultado = {
                "status": "error",
                "ocr": None,
                "mensaje": "Datos extraídos no pasaron validación Pydantic",
                "datos_crudos": datos_json,
                "origen_procesamiento": origen,
            }
            if errores:
                resultado["errores"] = errores
            return resultado

        ocr_validado.origen_procesamiento = origen

        print(f"[OCR] Validación exitosa: nota={ocr_validado.numero_nota}, "
              f"cliente={ocr_validado.nombre_cliente}, "
              f"items={len(ocr_validado.items)}, "
              f"origen={origen}")

        resultado = {
            "status": "success",
            "ocr": ocr_validado.model_dump(),
            "origen_procesamiento": origen,
            "mensaje": f"Nota {ocr_validado.numero_nota} procesada con "
                       f"{len(ocr_validado.items)} items "
                       f"(confianza: {ocr_validado.confianza_escaneo:.2f})",
        }
        if errores:
            resultado["errores_advertencia"] = errores

        if verificar_stock:
            verificacion = await asyncio.to_thread(
                self._repositorio.verificar_disponibilidad, ocr_validado
            )
            resultado["verificacion"] = verificacion
            total = len(verificacion)
            ok = sum(1 for v in verificacion if v.get("suficiente"))
            if ok < total:
                resultado["status"] = "partial"
                resultado["mensaje"] += f" | Stock: {ok}/{total} items suficientes"

        if registrar_en_bd and ocr_validado.numero_nota:
            nota_bd = await asyncio.to_thread(
                self._repositorio.crear_nota, ocr_validado
            )
            resultado["nota_bd"] = nota_bd
            if ocr_validado.observaciones_manuales:
                resultado["observaciones_manuales"] = ocr_validado.observaciones_manuales

        return resultado


async def procesar_nota_ocr(
    imagen_bytes: bytes,
    verificar_stock: bool = True,
    registrar_en_bd: bool = True,
) -> dict:
    """Wrapper de compatibilidad: crea service por defecto y ejecuta."""
    from .adapters.vision_nvidia import NvidiaVisionProvider
    from .adapters.vision_ollama import OllamaVisionProvider
    from .adapters.nota_repository import SqliteNotaRepository

    nvidia = NvidiaVisionProvider()
    ollama = OllamaVisionProvider()
    repo = SqliteNotaRepository()
    service = OcrNotasService(nvidia, ollama, repo)
    return await service.procesar(imagen_bytes, verificar_stock, registrar_en_bd)


def procesar_nota_ocr_sync(
    imagen_bytes: bytes,
    verificar_stock: bool = True,
    registrar_en_bd: bool = True,
) -> dict:
    """Wrapper síncrono de procesar_nota_ocr para endpoints Flask."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(
                asyncio.run,
                procesar_nota_ocr(
                    imagen_bytes,
                    verificar_stock=verificar_stock,
                    registrar_en_bd=registrar_en_bd,
                ),
            )
            return future.result()
    else:
        return loop.run_until_complete(
            procesar_nota_ocr(
                imagen_bytes,
                verificar_stock=verificar_stock,
                registrar_en_bd=registrar_en_bd,
            )
        )

# -*- coding: utf-8 -*-
"""
mini_ara_engine.py — Motor local Mini ARA Intelligent.
Integración con Ollama local + faster-whisper + trazabilidad hexagonal.
Ejecuta la IA 100% en edge sin dependencia de APIs externas.

Capacidades:
  - Transcripción de audio (faster-whisper)
  - Inferencia local (Ollama mini-ara)
  - Reconocimiento visual de productos (Ollama llava + búsqueda en stock)
  - Trazabilidad hexagonal (ara_brain)
"""
import os
import json
import tempfile
import base64
import requests
from io import BytesIO
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'proyecto_ara.db')

# Intentar importar faster-whisper (opcional)
try:
    from faster_whisper import WhisperModel
    WHISPER_DISPONIBLE = True
except ImportError:
    WHISPER_DISPONIBLE = False

# Prompt de visión para análisis de producto
VISION_PROMPT_PRODUCTO = (
    'Analiza esta imagen de inventario/logística. '
    'Extrae en formato JSON estricto los siguientes campos si están visibles en la imagen: '
    '{"codigo": string, "codigo_barra": string, "descripcion": string, '
    '"laboratorio": string, "dosis": string, "lote": string}. '
    'Si no detectas un campo, colócalo como null. '
    'Responde ÚNICAMENTE con el JSON sin explicaciones ni markdown.'
)


class MiniAraEngine:
    """
    Motor de inferencia local para Mini ARA Intelligent.

    - Modelo Ollama: mini-ara (creado vía Modelfile.mini_ara)
    - Transcripción: faster-whisper (modelo base o small)
    - Trazabilidad: puerto hexagonal obtener_trazabilidad_hexagonal()
    """

    def __init__(
        self,
        modelo_ollama: str = "mini-ara",
        ollama_url: str = "http://127.0.0.1:11434",
        modelo_whisper: str = "base",
    ):
        self.modelo_ollama = modelo_ollama
        self.ollama_url = ollama_url.rstrip("/")
        self.modelo_whisper = modelo_whisper
        self._whisper_model = None

    # ------------------------------------------------------------------
    # Whisper (transcripción de audio)
    # ------------------------------------------------------------------

    def _get_whisper(self):
        if not WHISPER_DISPONIBLE:
            raise RuntimeError(
                "faster-whisper no instalado. "
                "Ejecuta: pip install faster-whisper"
            )
        if self._whisper_model is None:
            self._whisper_model = WhisperModel(
                self.modelo_whisper,
                device="cpu",
                compute_type="int8",
                cpu_threads=4,
                num_workers=2,
            )
        return self._whisper_model

    def transcribir_audio(self, ruta_audio: str) -> str:
        """
        Transcribe un archivo de audio a texto usando faster-whisper.
        Soporta formatos: wav, mp3, m4a, ogg, flac.
        """
        model = self._get_whisper()
        segments, info = model.transcribe(ruta_audio, language="es")
        texto = " ".join(seg.text for seg in segments)
        return texto.strip()

    def procesar_audio_local(self, datos_binarios: bytes, formato: str = "wav") -> str:
        """
        Recibe bytes de audio, los escribe a archivo temporal,
        transcribe con whisper y retorna el texto.
        """
        if not WHISPER_DISPONIBLE:
            return "[Mini ARA] faster-whisper no disponible. Instala: pip install faster-whisper"

        with tempfile.NamedTemporaryFile(
            suffix=f".{formato}", delete=False
        ) as tmp:
            tmp.write(datos_binarios)
            tmp_path = tmp.name

        try:
            texto = self.transcribir_audio(tmp_path)
            return texto
        except Exception as e:
            return f"[Mini ARA] Error transcribiendo audio: {e}"
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Ollama (inferencia local)
    # ------------------------------------------------------------------

    def preguntar(
        self,
        mensaje: str,
        contexto_extra: str = "",
        temperature: float = 0.2,
        max_tokens: int = 512,
    ) -> str:
        """
        Envía un prompt al modelo mini-ara en Ollama local.
        Inyecta contexto de trazabilidad hexagonal si está disponible.

        Retorna el texto de respuesta o un mensaje de error.
        """
        # Intentar inyectar trazabilidad hexagonal
        trazabilidad = self._obtener_trazabilidad(mensaje)

        system_msg = (
            "Eres Mini ARA Intelligent, el asistente local de logística e inventario de Proyecto ARA. "
            "Solo respondes preguntas del dominio logístico. "
            "NUNCA inventes datos. Si no tienes información, indícalo honestamente."
        )

        if trazabilidad:
            context_block = (
                "\n\n[DATOS DE TRAZABILIDAD EXTRAÍDOS DE LA BD EN TIEMPO REAL]:\n"
                f"{json.dumps(trazabilidad, indent=2, ensure_ascii=False)}\n"
                "[FIN DE TRAZABILIDAD]\n"
            )
        else:
            context_block = ""

        if contexto_extra:
            context_block += f"\n[CONTEXTO ADICIONAL]:\n{contexto_extra}\n"

        prompt = f"{system_msg}\n{context_block}\nUsuario: {mensaje}\nMini ARA:"

        payload = {
            "model": self.modelo_ollama,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "stop": ["</s>", "Usuario:", "Mini ARA:"],
            },
        }

        try:
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json=payload,
                timeout=120,
            )
            if resp.status_code == 200:
                return resp.json().get("response", "").strip()
            else:
                return (
                    f"[Mini ARA] Error Ollama (HTTP {resp.status_code}): "
                    f"{resp.text[:200]}"
                )
        except requests.exceptions.ConnectionError:
            return (
                "[Mini ARA] No se pudo conectar con Ollama. "
                "Verifica que esté corriendo: ollama serve"
            )
        except Exception as e:
            return f"[Mini ARA] Error de inferencia: {e}"

    def verificar_disponibilidad(self) -> bool:
        """Verifica que el modelo mini-ara esté disponible en Ollama."""
        try:
            resp = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                modelos = resp.json().get("models", [])
                return any(m["name"].startswith(self.modelo_ollama) for m in modelos)
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Trazabilidad hexagonal (inyección de contexto)
    # ------------------------------------------------------------------

    def _obtener_trazabilidad(self, mensaje: str) -> dict | None:
        """
        Detecta si el mensaje contiene un código de nota o artículo
        y retorna la trazabilidad hexagonal.
        """
        try:
            from ara_brain import detectar_codigo_articulo, obtener_trazabilidad_hexagonal

            codigo = detectar_codigo_articulo(mensaje)
            if not codigo:
                return None

            return obtener_trazabilidad_hexagonal(codigo)
        except ImportError:
            return None
        except Exception as e:
            print(f"[MiniAraEngine] Error en trazabilidad: {e}")
            return None

    # ------------------------------------------------------------------
    # Visión (reconocimiento de productos por foto)
    # ------------------------------------------------------------------

    def _imagen_a_base64(self, image_input) -> str:
        """Convierte bytes / BytesIO / str a base64."""
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

    def _extraer_json_de_respuesta(self, texto: str) -> dict | None:
        """Extrae el primer bloque JSON de la respuesta del modelo."""
        import re as _re
        # Buscar bloque ```json ... ``` o {...} directamente
        m = _re.search(r'```(?:json)?\s*(\{.*?\})\s*```', texto, _re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        m = _re.search(r'(\{.*\})', texto, _re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        return None

    def _buscar_en_stock(self, codigo: str = "", codigo_barra: str = "", descripcion: str = "") -> dict | None:
        """
        Busca un producto en stock_maestro por código, código de barras o descripción.
        Retorna la ficha técnica del producto o None.
        """
        import sqlite3
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            # A) Código exacto o código de barras
            termino = (codigo or codigo_barra or "").strip().upper()
            if termino:
                row = conn.execute(
                    "SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado, "
                    "       campo7, deposito_bqto, codigo_barra "
                    "FROM stock_maestro WHERE codigo = ? OR codigo_barra = ? LIMIT 1",
                    (termino, termino)
                ).fetchone()
                if row:
                    return dict(row)

            # B) Búsqueda por palabras clave en descripción
            tokens = [t for t in descripcion.upper().split() if len(t) > 2]
            if tokens:
                # Buscar AND parcial: productos que contengan al menos 2 tokens
                like_clauses = " AND ".join(f"UPPER(descripcion) LIKE '%{t}%'" for t in tokens[:3])
                row = conn.execute(
                    f"SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado, "
                    f"       campo7, deposito_bqto, codigo_barra "
                    f"FROM stock_maestro WHERE {like_clauses} LIMIT 1"
                ).fetchone()
                if row:
                    return dict(row)

            return None
        finally:
            conn.close()

    def _obtener_ultimos_movimientos(self, co_art: str, limite: int = 5) -> list[dict]:
        """Obtiene los últimos movimientos de un artículo."""
        import sqlite3
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("""
                SELECT mp.usuario, mp.accion, mp.cantidad, mp.timestamp,
                       ne.numero_nota
                FROM movimientos_preparador mp
                LEFT JOIN notas_entrega ne ON ne.id = mp.nota_id
                WHERE mp.co_art = ?
                ORDER BY mp.timestamp DESC
                LIMIT ?
            """, (co_art, limite)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def analizar_foto_producto(self, imagen_b64: str) -> dict:
        """
        Analiza una foto de producto usando el modelo de visión local (llava).

        Flujo:
          1. Envía la imagen a Ollama llava para extraer código/descripción.
          2. Busca el producto en stock_maestro.
          3. Obtiene últimos movimientos.
          4. Retorna ficha técnica JSON.

        Retorna dict con:
          - vision: datos extraídos por la IA de visión
          - producto: datos del stock_maestro (o None si no encontrado)
          - ultimos_movimientos: list[dict]
          - ficha_tecnica: texto formateado para inyectar en System Prompt
        """
        resultado = {
            "vision": None,
            "producto": None,
            "ultimos_movimientos": [],
            "ficha_tecnica": "",
            "error": None
        }

        # 1. Análisis visual con llava
        try:
            payload = {
                "model": "llava",
                "prompt": VISION_PROMPT_PRODUCTO,
                "images": [imagen_b64],
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 256}
            }
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json=payload,
                timeout=60
            )
            if resp.status_code == 200:
                texto_vision = resp.json().get("response", "")
                datos_vision = self._extraer_json_de_respuesta(texto_vision)
                if not datos_vision:
                    resultado["error"] = f"No se pudo extraer JSON de la respuesta: {texto_vision[:200]}"
                    return resultado
                resultado["vision"] = datos_vision
            else:
                resultado["error"] = f"Error llamando a llava: HTTP {resp.status_code}"
                return resultado
        except requests.exceptions.ConnectionError:
            resultado["error"] = "No se pudo conectar con Ollama para visión. Verifica que esté corriendo."
            return resultado
        except Exception as e:
            resultado["error"] = f"Error en análisis visual: {e}"
            return resultado

        # 2. Búsqueda en stock
        codigo = (resultado["vision"] or {}).get("codigo", "")
        codigo_barra = (resultado["vision"] or {}).get("codigo_barra", "")
        descripcion = (resultado["vision"] or {}).get("descripcion", "")
        producto = self._buscar_en_stock(codigo, codigo_barra, descripcion)
        resultado["producto"] = producto

        # 3. Últimos movimientos
        if producto:
            co_art = producto.get("codigo", "")
            if co_art:
                resultado["ultimos_movimientos"] = self._obtener_ultimos_movimientos(co_art)

        # 4. Ficha técnica formateada
        resultado["ficha_tecnica"] = self._formatear_ficha(
            resultado["vision"], resultado["producto"], resultado["ultimos_movimientos"]
        )

        return resultado

    def _formatear_ficha(
        self,
        vision: dict | None,
        producto: dict | None,
        movimientos: list[dict]
    ) -> str:
        """Genera un bloque de texto formateado con la ficha técnica del producto."""
        lines = ["--- FICHA TÉCNICA DEL PRODUCTO (desde foto) ---"]

        if vision:
            lines.append(f"Detectado por visión: {json.dumps(vision, ensure_ascii=False)}")

        if producto:
            lines.append(f"Código: {producto.get('codigo', 'N/A')}")
            lines.append(f"Descripción: {producto.get('descripcion', 'N/A')}")
            lines.append(f"Stock Piso: {float(producto.get('stock_maestro', 0) or 0):.0f} unds")
            lines.append(f"Stock Bulto: {float(producto.get('stock_bulto_cerrado', 0) or 0):.0f} unds")
            lines.append(f"Ubicación Física: {producto.get('campo7', 'N/A')}")
            lines.append(f"Depósito: {producto.get('deposito_bqto', 'N/A')}")
            lines.append(f"Código de Barras: {producto.get('codigo_barra', 'N/A')}")
        else:
            lines.append("⚠️ Producto NO encontrado en stock_maestro.")

        if movimientos:
            lines.append("\nÚltimos movimientos:")
            for m in movimientos[:3]:
                lines.append(
                    f"  - {m.get('timestamp', 'N/A')}: {m.get('usuario', 'N/A')} "
                    f"hizo '{m.get('accion', 'N/A')}' de {float(m.get('cantidad', 0) or 0):.0f} unds "
                    f"(Nota: {m.get('numero_nota', 'N/A')})"
                )

        lines.append("--- FIN FICHA TÉCNICA ---")
        return "\n".join(lines)


# ------------------------------------------------------------------
# Instancia singleton global para compartir en ara_server.py
# ------------------------------------------------------------------
_instancia_engine: MiniAraEngine | None = None


def get_engine() -> MiniAraEngine:
    global _instancia_engine
    if _instancia_engine is None:
        _instancia_engine = MiniAraEngine()
    return _instancia_engine

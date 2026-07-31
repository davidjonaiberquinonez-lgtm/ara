import json
import traceback
from typing import Optional, Tuple

import requests as req_lib

from ..ports import VisionProvider

NOTA_PROMPT = """
Eres un motor OCR de alta precision para notas de entrega de almacen farmaceutico (formato Profit Plus).
Analiza la imagen adjunta y extrae UNICAMENTE un objeto JSON estricto con las siguientes reglas de lectura.

REGLAS DE EXTRACCION POR SECCIONES:

1. ENCABEZADO Y CLIENTE:
   - "numero_nota": Extrae el valor exacto del campo "Not. Entrega:".
   - "almacen": Extrae el titulo superior central.
   - "cliente": Extrae el texto exacto de "Razon Social:".
   - "rif": Extrae el campo "R.I.F.:".
   - "domicilio": Extrae el texto de "Domicilio Fiscal:".

2. TABLA DE PRODUCTOS (ITEMS):
   - "codigo": Extrae el codigo alfanumerico de la columna "Codigo". Es mas corto que la descripcion.
   - "ubicacion": Extrae el codigo de estante/piso de la columna "Ubicac.".
   - "cantidad": Extrae el numero entero de la columna "Cant.".
   - "unidad": Extrae la unidad de medida ("UND" = unidad, "CAJA" = caja, "BLIST" = blisters/sobre).
   - "descripcion": Extrae la columna "Descripcion" (nombre del medicamento, concentracion y laboratorio).

3. PIE DE PAGINA Y LOGISTICA:
   - "ruta": Extrae el campo "RUTA:".
   - "zona": Extrae el campo "ZONA:".
   - "vendedor": Extrae el campo "VENDEDOR:".

REGLAS DE COMPORTAMIENTO (LEER ESTRICTAMENTE):
- NO uses datos de ejemplo. Lee SOLO lo impreso en la imagen adjunta.
- Si un campo no es visible, devuelvelo como string vacio "".
- Si no hay items, devuelve array vacio [].
- No inventes, no extrapoles, no uses conocimiento previo.
- Responde UNICAMENTE con el JSON sin markdown ni explicaciones.

FORMATO DE SALIDA (usa SOLO este esquema, NO los valores de ejemplo):
{
  "numero_nota": "XXXXXXXXX",
  "almacen": "",
  "cliente": "",
  "rif": "",
  "domicilio": "",
  "ruta": "",
  "zona": "",
  "vendedor": "",
  "items": [
    {
      "ubicacion": "",
      "codigo": "",
      "cantidad": 0,
      "unidad": "",
      "descripcion": ""
    }
  ]
}
"""

STATUS_FALLO_KEY = {503, 429, 401, 403}
NVIDIA_VISION_MODEL = "meta/llama-3.2-11b-vision-instruct"
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


class NvidiaVisionProvider(VisionProvider):
    def __init__(self, timeout: int = 30):
        self._timeout = timeout

    async def analizar(self, base64_img: str) -> Tuple[Optional[str], Optional[str]]:
        import ara_brain

        api_keys = ara_brain.NVIDIA_API_KEYS
        payload = {
            "model": NVIDIA_VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": NOTA_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"},
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
            "temperature": 0.1,
        }

        # ── Log: payload (sin base64 que es muy largo) ──
        payload_log = {
            "model": payload["model"],
            "max_tokens": payload["max_tokens"],
            "temperature": payload["temperature"],
            "image_base64_len": len(base64_img),
            "prompt_preview": NOTA_PROMPT.strip()[:200],
        }
        print(f"[NVIDIA] Enviando a {NVIDIA_API_URL}")
        print(f"[NVIDIA] Payload: {json.dumps(payload_log, ensure_ascii=False)}")

        keys_probadas = set()

        with ara_brain._KEY_LOCK:
            idx = ara_brain._KEY_INDEX

        while len(keys_probadas) < len(api_keys):
            if idx in keys_probadas:
                break
            keys_probadas.add(idx)
            api_key = api_keys[idx]

            try:
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                resp = req_lib.post(
                    NVIDIA_API_URL, json=payload, headers=headers, timeout=self._timeout
                )

                if resp.status_code == 200:
                    texto = (
                        resp.json()
                        .get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    )
                    # ── Log: respuesta RAW antes del parsing ──
                    print(f"[NVIDIA] Respuesta RAW (primeros 600 chars): {texto[:600]}")
                    print(f"[NVIDIA] Respuesta RAW length: {len(texto)} chars")
                    with ara_brain._KEY_LOCK:
                        ara_brain._KEY_INDEX = (idx + 1) % len(api_keys)
                    return texto, None

                if resp.status_code in STATUS_FALLO_KEY:
                    detalle = f"Key #{idx + 1} fallo (HTTP {resp.status_code}): {resp.text[:200]}"
                    print(f"[NVIDIA] {detalle}")
                    with ara_brain._KEY_LOCK:
                        ara_brain._KEY_INDEX = (idx + 1) % len(api_keys)
                        idx = ara_brain._KEY_INDEX
                    continue

                detalle = f"Key #{idx + 1} error HTTP {resp.status_code}: {resp.text[:200]}"
                print(f"[NVIDIA] {detalle}")
                with ara_brain._KEY_LOCK:
                    ara_brain._KEY_INDEX = (idx + 1) % len(api_keys)
                    idx = ara_brain._KEY_INDEX
                continue

            except req_lib.exceptions.Timeout as e:
                detalle = f"Key #{idx + 1} timeout ({self._timeout}s): {e}"
                print(f"[NVIDIA] {detalle}")
                traceback.print_exc()
                with ara_brain._KEY_LOCK:
                    ara_brain._KEY_INDEX = (idx + 1) % len(api_keys)
                    idx = ara_brain._KEY_INDEX
                continue

            except req_lib.exceptions.ConnectionError as e:
                detalle = f"Key #{idx + 1} ConnectionError: {e}"
                print(f"[NVIDIA] {detalle}")
                traceback.print_exc()
                with ara_brain._KEY_LOCK:
                    ara_brain._KEY_INDEX = (idx + 1) % len(api_keys)
                    idx = ara_brain._KEY_INDEX
                continue

            except Exception as e:
                detalle = f"Key #{idx + 1} excepcion: {type(e).__name__}: {e}"
                print(f"[NVIDIA] {detalle}")
                traceback.print_exc()
                with ara_brain._KEY_LOCK:
                    ara_brain._KEY_INDEX = (idx + 1) % len(api_keys)
                    idx = ara_brain._KEY_INDEX
                continue

        error_msg = "Las 5 claves NVIDIA fallaron. Ver logs para detalles."
        print(f"[NVIDIA] {error_msg}")
        with ara_brain._KEY_LOCK:
            ara_brain._KEY_INDEX = 0
        return None, error_msg

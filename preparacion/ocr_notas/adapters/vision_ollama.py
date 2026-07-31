import traceback
from typing import Optional, Tuple

import requests as req_lib

from ..ports import VisionProvider
from ..prompts import SYSTEM_PROMPT_OCR

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "llava"


class OllamaVisionProvider(VisionProvider):
    def __init__(self, timeout: int = 30):
        self._timeout = timeout

    async def analizar(self, base64_img: str) -> Tuple[Optional[str], Optional[str]]:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": SYSTEM_PROMPT_OCR,
            "images": [base64_img],
            "stream": False,
        }
        try:
            resp = req_lib.post(OLLAMA_URL, json=payload, timeout=self._timeout)
            if resp.status_code == 200:
                texto = resp.json().get("response", "")
                return texto.strip() or None, None
            detalle = f"HTTP {resp.status_code}: {resp.text[:200]}"
            print(f"[OLLAMA] {detalle}")
            return None, detalle
        except req_lib.exceptions.ConnectionError as e:
            detalle = f"ConnectionError: {e}"
            print(f"[OLLAMA] {detalle}")
            traceback.print_exc()
            return None, detalle
        except Exception as e:
            detalle = f"{type(e).__name__}: {e}"
            print(f"[OLLAMA] Falló: {detalle}")
            traceback.print_exc()
            return None, detalle

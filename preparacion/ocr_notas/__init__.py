from .domain import ItemNota, NotaEntregaOCR
from .ports import VisionProvider, NotaRepository
from .service import OcrNotasService
from .adapters.vision_nvidia import NvidiaVisionProvider
from .adapters.vision_ollama import OllamaVisionProvider
from .adapters.nota_repository import SqliteNotaRepository
from .router import register_ocr_notas_routes
from .prompts import SYSTEM_PROMPT_OCR, USER_MESSAGE_OCR

__all__ = [
    "ItemNota",
    "NotaEntregaOCR",
    "VisionProvider",
    "NotaRepository",
    "OcrNotasService",
    "NvidiaVisionProvider",
    "OllamaVisionProvider",
    "SqliteNotaRepository",
    "register_ocr_notas_routes",
    "SYSTEM_PROMPT_OCR",
    "USER_MESSAGE_OCR",
]

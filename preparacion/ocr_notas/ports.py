from abc import ABC, abstractmethod
from typing import Optional, Tuple

from .domain import NotaEntregaOCR


class VisionProvider(ABC):
    @abstractmethod
    async def analizar(self, base64_img: str) -> Tuple[Optional[str], Optional[str]]:
        """Retorna (texto_respuesta, error_detalle).
        - texto_respuesta: el JSON extraído por la IA, o None si falló.
        - error_detalle: mensaje del error (None si éxito).
        """
        ...


class NotaRepository(ABC):
    @abstractmethod
    def crear_nota(self, ocr: NotaEntregaOCR) -> dict:
        ...

    @abstractmethod
    def verificar_disponibilidad(self, ocr: NotaEntregaOCR) -> list:
        ...

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional


class ItemNota(BaseModel):
    codigo_producto: str = Field(
        default="",
        description="Código interno del producto / SKU"
    )
    descripcion: str = Field(
        ...,
        description="Nombre del producto farmacéutico"
    )
    cantidad_solicitada: float = Field(
        ...,
        gt=0,
        description="Cantidad solicitada en la nota"
    )
    unidad: str = Field(
        default="UND",
        description="Unidad de empaque: UND, CAJA, BULTO, FRASCO, BLISTER, SOBRE"
    )
    ubicacion: Optional[str] = Field(
        default=None,
        description="Código de estante/piso (ej: 2AMP05-P1)"
    )

    @field_validator('unidad')
    @classmethod
    def normalizar_unidad(cls, v: str) -> str:
        m = v.upper().strip()
        equivalencias = {
            "UND": "UND", "UNIDAD": "UND", "UNID": "UND",
            "CAJA": "CAJA", "CAJ": "CAJA", "CAJAS": "CAJA",
            "BULTO": "BULTO", "BLT": "BULTO", "BULTOS": "BULTO",
            "FRASCO": "FRASCO", "FRAS": "FRASCO", "FRASCOS": "FRASCO",
            "BLISTER": "BLISTER", "BLIS": "BLISTER", "BLIST": "BLISTER",
            "SOBRE": "SOBRE", "SOB": "SOBRE", "SOBRES": "SOBRE",
        }
        return equivalencias.get(m, "UND")


class NotaEntregaOCR(BaseModel):
    numero_nota: str = Field(
        ...,
        min_length=1,
        description="Número de nota de entrega / factura"
    )
    codigo_cliente: str = Field(
        default="",
        description="Código / RIF del cliente"
    )
    nombre_cliente: str = Field(
        default="",
        description="Nombre / razón social del cliente"
    )
    items: List[ItemNota] = Field(
        default_factory=list,
        description="Listado de renglones procesados"
    )
    observaciones_manuales: Optional[str] = Field(
        default=None,
        description="Anotaciones manuscritas detectadas"
    )
    confianza_escaneo: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Índice de certeza del modelo (0.0 a 1.0)"
    )
    origen_procesamiento: str = Field(
        default="",
        description="Origen: NVIDIA_NIM_VISION | MINI_ARA_LOCAL | MANUAL_FALLBACK"
    )
    # Campos extendidos del formato Profit Plus
    almacen: Optional[str] = Field(
        default=None,
        description="Almacén / depósito de origen"
    )
    rif: Optional[str] = Field(
        default=None,
        description="RIF del cliente"
    )
    domicilio_fiscal: Optional[str] = Field(
        default=None,
        description="Domicilio fiscal del cliente"
    )
    ruta: Optional[str] = Field(
        default=None,
        description="Ruta de despacho"
    )
    zona: Optional[str] = Field(
        default=None,
        description="Zona geográfica de entrega"
    )
    vendedor: Optional[str] = Field(
        default=None,
        description="Vendedor asignado"
    )

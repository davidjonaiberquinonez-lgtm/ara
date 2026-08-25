from pydantic import BaseModel, Field
from typing import List


class ItemNota(BaseModel):
    codigo_art: str = Field(
        ...,
        min_length=1,
        description="Código del artículo (co_art)"
    )
    descripcion: str = Field(
        default="",
        description="Descripción del artículo (art_des)"
    )
    cantidad: float = Field(
        ...,
        gt=0,
        description="Cantidad solicitada (total_art)"
    )
    cantidad_escaneada: float = Field(
        default=0,
        ge=0,
        description="Cantidad real escaneada/surtida en Profit (stotal_art/cant_imp/cant_prod)"
    )
    estatus: str = Field(
        default="PENDIENTE",
        description="Estatus operativo del renglón: PENDIENTE | ESCANEADO | COMPLETO"
    )
    unidad: str = Field(
        default="UND",
        description="Unidad de medida: UND, CAJA, SOBRE, BLISTER, BULTO"
    )
    campo7: str = Field(
        default="",
        description="Ubicación real en Profit (campo7 de reng_nd), nota S/C"
    )
    reng_nd: int = Field(
        default=0,
        description="Número de renglón (reng_num de reng_nde/reng_fac): pareo ítem por ítem del cotejo hexagonal"
    )
    co_alma: str = Field(
        default="01",
        description="Almacén de despacho del renglón (co_alma en Profit): '01'/'02' S/C, '04' BQTO"
    )


class NotaPreparacion(BaseModel):
    codigo_nota: str = Field(
        ...,
        min_length=1,
        description="Número de nota / factura (código de barras)"
    )
    codigo_cliente: str = Field(
        default="",
        description="Código del cliente (co_cli)"
    )
    nombre_cliente: str = Field(
        default="",
        description="Nombre / razón social del cliente (cli_des)"
    )
    items: List[ItemNota] = Field(
        default_factory=list,
        description="Renglones de la nota"
    )
    preparado: bool = Field(
        default=False,
        description="True si la nota ya fue preparada"
    )
    estado: str = Field(
        default="",
        description="Estado en BD local: preparando | preparada | chequeada | ..."
    )
    almacen_origen: str = Field(
        default="",
        description="Almacén de origen: 'SAN_CRISTOBAL' para notas S/C (serie 'A...'), '' para Barquisimeto"
    )


class NotaNotFoundError(Exception):
    """La nota no existe en la fuente externa (Legacy PHP / Profit)."""

    def __init__(self, codigo_nota: str = "", mensaje: str = ""):
        super().__init__(
            mensaje
            or f"La nota {codigo_nota} no está registrada en la tabla rep_not del sistema."
        )
        self.codigo_nota = codigo_nota


class NotaNotVerifiedError(Exception):
    """La fuente externa no pudo verificar la nota (respuesta no reconocida, error HTTP, timeout)."""


class NotaAlreadyPreparedError(Exception):
    """La nota ya fue registrada / preparada en el sistema."""

    def __init__(self, codigo_nota: str = ""):
        super().__init__(f"La nota {codigo_nota} ya fue registrada y no puede duplicarse.")
        self.codigo_nota = codigo_nota

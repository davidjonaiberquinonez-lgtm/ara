from pydantic import BaseModel, Field
from typing import List, Optional


class ItemRuta(BaseModel):
    nota_num: str = Field(
        default="",
        description="Número de la nota de entrega / caja (barcode de caja). "
                    "Vacío para ítems SOLO_FACTURA."
    )
    factura_num: str = Field(
        default="",
        description="Número de factura asociada (barcode de factura)"
    )
    num_fact: Optional[str] = Field(
        default=None,
        description="Número de factura REAL totalizada en Profit ERP (CRISTM25), "
                    "resuelto por el JOIN not_ent <-> reng_fac <-> factura. "
                    "None = nota sin factura totalizada asociada ('Sin Factura'); "
                    "NUNCA se iguala a la nota."
    )
    tipo_documento: str = Field(
        default="PEDIDO",
        description="Tipo de documento: PEDIDO | NOTA_CREDITO | SOLO_FACTURA"
    )
    is_invoice_only: int = Field(
        default=0,
        description="1 si es SOLO_FACTURA (sin nota de venta previa): omite el "
                    "conteo de cajas físicas y se coteja directo a 1-1 con su factura"
    )
    has_credit_notes: int = Field(
        default=0,
        description="1 si el ítem es una nota de crédito (NC)"
    )
    sede_origen: str = Field(
        default="",
        description="Sede real del documento ('SC'|'BQTO'), resuelta vía co_sucu en "
                    "Profit (not_ent/factura/dev_cli). Vacío si Profit no respondió o "
                    "la nota no se encontró — nunca se adivina por rango de número."
    )
    sub_rutagrama_id: str = Field(
        default="",
        description="Código de Sub-Rutagrama individual que vincula el pedido/nota "
                    "con el vendedor y la sub-ruta (ej: RGT-87813-2)"
    )
    sub_ruta: str = Field(
        default="",
        description="Nombre exacto de la sub-ruta (ej: CARACAS BAJA CHARALLAVE)"
    )
    ruta_macro: str = Field(
        default="",
        description="Ruta macro que originó la consolidación (ej: CARACAS)"
    )
    codigo_cliente: str = Field(
        default="",
        description="Código del cliente (columna 'Codigo' de la tabla 'Notas por cargar' de lista.php)"
    )
    razon_social: str = Field(
        default="",
        description="Razón social del cliente"
    )
    estado_ara: str = Field(
        default="pendiente",
        description="Estado en ARA (semáforo): pendiente | preparando | preparada | chequeada | embalada"
    )
    estado_legacy: str = Field(
        default="",
        description="Estado crudo del visor/Profit (ej: 'Procesada', 'S/P', 'T', 'P'). "
                    "Usado por el cotejamiento estricto: se omite 'P', se exige 'T'."
    )
    scanned_caja: bool = Field(
        default=False,
        description="True si la caja fue escaneada en la sesión actual"
    )
    scanned_factura: bool = Field(
        default=False,
        description="True si la factura fue escaneada en la sesión actual"
    )
    scan_lote: bool = Field(
        default=False,
        description="Matriz 1-0: la caja fue escaneada en el lote unificado de la ruta"
    )
    verificado: bool = Field(
        default=False,
        description="Matriz 1-1: la factura fue verificada contra la sub-ruta asignada por el bot"
    )
    paquetes: int = Field(
        default=0,
        ge=0,
        description="Cantidad de paquetes / bultos de la nota"
    )
    peso: float = Field(
        default=0.0,
        ge=0,
        description="Peso en Kg de la nota, si el legacy/Profit lo expone. 0.0 = sin "
                    "dato real disponible (el rutagrama impreso lo muestra como '—', "
                    "nunca se inventa un peso)."
    )
    fecha_creacion: str = Field(
        default="",
        description="Fecha/hora real de creación del pedido (columna 'Creada' de "
                    "lista.php) — se muestra en la tarjeta individual de cada "
                    "pedido. Vacío si el legacy no la trae."
    )

    @property
    def matriz_estado(self) -> str:
        """Matriz de estados de cotejo: '0-0' no escaneada · '1-0' escaneada en lote · '1-1' verificada."""
        if self.scan_lote and self.verificado:
            return "1-1"
        if self.scan_lote:
            return "1-0"
        return "0-0"

    @property
    def estado_cotejo(self) -> str:
        """Alias canónico de la matriz de cotejo (0-0 → 1-0 → 1-1)."""
        return self.matriz_estado

    @property
    def nota(self) -> str:
        """Alias canónico de nota_num (número de nota de entrega / caja)."""
        return self.nota_num

    @property
    def factura(self) -> str:
        """Alias canónico de factura_num (número de factura)."""
        return self.factura_num


class DatosVehiculo(BaseModel):
    ayudantes: str = Field(
        default="",
        description="IDs de asistentes separados por punto (ej: 10.15.22). "
                    "Formato exigido por el cierre real del visor legacy "
                    "(POST registro.php: rg, ayudantes, chofer, carro)."
    )
    chofer: str = Field(
        default="",
        description="Nombre del conductor. Mismo campo que exige el cierre real del visor."
    )
    carro: str = Field(
        default="",
        description="Credenciales del vehículo (placa / unidad). Mismo campo que "
                    "exige el cierre real del visor (POST registro.php: campo 'carro')."
    )
    clave_visor: str = Field(
        default="",
        description="Clave de seguridad del visor para el cierre (registro.php). "
                    "Solo viaja en el POST; nunca se loguea ni imprime."
    )
    # ── Campos adicionales SOLO para el rutagrama impreso (REPOLAB) ──────────
    # El cierre real en el visor legacy solo exige ayudantes/chofer/carro
    # (arriba); estos campos son opcionales, exclusivos para que el documento
    # impreso (repolab_printer.js) quede con el nivel de detalle del formato
    # A4 de referencia — nunca se envían al visor legacy.
    responsable_id: str = Field(default="", description="ID del responsable de almacén (impreso)")
    responsable_nombre: str = Field(default="", description="Nombre del responsable de almacén (impreso)")
    chofer_id: str = Field(default="", description="ID interno del chofer (impreso)")
    chofer_cedula: str = Field(default="", description="Cédula del chofer (impreso)")
    ayudante_id: str = Field(default="", description="ID interno del ayudante de despacho (impreso)")
    ayudante_nombre: str = Field(default="", description="Nombre del ayudante de despacho (impreso)")
    ayudante_cedula: str = Field(default="", description="Cédula del ayudante de despacho (impreso)")
    vehiculo_descripcion: str = Field(default="", description="Marca/modelo/unidad del vehículo (impreso, ej: 'CHEVROLET NHR (UNIDAD #01)')")
    vehiculo_placa: str = Field(default="", description="Placa del vehículo (impreso)")
    vehiculo_intt: str = Field(default="", description="Número de credencial INTT del vehículo (impreso)")
    hora_prog_salida: str = Field(default="", description="Hora programada de salida (impreso, ej: '06:30 AM')")


class SubRutaFinalizada(BaseModel):
    id: Optional[int] = Field(default=None, description="ID en BD local")
    guia: str = Field(
        default="",
        description="Número de guía generado para la sub-ruta"
    )
    rutagrama_padre: str = Field(
        default="",
        description="Rutagrama Macro de la consolidación (ej: RGT-87813)"
    )
    sub_rutagrama: str = Field(
        default="",
        description="Rutagrama individual de la sub-ruta (ej: RGT-87813-2)"
    )
    sub_ruta: str = Field(
        default="",
        description="Nombre exacto de la sub-ruta"
    )
    items: List[ItemRuta] = Field(
        default_factory=list,
        description="Notas / facturas pertenecientes SOLO a esta sub-ruta"
    )
    datos_vehiculo: DatosVehiculo = Field(
        default_factory=DatosVehiculo,
        description="Datos del vehículo (chofer, asistentes, credenciales)"
    )
    responsable_id: str = Field(
        default="",
        description="ID del usuario responsable de la ruta"
    )
    fecha: Optional[str] = Field(default=None)


# =============================================================================
# MACRO-RUTAS MULTI-SEDE (embalaje → despacho por escaneo de facturas)
# -----------------------------------------------------------------------------
# La sesión del usuario determina la sede ('SC' | 'BQTO'). El embalaje alimenta
# la Macro-Ruta ACTIVA de dicha sede y el despacho opera escaneando Facturas.
# Al cerrar la Macro-Ruta se dividen automáticamente los Rutagramas/Sub-rutas
# por zona de destino.
# =============================================================================

# Estados del ítem dentro de la Macro-Ruta.
ESTADO_ITEM_PENDIENTE = "PENDIENTE"
ESTADO_ITEM_DESPACHADO = "DESPACHADO"

# Estados de la Macro-Ruta.
ESTADO_MACRO_ACTIVA = "ACTIVA"
ESTADO_MACRO_CERRADA = "CERRADA"


class ItemMacroRuta(BaseModel):
    num_nota: str = Field(
        default="",
        description="Número de nota / pedido empacado (llave del ítem)"
    )
    num_factura: Optional[str] = Field(
        default=None,
        description="Número de factura real (puede llegar con el evento de embalaje)"
    )
    co_cli: str = Field(
        default="",
        description="Código del cliente"
    )
    razon_social: str = Field(
        default="",
        description="Razón social del cliente"
    )
    total_bultos: int = Field(
        default=0,
        ge=0,
        description="Total de bultos empacados (heredado del cierre de embalaje)"
    )
    estado_despacho: str = Field(
        default=ESTADO_ITEM_PENDIENTE,
        description="PENDIENTE | DESPACHADO"
    )
    zona_destino: str = Field(
        default="",
        description="Zona / sub-ruta de destino asignada (base para la división automática)"
    )
    operador_embalaje: str = Field(
        default="",
        description="Operador que empacó la nota"
    )
    fecha_embalaje: Optional[str] = Field(default=None)
    fecha_despacho: Optional[str] = Field(default=None)
    despachado_por: str = Field(
        default="",
        description="Usuario que escaneó la factura en despacho"
    )


class MacroRuta(BaseModel):
    id: str = Field(
        default="",
        description="Código de la Macro-Ruta (ej: MACRO-SC-20260806)"
    )
    sede_id: str = Field(
        default="BQTO",
        description="Sede operativa: 'SC' (San Cristóbal) | 'BQTO' (Barquisimeto)"
    )
    fecha: str = Field(
        default="",
        description="Fecha de creación (YYYYMMDD)"
    )
    estado: str = Field(
        default=ESTADO_MACRO_ACTIVA,
        description="ACTIVA | CERRADA"
    )
    notas: List[ItemMacroRuta] = Field(
        default_factory=list,
        description="Ítems empacados de la Macro-Ruta"
    )

    def agregar_o_actualizar_nota(
        self,
        num_nota: str,
        num_factura: Optional[str] = None,
        co_cli: str = "",
        razon_social: str = "",
        total_bultos: int = 0,
        zona_destino: str = "",
        operador_embalaje: str = "",
    ) -> None:
        """Agrega la nota empacada o actualiza su conteo/bultos (idempotente).

        La nota ya DESPACHADA conserva su estado: el re-embalaje posterior al
        despacho no la regresa a PENDIENTE.
        """
        for it in self.notas:
            if it.num_nota == str(num_nota).strip():
                it.total_bultos = max(int(total_bultos or 0), it.total_bultos)
                if num_factura:
                    it.num_factura = str(num_factura).strip()
                if co_cli:
                    it.co_cli = str(co_cli).strip()
                if razon_social:
                    it.razon_social = str(razon_social).strip()
                if zona_destino:
                    it.zona_destino = str(zona_destino).strip()
                if operador_embalaje:
                    it.operador_embalaje = str(operador_embalaje).strip()
                return
        self.notas.append(
            ItemMacroRuta(
                num_nota=str(num_nota).strip(),
                num_factura=str(num_factura or "").strip() or None,
                co_cli=str(co_cli or "").strip(),
                razon_social=str(razon_social or "").strip(),
                total_bultos=int(total_bultos or 0),
                zona_destino=str(zona_destino or "").strip(),
                operador_embalaje=str(operador_embalaje or "").strip(),
            )
        )

    def buscar_por_codigo(self, codigo: str):
        """Ítem cuyo num_nota o num_factura coincida con el código (tolerante a dígitos).

        Tolera además códigos impresos con letra de serie + 1 dígito
        reemplazando el prefijo real '72' (ej. 'A2154234' -> '72154234',
        'B0068080' -> '72068080' — verificado en vivo 2026-08-20 contra
        rep_not con DOS letras distintas, regla general no específica de
        una letra: se quitan los primeros 2 caracteres y se reemplazan por
        '72', quedan los últimos 6 dígitos igual)."""
        import re
        codigo = str(codigo or "").strip()
        if not codigo:
            return None
        codigo_up = codigo.upper()
        m = re.match(r"^[A-Z](\d{7})$", codigo_up)
        m71 = re.match(r"^71(\d{6})$", codigo_up)
        candidatos = {codigo}
        if m:
            candidatos.add("72" + m.group(1)[1:])
        if m71:
            candidatos.add("72" + m71.group(1))
        for it in self.notas:
            if it.num_nota in candidatos or (it.num_factura and it.num_factura in candidatos):
                return it
        dig = re.sub(r"\D", "", codigo).lstrip("0")
        if not dig:
            return None
        for it in self.notas:
            n = re.sub(r"\D", "", it.num_nota).lstrip("0")
            f = re.sub(r"\D", "", it.num_factura or "").lstrip("0")
            if n == dig or (f and f == dig):
                return it
        return None


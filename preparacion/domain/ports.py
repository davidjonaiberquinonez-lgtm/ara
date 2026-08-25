from abc import ABC, abstractmethod
from typing import Optional

from .models import NotaPreparacion


class PreparationRepositoryPort(ABC):
    """Puerto de acceso a la fuente externa de notas (Legacy PHP o SQL Profit)."""

    @abstractmethod
    def get_nota_by_barcode(self, codigo_barra: str) -> NotaPreparacion:
        """Consulta la nota en la fuente externa.

        Lanza NotaNotFoundError si no existe o NotaNotVerifiedError si no se pudo verificar.
        """
        ...

    @abstractmethod
    def assign_preparer(self, codigo_barra: str, numero_preparador: str, cant_items: int) -> dict:
        """Notifica a la fuente externa el preparador asignado a la nota."""
        ...

    @abstractmethod
    def registrar_despacho_legacy(
        self,
        codigo_barra: str,
        numero_preparador: str,
        cant_items: int,
        renglones_escaneados: Optional[list] = None,
    ) -> dict:
        """Fast-Track: registra el despacho en el endpoint Legacy (registro.php).

        `renglones_escaneados` (opcional) = list[{co_art, cantidad}] con las
        cantidades reales escaneadas para el sync por ítem de reng_nde antes
        del cierre. Se invoca solo para notas < UMBRAL_CHEQUEO (auto-chequeo).
        Retorna {"status": str, "respuesta": str, "aviso_legacy": Optional[str]}.
        """
        ...

    @abstractmethod
    def registrar_autochequeo_php(
        self,
        numero_nota: str,
        usuario_id: str,
        mesa: str = "99",
        estado: str = "AUTOCHEQUEO",
        hora: Optional[str] = None,
        renglones_escaneados: Optional[list] = None,
    ) -> dict:
        """Fast-Track (< UMBRAL_CHEQUEO): registra el AUTOCHEQUEO en el endpoint
        PHP real de chequeo (chequeo/registro.php) para que gestion.php llene
        de inmediato 'Registro de Chequeo', 'Número de Chequeador' y 'Hora del
        Registro' con la ID del preparador activo.

        `renglones_escaneados` (opcional) = list[{co_art, cantidad}] con las
        cantidades reales escaneadas para el sync por ítem de reng_nde antes
        del POST. NUNCA lanza excepción ni bloquea la transacción local: usa
        timeout corto (2-3s) y devuelve {"status", "respuesta", "aviso_legacy"}.
        """
        ...


class NotaLocalStorePort(ABC):
    """Puerto de persistencia local en SQLite (tablas notas_entrega / detalle_nota)."""

    @abstractmethod
    def find_by_barcode(self, codigo_barra: str) -> Optional[dict]:
        """Retorna el encabezado + items de la nota local, o None si no existe."""
        ...

    @abstractmethod
    def create_nota(self, nota: NotaPreparacion, estado: str) -> dict:
        """Inserta el encabezado de la nota en notas_entrega. Retorna {id, numero_nota, estado}."""
        ...

    @abstractmethod
    def insert_items(self, nota_id: int, nota: NotaPreparacion) -> None:
        """Inserta los renglones (con co_art) en detalle_nota y actualiza items_count."""
        ...

    @abstractmethod
    def finalize_nota(self, nota_id: int, estado: str, preparador_id: str) -> dict:
        """Actualiza estado + preparador_id + fecha_completada y marca items como preparados."""
        ...

    @abstractmethod
    def insert_movimiento(
        self,
        nota_id: int,
        nota: NotaPreparacion,
        usuario: str,
        accion: str,
        origen: str,
        destino: str,
    ) -> None:
        """Registra trazabilidad en movimientos_preparador (un registro por item)."""
        ...

    @abstractmethod
    def enriquecer_con_stock(self, items: list) -> list:
        """Enriquece cada item {co_art/codigo_art, ...} con datos de stock_maestro:
        - 'ubicacion': campo7 (o 'POR_ASIGNAR' si NULL/vacío)
        - 'codigo_barra': columna codigo_barra en minúsculas
        """
        ...

    @abstractmethod
    def resolver_id_usuario(self, identificador: Optional[str]) -> Optional[str]:
        """Resuelve el id real del operador (columna id de usuarios) desde un id o nombre.

        Retorna None si el valor es un default ('0', 'null', 'admin1') o no existe.
        """
        ...

    @abstractmethod
    def registrar_puntos_preparacion(self, usuario_id: str, referencia_id: str, cant_items: int) -> dict:
        """Registra puntos de PREPARACIÓN en log_puntos (modulo='picking', 1.0 pt/renglón).

        Anti-duplicado por (usuario, modulo='picking', referencia_id). Retorna
        {"registrado": bool, "puntos": float, "motivo": str}.
        """
        ...

    @abstractmethod
    def registrar_puntos_chequeo(self, usuario_id: str, referencia_id: str, cant_items: int) -> dict:
        """Registra puntos de CHEQUEO en log_puntos (modulo='chequeo', 1.0 pt/renglón).

        Se invoca en el Fast-Track para otorgar doble puntuación (PREPARACION + CHEQUEO)
        a las notas auto-chequeadas (< UMBRAL_CHEQUEO). Anti-duplicado por
        (usuario, modulo='chequeo', referencia_id). Retorna
        {"registrado": bool, "puntos": float, "motivo": str}.
        """
        ...

from abc import ABC, abstractmethod
from typing import List, Optional

from .models import ItemRuta, SubRutaFinalizada


class IncompleteScanError(Exception):
    """Bloqueo estricto: quedan cajas/facturas pendientes por verificar (estado 1-1) antes de cerrar la ruta."""

    def __init__(
        self,
        mensaje: str,
        faltan_cajas: int = 0,
        faltan_facturas: int = 0,
        faltan_verificar: int = 0,
    ):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.faltan_cajas = faltan_cajas
        self.faltan_facturas = faltan_facturas
        self.faltan_verificar = faltan_verificar


class RouteLegacyPort(ABC):
    """Puerto de acceso a la fuente externa (PHP Legacy /visor/lista.php, /visor/registro.php, /visor/index.php)."""

    @abstractmethod
    def rutas_disponibles(self) -> List[str]:
        """Retorna los nombres de las macro-rutas activas disponibles para despacho (ej: ['CARACAS', 'ARAGUA'])."""
        ...

    @abstractmethod
    def fetch_catalogo_rutas_legacy(self) -> List[tuple]:
        """GET /visor/index.php y parsea las opciones de <select name="ruta">.

        Retorna una lista de pares (codigo_ruta, nombre_sub_ruta) con el nombre
        limpio (ej: [('10', 'ARAGUA - ZARAZA'), ('000145', 'CARACAS - BAJA CHARALLAVE')]).
        Si el HTML no está disponible retorna [] (el servicio aplica el respaldo estático).
        """
        ...

    @abstractmethod
    def catalogo_estatico(self) -> dict:
        """Catálogo de respaldo: Macro-Rutas → sub-rutas conocidas, sin HTTP."""
        ...

    @abstractmethod
    def sub_rutas_de_macro(self, ruta_macro: str) -> List[str]:
        """Retorna las sub-rutas reales asociadas a una macro-ruta (usadas por el bot de clasificación)."""
        ...

    @abstractmethod
    def fetch_ruta_macro(self, ruta_macro: str) -> List[ItemRuta]:
        """Obtiene todas las cajas/facturas de la ruta macro y sus sub-rutas asociadas."""
        ...

    @abstractmethod
    def confirmar_registro(self, barcode: str, tipo_escaneo: str, **extra) -> dict:
        """Confirma el registro (caja|factura|despacho) en el sistema Legacy.

        Para cierre de despacho, extra acepta: guia, sub_ruta, nota, ayudantes,
        chofer, carro (credenciales del vehículo).
        """
        ...


class RouteRepositoryPort(ABC):
    """Puerto de persistencia local SQLite (sub_rutas_finalizadas + subruta_items)."""

    @abstractmethod
    def get_ruta_asignada(self, user_id: str) -> Optional[str]:
        """Retorna la ruta macro asignada al usuario (columna ruta_asignada)."""
        ...

    @abstractmethod
    def get_estado_ara(self, factura_num: str) -> str:
        """Retorna el estado de la nota en notas_entrega (semáforo) o 'pendiente'."""
        ...

    @abstractmethod
    def save_subrutas(self, subrutas: List[SubRutaFinalizada]) -> List[dict]:
        """Guarda cada sub-ruta finalizada con sus items. Retorna las sub-rutas guardadas."""
        ...

    @abstractmethod
    def get_rutas_finalizadas_by_user(self, user_id: str) -> List[dict]:
        """Consulta sub-rutas finalizadas del usuario, con sus items, para reportes."""
        ...

    @abstractmethod
    def get_rutas_finalizadas_todas(self, usuario_filtro: Optional[str] = None) -> List[dict]:
        """Auditoría admin: todas las sub-rutas finalizadas de TODOS los operadores.

        `usuario_filtro`: si se pasa (y no es None/"Todos"), limita a un
        responsable_id específico — mismo criterio que el resto de reportes
        con RBAC admin (discrepancias/trazabilidad)."""
        ...

    @abstractmethod
    def buscar_notas_locales(self, patron: str) -> List[dict]:
        """Búsqueda local (notas_entrega) con LIKE %patron% sobre numero_nota/cliente/co_cli.

        Usada como fallback cuando el Legacy no devuelve items para la ruta macro.
        Retorna dicts con las claves: nota_num, factura_num, sub_ruta, razon_social,
        estado_ara, paquetes.
        """
        ...

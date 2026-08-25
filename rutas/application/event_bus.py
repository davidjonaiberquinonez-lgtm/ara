"""Bus de Eventos interno del módulo de Rutas (Event Dispatcher).

Desacopla la capa de Embalaje de la capa de Rutas: el cierre de un embalaje
emite el evento 'embalaje.finalizado' y el RouteService (suscrito) vincula la
nota empacada al Rutagrama activo sin que el embalaje conozca los detalles
del servicio de rutas.

Los handlers se ejecutan de forma SÍNCRONA (llamado directo en el mismo
request): el embalaje recibe el resultado de la vinculación para exponerlo
en su respuesta. El bus es tolerante a fallos: si un handler lanza, el
evento NO se propaga al resto y el error queda registrado en consola.
"""

import traceback
from typing import Any, Callable, Dict, List

Handler = Callable[[str, dict], Any]


class EventBus:
    """Dispatcher síncrono de eventos con suscripción por nombre de evento."""

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Handler]] = {}

    def suscribir(self, evento: str, handler: Handler) -> None:
        """Registra un handler para un evento ('embalaje.finalizado', ...)."""
        self._handlers.setdefault(evento, []).append(handler)

    def emitir(self, evento: str, payload: dict) -> list:
        """Emite el evento a todos sus handlers (orden de suscripción).

        Retorna la lista de resultados (None si un handler no devolvió nada).
        Un handler fallido NO detiene a los demás: se captura y se loguea.
        """
        resultados = []
        for handler in self._handlers.get(evento, []):
            try:
                resultados.append(handler(evento, payload))
            except Exception as e:
                print(f"[EventBus] Handler de '{evento}' falló: {e}")
                traceback.print_exc()
                resultados.append(None)
        return resultados

    def handler_count(self, evento: str) -> int:
        return len(self._handlers.get(evento, []))


_bus_instancia: EventBus | None = None


def get_event_bus() -> EventBus:
    """Singleton del bus compartido (mismo bus en embalaje y rutas)."""
    global _bus_instancia
    if _bus_instancia is None:
        _bus_instancia = EventBus()
    return _bus_instancia

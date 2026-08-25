"""Skill de Auditoría — Detector de Mal Surtido en Logs de Bulto Cerrado.

Parseo de los archivos de traza ``LOG_REPORTE`` generados por las estaciones
de lectura de Bulto Cerrado. Compara el código de barras SOLICITADO (nota de
traslado/pedido) contra el ESCANEADO por el operario; cada discrepancia se
registra en la matriz de errores ``{operario_id, timestamp, sku_pedido,
sku_escaneado, estacion}``, marca la interrupción del cierre de la nota y
dispara alertas sonora/visual hacia la interfaz.

Formato de línea soportado (varias variantes, separadores ``|`` / CSV):

    # Formato clave=valor (recomendado, generado por las estaciones):
    2026-08-07 10:00:05 | OPERARIO=03 | PEDIDO=841234567890 | ESCANEADO=841199990001 | ESTACION=BC-01 | ERROR

    # Formato CSV: timestamp,operario_id,solicitado,escaneado,estacion
    2026-08-07 10:00:05,03,841234567890,841199990001,BC-01

Reglas de negocio: la discrepancia se detecta cuando ``solicitado != escaneado``
y ambos no son vacíos; líneas sin campos de barras se ignoran sin error.
Best-effort absoluto: el parser nunca lanza ante líneas corruptas.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from skills import base

# Claves válidas del formato clave=valor (case-insensitive).
_CLAVES_VALIDAS = ("operario", "usuario", "responsable", "pedido", "solicitado",
                   "esperado", "escaneado", "leido", "estacion", "estacion_id", "nota")

_PATRON_KV = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})?.*?"
    r"(?P<claves>(?:[A-Za-z_]+=[^|,;\s][^|;]*?)(?:\s*[|,;]\s*[A-Za-z_]+=[^|;]*?)*)"
)

_PATRON_CSV = re.compile(
    r"^\s*(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s*[,;]\s*"
    r"(?P<operario>[^,;]+)\s*[,;]\s*"
    r"(?P<solicitado>[^,;]+)\s*[,;]\s*"
    r"(?P<escaneado>[^,;]+)\s*[,;]\s*"
    r"(?P<estacion>[^,;]+)\s*$"
)


@dataclass
class EventoDiscrepancia:
    """Un mal surtido detectado en la traza (matriz de errores)."""

    operario_id: str
    timestamp: str
    sku_pedido: str
    sku_escaneado: str
    estacion: str
    nota: str = ""
    linea: int = 0


@dataclass
class ResultadoAnalisis:
    """Resultado del análisis de un archivo LOG_REPORTE."""

    ruta: str
    eventos: List[EventoDiscrepancia] = field(default_factory=list)
    lineas_procesadas: int = 0
    lineas_ok: int = 0
    lineas_discrepantes: int = 0
    interrumpir_cierre: bool = False
    aviso: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ruta": self.ruta,
            "lineas_procesadas": self.lineas_procesadas,
            "lineas_ok": self.lineas_ok,
            "lineas_discrepantes": self.lineas_discrepantes,
            "interrumpir_cierre": self.interrumpir_cierre,
            "aviso": self.aviso,
            "eventos": [
                {
                    "operario_id": e.operario_id,
                    "timestamp": e.timestamp,
                    "sku_pedido": e.sku_pedido,
                    "sku_escaneado": e.sku_escaneado,
                    "estacion": e.estacion,
                    "nota": e.nota,
                    "linea": e.linea,
                }
                for e in self.eventos
            ],
        }


def _parsear_linea(linea: str, nro_linea: int) -> Optional[EventoDiscrepancia]:
    """Convierte una línea del LOG_REPORTE en un evento (None si no aplica)."""
    linea = linea.strip()
    if not linea or linea.startswith("#"):
        return None

    # ── Variante CSV: ts,operario,solicitado,escaneado,estacion ──────────────
    m = _PATRON_CSV.match(linea)
    if m:
        datos = {
            "ts": m.group("ts"),
            "operario": m.group("operario").strip(),
            "solicitado": m.group("solicitado").strip(),
            "escaneado": m.group("escaneado").strip(),
            "estacion": m.group("estacion").strip(),
        }
    else:
        # ── Variante clave=valor ─────────────────────────────────────────────
        partes = re.split(r"[|;,]", linea)
        datos: Dict[str, str] = {}
        timestamp = ""
        for parte in partes:
            parte = parte.strip()
            if not parte:
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$", parte):
                timestamp = parte
                continue
            if "=" in parte:
                clave, valor = parte.split("=", 1)
                c = clave.strip().lower()
                if c in _CLAVES_VALIDAS:
                    datos[c] = valor.strip()
        datos["ts"] = timestamp

    solicitado = datos.get("solicitado") or datos.get("pedido") or ""
    escaneado = datos.get("escaneado") or datos.get("leido") or ""
    operario = datos.get("operario") or datos.get("usuario") or datos.get("responsable") or ""
    estacion = datos.get("estacion") or datos.get("estacion_id") or ""
    nota = datos.get("nota") or ""

    if not solicitado or not escaneado:
        return None  # línea de traza sin barras (ok, ignorada)

    if solicitado == escaneado:
        return None  # surtido correcto

    return EventoDiscrepancia(
        operario_id=operario or "DESCONOCIDO",
        timestamp=datos.get("ts") or time.strftime("%Y-%m-%d %H:%M:%S"),
        sku_pedido=solicitado,
        sku_escaneado=escaneado,
        estacion=estacion or "DESCONOCIDA",
        nota=nota,
        linea=nro_linea,
    )


def analizar_log_surtido(ruta_log: str) -> Dict[str, Any]:
    """Analiza un archivo LOG_REPORTE completo y reporta los mal surtidos.

    Parámetros:
        ruta_log (str): ruta del archivo de traza (o directorio: se procesan
            todos los ``*.log`` / ``LOG_REPORTE*`` en orden alfabético).

    Retorna ``ResultadoAnalisis.to_dict()``: matriz de errores (eventos),
    conteos y ``interrumpir_cierre=True`` si hubo al menos una discrepancia.
    Nunca lanza; errores de lectura se reflejan en ``aviso``.
    """
    resultado = ResultadoAnalisis(ruta=ruta_log)
    if not os.path.exists(ruta_log):
        resultado.aviso = f"La ruta del log no existe: {ruta_log}"
        return resultado.to_dict()

    rutas: List[str]
    if os.path.isdir(ruta_log):
        rutas = sorted(
            p for p in os.listdir(ruta_log)
            if os.path.isfile(os.path.join(ruta_log, p))
            and (p.endswith(".log") or p.startswith("LOG_REPORTE"))
        )
        rutas = [os.path.join(ruta_log, p) for p in rutas]
        if not rutas:
            resultado.aviso = "No se encontraron archivos *.log/LOG_REPORTE* en el directorio."
            return resultado.to_dict()
    else:
        rutas = [ruta_log]

    for ruta in rutas:
        try:
            with open(ruta, encoding="utf-8", errors="replace") as f:
                lineas = f.readlines()
        except Exception as e:
            resultado.aviso = (resultado.aviso + " | " if resultado.aviso else "") + (
                f"No se pudo leer {ruta}: {e}"
            )
            continue

        for i, linea in enumerate(lineas, start=1):
            resultado.lineas_procesadas += 1
            evento = _parsear_linea(linea, i)
            if evento is None:
                resultado.lineas_ok += 1
                continue
            resultado.eventos.append(evento)
            resultado.lineas_discrepantes += 1

    resultado.interrumpir_cierre = resultado.lineas_discrepantes > 0
    if resultado.eventos:
        resultado.aviso = (
            f"{len(resultado.eventos)} discrepancia(s) detectada(s): "
            "se interrumpe el cierre de la nota hasta corrección."
        )
    return resultado.to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# Alerta a la interfaz (sonora/visual) e interrupción del cierre
# ─────────────────────────────────────────────────────────────────────────────

def emitir_alerta(evento: Dict[str, Any], sonora: bool = True) -> Dict[str, Any]:
    """Alerta sonora/visual por un evento de mal surtido (best-effort).

    Sonora: beep de error vía ``winsound`` (Windows) o terminal bell.
    Visual: estructura JSON lista para la interfaz (no depende de framework).

    Retorna el payload de alerta listo para el frontend.
    """
    try:
        if sonora:
            try:
                import winsound

                winsound.Beep(880, 300)
                winsound.Beep(440, 300)
            except Exception:
                print("\a", end="", flush=True)
    except Exception:
        pass

    payload: Dict[str, Any] = {
        "tipo": "MAL_SURTIDO",
        "severidad": "ALTA",
        "accion": "INTERRUMPIR_CIERRE",
        "mensaje": (
            f"Mal surtido detectado en {evento.get('estacion', '?')} por "
            f"operario {evento.get('operario_id', '?')}: se esperaba "
            f"{evento.get('sku_pedido', '?')} y se escaneó "
            f"{evento.get('sku_escaneado', '?')}."
        ),
        "evento": evento,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    base._print_sync("🔊 ALERTA:", payload["mensaje"])
    return payload


def bloquear_cierre_nota(numero_nota: str, evento: Dict[str, Any]) -> Dict[str, Any]:
    """Devuelve el bloqueo de cierre para la nota (interfaz decide aplicarlo).

    No escribe en BD: expone la decisión de interrupción con el motivo y el
    evento responsable, para que la capa de persistencia marque la nota como
    ``EN_REVISION`` y no cierre el surtido.
    """
    return {
        "nota": numero_nota,
        "permitir_cierre": False,
        "motivo": "mal_surtido_pendiente_correccion",
        "evento_responsable": evento,
        "siguiente_paso": "Reescaneo correcto del SKU solicitado antes de cerrar.",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Vigilancia continua (parsing continuo de la traza)
# ─────────────────────────────────────────────────────────────────────────────

def vigilar_log_surtido(
    ruta_log: str,
    callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    intervalo_s: float = 1.0,
    detener: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Vigilancia continua incremental del LOG_REPORTE (polling).

    Lee solo las líneas nuevas desde la última pasada (offset por bytes).
    Por cada discrepancia nueva invoca ``callback(evento)`` y emite la alerta.
    La vigilancia termina cuando ``detener()`` retorna True (o al primer error
    de lectura).

    Parámetros:
        ruta_log (str): archivo o directorio (se vigila el más reciente).
        callback: función que recibe el dict del evento (para interfaz).
        intervalo_s: segundos entre pasadas (mínimo 0.2).
        detener: predicado opcional para cortar el bucle desde fuera.

    Retorna dict resumen al terminar (nunca lanza).
    """
    resumen: Dict[str, Any] = {"eventos_nuevos": 0, "pasadas": 0, "activo": True, "aviso": ""}

    def _ruta_activa() -> str:
        if os.path.isfile(ruta_log):
            return ruta_log
        if os.path.isdir(ruta_log):
            logs = sorted(
                (
                    os.path.join(ruta_log, p)
                    for p in os.listdir(ruta_log)
                    if p.endswith(".log") or p.startswith("LOG_REPORTE")
                ),
                key=os.path.getmtime,
                reverse=True,
            )
            return logs[0] if logs else ""
        return ""

    offset = 0
    try:
        while True:
            if detener is not None and detener():
                break
            ruta = _ruta_activa()
            if not ruta:
                resumen["aviso"] = "Sin archivos de log que vigilar."
                break
            try:
                tamano = os.path.getsize(ruta)
                if tamano > offset:
                    with open(ruta, encoding="utf-8", errors="replace") as f:
                        f.seek(offset)
                        lineas = f.readlines()
                    offset = tamano
                    for i, linea in enumerate(lineas, start=1):
                        evento = _parsear_linea(linea, i)
                        if evento is None:
                            continue
                        ev = {
                            "operario_id": evento.operario_id,
                            "timestamp": evento.timestamp,
                            "sku_pedido": evento.sku_pedido,
                            "sku_escaneado": evento.sku_escaneado,
                            "estacion": evento.estacion,
                            "nota": evento.nota,
                        }
                        payload = emitir_alerta(ev)
                        resumen["eventos_nuevos"] += 1
                        if callback is not None:
                            try:
                                callback(payload)
                            except Exception:
                                pass
                resumen["pasadas"] += 1
            except Exception as e:
                resumen["aviso"] = f"Error leyendo la traza: {e}"
                break
            time.sleep(max(0.2, float(intervalo_s)))
    finally:
        resumen["activo"] = False
    return resumen


if __name__ == "__main__":
    import json
    import sys
    ruta = sys.argv[1] if len(sys.argv) > 1 else "."
    print(json.dumps(analizar_log_surtido(ruta), ensure_ascii=False, indent=2))

#!/usr/bin/env python3
"""Puente universal PHP <-> Python para las Skills de ARA_SYNC (v4.1).

Superconector: cualquier script en C:\\ARA_PROYECT\\skills\\ se vuelve
ejecutable desde PHP (PythonSkillExecutor) con este contrato:

  - Entrada: JSON por stdin (o argv[1] si no hay stdin):
      {"modulo": "auditoria.detector_malsurtido",
       "accion": "analizar_log_surtido",
       "params": {...}}
  - Salida:  una sola linea JSON en stdout:
      {"success": true, "data": ...}
      {"success": false, "error": "...", "tipo": "..."}

Best-effort absoluto: el puente NUNCA lanza; cualquier excepcion se
serializa a JSON con mensaje y tipo. El cwd de ejecucion es la raiz del
proyecto para que "import skills" resuelva el paquete.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from typing import Any, Dict

# Forzar UTF-8 en stdin/stdout: sin esto, al ser pipes (no consola) Python
# usa la codificación local cp1252 y el JSON con tildes corrompe json_decode
# del lado PHP. best-effort: si el runtime no permite reconfigure, se sigue.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Raíz de skills: env ARA_SKILLS_DIR o C:\ARA_PROYECT\skills (derivada del
# puente: app/Services/NvidiaBrain/Adapters/ -> subimos 5 niveles).
_RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
_RAIZ_SKILLS = os.getenv("ARA_SKILLS_DIR") or os.path.join(_RAIZ_PROYECTO, "skills")
if os.path.isdir(_RAIZ_SKILLS):
    _PADRE_SKILLS = os.path.dirname(os.path.abspath(_RAIZ_SKILLS))
    if _PADRE_SKILLS not in sys.path:
        sys.path.insert(0, _PADRE_SKILLS)


def _leer_payload() -> Dict[str, Any]:
    """Payload JSON desde stdin (o argv[1] como respaldo). Nunca lanza."""
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    if not raw.strip() and len(sys.argv) > 1:
        raw = sys.argv[1]
    if not raw.strip():
        return {}
    try:
        datos = json.loads(raw)
        return datos if isinstance(datos, dict) else {}
    except Exception:
        return {}


def _cargar_modulo(ruta: str) -> Any:
    """skills/<ruta>.py -> modulo importable (tolerante a \\ y .py)."""
    ruta = ruta.replace("\\", "/").strip("/")
    if ruta.endswith(".py"):
        ruta = ruta[:-3]
    return importlib.import_module("skills." + ruta.replace("/", "."))


def _serializable(valor: Any) -> Any:
    """Verifica que el resultado sea JSON-serializable (sino str())."""
    try:
        json.dumps(valor, ensure_ascii=False)
        return valor
    except Exception:
        return str(valor)


def main() -> int:
    salida: Dict[str, Any]
    payload = _leer_payload()
    modulo_ruta = str(payload.get("modulo") or "").strip()
    accion = str(payload.get("accion") or "").strip()
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    try:
        if not modulo_ruta or not accion:
            salida = {
                "success": False,
                "error": "Faltan 'modulo' y/o 'accion' en el payload.",
                "tipo": "payload_invalido",
            }
        else:
            modulo = _cargar_modulo(modulo_ruta)
            funcion = getattr(modulo, accion, None)
            if funcion is None or not callable(funcion):
                salida = {
                    "success": False,
                    "error": f"La skill {modulo_ruta} no expone la accion '{accion}'.",
                    "tipo": "accion_inexistente",
                }
            else:
                resultado = funcion(**params)
                salida = {"success": True, "data": _serializable(resultado)}
    except TypeError as e:
        salida = {
            "success": False,
            "error": f"Parametros invalidos para {accion}: {e}",
            "tipo": "type_error",
        }
    except Exception as e:
        salida = {
            "success": False,
            "error": f"{type(e).__name__}: {e}",
            "tipo": type(e).__name__,
        }

    try:
        sys.stdout.write(json.dumps(salida, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        sys.stdout.write('{"success": false, "error": "salida no serializable"}\n')
    return 0


if __name__ == "__main__":
    sys.exit(main())

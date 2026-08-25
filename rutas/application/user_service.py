"""Servicio de Sesión Multi-Sede (arquitectura de Macro-Rutas).

Resuelve la sede operativa (`'SC'` para San Cristóbal, `'BQTO'` para
Barquisimeto) del usuario autenticado, sin estado global compartido:

  - `asegurar_columna_sede()`: migración idempotente de `usuarios.sede`.
  - `resolver_sede_usuario(user_id)`: consulta la sede persistida del usuario.
  - `get_current_user_sede(request)`: helper de middleware que resuelve la sede
    operativa del request actual (JSON body → args → perfil de usuario → BQTO).

El `sede_id` inyectado en el usuario autenticado (login) alimenta el bus de
eventos: cada `embalaje.finalizado` lleva su `sede_id` para alimentar la
Macro-Ruta ACTIVA de la sede correspondiente.
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.getenv("ARA_DB_PATH", str(_PROJECT_ROOT / "ara" / "ARA_Brain" / "data" / "proyecto_ara.db")))

SEDES_VALIDAS = {
    "SC": "San Cristóbal",
    "BQTO": "Barquisimeto",
}
SEDE_DEFAULT = "BQTO"


def normalizar_sede(valor) -> Optional[str]:
    """Devuelve la sede canónica ('SC' | 'BQTO') o None si el valor es inválido."""
    if valor is None:
        return None
    s = str(valor).strip().upper()
    if s in ("SC", "SAN_CRISTOBAL", "SAN CRISTOBAL", "SAN CRISTÓBAL", "CRISTM25"):
        return "SC"
    if s in ("BQTO", "BARQUISIMETO", "PROFIT_BQTO"):
        return "BQTO"
    return None


def _conectar() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def asegurar_columna_sede() -> bool:
    """Migración idempotente: agrega `usuarios.sede` si no existe.

    Retorna True si la columna existe tras la llamada (ya existía o fue creada).
    """
    try:
        conn = _conectar()
        try:
            existentes = {
                r[1] for r in conn.execute("PRAGMA table_info(usuarios)").fetchall()
            }
            if "sede" not in existentes:
                conn.execute("ALTER TABLE usuarios ADD COLUMN sede TEXT DEFAULT ''")
                conn.commit()
                print("[UserService] Migración: columna usuarios.sede creada.")
            return True
        except sqlite3.OperationalError as e:
            print(f"[UserService] No se pudo migrar usuarios.sede: {e}")
            return False
        finally:
            conn.close()
    except Exception as e:
        print(f"[UserService] Error de conexión en migración de sede: {e}")
        return False


def resolver_sede_usuario(user_id) -> str:
    """Sede operativa del usuario (usuarios.sede) con fallback determinista.

    Orden de resolución:
      1. `usuarios.sede` (columna persistida; puede ser 'SC' | 'BQTO' | '').
      2. Si la columna no existe / valor vacío → BQTO (sede principal).
    """
    user_id = str(user_id or "").strip()
    if not user_id:
        return SEDE_DEFAULT
    try:
        conn = _conectar()
        try:
            fila = conn.execute(
                "SELECT sede FROM usuarios "
                "WHERE UPPER(id) = UPPER(?) OR UPPER(nombre) = UPPER(?) LIMIT 1",
                (user_id, user_id),
            ).fetchone()
        finally:
            conn.close()
        if fila is None:
            return SEDE_DEFAULT
        sede = normalizar_sede(fila["sede"])
        return sede or SEDE_DEFAULT
    except Exception as e:
        print(f"[UserService] No se pudo resolver sede de {user_id}: {e}")
        return SEDE_DEFAULT


def get_current_user_sede(request) -> str:
    """Helper de middleware: resuelve la sede operativa del request actual.

    Prioridad (sin sobrecarga):
      1. Parámetro explícito `sede` (JSON body o query string), validado.
      2. `usuario_id` / `user_id` / `numero_embalador` → perfil de usuarios.
      3. Sede por defecto: 'BQTO'.
    """
    body = {}
    if request is not None:
        try:
            body = request.get_json(silent=True) or {}
        except Exception:
            body = {}
        sede_explicita = body.get("sede") or (request.args or {}).get("sede")
        sede = normalizar_sede(sede_explicita)
        if sede:
            return sede
        user_id = (
            body.get("usuario_id")
            or body.get("user_id")
            or body.get("numero_embalador")
            or (request.args or {}).get("user_id")
            or (request.args or {}).get("usuario")
            or ""
        )
        if user_id:
            return resolver_sede_usuario(user_id)
    return SEDE_DEFAULT

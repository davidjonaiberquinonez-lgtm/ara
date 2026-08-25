import os
import sqlite3
from pathlib import Path
from typing import Optional

# Raíz del proyecto: rutas/infrastructure → 3 niveles arriba
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.getenv("ARA_DB_PATH", str(_PROJECT_ROOT / "ara" / "ARA_Brain" / "data" / "proyecto_ara.db")))

# Regla del sistema (ara_server.registrar_operacion_historial): el embalaje
# otorga 5.0 pts por ítem/relleno diferente surtido en la nota.
PUNTOS_POR_ITEM_EMBALAJE = 1.0
MODULO_EMBALAJE = "embalaje"
ACCION_EMBALAJE = "EMBALAJE"


class EmbalajeLocalStore:
    """Persistencia local del Módulo de Embalaje en proyecto_ara.db.

    Tras la confirmación del visor PHP legacy, registra:
      - movimiento en movimientos_preparador (accion='EMBALAJE', mesa, cajas)
      - puntos en log_puntos (modulo='embalaje', 5.0 pts/ítem, anti-duplicado)
    e invalida la caché del dashboard para reflejar las métricas al instante.
    """

    _COLUMNAS_EXTRA = (
        ("mesa", "TEXT DEFAULT ''"),
        ("cant_cajas", "INTEGER DEFAULT 0"),
        ("cant_items", "INTEGER DEFAULT 0"),
        ("tipo_movimiento", "TEXT DEFAULT ''"),
    )

    def _conectar(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _migrar_movimientos(self, conn: sqlite3.Connection) -> None:
        """ALTER TABLE seguro: agrega columnas de embalaje si no existen."""
        existentes = {
            r[1] for r in conn.execute("PRAGMA table_info(movimientos_preparador)").fetchall()
        }
        for nombre, ddl in self._COLUMNAS_EXTRA:
            if nombre not in existentes:
                conn.execute(
                    f"ALTER TABLE movimientos_preparador ADD COLUMN {nombre} {ddl}"
                )
        conn.commit()

    def resolver_nombre_usuario(self, identificador: Optional[str]) -> str:
        """Resuelve el NOMBRE real del operador desde usuarios (id o nombre)."""
        if not identificador:
            return "OPERADOR"
        valor = str(identificador).strip()
        conn = self._conectar()
        try:
            row = conn.execute(
                "SELECT nombre FROM usuarios WHERE id = ? OR UPPER(nombre) = UPPER(?) LIMIT 1",
                (valor, valor),
            ).fetchone()
            return str(row["nombre"]).strip() if row else valor
        except sqlite3.OperationalError:
            return valor
        finally:
            conn.close()

    def buscar_nota_local(self, codigo_barra: str) -> Optional[dict]:
        """Devuelve la nota local (notas_entrega) si ya fue escaneada por ARA."""
        conn = self._conectar()
        try:
            row = conn.execute(
                "SELECT id, numero_nota, estado, items_count FROM notas_entrega "
                "WHERE numero_nota = ? LIMIT 1",
                (str(codigo_barra or "").strip(),),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def registrar_embalaje_local(
        self,
        codigo_barra: str,
        usuario_id: str,
        numero_mesa: str = "",
        cant_items: int = 0,
        cant_cajas: int = 0,
    ) -> dict:
        """Registra el evento de embalaje en movimientos_preparador y log_puntos.

        Anti-duplicado por (usuario, modulo='embalaje', referencia_id). Nunca
        lanza: el fallo local no rompe el flujo ya confirmado en el visor.
        Retorno: {movimiento_registrado, puntos_registrados, puntos_ganados,
                  puntos_totales, duplicado, mensaje}
        """
        codigo = str(codigo_barra or "").strip()
        usuario = self.resolver_nombre_usuario(usuario_id)
        try:
            cant_i = int(cant_items or 0)
        except (TypeError, ValueError):
            cant_i = 0
        try:
            cant_c = int(cant_cajas or 0)
        except (TypeError, ValueError):
            cant_c = 0

        if not codigo or not usuario or cant_i <= 0:
            return {
                "movimiento_registrado": False, "puntos_registrados": False,
                "puntos_ganados": 0.0, "puntos_totales": self._puntos_totales(usuario),
                "duplicado": False, "mensaje": "Datos inválidos para registro local.",
            }

        conn = self._conectar()
        try:
            self._migrar_movimientos(conn)

            nota_local = self.buscar_nota_local(codigo)
            nota_id = nota_local["id"] if nota_local else None

            # 1. Movimiento de embalaje (una fila resumen por nota)
            ya_movimiento = conn.execute(
                "SELECT id FROM movimientos_preparador WHERE accion = ? "
                "AND usuario = ? AND (nota_id = ? OR (nota_id IS NULL AND ? IS NULL))",
                (ACCION_EMBALAJE, usuario, nota_id, nota_id),
            ).fetchone()
            movimiento_ok = False
            if not ya_movimiento:
                conn.execute(
                    "INSERT INTO movimientos_preparador "
                    "(nota_id, co_art, descripcion, cantidad, unidad_medida, usuario, "
                    " accion, origen, destino, tipo_movimiento, mesa, cant_cajas, "
                    " cant_items, timestamp) "
                    "VALUES (?, '', ?, ?, 'UND', ?, ?, 'chequeada', 'embalada', "
                    "        'EMBALAJE', ?, ?, ?, datetime('now','localtime'))",
                    (
                        nota_id,
                        f"EMBALAJE nota {codigo}",
                        float(cant_i),
                        usuario,
                        ACCION_EMBALAJE,
                        str(numero_mesa or "").strip(),
                        cant_c,
                        cant_i,
                    ),
                )
                conn.commit()
                movimiento_ok = True

            # 2. Puntos en log_puntos (5.0 pts/ítem, anti-duplicado)
            puntos = PUNTOS_POR_ITEM_EMBALAJE * cant_i
            ya_puntos = conn.execute(
                "SELECT id FROM log_puntos WHERE usuario = ? AND modulo = ? "
                "AND referencia_id = ? LIMIT 1",
                (usuario, MODULO_EMBALAJE, codigo),
            ).fetchone()
            puntos_ok = False
            if not ya_puntos:
                conn.execute(
                    "INSERT INTO log_puntos "
                    "(usuario, modulo, referencia_id, cantidad_renglones, puntos_ganados, fecha_registro) "
                    "VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))",
                    (usuario, MODULO_EMBALAJE, codigo, cant_i, puntos),
                )
                conn.commit()
                puntos_ok = True

            duplicado = (not movimiento_ok) and (not puntos_ok)
            if movimiento_ok or puntos_ok:
                self._invalidar_cache_dashboard()

            total = self._puntos_totales(usuario, conn)
            return {
                "movimiento_registrado": movimiento_ok,
                "puntos_registrados": puntos_ok,
                "puntos_ganados": round(puntos, 2) if puntos_ok else 0.0,
                "puntos_totales": total,
                "duplicado": duplicado,
                "mensaje": "Embalaje registrado en la BD local."
                if (movimiento_ok or puntos_ok)
                else "El embalaje ya estaba registrado en la BD local (sin duplicar).",
            }
        except sqlite3.Error as e:
            print(f"❌ Error registrando embalaje local: {e}")
            return {
                "movimiento_registrado": False, "puntos_registrados": False,
                "puntos_ganados": 0.0, "puntos_totales": self._puntos_totales(usuario),
                "duplicado": False, "mensaje": f"Error en BD local: {e}",
            }
        finally:
            conn.close()

    def _puntos_totales(self, usuario: str, conn: Optional[sqlite3.Connection] = None) -> float:
        """Acumulado de puntos del usuario en log_puntos (para la respuesta API)."""
        cerrar = conn is None
        if cerrar:
            conn = self._conectar()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(puntos_ganados), 0) AS total FROM log_puntos WHERE usuario = ?",
                (usuario,),
            ).fetchone()
            return round(float(row["total"] or 0), 2)
        finally:
            if cerrar:
                conn.close()

    @staticmethod
    def _invalidar_cache_dashboard() -> None:
        """Borra la caché TTL del dashboard (ara_server) para reflejar al instante."""
        for modulo in ("ara_server", "ara.ARA_Brain.ara_server"):
            try:
                from importlib import import_module
                mod = import_module(modulo)
                cache = getattr(mod, "_dashboard_cache", None)
                if isinstance(cache, dict):
                    cache["data"] = None
                    cache["timestamp"] = 0
                    return
            except Exception:
                pass

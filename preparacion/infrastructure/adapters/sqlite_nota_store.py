import os
import sqlite3
from pathlib import Path
from typing import Optional

from ...domain.models import NotaPreparacion
from ...domain.ports import NotaLocalStorePort

# Raíz del proyecto: preparacion/infrastructure/adapters → 4 niveles arriba
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = Path(os.getenv("ARA_DB_PATH", str(_PROJECT_ROOT / "ara" / "ARA_Brain" / "data" / "proyecto_ara.db")))


class SqliteNotaStore(NotaLocalStorePort):
    """Persistencia local en SQLite reutilizando las tablas hexagonales existentes:
    notas_entrega / detalle_nota / movimientos_preparador (schema de notas_hexagonal.py).
    """

    def _conectar(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def find_by_barcode(self, codigo_barra: str) -> Optional[dict]:
        conn = self._conectar()
        try:
            row = conn.execute(
                "SELECT * FROM notas_entrega WHERE numero_nota = ?", (codigo_barra,)
            ).fetchone()
            if not row:
                return None
            nota = dict(row)
            items = conn.execute(
                "SELECT * FROM detalle_nota WHERE nota_id = ? ORDER BY id", (nota["id"],)
            ).fetchall()
            nota["items"] = [dict(i) for i in items]
            return nota
        finally:
            conn.close()

    def create_nota(self, nota: NotaPreparacion, estado: str, almacen_origen: str = "") -> dict:
        conn = self._conectar()
        try:
            cur = conn.execute(
                "INSERT INTO notas_entrega "
                "(numero_nota, cliente, co_cli, estado, es_prueba, almacen_origen) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (
                    nota.codigo_nota,
                    nota.nombre_cliente,
                    nota.codigo_cliente or "",
                    estado,
                    almacen_origen or nota.almacen_origen or "",
                ),
            )
            nota_id = cur.lastrowid
            conn.commit()
            print(f"[SqliteNotaStore] Nota {nota.codigo_nota} creada (id={nota_id}, estado={estado}, origen={almacen_origen or 'BQTO'})")
            return {"id": nota_id, "numero_nota": nota.codigo_nota, "estado": estado}
        finally:
            conn.close()

    def insert_items(self, nota_id: int, nota: NotaPreparacion) -> None:
        conn = self._conectar()
        try:
            for it in nota.items:
                conn.execute(
                    "INSERT INTO detalle_nota "
                    "(nota_id, co_art, descripcion, cantidad_solicitada, unidad_medida, estado, campo7, reng_num) "
                    "VALUES (?, ?, ?, ?, ?, 'pendiente', ?, ?)",
                    (
                        nota_id,
                        it.codigo_art,
                        it.descripcion,
                        float(it.cantidad),
                        (it.unidad or "UND").upper(),
                        it.campo7 or "",
                        int(it.reng_nd or 0),
                    ),
                )
            conn.execute(
                "UPDATE notas_entrega SET items_count = "
                "(SELECT COUNT(*) FROM detalle_nota WHERE nota_id = ?) WHERE id = ?",
                (nota_id, nota_id),
            )
            conn.commit()
        finally:
            conn.close()

    def finalize_nota(self, nota_id: int, estado: str, preparador_id: str) -> dict:
        conn = self._conectar()
        try:
            # Estados del dominio que persisten tal cual (schema v4.0 de
            # notas_hexagonal admite el flujo completo); cualquier otro se
            # degrada a 'completada'.
            estado_sqlite = str(estado or "").strip()
            if estado_sqlite not in {
                "pendiente", "preparando", "preparada", "chequeada",
                "embalada", "entregada", "devuelta", "completada",
            }:
                estado_sqlite = "completada"
            conn.execute(
                "UPDATE notas_entrega SET estado = ?, preparador_id = ?, "
                "fecha_completada = datetime('now','localtime'), "
                "auto_chequeado = CASE WHEN ? = 'chequeada' THEN 1 ELSE auto_chequeado END "
                "WHERE id = ?",
                (estado_sqlite, preparador_id, estado_sqlite, nota_id),
            )
            conn.execute(
                "UPDATE detalle_nota SET estado = 'preparado' WHERE nota_id = ?", (nota_id,)
            )
            conn.commit()
            return {"id": nota_id, "estado": estado_sqlite, "preparador_id": preparador_id}
        finally:
            conn.close()

    def insert_movimiento(
        self,
        nota_id: int,
        nota: NotaPreparacion,
        usuario: str,
        accion: str,
        origen: str,
        destino: str,
    ) -> None:
        conn = self._conectar()
        try:
            for it in nota.items:
                conn.execute(
                    "INSERT INTO movimientos_preparador "
                    "(nota_id, co_art, descripcion, cantidad, unidad_medida, usuario, accion, origen, destino) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        nota_id,
                        it.codigo_art,
                        it.descripcion,
                        float(it.cantidad),
                        (it.unidad or "UND").upper(),
                        usuario,
                        accion,
                        origen,
                        destino,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def resolver_id_usuario(self, identificador: Optional[str]) -> Optional[str]:
        """Resuelve el id real del operador desde la tabla usuarios (columna id).

        Acepta un id ('70', '7', '39') o un nombre ('Argenis Ramirez'). Retorna
        None si el valor es un default ('0', 'null', 'admin1') o no se encuentra.
        """
        if not identificador:
            return None
        valor = str(identificador).strip()
        if valor.lower() in ("0", "null", "none", "admin1", "operador_central"):
            return None
        conn = self._conectar()
        try:
            row = conn.execute(
                "SELECT id FROM usuarios WHERE id = ? OR UPPER(nombre) = UPPER(?) LIMIT 1",
                (valor, valor),
            ).fetchone()
            return str(row["id"]) if row else None
        finally:
            conn.close()

    def registrar_puntos_preparacion(self, usuario_id: str, referencia_id: str, cant_items: int) -> dict:
        """Registra puntos de PREPARACIÓN en log_puntos (modulo='picking', 1.0 pt/renglón).

        log_puntos.usuario guarda el NOMBRE real del operador (no el id); se resuelve
        desde la tabla usuarios. Anti-duplicado por (usuario, 'picking', referencia_id).
        Invalida la caché del dashboard para reflejar los puntos en tiempo real.
        """
        conn = self._conectar()
        try:
            uid = str(usuario_id or "").strip()
            ref = str(referencia_id or "").strip()
            try:
                cant = int(cant_items or 0)
            except (TypeError, ValueError):
                cant = 0

            if not uid or not ref or cant <= 0:
                return {"registrado": False, "puntos": 0.0, "motivo": "Datos inválidos"}

            fila_usuario = conn.execute(
                "SELECT nombre FROM usuarios WHERE id = ?", (uid,)
            ).fetchone()
            nombre = str(fila_usuario["nombre"]).strip() if fila_usuario else uid

            existe = conn.execute(
                "SELECT id FROM log_puntos WHERE usuario = ? AND modulo = 'picking' "
                "AND referencia_id = ? LIMIT 1",
                (nombre, ref),
            ).fetchone()
            if existe:
                return {"registrado": False, "puntos": 0.0, "motivo": "Duplicado"}

            puntos = 1.0 * cant
            conn.execute(
                "INSERT INTO log_puntos "
                "(usuario, modulo, referencia_id, cantidad_renglones, puntos_ganados, fecha_registro) "
                "VALUES (?, 'picking', ?, ?, ?, datetime('now','localtime'))",
                (nombre, ref, cant, puntos),
            )
            conn.commit()
            print(f"🏆 [PUNTOS] {nombre} +{puntos} pts (picking: {cant} renglones) -> Ref: {ref}")

            self._invalidar_cache_dashboard()

            return {"registrado": True, "puntos": puntos, "motivo": "OK"}
        except Exception as e:
            print(f"❌ Error registrando puntos de preparación: {e}")
            return {"registrado": False, "puntos": 0.0, "motivo": str(e)}
        finally:
            conn.close()

    def registrar_puntos_chequeo(self, usuario_id: str, referencia_id: str, cant_items: int) -> dict:
        """Registra puntos de CHEQUEO en log_puntos (modulo='chequeo', 1.0 pt/renglón).

        Se usa en el Fast-Track (< 3 ítems): la nota se auto-chequea y el operador
        recibe doble puntuación (PREPARACION + CHEQUEO). Anti-duplicado por
        (usuario, 'chequeo', referencia_id). Invalida la caché del dashboard.
        """
        conn = self._conectar()
        try:
            uid = str(usuario_id or "").strip()
            ref = str(referencia_id or "").strip()
            try:
                cant = int(cant_items or 0)
            except (TypeError, ValueError):
                cant = 0

            if not uid or not ref or cant <= 0:
                return {"registrado": False, "puntos": 0.0, "motivo": "Datos inválidos"}

            fila_usuario = conn.execute(
                "SELECT nombre FROM usuarios WHERE id = ?", (uid,)
            ).fetchone()
            nombre = str(fila_usuario["nombre"]).strip() if fila_usuario else uid

            existe = conn.execute(
                "SELECT id FROM log_puntos WHERE usuario = ? AND modulo = 'chequeo' "
                "AND referencia_id = ? LIMIT 1",
                (nombre, ref),
            ).fetchone()
            if existe:
                return {"registrado": False, "puntos": 0.0, "motivo": "Duplicado"}

            puntos = 1.0 * cant
            conn.execute(
                "INSERT INTO log_puntos "
                "(usuario, modulo, referencia_id, cantidad_renglones, puntos_ganados, fecha_registro) "
                "VALUES (?, 'chequeo', ?, ?, ?, datetime('now','localtime'))",
                (nombre, ref, cant, puntos),
            )
            conn.commit()
            print(f"🏆 [PUNTOS] {nombre} +{puntos} pts (chequeo: {cant} renglones) -> Ref: {ref}")

            self._invalidar_cache_dashboard()

            return {"registrado": True, "puntos": puntos, "motivo": "OK"}
        except Exception as e:
            print(f"❌ Error registrando puntos de chequeo: {e}")
            return {"registrado": False, "puntos": 0.0, "motivo": str(e)}
        finally:
            conn.close()

    @staticmethod
    def _invalidar_cache_dashboard() -> None:
        """Borra la caché TTL del dashboard (ara_server) para reflejar los puntos al instante."""
        try:
            from ara_server import _dashboard_cache
            _dashboard_cache["data"] = None
            _dashboard_cache["timestamp"] = 0
        except Exception:
            try:
                from ara.ARA_Brain.ara_server import _dashboard_cache
                _dashboard_cache["data"] = None
                _dashboard_cache["timestamp"] = 0
            except Exception:
                pass

    def enriquecer_con_stock(self, items: list) -> list:
        """Enriquece items con 'ubicacion' (campo7 de stock_maestro), 'codigo_barra'
        y 'descripcion' (solo si el renglón llegó vacío: fallback del maestro local)."""
        if not items:
            return items
        codigos = []
        for it in items:
            co = str(it.get("co_art") or it.get("codigo_art") or "").strip()
            if co:
                codigos.append(co)
        info = {}
        if codigos:
            conn = self._conectar()
            try:
                ph = ",".join("?" * len(codigos))
                rows = conn.execute(
                    f"SELECT codigo, campo7, codigo_barra, descripcion "
                    f"FROM stock_maestro WHERE codigo IN ({ph})",
                    codigos,
                ).fetchall()
                info = {str(r["codigo"]): r for r in rows}
            except sqlite3.OperationalError as e:
                print(f"[SqliteNotaStore] stock_maestro no disponible: {e}")
            finally:
                conn.close()
        for it in items:
            co = str(it.get("co_art") or it.get("codigo_art") or "").strip()
            row = info.get(co)
            campo7 = str(row["campo7"] or "").strip() if row else ""
            it["ubicacion"] = campo7 or "POR_ASIGNAR"
            cb = str(row["codigo_barra"] or "").strip() if row else ""
            it["codigo_barra"] = cb.lower()
            if not it.get("descripcion") and row and row["descripcion"]:
                it["descripcion"] = str(row["descripcion"]).strip()
            it["imagen_url"] = self._obtener_url_imagen(co)
        return items

    @staticmethod
    def _obtener_url_imagen(co_art: str) -> str:
        """URL CDN de la imagen del artículo (misma regla que ara_server.obtener_url_imagen)."""
        try:
            from ara_server import obtener_url_imagen
            return obtener_url_imagen(co_art)
        except Exception:
            return f"https://imagenes.cristmedicals.com/imagenes-v3/imagenes/{str(co_art or '').strip().upper()}.jpg"

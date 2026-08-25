import os
import re
import sqlite3
from pathlib import Path
from typing import List, Optional

from ..domain.models import SubRutaFinalizada
from ..domain.ports import RouteRepositoryPort

# Raíz del proyecto: rutas/infrastructure → 2 niveles arriba → C:\ARA_PROYECT
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.getenv("ARA_DB_PATH", str(_PROJECT_ROOT / "ara" / "ARA_Brain" / "data" / "proyecto_ara.db")))

DDL_SUBRUTAS = """
CREATE TABLE IF NOT EXISTS sub_rutas_finalizadas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guia TEXT,
    rutagrama_padre TEXT,
    sub_rutagrama TEXT,
    sub_ruta TEXT,
    responsable_id TEXT,
    chofer TEXT,
    ayudantes TEXT,
    carro TEXT,
    total_notas INTEGER DEFAULT 0,
    total_paquetes INTEGER DEFAULT 0,
    fecha TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS subruta_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subruta_id INTEGER NOT NULL REFERENCES sub_rutas_finalizadas(id),
    nota_num TEXT,
    factura_num TEXT,
    num_fact TEXT,
    razon_social TEXT,
    paquetes INTEGER DEFAULT 0,
    tipo_documento TEXT DEFAULT 'PEDIDO',
    is_invoice_only INTEGER DEFAULT 0,
    has_credit_notes INTEGER DEFAULT 0,
    sub_rutagrama_id TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_subruta_items_subruta ON subruta_items(subruta_id);
CREATE TABLE IF NOT EXISTS rutagrama_notas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    num_nota TEXT NOT NULL,
    factura_num TEXT DEFAULT '',
    co_cli TEXT DEFAULT '',
    razon_social TEXT DEFAULT '',
    total_bultos INTEGER DEFAULT 0,
    operador_embalaje TEXT DEFAULT '',
    ruta_macro TEXT DEFAULT '',
    sub_ruta TEXT DEFAULT '',
    rutagrama TEXT DEFAULT '',
    estado TEXT DEFAULT 'EMBALADO_LISTO_PARA_DESPACHO',
    fecha_embalaje TEXT DEFAULT (datetime('now','localtime')),
    fecha_despacho TEXT,
    despachado_por TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_rutagrama_notas_nota ON rutagrama_notas(num_nota);
CREATE INDEX IF NOT EXISTS idx_rutagrama_notas_estado ON rutagrama_notas(estado);
CREATE TABLE IF NOT EXISTS macro_rutas (
    id TEXT PRIMARY KEY,
    sede_id TEXT DEFAULT 'BQTO',
    fecha TEXT DEFAULT '',
    estado TEXT DEFAULT 'ACTIVA',
    creada_en TEXT DEFAULT (datetime('now','localtime')),
    cerrada_en TEXT
);
CREATE INDEX IF NOT EXISTS idx_macro_rutas_sede ON macro_rutas(sede_id, estado);
CREATE TABLE IF NOT EXISTS macro_ruta_notas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    macro_id TEXT NOT NULL REFERENCES macro_rutas(id),
    num_nota TEXT NOT NULL,
    num_factura TEXT DEFAULT '',
    co_cli TEXT DEFAULT '',
    razon_social TEXT DEFAULT '',
    total_bultos INTEGER DEFAULT 0,
    estado_despacho TEXT DEFAULT 'PENDIENTE',
    zona_destino TEXT DEFAULT '',
    operador_embalaje TEXT DEFAULT '',
    fecha_embalaje TEXT DEFAULT (datetime('now','localtime')),
    fecha_despacho TEXT,
    despachado_por TEXT DEFAULT '',
    UNIQUE(macro_id, num_nota)
);
CREATE INDEX IF NOT EXISTS idx_macro_ruta_notas_macro ON macro_ruta_notas(macro_id);
CREATE INDEX IF NOT EXISTS idx_macro_ruta_notas_estado ON macro_ruta_notas(estado_despacho);
CREATE TABLE IF NOT EXISTS sesiones_ruta_activa (
    user_id TEXT PRIMARY KEY,
    ruta_macro TEXT NOT NULL,
    sede TEXT DEFAULT '',
    datos_json TEXT NOT NULL,
    actualizado_en TEXT DEFAULT (datetime('now','localtime'))
);
"""


class SqliteRouteRepository(RouteRepositoryPort):
    def __init__(self):
        self._init_tablas()

    def _conectar(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_tablas(self):
        conn = self._conectar()
        try:
            conn.executescript(DDL_SUBRUTAS)
            # Migración no destructiva: columnas nuevas sobre BDs antiguas
            for tabla, columnas in {
                "sub_rutas_finalizadas": {
                    "rutagrama_padre": "TEXT DEFAULT ''",
                    "sub_rutagrama": "TEXT DEFAULT ''",
                },
                "subruta_items": {
                    "tipo_documento": "TEXT DEFAULT 'PEDIDO'",
                    "is_invoice_only": "INTEGER DEFAULT 0",
                    "has_credit_notes": "INTEGER DEFAULT 0",
                    "sub_rutagrama_id": "TEXT DEFAULT ''",
                    "num_fact": "TEXT",
                },
            }.items():
                existentes = {
                    row["name"] for row in conn.execute(f"PRAGMA table_info({tabla})")
                }
                for columna, ddl in columnas.items():
                    if columna not in existentes:
                        conn.execute(
                            f"ALTER TABLE {tabla} ADD COLUMN {columna} {ddl}")
            conn.commit()
        finally:
            conn.close()

    def get_ruta_asignada(self, user_id: str) -> Optional[str]:
        conn = self._conectar()
        try:
            row = conn.execute(
                "SELECT ruta_asignada FROM usuarios WHERE UPPER(id) = UPPER(?)",
                (str(user_id or "").strip(),),
            ).fetchone()
            if not row or not row["ruta_asignada"]:
                return None
            return str(row["ruta_asignada"]).strip()
        except sqlite3.OperationalError:
            # Columna ruta_asignada aún no migrada en BD antigua
            return None
        finally:
            conn.close()

    def get_estado_ara(self, factura_num: str) -> str:
        conn = self._conectar()
        try:
            row = conn.execute(
                "SELECT estado FROM notas_entrega WHERE numero_nota = ? LIMIT 1",
                (str(factura_num or "").strip(),),
            ).fetchone()
            return str(row["estado"]) if row and row["estado"] else "pendiente"
        except sqlite3.OperationalError:
            return "pendiente"
        finally:
            conn.close()

    def save_subrutas(self, subrutas: List[SubRutaFinalizada]) -> List[dict]:
        conn = self._conectar()
        guardadas = []
        try:
            for sr in subrutas:
                cur = conn.execute(
                    "INSERT INTO sub_rutas_finalizadas "
                    "(guia, rutagrama_padre, sub_rutagrama, sub_ruta, responsable_id, "
                    " chofer, ayudantes, carro, total_notas, total_paquetes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        sr.guia,
                        sr.rutagrama_padre,
                        sr.sub_rutagrama,
                        sr.sub_ruta,
                        sr.responsable_id,
                        sr.datos_vehiculo.chofer,
                        sr.datos_vehiculo.ayudantes,
                        sr.datos_vehiculo.carro,
                        len(sr.items),
                        sum(i.paquetes for i in sr.items),
                    ),
                )
                sr_id = cur.lastrowid
                for it in sr.items:
                    conn.execute(
                        "INSERT INTO subruta_items "
                        "(subruta_id, nota_num, factura_num, num_fact, razon_social, "
                        " paquetes, tipo_documento, is_invoice_only, has_credit_notes, "
                        " sub_rutagrama_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (sr_id, it.nota_num, it.factura_num, it.num_fact,
                         it.razon_social, it.paquetes,
                         it.tipo_documento, it.is_invoice_only, it.has_credit_notes,
                         it.sub_rutagrama_id),
                    )
                guardadas.append({
                    "id": sr_id,
                    "guia": sr.guia,
                    "rutagrama_padre": sr.rutagrama_padre,
                    "sub_rutagrama": sr.sub_rutagrama,
                    "sub_ruta": sr.sub_ruta,
                    "responsable_id": sr.responsable_id,
                    "chofer": sr.datos_vehiculo.chofer,
                    "ayudantes": sr.datos_vehiculo.ayudantes,
                    "carro": sr.datos_vehiculo.carro,
                    "total_notas": len(sr.items),
                    "total_paquetes": sum(i.paquetes for i in sr.items),
                    "items": [
                        {
                            "nota_num": i.nota_num,
                            "factura_num": i.factura_num,
                            "num_fact": i.num_fact,
                            "razon_social": i.razon_social,
                            "paquetes": i.paquetes,
                            "tipo_documento": i.tipo_documento,
                            "is_invoice_only": i.is_invoice_only,
                            "has_credit_notes": i.has_credit_notes,
                            "sub_rutagrama_id": i.sub_rutagrama_id,
                        }
                        for i in sr.items
                    ],
                })
            conn.commit()
            print(f"[SqliteRouteRepository] {len(guardadas)} sub-ruta(s) guardadas")
            return guardadas
        finally:
            conn.close()

    def buscar_notas_locales(self, patron: str) -> List[dict]:
        """Notas locales (notas_entrega) que coincidan con el patrón (LIKE %patron%)."""
        p = str(patron or "").strip()
        if not p:
            return []
        like = f"%{p.upper()}%"
        conn = self._conectar()
        try:
            filas = conn.execute(
                "SELECT numero_nota, cliente, co_cli, estado FROM notas_entrega "
                "WHERE UPPER(numero_nota) LIKE ? OR UPPER(cliente) LIKE ? "
                "OR UPPER(co_cli) LIKE ? ORDER BY fecha_creacion DESC LIMIT 100",
                (like, like, like),
            ).fetchall()
            resultado = []
            for r in filas:
                d = dict(r)
                resultado.append({
                    "nota_num": d.get("numero_nota") or "",
                    "factura_num": "",
                    "sub_ruta": "LOCAL",
                    "razon_social": d.get("cliente") or "",
                    "estado_ara": d.get("estado") or "pendiente",
                    "paquetes": 1,
                })
            return resultado
        except sqlite3.OperationalError as e:
            print(f"[SqliteRouteRepository] Búsqueda local fallida: {e}")
            return []
        finally:
            conn.close()

    def get_rutas_finalizadas_by_user(self, user_id: str) -> List[dict]:
        conn = self._conectar()
        try:
            filas = conn.execute(
                "SELECT * FROM sub_rutas_finalizadas WHERE responsable_id = ? ORDER BY id DESC",
                (str(user_id or "").strip(),),
            ).fetchall()
            registros = []
            for f in filas:
                d = dict(f)
                items = conn.execute(
                    "SELECT * FROM subruta_items WHERE subruta_id = ? ORDER BY id",
                    (d["id"],),
                ).fetchall()
                d["items"] = [dict(i) for i in items]
                registros.append(d)
            return registros
        finally:
            conn.close()

    def get_rutas_finalizadas_todas(self, usuario_filtro: Optional[str] = None) -> List[dict]:
        """Auditoría admin: todas las sub-rutas finalizadas, de cualquier responsable_id."""
        conn = self._conectar()
        try:
            filtro = str(usuario_filtro or "").strip()
            if filtro and filtro.lower() != "todos":
                filas = conn.execute(
                    "SELECT * FROM sub_rutas_finalizadas WHERE responsable_id = ? ORDER BY id DESC",
                    (filtro,),
                ).fetchall()
            else:
                filas = conn.execute(
                    "SELECT * FROM sub_rutas_finalizadas ORDER BY id DESC"
                ).fetchall()
            registros = []
            for f in filas:
                d = dict(f)
                items = conn.execute(
                    "SELECT * FROM subruta_items WHERE subruta_id = ? ORDER BY id",
                    (d["id"],),
                ).fetchall()
                d["items"] = [dict(i) for i in items]
                registros.append(d)
            return registros
        finally:
            conn.close()

    # ── Rutagrama de notas embaladas (flujo embalaje → despacho) ─────────────
    def guardar_nota_embalada(self, registro: dict) -> dict:
        """Guarda/vincular la nota embalada al rutagrama (idempotente por num_nota).

        Si ya existe un registro activo para la nota, actualiza el total de
        bultos y la ruta; nunca duplica. Estados: EMBALADO_LISTO_PARA_DESPACHO
        (al embalar) → DESPACHADO (cuando el responsable escanea la factura).
        """
        num_nota = str(registro.get("num_nota") or "").strip()
        if not num_nota:
            raise ValueError("num_nota es obligatorio para vincular al rutagrama")
        conn = self._conectar()
        try:
            previo = conn.execute(
                "SELECT * FROM rutagrama_notas WHERE num_nota = ? ORDER BY id DESC LIMIT 1",
                (num_nota,),
            ).fetchone()
            if previo:
                conn.execute(
                    "UPDATE rutagrama_notas SET total_bultos = ?, co_cli = ?, "
                    "razon_social = COALESCE(NULLIF(?, ''), razon_social), "
                    "operador_embalaje = ?, ruta_macro = COALESCE(NULLIF(?, ''), ruta_macro), "
                    "sub_ruta = COALESCE(NULLIF(?, ''), sub_ruta), "
                    "rutagrama = COALESCE(NULLIF(?, ''), rutagrama), "
                    "estado = CASE WHEN estado = 'DESPACHADO' THEN estado "
                    "                 ELSE 'EMBALADO_LISTO_PARA_DESPACHO' END "
                    "WHERE id = ?",
                    (
                        int(registro.get("total_bultos") or 0),
                        str(registro.get("co_cli") or "").strip(),
                        str(registro.get("razon_social") or "").strip(),
                        str(registro.get("operador_embalaje") or "").strip(),
                        str(registro.get("ruta_macro") or "").strip(),
                        str(registro.get("sub_ruta") or "").strip(),
                        str(registro.get("rutagrama") or "").strip(),
                        previo["id"],
                    ),
                )
                conn.commit()
                fila = conn.execute(
                    "SELECT * FROM rutagrama_notas WHERE id = ?", (previo["id"],)
                ).fetchone()
                return dict(fila)
            cur = conn.execute(
                "INSERT INTO rutagrama_notas "
                "(num_nota, factura_num, co_cli, razon_social, total_bultos, "
                " operador_embalaje, ruta_macro, sub_ruta, rutagrama, estado) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'EMBALADO_LISTO_PARA_DESPACHO')",
                (
                    num_nota,
                    str(registro.get("factura_num") or "").strip(),
                    str(registro.get("co_cli") or "").strip(),
                    str(registro.get("razon_social") or "").strip(),
                    int(registro.get("total_bultos") or 0),
                    str(registro.get("operador_embalaje") or "").strip(),
                    str(registro.get("ruta_macro") or "").strip(),
                    str(registro.get("sub_ruta") or "").strip(),
                    str(registro.get("rutagrama") or "").strip(),
                ),
            )
            conn.commit()
            fila = conn.execute(
                "SELECT * FROM rutagrama_notas WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(fila)
        finally:
            conn.close()

    def buscar_nota_rutagrama(self, num_nota: str) -> Optional[dict]:
        """Registro activo de la nota embalada (exacto o por dígitos)."""
        p = str(num_nota or "").strip()
        if not p:
            return None
        conn = self._conectar()
        try:
            fila = conn.execute(
                "SELECT * FROM rutagrama_notas WHERE num_nota = ? "
                "ORDER BY id DESC LIMIT 1",
                (p,),
            ).fetchone()
            if fila:
                return dict(fila)
            # Tolerancia de formato (prefijos de serie): compara solo dígitos.
            import re as _re
            dig = _re.sub(r"\D", "", p).lstrip("0")
            if not dig:
                return None
            filas = conn.execute(
                "SELECT * FROM rutagrama_notas ORDER BY id DESC"
            ).fetchall()
            for f in filas:
                d = dict(f)
                if _re.sub(r"\D", "", str(d.get("num_nota") or "")).lstrip("0") == dig:
                    return d
            return None
        finally:
            conn.close()

    def marcar_nota_despachada(self, num_nota: str, despachado_por: str = "") -> bool:
        """Marca la nota como DESPACHADO (solo si está lista para despacho)."""
        p = str(num_nota or "").strip()
        if not p:
            return False
        despachado_por = str(despachado_por or "").strip()
        conn = self._conectar()
        try:
            cur = conn.execute(
                "UPDATE rutagrama_notas SET estado = 'DESPACHADO', "
                "fecha_despacho = datetime('now','localtime'), despachado_por = ? "
                "WHERE num_nota = ? AND estado = 'EMBALADO_LISTO_PARA_DESPACHO'",
                (despachado_por, p),
            )
            if cur.rowcount > 0:
                conn.commit()
                return True
            # Tolerancia de formato (prefijos de serie): compara solo dígitos.
            import re as _re
            dig = _re.sub(r"\D", "", p).lstrip("0")
            if not dig:
                return False
            filas = conn.execute(
                "SELECT id, num_nota FROM rutagrama_notas "
                "WHERE estado = 'EMBALADO_LISTO_PARA_DESPACHO'"
            ).fetchall()
            objetivo = None
            for f in filas:
                if _re.sub(r"\D", "", str(f["num_nota"] or "")).lstrip("0") == dig:
                    objetivo = f["id"]
                    break
            if objetivo is None:
                return False
            conn.execute(
                "UPDATE rutagrama_notas SET estado = 'DESPACHADO', "
                "fecha_despacho = datetime('now','localtime'), despachado_por = ? "
                "WHERE id = ?",
                (despachado_por, objetivo),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    # ── Persistencia de sesión de ruta activa (v4.53) ─────────────────────────
    # El progreso de escaneo (0-0/1-0/1-1) vivía SOLO en memoria RAM del
    # proceso (RouteService._sesiones) — un reinicio del servidor (crash,
    # actualización, etc.) borraba todo el avance de cualquier ruta en curso,
    # sin ningún respaldo. Ahora se guarda un snapshot JSON completo tras
    # cada escaneo; al reiniciar, iniciar_ruta lo recupera igual que ya hacía
    # con la sesión en memoria (F5).
    def guardar_sesion_ruta(self, user_id: str, ruta_macro: str, sede: str, datos_json: str) -> None:
        conn = self._conectar()
        try:
            conn.execute(
                "INSERT INTO sesiones_ruta_activa (user_id, ruta_macro, sede, datos_json, actualizado_en) "
                "VALUES (?, ?, ?, ?, datetime('now','localtime')) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "ruta_macro = excluded.ruta_macro, sede = excluded.sede, "
                "datos_json = excluded.datos_json, actualizado_en = excluded.actualizado_en",
                (str(user_id or "").strip(), str(ruta_macro or "").strip(),
                 str(sede or "").strip(), datos_json),
            )
            conn.commit()
        finally:
            conn.close()

    def cargar_sesion_ruta(self, user_id: str) -> Optional[dict]:
        conn = self._conectar()
        try:
            fila = conn.execute(
                "SELECT ruta_macro, sede, datos_json FROM sesiones_ruta_activa WHERE user_id = ?",
                (str(user_id or "").strip(),),
            ).fetchone()
            return dict(fila) if fila else None
        finally:
            conn.close()

    # Puntos por cierre de ruta (local, espeja el registro en `puntajes`
    # MySQL — ver LegacyMySQLConnector.confirmar_puntos_ruta). 1 pt/nota,
    # MISMOS puntos para responsable y ayudante, anti-duplicado por
    # (usuario, modulo='ruta', referencia_id=sub_rutagrama).
    PUNTOS_POR_NOTA_RUTA = 1.0
    MODULO_RUTA = "ruta"

    def _resolver_nombre_usuario_ruta(self, conn: sqlite3.Connection, identificador: str) -> str:
        if not identificador:
            return "OPERADOR"
        valor = str(identificador).strip()
        try:
            row = conn.execute(
                "SELECT nombre FROM usuarios WHERE id = ? OR UPPER(nombre) = UPPER(?) LIMIT 1",
                (valor, valor),
            ).fetchone()
            return str(row["nombre"]).strip() if row else valor
        except sqlite3.OperationalError:
            return valor

    def registrar_puntos_ruta(self, sub_rutagrama: str, responsable_id: str, ayudante_id: str, total_notas: int) -> None:
        """Registra en `log_puntos` (SQLite local) los mismos puntos que
        confirmar_puntos_ruta() escribe en MySQL `puntajes` — para que el
        Dashboard de Rendimiento de ARA también refleje los puntos de ruta.
        Best-effort: nunca lanza excepción, no bloquea el cierre de ruta."""
        puntos = float(total_notas or 0) * self.PUNTOS_POR_NOTA_RUTA
        if puntos <= 0:
            return
        ref = str(sub_rutagrama or "").strip()
        if not ref:
            return
        try:
            conn = self._conectar()
            try:
                nombre_resp = self._resolver_nombre_usuario_ruta(conn, responsable_id)
                identificadores = [nombre_resp]
                ids_ayudante = [
                    a.strip() for a in re.split(r"[,/]| y ", str(ayudante_id or ""), flags=re.IGNORECASE)
                    if a.strip()
                ]
                identificadores += [self._resolver_nombre_usuario_ruta(conn, a) for a in ids_ayudante]

                for nombre in identificadores:
                    ya = conn.execute(
                        "SELECT id FROM log_puntos WHERE usuario = ? AND modulo = ? AND referencia_id = ? LIMIT 1",
                        (nombre, self.MODULO_RUTA, ref),
                    ).fetchone()
                    if ya:
                        continue
                    conn.execute(
                        "INSERT INTO log_puntos (usuario, modulo, referencia_id, cantidad_renglones, puntos_ganados, fecha_registro) "
                        "VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))",
                        (nombre, self.MODULO_RUTA, ref, int(total_notas or 0), puntos),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print(f"[SqliteRouteRepository] No se pudo registrar puntos de ruta local: {e}")

    def listar_rutas_macro_activas(self) -> List[str]:
        """Nombres de Macro-Ruta con AL MENOS una sesión activa ahora mismo
        (de cualquier operador) — usado por el "resumen de todas las rutas"
        del chat (progreso_ruta / listar_progreso_rutas), para no obligar a
        conocer el nombre exacto de antemano."""
        conn = self._conectar()
        try:
            filas = conn.execute(
                "SELECT DISTINCT ruta_macro FROM sesiones_ruta_activa "
                "WHERE TRIM(COALESCE(ruta_macro, '')) != '' ORDER BY ruta_macro"
            ).fetchall()
            return [f["ruta_macro"] for f in filas]
        finally:
            conn.close()

    def listar_sesiones_por_ruta_macro(self, ruta_macro: str) -> List[dict]:
        """Todas las sesiones activas (de cualquier operador) sobre una
        Macro-Ruta por nombre — varios operadores pueden estar trabajando
        distintas sub-rutas de la misma macro-ruta a la vez."""
        conn = self._conectar()
        try:
            filas = conn.execute(
                "SELECT user_id, ruta_macro, sede, datos_json, actualizado_en "
                "FROM sesiones_ruta_activa WHERE UPPER(ruta_macro) = UPPER(?)",
                (str(ruta_macro or "").strip(),),
            ).fetchall()
            return [dict(f) for f in filas]
        finally:
            conn.close()

    def borrar_sesion_ruta(self, user_id: str) -> None:
        conn = self._conectar()
        try:
            conn.execute(
                "DELETE FROM sesiones_ruta_activa WHERE user_id = ?",
                (str(user_id or "").strip(),),
            )
            conn.commit()
        finally:
            conn.close()

    def listar_notas_rutagrama(self, estado: Optional[str] = None) -> List[dict]:
        """Notas vinculadas al rutagrama, opcionalmente por estado."""
        conn = self._conectar()
        try:
            if estado:
                filas = conn.execute(
                    "SELECT * FROM rutagrama_notas WHERE estado = ? ORDER BY id DESC",
                    (str(estado),),
                ).fetchall()
            else:
                filas = conn.execute(
                    "SELECT * FROM rutagrama_notas ORDER BY id DESC"
                ).fetchall()
            return [dict(f) for f in filas]
        finally:
            conn.close()

    # ── Macro-Rutas Multi-Sede (embalaje → despacho por factura) ────────────
    def get_macro_ruta_activa(self, sede_id: str) -> Optional[dict]:
        """Macro-Ruta ACTIVA de la sede (SC | BQTO) con sus notas, o None."""
        return self._get_macro_ruta_por_estado(sede_id, "ACTIVA")

    def _get_macro_ruta_por_estado(self, sede_id: str, estado: str) -> Optional[dict]:
        sede = str(sede_id or "").strip().upper()
        conn = self._conectar()
        try:
            fila = conn.execute(
                "SELECT * FROM macro_rutas WHERE sede_id = ? AND estado = ? "
                "ORDER BY creada_en DESC LIMIT 1",
                (sede, estado),
            ).fetchone()
            if not fila:
                return None
            d = dict(fila)
            notas = conn.execute(
                "SELECT * FROM macro_ruta_notas WHERE macro_id = ? ORDER BY id",
                (d["id"],),
            ).fetchall()
            d["notas"] = [dict(n) for n in notas]
            return d
        finally:
            conn.close()

    def get_macro_ruta(self, macro_id: str) -> Optional[dict]:
        """Macro-Ruta por su id (ej: MACRO-SC-20260806) con sus notas."""
        macro_id = str(macro_id or "").strip()
        conn = self._conectar()
        try:
            fila = conn.execute(
                "SELECT * FROM macro_rutas WHERE id = ?", (macro_id,)
            ).fetchone()
            if not fila:
                return None
            d = dict(fila)
            notas = conn.execute(
                "SELECT * FROM macro_ruta_notas WHERE macro_id = ? ORDER BY id",
                (macro_id,),
            ).fetchall()
            d["notas"] = [dict(n) for n in notas]
            return d
        finally:
            conn.close()

    def get_macro_rutas(self, sede_id: Optional[str] = None,
                        estado: Optional[str] = None) -> List[dict]:
        """Histórico de Macro-Rutas (filtros opcionales de sede y estado)."""
        sql = "SELECT * FROM macro_rutas WHERE 1=1"
        params: list = []
        if sede_id:
            sql += " AND sede_id = ?"
            params.append(str(sede_id).strip().upper())
        if estado:
            sql += " AND estado = ?"
            params.append(str(estado))
        sql += " ORDER BY creada_en DESC"
        conn = self._conectar()
        try:
            filas = conn.execute(sql, params).fetchall()
            registros = []
            for f in filas:
                d = dict(f)
                notas = conn.execute(
                    "SELECT * FROM macro_ruta_notas WHERE macro_id = ? ORDER BY id",
                    (d["id"],),
                ).fetchall()
                d["notas"] = [dict(n) for n in notas]
                registros.append(d)
            return registros
        finally:
            conn.close()

    def guardar_macro_ruta(self, macro: dict) -> dict:
        """Persiste la Macro-Ruta (upsert) y sus notas (upsert idempotente).

        `macro` debe traer: id, sede_id, fecha, estado, notas[{num_nota,
        num_factura, co_cli, razon_social, total_bultos, estado_despacho,
        zona_destino, operador_embalaje}]. Retorna el dict persistido.
        """
        macro_id = str(macro.get("id") or "").strip()
        if not macro_id:
            raise ValueError("macro.id es obligatorio")
        sede = str(macro.get("sede_id") or "BQTO").strip().upper()
        fecha = str(macro.get("fecha") or "").strip()
        estado = str(macro.get("estado") or "ACTIVA").strip().upper()
        conn = self._conectar()
        try:
            previo = conn.execute(
                "SELECT id FROM macro_rutas WHERE id = ?", (macro_id,)
            ).fetchone()
            if previo:
                conn.execute(
                    "UPDATE macro_rutas SET sede_id = ?, fecha = ?, estado = ?, "
                    "cerrada_en = CASE WHEN ? = 'CERRADA' THEN datetime('now','localtime') "
                    "                     ELSE cerrada_en END "
                    "WHERE id = ?",
                    (sede, fecha, estado, estado, macro_id),
                )
            else:
                conn.execute(
                    "INSERT INTO macro_rutas (id, sede_id, fecha, estado) "
                    "VALUES (?, ?, ?, ?)",
                    (macro_id, sede, fecha, estado),
                )
            for n in macro.get("notas") or []:
                num_nota = str(n.get("num_nota") or "").strip()
                if not num_nota:
                    continue
                conn.execute(
                    "INSERT INTO macro_ruta_notas "
                    "(macro_id, num_nota, num_factura, co_cli, razon_social, "
                    " total_bultos, estado_despacho, zona_destino, "
                    " operador_embalaje, fecha_embalaje, fecha_despacho, despachado_por) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "        COALESCE(?, datetime('now','localtime')), ?, ?) "
                    "ON CONFLICT(macro_id, num_nota) DO UPDATE SET "
                    " num_factura = COALESCE(excluded.num_factura, macro_ruta_notas.num_factura), "
                    " co_cli = COALESCE(NULLIF(excluded.co_cli, ''), macro_ruta_notas.co_cli), "
                    " razon_social = COALESCE(NULLIF(excluded.razon_social, ''), macro_ruta_notas.razon_social), "
                    " total_bultos = MAX(excluded.total_bultos, macro_ruta_notas.total_bultos), "
                    " estado_despacho = CASE WHEN macro_ruta_notas.estado_despacho = 'DESPACHADO' "
                    "                            THEN macro_ruta_notas.estado_despacho "
                    "                            ELSE excluded.estado_despacho END, "
                    " zona_destino = COALESCE(NULLIF(excluded.zona_destino, ''), macro_ruta_notas.zona_destino), "
                    " operador_embalaje = COALESCE(NULLIF(excluded.operador_embalaje, ''), macro_ruta_notas.operador_embalaje), "
                    " fecha_despacho = COALESCE(excluded.fecha_despacho, macro_ruta_notas.fecha_despacho), "
                    " despachado_por = COALESCE(NULLIF(excluded.despachado_por, ''), macro_ruta_notas.despachado_por)",
                    (
                        macro_id,
                        num_nota,
                        str(n.get("num_factura") or "").strip(),
                        str(n.get("co_cli") or "").strip(),
                        str(n.get("razon_social") or "").strip(),
                        int(n.get("total_bultos") or 0),
                        str(n.get("estado_despacho") or "PENDIENTE").strip().upper(),
                        str(n.get("zona_destino") or "").strip(),
                        str(n.get("operador_embalaje") or "").strip(),
                        str(n.get("fecha_embalaje") or "").strip() or None,
                        str(n.get("fecha_despacho") or "").strip() or None,
                        str(n.get("despachado_por") or "").strip(),
                    ),
                )
            conn.commit()
            return self.get_macro_ruta(macro_id)
        finally:
            conn.close()

    def marcar_item_macro_despachado(self, macro_id: str, num_nota: str,
                                     despachado_por: str = "") -> bool:
        """Marca el ítem de la Macro-Ruta como DESPACHADO (solo si estaba PENDIENTE)."""
        macro_id = str(macro_id or "").strip()
        p = str(num_nota or "").strip()
        if not macro_id or not p:
            return False
        despachado_por = str(despachado_por or "").strip()
        conn = self._conectar()
        try:
            cur = conn.execute(
                "UPDATE macro_ruta_notas SET estado_despacho = 'DESPACHADO', "
                "fecha_despacho = datetime('now','localtime'), despachado_por = ? "
                "WHERE macro_id = ? AND num_nota = ? AND estado_despacho = 'PENDIENTE'",
                (despachado_por, macro_id, p),
            )
            if cur.rowcount > 0:
                conn.commit()
                return True
            # Tolerancia de formato (prefijos de serie S/C).
            import re as _re
            dig = _re.sub(r"\D", "", p).lstrip("0")
            if not dig:
                return False
            filas = conn.execute(
                "SELECT id FROM macro_ruta_notas "
                "WHERE macro_id = ? AND estado_despacho = 'PENDIENTE'",
                (macro_id,),
            ).fetchall()
            objetivo = None
            for f in filas:
                fila = conn.execute(
                    "SELECT num_nota FROM macro_ruta_notas WHERE id = ?", (f["id"],)
                ).fetchone()
                if fila and _re.sub(r"\D", "", str(fila["num_nota"] or "")).lstrip("0") == dig:
                    objetivo = f["id"]
                    break
            if objetivo is None:
                return False
            conn.execute(
                "UPDATE macro_ruta_notas SET estado_despacho = 'DESPACHADO', "
                "fecha_despacho = datetime('now','localtime'), despachado_por = ? "
                "WHERE id = ?",
                (despachado_por, objetivo),
            )
            conn.commit()
            return True
        finally:
            conn.close()

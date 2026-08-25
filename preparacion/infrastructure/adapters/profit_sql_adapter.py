"""
ProfitSQLAdapter — Conexión SQL directa a Profit Plus (CRISTM25, 192.168.4.20).

Fuente de VERDAD para las NOTAS DE SAN CRISTÓBAL (S/C, serie 'A...'): se
consultan en la BD CRISTM25 saltando el `rep_not` del Legacy PHP de
Barquisimeto que no registra las notas S/C.

Esquema REAL verificado (probe read-only, ago/2026):
  - Encabezado: `not_dep` existe pero con solo 3 filas de prueba; las notas
    reales S/C viven en `not_ent.fact_num` (ej. 467959 → FAR00295).
  - Renglones: `reng_nd` NO existe; `reng_nde` es la tabla real de notas de
    entrega (FK `fact_num`), `reng_ndd` para despachos y `reng_fac` solo para
    notas totalizadas (FK `num_doc` + `tipo_doc IN ('E','N','')`; el `fact_num`
    del renglón es la FACTURA, nunca la nota).
  - Ubicación real: `art.campo7` por co_art (misma lectura de
    SincronizadorCampo7); `reng_nde` no trae campo7.

Se iteran las tablas candidatas (not_dep → not_ent, reng_nd → reng_fac) con
resolución dinámica de columnas vía INFORMATION_SCHEMA (patrón de
profit_facturador.py): nunca se asumen nombres de columna.

Configuración (orden de precedencia):
  1. Env PROFIT_SQL_SERVER/PORT/DATABASE/USER/PASSWORD
  2. DB_CONFIG de ara/ARA_Brain/config.py (se ignora si trae placeholders)
  3. Env PROFIT_DB_HOST/PORT/NAME/USER/PASS (config CRISTM25 verificada en producción)
  4. Defaults: 192.168.4.20:1433 / CRISTM25 / profit / profit

Los métodos del puerto que aún son TODO (rep_not) se mantienen como stubs
hasta activar USE_DIRECT_SQL=true.
"""
import os
import traceback
from typing import List, Optional

from ...domain.models import (
    ItemNota,
    NotaNotFoundError,
    NotaNotVerifiedError,
    NotaPreparacion,
)
from ...domain.notas_sc import resolver_almacen_picking
from ...domain.ports import PreparationRepositoryPort

# Tablas/columnas candidatas por rol (en orden de preferencia)
_CAND_TABLAS_ENC = ["not_dep", "not_ent"]
# Renglones por tipo de documento (verificado en CRISTM25):
#   reng_nd  (no existe) / reng_ndd (despacho, FK fact_num)
#   reng_nde (nota de ENTREGA, FK fact_num)  ← tabla real para notas S/C
#   reng_fac (solo notas TOTALIZADAS, FK num_doc; fact_num = factura)
_CAND_TABLAS_RENG = ["reng_nd", "reng_ndd", "reng_nde", "reng_fac"]
_CAND_FACT_NUM = ["fact_num", "num_fac", "numero_nota", "factura", "nro_factura"]
_CAND_CO_CLI = ["co_cli", "co_cliente", "cod_cli"]
_CAND_CO_ART = ["co_art", "cod_art", "articulo"]
_CAND_ART_DES = ["art_des", "descripcion", "des_art"]
_CAND_TOTAL = ["total_art", "cantidad", "cant", "total"]
_CAND_CARGADA = ["stotal_art", "cant_imp", "cant_prod", "cant_cargada", "cant_verificada"]
_CAND_PENDIENTE = ["pendiente"]
_CAND_CAMPO7 = ["campo7", "ubicacion"]
_CAND_RENG_NUM = ["reng_num", "nro_linea", "linea"]
_CAND_NUM_DOC = ["num_doc", "nro_doc", "documento"]

_DEFAULT_CONN = {
    "server": os.environ.get("PROFIT_DB_HOST", "192.168.4.20"),
    "port": os.environ.get("PROFIT_DB_PORT", "1433"),
    "database": os.environ.get("PROFIT_DB_NAME", "CRISTM25"),
    "user": os.environ.get("PROFIT_DB_USER", "profit"),
    "password": os.environ.get("PROFIT_DB_PASS", "profit"),
    "driver": os.environ.get("PROFIT_DB_DRIVER", "SQL Server"),
    "timeout_s": os.environ.get("PROFIT_DB_TIMEOUT_S", "8"),
}

MENSAJE_NO_DISPONIBLE = (
    "ProfitSQLAdapter: credenciales SQL Profit no activas. "
    "Configure USE_DIRECT_SQL=true y las credenciales en config.py para habilitar "
    "la consulta directa a rep_not."
)


class ProfitSQLAdapter(PreparationRepositoryPort):
    def __init__(self, db_config: Optional[dict] = None):
        cfg = db_config if db_config is not None else self._cargar_config()
        self._base_cfg = cfg
        self._timeout = int(cfg.get("timeout_s") or _DEFAULT_CONN["timeout_s"])
        # Conexiones por base de datos (contexto de almacén): la S/C usa
        # CRISTM25 y la de Barquisimeto PROFIT_BQTO (mismo servidor Profit).
        self._conns: dict = {}
        self._columnas: dict = {}

    @staticmethod
    def _cargar_config() -> dict:
        cfg = {
            "server": os.getenv("PROFIT_SQL_SERVER") or _DEFAULT_CONN["server"],
            "port": os.getenv("PROFIT_SQL_PORT") or _DEFAULT_CONN["port"],
            "database": os.getenv("PROFIT_SQL_DATABASE") or _DEFAULT_CONN["database"],
            "user": os.getenv("PROFIT_SQL_USER") or _DEFAULT_CONN["user"],
            "password": os.getenv("PROFIT_SQL_PASSWORD") or _DEFAULT_CONN["password"],
        }
        try:
            from config import DB_CONFIG

            for clave in ("server", "database", "user", "password"):
                # Se ignora DB_CONFIG si trae placeholders ('...NOMBRE...')
                valor = DB_CONFIG.get(clave)
                if valor and not cfg.get(clave) and "NOMBRE" not in str(valor).upper():
                    cfg[clave] = valor
        except Exception:
            pass
        cfg["driver"] = _DEFAULT_CONN["driver"]
        cfg["timeout_s"] = _DEFAULT_CONN["timeout_s"]
        return cfg

    # ── Conexión y esquema (perezoso, cacheado por base de datos) ────────────
    def _conn_str_db(self, database: str) -> str:
        cfg = self._base_cfg
        return (
            f"DRIVER={{{cfg['driver']}}};SERVER={cfg['server']},{cfg['port']};"
            f"DATABASE={database};UID={cfg['user']};PWD={cfg['password']}"
        )

    def _conectar(self, db: Optional[str] = None):
        database = db or self._base_cfg["database"]
        if database not in self._conns:
            import pyodbc  # import local: dependencia solo de este adaptador

            # Candado anti-zombi (v4.36): desactiva el pooling del ODBC
            # Driver Manager a nivel de proceso — equivalente Python del
            # ConnectionPooling=0 ya usado en el lado PHP (ConnectionWrapper).
            # Debe fijarse antes de la primera conexión pyodbc del proceso.
            pyodbc.pooling = False
            self._conns[database] = pyodbc.connect(
                self._conn_str_db(database), timeout=self._timeout
            )
        return self._conns[database]

    def cerrar(self) -> None:
        """Candado anti-zombi (v4.36): cierra TODAS las conexiones cacheadas
        (una por base de datos). Se llama al final de cada método público que
        toca Profit — nunca se deja una conexión viva entre llamadas."""
        for conn in self._conns.values():
            try:
                conn.close()
            except Exception:
                pass
        self._conns = {}

    def _columnas_tabla(self, tabla: str, db: Optional[str] = None):
        database = db or self._base_cfg["database"]
        key = (database, tabla)
        if key not in self._columnas:
            cur = self._conectar(database).cursor()
            try:
                self._columnas[key] = [
                    r[0]
                    for r in cur.execute(
                        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_NAME = ? AND TABLE_SCHEMA = 'dbo' "
                        "ORDER BY ORDINAL_POSITION",
                        tabla,
                    ).fetchall()
                ]
            except Exception:
                self._columnas[key] = []
        return self._columnas[key]

    @staticmethod
    def _resolver(cols: List[str], candidatas: List[str]) -> Optional[str]:
        lower = {c.lower(): c for c in cols}
        for c in candidatas:
            if c.lower() in lower:
                return lower[c.lower()]
        return None

    # ── Notas de SAN CRISTÓBAL (serie 'A...') → CRISTM25 directo ─────────────
    def verificar_nota_sc_existe(self, numero: str) -> bool:
        """SELECT 1 en el encabezado de despacho (not_dep → not_ent) de CRISTM25.

        Aplica la discriminación automática de almacén por número
        (resolver_almacen_picking): si la nota no es de SAN CRISTÓBAL
        (num_nota > 5000000) retorna False sin tocar la BD. No lanza
        excepciones: devuelve False ante cualquier error.
        """
        numero = str(numero or "").strip()
        if not numero:
            return False
        almacen = resolver_almacen_picking(numero)
        if almacen["origen"] != "SAN_CRISTOBAL":
            return False
        try:
            conn = self._conectar(almacen["db"])
            cur = conn.cursor()
            for tabla in _CAND_TABLAS_ENC:
                cols = self._columnas_tabla(tabla, almacen["db"])
                if not cols:
                    continue
                col_fact = self._resolver(cols, _CAND_FACT_NUM)
                if not col_fact:
                    continue
                fila = cur.execute(
                    f"SELECT 1 FROM dbo.[{tabla}] WHERE [{col_fact}] = ?", (numero,)
                ).fetchone()
                if fila is not None:
                    return True
            return False
        except Exception as e:
            print(f"[ProfitSQLAdapter] verificar_nota_sc_existe({numero}) falló: {e}")
            return False
        finally:
            # Candado anti-zombi (v4.36): no queda ninguna conexión viva
            # esperando a la próxima llamada.
            self.cerrar()

    def get_nota_sc_por_numero(self, numero: str) -> NotaPreparacion:
        """Carga una nota S/C desde CRISTM25.

        Encabezado: primera tabla existente (not_dep → not_ent) que tenga la
        fila (not_dep existe en CRISTM25 pero con datos de prueba; las notas
        reales S/C viven en not_ent). Renglones: reng_nd → reng_fac (en
        CRISTM25 real es reng_fac, llave num_doc + tipo_doc 'E'/'N'/'').
        Ubicación: campo7 del propio renglón si existe, si no `art.campo7`.

        Aplica resolver_almacen_picking: conecta a la base del contexto
        (CRISTM25) y filtra los renglones de picking por los almacenes de
        despacho SAN CRISTÓBAL ('01'/'02').

        Lanza NotaNotFoundError si ningún encabezado existe y
        NotaNotVerifiedError si no se pudo consultar o no devolvió artículos.
        """
        numero = str(numero or "").strip()
        if not numero:
            raise NotaNotFoundError(numero, "Ingrese un número de nota S/C válido.")
        almacen = resolver_almacen_picking(numero)
        if almacen["origen"] != "SAN_CRISTOBAL":
            raise NotaNotFoundError(
                numero,
                f"La nota {numero} pertenece al almacén {almacen['origen']} "
                f"(num_nota > 5000000 → {almacen['db']}); consúltela por la vía de Barquisimeto.",
            )
        numero = almacen["num_nota"]
        db = almacen["db"]
        co_alma_list = almacen["co_alma_list"]
        try:
            conn = self._conectar(db)
            cur = conn.cursor()

            # ── Maestro de artículos (dbo.art) para el LEFT JOIN ───────────────
            # Descripción REAL (art_des) y ubicación (art.campo7) viven en el
            # maestro de artículos de CRISTM25, no en las tablas de renglones
            # (reng_nde no trae ni art_des ni campo7).
            cols_art = self._columnas_tabla("art", db)
            col_art_cod = self._resolver(cols_art, _CAND_CO_ART)
            col_art_des = self._resolver(cols_art, _CAND_ART_DES)
            col_art_c7 = self._resolver(cols_art, _CAND_CAMPO7)

            # ── Encabezado: probar not_dep, luego not_ent ──────────────────────
            co_cli, nombre_cliente, tabla_enc = "", "", None
            for tabla in _CAND_TABLAS_ENC:
                cols_enc = self._columnas_tabla(tabla, db)
                if not cols_enc:
                    continue
                col_fact = self._resolver(cols_enc, _CAND_FACT_NUM)
                if not col_fact:
                    continue
                fila = cur.execute(
                    f"SELECT * FROM dbo.[{tabla}] WHERE [{col_fact}] = ?", (numero,)
                ).fetchone()
                if fila is None:
                    continue
                cols_fila = [d[0] for d in cur.description]
                row = dict(zip(cols_fila, fila))
                col_cli = self._resolver(cols_fila, _CAND_CO_CLI)
                co_cli = str(row.get(col_cli) or "").strip() if col_cli else ""
                nombre_cliente = self._nombre_cliente(cur, co_cli)
                tabla_enc = tabla
                break
            if tabla_enc is None:
                raise NotaNotFoundError(
                    numero,
                    f"La nota {numero} no está registrada en CRISTM25 (not_dep/not_ent).",
                )

            # ── Renglones: reng_nd → reng_ndd → reng_nde → reng_fac ────────────
            items = []
            tabla_reng = None
            for tabla in _CAND_TABLAS_RENG:
                cols_reng = self._columnas_tabla(tabla, db)
                if not cols_reng:
                    continue
                # Llave del renglón: en reng_fac SIEMPRE num_doc (su fact_num
                # guarda la FACTURA, no la nota); en las demás (reng_ndd/
                # reng_nde) primero fact_num y luego num_doc.
                if tabla == "reng_fac":
                    col_fk = self._resolver(cols_reng, _CAND_NUM_DOC)
                else:
                    col_fk = self._resolver(cols_reng, _CAND_FACT_NUM) or self._resolver(
                        cols_reng, _CAND_NUM_DOC
                    )
                if not col_fk:
                    continue
                col_cod = self._resolver(cols_reng, _CAND_CO_ART)
                # SELECT base: LEFT JOIN con dbo.art para traer la descripción
                # real (art_des) y la ubicación del maestro (art.campo7) con
                # alias propios (ara_art_des / ara_art_campo7) sin colisionar
                # con columnas propias de la tabla de renglones.
                join_cols = []
                if col_art_des:
                    join_cols.append(f"a.[{col_art_des}] AS ara_art_des")
                if col_art_c7:
                    join_cols.append(f"a.[{col_art_c7}] AS ara_art_campo7")
                join_art = None
                if col_cod and col_art_cod and join_cols:
                    join_art = (
                        f"SELECT r.*, {', '.join(join_cols)} "
                        f"FROM dbo.[{tabla}] r "
                        f"LEFT JOIN dbo.art a ON TRIM(r.[{col_cod}]) = TRIM(a.[{col_art_cod}])"
                    )
                if join_art:
                    sql = f"{join_art} WHERE r.[{col_fk}] = ?"
                else:
                    sql = f"SELECT * FROM dbo.[{tabla}] WHERE [{col_fk}] = ?"
                params: tuple = (numero,)
                pfx = "r." if join_art else ""
                lower_cols = {c.lower(): c for c in cols_reng}
                if tabla == "reng_fac" and "tipo_doc" in lower_cols:
                    sql += f" AND {pfx}tipo_doc IN ('E','N','')"
                # Picking filtrado por los almacenes de despacho del contexto
                # (regla de negocio): renglones cuyo co_alma esté en
                # co_alma_list, o sin almacén asignado.
                if "co_alma" in lower_cols and co_alma_list:
                    placeholders = ",".join("?" * len(co_alma_list))
                    sql += (
                        f" AND ({pfx}co_alma IN ({placeholders}) OR {pfx}co_alma IS NULL "
                        f"OR LTRIM(RTRIM({pfx}co_alma)) = '')"
                    )
                    params = (numero, *co_alma_list)
                col_reng = self._resolver(cols_reng, _CAND_RENG_NUM)
                if col_reng:
                    sql += f" ORDER BY {pfx}[{col_reng}]"
                filas_reng = cur.execute(sql, params).fetchall()
                if not filas_reng:
                    continue

                cols_r = [d[0] for d in cur.description]
                col_des = self._resolver(cols_reng, _CAND_ART_DES)
                col_cant = self._resolver(cols_reng, _CAND_TOTAL)
                col_cargada = self._resolver(cols_reng, _CAND_CARGADA)
                col_pend = self._resolver(cols_reng, _CAND_PENDIENTE)
                col_c7 = self._resolver(cols_reng, _CAND_CAMPO7)
                col_alma = "co_alma" if "co_alma" in lower_cols else None
                for f in filas_reng:
                    r = dict(zip(cols_r, f))
                    co_art = str(r.get(col_cod) or "").strip() if col_cod else ""
                    try:
                        cantidad = float(r.get(col_cant) or 0) if col_cant else 0.0
                    except (TypeError, ValueError):
                        cantidad = 0.0
                    # Estado REAL de escaneo en Profit: cantidad surtida
                    # (stotal_art/cant_imp/cant_prod) y estatus operativo.
                    try:
                        escaneada = float(r.get(col_cargada) or 0) if col_cargada else 0.0
                    except (TypeError, ValueError):
                        escaneada = 0.0
                    if escaneada >= cantidad and cantidad > 0:
                        estatus = "COMPLETO"
                    elif escaneada > 0:
                        estatus = "ESCANEADO"
                    else:
                        estatus = "PENDIENTE"
                    try:
                        reng_nd = int(r.get(col_reng) or 0) if col_reng else 0
                    except (TypeError, ValueError):
                        reng_nd = 0
                    # Descripción REAL: renglón → art_des (JOIN) → fallback
                    descripcion = ""
                    if col_des:
                        descripcion = str(r.get(col_des) or "").strip()
                    if not descripcion:
                        descripcion = str(r.get("ara_art_des") or "").strip()
                    # Ubicación real (campo7): renglón → art.campo7 (JOIN) → art.campo7 (query)
                    campo7 = str(r.get(col_c7) or "").strip() if col_c7 else ""
                    if not campo7:
                        campo7 = str(r.get("ara_art_campo7") or "").strip()
                    if not campo7 and co_art:
                        campo7 = self._campo7_articulo(cur, co_art)
                    co_alma = str(r.get(col_alma) or "").strip() if col_alma else ""
                    items.append(
                        ItemNota(
                            codigo_art=co_art,
                            descripcion=descripcion,
                            cantidad=cantidad,
                            cantidad_escaneada=escaneada,
                            estatus=estatus,
                            unidad="UND",
                            campo7=campo7,
                            reng_nd=reng_nd,
                            co_alma=co_alma or (co_alma_list[0] if co_alma_list else "01"),
                        )
                    )
                tabla_reng = tabla
                break
            if tabla_reng is None:
                raise NotaNotVerifiedError(
                    f"La nota {numero} no devolvió artículos en CRISTM25 (reng_nde/reng_ndd/reng_fac)."
                )

            nota = NotaPreparacion(
                codigo_nota=numero,
                codigo_cliente=co_cli,
                nombre_cliente=nombre_cliente,
                items=items,
                almacen_origen="SAN_CRISTOBAL",
            )
            print(
                f"[ProfitSQLAdapter] Nota S/C {numero} cargada desde CRISTM25 "
                f"({tabla_enc}/{tabla_reng}, {len(items)} items, cliente={nombre_cliente or co_cli})"
            )
            return nota
        except (NotaNotFoundError, NotaNotVerifiedError):
            raise
        except Exception as e:
            traceback.print_exc()
            raise NotaNotVerifiedError(
                f"Error consultando CRISTM25 para la nota {numero}: {e}"
            )
        finally:
            # Candado anti-zombi (v4.36): no queda ninguna conexión viva
            # esperando a la próxima llamada.
            self.cerrar()

    @staticmethod
    def _nombre_cliente(cur, co_cli: str) -> str:
        if not co_cli:
            return ""
        try:
            fila = cur.execute(
                "SELECT cli_des FROM dbo.clientes WHERE RTRIM(co_cli) = ?", (co_cli.strip(),)
            ).fetchone()
            return str(fila[0] or "").strip() if fila else ""
        except Exception:
            return ""

    @staticmethod
    def _campo7_articulo(cur, co_art: str) -> str:
        """Ubicación real en Profit: art.campo7 (misma lectura de SincronizadorCampo7)."""
        try:
            fila = cur.execute(
                "SELECT campo7 FROM dbo.art WHERE co_art = ?", (co_art,)
            ).fetchone()
            return str(fila[0] or "").strip() if fila else ""
        except Exception:
            return ""

    # ── Métodos del puerto aún TODO (rep_not / Legacy PHP) ───────────────────
    def _verificar_disponibilidad(self):
        raise NotImplementedError(MENSAJE_NO_DISPONIBLE)

    def get_nota_by_barcode(self, codigo_barra: str) -> NotaPreparacion:
        # TODO(profit): SELECT ne.* FROM rep_not ne WHERE ne.fact_num = ? + renglones (co_art, art_des, total_art)
        self._verificar_disponibilidad()

    def assign_preparer(self, codigo_barra: str, numero_preparador: str, cant_items: int) -> dict:
        # TODO(profit): UPDATE rep_not SET preparador = ? WHERE fact_num = ?
        self._verificar_disponibilidad()

    def registrar_despacho_legacy(
        self,
        codigo_barra: str,
        numero_preparador: str,
        cant_items: int,
        renglones_escaneados: Optional[list] = None,
    ) -> dict:
        # TODO(profit): registrar despacho en Profit (equivalente a /visor/registro.php)
        self._verificar_disponibilidad()

    def registrar_autochequeo_php(
        self,
        numero_nota: str,
        usuario_id: str,
        mesa: str = "0",
        estado: str = "AUTOCHEQUEO",
        hora: Optional[str] = None,
        renglones_escaneados: Optional[list] = None,
    ) -> dict:
        # TODO(profit): registrar autochequeo directo en Profit
        # (equivalente a chequeo/registro.php) — Mesa 0 (pasillo) + sello de hora
        self._verificar_disponibilidad()

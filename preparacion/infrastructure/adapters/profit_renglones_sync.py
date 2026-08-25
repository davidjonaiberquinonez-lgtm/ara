"""
ProfitRenglonesSync — Marca los renglones de una nota como 100% cargados en
Profit SQL Server ANTES de invocar el cierre de chequeo del PHP Legacy.

CONTEXTO (diagnóstico v3.19): al ejecutar el autochequeo de una nota, el PHP
Legacy (`visor/registro.php`) responde "Faltan articulos por cargar. Si
finaliza la nota sera enviada a revision" porque el backend PHP exige que los
renglones/artículos individuales de la nota estén marcados como
cargados/verificados en la base de datos Profit.

ESQUEMA REAL verificado (probe read-only, ago/2026, CRISTM25):
  - Los renglones de una nota de entrega viven en `reng_nde` (FK `fact_num`),
    no en `rep_not` (que NO existe como tabla en la BD Profit).
  - Columnas de cantidad: `total_art` (cantidad pedida), `stotal_art`
    (cantidad surtida), `pendiente` (cantidad aún pendiente de surtir),
    `cant_imp` / `cant_prod` (cantidad cargada/verificada).
  - Para una nota recién escaneada (ej. 72161167) se observa:
    total_art=8, stotal_art=0, pendiente=8, cant_imp=0 → el Legacy ve
    "Faltan articulos por cargar".

La sincronización IGUALA la cantidad cargada/verificada a la cantidad total
del pedido: `pendiente=0`, `stotal_art=total_art`, `cant_imp=total_art`,
`cant_prod=total_art`, `seleccion=1`. Tablas y columnas se resuelven
dinámicamente vía INFORMATION_SCHEMA (mismo patrón de profit_facturador.py y
profit_sql_adapter.py): nunca se asumen nombres.

Best-effort absoluto: si Profit no responde o no tiene la nota, retorna un
dict con estado y mensaje SIN lanzar excepción — el flujo local (SQLite) y el
cierre en el Legacy nunca se interrumpen.
"""
import os
import time
import traceback
from typing import List, Optional

# Tablas de renglones candidatas (en orden de preferencia). `rep_not` NO es
# una tabla de la BD Profit: es la vista del PHP Legacy, por eso no se incluye.
_CAND_TABLAS_RENG = ["reng_nde", "reng_ndd", "reng_fac", "reng_nd"]
_CAND_FK = ["fact_num", "num_doc", "numero_nota", "factura", "nro_factura"]
_CAND_TOTAL = ["total_art", "cantidad", "cant", "total"]
_CAND_CARGADA = ["stotal_art", "cant_imp", "cant_prod", "cant_cargada", "cant_verificada"]
_CAND_PENDIENTE = ["pendiente"]
_CAND_SELECCION = ["seleccion"]
_CAND_RENG = ["reng_num", "reng_nd", "numero_renglon", "nro_renglon"]
_CAND_CO_ART = ["co_art", "codigo_art", "codigo", "art_codigo", "cod_art"]

_DRIVER = os.environ.get("PROFIT_DB_DRIVER", "SQL Server")
_HOST = os.environ.get("PROFIT_DB_HOST", "192.168.4.20")
_PORT = os.environ.get("PROFIT_DB_PORT", "1433")
_DB = os.environ.get("PROFIT_DB_NAME", "CRISTM25")
_USER = os.environ.get("PROFIT_DB_USER", "profit")
_PASS = os.environ.get("PROFIT_DB_PASS", "profit")
_TIMEOUT_S = int(os.environ.get("PROFIT_DB_TIMEOUT_S", "8"))

# SQLSTATE de pyodbc que indican conexión rota/perdida o timeout de red (v4.0):
#   08S01 = Communication link failure | 08001 = no se pudo abrir la conexión
#   08003 = conexión cerrada           | 08S00/08S02 = roturas de enlace
#   HYT00 = timeout de espera de respuesta del servidor
#   HYT01 = timeout al abrir la conexión
#   10053/10054 = conexión abortada/reset (Winsock, nativo bajo 08S01)
_SQLSTATES_CONEXION = {
    "08S01", "08001", "08003", "08007", "08S00", "08S02",
    "HYT00", "HYT01", "10053", "10054",
}
# Reconexión en caliente (v4.0): máximo de reintentos tras detectar error de
# conexión (directiva: "máximo 2 reintentos").
_REINTENTOS_CONEXION = 2

LOG_OK = "[ARA_SYNC] ✅ Autochequeo y renglones sincronizados exitosamente en BD Legacy para nota {num_nota}."


class ProfitRenglonesSync:
    """Sincroniza los renglones de una nota en Profit SQL (marcado como cargado).

    Uso (inyectado en el flujo de autochequeo):
        sync = ProfitRenglonesSync()
        sync.marcar_renglones_cargados("72161167")
    """

    def __init__(self, conn_str: Optional[str] = None, timeout: float = None):
        self._conn_str = conn_str or (
            f"DRIVER={{{_DRIVER}}};SERVER={_HOST},{_PORT};DATABASE={_DB};"
            f"UID={_USER};PWD={_PASS}"
        )
        self._timeout = timeout if timeout is not None else _TIMEOUT_S
        self._conn = None
        self._columnas: dict = {}

    # ── Conexión y esquema (perezoso, cacheado) ───────────────────────────────
    def _conectar(self):
        if self._conn is None:
            import pyodbc  # import local: dependencia solo de este sincronizador

            # Candado anti-zombi (v4.36): desactiva el pooling del ODBC
            # Driver Manager a nivel de proceso — equivalente Python del
            # ConnectionPooling=0 ya usado en el lado PHP (ConnectionWrapper).
            pyodbc.pooling = False
            self._conn = pyodbc.connect(self._conn_str, timeout=self._timeout)
        return self._conn

    # ── Reconexión en caliente (v4.0) ─────────────────────────────────────────
    @staticmethod
    def _es_error_conexion(exc: Exception) -> bool:
        """True si el error pyodbc indica una conexión rota/perdida (v4.0).

        SQLSTATE 08S01 (Communication link failure), 08001 (no se pudo abrir
        la conexión), 08003 (conexión cerrada), HYT00/HYT01 (timeouts) o los
        mensajes clásicos de enlace caído ("connection is closed/busy").
        Cualquier otro error de pyodbc (sintaxis, restricciones, etc.) NO
        dispara reconexión: se relanza tal cual.
        """
        sqlstate = ""
        if getattr(exc, "args", None):
            try:
                sqlstate = str(exc.args[0]).upper()
            except Exception:
                sqlstate = ""
        mensaje = str(exc).lower()
        if sqlstate and sqlstate in _SQLSTATES_CONEXION:
            return True
        return (
            "communication link failure" in mensaje
            or "link failure" in mensaje
            or "connection is closed" in mensaje
            or "connection is busy" in mensaje
        )

    def _cerrar_conexion(self) -> None:
        """Fuerza el cierre de la conexión activa (invalida `self._conn`).

        La próxima llamada a `_conectar()` abre una conexión fresca.
        """
        conn = self._conn
        self._conn = None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _con_ejecutar(self, fn, max_reintentos: int = _REINTENTOS_CONEXION):
        """Ejecuta `fn(cur)` con reconexión en caliente ante errores de conexión.

        Si pyodbc reporta una conexión rota (08S01/08001/HYT00/cerrada):
          1. Fuerza el cierre de la conexión activa (`_cerrar_conexion()`).
          2. Reabre la conexión (`_conectar()`).
          3. Reintenta la ejecución (hasta `max_reintentos` reintentos).
        Cualquier otro error se relanza tal cual (nunca se enmascara).
        """
        import pyodbc  # import local: misma política que _conectar()

        for intento in range(max_reintentos + 1):
            try:
                return fn(self._conectar().cursor())
            except pyodbc.Error as e:
                if intento < max_reintentos and self._es_error_conexion(e):
                    self._print_sync(
                        f"[ProfitRenglonesSync] ⚠️ Error de conexión SQL Server "
                        f"({getattr(e, 'args', None) or type(e).__name__}): "
                        f"reconectando en caliente… (intento {intento + 1}/"
                        f"{max_reintentos})"
                    )
                    self._cerrar_conexion()
                    continue
                raise

    def _ejecutar_update(self, sql: str, params: list, commit: bool = False) -> int:
        """UPDATE (+COMMIT opcional) con reconexión en caliente (v4.0).

        Idempotente: el marcaje siempre setea los mismos valores (estado final
        del renglón), por lo que re-ejecutar tras reconectar es seguro.
        Retorna el rowcount del cursor.
        """
        resultado = {"rowcount": 0}

        def _run(cur):
            cur.execute(sql, params)
            resultado["rowcount"] = max(int(cur.rowcount), 0)
            if commit and self._conn is not None:
                self._conn.commit()

        self._con_ejecutar(_run)
        return resultado["rowcount"]

    def _commit_reintento(self) -> None:
        """COMMIT con reconexión en caliente (v4.0)."""
        self._con_ejecutar(lambda cur: self._conectar().commit())

    def _columnas_tabla(self, tabla: str) -> List[str]:
        if tabla not in self._columnas:
            filas = self._con_ejecutar(
                lambda cur: cur.execute(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_NAME = ? AND TABLE_SCHEMA = 'dbo' "
                    "ORDER BY ORDINAL_POSITION",
                    tabla,
                ).fetchall()
            )
            self._columnas[tabla] = [r[0] for r in filas]
        return self._columnas[tabla]

    @staticmethod
    def _resolver(cols: List[str], candidatas: List[str]) -> Optional[str]:
        lower = {c.lower(): c for c in cols}
        for c in candidatas:
            if c.lower() in lower:
                return lower[c.lower()]
        return None

    @staticmethod
    def _print_sync(*args) -> None:
        """print seguro de logs [ARA_SYNC] en consola Windows (cp1252)."""
        import sys

        try:
            print(*args, flush=True)
        except UnicodeEncodeError:
            stdout = sys.stdout
            if stdout is not None and hasattr(stdout, "reconfigure"):
                try:
                    stdout.reconfigure(errors="replace")
                    print(*args, flush=True)
                    return
                except Exception:
                    pass
            print(*(str(a).encode("ascii", errors="replace").decode("ascii") for a in args), flush=True)

    # ── Sincronización principal ──────────────────────────────────────────────
    def estado_escaneo_nota(self, numero_nota: str) -> dict:
        """Cruza la nota con `reng_nde` y reporta el estado REAL de escaneo.

        Para cada renglón de la nota en la primera tabla que la contenga
        (reng_nde → reng_ndd → reng_fac → reng_nd), expone:
          - solicitada : cantidad pedida por Profit (total_art)
          - escaneada  : cantidad real surtida/escaneada (stotal_art /
                         cant_imp / cant_prod)
          - pendiente  : cantidad aún pendiente (columna 'pendiente' o
                         solicitada - escaneada)
          - estatus    : 'PENDIENTE' (0) | 'ESCANEADO' (0 < n < solicitada)
                         | 'COMPLETO' (n >= solicitada)

        Retorna {"status":"ok", "nota", "tabla", "renglones": [...],
                 "total_solicitada", "total_escaneada", "completo": bool} o
        {"status":"error", "aviso_legacy": ...}. NUNCA lanza excepción.
        """
        numero_nota = str(numero_nota or "").strip()
        if not numero_nota:
            return {
                "status": "error",
                "aviso_legacy": "ProfitRenglonesSync: número de nota vacío.",
            }

        t0 = time.perf_counter()
        try:
            for tabla in _CAND_TABLAS_RENG:
                cols = self._columnas_tabla(tabla)
                if not cols:
                    continue
                col_fk = self._resolver(cols, _CAND_FK)
                if not col_fk:
                    continue

                cur, filas = self._con_ejecutar(
                    lambda cur: (
                        cur,
                        cur.execute(
                            f"SELECT * FROM dbo.[{tabla}] WHERE [{col_fk}] = ?",
                            (numero_nota,),
                        ).fetchall(),
                    )
                )
                if not filas:
                    continue

                cols_fila = [d[0] for d in cur.description]
                col_total = self._resolver(cols, _CAND_TOTAL)
                col_cargada = self._resolver(cols, _CAND_CARGADA)
                col_pend = self._resolver(cols, _CAND_PENDIENTE)
                col_sel = self._resolver(cols, _CAND_SELECCION)
                col_reng = self._resolver(cols, _CAND_RENG)
                col_cod = self._resolver(cols, _CAND_CO_ART)

                renglones = []
                total_solicitada = 0.0
                total_escaneada = 0.0
                for f in filas:
                    r = dict(zip(cols_fila, f))
                    try:
                        solicitada = float(r.get(col_total) or 0) if col_total else 0.0
                    except (TypeError, ValueError):
                        solicitada = 0.0
                    try:
                        escaneada = float(r.get(col_cargada) or 0) if col_cargada else 0.0
                    except (TypeError, ValueError):
                        escaneada = 0.0
                    try:
                        pendiente = float(r.get(col_pend) or 0) if col_pend else 0.0
                    except (TypeError, ValueError):
                        pendiente = max(solicitada - escaneada, 0.0)
                    try:
                        reng_num = int(r.get(col_reng) or 0) if col_reng else 0
                    except (TypeError, ValueError):
                        reng_num = 0
                    co_art = str(r.get(col_cod) or "").strip() if col_cod else ""
                    if escaneada >= solicitada and solicitada > 0:
                        estatus = "COMPLETO"
                    elif escaneada > 0:
                        estatus = "ESCANEADO"
                    else:
                        estatus = "PENDIENTE"
                    total_solicitada += solicitada
                    total_escaneada += escaneada
                    renglones.append({
                        "reng_num": reng_num,
                        "co_art": co_art,
                        "solicitada": solicitada,
                        "escaneada": escaneada,
                        "pendiente": pendiente,
                        "estatus": estatus,
                    })

                completo = (
                    len(renglones) > 0
                    and all(r["estatus"] == "COMPLETO" for r in renglones)
                )
                self._print_sync(
                    f"[ProfitRenglonesSync] Nota {numero_nota}: estado de escaneo en "
                    f"{tabla} — {sum(1 for r in renglones if r['estatus'] == 'COMPLETO')}"
                    f"/{len(renglones)} renglones completos, "
                    f"total {total_escaneada:.0f}/{total_solicitada:.0f} "
                    f"({time.perf_counter() - t0:.2f}s)"
                )
                return {
                    "status": "ok",
                    "nota": numero_nota,
                    "tabla": tabla,
                    "renglones": renglones,
                    "total_solicitada": total_solicitada,
                    "total_escaneada": total_escaneada,
                    "completo": completo,
                    "aviso_legacy": None,
                }

            return {
                "status": "error",
                "nota": numero_nota,
                "renglones": [],
                "total_solicitada": 0.0,
                "total_escaneada": 0.0,
                "completo": False,
                "aviso_legacy": (
                    f"ProfitRenglonesSync: la nota {numero_nota} no tiene renglones "
                    "en reng_nde/reng_ndd/reng_fac/reng_nd."
                ),
            }
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "nota": numero_nota,
                "renglones": [],
                "completo": False,
                "aviso_legacy": f"ProfitRenglonesSync: no se pudo leer el estado: {e}",
            }
        finally:
            self.cerrar()  # candado anti-zombi (v4.36)

    def marcar_renglones_cargados(
        self,
        numero_nota: str,
        renglones_escaneados: Optional[list] = None,
    ) -> dict:
        """Marca los renglones de la nota con el estado REAL de escaneo.

        - Sin `renglones_escaneados` (compatibilidad v3.19): iguala la
          cantidad cargada/verificada a la cantidad total del pedido para
          cada renglón de la nota (`stotal_art`/`cant_imp`/`cant_prod` =
          total_art, `pendiente` = 0, `seleccion` = 1).
        - Con `renglones_escaneados` (list[{co_art, cantidad}]): sincroniza
          POR ÍTEM con las cantidades reales escaneadas — la cantidad cargada
          se iguala a lo escaneado, `pendiente` = total - escaneado y
          `seleccion` = 1 solo si hubo escaneo. El estado intermedio queda
          fiel al progreso del operador (cero falsos positivos de faltantes
          y cero renglones fantasma al 100%).

        Retorna {"status":"ok", "nota", "tabla", "actualizados", "renglones",
                 "completo": bool, "pendientes": [...]} o
        {"status":"error", "aviso_legacy": ...}. NUNCA lanza excepción.
        """
        numero_nota = str(numero_nota or "").strip()
        if not numero_nota:
            return {
                "status": "error",
                "aviso_legacy": "ProfitRenglonesSync: número de nota vacío.",
            }

        # Mapa co_art → cantidad escaneada real (normalizado por artículo).
        escaneadas: dict = {}
        if renglones_escaneados:
            for it in renglones_escaneados:
                if not isinstance(it, dict):
                    continue
                co = str(it.get("co_art") or it.get("codigo") or "").strip().upper()
                if not co:
                    continue
                try:
                    escaneadas[co] = float(it.get("cantidad") or it.get("escaneada") or it.get("chequeada") or 0)
                except (TypeError, ValueError):
                    escaneadas[co] = 0.0

        t0 = time.perf_counter()
        try:
            for tabla in _CAND_TABLAS_RENG:
                cols = self._columnas_tabla(tabla)
                if not cols:
                    continue
                col_fk = self._resolver(cols, _CAND_FK)
                if not col_fk:
                    continue

                cur, filas = self._con_ejecutar(
                    lambda cur: (
                        cur,
                        cur.execute(
                            f"SELECT * FROM dbo.[{tabla}] WHERE [{col_fk}] = ?",
                            (numero_nota,),
                        ).fetchall(),
                    )
                )
                if not filas:
                    continue

                cols_fila = [d[0] for d in cur.description]
                col_total = self._resolver(cols, _CAND_TOTAL)
                col_cargada = self._resolver(cols, _CAND_CARGADA)
                col_pend = self._resolver(cols, _CAND_PENDIENTE)
                col_sel = self._resolver(cols, _CAND_SELECCION)
                col_cod = self._resolver(cols, _CAND_CO_ART)
                col_reng = self._resolver(cols, _CAND_RENG)

                sets: List[str] = []
                params: list = []

                if escaneadas:
                    # ── Sync POR ÍTEM con cantidades reales ────────────────
                    # UPDATE individual por renglón: el estado intermedio
                    # refleja exactamente lo escaneado por el operador.
                    if not (col_cod and (col_total or col_cargada) and (col_pend or col_cargada or col_sel)):
                        return {
                            "status": "error",
                            "nota": numero_nota,
                            "tabla": tabla,
                            "actualizados": 0,
                            "completo": False,
                            "aviso_legacy": (
                                f"ProfitRenglonesSync: la tabla {tabla} no tiene "
                                "columnas co_art/cantidad reconocibles para el sync por ítem."
                            ),
                        }
                    pendientes = []
                    completos = 0
                    total = 0
                    for f in filas:
                        r = dict(zip(cols_fila, f))
                        co = str(r.get(col_cod) or "").strip().upper() if col_cod else ""
                        try:
                            solicitada = float(r.get(col_total) or 0) if col_total else 0.0
                        except (TypeError, ValueError):
                            solicitada = 0.0
                        escaneada = escaneadas.get(co, 0.0)
                        if escaneada >= solicitada and solicitada > 0:
                            completos += 1
                        elif escaneada > 0:
                            pendientes.append({
                                "co_art": co,
                                "solicitada": solicitada,
                                "escaneada": escaneada,
                                "estatus": "ESCANEADO",
                            })
                        else:
                            pendientes.append({
                                "co_art": co,
                                "solicitada": solicitada,
                                "escaneada": escaneada,
                                "estatus": "PENDIENTE",
                            })
                        sets_item: List[str] = []
                        params_item: list = []
                        if col_cargada:
                            sets_item.append(f"[{col_cargada}] = ?")
                            params_item.append(escaneada)
                        if col_pend:
                            sets_item.append(f"[{col_pend}] = ?")
                            params_item.append(max(solicitada - escaneada, 0.0))
                        if col_sel:
                            sets_item.append(f"[{col_sel}] = ?")
                            params_item.append(1 if escaneada > 0 else 0)
                        if not sets_item:
                            continue
                        sql_item = (
                            f"UPDATE dbo.[{tabla}] SET {', '.join(sets_item)} "
                            f"WHERE [{col_fk}] = ?"
                        )
                        params_item.append(numero_nota)
                        if col_cod and co:
                            sql_item += f" AND [{col_cod}] = ?"
                            params_item.append(str(r.get(col_cod) or "").strip())
                        self._ejecutar_update(sql_item, params_item)
                        total += 1
                    self._commit_reintento()

                    completo = len(pendientes) == 0 and total > 0
                    self._print_sync(
                        f"[ProfitRenglonesSync] Nota {numero_nota}: sync por ítem en "
                        f"{tabla} ({total} renglones, {completos} completos, "
                        f"{len(pendientes)} pendientes; {time.perf_counter() - t0:.2f}s)"
                    )
                    return {
                        "status": "ok",
                        "nota": numero_nota,
                        "tabla": tabla,
                        "actualizados": total,
                        "renglones": len(filas),
                        "completo": completo,
                        "pendientes": pendientes,
                        "aviso_legacy": None,
                    }

                # ── Sin datos de escaneo: marcado total (compatibilidad v3.19) ─
                if col_total and col_cargada:
                    sets.append(f"[{col_cargada}] = [{col_total}]")
                elif col_cargada:
                    sets.append(f"[{col_cargada}] = 0")

                if col_pend:
                    sets.append(f"[{col_pend}] = 0")

                if col_sel:
                    sets.append(f"[{col_sel}] = 1")

                if not sets:
                    return {
                        "status": "error",
                        "nota": numero_nota,
                        "tabla": tabla,
                        "actualizados": 0,
                        "aviso_legacy": (
                            f"ProfitRenglonesSync: la tabla {tabla} no tiene columnas "
                            "de cantidad/pendiente/selección reconocibles."
                        ),
                    }

                sql = (
                    f"UPDATE dbo.[{tabla}] SET {', '.join(sets)} "
                    f"WHERE [{col_fk}] = ?"
                )
                params.append(numero_nota)
                actualizados = self._ejecutar_update(sql, params, commit=True)

                self._print_sync(
                    f"[ProfitRenglonesSync] Nota {numero_nota}: "
                    f"{actualizados} renglón(es) marcados como cargados en {tabla} "
                    f"({time.perf_counter() - t0:.2f}s)"
                )
                return {
                    "status": "ok",
                    "nota": numero_nota,
                    "tabla": tabla,
                    "actualizados": actualizados,
                    "renglones": len(filas),
                    "completo": True,
                    "pendientes": [],
                    "aviso_legacy": None,
                }

            return {
                "status": "error",
                "nota": numero_nota,
                "actualizados": 0,
                "completo": False,
                "aviso_legacy": (
                    f"ProfitRenglonesSync: la nota {numero_nota} no tiene renglones "
                    "en reng_nde/reng_ndd/reng_fac/reng_nd."
                ),
            }
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "nota": numero_nota,
                "actualizados": 0,
                "completo": False,
                "aviso_legacy": f"ProfitRenglonesSync: no se pudo sincronizar renglones: {e}",
            }
        finally:
            self.cerrar()  # candado anti-zombi (v4.36)

    def cerrar(self) -> None:
        self._cerrar_conexion()

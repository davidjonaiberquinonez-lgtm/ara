"""
LegacyMySQLConnector — Conexión NATIVA a la base MySQL del servidor legacy
(192.168.4.148:3306) para ARA_SYNC.

CONTEXTO (directiva v3.30): el cierre/autochequeo vía HTTP (`visor/registro.php`,
`chequeo/registro.php`) depende de la sesión web PHP (PHPSESSID); sin sesión el
Legacy rechaza el guardado. Este conector ejecuta la MISMA transacción que esos
endpoints de forma directa en la BD MySQL del servidor legacy:

  - `confirmar_despacho(nota, ...)`: equivalente a visor/registro.php —
    UPDATE del estado de la nota (PROCESADA) + bitácora `despachos`.
  - `confirmar_chequeo(nota, ...)`: equivalente a chequeo/registro.php —
    UPDATE del estado de la nota (CHEQUEADA) + bitácora `log_chequeo`.

Tablas y columnas se resuelven dinámicamente vía INFORMATION_SCHEMA (mismo
patrón de profit_sql_adapter.py / profit_renglones_sync.py): nunca se asumen
nombres. El pool de conexiones está limitado por `MYSQL_CONNECTION_LIMIT`
(default 30) y es perezoso (no abre nada hasta el primer uso).

Best-effort absoluto: si la BD no responde, la credencial falla o la tabla no
existe, retorna un dict con estado y detalle SIN lanzar excepción — el flujo
local (SQLite) y el fallback HTTP del Legacy nunca se interrumpen.

Credenciales desde .env:
  MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_PORT, MYSQL_CONNECTION_LIMIT
"""
import os
import re
import threading
import time
import traceback
from typing import List, Optional

# Claves MYSQL_* que el conector consume del entorno (.env o env real).
_ENV_MYSQL_CLAVES = (
    "MYSQL_HOST",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "MYSQL_PORT",
    "MYSQL_CONNECTION_LIMIT",
)


def _cargar_env_defensivo() -> None:
    """Carga las claves MYSQL_* desde el .env del proyecto (setdefault).

    No pisa variables ya definidas en el entorno real. Útil cuando el
    conector se usa en scripts/arneses que no importan ara_vision (único
    cargador de dotenv del proyecto) ni llaman a load_dotenv.
    """
    ruta = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))), ".env"
    )
    if not os.path.exists(ruta):
        return
    try:
        with open(ruta, encoding="utf-8", errors="replace") as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith("#") or "=" not in linea:
                    continue
                clave, valor = linea.split("=", 1)
                clave = clave.strip()
                if clave in _ENV_MYSQL_CLAVES:
                    os.environ.setdefault(clave, valor.strip().strip('"'))
    except Exception:
        pass


# Candidatas (en orden de preferencia) para la resolución dinámica de esquema.
# NOTA: si la BD real del legacy tiene nombres distintos, se agregan aquí sin
# cambiar la lógica (el arnés bin/test_legacy_mysql_sync.py las reporta).
_CAND_TABLAS_NOTA = ["rep_not", "notas", "notas_entrega", "notas_despacho", "nota"]
_CAND_TABLAS_DESPACHO = ["despachos", "log_despachos", "log_despacho"]
_CAND_TABLAS_CHEQUEO = ["log_chequeo", "log_chequeos", "chequeos"]
_CAND_COL_ESTADO = ["estado", "status", "statu", "estatus"]
_CAND_COL_NOTA = ["not_num", "nota", "numero_nota", "nro_nota", "num_nota",
                  "codigobarra", "fact_num", "cod_nota"]
_CAND_COL_FECHA = ["fecha", "fecha_registro", "fecha_despacho", "fecha_chequeo",
                   "fec_desp", "fec_cheq"]
_CAND_COL_USUARIO = ["usuario", "responsable", "chequeador", "id_usuario"]
_CAND_COL_MONTO = ["monto", "total", "monto_nota"]
_CAND_COL_ITEMS = ["items", "cantidad_items", "total_items"]

# Valor REAL del estatus de chequeo en rep_not (verificado en vivo, chequeo/registro.php:
# "731 filas usan 'CHEQUEO'; 'CHEQUEADA' solo 1") — NUNCA usar 'CHEQUEADA' como default.
_VALOR_ESTATUS_CHEQUEO = "CHEQUEO"

# Bloque de CHEQUEO de la tabla `gestion` (trazabilidad física que lee la
# interfaz web) — mismas candidatas que chequeo/registro.php (COLS_GESTION),
# replicadas aquí para que la vía nativa MySQL escriba lo mismo que el PHP.
_CAND_TABLA_GESTION = ["gestion"]
_CAND_COL_GESTION_NOTA = ["cd_barr", "codigo", "cod_nota"]
_CAND_COL_GESTION_VERIFI = ["verifi_cheq", "verificacion_chequeo"]
_CAND_COL_GESTION_MESA = ["numeroMesa", "num_mesa", "mesa"]
_CAND_COL_GESTION_CHEQUEADOR = ["num_cheq", "chequeador"]
_CAND_COL_GESTION_TIPO = ["tip_pre", "tipo_chequeador"]
_CAND_COL_GESTION_UBICACION = ["ubicacion2", "ubicacion_chequeo"]
_CAND_COL_GESTION_HORA = ["hora2", "fecha_chequeo", "fec_cheq"]
_CAND_COL_GESTION_ITEMS = ["cant_items", "cantidad_items", "items"]
_VALOR_VERIFI_CHEQ = "VERIFICADA"
_VALOR_TIPO_CHEQ = "CHEQUEADOR"
_VALOR_UBIC_CHEQ = "CHEQUEO"

# Valor REAL del estatus de embalaje en rep_not (verificado en vivo, 2026-08-19:
# 154.951 filas usan 'EMBALADA' — es, con diferencia, el valor más común de la
# columna). Bloque de EMBALAJE de `gestion` (verificado en vivo, mismo día,
# contra filas reales con verifi_emb no vacío): verifi_emb='VERIFICADA',
# tip_emb='EMBALADOR', ubicacion3='EMBALADO', num_emb=<numero del embalador>,
# hora3=<timestamp>, num_mesa_emba=<mesa>, cant_items=<items>.
_VALOR_ESTATUS_EMBALADO = "EMBALADA"
_CAND_COL_GESTION_VERIFI_EMB = ["verifi_emb"]
_CAND_COL_GESTION_EMBALADOR = ["num_emb"]
_CAND_COL_GESTION_TIPO_EMB = ["tip_emb"]
_CAND_COL_GESTION_UBICACION_EMB = ["ubicacion3"]
_CAND_COL_GESTION_HORA_EMB = ["hora3"]
_CAND_COL_GESTION_MESA_EMB = ["num_mesa_emba"]
_VALOR_VERIFI_EMB = "VERIFICADA"
_VALOR_TIPO_EMB = "EMBALADOR"
_VALOR_UBIC_EMB = "EMBALADO"

_LOG_LOCK = threading.Lock()


def _extraer_mesa_numerica(mesa) -> int:
    """Extrae el número de mesa aunque venga con letras ('M1', 'Mesa 1', 'm-1').

    Las columnas `numeroMesa`/`num_mesa_emba` de `gestion` son INT: un
    `int("M1")` directo lanza ValueError y quedaba guardando 0 (bug real
    reportado 2026-08-20, captura de pantalla con 'Numero de Mesa de
    Embalaje: 0' pese a que el operador escribió 'M1'). Se toma el primer
    grupo de dígitos de la cadena en vez de exigir que sea un int puro.
    """
    try:
        return int(mesa)
    except (TypeError, ValueError):
        m = re.search(r"\d+", str(mesa or ""))
        return int(m.group(0)) if m else 0


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


class LegacyMySQLConnector:
    """Pool perezoso de conexiones pymysql a la BD MySQL del servidor legacy.

    Uso:
        conector = LegacyMySQLConnector()
        res = conector.confirmar_chequeo("72161167", "admin1", 0.0, 2)
        # res -> {"status": "ok"|"error"|"no_disponible", "confirmado": bool, ...}
    """

    def __init__(
        self,
        host: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        port: Optional[int] = None,
        connection_limit: Optional[int] = None,
        connect_timeout: float = 8.0,
        database: Optional[str] = None,
    ):
        _cargar_env_defensivo()
        self._host = host or os.getenv("MYSQL_HOST", "192.168.4.148")
        self._user = user or os.getenv("MYSQL_USER", "root")
        self._password = password if password is not None else os.getenv("MYSQL_PASSWORD", "")
        try:
            self._port = int(port if port is not None else os.getenv("MYSQL_PORT", "3306"))
        except (TypeError, ValueError):
            self._port = 3306
        try:
            self._limit = int(
                connection_limit if connection_limit is not None
                else os.getenv("MYSQL_CONNECTION_LIMIT", "30")
            )
        except (TypeError, ValueError):
            self._limit = 30
        self._connect_timeout = float(connect_timeout)
        self._pool: List[object] = []
        self._lock = threading.Lock()
        self._esquema_cache: dict = {}
        self._disponibilidad: Optional[bool] = None
        # BD concreta del legacy: env MYSQL_DATABASE si se define; si es None la
        # resuelve automáticamente en la primera consulta (_resolver_bd).
        self._database: Optional[str] = (database or os.getenv("MYSQL_DATABASE") or None)

    # ── Pool de conexiones (lazy, límite MYSQL_CONNECTION_LIMIT) ─────────────
    def _conectar(self):
        """Abre UNA conexión pymysql (no agrega al pool)."""
        import pymysql  # import local: dependencia solo de este conector

        return pymysql.connect(
            host=self._host,
            user=self._user,
            password=self._password,
            port=self._port,
            database=self._database or None,
            charset="utf8mb4",
            connect_timeout=self._connect_timeout,
            read_timeout=max(self._connect_timeout * 2, 16),
            write_timeout=max(self._connect_timeout * 2, 16),
            autocommit=False,
        )

    def obtener(self):
        """Préstamo de conexión del pool (crea hasta MYSQL_CONNECTION_LIMIT)."""
        with self._lock:
            if self._pool:
                conn = self._pool.pop()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                        cur.fetchone()
                    return conn
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
            try:
                conn = self._conectar()
                self._disponibilidad = True
                return conn
            except Exception:
                self._disponibilidad = False
                raise

    def liberar(self, conn) -> None:
        """Devuelve la conexión al pool (cierra si ya no sirve o el pool está lleno)."""
        if conn is None:
            return
        try:
            conn.rollback()
        except Exception:
            pass
        with self._lock:
            if len(self._pool) < self._limit:
                self._pool.append(conn)
                return
        try:
            conn.close()
        except Exception:
            pass

    def cerrar(self) -> None:
        """Cierra todo el pool (útil al final de procesos CLI)."""
        with self._lock:
            for conn in self._pool:
                try:
                    conn.close()
                except Exception:
                    pass
            self._pool = []

    @property
    def tamano_pool(self) -> int:
        with self._lock:
            return len(self._pool)

    # ── Esquema (perezoso, cacheado por base de datos) ───────────────────────
    def _resolver_bd(self, conn) -> Optional[str]:
        """Resuelve la BD concreta del legacy donde viven las tablas reales.

        El usuario MYSQL_USER del .env (jonaiber) NO tiene base por defecto
        (DATABASE() = NULL), y las tablas del sistema legacy (rep_not, gestion,
        usuarios...) viven en BDs concretas del servidor 192.168.4.148
        (verificado: `barquisimeto`, `sistema_operaciones`, `monitor_notas`).
        Busca en information_schema las BDs que contienen las tablas del grupo
        de notas y activa la primera en la conexión (`USE`). Cacheado; si la
        env MYSQL_DATABASE está definida se respeta y solo se aplica el USE.
        """
        if self._database:
            return self._database
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_schema FROM information_schema.tables "
                    "WHERE table_schema NOT IN "
                    "('information_schema','mysql','performance_schema','sys') "
                    "AND table_name IN (%s, %s, %s) "
                    "GROUP BY table_schema ORDER BY table_schema",
                    ("rep_not", "notas_entrega", "notas"),
                )
                candidatas = [r[0] for r in cur.fetchall()]
            if not candidatas:
                return None
            self._database = candidatas[0]
            self._activa_bd(conn, self._database)
            return self._database
        except Exception:
            return None

    @staticmethod
    def _activa_bd(conn, bd: str) -> None:
        """Ejecuta `USE bd` en la conexión (nombre proveniente de
        information_schema, seguro para backticks)."""
        try:
            with conn.cursor() as cur:
                cur.execute(f"USE `{bd}`")
        except Exception:
            pass

    def _tablas(self, conn) -> List[str]:
        if self._database is None:
            self._resolver_bd(conn)
        if self._database is None:
            return []
        with conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME FROM information_schema.tables "
                "WHERE table_schema = %s ORDER BY TABLE_NAME",
                (self._database,),
            )
            return [r[0] for r in cur.fetchall()]

    def _columnas(self, conn, tabla: str) -> List[str]:
        if self._database is None:
            self._resolver_bd(conn)
        if self._database is None:
            return []
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s "
                "ORDER BY ORDINAL_POSITION",
                (self._database, tabla),
            )
            return [r[0] for r in cur.fetchall()]

    @staticmethod
    def _resolver(cols: List[str], candidatas: List[str]) -> Optional[str]:
        lower = {c.lower(): c for c in cols}
        for c in candidatas:
            if c.lower() in lower:
                return lower[c.lower()]
        return None

    def _resolver_tabla(self, conn, candidatas: List[str]) -> Optional[str]:
        tablas = self._tablas(conn)
        lower = {t.lower(): t for t in tablas}
        for t in candidatas:
            if t.lower() in lower:
                return lower[t.lower()]
        return None

    # ── Verificación / diagnóstico (arnés CLI) ───────────────────────────────
    def verificar(self) -> dict:
        """Ping + esquema de las tablas candidatas (solo lectura).

        Retorna {"status":"ok", "server":..., "tablas":{tabla: [cols]}, ...}
        o {"status":"error", "aviso_legacy": ...}. NUNCA lanza excepción.
        """
        t0 = time.perf_counter()
        try:
            conn = self.obtener()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT VERSION(), DATABASE()")
                    version, base = cur.fetchone()
                if self._database is None:
                    self._resolver_bd(conn)
                base = self._database or base
                encontradas = {}
                for grupo, candidatas in (
                    ("notas", _CAND_TABLAS_NOTA),
                    ("despachos", _CAND_TABLAS_DESPACHO),
                    ("chequeo", _CAND_TABLAS_CHEQUEO),
                ):
                    tabla = self._resolver_tabla(conn, candidatas)
                    if tabla:
                        encontradas[grupo] = {
                            "tabla": tabla,
                            "columnas": self._columnas(conn, tabla),
                        }
                return {
                    "status": "ok",
                    "server": version,
                    "database": base,
                    "host": self._host,
                    "port": self._port,
                    "tablas": encontradas,
                    "latencia_ms": round((time.perf_counter() - t0) * 1000, 1),
                }
            finally:
                self.liberar(conn)
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "aviso_legacy": (
                    f"LegacyMySQLConnector: no se pudo conectar a {self._host}:{self._port}: {e}"
                ),
            }

    # ── Transacciones directas (equivalente nativo a los PHP legacy) ─────────
    def confirmar_despacho(
        self,
        nota: str,
        detalle: Optional[dict] = None,
        modo_real: bool = True,
    ) -> dict:
        """Equivalente NATIVO a visor/registro.php: marca la nota como PROCESADA.

        UPDATE estado='PROCESADA' (+ fecha) en la tabla de notas y INSERT en la
        bitácora de despachos, todo en UNA transacción con commit. `modo_real`
        False = dry-run (no ejecuta, solo reporta la transacción que haría).
        Best-effort: NUNCA lanza excepción.
        """
        return self._confirmar(
            nota=nota,
            valor_estado="PROCESADA",
            grupo_tabla_nota="notas",
            grupo_tabla_log="despachos",
            log_extra=detalle or {},
            modo_real=modo_real,
            origen="visor/registro.php",
        )

    def confirmar_chequeo(
        self,
        nota: str,
        responsable: str,
        monto: float = 0.0,
        total_items: int = 0,
        items: Optional[list] = None,
        mesa: str = "0",
        hora: Optional[str] = None,
        modo_real: bool = True,
    ) -> dict:
        """Equivalente NATIVO a chequeo/registro.php: rep_not + gestion (v4.23).

        Hasta v4.22 esta función SOLO actualizaba `rep_not.estatus` (con el
        valor 'CHEQUEADA', que además era incorrecto — el valor real legacy es
        'CHEQUEO', 731/732 filas lo usan así) y probaba una bitácora
        (`log_chequeo`/`chequeos`) que no existe en la BD real: por eso quedaba
        "(sin bitácora)" en el log y la web seguía mostrando "Numero de
        Chequeador"/"Hora del Registro" en 0/0000-00-00 — nunca tocaba
        `gestion`, que es la tabla que la interfaz web realmente lee para el
        bloque de chequeo (verifi_cheq/num_cheq/hora2/numeroMesa).

        Replica ahora, en UNA transacción, exactamente lo que hace
        `chequeo/registro.php::registrar_estado_legacy()`:
          1. UPDATE rep_not.estatus = 'CHEQUEO' WHERE <col_nota> = nota.
          2. Resuelve `responsable` (puede venir '03') contra `usuarios`
             (numero/id/nombre) para obtener el `numero` canónico sin ceros a
             la izquierda — gestion.num_cheq debe enlazar con usuarios.numero
             para que la web legacy sume los puntos del chequeador.
          3. UPDATE (o INSERT si la fila no existe todavía en gestion) del
             bloque de chequeo: verifi_cheq='VERIFICADA', num_cheq, tip_pre=
             'CHEQUEADOR', ubicacion2='CHEQUEO', hora2, numeroMesa, cant_items.

        `modo_real` False = dry-run (no ejecuta, solo reporta lo que haría).
        Best-effort: NUNCA lanza excepción.
        """
        nota = str(nota or "").strip()
        if not nota:
            return {
                "status": "error",
                "confirmado": False,
                "aviso_legacy": "LegacyMySQLConnector: falta el número de nota.",
            }

        hora_valida = hora if (hora and re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$", hora)) \
            else time.strftime("%Y-%m-%d %H:%M:%S")
        mesa_int = _extraer_mesa_numerica(mesa)
        # gestion.cd_barr es INT: normaliza notas compuestas ('NC-72160252') a
        # la secuencia numérica, igual que chequeo/registro.php (v4.5).
        if nota.isdigit():
            nota_busqueda = int(nota)
        else:
            m = re.search(r"\d{6,12}", nota)
            nota_busqueda = int(m.group(0)) if m else nota

        t0 = time.perf_counter()
        try:
            conn = self.obtener()
        except Exception as e:
            return {
                "status": "no_disponible",
                "confirmado": False,
                "aviso_legacy": f"MySQL legacy no disponible ({self._host}:{self._port}): {e}",
            }

        advertencias: List[str] = []
        try:
            tabla_nota = self._resolver_tabla(conn, _CAND_TABLAS_NOTA)
            tabla_gestion = self._resolver_tabla(conn, _CAND_TABLA_GESTION)

            if not modo_real:
                return {
                    "status": "dry_run",
                    "confirmado": False,
                    "nota": nota,
                    "tabla_nota": tabla_nota,
                    "tabla_gestion": tabla_gestion,
                    "origen": "chequeo/registro.php",
                    "latencia_ms": round((time.perf_counter() - t0) * 1000, 1),
                }

            filas_nota = 0
            filas_gestion = 0
            try:
                # 1) rep_not.estatus = 'CHEQUEO'
                if tabla_nota:
                    cols_nota = self._columnas(conn, tabla_nota)
                    col_estado = self._resolver(cols_nota, _CAND_COL_ESTADO)
                    col_nota_col = self._resolver(cols_nota, _CAND_COL_NOTA)
                    if col_estado and col_nota_col:
                        with conn.cursor() as cur:
                            cur.execute(
                                f"UPDATE `{tabla_nota}` SET `{col_estado}` = %s WHERE `{col_nota_col}` = %s",
                                (_VALOR_ESTATUS_CHEQUEO, nota),
                            )
                            filas_nota = cur.rowcount
                        advertencias.append(
                            f"{tabla_nota}: nota {nota} marcada como {_VALOR_ESTATUS_CHEQUEO} ({filas_nota} fila(s))."
                        )
                    else:
                        advertencias.append(f"No se actualizó {tabla_nota}: sin columnas reconocibles.")
                else:
                    advertencias.append("Ninguna tabla candidata de notas encontrada.")

                # 2) gestion: bloque de chequeo (lo que la web realmente lee)
                if tabla_gestion:
                    numero_cheq = self._resolver_usuario_numero(conn, responsable, advertencias)
                    filas_gestion = self._actualizar_gestion_chequeo(
                        conn, tabla_gestion, nota_busqueda, numero_cheq, mesa_int,
                        hora_valida, total_items, advertencias,
                    )
                else:
                    advertencias.append("Tabla `gestion` no encontrada — bloque de chequeo NO actualizado.")

                conn.commit()
            except Exception:
                conn.rollback()
                raise

            _print_sync(
                f"[ARA_SYNC] ✅ chequeo/registro.php (MySQL directo): nota {nota} "
                f"marcada como {_VALOR_ESTATUS_CHEQUEO} ({filas_nota} fila(s)) + "
                f"gestion ({filas_gestion} fila(s)) — {time.perf_counter() - t0:.2f}s"
            )
            return {
                "status": "ok",
                "confirmado": True,
                "nota": nota,
                "tabla_nota": tabla_nota,
                "tabla_gestion": tabla_gestion,
                "filas_actualizadas": int(filas_nota),
                "filas_gestion": int(filas_gestion),
                "origen": "chequeo/registro.php",
                "via": "mysql_directo",
                "advertencias": advertencias,
                "aviso_legacy": None,
            }
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "confirmado": False,
                "aviso_legacy": f"MySQL legacy: transacción de chequeo fallida para {nota}: {e}",
            }
        finally:
            self.liberar(conn)

    def confirmar_embalaje(
        self,
        nota: str,
        responsable: str,
        total_items: int = 0,
        mesa: str = "0",
        hora: Optional[str] = None,
        modo_real: bool = True,
    ) -> dict:
        """Equivalente NATIVO al bloque de embalaje de actualizar_nota_embalaje.php.

        embalaje.php (servidor legacy 192.168.4.148:8000) POSTea directo a ese
        PHP y ARA ya lo llama vía LegacyRouteAdapter.registrar_embalaje() —
        pero el usuario reportó que el registro en `gestion` (puntos del
        embalador) no siempre queda, y quiere que ARA lo garantice por su
        cuenta además de la llamada legacy existente (no en su reemplazo).

        Replica, en UNA transacción, lo mismo que hace confirmar_chequeo()
        para su bloque pero con las columnas de EMBALAJE:
          1. UPDATE rep_not.estatus = 'EMBALADA' WHERE cod_nota = nota.
          2. Resuelve `responsable` contra `usuarios.numero` (mismo criterio
             que chequeo: match exacto por numero antes que id/nombre).
          3. UPDATE (o INSERT si la fila no existe) del bloque de embalaje en
             `gestion`: verifi_emb='VERIFICADA', num_emb, tip_emb='EMBALADOR',
             ubicacion3='EMBALADO', hora3, num_mesa_emba, cant_items.

        `modo_real` False = dry-run. Best-effort: NUNCA lanza excepción.
        """
        nota = str(nota or "").strip()
        if not nota:
            return {
                "status": "error",
                "confirmado": False,
                "aviso_legacy": "LegacyMySQLConnector: falta el número de nota.",
            }

        hora_valida = hora if (hora and re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$", hora)) \
            else time.strftime("%Y-%m-%d %H:%M:%S")
        mesa_int = _extraer_mesa_numerica(mesa)
        # gestion.cd_barr es INT: misma normalización que confirmar_chequeo
        # para notas compuestas ('NC-72160252' -> 72160252).
        if nota.isdigit():
            nota_busqueda = int(nota)
        else:
            m = re.search(r"\d{6,12}", nota)
            nota_busqueda = int(m.group(0)) if m else nota

        t0 = time.perf_counter()
        try:
            conn = self.obtener()
        except Exception as e:
            return {
                "status": "no_disponible",
                "confirmado": False,
                "aviso_legacy": f"MySQL legacy no disponible ({self._host}:{self._port}): {e}",
            }

        advertencias: List[str] = []
        try:
            tabla_nota = self._resolver_tabla(conn, _CAND_TABLAS_NOTA)
            tabla_gestion = self._resolver_tabla(conn, _CAND_TABLA_GESTION)

            if not modo_real:
                return {
                    "status": "dry_run",
                    "confirmado": False,
                    "nota": nota,
                    "tabla_nota": tabla_nota,
                    "tabla_gestion": tabla_gestion,
                    "origen": "actualizar_nota_embalaje.php",
                    "latencia_ms": round((time.perf_counter() - t0) * 1000, 1),
                }

            filas_nota = 0
            filas_gestion = 0
            try:
                # 1) rep_not.estatus = 'EMBALADA'
                if tabla_nota:
                    cols_nota = self._columnas(conn, tabla_nota)
                    col_estado = self._resolver(cols_nota, _CAND_COL_ESTADO)
                    col_nota_col = self._resolver(cols_nota, _CAND_COL_NOTA)
                    if col_estado and col_nota_col:
                        with conn.cursor() as cur:
                            cur.execute(
                                f"UPDATE `{tabla_nota}` SET `{col_estado}` = %s WHERE `{col_nota_col}` = %s",
                                (_VALOR_ESTATUS_EMBALADO, nota),
                            )
                            filas_nota = cur.rowcount
                        advertencias.append(
                            f"{tabla_nota}: nota {nota} marcada como {_VALOR_ESTATUS_EMBALADO} ({filas_nota} fila(s))."
                        )
                    else:
                        advertencias.append(f"No se actualizó {tabla_nota}: sin columnas reconocibles.")
                else:
                    advertencias.append("Ninguna tabla candidata de notas encontrada.")

                # 2) gestion: bloque de embalaje (puntos del embalador)
                if tabla_gestion:
                    numero_emb = self._resolver_usuario_numero(conn, responsable, advertencias)
                    filas_gestion = self._actualizar_gestion_embalaje(
                        conn, tabla_gestion, nota_busqueda, numero_emb, mesa_int,
                        hora_valida, total_items, advertencias,
                    )
                else:
                    advertencias.append("Tabla `gestion` no encontrada — bloque de embalaje NO actualizado.")

                conn.commit()
            except Exception:
                conn.rollback()
                raise

            _print_sync(
                f"[ARA_SYNC] ✅ actualizar_nota_embalaje.php (MySQL directo): nota {nota} "
                f"marcada como {_VALOR_ESTATUS_EMBALADO} ({filas_nota} fila(s)) + "
                f"gestion ({filas_gestion} fila(s)) — {time.perf_counter() - t0:.2f}s"
            )
            return {
                "status": "ok",
                "confirmado": True,
                "nota": nota,
                "tabla_nota": tabla_nota,
                "tabla_gestion": tabla_gestion,
                "filas_actualizadas": int(filas_nota),
                "filas_gestion": int(filas_gestion),
                "origen": "actualizar_nota_embalaje.php",
                "via": "mysql_directo",
                "advertencias": advertencias,
                "aviso_legacy": None,
            }
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "confirmado": False,
                "aviso_legacy": f"MySQL legacy: transacción de embalaje fallida para {nota}: {e}",
            }
        finally:
            self.liberar(conn)

    def _actualizar_gestion_embalaje(
        self, conn, tabla_gestion: str, nota_busqueda, numero_emb: int, mesa: int,
        hora: str, total_items: int, advertencias: List[str],
    ) -> int:
        """UPDATE (o INSERT si la fila no existe) del bloque de embalaje en
        `gestion` — mismo criterio que _actualizar_gestion_chequeo(), columnas
        de embalaje en vez de chequeo."""
        cols_g = [c.lower() for c in self._columnas(conn, tabla_gestion)]
        col_nota = self._resolver(cols_g, _CAND_COL_GESTION_NOTA)
        col_verifi = self._resolver(cols_g, _CAND_COL_GESTION_VERIFI_EMB)
        if not col_nota or not col_verifi:
            advertencias.append(f"gestion: sin columnas de embalaje reconocibles (cols={cols_g}).")
            return 0

        col_mesa = self._resolver(cols_g, _CAND_COL_GESTION_MESA_EMB)
        col_emb = self._resolver(cols_g, _CAND_COL_GESTION_EMBALADOR)
        col_tipo = self._resolver(cols_g, _CAND_COL_GESTION_TIPO_EMB)
        col_ubic = self._resolver(cols_g, _CAND_COL_GESTION_UBICACION_EMB)
        col_hora = self._resolver(cols_g, _CAND_COL_GESTION_HORA_EMB)
        col_items = self._resolver(cols_g, _CAND_COL_GESTION_ITEMS)

        sets: List[str] = [f"`{col_verifi}` = %s"]
        params: list = [_VALOR_VERIFI_EMB]
        if col_emb:
            sets.append(f"`{col_emb}` = %s")
            params.append(numero_emb)
        if col_tipo:
            sets.append(f"`{col_tipo}` = %s")
            params.append(_VALOR_TIPO_EMB)
        if col_ubic:
            sets.append(f"`{col_ubic}` = %s")
            params.append(_VALOR_UBIC_EMB)
        if col_hora:
            sets.append(f"`{col_hora}` = %s")
            params.append(hora)
        if col_mesa:
            sets.append(f"`{col_mesa}` = %s")
            params.append(mesa)
        if col_items and total_items > 0:
            sets.append(f"`{col_items}` = %s")
            params.append(total_items)

        sql_update = f"UPDATE `{tabla_gestion}` SET {', '.join(sets)} WHERE `{col_nota}` = %s"
        with conn.cursor() as cur:
            cur.execute(sql_update, (*params, nota_busqueda))
            filas = cur.rowcount
        advertencias.append(f"gestion: bloque de embalaje actualizado ({filas} fila(s)) para la nota {nota_busqueda}.")

        if filas != 0:
            return filas

        # rowcount 0: fila inexistente (INSERT) o sin cambios reales — misma
        # reconfirmación defensiva que _actualizar_gestion_chequeo().
        with conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM `{tabla_gestion}` WHERE `{col_nota}` = %s LIMIT 1", (nota_busqueda,))
            existe = cur.fetchone() is not None

        if existe:
            advertencias.append("gestion: fila existente sin cambios detectados (re-embalaje idempotente).")
            return 1

        ic: List[str] = []
        iv: list = []

        def agrega(col: Optional[str], val) -> None:
            if col and col not in ic:
                ic.append(col)
                iv.append(val)

        agrega(col_nota, nota_busqueda)
        agrega(col_verifi, _VALOR_VERIFI_EMB)
        agrega(col_mesa, mesa)
        agrega(col_emb, numero_emb)
        agrega(col_tipo, _VALOR_TIPO_EMB)
        agrega(col_ubic, _VALOR_UBIC_EMB)
        agrega(col_hora, hora)
        if col_items and total_items > 0:
            agrega(col_items, total_items)
        # Defaults legacy NOT NULL (mismo criterio que _actualizar_gestion_chequeo:
        # sin esto el INSERT revienta con error 1364 y hace rollback completo).
        for c, v in (
            ("co_cli", ""), ("cli_des", ""), ("verifi_pre", ""), ("num_prep", 0),
            ("tipo_perso", ""), ("ubicacion", ""), ("hora", hora),
            ("verifi_cheq", ""), ("numeroMesa", 0), ("num_cheq", 0),
            ("tip_pre", ""), ("ubicacion2", ""), ("hora2", hora),
        ):
            if c in cols_g:
                agrega(c, v)

        cols_sql = ", ".join(f"`{c}`" for c in ic)
        marks = ", ".join(["%s"] * len(iv))
        try:
            with conn.cursor() as cur:
                cur.execute(f"INSERT INTO `{tabla_gestion}` ({cols_sql}) VALUES ({marks})", iv)
                filas_insert = cur.rowcount
            advertencias.append(f"gestion: fila creada para la nota {nota_busqueda} (INSERT {filas_insert}).")
            return filas_insert
        except Exception as e:
            if "1062" in str(e) or "Duplicate" in str(e):
                with conn.cursor() as cur:
                    cur.execute(sql_update, (*params, nota_busqueda))
                    filas_retry = cur.rowcount
                advertencias.append(f"gestion: INSERT duplicado (carrera), retry UPDATE {filas_retry} fila(s).")
                return filas_retry
            raise

    def confirmar_puntos_ruta(
        self,
        referencia: str,
        responsable: str,
        total_notas: int,
        ayudante: Optional[str] = None,
        modo_real: bool = True,
    ) -> dict:
        """Registra los puntos de cierre de ruta en `puntajes` (tabla REAL del
        legacy, verificada en vivo 2026-08-20 — NO se adivinó): el visor legacy
        ya inserta ahí, tipo='CARGAR RUTA', UNA fila 'RESPONSABLE DE RUTA #N' y
        una fila 'AYUDANTE DE RUTA #N' por CADA ayudante, con el MISMO
        `cant_puntos` en ambas (confirmado con filas reales, ej. responsable=20
        y ayudante=20 para la misma ruta) — el ayudante gana igual que el
        responsable, tal como pidió el usuario. ARA no alimentaba esta tabla
        al cerrar rutas por su cuenta (solo el visor legacy viejo lo hacía),
        así que los choferes que cierran vía ARA se quedaban sin puntos.

        `cant_puntos` = total_notas de la sub-ruta cerrada (1 pt/nota, mismo
        criterio ya usado en picking/chequeo/embalaje). `ayudante` acepta un
        identificador único o varios separados por coma/'y' (varias filas
        reales tienen 2 ayudantes para la misma ruta).

        Best-effort: NUNCA lanza excepción. `modo_real=False` = dry-run.
        """
        referencia = str(referencia or "").strip()
        responsable = str(responsable or "").strip()
        puntos = int(total_notas or 0)
        if not referencia or not responsable or puntos <= 0:
            return {
                "status": "error",
                "confirmado": False,
                "aviso_legacy": "LegacyMySQLConnector: faltan datos para registrar puntos de ruta.",
            }

        try:
            conn = self.obtener()
        except Exception as e:
            return {
                "status": "no_disponible",
                "confirmado": False,
                "aviso_legacy": f"MySQL legacy no disponible ({self._host}:{self._port}): {e}",
            }

        advertencias: List[str] = []
        try:
            tabla_puntajes = self._resolver_tabla(conn, ["puntajes"])
            if not tabla_puntajes:
                return {
                    "status": "error",
                    "confirmado": False,
                    "aviso_legacy": "Tabla `puntajes` no encontrada en el legacy.",
                }

            if not modo_real:
                return {
                    "status": "dry_run",
                    "confirmado": False,
                    "referencia": referencia,
                    "puntos": puntos,
                    "latencia_ms": 0.0,
                }

            filas_insertadas = 0
            try:
                num_resp = self._resolver_usuario_numero(conn, responsable, advertencias)
                with conn.cursor() as cur:
                    cur.execute(
                        f"INSERT INTO `{tabla_puntajes}` "
                        "(num_trab, nombre, tipo, cant_puntos, puntos_positivos, puntos_negativos, obvervacion, fecha) "
                        "VALUES (%s, '', 'CARGAR RUTA', %s, 0, 0, %s, NOW())",
                        (num_resp, puntos, f"RESPONSABLE DE RUTA #{referencia}"),
                    )
                    filas_insertadas += cur.rowcount

                # Uno o varios ayudantes (separados por coma/'y') — MISMOS
                # puntos que el responsable, igual que el visor legacy real.
                ayudantes_ids = [
                    a.strip() for a in re.split(r"[,/]| y ", str(ayudante or ""), flags=re.IGNORECASE)
                    if a.strip()
                ]
                for aid in ayudantes_ids:
                    num_ayu = self._resolver_usuario_numero(conn, aid, advertencias)
                    with conn.cursor() as cur:
                        cur.execute(
                            f"INSERT INTO `{tabla_puntajes}` "
                            "(num_trab, nombre, tipo, cant_puntos, puntos_positivos, puntos_negativos, obvervacion, fecha) "
                            "VALUES (%s, '', 'CARGAR RUTA', %s, 0, 0, %s, NOW())",
                            (num_ayu, puntos, f"AYUDANTE DE RUTA #{referencia}"),
                        )
                        filas_insertadas += cur.rowcount

                conn.commit()
            except Exception:
                conn.rollback()
                raise

            _print_sync(
                f"[ARA_SYNC] ✅ puntajes (MySQL directo): ruta #{referencia} — "
                f"responsable {responsable} + {len(ayudantes_ids)} ayudante(s), "
                f"{puntos} pts c/u ({filas_insertadas} fila(s))"
            )
            return {
                "status": "ok",
                "confirmado": True,
                "referencia": referencia,
                "puntos": puntos,
                "filas_insertadas": filas_insertadas,
                "ayudantes": ayudantes_ids,
                "advertencias": advertencias,
                "aviso_legacy": None,
            }
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "confirmado": False,
                "aviso_legacy": f"MySQL legacy: registro de puntos de ruta fallido para #{referencia}: {e}",
            }
        finally:
            self.liberar(conn)

    def _resolver_usuario_numero(self, conn, identificador: str, advertencias: List[str]) -> int:
        """Mapea el id del chequeador contra `usuarios` y devuelve el `numero`
        canónico (sin ceros a la izquierda).

        Autochequeo (regla >3 ítems, mismo operador que preparó): el
        identificador que llega aquí YA es el mismo `preparador_id` con el
        que se escribió `gestion.num_prep` — debe resolver al MISMO
        `usuarios.numero`, nunca a otra persona.

        BUG real (2026-08-12, nota 72163448): `numero` e `id` son columnas
        INDEPENDIENTES en `usuarios` (numero='9'→MIGUEL CAMPOS id=10, pero
        id=9→PEDRO VIZCAYA numero='8'). El match anterior
        `numero=? OR id=? OR nombre=?` sin ORDER BY, con dos usuarios reales
        distintos calzando por columnas distintas, dejaba que MySQL devolviera
        cualquiera de los dos — así quedó num_cheq=8 (PEDRO) en vez de 9
        (MIGUEL, el preparador real). Fix: match EXACTO por `numero` primero
        (columna con la que el resto del flujo ya trabaja); solo si no hay
        ningún usuario con ese `numero` se cae a `id`/`nombre` como fallback.
        """
        identificador = str(identificador or "").strip()
        normalizado = identificador.lstrip("0")
        if not normalizado:
            advertencias.append("usuarios: identificador de chequeador vacío; num_cheq=0.")
            return 0
        try:
            with conn.cursor() as cur:
                # 1) Match EXACTO por numero — evita la ambigüedad numero/id.
                cur.execute(
                    "SELECT numero, nombre FROM usuarios WHERE activo = 1 "
                    "AND CAST(numero AS CHAR) = %s LIMIT 1",
                    (normalizado,),
                )
                fila = cur.fetchone()
                if fila is None:
                    # 2) Fallback: id o nombre, solo si no hubo match por numero.
                    cur.execute(
                        "SELECT numero, nombre FROM usuarios WHERE activo = 1 "
                        "AND (CAST(id AS CHAR) = %s OR UPPER(nombre) = UPPER(%s)) LIMIT 1",
                        (normalizado, identificador),
                    )
                    fila = cur.fetchone()
            if fila:
                numero, nombre = fila
                advertencias.append(f"usuarios: {identificador} -> {nombre} (numero={numero}).")
                return int(numero)
        except Exception as e:
            advertencias.append(f"usuarios: resolución falló ({e}).")
        if normalizado.isdigit():
            advertencias.append(f"usuarios: {identificador} sin match; usando valor numérico directo.")
            return int(normalizado)
        advertencias.append(f"usuarios: {identificador} sin match en usuarios; num_cheq=0.")
        return 0

    def _actualizar_gestion_chequeo(
        self, conn, tabla_gestion: str, nota_busqueda, numero_cheq: int, mesa: int,
        hora: str, total_items: int, advertencias: List[str],
    ) -> int:
        """UPDATE (o INSERT si la fila no existe) del bloque de chequeo en
        `gestion` — misma lógica que `chequeo/registro.php::registrar_estado_legacy()`."""
        cols_g = [c.lower() for c in self._columnas(conn, tabla_gestion)]
        col_nota = self._resolver(cols_g, _CAND_COL_GESTION_NOTA)
        col_verifi = self._resolver(cols_g, _CAND_COL_GESTION_VERIFI)
        if not col_nota or not col_verifi:
            advertencias.append(f"gestion: sin columnas reconocibles (cols={cols_g}).")
            return 0

        col_mesa = self._resolver(cols_g, _CAND_COL_GESTION_MESA)
        col_cheq = self._resolver(cols_g, _CAND_COL_GESTION_CHEQUEADOR)
        col_tipo = self._resolver(cols_g, _CAND_COL_GESTION_TIPO)
        col_ubic = self._resolver(cols_g, _CAND_COL_GESTION_UBICACION)
        col_hora = self._resolver(cols_g, _CAND_COL_GESTION_HORA)
        col_items = self._resolver(cols_g, _CAND_COL_GESTION_ITEMS)

        sets: List[str] = [f"`{col_verifi}` = %s"]
        params: list = [_VALOR_VERIFI_CHEQ]
        if col_cheq:
            sets.append(f"`{col_cheq}` = %s")
            params.append(numero_cheq)
        if col_tipo:
            sets.append(f"`{col_tipo}` = %s")
            params.append(_VALOR_TIPO_CHEQ)
        if col_ubic:
            sets.append(f"`{col_ubic}` = %s")
            params.append(_VALOR_UBIC_CHEQ)
        if col_hora:
            sets.append(f"`{col_hora}` = %s")
            params.append(hora)
        if col_mesa:
            sets.append(f"`{col_mesa}` = %s")
            params.append(mesa)
        if col_items and total_items > 0:
            sets.append(f"`{col_items}` = %s")
            params.append(total_items)

        sql_update = f"UPDATE `{tabla_gestion}` SET {', '.join(sets)} WHERE `{col_nota}` = %s"
        with conn.cursor() as cur:
            cur.execute(sql_update, (*params, nota_busqueda))
            filas = cur.rowcount
        advertencias.append(f"gestion: bloque de chequeo actualizado ({filas} fila(s)) para la nota {nota_busqueda}.")

        if filas != 0:
            return filas

        # rowcount 0: puede ser fila inexistente (INSERT) o fila existente sin
        # cambios (FOUND_ROWS no está activo en pymysql por defecto: reconfirma
        # con SELECT antes de decidir, igual criterio defensivo que el PHP).
        with conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM `{tabla_gestion}` WHERE `{col_nota}` = %s LIMIT 1", (nota_busqueda,))
            existe = cur.fetchone() is not None

        if existe:
            advertencias.append("gestion: fila existente sin cambios detectados (re-chequeo idempotente).")
            return 1

        ic: List[str] = []
        iv: list = []

        def agrega(col: Optional[str], val) -> None:
            if col and col not in ic:
                ic.append(col)
                iv.append(val)

        agrega(col_nota, nota_busqueda)
        agrega(col_verifi, _VALOR_VERIFI_CHEQ)
        agrega(col_mesa, mesa)
        agrega(col_cheq, numero_cheq)
        agrega(col_tipo, _VALOR_TIPO_CHEQ)
        agrega(col_ubic, _VALOR_UBIC_CHEQ)
        agrega(col_hora, hora)
        if col_items and total_items > 0:
            agrega(col_items, total_items)
        # Defaults legacy NOT NULL (mismo criterio que chequeo/registro.php:
        # sin esto el INSERT revienta con error 1364 "Field doesn't have a
        # default" y hace rollback de rep_not + gestion completo).
        for c, v in (
            ("co_cli", ""), ("cli_des", ""), ("verifi_pre", ""), ("num_prep", 0),
            ("tipo_perso", ""), ("ubicacion", _VALOR_UBIC_CHEQ), ("num_emb", 0),
            ("verifi_emb", ""), ("tip_emb", ""), ("ubicacion3", ""), ("hora3", hora),
            ("num_mesa_emba", 0),
        ):
            if c in cols_g:
                agrega(c, v)

        cols_sql = ", ".join(f"`{c}`" for c in ic)
        marks = ", ".join(["%s"] * len(iv))
        try:
            with conn.cursor() as cur:
                cur.execute(f"INSERT INTO `{tabla_gestion}` ({cols_sql}) VALUES ({marks})", iv)
                filas_insert = cur.rowcount
            advertencias.append(f"gestion: fila creada para la nota {nota_busqueda} (INSERT {filas_insert}).")
            return filas_insert
        except Exception as e:
            # Carrera con otro proceso (la fila nació entre el SELECT y el
            # INSERT): reintentar el UPDATE en vez de abortar la transacción.
            if "1062" in str(e) or "Duplicate" in str(e):
                with conn.cursor() as cur:
                    cur.execute(sql_update, (*params, nota_busqueda))
                    filas_retry = cur.rowcount
                advertencias.append(f"gestion: INSERT duplicado (carrera), retry UPDATE {filas_retry} fila(s).")
                return filas_retry
            raise

    def _confirmar(
        self,
        nota: str,
        valor_estado: str,
        grupo_tabla_nota: str,
        grupo_tabla_log: str,
        log_extra: dict,
        modo_real: bool,
        origen: str,
    ) -> dict:
        """Núcleo transaccional compartido por despacho/chequeo (best-effort)."""
        nota = str(nota or "").strip()
        if not nota:
            return {
                "status": "error",
                "confirmado": False,
                "aviso_legacy": "LegacyMySQLConnector: falta el número de nota.",
            }
        t0 = time.perf_counter()
        try:
            conn = self.obtener()
        except Exception as e:
            return {
                "status": "no_disponible",
                "confirmado": False,
                "aviso_legacy": (
                    f"MySQL legacy no disponible ({self._host}:{self._port}): {e}"
                ),
            }

        try:
            candidatas_nota = (
                _CAND_TABLAS_NOTA if grupo_tabla_nota == "notas" else _CAND_TABLAS_NOTA
            )
            tabla_nota = self._resolver_tabla(conn, candidatas_nota)
            if not tabla_nota:
                return {
                    "status": "error",
                    "confirmado": False,
                    "disponible_schema": True,
                    "aviso_legacy": (
                        f"MySQL legacy: ninguna tabla candidata {candidatas_nota} "
                        "encontrada en la base de datos."
                    ),
                }

            cols_nota = self._columnas(conn, tabla_nota)
            col_estado = self._resolver(cols_nota, _CAND_COL_ESTADO)
            col_nota_col = self._resolver(cols_nota, _CAND_COL_NOTA)
            col_fecha = self._resolver(cols_nota, _CAND_COL_FECHA)
            if col_estado is None or col_nota_col is None:
                return {
                    "status": "error",
                    "confirmado": False,
                    "disponible_schema": True,
                    "aviso_legacy": (
                        f"MySQL legacy: la tabla {tabla_nota} no tiene columnas "
                        f"de estado/nota reconocibles (cols: {cols_nota})."
                    ),
                }

            set_sql = f"`{col_estado}` = %s"
            params = [valor_estado]
            if col_fecha is not None:
                set_sql += f", `{col_fecha}` = NOW()"
            sql = f"UPDATE `{tabla_nota}` SET {set_sql} WHERE `{col_nota_col}` = %s"
            params.append(nota)

            # Bitácora (solo si la tabla existe)
            log_sql = None
            log_params = []
            tabla_log = self._resolver_tabla(
                conn,
                _CAND_TABLAS_CHEQUEO if grupo_tabla_log == "chequeo"
                else _CAND_TABLAS_DESPACHO,
            )
            if tabla_log:
                cols_log = self._columnas(conn, tabla_log)
                col_l_nota = self._resolver(cols_log, _CAND_COL_NOTA)
                col_l_fecha = self._resolver(cols_log, _CAND_COL_FECHA)
                if col_l_nota is not None:
                    cols_insert = [col_l_nota]
                    marks = ["%s"]
                    l_params = [nota]
                    col_l_usuario = self._resolver(cols_log, _CAND_COL_USUARIO)
                    col_l_monto = self._resolver(cols_log, _CAND_COL_MONTO)
                    col_l_items = self._resolver(cols_log, _CAND_COL_ITEMS)
                    if col_l_usuario is not None:
                        cols_insert.append(col_l_usuario)
                        marks.append("%s")
                        l_params.append(str(log_extra.get("responsable") or ""))
                    if col_l_monto is not None:
                        cols_insert.append(col_l_monto)
                        marks.append("%s")
                        l_params.append(float(log_extra.get("monto") or 0))
                    if col_l_items is not None:
                        cols_insert.append(col_l_items)
                        marks.append("%s")
                        l_params.append(int(log_extra.get("items") or 0))
                    if col_l_fecha is not None:
                        cols_insert.append(col_l_fecha)
                        marks.append("NOW()")
                    log_sql = (
                        f"INSERT INTO `{tabla_log}` (`{'`, `'.join(cols_insert)}`) "
                        f"VALUES ({', '.join(marks)})"
                    )
                    log_params = l_params

            if not modo_real:
                return {
                    "status": "dry_run",
                    "confirmado": False,
                    "disponible_schema": True,
                    "nota": nota,
                    "sql_nota": sql,
                    "sql_log": log_sql,
                    "tabla_nota": tabla_nota,
                    "tabla_log": tabla_log,
                    "origen": origen,
                    "latencia_ms": round((time.perf_counter() - t0) * 1000, 1),
                }

            try:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    filas_nota = cur.rowcount
                    if log_sql:
                        cur.execute(log_sql, log_params)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            _print_sync(
                f"[ARA_SYNC] ✅ {origen} (MySQL directo): nota {nota} marcada como "
                f"{valor_estado} ({filas_nota} fila(s))"
                + (f" + bitácora en {tabla_log}" if tabla_log else " (sin bitácora)")
                + f" — {time.perf_counter() - t0:.2f}s"
            )
            return {
                "status": "ok",
                "confirmado": True,
                "nota": nota,
                "tabla_nota": tabla_nota,
                "tabla_log": tabla_log,
                "filas_actualizadas": int(filas_nota),
                "origen": origen,
                "via": "mysql_directo",
                "aviso_legacy": None,
            }
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "confirmado": False,
                "aviso_legacy": f"MySQL legacy: transacción fallida para {nota}: {e}",
            }
        finally:
            self.liberar(conn)

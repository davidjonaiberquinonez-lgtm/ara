import os
import time
import sqlite3
import traceback
try:
    from watchdog.events import FileSystemEventHandler
except ImportError:
    FileSystemEventHandler = object
# Importamos tu función del script anterior
from migrar_datos import migrar_a_sql, DATA_FOLDER, BRAIN_FOLDER, DB_PATH

# ── Blindaje de consola: nunca quebrar el daemon por un emoji/acento que no
# ── quepa en el codepage de la consola Windows (cp1252) ─────────────────────
try:
    import sys

    for _flujo in (sys.stdout, sys.stderr):
        try:
            _flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
except Exception:
    pass

# ── Configuración Profit (CRISTM25 producción, v4.16: solo lectura de stock/ubicación/código/descripción) ────
_DRIVER = os.environ.get("PROFIT_SQL_DRIVER", os.environ.get("PROFIT_DB_DRIVER", "SQL Server"))
_HOST = os.environ.get("PROFIT_SQL_HOST", os.environ.get("PROFIT_DB_HOST", "192.168.4.20"))
_PORT = os.environ.get("PROFIT_SQL_PORT", os.environ.get("PROFIT_DB_PORT", "1433"))
_DB = os.environ.get("PROFIT_SQL_NAME", os.environ.get("PROFIT_DB_NAME", "CRISTM25"))
_USER = os.environ.get("PROFIT_DB_USER", "profit")
_PASS = os.environ.get("PROFIT_DB_PASS", "profit")
_TIMEOUT_S = int(os.environ.get("PROFIT_DB_TIMEOUT_S", "8"))
_ALMACEN_BQTO = os.environ.get("PROFIT_ALMACEN_BQTO", "02")
# ── Blindaje de red: reintentos con backoff fijo ante caídas de enlace ODBC ─
_MAX_INTENTOS = int(os.environ.get("PROFIT_DB_MAX_RETRIES", "3"))
_REINTENTO_ESPERA_S = int(os.environ.get("PROFIT_DB_RETRY_DELAY_S", "3"))
# ── Temporizadores independientes del daemon (v4.7) ──────────────────────────
# Stock: 60s (baja latencia). Ubicaciones: intervalo heredado (300s) o por env.
_INTERVALO_STOCK_S = int(os.environ.get("VIGILAR_STOCK_INTERVALO_S", "60"))
_INTERVALO_UBICACIONES_S = int(
    os.environ.get(
        "VIGILAR_UBICACIONES_INTERVALO_S",
        os.environ.get("VIGILAR_PERIODO_S", "300"),
    )
)
# Códigos de barra (art.campo4 — campo genérico sin nombre descriptivo, pero
# es donde Profit guarda el EAN real: verificado en vivo 19/08 contra 8
# artículos del Excel importado, coincide exacto en 7/8; cambia aún menos que
# la ubicación física, mismo intervalo que ubicaciones por defecto.
_INTERVALO_CODIGOS_BARRA_S = int(
    os.environ.get("VIGILAR_CODIGOS_BARRA_INTERVALO_S", str(_INTERVALO_UBICACIONES_S))
)
_TAM_LOTE = 500  # ejecutemany por lotes (mismo patrón del sync v3.x)


class SincronizadorCampo7:
    """Sincroniza stock (pivot de los 4 almacenes corporativos 01/02/04/05
    desde Profit st_almac) y ubicaciones (art.campo7) hacia stock_maestro.

    Estrategia de CERO bloqueos y baja latencia (v4.7):
      - CONEXIÓN CORTA: se abre la conexión pyodbc, se ejecuta el SELECT, se
        extrae con fetchall() y se CIERRA inmediatamente antes de tocar la BD
        local. Sin conexión persistente a Profit entre ciclos.
      - WITH (NOLOCK) en TODAS las lecturas de Profit (st_almac / art): sin
        shared locks ni dirty reads sobre las transacciones del ERP.
      - SOLO columnas indispensables: pivot co_art → despacho/deposito/
        despacho_bqto/deposito_bqto, y campo7 para ubicaciones.
      - BULK UPSERT local en una sola transacción (executemany en lotes de
        500) + creación de renglones faltantes + campo de auditoría
        `actualizado_el`.
      - Temporizadores independientes (stock 60s / ubicaciones 300s) con
        try/except aislados: un fallo puntual de red NO detiene el daemon ni
        interfiere con la otra tarea.

    Modo degradado: cualquier fallo de Profit/SQLite se reporta sin romper el
    vigilante de archivos.
    """

    def __init__(self, conn_str=None, timeout=None, db_path=None):
        self._conn_str = conn_str or (
            f"DRIVER={{{_DRIVER}}};SERVER={_HOST},{_PORT};DATABASE={_DB};"
            f"UID={_USER};PWD={_PASS}"
        )
        self._timeout = timeout if timeout is not None else _TIMEOUT_S
        self._db_path = db_path or DB_PATH
        self._asegurar_columna_auditoria()

    # ------------------------------------------------------------------ Profit
    def _consultar_profit(self, sql, params=()):
        """Ejecuta un SELECT contra Profit con conexión CORTA (abre → fetchall
        → cierra) y reintentos con backoff ante fallos de enlace ODBC (08S01,
        HYT00, etc.). La conexión NUNCA sobrevive al ciclo."""
        intento = 0
        while True:
            intento += 1
            try:
                import pyodbc  # import local: dependencia solo de este sincronizador

                # Candado anti-zombi (v4.36): desactiva el pooling del ODBC
                # Driver Manager a nivel de proceso — refuerza el "libera el
                # pool/vínculo al instante" de abajo también a nivel driver.
                pyodbc.pooling = False
                conn = pyodbc.connect(self._conn_str, timeout=self._timeout)
                try:
                    cur = conn.cursor()
                    return cur.execute(sql, params).fetchall()
                finally:
                    try:
                        conn.close()  # libera el pool/vínculo al instante
                    except Exception:
                        pass
            except Exception as e:
                es_red = self._es_error_reintentable(e)
                if not es_red:
                    raise
                if intento >= _MAX_INTENTOS:
                    print(
                        f"[VIGILAR_DATOS] ERROR: se agotaron los {_MAX_INTENTOS} intentos "
                        f"de conexión con Profit: {e}"
                    )
                    raise
                print(
                    f"[VIGILAR_DATOS] ADVERTENCIA: intento {intento}/{_MAX_INTENTOS} falló por "
                    f"error de red/ODBC ({e}). Reintentando en {_REINTENTO_ESPERA_S}s..."
                )
                time.sleep(_REINTENTO_ESPERA_S)

    @staticmethod
    def _es_error_reintentable(e):
        """Errores ODBC reinientables: códigos de clase '08' (08S01 lost
        connection, 08001 can't reach server, HYT00 timeout...)."""
        args = getattr(e, "args", None) or []
        if args and isinstance(args[0], tuple) and args[0]:
            return str(args[0][0]).startswith("08")
        if args and isinstance(args[0], str):
            return args[0].startswith("08")
        return False

    def _leer_stock(self):
        """(co_art, despacho, deposito, despacho_bqto, deposito_bqto) desde
        st_almac con pivot de los 4 almacenes corporativos:
          01 → despacho      (SC despacho)
          02 → deposito      (SC depósito)
          04 → despacho_bqto (BQTO despacho)
          05 → deposito_bqto (BQTO depósito)
        Ultra-ligera: 1 tabla, NOLOCK, GROUP BY co_art."""
        return self._consultar_profit(
            """
            SELECT
                RTRIM(co_art) AS co_art,
                SUM(CASE WHEN LTRIM(RTRIM(co_alma)) = '01' THEN stock_act ELSE 0 END) AS despacho,
                SUM(CASE WHEN LTRIM(RTRIM(co_alma)) = '02' THEN stock_act ELSE 0 END) AS deposito,
                SUM(CASE WHEN LTRIM(RTRIM(co_alma)) = '04' THEN stock_act ELSE 0 END) AS despacho_bqto,
                SUM(CASE WHEN LTRIM(RTRIM(co_alma)) = '05' THEN stock_act ELSE 0 END) AS deposito_bqto
            FROM st_almac WITH (NOLOCK)
            WHERE LTRIM(RTRIM(co_alma)) IN ('01', '02', '04', '05')
            GROUP BY RTRIM(co_art)
            """
        )

    def _leer_datos_articulo(self):
        """(co_art, campo7, campo4, art_des) en UNA sola consulta a Profit.

        Antes había una consulta separada por cada dato (ubicación, código de
        barra) — se fusionan en una sola lectura de `art` (mismo WITH NOLOCK,
        mismo costo de conexión corta) para no multiplicar la carga sobre
        SQL Server: 1 round-trip por ciclo en vez de 2-3. art_des se agrega
        aquí mismo (bug real detectado en vivo 19/08: 6.729 artículos con
        código válido tenían descripcion=NULL en SQLite — nunca se
        sincronizaba en vivo, solo quedaba lo que trajo el Excel una vez).

        campo7 = ubicación física. campo4 = código de barra real (campo
        genérico sin nombre descriptivo; == co_art cuando Profit no tiene uno
        cargado, ahí NO es un dato válido). art_des = descripción real.
        Se traen las 3 aunque alguna venga vacía para esa fila: cada UPDATE
        posterior filtra la suya y no pisa con blanco lo que ya había."""
        return self._consultar_profit(
            """
            SELECT LTRIM(RTRIM(co_art)) AS co_art,
                   LTRIM(RTRIM(campo7)) AS campo7,
                   LTRIM(RTRIM(campo4)) AS campo4,
                   LTRIM(RTRIM(art_des)) AS art_des
            FROM art WITH (NOLOCK)
            WHERE LTRIM(RTRIM(campo7)) <> ''
               OR LTRIM(RTRIM(art_des)) <> ''
               OR (LTRIM(RTRIM(campo4)) <> '' AND LTRIM(RTRIM(campo4)) <> LTRIM(RTRIM(co_art)))
            """
        )

    # --------------------------------------------------------------- SQLite
    def _asegurar_columna_auditoria(self):
        """Crea `actualizado_el` en stock_maestro si no existe (idempotente)."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=30.0)
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(stock_maestro)")]
                if "actualizado_el" not in cols:
                    conn.execute(
                        "ALTER TABLE stock_maestro ADD COLUMN actualizado_el TEXT"
                    )
                    conn.commit()
                    print("[VIGILAR_DATOS] Columna auditoría actualizado_el creada en stock_maestro.")
            finally:
                conn.close()
        except Exception as e:
            print(f"[VIGILAR_DATOS] ADVERTENCIA: no se pudo asegurar la columna auditoría: {e}")

    def _aplicar_local(self, sql, filas, etiqueta):
        """Bulk UPSERT local: UNA transacción, ejecutemany por lotes de 500."""
        t0 = time.perf_counter()
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        try:
            cur = conn.cursor()
            for i in range(0, len(filas), _TAM_LOTE):
                cur.executemany(sql, filas[i : i + _TAM_LOTE])
            conn.commit()
        finally:
            conn.close()
        return time.perf_counter() - t0

    # ------------------------------------------------------------ Sincronizar
    def sincronizar_stock(self):
        """Ciclo de stock (1m): Profit (pivot 4 almacenes) → despacho,
        deposito, despacho_bqto, deposito_bqto + actualizado_el en
        stock_maestro, para TODOS los artículos (los ausentes del catálogo
        local se crean como renglones mínimos y luego se actualizan).

        v4.56 (bug real detectado en vivo, comparando contra ubicacion.php
        legacy): el Visor de Artículos lee `stock_act` (total S/C) y
        `stock_maestro` (total general) — columnas que este sincronizador
        NUNCA tocaba, así que quedaban congeladas en el valor de la última
        migración manual (para un artículo de prueba: stock_act=55 vs el
        real despacho+deposito=17, un desfase enorme). Se agregan acá
        derivadas de los mismos 4 valores ya leídos, sin golpear Profit de
        nuevo — mantiene la decisión de NO cambiar la consulta del Visor,
        sino que el sincronizador entregue esas columnas al día también."""
        t0 = time.perf_counter()
        filas = self._leer_stock()
        if not filas:
            print("[VIGILAR_DATOS] ADVERTENCIA: profit no devolvió stock (st_almac).")
            return 0, time.perf_counter() - t0
        lotes = []
        for f in filas:
            co = str(f.co_art or "").strip()
            if not co:
                continue
            despacho = int(f.despacho or 0)
            deposito = int(f.deposito or 0)
            despacho_bqto = int(f.despacho_bqto or 0)
            deposito_bqto = int(f.deposito_bqto or 0)
            lotes.append(
                (
                    despacho,
                    deposito,
                    despacho_bqto,
                    deposito_bqto,
                    despacho + deposito,                                   # stock_act (total S/C)
                    deposito,                                              # stock_bulto_cerrado (depósito S/C)
                    despacho + deposito + despacho_bqto + deposito_bqto,   # stock_maestro (total general)
                    co,
                )
            )

        # 1) Crear renglones faltantes del catálogo local (INSERT de códigos
        #    que Profit conoce y stock_maestro aún no tiene). stock_maestro
        #    NO tiene UNIQUE en codigo, así que el INSERT es selectivo sobre
        #    los códigos existentes (no ON CONFLICT).
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        try:
            existentes = {
                r[0]
                for r in conn.execute(
                    "SELECT codigo FROM stock_maestro WHERE codigo IS NOT NULL"
                )
            }
            faltantes = [fila[-1] for fila in lotes if fila[-1] not in existentes]
            if faltantes:
                cur = conn.cursor()
                for i in range(0, len(faltantes), _TAM_LOTE):
                    cur.executemany(
                        "INSERT INTO stock_maestro (codigo) VALUES (?)",
                        [(c,) for c in faltantes[i : i + _TAM_LOTE]],
                    )
                conn.commit()
                print(
                    f"[VIGILAR_DATOS] Stock: {len(faltantes)} artículo(s) creado(s) "
                    f"en stock_maestro (faltaban en el catálogo local)."
                )
        finally:
            conn.close()

        # 2) Bulk UPDATE masivo de las 4 columnas de stock + las 3 derivadas
        #    que lee el Visor (stock_act, stock_bulto_cerrado, stock_maestro)
        #    + auditoría.
        duracion_upsert = self._aplicar_local(
            "UPDATE stock_maestro SET despacho = ?, deposito = ?, "
            "despacho_bqto = ?, deposito_bqto = ?, "
            "stock_act = ?, stock_bulto_cerrado = ?, stock_maestro = ?, "
            "actualizado_el = datetime('now','localtime') WHERE codigo = ?",
            lotes,
            "stock",
        )
        total = time.perf_counter() - t0
        print(
            f"[VIGILAR_DATOS] Stock 4 almacenes (01/02/04/05): {len(lotes)} artículos en "
            f"{total:.2f}s total (upsert local {duracion_upsert:.2f}s)."
        )
        return len(lotes), total

    def sincronizar_datos_articulo(self):
        """Ciclo de datos de artículo (300s por defecto): UNA sola consulta a
        Profit (art.campo7 + campo4 + art_des) → 3 UPDATE locales (campo7,
        codigo_barra, descripcion) + actualizado_el.

        Antes eran 2 consultas separadas (ubicaciones, códigos de barra) y la
        descripción nunca se sincronizaba en vivo — se fusionaron en una sola
        lectura para no multiplicar el round-trip a SQL Server (a pedido:
        "sin tumbar el sqlsrv"). Cada UPDATE filtra sus propias filas no
        vacías, así una fila con art_des vacío pero campo7 lleno no pisa con
        blanco la descripción que ya hubiera en SQLite."""
        t0 = time.perf_counter()
        filas = self._leer_datos_articulo()
        if not filas:
            print("[VIGILAR_DATOS] ADVERTENCIA: profit no devolvió datos de artículo (campo7/campo4/art_des).")
            return 0, time.perf_counter() - t0

        lotes_ubi, lotes_barra, lotes_desc = [], [], []
        for f in filas:
            co = str(f.co_art or "").strip()
            if not co:
                continue
            campo7 = str(f.campo7 or "").strip()
            campo4 = str(f.campo4 or "").strip()
            art_des = str(f.art_des or "").strip()
            if campo7:
                lotes_ubi.append((campo7, co))
            if campo4:
                lotes_barra.append((campo4, co))
            if art_des:
                lotes_desc.append((art_des, co))

        dur_ubi = self._aplicar_local(
            "UPDATE stock_maestro SET campo7 = ?, "
            "actualizado_el = datetime('now','localtime') WHERE codigo = ?",
            lotes_ubi, "ubicaciones",
        ) if lotes_ubi else 0.0
        dur_barra = self._aplicar_local(
            "UPDATE stock_maestro SET codigo_barra = ?, "
            "actualizado_el = datetime('now','localtime') WHERE codigo = ?",
            lotes_barra, "codigos_barra",
        ) if lotes_barra else 0.0
        dur_desc = self._aplicar_local(
            "UPDATE stock_maestro SET descripcion = ?, "
            "actualizado_el = datetime('now','localtime') WHERE codigo = ?",
            lotes_desc, "descripciones",
        ) if lotes_desc else 0.0

        total = time.perf_counter() - t0
        totales = len(lotes_ubi) + len(lotes_barra) + len(lotes_desc)
        print(
            f"[VIGILAR_DATOS] Datos de artículo (1 consulta): ubicaciones={len(lotes_ubi)} "
            f"codigos_barra={len(lotes_barra)} descripciones={len(lotes_desc)} en "
            f"{total:.2f}s total (upsert local {dur_ubi + dur_barra + dur_desc:.2f}s)."
        )
        return totales, total

    def sincronizar(self) -> int:
        """Compatibilidad: ejecuta ambos ciclos (stock + datos de artículo) y
        devuelve el total de registros procesados."""
        n_datos, _ = self.sincronizar_datos_articulo()
        n_stock, _ = self.sincronizar_stock()
        return n_datos + n_stock

    def cerrar(self):
        """Compatibilidad: ya no hay conexión persistente que cerrar."""


_sincronizador = SincronizadorCampo7()


def sincronizar_campo7() -> int:
    try:
        return _sincronizador.sincronizar()
    except Exception as e:
        traceback.print_exc()
        print(f"[VIGILAR_DATOS] ERROR: sincronizando campo 7 desde Profit: {e}")
        return 0


def sincronizar_stock() -> int:
    try:
        n, _ = _sincronizador.sincronizar_stock()
        return n
    except Exception as e:
        traceback.print_exc()
        print(f"[VIGILAR_DATOS] ERROR: en el ciclo de stock: {e}")
        return 0


def sincronizar_datos_articulo() -> int:
    try:
        n, _ = _sincronizador.sincronizar_datos_articulo()
        return n
    except Exception as e:
        traceback.print_exc()
        print(f"[VIGILAR_DATOS] ERROR: en el ciclo de datos de artículo: {e}")
        return 0


# Alias de compatibilidad (nombre viejo, por si algo externo lo importaba).
sincronizar_ubicaciones = sincronizar_datos_articulo


def bucle_periodico():
    """Bucle principal con TEMPORIZADORES INDEPENDIENTES:
    - INTERVALO_STOCK_S (60s)          → sincronizar_stock()
    - INTERVALO_CODIGOS_BARRA_S (300s) → sincronizar_datos_articulo()
      (ubicación + código de barra + descripción, 1 sola consulta a Profit)
    Cada tarea va en su propio try/except: un fallo puntual de red en Profit
    (o SQLite) jamás detiene el daemon ni la otra sincronización."""
    ultimo_stock = time.time()
    ultimo_datos_articulo = time.time()
    print(
        f"[VIGILAR_DATOS] Temporizadores: stock cada {_INTERVALO_STOCK_S}s | "
        f"datos de artículo (ubicación+código de barra+descripción) cada {_INTERVALO_CODIGOS_BARRA_S}s"
    )
    while True:
        ahora = time.time()
        if ahora - ultimo_stock >= _INTERVALO_STOCK_S:
            ultimo_stock = ahora
            try:
                sincronizar_stock()
            except Exception:
                traceback.print_exc()
        if ahora - ultimo_datos_articulo >= _INTERVALO_CODIGOS_BARRA_S:
            ultimo_datos_articulo = ahora
            try:
                sincronizar_datos_articulo()
            except Exception:
                traceback.print_exc()
        time.sleep(1)


class ManejadorCambios(FileSystemEventHandler):
    def __init__(self):
        self.ultima_ejecucion = 0

    def procesar_cambio(self, event):
        # Evitar que se procesen directorios
        if event.is_directory:
            return

        # Evitar ejecuciones duplicadas seguidas (anti-bounce)
        ahora = time.time()
        if ahora - self.ultima_ejecucion < 2:
            return

        nombre_archivo = os.path.basename(event.src_path)
        ruta_completa = event.src_path

        # Filtro inteligente: reacciona a los archivos maestros o a nuevos JSONs en reportes_ubicacion
        es_archivo_maestro = nombre_archivo in ['Libro1.xlsx', 'usuarios.json']
        es_nuevo_reporte = nombre_archivo.endswith('.json') and 'reportes_ubicacion' in ruta_completa

        if es_archivo_maestro or es_nuevo_reporte:
            print(f"🔄 Cambio detectado en: {nombre_archivo} ({event.event_type.upper()}). Iniciando migración...")

            # Esperamos medio segundo a que el sistema termine de escribir el archivo por completo
            time.sleep(0.5)

            try:
                migrar_a_sql()
                # Tras cada migración exitosa, refresca stock + ubicaciones desde Profit
                sincronizar_campo7()
                self.ultima_ejecucion = time.time()
            except Exception as e:
                print(f"❌ Error durante la automigración: {e}")

    # Capturamos modificaciones de archivos existentes
    def on_modified(self, event):
        self.procesar_cambio(event)

    # Capturamos cuando el sistema operativo crea un archivo JSON nuevo en la carpeta
    def on_created(self, event):
        self.procesar_cambio(event)


def _asegurar_instancia_unica():
    """Candado de instancia única (Windows, msvcrt.locking): evita que dos
    daemons corran a la vez sobre la misma stock_maestro.

    BUG REAL detectado en vivo (v4.56): con dos procesos vigilar_datos.py
    corriendo sin coordinación (pasó varias veces seguidas al reiniciar
    manualmente, incluso lanzando uno solo por PowerShell/Bash), cada uno
    lee Profit y escribe SQLite en su propio ciclo — el que ESCRIBE último
    gana, sin importar cuál LEYÓ el dato más reciente. Con NOLOCK sobre una
    tabla activa, dos lecturas separadas por milisegundos pueden ver
    valores distintos (transacciones en curso), así que el "último en
    escribir" a veces terminaba pisando un valor fresco con uno un
    instante más viejo — exactamente el síntoma reportado ("el SQLite
    vuelve a 22 después de haber estado en 19").

    Si ya hay una instancia corriendo, este proceso se cierra solo con un
    mensaje claro en vez de duplicar el trabajo silenciosamente."""
    import sys
    ruta_lock = os.path.join(DATA_FOLDER, "vigilar_datos.lock")
    try:
        archivo_lock = open(ruta_lock, "w")
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(archivo_lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(archivo_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        archivo_lock.write(str(os.getpid()))
        archivo_lock.flush()
        globals()["_ARCHIVO_LOCK"] = archivo_lock  # mantener abierto (libera el lock al morir el proceso)
    except OSError:
        print(
            "[VIGILAR_DATOS] ERROR: ya hay otra instancia de vigilar_datos.py "
            "corriendo (candado de instancia única). Cerrando este proceso "
            "para no duplicar sincronizaciones sobre la misma stock_maestro."
        )
        sys.exit(1)


if __name__ == "__main__":
    _asegurar_instancia_unica()

    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    event_handler = ManejadorCambios()
    observer = Observer()

    # Vigilamos data de forma plana
    observer.schedule(event_handler, path=DATA_FOLDER, recursive=False)

    # 🔥 CLAVE: recursive=True para que watchdog vigile la subcarpeta 'reportes_ubicacion'
    observer.schedule(event_handler, path=BRAIN_FOLDER, recursive=True)

    print("👀 Vigilante de archivos activado.")
    print("🤖 Esperando cambios en Libro1.xlsx, usuarios.json o nuevos reportes de ubicación...")
    observer.start()

    # Sincronización inicial y periódica INDEPENDIENTE (stock 1m / ubicaciones 300s)
    try:
        try:
            sincronizar_stock()
        except Exception:
            traceback.print_exc()
        try:
            sincronizar_ubicaciones()
        except Exception:
            traceback.print_exc()
        bucle_periodico()
    except KeyboardInterrupt:
        observer.stop()
    finally:
        _sincronizador.cerrar()
    observer.join()

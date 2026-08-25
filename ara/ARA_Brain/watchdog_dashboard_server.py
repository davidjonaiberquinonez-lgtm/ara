# -*- coding: utf-8 -*-
"""
Dashboard en tiempo real del watchdog anti-bloqueo SQL Server — Proyecto ARA.

Lee y correlaciona dos fuentes reales (nunca inventa datos):
  - ara/ARA_Brain/data/watchdog_bloqueos_sql.log  (timeline de ejecuciones
    del watchdog cada 2 min: 0 candidatos / MATADOS N / error de timeout).
  - logs/kill_switch_*.txt (detalle por SPID: motivo exacto — KICKSERVER
    ghost vs bloqueador activo — y segundos dormido/bloqueando).

Cruza ambas por SPID para rankear "culpables recurrentes" (el mismo SPID
apareciendo una y otra vez es la señal real de un proceso que sigue dejando
conexiones fantasma o transacciones sin cerrar). Además intenta una consulta
EN VIVO (solo lectura, mismo patrón anti-zombi que bin/diagnostico_bloqueo_
servidor_completo.php) para mostrar quién está bloqueando ahora mismo con
host/IP reales — si el servidor no responde, esa sección se degrada sola
sin romper el resto del dashboard.

Uso:
    python ara/ARA_Brain/watchdog_dashboard_server.py
    → http://localhost:5005
"""
import os
import re
import glob
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, request, abort

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))  # c:\ARA_PROYECT

LOG_WATCHDOG = os.path.join(BASE_DIR, "data", "watchdog_bloqueos_sql.log")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")

INTERVALO_ESPERADO_MIN = 2   # el watchdog corre cada 2 min (tarea programada)
UMBRAL_HUECO_MIN = 6         # >6 min sin registro = se considera un hueco real

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))

# BUG real detectado en vivo (24/08): ningún endpoint tenía autenticación —
# cualquiera en la red 192.168.4.x podía leer el detalle de bloqueos SQL
# (hosts, IPs, logins, texto de queries reales). Antes de conectar este
# dashboard a un panel externo (DIP) se exige X-API-Key en todo /api/*. El
# propio dashboard HTML (mismo origen) manda la misma clave — se la inyecta
# el server al renderizar la página (ver index()).
WATCHDOG_API_KEY = os.environ.get("WATCHDOG_API_KEY", "")


@app.before_request
def _exigir_api_key():
    if not request.path.startswith("/api/"):
        return None
    if not WATCHDOG_API_KEY:
        # Sin clave configurada: no se puede validar nada — se bloquea todo
        # /api/* en vez de dejarlo abierto por omisión.
        abort(503, description="WATCHDOG_API_KEY no está configurada en el servidor.")
    if request.headers.get("X-API-Key") != WATCHDOG_API_KEY:
        abort(401, description="Falta o es inválido el header X-API-Key.")
    return None


# =============================================================================
# PARSER: ara/ARA_Brain/data/watchdog_bloqueos_sql.log
# =============================================================================
_RE_WD_LINEA = re.compile(
    r"^(?P<fecha>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - "
    r"Watchdog bloqueos(?: ejecutado \((?P<candidatos>\d+) candidatos\)"
    r"|: MATADOS (?P<n>\d+) SPID\(s\) de \d+ detectado\(s\) - (?P<spids>[\d,\s]+)"
    r"|: FALLO AL MATAR SPID (?P<spid_fallo>\d+) - (?P<error_fallo>.*)"
    r"|: salida no parseable:)"
)


def _parsear_watchdog_log(ruta: str) -> list:
    """Devuelve una lista de eventos {fecha, tipo, spids, detalle}."""
    eventos = []
    if not os.path.isfile(ruta):
        return eventos
    with open(ruta, "r", encoding="utf-8", errors="replace") as f:
        lineas = f.readlines()

    i = 0
    while i < len(lineas):
        linea = lineas[i].rstrip("\n")
        m = _RE_WD_LINEA.match(linea)
        if not m:
            i += 1
            continue
        fecha = m.group("fecha")
        if m.group("candidatos") is not None:
            eventos.append({
                "fecha": fecha, "tipo": "ok", "spids": [],
                "detalle": f"0 candidatos" if m.group("candidatos") == "0"
                           else f"{m.group('candidatos')} candidatos",
            })
        elif m.group("n") is not None:
            spids = [int(s.strip()) for s in m.group("spids").split(",") if s.strip()]
            eventos.append({
                "fecha": fecha, "tipo": "kill", "spids": spids,
                "detalle": f"MATADOS {m.group('n')} SPID(s): {', '.join(map(str, spids))}",
            })
        elif m.group("spid_fallo") is not None:
            # Caso más crítico de auditar: el watchdog detectó un candidato
            # a matar pero el KILL no surtió efecto — un proceso zombie que
            # sigue vivo pese a la mitigación (ver bin/watchdog_bloqueos_sql.ps1).
            eventos.append({
                "fecha": fecha, "tipo": "fallo", "spids": [int(m.group("spid_fallo"))],
                "detalle": f"FALLO AL MATAR SPID {m.group('spid_fallo')}: {m.group('error_fallo')}",
            })
        else:
            # "salida no parseable:" — la siguiente línea trae el error real.
            detalle_err = lineas[i + 1].strip() if i + 1 < len(lineas) else ""
            eventos.append({
                "fecha": fecha, "tipo": "error", "spids": [],
                "detalle": detalle_err or "salida no parseable",
            })
            i += 1  # saltar la línea del error ya consumida
        i += 1
    return eventos


# =============================================================================
# PARSER: logs/kill_switch_*.txt
# =============================================================================
_RE_KS_LINEA = re.compile(
    r"^(?P<fecha>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| SPID (?P<spid>\d+) \| "
    r"dormido/bloqueando (?P<seg>\d+) s \| motivo (?P<motivo>\S+)"
    r"(?: \| host (?P<host>[^|]*) \| ip (?P<ip>[^|]*) \| login (?P<login>[^|]*) \| programa (?P<programa>.*))?"
)


def _parsear_kill_switch_logs() -> list:
    """Une todos los logs/kill_switch_*.txt en una sola lista de detalles
    por SPID: {fecha, spid, segundos, motivo, host, ip, login, programa}.
    Las líneas viejas (antes de v4.50) no traían host/ip/login/programa —
    quedan como '' para no romper el parseo hacia atrás."""
    detalles = []
    for ruta in sorted(glob.glob(os.path.join(LOGS_DIR, "kill_switch_*.txt"))):
        with open(ruta, "r", encoding="utf-8", errors="replace") as f:
            for linea in f:
                m = _RE_KS_LINEA.match(linea.strip())
                if m:
                    detalles.append({
                        "fecha": m.group("fecha"),
                        "spid": int(m.group("spid")),
                        "segundos": int(m.group("seg")),
                        "motivo": m.group("motivo"),
                        "host": (m.group("host") or "").strip(),
                        "ip": (m.group("ip") or "").strip(),
                        "login": (m.group("login") or "").strip(),
                        "programa": (m.group("programa") or "").strip(),
                    })
    return detalles


def _mas_comun(valores) -> str:
    """Valor no vacío más frecuente en una lista (para host/ip/programa
    dominantes de un SPID reciclado entre varias sesiones distintas)."""
    vistos = Counter(v for v in valores if v)
    return vistos.most_common(1)[0][0] if vistos else ""


_VENTANA_CORRELACION_SEG = 15


def _indice_por_spid(detalles_ks: list) -> dict:
    """{spid: [(datetime, detalle), ...]} ordenado por fecha — para
    encontrar, dado un SPID + fecha del log del watchdog, el detalle de
    kill_switch más cercano en el tiempo (los dos logs los escribe la misma
    ejecución pero con un par de segundos de diferencia entre wrapper y
    script PHP, así que el match es por SPID + ventana corta, no exacto)."""
    idx = defaultdict(list)
    for d in detalles_ks:
        try:
            dt = datetime.strptime(d["fecha"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        idx[d["spid"]].append((dt, d))
    for spid in idx:
        idx[spid].sort(key=lambda t: t[0])
    return idx


def _origen_mas_cercano(idx: dict, spid: int, fecha_evento: str):
    """Detalle de kill_switch (con host/ip/programa) más cercano en el
    tiempo a `fecha_evento` para ese SPID, dentro de la ventana de
    correlación. None si no hay ninguno lo bastante cerca (línea vieja sin
    esos campos, o no se encontró en absoluto)."""
    candidatos = idx.get(spid)
    if not candidatos:
        return None
    try:
        dt_evento = datetime.strptime(fecha_evento, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    mejor = min(candidatos, key=lambda t: abs((t[0] - dt_evento).total_seconds()))
    if abs((mejor[0] - dt_evento).total_seconds()) > _VENTANA_CORRELACION_SEG:
        return None
    return mejor[1]


# =============================================================================
# ANÁLISIS: correlación + ranking de "culpables recurrentes" + huecos
# =============================================================================
def _detectar_huecos(eventos: list) -> list:
    """Cualquier salto > UMBRAL_HUECO_MIN entre dos registros consecutivos
    del watchdog = el proceso no corrió (reinicio, sleep, tarea deshabilitada)."""
    huecos = []
    fechas = [datetime.strptime(e["fecha"], "%Y-%m-%d %H:%M:%S") for e in eventos]
    for i in range(1, len(fechas)):
        delta = fechas[i] - fechas[i - 1]
        if delta > timedelta(minutes=UMBRAL_HUECO_MIN):
            huecos.append({
                "desde": fechas[i - 1].strftime("%Y-%m-%d %H:%M:%S"),
                "hasta": fechas[i].strftime("%Y-%m-%d %H:%M:%S"),
                "duracion_min": round(delta.total_seconds() / 60, 1),
            })
    return huecos


def _armar_analisis(filtros: dict = None) -> dict:
    """filtros admitidos (todos opcionales, se combinan con AND):
    spid (int o substring), host (substring, case-insensitive),
    ip (substring), motivo (exacto), desde/hasta ('YYYY-MM-DD HH:MM:SS')."""
    filtros = filtros or {}
    eventos_wd = _parsear_watchdog_log(LOG_WATCHDOG)
    detalles_ks = _parsear_kill_switch_logs()

    # ── Aplicar filtros sobre el detalle de kills (fuente con host/ip/login) ──
    f_spid = (filtros.get("spid") or "").strip()
    f_host = (filtros.get("host") or "").strip().lower()
    f_ip = (filtros.get("ip") or "").strip()
    f_motivo = (filtros.get("motivo") or "").strip()
    f_desde = (filtros.get("desde") or "").strip()
    f_hasta = (filtros.get("hasta") or "").strip()

    def _pasa_filtro(d: dict) -> bool:
        if f_spid and f_spid not in str(d["spid"]):
            return False
        if f_host and f_host not in d["host"].lower():
            return False
        if f_ip and f_ip not in d["ip"]:
            return False
        if f_motivo and d["motivo"] != f_motivo:
            return False
        if f_desde and d["fecha"] < f_desde:
            return False
        if f_hasta and d["fecha"] > f_hasta:
            return False
        return True

    hay_filtros = any([f_spid, f_host, f_ip, f_motivo, f_desde, f_hasta])
    detalles_filtrados = [d for d in detalles_ks if _pasa_filtro(d)] if hay_filtros else detalles_ks

    total_escaneos = len(eventos_wd)
    total_kills = sum(len(e["spids"]) for e in eventos_wd if e["tipo"] == "kill")
    total_errores = sum(1 for e in eventos_wd if e["tipo"] == "error")
    eventos_fallo = [e for e in eventos_wd if e["tipo"] == "fallo"]
    huecos = _detectar_huecos(eventos_wd)

    # Ranking de SPIDs recurrentes (culpables) cruzando con el motivo real
    # detallado del kill_switch — un mismo SPID matado varias veces a lo
    # largo de horas es la señal de un proceso que sigue reabriendo la
    # misma conexión fantasma. Incluye host/ip/login/programa dominantes
    # (el más frecuente si el SPID se reciclara entre sesiones distintas).
    conteo_spid = Counter(d["spid"] for d in detalles_filtrados)
    por_spid = defaultdict(list)
    for d in detalles_filtrados:
        por_spid[d["spid"]].append(d)

    culpables = []
    for spid, veces in conteo_spid.most_common():
        ocurrencias = por_spid[spid]
        motivos = Counter(o["motivo"] for o in ocurrencias)
        motivo_dominante = motivos.most_common(1)[0][0]
        culpables.append({
            "spid": spid,
            "veces": veces,
            "motivo_dominante": motivo_dominante,
            "primera_vez": min(o["fecha"] for o in ocurrencias),
            "ultima_vez": max(o["fecha"] for o in ocurrencias),
            "segundos_prom": round(sum(o["segundos"] for o in ocurrencias) / veces, 1),
            "host": _mas_comun(o["host"] for o in ocurrencias),
            "ip": _mas_comun(o["ip"] for o in ocurrencias),
            "login": _mas_comun(o["login"] for o in ocurrencias),
            "programa": _mas_comun(o["programa"] for o in ocurrencias),
        })

    # Desglose por motivo (KICKSERVER_APACHE_10MIN vs BLOQUEADOR_ACTIVO_15S)
    motivos_totales = Counter(d["motivo"] for d in detalles_filtrados)

    # Ranking por host/IP responsable (agrupa todos los SPIDs de un mismo
    # origen — más útil que ver SPIDs sueltos cuando se reciclan seguido).
    conteo_host = Counter(d["host"] for d in detalles_filtrados if d["host"])
    conteo_ip = Counter(d["ip"] for d in detalles_filtrados if d["ip"])

    # La línea de tiempo (del log del watchdog, sin host/ip en su propio
    # formato) se enriquece por SPID+fecha más cercana contra el detalle de
    # kill_switch — así cada evento "KILL" muestra de dónde vino cada SPID
    # y con qué programa, no solo el número.
    idx_por_spid = _indice_por_spid(detalles_ks)
    for e in eventos_wd:
        if e["tipo"] != "kill" or not e["spids"]:
            e["origenes"] = []
            continue
        origenes = []
        for s in e["spids"]:
            o = _origen_mas_cercano(idx_por_spid, s, e["fecha"])
            origenes.append({
                "spid": s,
                "host": o["host"] if o else "",
                "ip": o["ip"] if o else "",
                "programa": o["programa"] if o else "",
                "motivo": o["motivo"] if o else "",
            })
        e["origenes"] = origenes

    timeline = sorted(eventos_wd, key=lambda e: e["fecha"], reverse=True)
    if hay_filtros:
        spids_filtrados = {d["spid"] for d in detalles_filtrados}
        timeline = [
            e for e in timeline
            if (not (f_host or f_ip) or any(s in spids_filtrados for s in e["spids"]))
            and (not f_spid or any(f_spid in str(s) for s in e["spids"]) or e["tipo"] != "kill")
            and (not f_desde or e["fecha"] >= f_desde)
            and (not f_hasta or e["fecha"] <= f_hasta)
        ]
    timeline = timeline[:300]

    return {
        "generado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "filtros_aplicados": {k: v for k, v in filtros.items() if v},
        "resumen": {
            "total_escaneos": total_escaneos,
            "total_kills": total_kills,
            "total_errores_timeout": total_errores,
            "total_fallos_kill": len(eventos_fallo),
            "total_huecos": len(huecos),
            "spids_unicos_matados": len(conteo_spid),
        },
        "motivos_totales": dict(motivos_totales),
        "ranking_hosts": conteo_host.most_common(15),
        "ranking_ips": conteo_ip.most_common(15),
        "huecos": huecos,
        # El caso más crítico de auditar: un candidato que el watchdog quiso
        # matar pero el KILL no surtió efecto (proceso zombie que sigue vivo).
        "fallos_kill": [{"fecha": e["fecha"], "spid": e["spids"][0], "detalle": e["detalle"]} for e in eventos_fallo],
        "culpables_recurrentes": culpables[:30],
        "timeline": timeline,
    }


# =============================================================================
# PANEL "EN VIVO": consulta real y de solo lectura al servidor SQL (best-effort)
# =============================================================================
def _consultar_vivo() -> dict:
    try:
        import pyodbc
    except Exception as e:
        return {"disponible": False, "motivo": f"pyodbc no disponible: {e}"}

    host = os.environ.get("PROFIT_SQL_HOST", os.environ.get("PROFIT_DB_HOST", "192.168.4.20"))
    port = os.environ.get("PROFIT_SQL_PORT", os.environ.get("PROFIT_DB_PORT", "1433"))
    user = os.environ.get("PROFIT_SQL_USER", os.environ.get("PROFIT_DB_USER", "profit"))
    pwd = os.environ.get("PROFIT_SQL_PASS", os.environ.get("PROFIT_DB_PASS", "profit"))

    conn = None
    try:
        pyodbc.pooling = False
        conn = pyodbc.connect(
            f"Driver={{SQL Server}};Server={host},{port};Database=master;"
            f"UID={user};PWD={pwd};Connection Timeout=4",
            timeout=4,
        )
        cur = conn.cursor()
        cur.execute("""
            SELECT r.session_id AS spid_bloqueado, r.blocking_session_id AS spid_bloqueador,
                   r.wait_time AS espera_ms, s.login_name, s.host_name, s.program_name,
                   c.client_net_address, DB_NAME(r.database_id) AS db
            FROM sys.dm_exec_requests r
            JOIN sys.dm_exec_sessions s ON s.session_id = r.blocking_session_id
            LEFT JOIN sys.dm_exec_connections c ON c.session_id = r.blocking_session_id
            WHERE r.blocking_session_id <> 0
            ORDER BY r.wait_time DESC
        """)
        cols = [c[0] for c in cur.description]
        filas = [dict(zip(cols, row)) for row in cur.fetchall()]
        return {"disponible": True, "bloqueos_activos": filas, "consultado": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    except Exception as e:
        return {"disponible": False, "motivo": str(e)[:200]}
    finally:
        if conn is not None:
            conn.close()


# =============================================================================
# EVIDENCIA HISTÓRICA: logs/evidencia_bloqueos_sql/*.jsonl (v4.51/v4.52)
# =============================================================================
EVIDENCIA_DIR = os.path.join(LOGS_DIR, "evidencia_bloqueos_sql")


def _buscar_evidencia_historica_spid(spid: int) -> list:
    """Un mismo número de SPID se recicla apenas la sesión se desconecta —
    consultarlo en vivo después solo trae la sesión NUEVA que ocupa ese
    número, no la que causó el bloqueo. Esta función busca en el archivo
    de evidencia (capturado en el instante de cada KILL, con sql_text real)
    TODAS las apariciones históricas de ese SPID, para que el usuario elija
    por fecha en vez de depender de que siga vivo."""
    resultados = []
    for ruta in sorted(glob.glob(os.path.join(EVIDENCIA_DIR, "*.jsonl"))):
        with open(ruta, "r", encoding="utf-8", errors="replace") as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    d = json.loads(linea)
                except json.JSONDecodeError:
                    continue
                if d.get("spid") == spid:
                    resultados.append(d)
    resultados.sort(key=lambda d: d.get("fecha", ""), reverse=True)
    return resultados


# =============================================================================
# CONSULTA PUNTUAL: qué está/estaba corriendo un SPID específico (solo lectura)
# =============================================================================
def _consultar_query_spid(spid: int) -> dict:
    """Dado un SPID, trae identidad (login/host/programa/DB), el texto
    completo de la query en ejecución (si status=running) y, si está
    sleeping, el texto de la última consulta ejecutada vía
    most_recent_sql_handle. Mismo patrón anti-zombi que el resto del
    watchdog: pooling desactivado, timeout corto, solo lectura."""
    try:
        import pyodbc
    except Exception as e:
        return {"disponible": False, "motivo": f"pyodbc no disponible: {e}"}

    host = os.environ.get("PROFIT_SQL_HOST", os.environ.get("PROFIT_DB_HOST", "192.168.4.20"))
    port = os.environ.get("PROFIT_SQL_PORT", os.environ.get("PROFIT_DB_PORT", "1433"))
    user = os.environ.get("PROFIT_SQL_USER", os.environ.get("PROFIT_DB_USER", "profit"))
    pwd = os.environ.get("PROFIT_SQL_PASS", os.environ.get("PROFIT_DB_PASS", "profit"))

    conn = None
    try:
        pyodbc.pooling = False
        conn = pyodbc.connect(
            f"Driver={{SQL Server}};Server={host},{port};Database=master;"
            f"UID={user};PWD={pwd};Connection Timeout=4",
            timeout=4,
        )
        cur = conn.cursor()

        cur.execute("""
            SELECT s.session_id, s.login_name, s.host_name, s.program_name, s.status,
                   DB_NAME(s.database_id) AS db_actual,
                   s.login_time, s.last_request_start_time,
                   c.client_net_address, c.connect_time
            FROM sys.dm_exec_sessions s
            LEFT JOIN sys.dm_exec_connections c ON c.session_id = s.session_id
            WHERE s.session_id = ?
        """, spid)
        cols = [c[0] for c in cur.description]
        fila = cur.fetchone()
        if fila is None:
            return {"disponible": True, "existe": False, "motivo": f"SPID {spid} no existe o ya se desconectó"}
        sesion = dict(zip(cols, [str(v) if hasattr(v, "isoformat") else v for v in fila]))

        cur.execute("""
            SELECT r.status, r.command, r.blocking_session_id, r.wait_type,
                   r.wait_time AS espera_ms, r.total_elapsed_time / 1000.0 AS segundos_corriendo,
                   DB_NAME(r.database_id) AS db, t.text AS sql_text
            FROM sys.dm_exec_requests r
            CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t
            WHERE r.session_id = ?
        """, spid)
        cols2 = [c[0] for c in cur.description]
        fila2 = cur.fetchone()
        request_activo = dict(zip(cols2, fila2)) if fila2 else None

        cur.execute("""
            SELECT t.text AS ultima_query
            FROM sys.dm_exec_connections c
            OUTER APPLY sys.dm_exec_sql_text(c.most_recent_sql_handle) t
            WHERE c.session_id = ?
        """, spid)
        fila3 = cur.fetchone()
        ultima_query = fila3[0] if fila3 else None

        return {
            "disponible": True,
            "existe": True,
            "sesion": sesion,
            "request_activo": request_activo,
            "ultima_query": ultima_query,
            "consultado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        return {"disponible": False, "motivo": str(e)[:200]}
    finally:
        if conn is not None:
            conn.close()


# =============================================================================
# RUTAS
# =============================================================================
@app.route("/")
def index():
    return render_template("watchdog_dashboard.html", api_key=WATCHDOG_API_KEY)


@app.route("/api/analisis")
def api_analisis():
    filtros = {
        "spid": request.args.get("spid", ""),
        "host": request.args.get("host", ""),
        "ip": request.args.get("ip", ""),
        "motivo": request.args.get("motivo", ""),
        "desde": request.args.get("desde", ""),
        "hasta": request.args.get("hasta", ""),
    }
    return jsonify(_armar_analisis(filtros))


@app.route("/api/vivo")
def api_vivo():
    return jsonify(_consultar_vivo())


@app.route("/api/spid/<int:spid>/query")
def api_spid_query(spid):
    resultado = _consultar_query_spid(spid)
    resultado["historico"] = _buscar_evidencia_historica_spid(spid)
    return jsonify(resultado)


if __name__ == "__main__":
    print("=== Dashboard Watchdog SQL — http://localhost:5005 ===")
    app.run(host="0.0.0.0", port=5005, debug=False)

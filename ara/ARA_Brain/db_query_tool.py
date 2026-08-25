"""Skill de consulta SQL de SOLO LECTURA para el asistente ARA (ReAct).

Flujo: el LLM inspecciona el esquema de `proyecto_ara.db`, construye una
consulta SELECT, se ejecuta en modo read-only REAL (URI mode=ro) y los
resultados se inyectan al prompt del chat para que ARA responda con datos
precisos (nunca inventados).

Seguridad: la conexión se abre con `mode=ro` (el motor SQLite bloquea
cualquier escritura aunque la validación de texto fallara) + `PRAGMA
query_only=ON` + validación de sentencia SELECT + tope de filas.
"""

import json
import os
import re
import sqlite3
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "proyecto_ara.db")

_LIMITE_FILAS_MAX = 25

# Sentencias/controles que nunca se permiten (aunque la conexión ya es
# read-only, se rechazan para responder con un error claro al usuario).
_PALABRAS_FORBIDDEN = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "REPLACE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "ANALYZE",
    "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "GRANT", "REVOKE",
)

# Palabras de dominio operativo (notas, preparadores, puntos, ubicaciones).
_PALABRAS_SQL_OPERATIVA = (
    "nota", "notas", "preparad", "preparó", "preparo", "cheque", "chequed",
    "puntos", "punto", "ubicaci", "reubicaci", "artícul", "articul",
    "producto", "inventario", "stock", "entrega", "bulto", "lote", "usuario",
    "empleado", "quien", "quién", "cuánt", "cuant", "listad", "total", "top",
    "movimiento", "mover", "cambiaron", "cambio", "llevó", "llevo", "despach",
)


def es_consulta_sql_operativa(mensaje: str) -> bool:
    """True si la pregunta parece una consulta operativa de la BD local.

    Requiere señal FUERTE (número de nota de 6+ dígitos, código de artículo
    tipo CR00459, o mención directa de nota/preparador) + al menos una
    palabra de dominio, para no dispararse en charla general.
    """
    m = (mensaje or "").lower()
    senal_fuerte = (
        bool(re.search(r"\b\d{6,}\b", mensaje or ""))
        or bool(re.search(r"\b[a-z]{1,3}\d{3,}\b", m))
        or any(p in m for p in ("preparador", "nota"))
    )
    senal_dominio = any(p in m for p in _PALABRAS_SQL_OPERATIVA)
    return senal_fuerte and senal_dominio


def _tablas_referenciadas(query: str) -> set:
    """Extrae los nombres de tabla que aparecen tras FROM/JOIN (incluye
    listas separadas por coma: `FROM a, b`). Usado para la whitelist —
    una lista negra de palabras se puede bordear con creatividad (ej.
    `UNION SELECT ... FROM sqlite_master`); exigir que TODA tabla referenciada
    esté en el esquema conocido cierra esa vía sin importar cómo se redacte
    la consulta."""
    tablas = set()
    for m in re.finditer(r'\b(?:FROM|JOIN)\s+["\'`\[]?([A-Za-z_][A-Za-z0-9_]*)', query, re.IGNORECASE):
        tablas.add(m.group(1).lower())
    m_from = re.search(
        r'\bFROM\s+(.*?)(?:\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|\bJOIN\b|$)',
        query, re.IGNORECASE | re.DOTALL
    )
    if m_from:
        for parte in m_from.group(1).split(','):
            ident = re.match(r'\s*["\'`\[]?([A-Za-z_][A-Za-z0-9_]*)', parte)
            if ident:
                tablas.add(ident.group(1).lower())
    return tablas


def ejecutar_consulta_sql_read_only(
    query_sql: str, limite_filas: int = _LIMITE_FILAS_MAX
) -> dict:
    """Ejecuta una consulta SELECT de solo lectura en `proyecto_ara.db`.

    Retorna {"status": "ok", "filas": [...], "total_filas": N,
             "columnas": [...], "consulta": ...} o
    {"status": "error", "detalle": ...}.
    """
    query_clean = (query_sql or "").strip().rstrip(";").strip()
    if not query_clean:
        return {"status": "error", "detalle": "Consulta vacía."}
    if not re.match(r"^\s*SELECT\b", query_clean, re.IGNORECASE):
        return {"status": "error",
                "detalle": "Solo se permiten consultas de lectura (SELECT)."}
    if ";" in query_clean:
        return {"status": "error", "detalle": "Múltiples sentencias no permitidas."}

    upper = query_clean.upper()
    if any(f in upper for f in _PALABRAS_FORBIDDEN):
        return {"status": "error",
                "detalle": "Operación no permitida en la base de datos."}

    # Whitelist de tablas (además de la lista negra de arriba): rechaza
    # cualquier tabla que no esté en el esquema real conocido — incluye
    # sqlite_master/sqlite_temp_master y cualquier otra no listada, cerrando
    # la vía de "UNION SELECT ... FROM tabla_no_prevista" que una lista
    # negra de palabras por sí sola no puede cubrir.
    esquema = obtener_esquema_bd()
    if esquema.get("status") == "ok":
        tablas_permitidas = {t.lower() for t in esquema["tablas"].keys()}
        tablas_usadas = _tablas_referenciadas(query_clean)
        tablas_no_permitidas = tablas_usadas - tablas_permitidas
        if tablas_no_permitidas:
            return {"status": "error",
                    "detalle": f"Tabla(s) no permitida(s): {', '.join(sorted(tablas_no_permitidas))}."}

    # Tope de filas: si el LLM no puso LIMIT, se aplica el máximo del skill.
    if not re.search(r"\bLIMIT\b", upper):
        query_clean = f"{query_clean} LIMIT {limite_filas}"

    try:
        conn = sqlite3.connect(
            f"file:{DB_PATH}?mode=ro", uri=True, timeout=5.0
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON;")
        rows = conn.execute(query_clean).fetchall()
        conn.close()
        filas = [dict(r) for r in rows[:limite_filas]]
        return {
            "status": "ok",
            "filas": filas,
            "total_filas": len(rows),
            "columnas": list(filas[0].keys()) if filas else [],
            "consulta": query_clean,
        }
    except Exception as e:
        return {"status": "error", "detalle": str(e)}


def obtener_esquema_bd(limite_tablas: int = 30) -> dict:
    """Inspecciona tablas + columnas de `proyecto_ara.db` (read-only)."""
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        tablas = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ][:limite_tablas]
        esquema = {}
        for t in tablas:
            cols = [
                r[1]
                for r in conn.execute(f'PRAGMA table_info("{t}")').fetchall()
            ]
            esquema[t] = cols
        conn.close()
        return {"status": "ok", "tablas": esquema}
    except Exception as e:
        return {"status": "error", "detalle": str(e)}


def formatear_esquema_para_prompt() -> str:
    """Texto compacto del esquema para el prompt del generador de SQL."""
    esquema = obtener_esquema_bd()
    if esquema.get("status") != "ok":
        return "(esquema no disponible)"
    lineas = []
    for tabla, cols in esquema["tablas"].items():
        lineas.append(f"- {tabla}: {', '.join(cols)}")
    return "\n".join(lineas)


def _extraer_select(texto: str):
    """Extrae el SELECT de la respuesta del LLM (JSON / fenced / inline)."""
    if not texto:
        return None
    try:
        obj = json.loads(texto)
        if isinstance(obj, dict) and obj.get("sql"):
            return str(obj["sql"]).strip()
    except Exception:
        pass
    m = re.search(r"```sql\s*([\s\S]*?)```", texto, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"SELECT\b[\s\S]*?(?:;|$)", texto, re.IGNORECASE)
    if m:
        return m.group(0).strip().rstrip(";")
    return None


def generar_select_desde_mensaje(mensaje_usuario: str) -> dict:
    """Paso 1 del skill (ReAct): el LLM construye el SELECT sobre el esquema.

    Reutiliza el pool de keys NVIDIA NIM con failover de ara_brain
    (timeout corto de 15s). Retorna {"status": "ok", "sql": ...} o
    {"status": "error", "detalle": ...}.
    """
    system_prompt = (
        "Eres un generador de consultas SQL para SQLite. "
        "Esquema disponible de la base local:\n"
        f"{formatear_esquema_para_prompt()}\n\n"
        "Reglas estrictas:\n"
        "1. Devuelve UNA sola consulta SELECT de solo lectura que responda "
        "la pregunta del usuario con la mayor precisión posible.\n"
        "2. Usa SOLO tablas y columnas del esquema listado.\n"
        "3. Prefiere texto sin distinción de mayúsculas (LOWER/LIKE).\n"
        "4. No uses INSERT/UPDATE/DELETE/PRAGMA/DROP.\n"
        "5. Responde EXCLUSIVAMENTE con JSON plano en una línea, con la "
        'forma: {"sql": "SELECT ..."}. Nada más.\n\n'
        "PISTAS DE DOMINIO:\n"
        "- Preparador/chequeador de una nota -> notas_entrega "
        "(numero_nota, preparador_id, estado, auto_chequeado) unido con "
        "usuarios (id, nombre).\n"
        "- Movimientos/reubicaciones de artículos -> reportes_ubicacion "
        "(co_art, usuario, desde, hacia, fecha, procesado_profit).\n"
        "- Puntos por usuario/módulo -> log_puntos (usuario, modulo, "
        "referencia_id, puntos_ganados, fecha_registro).\n"
        "- Artículos/stock -> stock_maestro (codigo, descripcion, "
        "stock_maestro, stock_bulto_cerrado, campo7, codigo_barra).\n"
        "- Detalle de ítems de una nota -> detalle_nota.\n"
        "- Revisa también el historial con reportes_ubicacion cuando la "
        "pregunta es sobre cambios de ubicación (el artículo puede no "
        "aparecer en stock_maestro)."
    )
    try:
        from ara_brain import llamar_nvidia_con_failover

        t0 = time.perf_counter()
        texto = llamar_nvidia_con_failover(
            system_prompt, mensaje_usuario,
            model="meta/llama-3.1-8b-instruct", timeout=15,
        )
        if not texto:
            return {"status": "error",
                    "detalle": "LLM no disponible para generar SQL."}
        sql = _extraer_select(texto)
        if not sql:
            return {"status": "error",
                    "detalle": "No se pudo extraer un SELECT de la respuesta."}
        ms = round((time.perf_counter() - t0) * 1000.0, 1)
        print(f"[SQL_SKILL] 🤖 SELECT generado en {ms}ms: {sql[:150]}", flush=True)
        return {"status": "ok", "sql": sql}
    except Exception as e:
        return {"status": "error", "detalle": str(e)[:150]}


def formatear_resultados_para_prompt(resultado: dict):
    """Convierte filas SQL en texto inyectable en el system prompt.

    Retorna str (aunque sea 0 filas, con evidencia) o None si la consulta
    falló (el chat sigue sin datos, sin romper el flujo).
    """
    if not resultado or resultado.get("status") != "ok":
        return None
    filas = resultado.get("filas", [])
    if not filas:
        return (
            "--- CONSULTA SQL (SOLO LECTURA): 0 FILAS ---\n"
            "La consulta ejecutada contra la base local no devolvió "
            "resultados. Indícalo honestamente y sugiere otra pregunta."
        )
    partes = [
        f"--- CONSULTA SQL (SOLO LECTURA) | {resultado['total_filas']} filas | "
        f"columnas: {', '.join(resultado.get('columnas', []))} ---"
    ]
    for f in filas:
        partes.append("- " + ", ".join(f"{k}={v}" for k, v in f.items()))
    return "\n".join(partes)

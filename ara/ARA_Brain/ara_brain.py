# -*- coding: utf-8 -*-
"""
ara_brain.py — Motor de Auditoría Inteligente 360° para Proyecto ARA.

Inyecta trazabilidad completa de artículos en el contexto de la IA
(function calling / context injection) para responder preguntas como:
  "¿Quién movió el artículo X?"
  "¿Cuál fue la última nota del producto Y y quién la chequeó?"
  "Auditoría de X"
"""
import os
import sqlite3
import json
import re
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'proyecto_ara.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def obtener_auditoria_completa_articulo(co_art: str) -> dict:
    """
    Consulta SQLite y retorna un dict JSON consolidado con:
      1. Stock / ubicación actual
      2. Último movimiento / reubicación
      3. Última nota de entrega con trazabilidad de operadores
    """
    resultado = {
        "co_art": co_art,
        "stock_actual": None,
        "ubicacion": None,
        "descripcion": None,
        "ultima_reubicacion": None,
        "ultima_nota": None,
        "trazabilidad_operadores": {},
        "error": None
    }

    conn = get_db()
    try:
        # --- 1. Stock maestro ---
        row = conn.execute("""
            SELECT codigo, descripcion, stock_maestro, campo7, deposito_bqto
            FROM stock_maestro
            WHERE codigo = ? OR codigo_barra = ?
            LIMIT 1
        """, (co_art, co_art)).fetchone()

        if row:
            resultado["stock_actual"] = float(row["stock_maestro"] or 0)
            resultado["ubicacion"] = row["campo7"] or "Sin asignar"
            resultado["descripcion"] = row["descripcion"] or ""

        # --- 2. Última reubicación (reportes_ubicacion) ---
        mov = conn.execute("""
            SELECT usuario, desde, hacia, fecha
            FROM reportes_ubicacion
            WHERE co_art = ?
            ORDER BY fecha DESC
            LIMIT 1
        """, (co_art,)).fetchone()

        if mov:
            resultado["ultima_reubicacion"] = {
                "usuario": mov["usuario"],
                "desde": mov["desde"],
                "hacia": mov["hacia"],
                "fecha": mov["fecha"]
            }

        # --- 3. Última nota de entrega donde salió el artículo ---
        nota_row = conn.execute("""
            SELECT dn.nota_id, ne.numero_nota, ne.cliente, ne.fecha_creacion,
                   dn.cantidad_preparada, dn.cantidad_solicitada,
                   ne.preparador_id, ne.auto_chequeado
            FROM detalle_nota dn
            JOIN notas_entrega ne ON ne.id = dn.nota_id
            WHERE dn.co_art = ? AND ne.estado IN ('embalada', 'entregada')
            ORDER BY ne.fecha_creacion DESC
            LIMIT 1
        """, (co_art,)).fetchone()

        if nota_row:
            nota_id = nota_row["nota_id"]
            resultado["ultima_nota"] = {
                "numero_nota": nota_row["numero_nota"],
                "cliente": nota_row["cliente"],
                "fecha": nota_row["fecha_creacion"],
                "cantidad": float(nota_row["cantidad_preparada"] or nota_row["cantidad_solicitada"] or 0),
                "nota_id": nota_id
            }

            # --- 4. Trazabilidad de operadores desde movimientos_preparador ---
            ops = conn.execute("""
                SELECT usuario, accion
                FROM movimientos_preparador
                WHERE nota_id = ?
                ORDER BY timestamp ASC
            """, (nota_id,)).fetchall()

            preparador = None
            chequeador = None
            embalador = None

            for op in ops:
                accion = (op["accion"] or "").lower()
                usr = op["usuario"]
                if accion == "tomar" or accion == "completar":
                    if not preparador:
                        preparador = usr
                elif "transicion" in accion:
                    if "chequeada" in accion:
                        chequeador = usr
                    elif "embalada" in accion:
                        embalador = usr
                elif accion == "chequeada":
                    chequeador = usr
                elif accion == "embalada":
                    embalador = usr

            # Fallback: si no se detectó vía transición, usar preparador_id
            if not preparador and nota_row["preparador_id"]:
                preparador = nota_row["preparador_id"]

            resultado["trazabilidad_operadores"] = {
                "usuario_preparador": preparador,
                "usuario_chequeador": chequeador,
                "usuario_embalador": embalador
            }

    except Exception as e:
        resultado["error"] = str(e)
    finally:
        conn.close()

    return resultado


# Patrones para detectar consultas de producto/trazabilidad en lenguaje natural
PATRONES_PRODUCTO = [
    re.compile(r'(?:código|articulo|producto|item|sku|referencia)\s*[:\s]*([a-z0-9\-\.]+)', re.I),
    re.compile(r'(?:auditoria|trazabilidad|rastrear|historial)\s*(?:de\s*)?(?:\S+\s+)?([a-z0-9\-\.]{3,})', re.I),
    re.compile(r'(?:quién|quien|quién)\s*(?:movió|manipuló|tocó|preparó|chequeó|embaló)\s*(?:el\s+)?(?:articulo|producto)?\s*([a-z0-9\-\.]+)', re.I),
    re.compile(r'(?:ultima|última|último)\s*(?:nota|movimiento|reubicación|reubicacion)\s*(?:de|del)?\s*([a-z0-9\-\.]{3,})', re.I),
]

# Palabras clave que activan búsqueda de código de artículo
PALABRAS_AUDITORIA = ['auditoria', 'trazabilidad', 'rastrear', 'historial', 'quién movió',
                       'quien movio', 'quién preparó', 'quien preparo',
                       'última nota', 'ultima nota', 'quién chequeó', 'quien chequeo',
                       'quién embaló', 'quien embarco']
PALABRAS_STOCK = ['stock', 'inventario', 'ubicación', 'ubicacion', 'existencia',
                   'cuánto hay', 'cuanto hay', 'suficiente']


def detectar_codigo_articulo(mensaje: str) -> str | None:
    """
    Analiza el mensaje del usuario y extrae un posible código de artículo.
    Retorna el código o None.
    """
    if not mensaje:
        return None

    for pat in PATRONES_PRODUCTO:
        m = pat.search(mensaje)
        if m:
            codigo = m.group(1).strip().upper()
            if len(codigo) >= 2:
                return codigo

    # Fallback: buscar tokens que parezcan códigos (mayúsculas, números, guiones)
    tokens = re.findall(r'\b([A-Z0-9][A-Z0-9\-\.]{2,15})\b', mensaje.upper())
    # Verificar si algún token existe en stock_maestro
    conn = get_db()
    try:
        for token in tokens:
            row = conn.execute(
                "SELECT codigo FROM stock_maestro WHERE codigo = ? LIMIT 1",
                (token,)
            ).fetchone()
            if row:
                conn.close()
                return row["codigo"]
    except Exception:
        pass
    conn.close()

    return None


SYSTEM_PROMPT_AUDITOR = """Eres ARA Intelligent, el Auditor Supervisor de Proyecto ARA.

📌 **DOMINIO HEXAGONAL**
Operas bajo **Arquitectura Hexagonal** y controlas de forma estricta la **Máquina de Estados Logística**:
[pendiente ➔ preparando ➔ preparada ➔ chequeada ➔ embalada ➔ entregada ➔ devuelta]

🎯 **TU REGLA DE ORO DE DOMINIO**:
1. TIENES ACCESO TOTAL a la trazabilidad atómica de notas y artículos.
2. NINGUNA nota se embala sin estar chequeada.
3. Tus respuestas deben ser **irrefutables**, citando operarios, números de caja, timestamps de estados y ubicaciones físicas.
4. Si falta información, **exígela con criterio operativo**.

[EVIDENCIAS EN TIEMPO REAL EXTRAÍDAS DE LA BD]:
--- Datos del Artículo {co_art} ---
- Descripción: {descripcion}
- Stock/Ubicación actual: {stock_actual} unds en {ubicacion}
- Última reubicación: por {usuario_reubicacion} el {fecha_reubicacion} de {origen_reubicacion} a {destino_reubicacion}.
- Última Nota Despachada: #{num_nota} del {fecha_nota} ({cantidad} unidades para {cliente}).
- Responsables de la Nota:
  * Preparado por: {usuario_preparador}
  * Chequeado por: {usuario_chequeador}
  * Embalado por: {usuario_embalador}
--- Fin de Evidencias ---

Si no hay datos en algún campo, indícalo honestamente.
"""

AUDITOR_PROMPT = SYSTEM_PROMPT_AUDITOR  # alias retrocompatible


# =============================================================================
# PUERTO DE TRAZABILIDAD 100% HEXAGONAL
# =============================================================================

def obtener_trazabilidad_hexagonal(entidad_id: str) -> dict:
    """
    Puerto hexagonal de trazabilidad atómica.
    Detecta automáticamente si entidad_id es una NOTA (co_nota) o ARTÍCULO (co_art)
    y extrae trazabilidad completa.
    
    Para NOTAS: cliente, estado actual, conteo cajas, operadores + timestamps.
    Para ARTÍCULOS: stock, ubicación campo7, historial reubicaciones, últimas 3 notas.
    """
    if not entidad_id:
        return {"error": "entidad_id vacío", "tipo": None}

    conn = get_db()
    try:
        tipo = None
        payload = {}

        # --- Detectar tipo: NOTA vs ARTÍCULO ---
        entidad_upper = entidad_id.strip().upper()

        # Buscar en notas_entrega por numero_nota
        nota = conn.execute(
            "SELECT id, numero_nota, cliente, estado, fecha_creacion, preparador_id, "
            "       usuario_chequeador, usuario_embalador, numero_cajas "
            "FROM notas_entrega WHERE numero_nota = ? LIMIT 1",
            (entidad_upper,)
        ).fetchone()

        if nota:
            tipo = "NOTA"
            nota_id = nota["id"]

            # Operadores y timestamps desde movimientos_preparador
            movs = conn.execute(
                "SELECT usuario, accion, timestamp FROM movimientos_preparador "
                "WHERE nota_id = ? ORDER BY timestamp ASC",
                (nota_id,)
            ).fetchall()

            operadores = []
            for m in movs:
                operadores.append({
                    "usuario": m["usuario"],
                    "accion": m["accion"],
                    "timestamp": m["timestamp"]
                })

            payload = {
                "tipo": "NOTA",
                "numero_nota": nota["numero_nota"],
                "cliente": nota["cliente"],
                "estado": nota["estado"],
                "fecha_creacion": nota["fecha_creacion"],
                "numero_cajas": nota["numero_cajas"],
                "preparador_id": nota["preparador_id"],
                "usuario_chequeador": nota["usuario_chequeador"],
                "usuario_embalador": nota["usuario_embalador"],
                "trazabilidad_operadores": operadores
            }
        else:
            # Buscar en stock_maestro como ARTÍCULO
            art = conn.execute(
                "SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado, campo7, deposito_bqto "
                "FROM stock_maestro WHERE codigo = ? OR codigo_barra = ? LIMIT 1",
                (entidad_upper, entidad_upper)
            ).fetchone()

            if art:
                tipo = "ARTICULO"
                co_art = art["codigo"]

                # Historial reubicaciones
                reubs = conn.execute(
                    "SELECT usuario, desde, hacia, fecha FROM reportes_ubicacion "
                    "WHERE co_art = ? ORDER BY fecha DESC LIMIT 10",
                    (co_art,)
                ).fetchall()

                # Últimas 3 notas donde fue despachado
                notas = conn.execute("""
                    SELECT ne.numero_nota, ne.cliente, ne.estado, ne.fecha_creacion,
                           dn.cantidad_preparada, dn.cantidad_solicitada
                    FROM detalle_nota dn
                    JOIN notas_entrega ne ON ne.id = dn.nota_id
                    WHERE dn.co_art = ?
                    ORDER BY ne.fecha_creacion DESC
                    LIMIT 3
                """, (co_art,)).fetchall()

                payload = {
                    "tipo": "ARTICULO",
                    "codigo": co_art,
                    "descripcion": art["descripcion"],
                    "stock_actual": float(art["stock_maestro"] or 0),
                    "stock_bulto_cerrado": float(art["stock_bulto_cerrado"] or 0),
                    "ubicacion": art["campo7"] or "Sin asignar",
                    "deposito_bqto": art["deposito_bqto"],
                    "historial_reubicaciones": [
                        {"usuario": r["usuario"], "desde": r["desde"],
                         "hacia": r["hacia"], "fecha": r["fecha"]}
                        for r in reubs
                    ],
                    "ultimas_notas": [
                        {"numero_nota": n["numero_nota"], "cliente": n["cliente"],
                         "estado": n["estado"], "fecha": n["fecha_creacion"],
                         "cantidad": float(n["cantidad_preparada"] or n["cantidad_solicitada"] or 0)}
                        for n in notas
                    ]
                }

        if not tipo:
            conn.close()
            return {"error": f"No se encontró '{entidad_id}' como NOTA ni ARTÍCULO", "tipo": None}

        conn.close()
        return payload

    except Exception as e:
        conn.close()
        return {"error": str(e), "tipo": None}


# =============================================================================
# MOTOR DE FEEDBACK Y AUTO-MEJORA (Aprendizaje de Skills)
# =============================================================================

def init_ia_feedback_table():
    """Crea la tabla log_ia_feedback si no existe."""
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS log_ia_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pregunta TEXT NOT NULL,
                respuesta_ia TEXT NOT NULL,
                es_correcta INTEGER DEFAULT 1,
                corregida_por TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    finally:
        conn.close()


def registrar_feedback(
    pregunta: str,
    respuesta_ia: str,
    es_correcta: bool = True,
    corregida_por: str | None = None
) -> int:
    """
    Registra el feedback del usuario sobre una respuesta de la IA.
    Retorna el ID insertado.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO log_ia_feedback (pregunta, respuesta_ia, es_correcta, corregida_por) "
            "VALUES (?, ?, ?, ?)",
            (pregunta, respuesta_ia, 1 if es_correcta else 0, corregida_por)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def obtener_feedback_reciente(limite: int = 10) -> list[dict]:
    """Retorna los últimos N registros de feedback para inyectar como contexto."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, pregunta, respuesta_ia, es_correcta, corregida_por, timestamp "
            "FROM log_ia_feedback ORDER BY id DESC LIMIT ?",
            (limite,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def obtener_lecciones_aprendidas(limite: int = 5) -> str:
    """
    Analiza respuestas correctas (es_correcta=1) y extrae un resumen
    de 'lecciones aprendidas' para inyectar en el System Prompt.
    """
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT pregunta, respuesta_ia FROM log_ia_feedback "
            "WHERE es_correcta = 1 ORDER BY id DESC LIMIT ?",
            (limite,)
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return ""

    lecciones = []
    for r in rows:
        pregunta_breve = (r["pregunta"] or "")[:80]
        respuesta_breve = (r["respuesta_ia"] or "")[:120]
        lecciones.append(f"- Pregunta: \"{pregunta_breve}\" → Respuesta: \"{respuesta_breve}\"")

    return ("--- LECCIONES APRENDIDAS (Feedback Correcto Reciente) ---\n"
            + "\n".join(lecciones)
            + "\n--- Fin Lecciones ---")


def formatear_evidencias_para_prompt(auditoria: dict) -> str:
    """Convierte el dict de auditoría en el bloque de evidencias para el System Prompt."""
    if not auditoria or auditoria.get("error"):
        return ""

    co_art = auditoria.get("co_art", "N/A")
    desc = auditoria.get("descripcion") or "Sin descripción"
    stock = auditoria.get("stock_actual") or "N/A"
    ubi = auditoria.get("ubicacion") or "N/A"

    reub = auditoria.get("ultima_reubicacion") or {}
    reub_usuario = reub.get("usuario") or "N/A"
    reub_fecha = reub.get("fecha") or "N/A"
    reub_origen = reub.get("desde") or "N/A"
    reub_destino = reub.get("hacia") or "N/A"

    nota = auditoria.get("ultima_nota") or {}
    num_nota = nota.get("numero_nota") or "N/A"
    fecha_nota = nota.get("fecha") or "N/A"
    cantidad = nota.get("cantidad") or "N/A"
    cliente = nota.get("cliente") or "N/A"

    ops = auditoria.get("trazabilidad_operadores") or {}
    prep = ops.get("usuario_preparador") or "No registrado"
    chq = ops.get("usuario_chequeador") or "No registrado"
    emb = ops.get("usuario_embalador") or "No registrado"

    return SYSTEM_PROMPT_AUDITOR.format(
        co_art=co_art,
        descripcion=desc,
        stock_actual=stock,
        ubicacion=ubi,
        usuario_reubicacion=reub_usuario,
        fecha_reubicacion=reub_fecha,
        origen_reubicacion=reub_origen,
        destino_reubicacion=reub_destino,
        num_nota=num_nota,
        fecha_nota=fecha_nota,
        cantidad=cantidad,
        cliente=cliente,
        usuario_preparador=prep,
        usuario_chequeador=chq,
        usuario_embalador=emb
    )


def es_consulta_auditoria(mensaje: str) -> bool:
    """Determina si el mensaje del usuario es una consulta de auditoría/trazabilidad."""
    if not mensaje:
        return False
    msg_lower = mensaje.lower()
    for kw in PALABRAS_AUDITORIA:
        if kw in msg_lower:
            return True
    for kw in PALABRAS_STOCK:
        if kw in msg_lower:
            return True
    return False


# =============================================================================
# GENERADOR DE REPORTES DE ROTACIÓN Y MÁS VENDIDOS
# =============================================================================

PALABRAS_REPORTE = [
    'reporte', 'report', 'más vendidos', 'mas vendidos', 'productos top',
    'top productos', 'rotación', 'rotacion', 'más movidos', 'mas movidos',
    'volumen de salida', 'producto más despachado', 'mayor salida',
    'ranking', 'productos más populares', 'productos estrella'
]


def es_consulta_reporte(mensaje: str) -> bool:
    """Detecta si el usuario está solicitando un reporte ejecutivo."""
    if not mensaje:
        return False
    msg_lower = mensaje.lower()
    for kw in PALABRAS_REPORTE:
        if kw in msg_lower:
            return True
    return False


def obtener_reporte_top_productos(dias: int = 30, limite: int = 10) -> dict:
    """
    Consulta SQLite para obtener los productos con mayor volumen de salida.

    Retorna dict con:
      - total_notas_procesadas: int
      - productos: list[dict] con co_art, descripcion, total_despachado,
                   total_notas, stock_actual, ubicacion, riesgo_quiebre
      - periodo_dias: int
      - fecha_generacion: str
    """
    conn = get_db()
    try:
        # Calcular fecha límite
        from datetime import datetime, timedelta
        fecha_limite = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d %H:%M:%S')

        # Total notas procesadas en el período
        total_notas = conn.execute(
            "SELECT COUNT(DISTINCT nota_id) as total FROM movimientos_preparador "
            "WHERE timestamp >= ?", (fecha_limite,)
        ).fetchone()["total"]

        # Top productos despachados
        rows = conn.execute("""
            SELECT mp.co_art,
                   mp.descripcion as art_des,
                   SUM(mp.cantidad) as total_despachado,
                   COUNT(DISTINCT mp.nota_id) as total_notas
            FROM movimientos_preparador mp
            WHERE mp.timestamp >= ?
              AND mp.cantidad > 0
            GROUP BY mp.co_art
            ORDER BY total_despachado DESC
            LIMIT ?
        """, (fecha_limite, limite)).fetchall()

        productos = []
        for r in rows:
            co_art = r["co_art"]
            despachado = float(r["total_despachado"] or 0)
            notas_count = r["total_notas"] or 0

            # Stock actual y ubicación
            stock_row = conn.execute(
                "SELECT stock_maestro, campo7 FROM stock_maestro "
                "WHERE codigo = ? LIMIT 1", (co_art,)
            ).fetchone()

            stock_actual = float(stock_row["stock_maestro"]) if stock_row and stock_row["stock_maestro"] else 0
            ubicacion = stock_row["campo7"] if stock_row and stock_row["campo7"] else "Sin asignar"

            productos.append({
                "co_art": co_art,
                "descripcion": r["art_des"] or "Sin descripción",
                "total_despachado": despachado,
                "total_notas": notas_count,
                "stock_actual": stock_actual,
                "ubicacion": ubicacion,
                "riesgo_quiebre": stock_actual < despachado * 0.3
            })

        conn.close()
        return {
            "total_notas_procesadas": total_notas,
            "productos": productos,
            "periodo_dias": dias,
            "fecha_generacion": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

    except Exception as e:
        conn.close()
        return {"error": str(e), "productos": []}


def formatear_reporte_para_prompt(reporte: dict) -> str:
    """Convierte el reporte en un bloque de texto para inyectar en el System Prompt."""
    if not reporte or reporte.get("error"):
        return ""

    productos = reporte.get("productos", [])
    if not productos:
        return ""

    lines = [
        "--- REPORTE DE PRODUCTOS MÁS VENDIDOS/ROTACIÓN ---",
        f"Período: últimos {reporte['periodo_dias']} días",
        f"Notas procesadas en el período: {reporte['total_notas_procesadas']}",
        f"Generado: {reporte['fecha_generacion']}",
        "",
        "Ranking de productos por volumen de salida:",
    ]

    for i, p in enumerate(productos, 1):
        riesgo = "⚠️ ALERTA: Stock bajo para su rotación" if p["riesgo_quiebre"] else "✅ Stock suficiente"
        lines.append(
            f"  {i}. {p['co_art']} — {p['descripcion']} | "
            f"Despachado: {p['total_despachado']:.0f} unds en {p['total_notas']} notas | "
            f"Stock actual: {p['stock_actual']:.0f} unds en {p['ubicacion']} | {riesgo}"
        )

    lines.append("--- FIN DEL REPORTE ---")
    return "\n".join(lines)


# =============================================================================
# KEY POOL NVIDIA NIM — Rotación Automática con Failover
# =============================================================================

NVIDIA_API_KEYS = [
    "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",
    "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",
    "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",
    "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",
    "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"
]

_KEY_INDEX = 0
_KEY_LOCK = __import__('threading').Lock()

STATUS_FALLO_KEY = {503, 429, 401, 403}


def llamar_nvidia_con_failover(
    prompt_sistema: str,
    mensaje_usuario: str,
    model: str = "deepseek-ai/deepseek-v4-flash-free",
    timeout: int = 60
) -> str | None:
    """
    Realiza la petición a NVIDIA NIM con rotación automática de API keys.

    - Intenta con key_index_actual.
    - Si la respuesta es 503/429/401/403, rota a la siguiente key y reintenta.
    - Si las 5 keys fallan, retorna None (para que el caller caiga a Ollama).
    - Retorna el texto de respuesta o None.
    """
    import requests as req_lib

    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": mensaje_usuario}
        ],
        "temperature": 0.3,
        "max_tokens": 512
    }

    global _KEY_INDEX
    keys_probadas = set()

    with _KEY_LOCK:
        idx_inicial = _KEY_INDEX
        idx = idx_inicial

    while len(keys_probadas) < len(NVIDIA_API_KEYS):
        if idx in keys_probadas:
            break
        keys_probadas.add(idx)
        api_key = NVIDIA_API_KEYS[idx]

        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            resp = req_lib.post(url, json=payload, headers=headers, timeout=timeout)

            if resp.status_code == 200:
                texto = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
                with _KEY_LOCK:
                    _KEY_INDEX = (idx + 1) % len(NVIDIA_API_KEYS)
                return texto

            if resp.status_code in STATUS_FALLO_KEY:
                print(f"⚠️ [NVIDIA KEY POOL] Key #{idx + 1} falló (HTTP {resp.status_code}). Rotando...")
                with _KEY_LOCK:
                    _KEY_INDEX = (idx + 1) % len(NVIDIA_API_KEYS)
                    idx = _KEY_INDEX
                continue

            print(f"⚠️ [NVIDIA KEY POOL] Key #{idx + 1} error HTTP {resp.status_code}: {resp.text[:100]}")
            with _KEY_LOCK:
                _KEY_INDEX = (idx + 1) % len(NVIDIA_API_KEYS)
                idx = _KEY_INDEX
            continue

        except req_lib.exceptions.Timeout:
            print(f"⚠️ [NVIDIA KEY POOL] Key #{idx + 1} timeout. Rotando...")
            with _KEY_LOCK:
                _KEY_INDEX = (idx + 1) % len(NVIDIA_API_KEYS)
                idx = _KEY_INDEX
            continue

        except Exception as e:
            print(f"⚠️ [NVIDIA KEY POOL] Key #{idx + 1} excepción: {e}. Rotando...")
            with _KEY_LOCK:
                _KEY_INDEX = (idx + 1) % len(NVIDIA_API_KEYS)
                idx = _KEY_INDEX
            continue

    print("❌ [NVIDIA KEY POOL] Las 5 keys fallaron. Cayendo a Ollama fallback.")
    with _KEY_LOCK:
        _KEY_INDEX = 0
    return None

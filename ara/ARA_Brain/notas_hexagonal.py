# -*- coding: utf-8 -*-
"""
notas_hexagonal.py — Arquitectura Hexagonal para Notas de Entrega

Capas:
  1. Domain Models (dataclasses)
  2. Puertos SQL (inicialización, CRUD)
  3. Servicios de aplicación (visión, concurrencia, trazabilidad)
  4. Endpoints Flask (registrados vía register_notas_routes)

Tablas:
  - notas_entrega       (encabezado con estado y es_prueba)
  - detalle_nota        (renglones)
  - movimientos_preparador (trazabilidad atómica)
"""
import os
import io
import json
import sqlite3
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
from enum import Enum

from flask import request, jsonify, send_file, make_response
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# =============================================================================
# 1. DOMAIN MODELS
# =============================================================================

class EstadoNota(str, Enum):
    PENDIENTE  = "pendiente"
    PREPARANDO = "preparando"
    PREPARADA  = "preparada"
    CHEQUEADA  = "chequeada"
    EMBALADA   = "embalada"
    ENTREGADA  = "entregada"
    DEVUELTA   = "devuelta"

TRANSICIONES_VALIDAS = {
    'pendiente':  ['preparando'],
    'preparando': ['preparada', 'chequeada'],
    'preparada':  ['chequeada'],
    'chequeada':  ['embalada'],
    'embalada':   ['entregada'],
    'entregada':  ['devuelta'],
    'devuelta':   [],
}

NOTA_REQUIERE_UBICACION = {'preparada', 'chequeada', 'embalada'}

def validar_transicion(estado_actual: str, estado_destino: str) -> bool:
    return estado_destino in TRANSICIONES_VALIDAS.get(estado_actual, [])

class EstadoItem(str, Enum):
    PENDIENTE = "pendiente"
    PREPARADO = "preparado"

class UnidadMedida(str, Enum):
    UND    = "UND"
    CAJA   = "CAJA"
    SOBRE  = "SOBRE"
    BLISTER = "BLISTER"

@dataclass
class NotaDomain:
    numero_nota: str
    cliente: str = ""
    estado: EstadoNota = EstadoNota.PENDIENTE
    preparador_id: Optional[str] = None
    es_prueba: bool = False
    items_count: int = 0
    auto_chequeado: bool = False
    id: Optional[int] = None
    fecha_creacion: Optional[str] = None
    fecha_completada: Optional[str] = None

    def to_dict(self):
        d = asdict(self)
        d['estado'] = self.estado.value
        return d

@dataclass
class ItemDomain:
    nota_id: int
    co_art: str
    descripcion: str
    cantidad_solicitada: float
    cantidad_preparada: float = 0.0
    unidad_medida: str = "UND"
    estado: EstadoItem = EstadoItem.PENDIENTE
    id: Optional[int] = None

    def to_dict(self):
        d = asdict(self)
        d['estado'] = self.estado.value
        return d

@dataclass
class MovimientoDomain:
    nota_id: int
    co_art: str
    descripcion: str
    cantidad: float
    unidad_medida: str
    usuario: str
    accion: str
    origen: Optional[str] = None
    destino: Optional[str] = None
    id: Optional[int] = None
    timestamp: Optional[str] = None

    def to_dict(self):
        return asdict(self)

# =============================================================================
# 2. PUERTOS SQL
# =============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'proyecto_ara.db')

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

def init_notas_tables():
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notas_entrega (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_nota TEXT UNIQUE NOT NULL,
                cliente TEXT DEFAULT '',
                estado TEXT DEFAULT 'pendiente'
                    CHECK(estado IN ('pendiente','preparando','preparada','chequeada','embalada','entregada','devuelta')),
                preparador_id TEXT,
                es_prueba INTEGER DEFAULT 0,
                items_count INTEGER DEFAULT 0,
                auto_chequeado INTEGER DEFAULT 0,
                fecha_creacion TEXT DEFAULT (datetime('now','localtime')),
                fecha_completada TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS detalle_nota (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nota_id INTEGER NOT NULL REFERENCES notas_entrega(id),
                co_art TEXT DEFAULT '',
                descripcion TEXT DEFAULT '',
                cantidad_solicitada REAL DEFAULT 0,
                cantidad_preparada REAL DEFAULT 0,
                unidad_medida TEXT DEFAULT 'UND',
                estado TEXT DEFAULT 'pendiente'
                    CHECK(estado IN ('pendiente','preparado'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS movimientos_preparador (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nota_id INTEGER,
                co_art TEXT,
                descripcion TEXT,
                cantidad REAL,
                unidad_medida TEXT,
                usuario TEXT,
                accion TEXT,
                origen TEXT,
                destino TEXT,
                timestamp TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_detalle_nota ON detalle_nota(nota_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_movimientos_co_art ON movimientos_preparador(co_art)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_movimientos_usuario ON movimientos_preparador(usuario)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_movimientos_timestamp ON movimientos_preparador(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notas_estado ON notas_entrega(estado)")

        # Migration v3.6: Agregar columnas de control Profit si no existen
        columnas_existentes = {
            row[1] for row in conn.execute("PRAGMA table_info(movimientos_preparador)").fetchall()
        }
        columnas_profit = {
            "procesado_profit": "INTEGER DEFAULT 0",
            "fec_procesado_profit": "DATETIME",
            "usuario_procesado_profit": "TEXT",
        }
        for col_name, col_type in columnas_profit.items():
            if col_name not in columnas_existentes:
                conn.execute(f"ALTER TABLE movimientos_preparador ADD COLUMN {col_name} {col_type}")
                print(f"[Migration] Columna '{col_name}' agregada a movimientos_preparador")
            else:
                pass  # ya existe

        conn.commit()
    finally:
        conn.close()

# =============================================================================
# 3. SERVICIOS DE APLICACIÓN
# =============================================================================

# --- Visión IA para notas (Eliminado: sustituido por preparacion/ocr_notas/) ---

# --- CRUD Notas ---

def _buscar_o_crear_nota(numero_nota: str, cliente: str = "",
                         es_prueba: bool = False) -> dict:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM notas_entrega WHERE numero_nota = ?",
                           (numero_nota,)).fetchone()
        if row:
            return dict(row)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cur = conn.execute("""
            INSERT INTO notas_entrega (numero_nota, cliente, estado, es_prueba, fecha_creacion)
            VALUES (?, ?, 'pendiente', ?, ?)
        """, (numero_nota, cliente, 1 if es_prueba else 0, now))
        nota_id = cur.lastrowid
        conn.commit()
        return {"id": nota_id, "numero_nota": numero_nota, "cliente": cliente,
                "estado": "pendiente", "es_prueba": 1 if es_prueba else 0,
                "items_count": 0, "auto_chequeado": 0,
                "fecha_creacion": now, "fecha_completada": None,
                "preparador_id": None}
    finally:
        conn.close()

def _insertar_items(nota_id: int, items: list) -> list:
    conn = get_db()
    try:
        insertados = []
        for it in items:
            desc = it.get('descripcion', '')
            cant = float(it.get('cantidad', 1))
            und  = it.get('unidad', 'UND').upper()
            if und not in ('UND', 'CAJA', 'SOBRE', 'BLISTER'):
                und = 'UND'
            cur = conn.execute("""
                INSERT INTO detalle_nota (nota_id, descripcion, cantidad_solicitada, unidad_medida)
                VALUES (?, ?, ?, ?)
            """, (nota_id, desc, cant, und))
            insertados.append({"id": cur.lastrowid, "descripcion": desc,
                               "cantidad_solicitada": cant, "unidad_medida": und})
        conn.execute("UPDATE notas_entrega SET items_count = "
                     "(SELECT COUNT(*) FROM detalle_nota WHERE nota_id = ?) "
                     "WHERE id = ?", (nota_id, nota_id))
        conn.commit()
        return insertados
    finally:
        conn.close()

def _descontar_stock(co_art: str, cantidad: float) -> bool:
    conn = get_db()
    try:
        row = conn.execute("SELECT stock_maestro FROM stock_maestro WHERE codigo = ?",
                           (co_art,)).fetchone()
        if not row:
            return False
        nuevo_stock = max(0, float(row['stock_maestro'] or 0) - cantidad)
        conn.execute("UPDATE stock_maestro SET stock_maestro = ? WHERE codigo = ?",
                     (nuevo_stock, co_art))
        conn.commit()
        return True
    finally:
        conn.close()

def _registrar_movimiento(nota_id: int, co_art: str, descripcion: str,
                          cantidad: float, unidad_medida: str,
                          usuario: str, accion: str,
                          origen: str = None, destino: str = None):
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO movimientos_preparador
                (nota_id, co_art, descripcion, cantidad, unidad_medida, usuario, accion, origen, destino)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (nota_id, co_art, descripcion, cantidad, unidad_medida, usuario, accion, origen, destino))
        conn.commit()
    finally:
        conn.close()

# --- Bloqueo de concurrencia ---

def _tomar_nota(nota_id: int, usuario: str) -> dict:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM notas_entrega WHERE id = ?",
                           (nota_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "Nota no encontrada"}
        nota = dict(row)
        estado_actual = nota['estado']
        if not validar_transicion(estado_actual, 'preparando'):
            return {"ok": False,
                    "error": f"La nota {nota['numero_nota']} en estado '{estado_actual}' no puede pasar a 'preparando'"}
        if estado_actual == 'preparando' and nota['preparador_id'] != usuario:
            return {"ok": False,
                    "error": f"La nota {nota['numero_nota']} está siendo preparada actualmente por {nota['preparador_id']}"}
        conn.execute("UPDATE notas_entrega SET estado = 'preparando', preparador_id = ? WHERE id = ?",
                     (usuario, nota_id))
        conn.commit()
        _registrar_movimiento(nota_id, '', '', 0, '', usuario, 'tomar')
        nota['estado'] = 'preparando'
        nota['preparador_id'] = usuario
        return {"ok": True, "nota": nota}
    finally:
        conn.close()

# --- Completar nota (con auto-chequeo) ---

def _completar_nota(nota_id: int, usuario: str, items_preparados: list = None) -> dict:
    conn = get_db()
    try:
        nota = dict(conn.execute("SELECT * FROM notas_entrega WHERE id = ?",
                                 (nota_id,)).fetchone() or {})
        if not nota:
            return {"ok": False, "error": "Nota no encontrada"}
        estado_actual = nota['estado']
        destino = 'chequeada' if int(nota.get('items_count', 0)) <= 2 else 'preparada'
        if not validar_transicion(estado_actual, destino):
            return {"ok": False, "error": f"No se puede completar desde estado '{estado_actual}'"}

        if items_preparados:
            for ip in items_preparados:
                item_id = ip.get('id')
                cant = float(ip.get('cantidad_preparada', 0))
                co_art = ip.get('co_art', '')
                if item_id:
                    conn.execute("""
                        UPDATE detalle_nota SET cantidad_preparada = ?, estado = 'preparado'
                        WHERE id = ? AND nota_id = ?
                    """, (cant, item_id, nota_id))
                if co_art and cant > 0:
                    _descontar_stock(co_art, cant)

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        total_items = conn.execute("SELECT COUNT(*) as c FROM detalle_nota WHERE nota_id = ?",
                                   (nota_id,)).fetchone()['c']
        auto_check = destino == 'chequeada'

        conn.execute("""
            UPDATE notas_entrega SET estado = ?, fecha_completada = ?,
                auto_chequeado = ?, items_count = ? WHERE id = ?
        """, (destino, now, 1 if auto_check else 0, total_items, nota_id))

        _registrar_movimiento(nota_id, '', '', 0, '', usuario,
                              destino, destino='auto_chequeo' if auto_check else 'pendiente_chequeo')
        conn.commit()
        return {"ok": True, "auto_chequeado": bool(auto_check), "nota_id": nota_id,
                "estado_destino": destino}
    finally:
        conn.close()

# --- Consulta de trazabilidad ---

def _consultar_trazabilidad(co_art: str = None, usuario: str = None,
                            fecha_inicio: str = None, fecha_fin: str = None,
                            estado_profit: str = None, limite: int = 50,
                            usuario_activo: str = None, es_admin: bool = False) -> list:
    conn = get_db()
    try:
        where = ["1=1"]
        params = []
        if co_art:
            where.append("co_art = ?")
            params.append(co_art)
        if not es_admin and usuario_activo:
            where.append("usuario = ?")
            params.append(usuario_activo)
        elif usuario and usuario != "Todos":
            where.append("usuario = ?")
            params.append(usuario)
        if fecha_inicio:
            where.append("date(timestamp) >= date(?)")
            params.append(fecha_inicio)
        if fecha_fin:
            where.append("date(timestamp) <= date(?)")
            params.append(fecha_fin)
        if estado_profit is not None and estado_profit != "":
            where.append("COALESCE(procesado_profit, 0) = ?")
            params.append(int(estado_profit))
        sql_where = "WHERE " + " AND ".join(where)
        rows = conn.execute(f"""
            SELECT *, COALESCE(procesado_profit, 0) as procesado_profit
            FROM movimientos_preparador {sql_where}
            ORDER BY timestamp DESC LIMIT ?
        """, params + [limite]).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# =============================================================================
# 4. ENDPOINTS FLASK
# =============================================================================

def register_notas_routes(app):

    @app.route('/api/notas/tomar', methods=['POST'])
    def notas_tomar():
        """Toma / bloquea una nota para preparación (control concurrencia)."""
        data = request.get_json(silent=True) or {}
        nota_id = data.get('nota_id')
        usuario = data.get('usuario', '').strip()
        if not nota_id or not usuario:
            return jsonify({"ok": False, "error": "nota_id y usuario son obligatorios"}), 400
        res = _tomar_nota(int(nota_id), usuario)
        code = 200 if res['ok'] else (409 if 'actualmente por' in res.get('error', '') else 404)
        return jsonify(res), code

    @app.route('/api/notas/completar', methods=['POST'])
    def notas_completar():
        """Completa preparación de nota. Si tiene 1-2 items, auto-chequeo."""
        data = request.get_json(silent=True) or {}
        nota_id = data.get('nota_id')
        usuario = data.get('usuario', '').strip()
        items   = data.get('items', [])
        if not nota_id or not usuario:
            return jsonify({"ok": False, "error": "nota_id y usuario son obligatorios"}), 400
        res = _completar_nota(int(nota_id), usuario, items)
        code = 200 if res['ok'] else 400
        return jsonify(res), code

    @app.route('/api/notas/pruebas', methods=['GET', 'POST'])
    def notas_pruebas():
        """Gestiona notas de prueba (es_prueba=1). GET lista, POST crea."""
        if request.method == 'GET':
            conn = get_db()
            try:
                rows = conn.execute("""
                    SELECT * FROM notas_entrega WHERE es_prueba = 1
                    ORDER BY fecha_creacion DESC LIMIT 50
                """).fetchall()
                return jsonify([dict(r) for r in rows])
            finally:
                conn.close()
        else:
            data = request.get_json(silent=True) or {}
            num = data.get('numero_nota', f"PRUEBA-{datetime.now().strftime('%Y%m%d%H%M%S')}")
            cli = data.get('cliente', 'CLIENTE PRUEBA')
            nota = _buscar_o_crear_nota(num, cli, es_prueba=True)
            items = data.get('items', [{"descripcion": "ARTÍCULO PRUEBA",
                                         "cantidad": 1, "unidad": "UND"}])
            _insertar_items(nota['id'], items)
            return jsonify({"status": "success", "nota": nota}), 201

    @app.route('/api/notas/detalle/<int:nota_id>', methods=['GET'])
    def notas_detalle(nota_id):
        """Obtiene encabezado + items de una nota."""
        conn = get_db()
        try:
            nota = conn.execute("SELECT * FROM notas_entrega WHERE id = ?",
                                (nota_id,)).fetchone()
            if not nota:
                return jsonify({"error": "Nota no encontrada"}), 404
            items = conn.execute("SELECT * FROM detalle_nota WHERE nota_id = ? ORDER BY id",
                                 (nota_id,)).fetchall()
            return jsonify({"nota": dict(nota), "items": [dict(r) for r in items]})
        finally:
            conn.close()

    @app.route('/api/notas/<int:nota_id>/transicion', methods=['POST'])
    def notas_transicion(nota_id):
        """Transiciona una nota a un estado válido (validado por State Machine)."""
        data = request.get_json(silent=True) or {}
        destino = data.get('estado', '').strip().lower()
        usuario = data.get('usuario', '').strip()
        if not destino or not usuario:
            return jsonify({"ok": False, "error": "estado y usuario requeridos"}), 400
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM notas_entrega WHERE id = ?",
                               (nota_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "Nota no encontrada"}), 404
            actual = dict(row)
            estado_actual = actual['estado']
            if not validar_transicion(estado_actual, destino):
                return jsonify({
                    "ok": False,
                    "error": f"Transición inválida: '{estado_actual}' → '{destino}'. "
                             f"Transiciones permitidas: {TRANSICIONES_VALIDAS.get(estado_actual, [])}"
                }), 400
            conn.execute("UPDATE notas_entrega SET estado = ? WHERE id = ?",
                         (destino, nota_id))
            _registrar_movimiento(nota_id, '', '', 0, '', usuario,
                                  f'transicion:{estado_actual}→{destino}')
            conn.commit()
            return jsonify({"ok": True, "estado_anterior": estado_actual,
                            "estado_actual": destino, "nota_id": nota_id})
        finally:
            conn.close()

    @app.route('/api/notas/<int:nota_id>/items', methods=['PATCH'])
    def notas_actualizar_items(nota_id):
        """Actualiza cantidad_preparada y estado de items (validando estado de nota)."""
        data = request.get_json(silent=True) or {}
        usuario = data.get('usuario', '').strip()
        items = data.get('items', [])
        if not usuario:
            return jsonify({"ok": False, "error": "usuario requerido"}), 400
        conn = get_db()
        try:
            nota = conn.execute("SELECT * FROM notas_entrega WHERE id = ?",
                                (nota_id,)).fetchone()
            if not nota:
                return jsonify({"ok": False, "error": "Nota no encontrada"}), 404
            nota_dict = dict(nota)
            if nota_dict['estado'] not in ('preparando',):
                return jsonify({"ok": False,
                                "error": f"Solo se pueden actualizar items en estado 'preparando' (actual: '{nota_dict['estado']}')"}), 400
            actualizados = 0
            for it in items:
                item_id = it.get('id')
                cant = float(it.get('cantidad_preparada', 0))
                co_art = it.get('co_art', '')
                if item_id:
                    conn.execute("""
                        UPDATE detalle_nota SET cantidad_preparada = ?, estado = 'preparado'
                        WHERE id = ? AND nota_id = ?
                    """, (cant, item_id, nota_id))
                    actualizados += 1
                if co_art and cant > 0:
                    _descontar_stock(co_art, cant)
            _registrar_movimiento(nota_id, '', '', 0, '', usuario,
                                  'actualizar_items', destino=f'{actualizados} items')
            conn.commit()
            return jsonify({"ok": True, "items_actualizados": actualizados})
        finally:
            conn.close()

    @app.route('/api/notas/lista', methods=['GET'])
    def notas_lista():
        """Lista notas reales (es_prueba=0) con filtro opcional de estado."""
        estado = request.args.get('estado')
        conn = get_db()
        try:
            if estado:
                rows = conn.execute("""
                    SELECT * FROM notas_entrega WHERE es_prueba = 0 AND estado = ?
                    ORDER BY fecha_creacion DESC LIMIT 50
                """, (estado,)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM notas_entrega WHERE es_prueba = 0
                    ORDER BY fecha_creacion DESC LIMIT 50
                """).fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    @app.route('/api/trazabilidad/movimientos', methods=['GET'])
    def trazabilidad_movimientos():
        """Consulta trazabilidad atómica. Filtros: co_art, usuario, fecha_inicio, fecha_fin, estado_profit, usuario_activo, es_admin."""
        co_art = request.args.get('co_art')
        usuario = request.args.get('usuario')
        fecha_inicio = request.args.get('fecha_inicio')
        fecha_fin = request.args.get('fecha_fin')
        estado_profit = request.args.get('estado_profit')
        usuario_activo = request.args.get('usuario_activo', '')
        es_admin = request.args.get('es_admin', 'false').lower() in ('true', '1', 'yes')
        rows = _consultar_trazabilidad(co_art, usuario, fecha_inicio, fecha_fin, estado_profit,
                                       usuario_activo=usuario_activo, es_admin=es_admin)
        return jsonify(rows)

    @app.route('/api/trazabilidad/marcar-procesado-profit', methods=['POST'])
    def marcar_procesado_profit():
        """Marca un movimiento como procesado en Profit Plus."""
        data = request.get_json(silent=True) or {}
        mov_id = data.get('mov_id')
        usuario_admin = data.get('usuario_admin')
        if not mov_id or not usuario_admin:
            return jsonify({"status": "error", "mensaje": "Faltan mov_id y/o usuario_admin"}), 400
        conn = get_db()
        try:
            conn.execute("""
                UPDATE movimientos_preparador
                SET procesado_profit = 1,
                    fec_procesado_profit = datetime('now', 'localtime'),
                    usuario_procesado_profit = ?
                WHERE id = ?
            """, (usuario_admin, mov_id))
            conn.commit()
            return jsonify({"status": "success", "mov_id": mov_id, "procesado_por": usuario_admin})
        except Exception as e:
            return jsonify({"status": "error", "mensaje": str(e)}), 500
        finally:
            conn.close()

    @app.route('/api/reportes/movimientos/pdf', methods=['GET', 'POST'])
    def reportes_movimientos_pdf():
        """Genera PDF de movimientos para un co_art (o descripción) en rango de fechas."""
        if request.method == 'POST':
            params = request.get_json(silent=True) or {}
        else:
            params = request.args

        co_art = params.get('co_art', '').strip()
        descripcion = params.get('descripcion', '').strip()
        fecha_inicio = params.get('fecha_inicio', '')
        fecha_fin = params.get('fecha_fin', '')

        if not co_art and not descripcion:
            return jsonify({"error": "Debe enviar co_art o descripcion"}), 400

        conn = get_db()
        try:
            if co_art:
                rows = conn.execute("""
                    SELECT * FROM movimientos_preparador
                    WHERE co_art = ? AND date(timestamp) BETWEEN date(?) AND date(?)
                    ORDER BY timestamp DESC
                """, (co_art, fecha_inicio or '2000-01-01', fecha_fin or '2099-12-31')).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM movimientos_preparador
                    WHERE descripcion LIKE ? AND date(timestamp) BETWEEN date(?) AND date(?)
                    ORDER BY timestamp DESC
                """, (f'%{descripcion}%', fecha_inicio or '2000-01-01', fecha_fin or '2099-12-31')).fetchall()
        finally:
            conn.close()

        if not rows:
            return jsonify({"error": "No se encontraron movimientos para los filtros dados"}), 404

        movs = [dict(r) for r in rows]

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=landscape(A4),
            rightMargin=1.5*cm, leftMargin=1.5*cm,
            topMargin=1.5*cm, bottomMargin=1.5*cm,
            title=f"Movimientos {co_art or descripcion}"
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'],
                                     fontSize=14, spaceAfter=16, alignment=TA_CENTER,
                                     textColor=colors.HexColor('#1a1a2e'))

        elements = []
        elements.append(Paragraph("REPORTE DE MOVIMIENTOS - SISTEMA ARA", title_style))
        elements.append(Paragraph(
            f"Artículo: {co_art or descripcion}  |  {fecha_inicio or '---'} al {fecha_fin or '---'}",
            styles['Normal']
        ))
        elements.append(Spacer(1, 12))

        headers = ['ID', 'Nota ID', 'Código', 'Descripción',
                   'Cant.', 'U/M', 'Usuario', 'Acción', 'Origen', 'Destino', 'Fecha']
        table_data = [headers]
        for m in movs:
            table_data.append([
                str(m['id']), str(m['nota_id']), str(m['co_art']),
                str(m['descripcion'])[:30], str(m['cantidad']),
                str(m['unidad_medida']), str(m['usuario']), str(m['accion']),
                str(m['origen'] or ''), str(m['destino'] or ''),
                str(m['timestamp'])[:19]
            ])
        col_widths = [1.2*cm, 1.5*cm, 2.5*cm, 5*cm, 1.5*cm, 1.5*cm, 3*cm, 2.5*cm, 2.5*cm, 2.5*cm, 3*cm]
        detail_table = Table(table_data, colWidths=col_widths, repeatRows=1)
        detail_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#16213e')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 7),
            ('BOTTOMPADDING', (0,0), (-1,0), 6),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(detail_table)
        doc.build(elements)
        buffer.seek(0)

        response = make_response(send_file(
            buffer, mimetype='application/pdf', as_attachment=False,
            download_name=f'movimientos_{co_art or "desconocido"}.pdf'
        ))
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = (
            f'inline; filename="movimientos_{co_art or "desconocido"}.pdf"'
        )
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response

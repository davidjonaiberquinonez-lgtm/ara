import sys
from pathlib import Path

# Obtener la raíz del proyecto (tres niveles arriba de ara_server.py → C:\ARA_PROYECT)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from flask import Flask, render_template, jsonify, request, send_from_directory, send_file
from flask_cors import CORS
import pandas as pd
import requests
import os
from datetime import datetime  
import json
from ara_vision import investigar_producto_ara, procesar_imagen_visor
import uuid
import sqlite3
import socket
from collections import defaultdict
import random
import string
import io
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import time

try:
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
    from reportlab.lib.units import inch
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    print("⚠️ reportlab no instalado. PDF no disponible. Instala: pip install reportlab")

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# -----------------------------------------------------------------------------
# Registro del módulo PDF (delegado a pdf_route.py para evitar colisión de rutas)
# DEBE ir justo después de CORS(app) para que la ruta /api/reporte/pdf
# (GET y POST) quede registrada antes de cualquier endpoint que pudiera
# hacer shadowing.
# -----------------------------------------------------------------------------
from pdf_route import register_pdf_route
register_pdf_route(app)

# -----------------------------------------------------------------------------
# Registro del módulo de BANDEJA DE MENSAJERÍA (chat_routes.py)
# Endpoints bajo /api/chat/* (conversaciones, historial, enviar, webhook, poll)
# -----------------------------------------------------------------------------
from chat_routes import register_chat_routes, init_chat_tables
init_chat_tables()              # crea tablas contactos/conversaciones/mensajes si faltan
register_chat_routes(app)

# -----------------------------------------------------------------------------
# Inicializar tabla de feedback de IA (ara_brain)
# -----------------------------------------------------------------------------
try:
    from ara_brain import init_ia_feedback_table
    init_ia_feedback_table()
    print("📝 Tabla log_ia_feedback inicializada.")
except ImportError:
    print("⚠️ ara_brain.init_ia_feedback_table no disponible")

# -----------------------------------------------------------------------------
# Registro del módulo HEXAGONAL DE NOTAS (notas_hexagonal.py)
# Endpoints: /api/notas/*, /api/trazabilidad/*, /api/reportes/movimientos/pdf
# -----------------------------------------------------------------------------
from notas_hexagonal import register_notas_routes, init_notas_tables
init_notas_tables()             # crea tablas notas_entrega / detalle_nota / movimientos_preparador
register_notas_routes(app)

# -----------------------------------------------------------------------------
# Registro del módulo OCR DE NOTAS (preparacion/ocr_notas/) — Key Pool NVIDIA NIM
# Reemplaza el endpoint legacy /api/vision/escanear_nota de notas_hexagonal.py
# -----------------------------------------------------------------------------
from preparacion.ocr_notas import (
    register_ocr_notas_routes,
    OcrNotasService,
    NvidiaVisionProvider,
    OllamaVisionProvider,
    SqliteNotaRepository,
)
_nvidia_provider = NvidiaVisionProvider(timeout=30)
_ollama_provider = OllamaVisionProvider(timeout=30)
_sqlite_repo = SqliteNotaRepository()
_ocr_service = OcrNotasService(_nvidia_provider, _ollama_provider, _sqlite_repo)
register_ocr_notas_routes(app, _ocr_service)

@app.after_request
def monitorear_trafico(response):
    print(f"➔ {request.method} {request.path} | Código Estado: {response.status_code}")
    return response

# =============================================================================
# 1. CONFIGURACIÓN DE RUTAS Y ARCHIVOS (SISTEMA DINÁMICO)
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(BASE_DIR, 'data')
BRAIN_FOLDER = os.path.join(BASE_DIR, 'brain_knowledge')
REPORTES_FOLDER = os.path.join(BRAIN_FOLDER, 'reportes')
LOC_REPORTES_FOLDER = os.path.join(BRAIN_FOLDER, 'reportes_ubicacion')
MEDIA_FOLDER = os.path.join(BASE_DIR, 'media')
# ✅ CORRECCIÓN 1: Definición explícita de la carpeta de chats para evitar NameError
CHATS_FOLDER = os.path.join(BRAIN_FOLDER, 'chats')
HISTORY_FILE = os.path.join('brain_knowledge', 'history.json')

os.makedirs(REPORTES_FOLDER, exist_ok=True)
os.makedirs(LOC_REPORTES_FOLDER, exist_ok=True)
os.makedirs(MEDIA_FOLDER, exist_ok=True)
os.makedirs(CHATS_FOLDER, exist_ok=True)

DB_PATH = os.path.join(DATA_FOLDER, 'proyecto_ara.db')

# =============================================================================
# TABLA DE GAMIFICACIÓN: log_puntos
# =============================================================================
def init_log_puntos_table():
    """Crea la tabla de auditoría de puntos si no existe."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS log_puntos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL,
            modulo TEXT NOT NULL,           -- 'picking', 'chequeo', 'inventario'
            referencia_id TEXT NOT NULL,    -- factura_id, nota_id, etc.
            cantidad_renglones INTEGER NOT NULL,
            puntos_ganados REAL NOT NULL,
            fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_log_puntos_usuario_fecha ON log_puntos(usuario, fecha_registro)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_log_puntos_fecha_usuario ON log_puntos(fecha_registro, usuario)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_log_puntos_modulo ON log_puntos(modulo)')
    conn.commit()
    conn.close()

init_log_puntos_table()

# Mapeos de compatibilidad heredada
FILE_ASIGNACIONES = os.path.join(DATA_FOLDER, 'INVENTARIO.xlsx') 
FILE_FACTURAS = os.path.join(DATA_FOLDER, 'factura_202604221922.csv')

# Función de conexión centralizada a SQL
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

# =============================================================================
# MIGRACIÓN RBAC: permisos por defecto para usuarios existentes
# =============================================================================
def migrar_permisos_usuarios():
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        # Verificar/agregar columna permisos si no existe
        cursor.execute("PRAGMA table_info(usuarios)")
        columnas = [col['name'] for col in cursor.fetchall()]
        if 'permisos' not in columnas:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN permisos TEXT")
            conn.commit()

        # Asignar permisos por defecto si están nulos
        cursor.execute("SELECT id, rol, permisos FROM usuarios")
        usuarios = cursor.fetchall()
        for usr in usuarios:
            if not usr['permisos']:
                permisos_default = '["*"]' if usr['rol'] == 'admin' else '["visor", "preparacion_notas", "notas_pruebas", "trazabilidad"]'
                cursor.execute("UPDATE usuarios SET permisos = ? WHERE id = ?", (permisos_default, usr['id']))
        conn.commit()
        print(f"[RBAC] Permisos migrados para {len(usuarios)} usuarios.")
    finally:
        conn.close()

migrar_permisos_usuarios()

# =============================================================================
# SISTEMA DE SEGURIDAD UNIFICADO (Sincronización Total)
# =============================================================================
USUARIOS_FILE = os.path.join(BRAIN_FOLDER, 'usuarios.json')

def cargar_usuarios_db():
    if os.path.exists(USUARIOS_FILE):
        try:
            with open(USUARIOS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def guardar_usuarios_db(usuarios):
    with open(USUARIOS_FILE, 'w', encoding='utf-8') as f:
        json.dump(usuarios, f, indent=4, ensure_ascii=False)

db_usuarios = cargar_usuarios_db()

def validar_usuario(identificador, password):
    if not identificador or not password:
        return False
    identificador = str(identificador).strip().upper()
    password = str(password).strip()

    try:
        # Abrimos conexión directa al motor SQL
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Buscamos de forma flexible: ya sea por su ID único o por su Nombre completo
        cursor.execute("""
            SELECT contrasena FROM usuarios 
            WHERE UPPER(id) = ? OR UPPER(nombre) = ?
        """, (identificador, identificador))
        
        user_row = cursor.fetchone()
        conn.close()

        # Si el usuario existe, comparamos la contraseña de la base de datos
        if user_row:
            if str(user_row['contrasena']) == password:
                return True
                
        return False
    except Exception as e:
        print(f"💥 Error crítico al validar credenciales en SQL: {str(e)}")
        return False
    
def consultar_sqlite_maestro(codigo_articulo):
    """Busca un producto en la base de datos SQLite usando su código"""
    try:
        # 🔥 NOTA: Cambia 'inventario.db' por el nombre real de tu archivo .db si es distinto
        ruta_db = os.path.join(DATA_FOLDER, 'proyecto_ara.db') 
        
        if not os.path.exists(ruta_db):
            print(f"⚠️ Alerta: No se encuentra el archivo de base de datos en: {ruta_db}")
            return None

        conn = sqlite3.connect(ruta_db)
        conn.row_factory = sqlite3.Row  # Para acceder a los campos por nombre
        cursor = conn.cursor()
        
        # 🔥 NOTA: Asegúrate de que tu tabla se llame 'productos' y tenga estas columnas
        # (Si tus columnas se llaman 'existencia' o 'cantidad' en vez de 'stock', cámbialo aquí)
        cursor.execute("""
            SELECT descripcion, stock_maestro, campo7 AS ubicacion 
            FROM stock_maestro 
            WHERE UPPER(trim(codigo)) = ?
        """, (codigo_articulo.strip().upper(),))
        
        resultado = cursor.fetchone()
        conn.close()
        return resultado
    except Exception as e:
        print(f"❌ Error al consultar SQLite: {str(e)}")
        return None

@app.route('/api/usuarios/guardar', methods=['POST'])
def guardar_usuario_servidor():
    try:
        data = request.json
        uid = data.get('id', '').strip().upper()
        if not uid: 
            return jsonify({"status": "error", "mensaje": "ID obligatorio"}), 400
        
        # Mapeamos las variables que vienen del frontend a las columnas de SQL
        nombre = data.get('nombre', '')
        contrasena = data.get('pass', '') # El formulario envía 'pass'
        rol = data.get('rol', 'surtidor')
        permisos = json.dumps(data.get('permisos', [])) # Guardamos la lista como texto JSON en SQL
        color = data.get('color', '#3b82f6')
        is_route_responsible = 1 if data.get('isRouteResponsible') else 0

        conn = get_db_connection()
        cursor = conn.cursor()
        
        # INSERT OR REPLACE evita duplicados; si el ID existe, lo actualiza en caliente sin romper nada
        cursor.execute("""
            INSERT OR REPLACE INTO usuarios (id, nombre, contrasena, rol, permisos, color, is_route_responsible)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (uid, nombre, contrasena, rol, permisos, color, is_route_responsible))
        
        conn.commit()
        conn.close()
        
        return jsonify({"status": "success", "mensaje": "Usuario guardado y sincronizado en SQL con éxito"})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": f"Error en base de datos: {str(e)}"}), 500


@app.route('/api/usuarios/get_all', methods=['GET'])
def obtener_usuarios():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM usuarios")
        rows = cursor.fetchall()
        conn.close()
        
        lista_usuarios = []
        for row in rows:
            try:
                permisos_list = json.loads(row['permisos']) if row['permisos'] else []
            except:
                permisos_list = []
            
            # Reconstruimos el formato que tu frontend ya conoce para que no se rompa nada arriba
            lista_usuarios.append({
                "id": row['id'],
                "nombre": row['nombre'],
                "pass": row['contrasena'],
                "rol": row['rol'],
                "permisos": permisos_list,
                "color": row['color'],
                "isRouteResponsible": bool(row['is_route_responsible'])
            })
            
        return jsonify({"status": "success", "usuarios": lista_usuarios})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": f"Error al leer usuarios: {str(e)}"}), 500


@app.route('/api/usuarios/actualizar_permisos', methods=['POST'])
def actualizar_permisos_usuario():
    """Actualiza solo los permisos de un usuario existente."""
    try:
        data = request.json
        uid = data.get('id', '').strip().upper()
        nuevos_permisos = data.get('permisos', [])
        conn = get_db_connection()
        conn.execute("UPDATE usuarios SET permisos = ? WHERE id = ?",
                     (json.dumps(nuevos_permisos), uid))
        conn.commit()
        conn.close()
        print(f"[RBAC] Permisos actualizados para {uid}: {nuevos_permisos}")
        return jsonify({"status": "success", "mensaje": "Permisos actualizados"})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500
@app.route('/api/login', methods=['POST'])
def login():
    try:
        print("\n=== 🔍 INTENTO DE INICIO DE SESIÓN ===")
        data = request.json
        uid = data.get('id', '').strip().upper()
        password = data.get('pass', '').strip()

        if not os.path.exists(DB_PATH):
            return jsonify({"status": "error", "mensaje": "Base de datos no encontrada"}), 500

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM usuarios WHERE UPPER(id) = ?", (uid,))
        user_row = cursor.fetchone()
        conn.close()

        if user_row:
            if str(user_row['contrasena']) == str(password):
                try:
                    permisos_list = json.loads(user_row['permisos']) if user_row['permisos'] else []
                except:
                    permisos_list = []

                return jsonify({
                    "status": "success", 
                    "user": {
                        "id": user_row['id'],
                        "nombre": user_row['nombre'],
                        "rol": user_row['rol'],
                        "permisos": permisos_list,
                        "color": user_row['color'],
                        "isRouteResponsible": bool(user_row['is_route_responsible'])
                    }
                })
        return jsonify({"status": "error", "mensaje": "ID o Contraseña incorrectos"}), 401
    except Exception as e:
        print(f"💥 ERROR CRÍTICO EN LOGUEO: {str(e)}")
        return jsonify({"status": "error", "mensaje": f"Error interno: {str(e)}"}), 500

# =============================================================================
# 2. CARGA DE DATOS COMPATIBILIDAD (Solo para facturas y asignaciones fijas)
# =============================================================================
def cargar_db():
    try:
        df_fac = pd.read_csv(FILE_FACTURAS, sep=None, engine='python').fillna('')
        df_asig = pd.read_excel(FILE_ASIGNACIONES, engine='openpyxl').fillna('')
        print("✅ Bases de datos operacionales de soporte cargadas.")
        return df_fac, df_asig
    except Exception as e:
        print(f"❌ Error crítico cargando archivos estáticos: {e}")
        return pd.DataFrame(), pd.DataFrame()

df_facturas, df_asignacion = cargar_db()
sesiones_inventario = {}

# =============================================================================
# 3. FUNCIONES DE SOPORTE Y MEMORIA
# =============================================================================
def guardar_en_memoria(usuario, pregunta, respuesta):
    try:
        log_path = os.path.join(BRAIN_FOLDER, 'history.json')
        nueva_data = {"usuario": usuario, "p": pregunta, "r": respuesta}
        historial = []
        if os.path.exists(log_path):
            with open(log_path, 'r', encoding='utf-8') as f:
                try: historial = json.load(f)
                except: historial = []
        historial.append(nueva_data)
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(historial[-100:], f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Error guardando en memoria: {e}")

def obtener_memoria_reciente():
    log_path = os.path.join(BRAIN_FOLDER, 'history.json')
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if not data: return "No hay eventos recientes registrados."
                recientes = data[-10:] 
                memoria_texto = "\n--- BITÁCORA DE EVENTOS RECIENTES ---\n"
                for entrada in recientes:
                    memoria_texto += f"- Usuario {entrada.get('usuario', 'Operador')}: {entrada.get('r', '')}\n"
                return memoria_texto
        except: return "Error al recuperar memoria."
    return "No hay memoria disponible."

STATUS_FILE = os.path.join(BRAIN_FOLDER, 'notas_estado.json')

def cargar_estados():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def guardar_estado(factura_id, estado):
    estados = cargar_estados()
    estados[str(factura_id)] = estado
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(estados, f, indent=4)

# =============================================================================
# HELPER DE GAMIFICACIÓN: registrar_puntos
# =============================================================================
PUNTOS_POR_MODULO = {
    'picking': 1.00,    # Preparación: 1.00 punto por renglón
    'chequeo': 1.00,    # Chequeo: 1.00 punto por renglón verificado
    'inventario': 0.25  # Inventario: 0.25 puntos por renglón contado
}

def registrar_puntos(usuario, modulo, referencia_id, cantidad_renglones):
    """
    Calcula puntos según reglas de negocio e inserta en log_puntos.
    Retorna los puntos ganados.
    """
    if cantidad_renglones <= 0:
        return 0.0
    
    factor = PUNTOS_POR_MODULO.get(modulo, 0)
    puntos = round(cantidad_renglones * factor, 2)
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO log_puntos (usuario, modulo, referencia_id, cantidad_renglones, puntos_ganados)
            VALUES (?, ?, ?, ?, ?)
        ''', (usuario, modulo, str(referencia_id), cantidad_renglones, puntos))
        conn.commit()
        conn.close()
        print(f"🏆 [PUNTOS] {usuario} +{puntos} pts ({modulo}: {cantidad_renglones} renglones) -> Ref: {referencia_id}")
        return puntos
    except Exception as e:
        print(f"❌ Error registrando puntos: {e}")
        return 0.0

#====================================================================
# Dasboard Profesional de Desempeño en tiempo real .
#======================================================================
# Caché TTL para Dashboard (30s) — evita consultas repetidas sin datos frescos
_dashboard_cache = {"data": None, "timestamp": 0}

@app.route('/api/dashboard/stats', methods=['GET'])
def obtener_estadisticas_dashboard():
    """
    Consulta la tabla log_puntos para generar métricas reales de gamificación.
    Acepta filtros opcionales: fecha_inicio, fecha_fin (YYYY-MM-DD)
    Incluye caché TTL de 30s para evitar consultas repetitivas.
    """
    ahora = time.time()
    if _dashboard_cache["data"] and (ahora - _dashboard_cache["timestamp"] < 30):
        return jsonify(_dashboard_cache["data"])
    try:
        # Leer filtros de fecha
        fecha_inicio = request.args.get('fecha_inicio')
        fecha_fin = request.args.get('fecha_fin')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Construir WHERE para filtros de fecha
        where_fecha = ""
        params = []
        if fecha_inicio and fecha_fin:
            where_fecha = "WHERE DATE(fecha_registro) BETWEEN ? AND ?"
            params = [fecha_inicio, fecha_fin]
        elif fecha_inicio:
            where_fecha = "WHERE DATE(fecha_registro) >= ?"
            params = [fecha_inicio]
        elif fecha_fin:
            where_fecha = "WHERE DATE(fecha_registro) <= ?"
            params = [fecha_fin]
        
        # 1. Resumen global
        cursor.execute(f'''
            SELECT 
                COALESCE(COUNT(*), 0) as total_operaciones,
                COALESCE(SUM(puntos_ganados), 0) as total_puntos,
                COUNT(DISTINCT usuario) as total_usuarios
            FROM log_puntos
            {where_fecha}
        ''', params)
        resumen_row = cursor.fetchone()
        total_ops = resumen_row['total_operaciones'] or 0
        total_puntos = resumen_row['total_puntos'] or 0
        
        # 2. Ranking por usuario (agregado desde log_puntos) - CON DESGLOSE POR MÓDULO
        cursor.execute(f'''
            SELECT 
                usuario,
                COUNT(*) as operaciones,
                COALESCE(SUM(puntos_ganados), 0) as puntos_totales,
                COALESCE(SUM(cantidad_renglones), 0) as total_renglones,
                COALESCE(SUM(CASE WHEN modulo = 'picking' THEN puntos_ganados ELSE 0 END), 0) as puntos_picking,
                COALESCE(SUM(CASE WHEN modulo = 'picking' THEN cantidad_renglones ELSE 0 END), 0) as renglones_picking,
                COALESCE(SUM(CASE WHEN modulo = 'chequeo' THEN puntos_ganados ELSE 0 END), 0) as puntos_chequeo,
                COALESCE(SUM(CASE WHEN modulo = 'chequeo' THEN cantidad_renglones ELSE 0 END), 0) as renglones_chequeo,
                COALESCE(SUM(CASE WHEN modulo = 'inventario' THEN puntos_ganados ELSE 0 END), 0) as puntos_inventario,
                COALESCE(SUM(CASE WHEN modulo = 'inventario' THEN cantidad_renglones ELSE 0 END), 0) as renglones_inventario
            FROM log_puntos
            {where_fecha}
            GROUP BY usuario
            ORDER BY puntos_totales DESC
        ''', params)
        ranking_rows = cursor.fetchall()
        
        ranking_final = []
        for row in ranking_rows:
            ranking_final.append({
                "nombre": row['usuario'],
                "rol": "Operador",
                "operaciones": row['operaciones'],
                "precision": 100.0,
                "puntos": round(row['puntos_totales'], 2),
                "renglones_procesados": row['total_renglones'],
                "puntos_picking": round(row['puntos_picking'], 2),
                "renglones_picking": row['renglones_picking'],
                "puntos_chequeo": round(row['puntos_chequeo'], 2),
                "renglones_chequeo": row['renglones_chequeo'],
                "puntos_inventario": round(row['puntos_inventario'], 2),
                "renglones_inventario": row['renglones_inventario']
            })
        
        operador_destacado = ranking_final[0]["nombre"] if ranking_final else "N/A"
        
        # 3. Gráfico semanal (últimos 6 días con operaciones)
        where_grafico = "WHERE fecha_registro >= DATE('now', '-6 days')"
        params_grafico = []
        if where_fecha:
            where_grafico += " " + where_fecha.replace("WHERE", "AND")
            params_grafico = params
        
        cursor.execute(f'''
            SELECT 
                DATE(fecha_registro) as fecha,
                COUNT(*) as operaciones,
                SUM(puntos_ganados) as puntos_dia
            FROM log_puntos
            {where_grafico}
            GROUP BY DATE(fecha_registro)
            ORDER BY fecha DESC
        ''', params_grafico)
        grafico_rows = cursor.fetchall()
        
        grafico_fechas = []
        grafico_operaciones = []
        for row in reversed(grafico_rows):
            try:
                partes = row['fecha'].split('-')
                formato_corto = f"{partes[2]}/{partes[1]}"
            except:
                formato_corto = row['fecha']
            grafico_fechas.append(formato_corto)
            grafico_operaciones.append(row['operaciones'])
        
        if not grafico_fechas:
            grafico_fechas = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"]
            grafico_operaciones = [0, 0, 0, 0, 0, 0]
        
        # 4. Incidencias recientes (últimas 10)
        cursor.execute(f'''
            SELECT usuario, modulo, referencia_id, puntos_ganados, fecha_registro
            FROM log_puntos
            {where_fecha}
            ORDER BY fecha_registro DESC
            LIMIT 10
        ''', params)
        incidencias_rows = cursor.fetchall()
        
        incidencias_registradas = []
        for row in incidencias_rows:
            incidencias_registradas.append({
                "usuario": row['usuario'],
                "rol": "Operador",
                "fecha": row['fecha_registro'],
                "estado": "exitoso" if row['puntos_ganados'] > 0 else "sin_puntos",
                "detalles": f"{row['modulo']} - Ref: {row['referencia_id']}",
                "puntos": row['puntos_ganados']
            })
        
        conn.close()

        resultado_stats = {
            "resumen": {
                "totalOperaciones": total_ops,
                "tasaPrecision": 100.0,
                "tiempoPromedio": "4m 12s",
                "operadorDestacado": operador_destacado
            },
            "ranking": ranking_final,
            "graficoFechas": grafico_fechas,
            "graficoOperaciones": grafico_operaciones,
            "incidencias": incidencias_registradas
        }
        _dashboard_cache["data"] = resultado_stats
        _dashboard_cache["timestamp"] = ahora
        return jsonify(resultado_stats)
    except Exception as e:
        print(f"❌ Error en dashboard stats: {e}")
        return jsonify({
            "resumen": {"totalOperaciones": 0, "tasaPrecision": 100.0, "tiempoPromedio": "0m 0s", "operadorDestacado": "N/A"},
            "ranking": [],
            "graficoFechas": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"],
            "graficoOperaciones": [0, 0, 0, 0, 0, 0],
            "incidencias": []
        }), 500

def registrar_operacion_historial(usuario, rol, tipo_accion, documento, estado, detalles, 
                                  items_diferentes=0, items_contados=0, 
                                  cajas=0, facturas=0, notas_credito=0):
    """
    Registra una operación en history.json calculando los puntos con las reglas reales del almacén.
    
    tipo_accion: 'surtido', 'chequeo', 'embalaje', 'traslado', 'inventario', 'ruta'
    estado: 'exitoso', 'leve', 'critico'
    """
    puntos = 0.0

    # 1. Aplicar la matemática exacta que me pasaste si la acción fue exitosa
    if estado == 'exitoso':
        # Surtido, Chequeo y Embalaje normal dependen de ítems diferentes (asignamos 5 pts por línea)
        if tipo_accion in ['surtido', 'chequeo', 'embalaje']:
            puntos = items_diferentes * 5.0
            
        # Surtido de Traslados: 5 puntos por cada ítem diferente
        elif tipo_accion == 'traslado':
            puntos = items_diferentes * 5.0
            
        # Inventario: 0.10 puntos por cada ítem/unidad contado
        elif tipo_accion == 'inventario':
            puntos = items_contados * 0.10
            
        # Ruta / Despacho: 1 pt por caja + 1 pt por factura + 1 pt por nota de crédito
        elif tipo_accion == 'ruta':
            puntos = (cajas * 1.0) + (facturas * 1.0) + (notas_credito * 1.0)

    # 2. Gestión de penalizaciones por errores operativos
    elif estado == 'leve':
        puntos = -50.0   # Incidencia menor
    elif estado == 'critico':
        puntos = -500.0  # Macana grave (Ej: Medicamento equivocado o pedido bajo la mesa)

    # Redondear a 2 decimales por si el conteo de inventario da decimales largos
    puntos = round(puntos, 2)

    # 3. Estructurar el objeto con toda la metadata para el Dashboard
    nueva_operacion = {
        "id_operacion": f"OP-{documento}-{int(datetime.now().timestamp())}",
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "usuario": usuario,
        "rol": rol,
        "tipo_accion": tipo_accion,
        "documento": documento,
        "estado": estado,
        "detalles": detalles,
        "puntos": puntos,
        "metricas": {
            "items_diferentes": items_diferentes,
            "items_contados": items_contados,
            "cajas": cajas,
            "facturas": facturas,
            "notas_credito": notas_credito
        }
    }

    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)

        historial = []
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                try:
                    historial = json.load(f)
                    if not isinstance(historial, list): historial = []
                except json.JSONDecodeError:
                    historial = []

        historial.append(nueva_operacion)

        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(historial, f, indent=4, ensure_ascii=False)
            
        print(f"✅ [ARA IA] Operación registrada para {usuario}: {puntos} Pts en {tipo_accion}.")
        return True

    except Exception as e:
        print(f"💥 Error al guardar historial: {str(e)}")
        return False

# =============================================================================
# 4. MÓDULO DE INVENTARIO OPTIMIZADO CON SQL (REMAIPADO A STOCK_MAESTRO)
# =============================================================================

@app.route('/api/stock/<codigo>', methods=['GET'])
def obtener_producto(codigo):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stock_maestro WHERE codigo = ? OR codigo_barra = ?", (codigo, codigo))
        producto = cursor.fetchone()
        conn.close()
        
        if producto:
            return jsonify({
                "status": "success", 
                "data": dict(producto)
            })
            
        return jsonify({"status": "error", "mensaje": "Producto no localizado en el Stock Maestro SQL"}), 404
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@app.route('/api/inventario/registrar', methods=['POST'])
def registrar_item_inventario():
    try:
        data = request.json
        codigo = data.get('codigo')
        detalle = data.get('detalle')
        amount = data.get('cantidad')
        usuario = data.get('usuario', 'Operador ARA')

        if not codigo or amount is None:
            return jsonify({"status": "error", "mensaje": "Datos del producto o cantidad incompletos"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO inventario_progreso (codigo, detalle, cantidad, usuario)
            VALUES (?, ?, ?, ?)
        ''', (codigo, detalle, int(amount), usuario))
        conn.commit()
        conn.close()

        return jsonify({"status": "success", "mensaje": "Item asentado en base de datos correctamente"})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@app.route('/api/inventario/progreso', methods=['GET'])
def ver_progreso_inventario():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM inventario_progreso ORDER BY fecha DESC")
        filas = cursor.fetchall()
        conn.close()
        return jsonify({"status": "success", "datos": [dict(fila) for fila in filas]})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@app.route('/api/inventario/asignacion/<nombre_usuario>', methods=['GET'])
def obtener_asignacion_usuario(nombre_usuario):
    try:
        col_responsable = next((c for c in df_asignacion.columns if 'respons' in c.lower() or 'repons' in c.lower()), None)
        col_estante = next((c for c in df_asignacion.columns if any(x in c.lower() for x in ['campo7', 'ubic', 'estante', 'loc'])), None)
        
        if not col_responsable or not col_estante:
            return jsonify({"status": "error", "mensaje": "Columnas de control ausentes en excel"}), 500

        df_filtrado = df_asignacion[df_asignacion[col_responsable].astype(str).str.strip().str.upper() == nombre_usuario.upper()]
        if df_filtrado.empty:
            return jsonify({"status": "error", "mensaje": f"El usuario {nombre_usuario} no tiene estantes asignados."}), 404
        
        estantes = df_filtrado[col_estante].unique().tolist()
        return jsonify({
            "status": "success",
            "usuario": nombre_usuario,
            "estantes_asignados": sorted(map(str, estantes)),
            "total_productos": len(df_filtrado)
        })
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500

#============================================================
# .importante recien agregado 

#============================================================

# Lista de prioridad para ordenar y validar tus zonas
ZONAS_PREFIJOS = ["MQ", "JBE", "CR", "PERF", "MD", "MISC", "RACK2"]

@app.route('/api/inventario/comenzar', methods=['GET'])
def comenzar_inventario():
    try:
        nombre_usuario = request.args.get('usuario')
        estante = request.args.get('estante', '').strip().upper()

        if not nombre_usuario or not estante:
            return jsonify({"status": "error", "mensaje": "Faltan datos de usuario o estante"}), 400

        col_responsable = next((c for c in df_asignacion.columns if 'respons' in c.lower() or 'repons' in c.lower()), None)
        col_est = next((c for c in df_asignacion.columns if any(x in c.lower() for x in ['campo7', 'ubic', 'estante', 'loc'])), None)
        
        # 🗺️ FILTRADO FLEXIBLE: Permite buscar coincidencia exacta o que empiece por el prefijo
        df_usuario_estante = df_asignacion[
            (df_asignacion[col_responsable].astype(str).str.strip().str.upper() == nombre_usuario.upper()) & 
            ((df_asignacion[col_est].astype(str).str.strip().str.upper() == estante) | 
             (df_asignacion[col_est].astype(str).str.strip().str.upper().str.startswith(estante)))
        ]
        
        if df_usuario_estante.empty:
            return jsonify({"status": "error", "mensaje": f"No tienes asignado el estante o zona '{estante}' en el plan de trabajo."}), 404

        lista_inventario_final = []

        conn = get_db_connection()
        cursor = conn.cursor()

        # 1️⃣ Buscamos en vivo en la base de datos real los productos del estante
        cursor.execute("SELECT codigo, descripcion, stock_maestro FROM stock_maestro WHERE campo7 = ?", (estante,))
        productos_en_vivo = cursor.fetchall()

        if not productos_en_vivo:
            conn.close()
            return jsonify({
                "status": "error", 
                "mensaje": f"El estante {estante} está asignado, pero no tiene productos en la base de datos real."
            }), 404

        for prod_row in productos_en_vivo:
            codigo_prod = str(prod_row['codigo']).strip()
            stock_real = int(prod_row['stock_maestro']) if prod_row['stock_maestro'] is not None else 0
            descripcion = str(prod_row['descripcion']) if prod_row['descripcion'] else 'Sin descripción'

            lista_inventario_final.append({
                "codigo": codigo_prod,
                "descripcion": descripcion,
                "estante": estante,
                "stock_teorico": stock_real
            })

        # =====================================================================
        # 🚀 2️⃣ NUEVA LÓGICA: BUSCAR ARTÍCULOS CON MOVIMIENTO DE HOY
        # =====================================================================
        # NOTA: Cambia 'movimientos' por el nombre real de tu tabla si varía (ej. 'ventas_diarias')
        # Filtramos por campo7 (estante) y por la fecha del día actual usando funciones de SQLite
        articulos_movimiento = []
        try:
            cursor.execute("""
                SELECT m.codigo, m.tipo, m.cantidad, sm.descripcion
                FROM movimientos m
                JOIN stock_maestro sm ON m.codigo = sm.codigo
                WHERE sm.campo7 = ? AND DATE(m.fecha) = DATE('now', 'localtime')
            """, (estante,))
            movimientos_en_vivo = cursor.fetchall()
            
            for m_row in movimientos_en_vivo:
                articulos_movimiento.append({
                    "codigo": str(m_row['codigo']).strip(),
                    "descripcion": str(m_row['descripcion']) if m_row['descripcion'] else 'Sin descripción',
                    "tipo": str(m_row['tipo']).strip().upper(),  # 'VENTA' o 'SURTIDO'
                    "cantidad": int(m_row['cantidad'])
                })
        except Exception as e_mov:
            # Ponemos un sub-try-except estratégico para que si la tabla de movimientos no existe
            # o cambia de nombre, la app no se caiga y permita al usuario seguir contando.
            print(f"⚠️ Alerta en tabla movimientos: {str(e_mov)}")
            articulos_movimiento = []
        # =====================================================================

        conn.close()
        
        # Ordenamos la lista general por código
        lista_ordenada = sorted(lista_inventario_final, key=lambda x: x['codigo'])
        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # =====================================================================
        # 🚨 PROCESAMIENTO DE TARJETA DE ALERTAS CRÍTICAS (STOCK 0 A 10)
        # =====================================================================
        articulos_criticos = [p for p in lista_ordenada if 0 <= p['stock_teorico'] <= 10]
        articulos_criticos_ordenados = sorted(articulos_criticos, key=lambda x: (x['stock_teorico'], x['codigo']))
        
        tarjeta_alerta = {
            "mostrar_tarjeta": len(articulos_criticos_ordenados) > 0,
            "total_criticos": len(articulos_criticos_ordenados),
            "productos": articulos_criticos_ordenados
        }
        # =====================================================================

        # =====================================================================
        # 🔄 ESTRUCTURA DE LA TARJETA DE MOVIMIENTOS RECIENTES
        # =====================================================================
        tarjeta_movimientos = {
            "mostrar_tarjeta": len(articulos_movimiento) > 0,
            "total_movimientos": len(articulos_movimiento),
            "productos": articulos_movimiento
        }
        # =====================================================================

        sesiones_inventario[nombre_usuario] = {
            "estante_actual": estante,
            "fecha_inicio": fecha_actual,
            "productos_en_estante": len(lista_ordenada),
            "verificados": 0,             
            "novedades": []               
        }

        # Retornamos todo integrado al Front-End
        return jsonify({
            "status": "success", 
            "usuario": nombre_usuario, 
            "estante": estante, 
            "lista": lista_ordenada, 
            "total": len(lista_ordenada),
            "tarjeta_criticos": tarjeta_alerta,
            "tarjeta_movimientos": tarjeta_movimientos  # 🔥 ¡Cruza perfecto con tu JavaScript!
        })
    except Exception as e:
        return jsonify({"status": "error", "mensaje": f"Error interno: {str(e)}"}), 500

@app.route('/api/inventario/verificar', methods=['POST'])
def verificar_producto():
    try:
        data = request.json
        usuario = data.get('usuario')
        codigo_prod = str(data.get('codigo')).strip()
        cantidad_contada = int(data.get('cantidad', 0))

        if usuario not in sesiones_inventario:
            return jsonify({"status": "error", "mensaje": "No hay una sesión activa."}), 400

        conn = get_db_connection()
        conn.row_factory = sqlite3.Row  # Asegura acceso por nombre de columna si no estaba global
        cursor = conn.cursor()
        cursor.execute("SELECT descripcion, stock_maestro FROM stock_maestro WHERE codigo = ? OR codigo_barra = ?", (codigo_prod, codigo_prod))
        prod_row = cursor.fetchone()
        conn.close()
        
        if not prod_row:
            return jsonify({"status": "error", "mensaje": "Producto no encontrado en base de datos SQL"}), 404

        stock_teorico = int(prod_row['stock_maestro'])
        descripcion = str(prod_row['descripcion']) if prod_row['descripcion'] else 'Sin descripción'
        ubicacion = sesiones_inventario[usuario].get("estante_actual", "N/A")

        diferencia = cantidad_contada - stock_teorico
        estado = "OK" if diferencia == 0 else "NOVEDAD"
        
        if diferencia == 0:
            mensaje_ara = f"✅ {codigo_prod}: Verificado al pelo."
        elif diferencia < 0:
            mensaje_ara = f"❌ NOVEDAD: {codigo_prod} ({descripcion}) en {ubicacion}. Faltante {abs(diferencia)} unidades."
        else:
            mensaje_ara = f"⚠️ NOVEDAD: {codigo_prod} ({descripcion}) en {ubicacion}. Sobrante {diferencia} unidades."

        resultado = {
            "codigo": codigo_prod,
            "descripcion": descripcion,
            "ubicacion": ubicacion,
            "teorico": stock_teorico,
            "fisico": cantidad_contada,
            "estado": estado,
            "detalle": mensaje_ara
        }
        
        sesiones_inventario[usuario]["verificados"] += 1 
        sesiones_inventario[usuario]["novedades"].append(resultado)

        # =====================================================================
        # 🚀 NUEVA INYECCIÓN: REGISTRO HISTÓRICO EN CALIENTE PARA LA TARJETA
        # =====================================================================
        base_dir = os.path.dirname(__file__)
        ruta_ultimos_conteos = os.path.join(base_dir, 'brain_knowledge', 'ultimos_conteos.json')
        
        # Leer el JSON maestro de conteos rápidos si existe
        historial_conteos = {}
        if os.path.exists(ruta_ultimos_conteos):
            try:
                with open(ruta_ultimos_conteos, 'r', encoding='utf-8') as f:
                    historial_conteos = json.load(f)
            except Exception:
                historial_conteos = {}
        
        # Estampas de tiempo con el formato que pediste para el front
        ahora = datetime.now()
        fecha_tarjeta = ahora.strftime("%d-%m-%y")
        hora_tarjeta = ahora.strftime("%I:%M%p")  # Ejemplo: 10:54AM

        # Guardamos o pisamos el conteo de este artículo usando su código en mayúsculas
        clave_producto = codigo_prod.upper()
        historial_conteos[clave_producto] = {
            "cantidad": cantidad_contada,
            "usuario": usuario.upper().strip(),
            "fecha": fecha_tarjeta,
            "hora": hora_tarjeta
        }

        # Guardar físicamente en el archivo plano de la carpeta brain_knowledge
        with open(ruta_ultimos_conteos, 'w', encoding='utf-8') as f:
            json.dump(historial_conteos, f, indent=4, ensure_ascii=False)
        # =====================================================================

        # Retornamos tu JSON original sumándole la data del conteo para tu JavaScript
        return jsonify({
            "status": "success",
            "estado": estado,
            "mensaje": mensaje_ara,
            "progreso": f"{sesiones_inventario[usuario]['verificados']} / {sesiones_inventario[usuario]['productos_en_estante']}",
            "conteo_info": historial_conteos[clave_producto]  # 🔥 ¡Listo para el Frontend!
        })

    except Exception as e:
        print(f"❌ Error en verificación: {str(e)}")
        return jsonify({"status": "error", "mensaje": f"Error en verificación: {str(e)}"}), 500

@app.route('/api/inventario/ultimos-conteos', methods=['GET'])
def obtener_ultimos_conteos():
    try:
        base_dir = os.path.dirname(__file__)
        ruta_ultimos_conteos = os.path.join(base_dir, 'brain_knowledge', 'ultimos_conteos.json')
        
        # Si el archivo JSON ya existe con datos, lo leemos y lo mandamos completico
        if os.path.exists(ruta_ultimos_conteos):
            try:
                with open(ruta_ultimos_conteos, 'r', encoding='utf-8') as f:
                    historial = json.load(f)
                return jsonify(historial)
            except Exception:
                # Si por alguna razón el archivo está corrupto o vacío, mandamos objeto limpio
                return jsonify({})
        
        # Si es la primera vez y el archivo no existe, mandamos un mapa vacío para que el JS no rompa
        return jsonify({})

    except Exception as e:
        print(f"❌ Error al exponer historial de conteos: {str(e)}")
        return jsonify({"status": "error", "mensaje": f"Error al recuperar historial: {str(e)}"}), 500

@app.route('/api/inventario/finalizar', methods=['POST'])
def finalizar_inventario():
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "mensaje": "No se recibieron datos"}), 400

        nombre_usuario = data.get('usuario', '').strip()
        estante = data.get('estante', '').strip()
        productos_frontend = data.get('productos', []) 

        if not nombre_usuario or not estante:
            return jsonify({"status": "error", "mensaje": "Data incompleta (falta usuario o estante)"}), 400

        ahora = datetime.now()
        fecha_reporte = ahora.strftime("%Y-%m-%d")
        hora_reporte = ahora.strftime("%I:%M:%S %p")

        # 📅 Formatos específicos para la visualización rápida en la tarjeta
        fecha_tarjeta = ahora.strftime("%d-%m-%y")
        hora_tarjeta = ahora.strftime("%I:%M %p")

        detalles_auditados = []
        verificados_ok = 0
        
        codigo_unico = uuid.uuid4().hex[:8].upper()
        reporte_id = f"REP-{codigo_unico}"

        # 📂 Localización de rutas respetando tu entorno global
        base_dir = os.path.dirname(__file__)
        carpeta_reportes = os.path.join(base_dir, 'brain_knowledge', 'reportes')
        ruta_ultimos_conteos = os.path.join(base_dir, 'brain_knowledge', 'ultimos_conteos.json')

        if not os.path.exists(carpeta_reportes):
            os.makedirs(carpeta_reportes)

        # 🔍 Cargar el historial existente de últimos conteos para actualizarlo en caliente
        historial_conteos = {}
        if os.path.exists(ruta_ultimos_conteos):
            try:
                with open(ruta_ultimos_conteos, 'r', encoding='utf-8') as f:
                    historial_conteos = json.load(f)
            except Exception:
                historial_conteos = {}

        # PROCESAMIENTO DE CADA PRODUCTO
        for item in productos_frontend:
            codigo = item.get('codigo', '').strip().upper()
            cantidad_fisica = float(item.get('cantidad', 0))

            producto_db = consultar_sqlite_maestro(codigo)
            
            if producto_db:
                descripcion_real = producto_db["descripcion"]
                cantidad_teorica = float(producto_db["stock_maestro"] if producto_db["stock_maestro"] else 0)
            else:
                descripcion_real = item.get('descripcion', 'Producto no registrado en maestro')
                cantidad_teorica = float(item.get('teorico', 0.0))

            estado_item = "OK"
            detalle_texto = ""

            if cantidad_fisica == cantidad_teorica:
                estado_item = "OK"
                detalle_texto = f"✅ {codigo}: Verificado al pelo."
                verificados_ok += 1
            elif cantidad_fisica < cantidad_teorica:
                estado_item = "FALTA"
                diferencia = int(cantidad_teorica - cantidad_fisica)
                detalle_texto = f"⚠️ {codigo}: Faltan {diferencia} unidades en físico."
            elif cantidad_fisica > cantidad_teorica:
                estado_item = "SOBRA"
                diferencia = int(cantidad_fisica - cantidad_teorica)
                detalle_texto = f"🔥 {codigo}: Sobran {diferencia} unidades en estante."

            detalles_auditados.append({
                "codigo": codigo,
                "descripcion": descripcion_real,
                "ubicacion": estante,
                "teorico": int(cantidad_teorica),
                "fisico": int(cantidad_fisica),
                "estado": estado_item,
                "detalle": detalle_texto
            })

            # 💾 Sincronizamos este producto en el historial global de conteos
            historial_conteos[codigo] = {
                "cantidad": int(cantidad_fisica),
                "usuario": nombre_usuario.upper().strip(),
                "fecha": fecha_tarjeta,
                "hora": hora_tarjeta
            }

        # CUERPO DEL REPORTE DE REUBICACIÓN O CIERRE
        cuerpo_reporte = {
            "id": reporte_id,
            "usuario": nombre_usuario,
            "estante": estante,
            "fecha": f"{fecha_reporte} {hora_reporte}", 
            "total_articulos": len(detalles_auditados),
            "verificados": verificados_ok,
            "detalles": detalles_auditados
        }

        # ESCRITURA DEL REPORTE INDIVIDUAL
        usuario_limpio = nombre_usuario.replace(" ", "_")
        nombre_archivo_json = f"{reporte_id}_{usuario_limpio}.json"
        ruta_final_archivo = os.path.join(carpeta_reportes, nombre_archivo_json)

        with open(ruta_final_archivo, 'w', encoding='utf-8') as archivo:
            json.dump(cuerpo_reporte, archivo, indent=4, ensure_ascii=False)

        # 💾 ESCRITURA FÍSICA DEL ARCHIVO DE ÚLTIMOS CONTEOS ACTUALIZADO
        with open(ruta_ultimos_conteos, 'w', encoding='utf-8') as archivo_c:
            json.dump(historial_conteos, archivo_c, indent=4, ensure_ascii=False)

        print(f"✅ Nuevo reporte único creado: {nombre_archivo_json}")
        print(f"💾 Historial global de conteos sincronizado correctamente.")

        # GAMIFICACIÓN: Registrar puntos por inventario (0.25 pts por renglón contado)
        total_renglones = len(detalles_auditados)
        registrar_puntos(nombre_usuario, 'inventario', reporte_id, total_renglones)

        usuario_key = nombre_usuario.lower()
        if usuario_key in sesiones_inventario:
            del sesiones_inventario[usuario_key]

        return jsonify({
            "status": "success",
            "reporte_id": cuerpo_reporte["id"],
            "mensaje": f"Reporte auditado con éxito para {nombre_usuario}",
            "datos": cuerpo_reporte
        })

    except Exception as e:
        print(f"❌ ERROR CRÍTICO al procesar auditoría: {str(e)}")
        return jsonify({"status": "error", "mensaje": f"Error interno en el servidor: {str(e)}"}), 500
    
@app.route('/api/ubicaciones/actualizar', methods=['POST'])
def actualizar_ubicacion_maestro():
    """
    Actualiza la columna real de ubicación en la base de datos (SQL Server / Profit)
    para matar de raíz las discrepancias con el Visor de Artículos.
    """
    data = request.get_json() or {}
    codigo = data.get('codigo')
    nueva_ubicacion = data.get('ubicacion')

    if not codigo or not nueva_ubicacion:
        return jsonify({"status": "error", "message": "Código y Ubicación son obligatorios."}), 400

    try:
        # EXECUTA TU QUERY DE PROFIT / SQL SERVER REAL
        # cursor = db.cursor()
        # query = "UPDATE sccodigo_ubicacion SET ubicacion = ? WHERE co_art = ?" (O como se llame tu tabla física)
        # cursor.execute(query, (nueva_ubicacion, codigo))
        # db.commit()
        
        print(f"⚙️ [ARA DATABASE] Sincronizado: Articulo {codigo} movido a {nueva_ubicacion}")
        
        return jsonify({
            "status": "success", 
            "message": f"Ubicación actualizada en la base de datos matriz a {nueva_ubicacion}."
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error en BD: {str(e)}"}), 500

# =====================================================================
# HISTORIAL DINÁMICO DE REPORTES
# =====================================================================
@app.route('/api/reportes/historial/<tipo>/<nombre_usuario>', methods=['GET'])
def historial_reportes_dinamico(tipo, nombre_usuario):
    try:
        folder = REPORTES_FOLDER if tipo == 'articulos' else LOC_REPORTES_FOLDER
        usuario_ups = nombre_usuario.strip().upper()
        es_admin = usuario_ups in ['ADMIN1', 'SÚPER ADMIN', 'SUPER ADMIN', 'ADMIN']
        
        mi_usuario_limpio = nombre_usuario.replace(' ', '_').upper()
        mi_usuario_normal = nombre_usuario.strip().upper()
        
        archivos = os.listdir(folder)
        mis_reportes = []
        
        for arch in archivos:
            if arch.endswith('.json'):
                try:
                    with open(os.path.join(folder, arch), 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    
                    creador_json = str(data.get('usuario', data.get('operador', ''))).upper().strip()
                    if es_admin or (mi_usuario_limpio in arch.upper()) or (mi_usuario_normal == creador_json):
                        id_reporte = data.get('id') or arch.replace('.json', '')
                        
                        if tipo == 'articulos':
                            mis_report_info = {
                                "id": id_reporte, 
                                "fecha": data.get('fecha', 'Sin fecha'), 
                                "estante": data.get('estante', 'General'), 
                                "total": data.get('total_articulos', data.get('total', 0)), 
                                "novedades": len(data.get('detalles', []))
                            }
                        else:
                            mis_report_info = {
                                "id": id_reporte, 
                                "fecha": data.get('fecha', 'Sin fecha'), 
                                "estante": data.get('desde', data.get('estante', 'N/A')), 
                                "total": 1, 
                                "novedades": 0
                            } 
                        mis_reportes.append(mis_report_info)
                except: continue
        
        mis_reportes.sort(key=lambda x: x['fecha'] if x['fecha'] else '', reverse=True)
        return jsonify({"status": "success", "reportes": mis_reportes})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500

@app.route('/api/reportes/historial/<nombre_usuario>', methods=['GET'])
def historial_reportes_legacy(nombre_usuario):
    return historial_reportes_dinamico('articulos', nombre_usuario)
    
@app.route('/api/reportes/detalle/<reporte_id>', methods=['GET'])
def detalle_reporte(reporte_id):
    try:
        archivo_encontrado = None
        for folder in [REPORTES_FOLDER, LOC_REPORTES_FOLDER]:
            for arch in os.listdir(folder):
                if arch.endswith('.json') and reporte_id in arch:
                    archivo_encontrado = os.path.join(folder, arch)
                    break
            if archivo_encontrado: break
        
        if not archivo_encontrado:
            return jsonify({"status": "error", "mensaje": "Archivo físico ausente."}), 404
        
        with open(archivo_encontrado, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500
#=============================================================================================
# MODULO DE REPORTES Y ANALISIS DE CAMBIO DE UBUCACIONES JSON - SQL
#==============================================================================
@app.route('/api/inventario/cambio-ubicacion', methods=['POST'])
def cambio_ubicacion():
    try:
        data = request.get_json()
        
        # Extraemos los datos usando los nombres EXACTOS que envía tu JS
        usuario = data.get('usuario')
        codigo = data.get('codigo')
        desde = data.get('ubicacion_vieja')  # <--- MAPEADO DESDE TU FRONT
        hacia = data.get('ubicacion_nueva')  # <--- MAPEADO DESDE TU FRONT
        
        # Validación estricta
        if not all([usuario, codigo, desde, hacia]):
            return jsonify({"status": "error", "mensaje": "Faltan campos obligatorios en el envío"}), 400
        
        # Generar ID de 6 caracteres (Ya con random y string importados arriba)
        id_aleatorio = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        mov_id = f"MOV-{id_aleatorio}"
        
        # Estampa de tiempo para el reporte
        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Formato idéntico a tus registros guardados (Claves: desde, hacia)
        reporte_data = {
            "id": mov_id,
            "usuario": usuario.upper().strip(),
            "codigo": codigo.strip(),
            "desde": desde.strip(),
            "hacia": hacia.strip(),
            "fecha": fecha_actual
        }
        
        # Localizar el directorio en tu estructura ARA_Brain
        base_dir = os.path.dirname(os.path.abspath(__file__))
        folder_path = os.path.join(base_dir, 'ARA_Brain', 'brain_knowledge', 'reportes_ubicacion')
        
        # Ajuste por si corres el servidor ya posicionado dentro de la carpeta ARA_Brain
        if not os.path.exists(os.path.join(base_dir, 'ARA_Brain')) and 'brain_knowledge' in os.listdir(base_dir):
            folder_path = os.path.join(base_dir, 'brain_knowledge', 'reportes_ubicacion')
            
        os.makedirs(folder_path, exist_ok=True)
        
        # Nombre de archivo limpio: MOV-XXXXXX_USUARIO.json
        usuario_filename = usuario.upper().strip().replace(" ", "_")
        filename = f"{mov_id}_{usuario_filename}.json"
        file_path = os.path.join(folder_path, filename)
        
        # Escritura física del JSON
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(reporte_data, f, ensure_ascii=False, indent=4)
            
        return jsonify({
            "status": "success", 
            "mensaje": f"Movimiento {mov_id} procesado con éxito. Archivo guardado."
        }), 200
        
    except Exception as e:
        print(f"❌ Error crítico en cambio-ubicacion: {str(e)}")
        return jsonify({"status": "error", "mensaje": f"Error interno en el servidor: {str(e)}"}), 500

# =============================================================================
# MÓDULO DE PREPARACIÓN OPTIMIZADO CON TRAZABILIDAD JSON-SQL
# =============================================================================
@app.route('/api/preparacion')
def api_preparacion():
    query = request.args.get('q', '').lower()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. Consulta principal a Profit Plus (Tu lógica original + Inyección de deposito_bqto)
        if query:
            cursor.execute("""
                SELECT codigo AS co_art, descripcion AS art_des, campo7 AS ubicacion, 
                       stock_maestro AS stock_act, deposito_bqto AS stock_bulto 
                FROM stock_maestro 
                WHERE LOWER(codigo) LIKE ? OR LOWER(descripcion) LIKE ? 
                LIMIT 50
            """, (f"%{query}%", f"%{query}%"))
        else:
            cursor.execute("""
                SELECT codigo AS co_art, descripcion AS art_des, campo7 AS ubicacion, 
                       stock_maestro AS stock_act, deposito_bqto AS stock_bulto 
                FROM stock_maestro 
                LIMIT 20
            """)
        
        filas = cursor.fetchall()
        items = [dict(f) for f in filas]
        
        # 2. Inyección del historial de relocalizaciones físicas + ubicación pendiente
        for item in items:
            cursor.execute("""
                SELECT desde, hacia AS ubicacion, usuario AS usuario_cambio, fecha AS fecha_cambio 
                FROM reportes_ubicacion 
                WHERE co_art = ? 
                ORDER BY fecha DESC
                LIMIT 3
            """, (item['co_art'],))
            movimientos = cursor.fetchall()
            item['ubicaciones_alternas'] = [dict(m) for m in movimientos]

            # Ubicación pendiente (procesado_profit = 0)
            cursor.execute("""
                SELECT hacia FROM reportes_ubicacion
                WHERE co_art = ? AND COALESCE(procesado_profit, 0) = 0
                ORDER BY fecha DESC LIMIT 1
            """, (item['co_art'],))
            row_pend = cursor.fetchone()
            item['ubicacion_pendiente'] = row_pend['hacia'] if row_pend else None
            
        conn.close()
        return jsonify(items)
        
    except Exception as e:
        return jsonify({"error": f"Falla en consulta SQL: {str(e)}"}), 500

# =============================================================================
# CAMBIO DE UBICACIÓN (Submódulo Inventario)
# =============================================================================
import secrets, re as _re

CATEGORIAS_UBICACION = {
    'CR': 'Cremas', 'AMP': 'Ampollas', 'JBE': 'Jarabes',
    'MD': 'Medicamentos', 'MISC': 'Misceláneos', 'MQ': 'Médico Quirúrgicos'
}
UBICACIONES_ESPECIALES = ['NEVERA', 'RACK', 'ESTIVA', 'BULTO CERRADO', 'OFICINA']

def _generar_id_mov():
    return 'MOV-' + ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))

def _clasificar_ubicacion(campo7):
    if not campo7:
        return 'OTROS', 'Otros'
    c = campo7.upper().strip()
    for esp in UBICACIONES_ESPECIALES:
        if esp in c:
            return esp, esp.title()
    m = _re.match(r'\d*([A-Z]+)\d*', c)
    prefix = m.group(1) if m else ''
    for key, name in CATEGORIAS_UBICACION.items():
        if key in prefix:
            return key, name
    return 'OTROS', 'Otros'

def _extraer_estante_piso(ubicacion):
    if not ubicacion:
        return None, None
    m = _re.match(r'\d*([A-Z]+)(\d+)-P(\d+)', ubicacion, _re.IGNORECASE)
    if m:
        return m.group(0), m.group(0)
    for esp in UBICACIONES_ESPECIALES:
        if esp in ubicacion.upper():
            return esp, ubicacion
    return ubicacion, ubicacion

@app.route('/api/inventario/reportar_cambio_ubicacion', methods=['POST'])
def reportar_cambio_ubicacion():
    """Registra cambio de ubicación en reportes_ubicacion (SQLite + JSON) sin modificar stock_maestro."""
    data = request.get_json(silent=True) or {}
    co_art = (data.get('co_art') or '').strip()
    desde = (data.get('desde') or '').strip()
    hacia = (data.get('hacia') or '').strip()
    usuario = (data.get('usuario') or '').strip()
    if not co_art or not hacia:
        return jsonify({"status": "error", "mensaje": "co_art y hacia son obligatorios"}), 400
    if not usuario:
        return jsonify({"status": "error", "mensaje": "Usuario no identificado"}), 401
    mov_id = _generar_id_mov()
    fecha_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db_connection()
    try:
        conn.execute("""
            INSERT INTO reportes_ubicacion (id, usuario, co_art, desde, hacia, fecha, procesado_profit)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """, (mov_id, usuario, co_art, desde or '', hacia, fecha_ts))
        conn.commit()

        # Doble escritura: archivo JSON en brain_knowledge/reportes_ubicacion/
        _guardar_reporte_ubicacion_json(mov_id, usuario, co_art, desde, hacia, fecha_ts)

        return jsonify({"status": "success", "mov_id": mov_id, "mensaje": f"Cambio {mov_id} registrado"})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500
    finally:
        conn.close()


def _guardar_reporte_ubicacion_json(mov_id, usuario, co_art, desde, hacia, fecha):
    """Escribe el reporte como archivo JSON en brain_knowledge/reportes_ubicacion/."""
    dir_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'brain_knowledge', 'reportes_ubicacion')
    os.makedirs(dir_path, exist_ok=True)
    nombre_normalizado = usuario.replace(' ', '_')
    filename = f"{mov_id}_{nombre_normalizado}.json"
    filepath = os.path.join(dir_path, filename)
    contenido = {
        "id": mov_id,
        "usuario": usuario,
        "codigo": co_art,
        "desde": desde or '',
        "hacia": hacia,
        "fecha": fecha
    }
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(contenido, f, ensure_ascii=False, indent=2)
        print(f"[JSON] Reporte guardado: {filepath}")
    except Exception as e:
        print(f"[JSON] Error guardando reporte: {e}")

@app.route('/api/inventario/ubicaciones_por_categoria', methods=['GET'])
def ubicaciones_por_categoria():
    """Retorna categorías y productos agrupados por estante-piso con filtro regex."""
    categoria_filtro = request.args.get('categoria', '').upper().strip()
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT DISTINCT codigo, descripcion, campo7, stock_maestro
            FROM stock_maestro
            WHERE campo7 IS NOT NULL AND campo7 != ''
            ORDER BY campo7
        """).fetchall()

        categorias = {}
        for r in rows:
            codigo = r['codigo']
            desc = r['descripcion'] or ''
            ubi = r['campo7']
            cat_key, cat_name = _clasificar_ubicacion(ubi)
            ep_label, ep_full = _extraer_estante_piso(ubi)
            if not ep_label:
                continue
            if categoria_filtro and cat_key != categoria_filtro:
                continue
            if cat_key not in categorias:
                categorias[cat_key] = {'nombre': cat_name, 'estantes': {}}
            if ep_label not in categorias[cat_key]['estantes']:
                categorias[cat_key]['estantes'][ep_label] = {
                    'etiqueta': ep_label, 'ubicacion': ep_full, 'productos': []
                }
            categorias[cat_key]['estantes'][ep_label]['productos'].append({
                'co_art': codigo, 'descripcion': desc, 'ubicacion': ubi
            })

        # Convertir dicts anidados a listas
        resultado = []
        for ck, cv in sorted(categorias.items()):
            estantes_list = sorted(cv['estantes'].values(),
                                   key=lambda x: int(_re.search(r'(\d+)', x.get('etiqueta','0')).group(1)) if _re.search(r'(\d+)', x.get('etiqueta','0')) else 0)
            resultado.append({
                'categoria_key': ck,
                'categoria_nombre': cv['nombre'],
                'total_estantes': len(estantes_list),
                'estantes': estantes_list
            })

        return jsonify({"status": "success", "categorias": resultado})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/usuarios/preferencia_tutorial', methods=['POST'])
def preferencia_tutorial():
    """Guarda preferencia de ocultar tutorial de cambio de ubicación."""
    data = request.get_json(silent=True) or {}
    usuario = (data.get('usuario') or '').strip()
    hide = 1 if data.get('hide_tutorial') else 0
    if not usuario:
        return jsonify({"status": "error", "mensaje": "Usuario requerido"}), 400
    conn = get_db_connection()
    try:
        conn.execute("UPDATE usuarios SET hide_location_tutorial = ? WHERE id = ? OR nombre = ?",
                     (hide, usuario, usuario))
        conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500
    finally:
        conn.close()

# =============================================================================
# REPORTES CON FILTROS: Discrepancias y Trazabilidad
# =============================================================================
@app.route('/api/reportes/discrepancias', methods=['GET'])
def reportes_discrepancias():
    """
    Retorna discrepancias de stock con filtros opcionales y control RBAC.
    Query params: fecha_inicio, fecha_fin, usuario, usuario_activo, es_admin
    - Si es_admin=false o no se envía: forza filtro case-insensitive por usuario_activo.
    """
    fecha_inicio = request.args.get('fecha_inicio', '')
    fecha_fin = request.args.get('fecha_fin', '')
    usuario = request.args.get('usuario', '')
    usuario_activo = request.args.get('usuario_activo', '')
    es_admin = request.args.get('es_admin', 'false').lower() in ('true', '1', 'yes')

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        where = ["1=1"]
        params = []

        if not es_admin and usuario_activo:
            where.append("(LOWER(ru.usuario) = LOWER(?) OR LOWER(ru.usuario) LIKE LOWER('%' || ? || '%'))")
            params.append(usuario_activo)
            params.append(usuario_activo)
        elif es_admin and usuario and usuario != "Todos":
            where.append("LOWER(ru.usuario) = LOWER(?)")
            params.append(usuario)

        if fecha_inicio:
            where.append("ru.fecha >= ?")
            params.append(fecha_inicio)
        if fecha_fin:
            where.append("ru.fecha <= ?")
            params.append(fecha_fin + ' 23:59:59')

        sql_where = "WHERE " + " AND ".join(where)

        cursor.execute(f"""
            SELECT ru.id, ru.co_art, ru.usuario, ru.desde, ru.hacia,
                   ru.fecha, sm.stock_maestro, sm.campo7,
                   CASE WHEN sm.stock_maestro IS NULL THEN 1 ELSE 0 END as discrepancia
            FROM reportes_ubicacion ru
            LEFT JOIN stock_maestro sm ON sm.codigo = ru.co_art
            {sql_where}
            ORDER BY ru.fecha DESC
            LIMIT 100
        """, params)

        rows = cursor.fetchall()
        conn.close()

        resultados = []
        for r in rows:
            resultados.append({
                "id": r["id"],
                "co_art": r["co_art"],
                "usuario": r["usuario"],
                "desde": r["desde"],
                "hacia": r["hacia"],
                "fecha": r["fecha"],
                "stock_actual": float(r["stock_maestro"] or 0),
                "ubicacion": r["campo7"] or "N/A",
                "discrepancia": bool(r["discrepancia"])
            })

        return jsonify({"status": "success", "data": resultados, "total": len(resultados)})

    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@app.route('/api/reportes/trazabilidad', methods=['GET'])
def reportes_trazabilidad():
    """
    Retorna movimientos de trazabilidad desde reportes_ubicacion con filtros opcionales y control RBAC.
    Query params: fecha_inicio, fecha_fin, usuario, estado_profit, usuario_activo, es_admin
    - Si es_admin=false o no se envía: forza filtro case-insensitive por usuario_activo (nombre completo o username).
    - Si es ES Admin: permite filtrar por parámetro usuario o ver todos.
    Columnas retornadas: mov_id, usuario, sku, desde, hacia, fecha, procesado_profit.
    """
    fecha_inicio = request.args.get('fecha_inicio', '')
    fecha_fin = request.args.get('fecha_fin', '')
    usuario = request.args.get('usuario', '')
    estado_profit = request.args.get('estado_profit', '')
    usuario_activo = request.args.get('usuario_activo', '')
    es_admin = request.args.get('es_admin', 'false').lower() in ('true', '1', 'yes')

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        where = ["1=1"]
        params = []

        if not es_admin and usuario_activo:
            # Case-insensitive: matching exact full name OR substring (for username-like values)
            where.append("(LOWER(ru.usuario) = LOWER(?) OR LOWER(ru.usuario) LIKE LOWER('%' || ? || '%'))")
            params.append(usuario_activo)
            params.append(usuario_activo)
        elif es_admin and usuario and usuario != "Todos":
            where.append("LOWER(ru.usuario) = LOWER(?)")
            params.append(usuario)

        if fecha_inicio:
            where.append("date(ru.fecha) >= date(?)")
            params.append(fecha_inicio)
        if fecha_fin:
            where.append("date(ru.fecha) <= date(?)")
            params.append(fecha_fin)
        if estado_profit != '' and estado_profit != "Todos":
            where.append("COALESCE(ru.procesado_profit, 0) = ?")
            params.append(int(estado_profit))

        sql_where = "WHERE " + " AND ".join(where)

        cursor.execute(f"""
            SELECT
                ru.id AS mov_id,
                ru.usuario,
                ru.co_art AS sku,
                ru.desde,
                ru.hacia,
                ru.fecha,
                COALESCE(ru.procesado_profit, 0) AS procesado_profit
            FROM reportes_ubicacion ru
            {sql_where}
            ORDER BY ru.fecha DESC
            LIMIT 200
        """, params)

        rows = cursor.fetchall()
        conn.close()

        return jsonify({"status": "success", "data": [dict(r) for r in rows], "total": len(rows)})

    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@app.route('/api/notas_pendientes_prep')
def notas_pendientes_prep():
    estados = cargar_estados()
    todas = df_facturas[['fact_num', 'nombre']].to_dict(orient='records')
    pendientes = [f for f in todas if estados.get(str(f['fact_num'])) != 'verificada']
    return jsonify(pendientes)

@app.route('/api/facturas_pendientes')
def facturas_pendientes_chequeo():
    """
    Retorna las facturas que están en estado 'verificada' (listas para chequeo).
    Lee el archivo notas_estado.json y filtra por estado == 'verificada'.
    """
    try:
        estados = cargar_estados()
        # Filtrar solo las que están "verificada" (preparadas y listas para chequeo)
        facturas_verificadas = [fact_id for fact_id, estado in estados.items() if estado == 'verificada']
        
        if not facturas_verificadas:
            return jsonify([])
        
        # Buscar detalles en df_facturas
        facturas_df = df_facturas[df_facturas['fact_num'].astype(str).isin(facturas_verificadas)]
        
        resultado = []
        for _, row in facturas_df.iterrows():
            resultado.append({
                "bar_code": str(row['fact_num']),
                "cliente": row.get('nombre', 'Sin cliente'),
                "status": "pendiente_chequeo"
            })
        
        return jsonify(resultado)
    except Exception as e:
        print(f"❌ Error en facturas_pendientes_chequeo: {e}")
        return jsonify({"status": "error", "mensaje": str(e)}), 500

@app.route('/api/finalizar_preparacion', methods=['POST'])
def finalizar_preparacion():
    try:
        data = request.json
        factura_id = str(data.get('id'))
        usuario = data.get('usuario')
        password_ingresada = data.get('password')
        
        if not validar_usuario(usuario, password_ingresada):
            return jsonify({"status": "error", "mensaje": "Contraseña incorrecta."}), 403

        # Contar renglones de la factura para calcular puntos
        factura = df_facturas[df_facturas['fact_num'].astype(str) == factura_id]
        renglones = 1  # default fallback
        if not factura.empty:
            # Asumimos 1 renglón por factura si no hay detalle; idealmente contar lines reales
            renglones = 1
        
        guardar_estado(factura_id, 'verificada')
        
        # GAMIFICACIÓN: Registrar puntos por picking (1.00 pt por renglón)
        registrar_puntos(usuario, 'picking', factura_id, renglones)
        
        mensaje_ara = f"El usuario {usuario} autenticado con éxito cerró la nota {factura_id}."
        guardar_en_memoria(usuario, "Finalizar Preparación", mensaje_ara)
        return jsonify({"status": "success", "message": "Sincronizado con éxito"})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500

@app.route('/api/notificar_faltante', methods=['POST'])
def notificar_faltante():
    data = request.json
    usuario = data.get('usuario')
    factura_id = data.get('id')
    articulo = data.get('articulo')
    mensaje_ara = f"ALERTA: El usuario {usuario} reporta que el artículo {articulo} NO existe en el estante para la nota {factura_id}."
    guardar_en_memoria(usuario, "Reportar Faltante", mensaje_ara)
    return jsonify({"status": "sent", "message": "Notificación enviada al supervisor"})

@app.route('/api/preparacion/<factura_id>', methods=['GET'])
def obtener_nota_preparacion(factura_id):
    """
    Retorna los detalles de una nota/factura para el módulo de Preparación.
    Estructura compatible con el frontend: {status, factura: {bar_code, cliente, preparador_id, items[]}}
    """
    try:
        factura_id = str(factura_id).strip()
        
        # 1. Buscar la factura en el CSV de facturas
        factura = df_facturas[df_facturas['fact_num'].astype(str) == factura_id]
        if factura.empty:
            return jsonify({"status": "error", "mensaje": "Nota no encontrada en el sistema"}), 404
        
        datos = factura.iloc[0].to_dict()
        
        # 2. Obtener TODOS los items desde stock_maestro (sin LIMIT)
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT codigo AS co_art, descripcion AS art_des, campo7 AS ubicacion, 
                   stock_maestro AS stock_act
            FROM stock_maestro 
            WHERE stock_maestro > 0
        """)
        filas = cursor.fetchall()
        conn.close()
        
        # 3. Construir lista completa de artículos
        todos_los_items = []
        for f in filas:
            todos_los_items.append({
                "cod": str(f['co_art']),
                "des": f['art_des'],
                "pedida": 1,  # Placeholder - idealmente vendría de líneas de factura
                "ubicacion": f['ubicacion'] or 'POR_ASIGNAR'
            })
        
        # 4. MUESTRA ALEATORIA DINÁMICA: 1 a 21 artículos por nota
        if todos_los_items:
            cantidad_aleatoria = random.randint(1, 21)
            # random.sample toma una muestra sin repetición
            items = random.sample(todos_los_items, min(cantidad_aleatoria, len(todos_los_items)))
        else:
            items = []
        
        return jsonify({
            "status": "success",
            "factura": {
                "bar_code": factura_id,
                "cliente": datos.get('nombre', 'No disponible'),
                "preparador_id": datos.get('co_us_in', 'SISTEMA'),
                "items": items
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500

 #===============================================================================
 #
 #===============================================================================
@app.route('/api/visor/producto/<codigo>', methods=['GET'])
def consultar_producto_visor(codigo):
    try:
        codigo_buscar = codigo.strip().upper()
        
        prod_db = consultar_sqlite_maestro(codigo_buscar)
        
        if not prod_db:
            return jsonify({"status": "error", "mensaje": "Producto no encontrado en el sistema maestro"}), 404
            
        # 💡 FIX SEGURO: Forzamos int() validando con 'is not None' para asegurar que viaje el '0' real
        stock_maestro_seguro = int(prod_db["stock_maestro"]) if prod_db["stock_maestro"] is not None else 0
        
        # 🔥 NUEVA INYECCIÓN: Parseo blindado para el bulto cerrado en Barquisimeto
        stock_bulto_seguro = 0
        if "deposito_bqto" in prod_db.keys():
            stock_bulto_seguro = int(prod_db["deposito_bqto"]) if prod_db["deposito_bqto"] is not None else 0

        producto_base = {
            "codigo": codigo_buscar,
            "descripcion": prod_db["descripcion"], 
            "ubicacion_maestra": prod_db["ubicacion"] if prod_db["ubicacion"] else "POR_ASIGNAR",
            "stock_maestro": stock_maestro_seguro,  # Enviado sin fallas
            "stock_bulto": stock_bulto_seguro       # ¡Viaja al Frontend!
        }

        historial_movimientos = []

        if os.path.exists(REPORTES_FOLDER):
            for archivo_nombre in os.listdir(REPORTES_FOLDER):
                if archivo_nombre.endswith('.json'):
                    ruta_archivo = os.path.join(REPORTES_FOLDER, archivo_nombre)
                    try:
                        with open(ruta_archivo, 'r', encoding='utf-8') as f:
                            reporte = json.load(f)
                        
                        fecha_rep = reporte.get('fecha', '')
                        estante_rep = reporte.get('estante', '')
                        usuario_rep = reporte.get('usuario', '')

                        for detalle in reporte.get('detalles', []):
                            if detalle.get('codigo', '').strip().upper() == codigo_buscar:
                                historial_movimientos.append({
                                    "id_reporte": reporte.get('id', 'S/N'),
                                    "fecha_registro": fecha_rep,
                                    "ubicacion_fisica": estante_rep,
                                    "operario": usuario_rep,
                                    "cantidad_contada": int(detalle.get('fisico', 0)),
                                    "estado_auditoria": detalle.get('estado', 'OK')
                                })
                    except Exception:
                        continue 

        def parsear_fecha_reporte(mov):
            for formato in ("%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(mov['fecha_registro'], formato)
                except ValueError:
                    pass
            return datetime.min

        historial_movimientos.sort(key=parsear_fecha_reporte, reverse=True)

        ultimo_movimiento = None
        otras_ubicaciones = []

        if historial_movimientos:
            ultimo_movimiento = historial_movimientos[0]
            ubicaciones_vistas = {ultimo_movimiento['ubicacion_fisica']}
            
            for m in historial_movimientos[1:]:
                ub = m['ubicacion_fisica']
                if ub not in ubicaciones_vistas:
                    ubicaciones_vistas.add(ub)
                    otras_ubicaciones.append({
                        "ubicacion": ub,
                        "fecha": m['fecha_registro'],
                        "operario": m['operario']
                    })

        return jsonify({
            "status": "success",
            "producto": {
                "codigo": producto_base["codigo"],
                "descripcion": producto_base["descripcion"],
                "ubicacion_maestra": producto_base["ubicacion_maestra"],
                "stock_maestro": producto_base["stock_maestro"],
                "stock_bulto": producto_base["stock_bulto"],  # Inyectado en la respuesta JSON
                "ultimo_movimiento": ultimo_movimiento, 
                "otras_ubicaciones": otras_ubicaciones
            }
        }), 200

    except Exception as e:
        print(f"❌ ERROR en módulo Visor: {str(e)}")
        return jsonify({"status": "error", "mensaje": f"Error interno: {str(e)}"}), 500
    
@app.route('/api/embalaje/<factura_id>', methods=['GET'])
def verificar_status_embalaje(factura_id):
    try:
        estados = cargar_estados()
        estado_actual = estados.get(str(factura_id))
        
        if not estado_actual: return jsonify({"status": "error", "mensaje": "Nota no ha iniciado proceso."}), 400
        if estado_actual == 'verificada': return jsonify({"status": "error", "mensaje": "Falta pasar por módulo de Chequeo."}), 400
        if estado_actual == 'embalada': return jsonify({"status": "error", "mensaje": "Bulto ya se encuentra cerrado."}), 400

        if estado_actual == 'chequeada':
            factura = df_facturas[df_facturas['fact_num'].astype(str) == str(factura_id)]
            if factura.empty: return jsonify({"status": "error", "mensaje": "Factura ausente"}), 404
            datos = factura.iloc[0].to_dict()
            
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT codigo AS co_art, descripcion AS art_des FROM stock_maestro LIMIT 3")
            articulos_reales = cursor.fetchall()
            conn.close()

            items_embalaje = [{"cod": str(art['co_art']), "des": art['art_des'], "pedida": 1} for art in articulos_reales]
            return jsonify({"status": "success", "factura": {"bar_code": str(factura_id), "cliente": datos.get('nombre', 'No disponible'), "preparador_id": datos.get('co_us_in', 'SISTEMA'), "items": items_embalaje}})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500

@app.route('/api/embalaje/finalizar', methods=['POST'])
def finalizar_embalaje_nota():
    try:
        data = request.json
        factura_id = str(data.get('id'))
        usuario = data.get('usuario')
        
        # Contar renglones verificados (simplificado: usamos 1 como base)
        renglones = 1
        
        guardar_estado(factura_id, 'embalada')
        
        # GAMIFICACIÓN: Registrar puntos por chequeo/embalaje (1.00 pt por renglón)
        registrar_puntos(usuario, 'chequeo', factura_id, renglones)
        
        precinto_seguridad = f"PCT-{uuid.uuid4().hex[:6].upper()}"
        mensaje_log = f"Bulto Cerrado: El usuario {usuario} embaló y selló la nota {factura_id}. Precinto asignado: {precinto_seguridad}."
        guardar_en_memoria(usuario, "Cierre de Embalaje", mensaje_log)
        return jsonify({"status": "success", "mensaje": "Sincronizado con el sistema central", "precinto": precinto_seguridad})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500

@app.route('/api/whatsapp', methods=['POST'])
def whatsapp_bot():
    try:
        data = request.json
        cliente_id = data.get('usuario') 
        mensaje = data.get('mensaje').strip().lower()
        factura_cliente = df_facturas[df_facturas['telefono'].astype(str) == str(cliente_id)]
        
        if "pedido" in mensaje or "paquete" in mensaje or "estado" in mensaje:
            if not factura_cliente.empty:
                fact = factura_cliente.iloc[0]
                estado_actual = fact.get('estado_actual', 'En proceso')
                return jsonify({"status": "success", "respuesta": f"📦 ¡Hola! Tu pedido {fact['fact_num']} está actualmente en estado: {estado_actual}."})
            return jsonify({"status": "success", "respuesta": "No encontré un pedido activo asociado a tu número."})

        prompt_bot = f"Eres el asistente de ventas de ARA. Un cliente escribe por WhatsApp: '{mensaje}'. Responde de forma amable, corta y profesional."
        return jsonify({"status": "success", "respuesta": generar_respuesta_phi3(prompt_bot)})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500

def generar_respuesta_phi3(prompt):
    try:
        payload = {"model": "phi3", "prompt": prompt, "stream": False}
        res = requests.post(f"http://100.82.4.15:11434/api/generate", json=payload, timeout=30)
        return res.json().get('response', "Procesando solicitud...")
    except: return "Hola, en este momento tengo problemas técnicos, pero pronto te atenderé."

# =============================================================================
# 5. CEREBRO LLAVA / PHI3 PARA CHAT (Alineado a SQL y Saneado)
# =============================================================================
@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    user_msg = data.get('message', '')
    imagen_b64 = data.get('image') or data.get('foto')
    datos_producto_especifico = ""
    trazabilidad_inyectada = ""
    entidad_detectada = None
    ficha_foto = ""

    # --- Si el mensaje incluye imagen, usar Visión (Mini ARA Engine) ---
    if imagen_b64:
        try:
            from mini_ara_engine import get_engine
            engine = get_engine()
            analisis = engine.analizar_foto_producto(imagen_b64)
            if analisis.get("ficha_tecnica"):
                ficha_foto = analisis["ficha_tecnica"]
                entidad_detectada = (analisis.get("producto") or {}).get("codigo", "")
        except Exception as e:
            print(f"[Chat] Error en análisis de foto: {e}")

    # --- Motor de Trazabilidad Hexagonal 360° ---
    try:
        from ara_brain import (
            detectar_codigo_articulo,
            obtener_auditoria_completa_articulo,
            obtener_trazabilidad_hexagonal,
            formatear_evidencias_para_prompt,
            es_consulta_auditoria,
            es_consulta_reporte,
            obtener_reporte_top_productos,
            formatear_reporte_para_prompt,
            obtener_lecciones_aprendidas,
            SYSTEM_PROMPT_AUDITOR
        )

        # --- Detectar si pide reporte de más vendidos ---
        if es_consulta_reporte(user_msg) and not imagen_b64:
            reporte = obtener_reporte_top_productos(dias=30, limite=10)
            if reporte and "productos" in reporte and reporte["productos"]:
                datos_producto_especifico = formatear_reporte_para_prompt(reporte)
                entidad_detectada = "REPORTE"

        if not datos_producto_especifico:
            # 1. Detectar si es NOTA o ARTÍCULO
            codigo_detectado = detectar_codigo_articulo(user_msg) if not imagen_b64 else None
            entidad_candidata = codigo_detectado or user_msg.strip().upper()

            if entidad_candidata and (codigo_detectado or es_consulta_auditoria(user_msg)):
                # Trazabilidad Hexagonal (detecta NOTA vs ARTÍCULO automáticamente)
                trazabilidad = obtener_trazabilidad_hexagonal(entidad_candidata)
                if trazabilidad and not trazabilidad.get("error"):
                    entidad_detectada = entidad_candidata
                    trazabilidad_inyectada = json.dumps(trazabilidad, indent=2, ensure_ascii=False)

                # También la auditoría clásica para artículos
                if not codigo_detectado and trazabilidad.get("tipo") == "ARTICULO":
                    pass  # ya tenemos los datos vía trazabilidad

                if codigo_detectado or trazabilidad.get("tipo") == "ARTICULO":
                    co_art = codigo_detectado or entidad_candidata
                    auditoria = obtener_auditoria_completa_articulo(co_art)
                    if auditoria and not auditoria.get("error"):
                        evidencias = formatear_evidencias_para_prompt(auditoria)
                        if evidencias:
                            datos_producto_especifico = evidencias
    except ImportError:
        print("[Chat] ara_brain no disponible, usando modo legacy")
    except Exception as e:
        print(f"[Chat] Error en motor auditoría: {e}")

    # --- Fallback legacy si no se activó auditoría ---
    if not datos_producto_especifico and not imagen_b64:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM stock_maestro")
            todos_productos = cursor.fetchall()
            conn.close()

            for prod in todos_productos:
                prod_dict = dict(prod)
                code = str(prod_dict.get('codigo', ''))
                if code.lower() in user_msg.lower():
                    datos_producto_especifico = (
                        f"\n--- DATOS REALES DEL PRODUCTO ENCONTRADO ---\n"
                        f"Código: {prod_dict.get('codigo')}\n"
                        f"Descripción: {prod_dict.get('descripcion')}\n"
                        f"Stock Maestro (Despacho en Piso): {prod_dict.get('stock_maestro', 0)} unds\n"
                        f"Stock Reserva (Bulto Cerrado): {prod_dict.get('stock_bulto_cerrado', 0)} bultos\n"
                        f"Ubicación Física: {prod_dict.get('campo7', 'N/A')}\n"
                        f"--------------------------------------------"
                    )
                    break
        except Exception as e:
            print(f"Error parseando productos en Chat: {e}")

    # --- Lecciones Aprendidas (Feedback Loop) ---
    lecciones = ""
    try:
        from ara_brain import obtener_lecciones_aprendidas
        lecciones = obtener_lecciones_aprendidas(limite=5)
    except Exception as e:
        print(f"[Chat] Error obteniendo lecciones: {e}")

    memoria_reciente = obtener_memoria_reciente()
    ficha_extra = ficha_foto if ficha_foto else ""
    prompt_sistema = f"""Eres ARA IA, el asistente logístico inteligente.
Tu misión es guiar al usuario basado en la bitácora real. NO inventes números.
--- BITÁCORA DE EVENTOS RECIENTES ---
{memoria_reciente}
--- DATOS DEL PRODUCTO / AUDITORÍA ---
{datos_producto_especifico if datos_producto_especifico else "Ningún producto seleccionado en el mensaje."}
{ficha_extra}
{lecciones}
"""
    # --- Key Pool NVIDIA NIM con failover automático ---
    respuesta_texto = None
    if not imagen_b64:
        try:
            from ara_brain import llamar_nvidia_con_failover
            respuesta_nvidia = llamar_nvidia_con_failover(prompt_sistema, user_msg)
            if respuesta_nvidia:
                respuesta_texto = respuesta_nvidia
        except ImportError:
            print("[Chat] ara_brain.llamar_nvidia_con_failover no disponible")
        except Exception as e:
            print(f"[Chat] NVIDIA NIM failover falló: {e}")

    if not respuesta_texto:
        # Fallback a Mini ARA Engine (local, edge)
        try:
            from mini_ara_engine import get_engine
            engine = get_engine()
            if engine.verificar_disponibilidad():
                respuesta_texto = engine.preguntar(user_msg, contexto_extra=datos_producto_especifico)
            else:
                print("[Chat] Mini ARA Engine no disponible, usando Ollama genérico")
        except ImportError:
            print("[Chat] mini_ara_engine no disponible, usando Ollama genérico")
        except Exception as e:
            print(f"[Chat] Mini ARA Engine falló: {e}")

    if not respuesta_texto:
        # Fallback Ollama genérico (último recurso)
        modelo = "llava" if imagen_b64 else "phi3"
        payload = {"model": modelo, "prompt": f"{prompt_sistema}\n\nUsuario: {user_msg}\nARA IA:", "stream": False}
        if imagen_b64: payload["images"] = [imagen_b64]

        try:
            response = requests.post('http://100.82.4.15:11434/api/generate', json=payload, timeout=120)
            respuesta_texto = response.json().get('response', 'Sin respuesta.')
        except Exception as e:
            respuesta_texto = f"Error de conexión con Ollama: {str(e)}"

    return jsonify({
        "respuesta": respuesta_texto,
        "entidad_detectada": entidad_detectada,
        "trazabilidad": trazabilidad_inyectada if trazabilidad_inyectada else None
    })


# =============================================================================
# Endpoint de Feedback para el Motor de Auto-Mejora
# =============================================================================
@app.route('/api/chat/feedback', methods=['POST'])
def chat_feedback():
    """
    Recibe feedback del usuario sobre una respuesta de la IA.
    Body: { "pregunta": "...", "respuesta_ia": "...", "es_correcta": true/false, "corregida_por": "..." }
    """
    data = request.json
    pregunta = data.get('pregunta', '')
    respuesta_ia = data.get('respuesta_ia', '')
    es_correcta = data.get('es_correcta', True)
    corregida_por = data.get('corregida_por')

    if not pregunta or not respuesta_ia:
        return jsonify({"status": "error", "mensaje": "Faltan pregunta y/o respuesta_ia"}), 400

    try:
        from ara_brain import registrar_feedback
        fid = registrar_feedback(pregunta, respuesta_ia, es_correcta, corregida_por)
        return jsonify({"status": "success", "id": fid})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500

# =============================================================================
# Endpoint de Audio (Mini ARA Engine + faster-whisper)
# =============================================================================
@app.route('/api/ia/audio', methods=['POST'])
def ia_audio():
    """
    Recibe un archivo de audio (multipart/form-data), lo transcribe con
    faster-whisper y retorna el texto. Opcionalmente responde con IA local.
    """
    if 'audio' not in request.files:
        return jsonify({"status": "error", "mensaje": "No se recibió archivo de audio"}), 400

    audio_file = request.files['audio']
    formato = audio_file.filename.rsplit('.', 1)[-1] if '.' in audio_file.filename else 'wav'
    datos_binarios = audio_file.read()

    if not datos_binarios:
        return jsonify({"status": "error", "mensaje": "Archivo de audio vacío"}), 400

    try:
        from mini_ara_engine import get_engine
        engine = get_engine()
        transcripcion = engine.procesar_audio_local(datos_binarios, formato)

        responder = request.form.get('responder', 'false').lower() == 'true'
        if responder and not transcripcion.startswith("[Mini ARA]"):
            respuesta = engine.preguntar(transcripcion)
            return jsonify({
                "status": "success",
                "transcripcion": transcripcion,
                "respuesta": respuesta
            })

        return jsonify({"status": "success", "transcripcion": transcripcion})
    except ImportError:
        return jsonify({
            "status": "error",
            "mensaje": "mini_ara_engine no disponible. Verifica instalación."
        }), 500
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/vision_search', methods=['POST'])
def vision_search():
    try:
        data = request.json
        imagen_b64 = data.get('image')
        if not imagen_b64: 
            return jsonify({"tipo": "error", "mensaje": "No se recibió imagen"}), 400

        # 1. OCR con Llava para extraer el texto/código de la foto
        payload_llava = {
            "model": "llava", 
            "prompt": "ACT AS AN OCR. Output ONLY the product code or name. No conversational sentences.", 
            "stream": False, 
            "images": [imagen_b64]
        }
        response_llava = requests.post('http://100.82.4.15:11434/api/generate', json=payload_llava, timeout=45)
        texto_extraido = response_llava.json().get('response', '').strip()
        
        # 2. INVESTIGACIÓN EN PROFUNDIDAD (SQL + Web)
        # Llama a la función optimizada de ara_vision.py
        resultado_analisis = investigar_producto_ara(texto_extraido)
        
        return jsonify({
            "status": "processed", 
            "text": texto_extraido,
            "analisis": resultado_analisis  # <-- El frontend recibe el bloque completo listo para pintar
        })
    except Exception as e:
        return jsonify({"tipo": "error", "mensaje": str(e)}), 500

# Ejecutor global de hilos para descargar llamadas IA bloqueantes de los hilos de Waitress
executor_vision = ThreadPoolExecutor(max_workers=10)

# Marca de tiempo de arranque para el endpoint de health
_SERVER_START_TIME = time.time()

# Rate limiter simple en memoria (IP → lista de timestamps)
_vision_rate_limit: dict[str, list[float]] = {}
_VISION_RATE_MAX = 15       # máx 15 requests
_VISION_RATE_WINDOW = 60    # en una ventana de 60s

def _check_vision_rate_limit(ip: str) -> bool:
    """Retorna True si la IP no ha excedido el límite, False si debe ser bloqueada."""
    ahora = time.time()
    ventana = ahora - _VISION_RATE_WINDOW
    timestamps = _vision_rate_limit.get(ip, [])
    # Podar timestamps fuera de la ventana
    timestamps = [t for t in timestamps if t > ventana]
    if len(timestamps) >= _VISION_RATE_MAX:
        _vision_rate_limit[ip] = timestamps
        return False
    timestamps.append(ahora)
    _vision_rate_limit[ip] = timestamps
    return True

# =============================================================================
# ENDPOINT /api/vision/escanear — Visor de artículos con IA (NVIDIA NIM + Ollama)
# =============================================================================
@app.route('/api/vision/escanear', methods=['POST'])
def vision_escanear():
    """Recibe imagen, la procesa con IA vision y busca en stock_maestro (asíncrono vía ThreadPoolExecutor, rate-limited)."""
    ip = request.remote_addr or 'unknown'
    if not _check_vision_rate_limit(ip):
        return jsonify({"status": "error", "mensaje": "Límite de escaneos alcanzado. Por favor espera un minuto."}), 429

    import traceback as tb
    try:
        if 'image' in request.files:
            image_file = request.files['image']
            image_bytes = image_file.read()
            future = executor_vision.submit(procesar_imagen_visor, image_bytes)
        elif request.is_json:
            data = request.get_json(silent=True)
            b64 = (data or {}).get('image', '')
            if not b64:
                return jsonify({"status": "error", "mensaje": "No se recibió imagen"}), 400
            future = executor_vision.submit(procesar_imagen_visor, b64)
        else:
            return jsonify({"status": "error", "mensaje": "Envíe image (form-data) o image (JSON base64)"}), 400

        resultado = future.result(timeout=25)
        return jsonify(resultado)
    except concurrent.futures.TimeoutError:
        return jsonify({"status": "error", "mensaje": "La IA de visión tardó más de 25s. Intente de nuevo."}), 504
    except Exception as e:
        tb.print_exc()
        return jsonify({"status": "error", "mensaje": str(e)}), 500


# =============================================================================
# ENDPOINT /api/health — Telemetría y monitoreo del servidor
# =============================================================================
@app.route('/api/health', methods=['GET'])
def health_check():
    """Endpoint liviano de telemetría pública. Retorna estado de la DB, cola de visión y uptime."""
    db_status = "connected (WAL mode)"
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
    except Exception:
        db_status = "disconnected"

    return jsonify({
        "status": "ok" if db_status != "disconnected" else "degraded",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "database": db_status,
        "vision_executor_queue": executor_vision._work_queue.qsize(),
        "uptime_seconds": round(time.time() - _SERVER_START_TIME, 1)
    })


# =============================================================================
# ENDPOINT /api/chat/send y /api/chat/history -> DELEGADOS A chat_routes.py
# (La implementación a base de archivos JSON en disco fue eliminada para
#  evitar cruce de chats entre usuarios. Toda la mensajería ahora vive en
#  SQLite vía el módulo chat_routes registrado tras CORS(app).)
# =============================================================================

# =============================================================================
# ENDPOINT /api/reporte/pdf  ->  DELEGADO A pdf_route.register_pdf_route(app)
# (La implementación antigua inline fue eliminada para evitar colisión de
#  rutas / shadowing. Ver archivo: pdf_route.py en esta misma carpeta.)
# =============================================================================


# =============================================================================
# MÓDULO RUTAS — ORSAdapter + Endpoints /api/rutas/*
# =============================================================================
ORS_API_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjVlYmZmNzk4OGE3YzQ3MmNiZDk5NGI1MGE2MWJjMDhjIiwiaCI6Im11cm11cjY0In0="

# Almacén en memoria de posiciones de choferes
_posiciones_choferes = {}
_rutas_activas = {}

class ORSAdapter:
    """Adaptador hexagonal para OpenRouteService."""

    BASE_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    MATRIX_URL = "https://api.openrouteservice.org/v2/matrix/driving-car"

    def calcular_ruta_optimizada(self, origen, destinos):
        """
        Reordena destinos por eficiencia y devuelve GeoJSON + orden óptimo.
        origen: [lng, lat]
        destinos: lista de [[lng, lat], ...]
        """
        if not destinos:
            return None, []

        coordenadas = [origen] + destinos

        headers = {
            "Authorization": ORS_API_KEY,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, application/geo+json"
        }

        body = {
            "coordinates": coordenadas,
            "format": "geojson",
            "instructions": True,
            "geometry": True,
            "preference": "recommended",
            "units": "km"
        }

        try:
            resp = requests.post(self.BASE_URL, json=body, headers=headers, timeout=30)
            if resp.status_code != 200:
                print(f"[ORS] Error {resp.status_code}: {resp.text[:200]}")
                return None, []

            data = resp.json()
            route = data.get("features", [{}])[0]
            geometry = route.get("geometry", None)
            properties = route.get("properties", {})
            segments = properties.get("segments", [{}])[0]
            steps = segments.get("steps", [])

            order = []
            for i, dest in enumerate(destinos):
                order.append({"index": i, "coords": dest})

            return geometry, order
        except requests.exceptions.Timeout:
            print("[ORS] Timeout al consultar ORS API")
            # fallback: secuencia sin optimizar
            return None, [{"index": i, "coords": d} for i, d in enumerate(destinos)]
        except Exception as e:
            print(f"[ORS] Error en calcular_ruta_optimizada: {e}")
            return None, []

    def estimar_eta(self, posicion_actual, destino_coords):
        """Devuelve minutos estimados de llegada."""
        from math import radians, sin, cos, sqrt, atan2
        lat1, lon1 = radians(posicion_actual[1]), radians(posicion_actual[0])
        lat2, lon2 = radians(destino_coords[1]), radians(destino_coords[0])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        dist_km = 6371 * c
        velocidad_kmh = 30  # velocidad urbana promedio
        minutos = (dist_km / velocidad_kmh) * 60
        return round(minutos, 1)


_ors_adapter = ORSAdapter()


@app.route('/api/rutas/pedidos_embalados', methods=['GET'])
def rutas_pedidos_embalados():
    """Notas en estado 'embalado' listas para despacho."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ne.id, ne.numero_nota, ne.cliente, ne.direccion,
                   ne.latitud, ne.longitud, ne.items_count, ne.fecha_creacion
            FROM notas_entrega ne
            WHERE ne.estado = 'embalado'
            ORDER BY ne.fecha_creacion DESC
        """)
        filas = cursor.fetchall()
        conn.close()
        return jsonify([dict(f) for f in filas])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/rutas/optimizar_ruta', methods=['POST'])
def rutas_optimizar_ruta():
    """Recibe origen + lista de nota_ids, retorna ruta optimizada con GeoJSON."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON requerido"}), 400

    origen = data.get("origen")  # [lng, lat]
    nota_ids = data.get("nota_ids", [])

    if not origen or not nota_ids:
        return jsonify({"error": "origen y nota_ids requeridos"}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in nota_ids)
        cursor.execute(f"""
            SELECT id, numero_nota, cliente, direccion, latitud, longitud
            FROM notas_entrega
            WHERE id IN ({placeholders}) AND estado = 'embalado'
        """, nota_ids)
        notas = [dict(r) for r in cursor.fetchall()]
        conn.close()

        destinos = []
        for n in notas:
            lat = n.get("latitud")
            lng = n.get("longitud")
            if lat and lng:
                destinos.append({"nota": n, "coords": [float(lng), float(lat)]})

        destinos_coords = [d["coords"] for d in destinos]

        geometry, order = _ors_adapter.calcular_ruta_optimizada(origen, destinos_coords)

        paradas = []
        if order:
            for paso in order:
                idx = paso["index"]
                dest = destinos[idx]
                nota = dest["nota"]
                d_coords = dest["coords"]
                eta = _ors_adapter.estimar_eta(origen, d_coords)
                paradas.append({
                    "orden": len(paradas) + 1,
                    "nota_id": nota["id"],
                    "numero_nota": nota["numero_nota"],
                    "cliente": nota["cliente"],
                    "direccion": nota["direccion"],
                    "latitud": nota["latitud"],
                    "longitud": nota["longitud"],
                    "eta_minutos": eta
                })

        _rutas_activas[data.get("ruta_id", "default")] = {
            "paradas": paradas,
            "origen": origen
        }

        return jsonify({
            "status": "success",
            "paradas": paradas,
            "geojson": geometry
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/rutas/telemetria_chofer', methods=['POST'])
def rutas_telemetria_chofer():
    """Recibe posición GPS del chofer y actualiza estado en memoria."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON requerido"}), 400

    chofer_id = data.get("chofer_id")
    if not chofer_id:
        return jsonify({"error": "chofer_id requerido"}), 400

    entrada = {
        "lat": data.get("lat"),
        "lng": data.get("lng"),
        "velocidad": data.get("velocidad", 0),
        "nota_actual_id": data.get("nota_actual_id"),
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

    _posiciones_choferes[chofer_id] = entrada

    # Calcular ETA para la próxima parada
    ruta_id = data.get("ruta_id", "default")
    ruta = _rutas_activas.get(ruta_id, {})
    paradas = ruta.get("paradas", [])
    eta = 0
    for p in paradas:
        if p.get("nota_id") == entrada["nota_actual_id"] or not p.get("entregada"):
            if entrada["lat"] and entrada["lng"] and p.get("longitud") and p.get("latitud"):
                eta = _ors_adapter.estimar_eta(
                    [entrada["lng"], entrada["lat"]],
                    [float(p["longitud"]), float(p["latitud"])]
                )
            break

    return jsonify({
        "status": "ok",
        "eta_minutos": eta,
        "timestamp": entrada["timestamp"]
    })


@app.route('/api/rutas/monitoreo_regente', methods=['GET'])
def rutas_monitoreo_regente():
    """Estado actual de todas las rutas activas + posiciones de choferes."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Contar entregas del día
        cursor.execute("""
            SELECT COUNT(*) as total FROM notas_entrega
            WHERE estado = 'entregado' AND date(fecha_creacion) = date('now')
        """)
        total_entregas = cursor.fetchone()["total"]

        cursor.execute("""
            SELECT COUNT(*) as total FROM notas_entrega
            WHERE estado = 'embalado'
        """)
        pendientes = cursor.fetchone()["total"]

        conn.close()
    except Exception:
        total_entregas = 0
        pendientes = 0

    choferes = []
    for cid, pos in _posiciones_choferes.items():
        vel = pos.get("velocidad", 0)
        if vel == 0:
            clase_vel = "quieto"
        elif vel < 20:
            clase_vel = "lento"
        else:
            clase_vel = "normal"

        # Buscar nota actual
        nota_actual = None
        for rid, ruta in _rutas_activas.items():
            for p in ruta.get("paradas", []):
                if str(p.get("nota_id")) == str(pos.get("nota_actual_id")):
                    nota_actual = p
                    break

        choferes.append({
            "chofer_id": cid,
            "lat": pos.get("lat"),
            "lng": pos.get("lng"),
            "velocidad": vel,
            "clase_velocidad": clase_vel,
            "nota_actual": nota_actual["cliente"] if nota_actual else "En tránsito",
            "timestamp": pos.get("timestamp", ""),
            "eta_minutos": None
        })

    # Calcular ETA para cada chofer
    for ch in choferes:
        if ch["lat"] and ch["lng"]:
            for _, ruta in _rutas_activas.items():
                for p in ruta.get("paradas", []):
                    if p["cliente"] == ch["nota_actual"]:
                        ch["eta_minutos"] = _ors_adapter.estimar_eta(
                            [ch["lng"], ch["lat"]],
                            [float(p["longitud"]), float(p["latitud"])]
                        )
                        break

    return jsonify({
        "total_entregas_hoy": total_entregas,
        "pendientes": pendientes,
        "rutas_activas": len(_rutas_activas),
        "choferes": choferes,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })


@app.route('/api/notas/estado', methods=['POST'])
def notas_actualizar_estado():
    """Actualiza el estado de una nota de entrega validando contra State Machine."""
    try:
        from notas_hexagonal import validar_transicion, TRANSICIONES_VALIDAS
    except ImportError:
        return jsonify({"error": "Módulo notas_hexagonal no disponible"}), 500
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON requerido"}), 400
    nota_id = data.get("nota_id")
    estado_destino = data.get("estado")
    if not nota_id or not estado_destino:
        return jsonify({"error": "nota_id y estado requeridos"}), 400
    try:
        conn = get_db_connection()
        row = conn.execute("SELECT estado FROM notas_entrega WHERE id = ?",
                           (nota_id,)).fetchone()
        if not row:
            return jsonify({"error": "Nota no encontrada"}), 404
        estado_actual = row['estado']
        if not validar_transicion(estado_actual, estado_destino):
            return jsonify({
                "error": f"Transición inválida: '{estado_actual}' → '{estado_destino}'. "
                         f"Permitidas: {TRANSICIONES_VALIDAS.get(estado_actual, [])}"
            }), 400
        conn.execute("UPDATE notas_entrega SET estado = ? WHERE id = ?",
                     (estado_destino, nota_id))
        conn.commit()
        conn.close()
        return jsonify({"status": "ok", "estado_anterior": estado_actual,
                        "estado_actual": estado_destino})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    PUERTO = 5000
    HOST_BIND = '0.0.0.0'
    print(f"🚀 Iniciando ARA Brain Middleware en http://{HOST_BIND}:{PUERTO}...")

    def obtener_ip_reales():
        """Detecta la IP local real de la máquina para no mostrar el genérico 0.0.0.0"""
        ips = ["127.0.0.1"]
        try:
            # Este truco abre un socket UDP ficticio para ver qué IP interna está usando la máquina
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip_red = s.getsockname()[0]
            s.close()
            if ip_red not in ips:
                ips.append(ip_red)
        except Exception:
            pass
        return ips

    # Obtener el mapa de IPs disponibles
    lista_ips = obtener_ip_reales()

    # 🔥 BANNER CRIMINAL EN CONSOLA
    print("\n" + "═" * 60)
    print(" 🤖 ¡SISTEMA ARA EN LÍNEA Y CORRIENDO, PERRO! 🤖")
    print("═" * 60)
    print("📌 Rutas de acceso disponibles:")
    print(f"   🏠 Local (Esta PC):  http://127.0.0.1:{PUERTO}")
    
    # Si detectó una IP de red (192.168.x.x o de Tailscale), la muestra aquí
    if len(lista_ips) > 1:
        print(f"   📱 Desde el Celular: http://{lista_ips[1]}:{PUERTO}")
    
    print("═" * 60)
    print("➔ Monitoreando peticiones en tiempo real...\n")

    # =========================================================================
    # MULTI-SERVER: Usa el que tengas activo (descomenta el tuyo y comenta el otro)
    # =========================================================================
    
    # Opción A: Si usas Waitress (Producción limpia)
    from waitress import serve
    serve(app, host=HOST_BIND, port=PUERTO)

    # Opción B: Si usas el server nativo de Flask (Modo Desarrollo)
    # app.run(host=HOST_BIND, port=PUERTO, debug=False)
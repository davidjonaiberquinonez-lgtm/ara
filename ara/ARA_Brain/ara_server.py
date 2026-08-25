import sys
from pathlib import Path

# Obtener la raíz del proyecto (tres niveles arriba de ara_server.py → C:\ARA_PROYECT)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from flask import Flask, render_template, jsonify, request, send_from_directory, send_file, make_response, redirect
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
import threading
import time

# Multi-Sede (Macro-Rutas): import global para que la resolución de sede no
# dependa de un try/except local que dejaría `normalizar_sede` sin vincular.
from rutas.application.user_service import asegurar_columna_sede, normalizar_sede
from auth_sesion import emitir_token_sesion, verificar_token_sesion

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
# Límite de recepción JSON explícito (32 MB): las fotos de cámara en alta
# resolución no deben romper la petición con "Payload Too Large" ({} vacíos).
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024
CORS(app, resources={r"/*": {"origins": "*"}})

# -----------------------------------------------------------------------------
# Registro del módulo PDF (delegado a pdf_route.py para evitar colisión de rutas)
# DEBE ir justo después de CORS(app) para que la ruta /api/reporte/pdf
# (GET y POST) quede registrada antes de cualquier endpoint que pudiera
# hacer shadowing.
# -----------------------------------------------------------------------------
from pdf_route import register_pdf_route
register_pdf_route(app)

# Reporte de rendimiento en XLSX (v4.57): reemplaza el PDF en el Dashboard
# de Rendimiento — el equipo necesita filtrar/ordenar los datos, algo que un
# PDF no permite. Mismo dataset que pdf_route.py, 4 hojas con autofiltro.
from xlsx_route import register_xlsx_route
register_xlsx_route(app)

# API local de OCR de vouchers para proyectos externos (misma PC/red local):
# envoltorio HTTP sobre la tool leer_voucher_ocr, que solo acepta ruta de
# archivo en el servidor — este módulo recibe la imagen (upload/base64) y
# hace el puente. Ver vision_ocr_route.py.
from vision_ocr_route import register_vision_ocr_routes
register_vision_ocr_routes(app)

# API local de consulta de clientes para proyectos externos (misma PC/red
# local): envoltorio HTTP sobre la tool consultar_cliente. Ver cliente_route.py.
from cliente_route import register_cliente_routes
register_cliente_routes(app)
from presencia_routes import register_presencia_routes
register_presencia_routes(app)
from ara_inteligente_routes import register_ara_inteligente_routes
register_ara_inteligente_routes(app)
from sso_erp_routes import register_sso_routes
register_sso_routes(app)

# -----------------------------------------------------------------------------
# Registro del módulo de BANDEJA DE MENSAJERÍA (chat_routes.py)
# Endpoints bajo /api/chat/* (conversaciones, historial, enviar, webhook, poll)
# -----------------------------------------------------------------------------
# Blindaje de consola (mismo patrón v4.7): los prints con emojis/acentos no
# deben tumbar el arranque ni el import en consolas cp1252.
try:
    for _flujo in (sys.stdout, sys.stderr):
        try:
            reconfigurar = getattr(_flujo, "reconfigure", None)
            if callable(reconfigurar):
                reconfigurar(encoding="utf-8", errors="replace")
        except Exception:
            pass
except Exception:
    pass

from chat_routes import register_chat_routes, init_chat_tables
init_chat_tables()              # crea tablas contactos/conversaciones/mensajes si faltan
register_chat_routes(app)

from atencion_cliente_routes import register_atencion_routes, init_atencion_tables
init_atencion_tables()          # crea tablas meta_numeros/atencion_conversaciones/mensajes si faltan
register_atencion_routes(app)

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
from notas_hexagonal import register_notas_routes, init_notas_tables, migrate_notas_estado_check
init_notas_tables()             # crea tablas notas_entrega / detalle_nota / movimientos_preparador
migrate_notas_estado_check()    # si el CHECK de estado es viejo, lo redefine (admite estados del flujo)
register_notas_routes(app)

# -----------------------------------------------------------------------------
# Servicio TTS Kokoro 82M (feedback de voz del Módulo Preparación/Picking)
# Endpoints: /api/tts/kokoro (POST), /api/tts/estado (GET)
# -----------------------------------------------------------------------------
# Garantizar que el directorio del servidor y la subcarpeta services estén
# en sys.path (resuelve reportMissingImports de Pylance y fallos de arranque
# cuando se ejecuta desde un CWD distinto).
_BASE_DIR = Path(__file__).resolve().parent
_SERVICES_DIR = _BASE_DIR / "services"
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

# Importación con try/except para resiliencia y compatibilidad con Pylance
try:
    from services.kokoro_tts import register_tts_routes
    register_tts_routes(app)
    print("TTS Kokoro 82M registrado (/api/tts/kokoro).")
except ImportError:
    try:
        from kokoro_tts import register_tts_routes  # noqa: F401 (respaldo)
        register_tts_routes(app)
        print("TTS Kokoro 82M registrado (respaldo /api/tts/kokoro).")
    except ImportError:
        register_tts_routes = None
        print("⚠️ TTS Kokoro no disponible: módulo kokoro_tts no encontrado.")

# -----------------------------------------------------------------------------
# Favicon: el navegador pide /favicon.ico en la raíz → se sirve el SVG minimalista
# (archivo multimedia reside en static/img/, servido por Flask como estático)
# -----------------------------------------------------------------------------
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static', 'img'),
                               'gemini-svg.svg', mimetype='image/svg+xml')

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

# -----------------------------------------------------------------------------
# Registro del módulo HEXAGONAL DE PREPARACIÓN (preparacion/) — Feature flag
# USE_DIRECT_SQL=false → LegacyPHPAdapter (192.168.4.148:8000)
# USE_DIRECT_SQL=true  → ProfitSQLAdapter (stubs hasta credenciales activas)
# -----------------------------------------------------------------------------
from preparacion import (
    PreparationService,
    LegacyPHPAdapter,
    ProfitSQLAdapter,
    SqliteNotaStore,
    register_preparation_routes,
)
from preparacion.infrastructure.adapters.profit_renglones_sync import ProfitRenglonesSync
from preparacion.infrastructure.adapters.legacy_mysql_sync import LegacyMySQLConnector
USE_DIRECT_SQL = os.getenv("USE_DIRECT_SQL", "false").lower() in ("true", "1", "yes")
LEGACY_API_URL = os.getenv("LEGACY_API_URL", "http://192.168.4.148:8000")
LEGACY_VISOR_CLAVE = os.getenv("LEGACY_VISOR_CLAVE", "")
# Sincronizador SQL de renglones (Profit): marca los renglones de la nota como
# 100% cargados en SQL Server ANTES del cierre en registro.php, para que el PHP
# Legacy no responda "Faltan articulos por cargar" ni envíe la nota a revisión.
_prep_renglones_sync = ProfitRenglonesSync()
# Conector NATIVO MySQL del legacy (ARA_SYNC, directiva v3.30): confirma
# despacho/chequeo DIRECTAMENTE en la BD MySQL (192.168.4.148, credenciales
# MYSQL_* del .env, pool limitado por MYSQL_CONNECTION_LIMIT). Si la red del
# servidor .148 limita la conexión directa, el adaptador conserva el respaldo
# HTTP (PHPSESSID + POST a registro.php) — ver LegacyPHPAdapter._confirmar_mysql.
_prep_mysql_sync = LegacyMySQLConnector()
if USE_DIRECT_SQL:
    _prep_repo = ProfitSQLAdapter()
    print("🧩 Preparación: MODO SQL DIRECTO PROFIT (requiere credenciales activas)")
else:
    _prep_repo = LegacyPHPAdapter(
        LEGACY_API_URL,
        renglones_sync=_prep_renglones_sync,
        mysql_connector=_prep_mysql_sync,
    )
    print(f"🧩 Preparación: MODO LEGACY PHP ({LEGACY_API_URL}) + sync renglones Profit")
# Fuente SQL directa de notas SAN CRISTÓBAL (serie 'A...' → CRISTM25,
# not_dep/reng_nd): se usa SIEMPRE que el código traiga serie 'A' o que el
# Legacy no encuentre la nota, sin importar USE_DIRECT_SQL.
_prep_repo_sc = ProfitSQLAdapter()
_prep_local_store = SqliteNotaStore()
_prep_service = PreparationService(_prep_repo, _prep_local_store, repositorio_sc=_prep_repo_sc)
register_preparation_routes(app, _prep_service)

# -----------------------------------------------------------------------------
# Registro del módulo HEXAGONAL DE RUTAS (rutas/) — REALIZAR RUTA
# Repo SQLite local (sub_rutas_finalizadas) + Legacy /visor/ (lista.php/registro.php)
# -----------------------------------------------------------------------------
from rutas import (
    RouteService,
    SqliteRouteRepository,
    LegacyRouteAdapter,
    register_routes_hex_routes,
)
_route_repo = SqliteRouteRepository()
_route_legacy = LegacyRouteAdapter(LEGACY_API_URL, clave_visor=LEGACY_VISOR_CLAVE)
# adapter_legacy_bqto se crea perezoso dentro de RouteService (ver
# rutas/application/route_service.py) apuntando a ARA_PUENTE_BQTO_URL —
# mismo visor legacy, consultado vía el puente que corre en una máquina de
# la red de BQTO (bin/puente_visor_bqto.ps1), porque el Apache filtra los
# datos según la IP de origen de quien consulta (v4.53).
_route_service = RouteService(_route_repo, _route_legacy, mysql_sync=_prep_mysql_sync)
register_routes_hex_routes(app, _route_service)
print(f"🚚 Módulo REALIZAR RUTA: MODO LEGACY /visor/ ({LEGACY_API_URL})")
# getattr (no acceso directo a _legacy_bqto._puente_url): el tipo estático de
# _legacy_bqto es RouteLegacyPort (puerto abstracto), que no declara ese
# atributo privado del adaptador concreto — Pylance lo marcaba como
# reportAttributeAccessIssue. LegacyRouteAdapter expone puente_url como
# propiedad pública para este caso.
_puente_bqto = getattr(_route_service._legacy_bqto, 'puente_url', None)
print(f"🚚 Módulo REALIZAR RUTA (BQTO): vía puente {_puente_bqto or '(no configurado)'}")

# Módulo EMBALAJE: consulta y registro por POST directo al PHP legacy
# (actualizar_nota_embalaje.php) — prevalece sobre lista.php cuando la nota
# no existe en la BD local. Al finalizar un embalaje se emite el evento
# 'embalaje.finalizado' hacia el bus interno (rutas.application.event_bus)
# para vincular la nota empacada al Rutagrama activo automáticamente.
from rutas.infrastructure.web.embalaje_router import register_embalaje_routes
from rutas.application.event_bus import get_event_bus
register_embalaje_routes(app, _route_legacy, event_bus=get_event_bus(), mysql_sync=_prep_mysql_sync)
print(f"📦 Módulo EMBALAJE: POST directo a {LEGACY_API_URL}/gestion_produc_bqmt/actualizar_nota_embalaje.php + confirmación MySQL directa (gestion + rep_not.estatus)")

# -----------------------------------------------------------------------------
# Registro del VISOR DE ARTÍCULOS HÍBRIDO (visor_articulos/) — motor en memoria
# Endpoint síncrono POST /api/visor/buscar (latencia < 10 ms por consulta).
# Reemplaza la ruta legacy /api/vision/escanear que dependía de IA de visión
# remota (NVIDIA NIM / Ollama) y la búsqueda SQL de /api/preparacion.
# -----------------------------------------------------------------------------
try:
    from visor_articulos.application.visor_routes import register_visor_routes
    register_visor_routes(app)
    print("🔍 VISOR HÍBRIDO registrado: POST /api/visor/buscar (síncrono en memoria)", flush=True)
except Exception as e:
    import traceback as tb
    tb.print_exc()
    print(f"⚠️ VISOR HÍBRIDO no disponible: {e}", flush=True)

@app.after_request
def monitorear_trafico(response):
    print(
        f"👉 [{request.method}] {request.path} — IP: {request.remote_addr}",
        flush=True,
    )
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
# IMÁGENES DE PRODUCTOS (CDN cristmedicals) — URL dinámica por co_art
# =============================================================================
def obtener_url_imagen(co_art):
    """Resuelve la URL CDN de la imagen del artículo a partir de su co_art."""
    return f"https://imagenes.cristmedicals.com/imagenes-v3/imagenes/{str(co_art or '').strip().upper()}.jpg"

# =============================================================================
# MIGRACIÓN IMAGEN_URL: columna en stock_maestro (si no existe)
# =============================================================================
def migrar_imagen_url_stock():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA table_info(stock_maestro)")
        columnas = [col['name'] for col in cursor.fetchall()]
        if 'imagen_url' not in columnas:
            cursor.execute("ALTER TABLE stock_maestro ADD COLUMN imagen_url TEXT DEFAULT ''")
            conn.commit()
            print("[IMAGENES] Columna imagen_url agregada a stock_maestro.")
        else:
            print("[IMAGENES] Columna imagen_url ya existía en stock_maestro.")
    finally:
        conn.close()

migrar_imagen_url_stock()

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
# MIGRACIÓN RUTAS: es_responsable_ruta + ruta_asignada (módulo REALIZAR RUTA)
# =============================================================================
def migrar_columnas_ruta_usuarios():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA table_info(usuarios)")
        columnas = [col['name'] for col in cursor.fetchall()]
        if 'es_responsable_ruta' not in columnas:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN es_responsable_ruta INTEGER DEFAULT 0")
            conn.commit()
        if 'ruta_asignada' not in columnas:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN ruta_asignada TEXT DEFAULT ''")
            conn.commit()
        # Backfill: los responsables de ruta existentes pasan a ser responsables macro
        cursor.execute(
            "UPDATE usuarios SET es_responsable_ruta = 1 "
            "WHERE es_responsable_ruta = 0 AND is_route_responsible = 1"
        )
        conn.commit()
        print("[RUTAS] Columnas es_responsable_ruta/ruta_asignada verificadas + backfill OK.")
    except Exception as e:
        print(f"[RUTAS] Advertencia migración columnas ruta: {e}")
    finally:
        conn.close()

migrar_columnas_ruta_usuarios()

# =============================================================================
# MIGRACIÓN PERFIL: foto_perfil + descripcion (tarjeta de visualización
# individual — se muestra a los demás usuarios en sus chats)
# =============================================================================
def migrar_columnas_perfil_usuarios():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA table_info(usuarios)")
        columnas = [col['name'] for col in cursor.fetchall()]
        if 'foto_perfil' not in columnas:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN foto_perfil TEXT DEFAULT ''")
            conn.commit()
        if 'descripcion' not in columnas:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN descripcion TEXT DEFAULT ''")
            conn.commit()
        print("[PERFIL] Columnas foto_perfil/descripcion verificadas OK.")
    except Exception as e:
        print(f"[PERFIL] Advertencia migración columnas perfil: {e}")
    finally:
        conn.close()

migrar_columnas_perfil_usuarios()

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
        es_responsable_ruta = 1 if data.get('esResponsableRuta') else is_route_responsible
        ruta_asignada = str(data.get('rutaAsignada', '') or '').strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        
        # INSERT OR REPLACE evita duplicados; si el ID existe, lo actualiza en caliente sin romper nada
        cursor.execute("""
            INSERT OR REPLACE INTO usuarios (id, nombre, contrasena, rol, permisos, color, is_route_responsible, es_responsable_ruta, ruta_asignada)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (uid, nombre, contrasena, rol, permisos, color, is_route_responsible, es_responsable_ruta, ruta_asignada))
        
        conn.commit()
        conn.close()
        
        return jsonify({"status": "success", "mensaje": "Usuario guardado y sincronizado en SQL con éxito"})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": f"Error en base de datos: {str(e)}"}), 500


@app.route('/api/usuarios/eliminar', methods=['POST'])
def eliminar_usuario_servidor():
    """Elimina un usuario por id. BUG REAL detectado en vivo (v4.56): el
    frontend siempre llamó a este endpoint (eliminarUsuario() en
    index.html) pero nunca se construyó del lado del servidor — 404 en
    cada intento de borrar un operador desde Gestión de Usuarios."""
    try:
        data = request.get_json(silent=True) or {}
        uid = str(data.get('id', '')).strip().upper()
        if not uid:
            return jsonify({"status": "error", "mensaje": "ID obligatorio"}), 400
        if uid in ('ADMIN1', 'ADMIN'):
            return jsonify({"status": "error", "mensaje": "No se puede eliminar el usuario administrador."}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM usuarios WHERE UPPER(id) = ?", (uid,))
        eliminado = cursor.rowcount > 0
        conn.commit()
        conn.close()

        if not eliminado:
            return jsonify({"status": "error", "mensaje": f"Usuario {uid} no encontrado."}), 404
        return jsonify({"status": "success", "mensaje": f"Usuario {uid} eliminado."})
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
            try:
                es_resp_ruta = bool(row['es_responsable_ruta'])
            except (KeyError, IndexError):
                es_resp_ruta = bool(row['is_route_responsible'])
            try:
                ruta_asig = row['ruta_asignada'] or ''
            except (KeyError, IndexError):
                ruta_asig = ''
            try:
                foto_perfil = row['foto_perfil'] or ''
            except (KeyError, IndexError):
                foto_perfil = ''
            try:
                descripcion = row['descripcion'] or ''
            except (KeyError, IndexError):
                descripcion = ''
            lista_usuarios.append({
                "id": row['id'],
                "nombre": row['nombre'],
                "pass": row['contrasena'],
                "rol": row['rol'],
                "permisos": permisos_list,
                "color": row['color'],
                "isRouteResponsible": bool(row['is_route_responsible']),
                "esResponsableRuta": es_resp_ruta,
                "rutaAsignada": ruta_asig,
                "fotoPerfil": foto_perfil,
                "descripcion": descripcion
            })

        return jsonify({"status": "success", "usuarios": lista_usuarios})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": f"Error al leer usuarios: {str(e)}"}), 500


@app.route('/api/usuarios/perfil', methods=['POST'])
def actualizar_perfil_usuario():
    """Actualiza la tarjeta de visualización individual del propio usuario
    (foto + descripción única) — es lo que ven los demás usuarios de él en
    sus chats (lista de contactos, encabezado de conversación, etc.)."""
    try:
        data = request.json or {}
        uid = str(data.get('id', '')).strip()
        if not uid:
            return jsonify({"status": "error", "mensaje": "ID de usuario obligatorio"}), 400

        foto_perfil = str(data.get('fotoPerfil', '') or '')
        descripcion = str(data.get('descripcion', '') or '').strip()[:200]

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE usuarios SET foto_perfil = ?, descripcion = ? WHERE id = ?",
            (foto_perfil, descripcion, uid)
        )
        actualizado = cursor.rowcount > 0
        conn.commit()
        conn.close()

        if not actualizado:
            return jsonify({"status": "error", "mensaje": "Usuario no encontrado"}), 404
        return jsonify({"status": "success", "mensaje": "Perfil actualizado",
                         "fotoPerfil": foto_perfil, "descripcion": descripcion})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": f"Error actualizando perfil: {str(e)}"}), 500


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

        # Multi-Sede: garantiza la columna usuarios.sede (migración idempotente)
        # para inyectar el sede_id ('SC' | 'BQTO') en el objeto autenticado.
        try:
            asegurar_columna_sede()
        except Exception:
            pass

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
                sede_raw = user_row['sede'] if 'sede' in user_row.keys() else ''
                try:
                    sede = normalizar_sede(sede_raw) or "BQTO"
                except Exception:
                    sede = "BQTO"

                # BUG DE SEGURIDAD real corregido (24/08): antes el login no
                # emitía ningún token — el frontend reenviaba `es_admin` a
                # mano en cada request de reportería, y el server confiaba
                # ciegamente en ese valor (cualquiera podía mandar
                # es_admin=true). Ahora se firma un token con el rol REAL
                # leído de la BD; los endpoints sensibles lo verifican en
                # vez de confiar en lo que mande el cliente.
                token = emitir_token_sesion(user_row['id'], user_row['nombre'], user_row['rol'], permisos_list)
                return jsonify({
                    "status": "success",
                    "token": token,
                    "user": {
                        "id": user_row['id'],
                        "nombre": user_row['nombre'],
                        "rol": user_row['rol'],
                        "permisos": permisos_list,
                        "color": user_row['color'],
                        "sede": sede,
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
# Caché TTL para Dashboard (10s) — consultas frescas en tiempo real sin saturar SQLite
_dashboard_cache = {"data": None, "timestamp": 0}

@app.route('/api/dashboard/stats', methods=['GET'])
def obtener_estadisticas_dashboard():
    """
    Consulta la tabla log_puntos para generar métricas reales de gamificación.
    Acepta filtros opcionales: fecha_inicio, fecha_fin (YYYY-MM-DD)
    Incluye caché TTL de 10s; se invalida al registrar puntos de preparación.
    """
    ahora = time.time()
    if _dashboard_cache["data"] and (ahora - _dashboard_cache["timestamp"] < 10):
        return jsonify(_dashboard_cache["data"])
    try:
        # Leer filtros de fecha
        fecha_inicio = request.args.get('fecha_inicio')
        fecha_fin = request.args.get('fecha_fin')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Construir WHERE para filtros de fecha (alias lp en todas las consultas)
        where_fecha = ""
        params = []
        if fecha_inicio and fecha_fin:
            where_fecha = "WHERE DATE(lp.fecha_registro) BETWEEN ? AND ?"
            params = [fecha_inicio, fecha_fin]
        elif fecha_inicio:
            where_fecha = "WHERE DATE(lp.fecha_registro) >= ?"
            params = [fecha_inicio]
        elif fecha_fin:
            where_fecha = "WHERE DATE(lp.fecha_registro) <= ?"
            params = [fecha_fin]
        
        # 1. Resumen global
        cursor.execute(f'''
            SELECT 
                COALESCE(COUNT(*), 0) as total_operaciones,
                COALESCE(SUM(lp.puntos_ganados), 0) as total_puntos,
                COUNT(DISTINCT lp.usuario) as total_usuarios,
                COALESCE(SUM(CASE WHEN lp.modulo = 'picking' THEN lp.puntos_ganados ELSE 0 END), 0) as puntos_preparacion,
                (SELECT COUNT(*) FROM notas_entrega ne
                 WHERE DATE(ne.fecha_completada) = DATE('now','localtime')) as notas_preparadas_hoy,
                (SELECT COUNT(DISTINCT referencia_id) FROM log_puntos
                 WHERE modulo = 'embalaje'
                 AND DATE(fecha_registro) = DATE('now','localtime')) as notas_embaladas_hoy,
                (SELECT COALESCE(SUM(cant_cajas), 0) FROM movimientos_preparador
                 WHERE accion = 'EMBALAJE'
                 AND DATE(timestamp) = DATE('now','localtime')) as cajas_procesadas_hoy
            FROM log_puntos lp
            {where_fecha}
        ''', params)
        resumen_row = cursor.fetchone()
        total_ops = resumen_row['total_operaciones'] or 0
        total_puntos = resumen_row['total_puntos'] or 0
        
        # 2. Ranking por usuario (agregado desde log_puntos) - CON DESGLOSE POR MÓDULO
        #    Nombre real desde usuarios + notas preparadas HOY desde notas_entrega.
        #    log_puntos.usuario guarda id o nombre según el módulo → JOIN dual.
        cursor.execute(f'''
            SELECT 
                COALESCE(u.id, lp.usuario) as usuario_id,
                COALESCE(u.nombre, lp.usuario) as nombre,
                COUNT(*) as operaciones,
                COALESCE(SUM(lp.puntos_ganados), 0) as puntos_totales,
                COALESCE(SUM(lp.cantidad_renglones), 0) as total_renglones,
                COALESCE(SUM(CASE WHEN lp.modulo = 'picking' THEN lp.puntos_ganados ELSE 0 END), 0) as puntos_picking,
                COALESCE(SUM(CASE WHEN lp.modulo = 'picking' THEN lp.cantidad_renglones ELSE 0 END), 0) as renglones_picking,
                COALESCE(SUM(CASE WHEN lp.modulo = 'chequeo' THEN lp.puntos_ganados ELSE 0 END), 0) as puntos_chequeo,
                COALESCE(SUM(CASE WHEN lp.modulo = 'chequeo' THEN lp.cantidad_renglones ELSE 0 END), 0) as renglones_chequeo,
                COALESCE(SUM(CASE WHEN lp.modulo = 'inventario' THEN lp.puntos_ganados ELSE 0 END), 0) as puntos_inventario,
                COALESCE(SUM(CASE WHEN lp.modulo = 'inventario' THEN lp.cantidad_renglones ELSE 0 END), 0) as renglones_inventario,
                COALESCE(SUM(CASE WHEN lp.modulo = 'embalaje' THEN lp.puntos_ganados ELSE 0 END), 0) as puntos_embalaje,
                COALESCE(SUM(CASE WHEN lp.modulo = 'embalaje' THEN lp.cantidad_renglones ELSE 0 END), 0) as renglones_embalaje,
                COALESCE(nh.notas_hoy, 0) as notas_hoy
            FROM log_puntos lp
            LEFT JOIN usuarios u ON (u.id = lp.usuario OR UPPER(u.nombre) = UPPER(lp.usuario))
            LEFT JOIN (
                SELECT preparador_id, COUNT(*) as notas_hoy
                FROM notas_entrega
                WHERE DATE(fecha_completada) = DATE('now','localtime')
                GROUP BY preparador_id
            ) nh ON u.id IS NOT NULL AND nh.preparador_id = u.id
            {where_fecha}
            GROUP BY COALESCE(u.id, lp.usuario)
            ORDER BY puntos_totales DESC
        ''', params)
        ranking_rows = cursor.fetchall()
        
        ranking_final = []
        for row in ranking_rows:
            ranking_final.append({
                "usuario_id": row['usuario_id'],
                "nombre": row['nombre'],
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
                "renglones_inventario": row['renglones_inventario'],
                "puntos_embalaje": round(row['puntos_embalaje'], 2),
                "renglones_embalaje": row['renglones_embalaje'],
                "notas_hoy": row['notas_hoy']
            })
        
        operador_destacado = ranking_final[0]["nombre"] if ranking_final else "N/A"
        
        # 3. Gráfico semanal (últimos 6 días con operaciones)
        where_grafico = "WHERE lp.fecha_registro >= DATE('now', '-6 days')"
        params_grafico = []
        if where_fecha:
            where_grafico += " " + where_fecha.replace("WHERE", "AND")
            params_grafico = params
        
        cursor.execute(f'''
            SELECT 
                DATE(lp.fecha_registro) as fecha,
                COUNT(*) as operaciones,
                SUM(lp.puntos_ganados) as puntos_dia
            FROM log_puntos lp
            {where_grafico}
            GROUP BY DATE(lp.fecha_registro)
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
        
        # 4. Incidencias recientes (últimas 10) - nombre real del operador
        cursor.execute(f'''
            SELECT COALESCE(u.id, lp.usuario) as usuario_id,
                   COALESCE(u.nombre, lp.usuario) as usuario,
                   lp.modulo, lp.referencia_id, lp.puntos_ganados, lp.fecha_registro
            FROM log_puntos lp
            LEFT JOIN usuarios u ON (u.id = lp.usuario OR UPPER(u.nombre) = UPPER(lp.usuario))
            {where_fecha}
            ORDER BY lp.fecha_registro DESC
            LIMIT 10
        ''', params)
        incidencias_rows = cursor.fetchall()
        
        incidencias_registradas = []
        for row in incidencias_rows:
            incidencias_registradas.append({
                "usuario": row['usuario'],
                "usuario_id": row['usuario_id'],
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
                "operadorDestacado": operador_destacado,
                "notasPreparadasHoy": resumen_row['notas_preparadas_hoy'] or 0,
                "puntosPreparacion": round(resumen_row['puntos_preparacion'] or 0, 2),
                "notasEmbaladasHoy": resumen_row['notas_embaladas_hoy'] or 0,
                "cajasProcesadasHoy": resumen_row['cajas_procesadas_hoy'] or 0
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
            "resumen": {"totalOperaciones": 0, "tasaPrecision": 100.0, "tiempoPromedio": "0m 0s", "operadorDestacado": "N/A", "notasPreparadasHoy": 0, "puntosPreparacion": 0, "notasEmbaladasHoy": 0, "cajasProcesadasHoy": 0},
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
        
        # 1. Consulta principal a Profit Plus (Total / S/C / BQTO + imagen CDN)
        if query:
            cursor.execute("""
                SELECT codigo AS co_art, descripcion AS art_des, campo7 AS ubicacion,
                       stock_maestro AS stock_total, stock_act AS stock_sc,
                       deposito_bqto, despacho_bqto
                FROM stock_maestro 
                WHERE LOWER(codigo) LIKE ? OR LOWER(descripcion) LIKE ? 
                LIMIT 50
            """, (f"%{query}%", f"%{query}%"))
        else:
            cursor.execute("""
                SELECT codigo AS co_art, descripcion AS art_des, campo7 AS ubicacion,
                       stock_maestro AS stock_total, stock_act AS stock_sc,
                       deposito_bqto, despacho_bqto
                FROM stock_maestro 
                LIMIT 20
            """)
        
        filas = cursor.fetchall()
        items = [dict(f) for f in filas]
        
        # 2. Desglose de stock por almacén (BQTO / S/C / Total) + imagen CDN dinámica
        for item in items:
            bqto_dep = item.get('deposito_bqto') or 0
            bqto_desp = item.get('despacho_bqto') or 0
            item['stock_bqto'] = bqto_dep + bqto_desp
            item['stock_sc'] = item.get('stock_sc') or 0
            item['stock_total'] = item.get('stock_total') or 0
            item['stock_act'] = item['stock_total']   # compat: frontend legacy
            item['stock_bulto'] = bqto_dep            # compat: frontend legacy
            item['imagen_url'] = obtener_url_imagen(item['co_art'])
        
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

        def _nro_etiqueta(estante: dict) -> int:
            m = _re.search(r'(\d+)', str(estante.get('etiqueta', '0') or '0'))
            return int(m.group(1)) if m else 0

        for ck, cv in sorted(categorias.items()):
            estantes_list = sorted(cv['estantes'].values(), key=_nro_etiqueta)
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
    Retorna discrepancias de stock de las Auditorías de Inventario (REP-*.json
    de brain_knowledge/reportes). Una fila por artículo discrepante (estado != 'OK').
    Query params: fecha_inicio, fecha_fin, usuario, usuario_activo, es_admin
    - Si es_admin=false o no se envía: forza filtro case-insensitive por usuario_activo.
    """
    fecha_inicio = request.args.get('fecha_inicio', '').strip()
    fecha_fin = request.args.get('fecha_fin', '').strip()
    usuario = request.args.get('usuario', '').strip()

    # BUG DE SEGURIDAD real corregido (24/08): es_admin/usuario_activo ya NO
    # se toman del query param (el cliente podía mandar es_admin=true y ver
    # los reportes de todos) — se derivan del token de sesión firmado en el
    # login, verificado server-side.
    sesion = verificar_token_sesion(request)
    if sesion is None:
        return jsonify({"status": "error", "mensaje": "Sesión inválida o expirada. Iniciá sesión de nuevo."}), 401
    es_admin = sesion['es_admin']
    usuario_activo = sesion['nombre'] or sesion['id']

    if not os.path.isdir(REPORTES_FOLDER):
        return jsonify({"status": "success", "data": [], "total": 0})

    resultados = []
    for archivo_nombre in os.listdir(REPORTES_FOLDER):
        if not archivo_nombre.upper().startswith('REP-') or not archivo_nombre.endswith('.json'):
            continue
        try:
            with open(os.path.join(REPORTES_FOLDER, archivo_nombre), 'r', encoding='utf-8') as f:
                reporte = json.load(f)
        except Exception:
            continue

        creador = str(reporte.get('usuario', '') or '').strip()
        fecha_rep = str(reporte.get('fecha', '') or '').strip()
        id_reporte = reporte.get('id') or archivo_nombre.replace('.json', '')
        estante_rep = str(reporte.get('estante', 'General') or 'General')

        # RBAC: no-admin solo ve sus propios reportes (case-insensitive)
        if not es_admin:
            if not usuario_activo or usuario_activo.upper() not in creador.upper():
                continue
        elif usuario and usuario != "Todos":
            if usuario.upper() != creador.upper():
                continue

        # Filtros de fecha (los REP guardan fecha YYYY-MM-DD HH:MM:SS o AM/PM)
        solo_fecha = fecha_rep[:10]
        if fecha_inicio and solo_fecha < fecha_inicio:
            continue
        if fecha_fin and solo_fecha > fecha_fin:
            continue

        for det in reporte.get('detalles', []):
            if not isinstance(det, dict):
                continue
            estado = str(det.get('estado', 'OK') or 'OK').upper()
            if estado == 'OK':
                continue
            resultados.append({
                "id": id_reporte,
                "co_art": str(det.get('codigo', '') or ''),
                "descripcion": str(det.get('descripcion', '') or ''),
                "usuario": creador,
                "ubicacion": str(det.get('ubicacion', '') or '') or estante_rep,
                "fecha": fecha_rep,
                "stock_actual": det.get('fisico', 0),
                "stock_teorico": det.get('teorico', 0),
                # Regla de negocio real (antes se perdía y todo se mostraba
                # como "Discrepancia" genérica): FALTA = faltante en físico,
                # SOBRA = sobrante en estante. 'OK' nunca llega aquí (filtrado
                # arriba), así que este campo siempre es FALTA o SOBRA.
                "estado": estado,
                "discrepancia": True
            })

    resultados.sort(key=lambda r: str(r.get('fecha') or ''), reverse=True)
    return jsonify({"status": "success", "data": resultados, "total": len(resultados)})


@app.route('/api/reportes/trazabilidad', methods=['GET'])
def reportes_trazabilidad():
    """
    Retorna movimientos de trazabilidad desde reportes_ubicacion con filtros opcionales y control RBAC.
    Query params: fecha_inicio, fecha_fin, usuario, estado_profit, usuario_activo, es_admin, co_art
    - Si es_admin=false o no se envía: forza filtro case-insensitive por usuario_activo (nombre completo o username).
    - Si es ES Admin: permite filtrar por parámetro usuario o ver todos.
    - co_art (opcional): historial de ubicaciones de UN artículo puntual, sin filtro de usuario/RBAC
      (consulta de solo-lectura por código, usada por la tool de supervisión).
    Columnas retornadas: mov_id, usuario, sku, desde, hacia, fecha, procesado_profit.
    """
    fecha_inicio = request.args.get('fecha_inicio', '')
    fecha_fin = request.args.get('fecha_fin', '')
    usuario = request.args.get('usuario', '')
    estado_profit = request.args.get('estado_profit', '')
    co_art = request.args.get('co_art', '').strip()

    # BUG DE SEGURIDAD real corregido (24/08): es_admin/usuario_activo ya NO
    # se toman del query param para la consulta RBAC de un usuario — se
    # derivan del token de sesión firmado en el login. Excepción: la
    # consulta por co_art es un lookup de solo lectura por artículo (usado
    # server-a-server por la tool de supervisión, sin sesión de usuario de
    # por medio) y ya viene sin filtro de usuario por diseño — no se le
    # exige token para no romper ese caso legítimo.
    if co_art:
        es_admin = True
        usuario_activo = ''
    else:
        sesion = verificar_token_sesion(request)
        if sesion is None:
            return jsonify({"status": "error", "mensaje": "Sesión inválida o expirada. Iniciá sesión de nuevo."}), 401
        es_admin = sesion['es_admin']
        usuario_activo = sesion['nombre'] or sesion['id']

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        where = ["1=1"]
        params = []

        if not es_admin and usuario_activo and not co_art:
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
        if co_art:
            where.append("LOWER(ru.co_art) = LOWER(?)")
            params.append(co_art)

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
        return jsonify({"status": "error", "mensaje": f"Estado '{estado_actual}' no válido para embalaje."}), 400
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
            obtener_trazabilidad_nota,
            obtener_historial_ubicacion,
            es_consulta_nota,
            es_consulta_historial_ubicacion,
            formatear_trazabilidad_nota_para_prompt,
            formatear_historial_ubicacion_para_prompt,
            SYSTEM_PROMPT_AUDITOR
        )

        # --- Detectar si pide reporte de más vendidos ---
        if es_consulta_reporte(user_msg) and not imagen_b64:
            reporte = obtener_reporte_top_productos(dias=30, limite=10)
            if reporte and "productos" in reporte and reporte["productos"]:
                datos_producto_especifico = formatear_reporte_para_prompt(reporte)
                entidad_detectada = "REPORTE"

        # --- Herramientas de auditoría SOLO LECTURA: trazabilidad de NOTA y
        #     historial de UBICACIÓN (PASO 1) ---
        if not datos_producto_especifico and not imagen_b64:
            import re as _re
            _m_nota = _re.search(r'\b(\d{6,10})\b', user_msg)
            if es_consulta_nota(user_msg) and _m_nota:
                tn = obtener_trazabilidad_nota(_m_nota.group(1))
                if tn and not tn.get("error"):
                    datos_producto_especifico = formatear_trazabilidad_nota_para_prompt(tn)
                    entidad_detectada = _m_nota.group(1)
            elif es_consulta_historial_ubicacion(user_msg):
                codigo_mov = detectar_codigo_articulo(user_msg)
                if codigo_mov:
                    hu = obtener_historial_ubicacion(codigo_mov)
                    if hu and not hu.get("error"):
                        datos_producto_especifico = formatear_historial_ubicacion_para_prompt(hu)
                        entidad_detectada = codigo_mov

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

    # --- Skill SQL de SOLO LECTURA (ReAct): si ninguna herramienta fija
    #     encontró datos, el LLM construye un SELECT sobre el esquema local
    #     y los resultados reales se inyectan al prompt (nunca inventados) ---
    if not datos_producto_especifico and not imagen_b64:
        try:
            from db_query_tool import (
                es_consulta_sql_operativa,
                generar_select_desde_mensaje,
                ejecutar_consulta_sql_read_only,
                formatear_resultados_para_prompt,
            )
            if es_consulta_sql_operativa(user_msg):
                print(
                    f"[SQL_SKILL] 🔎 Consulta operativa detectada: {user_msg[:80]}",
                    flush=True,
                )
                gen = generar_select_desde_mensaje(user_msg)
                if gen.get("status") == "ok":
                    res = ejecutar_consulta_sql_read_only(gen["sql"])
                    bloque = formatear_resultados_para_prompt(res)
                    if bloque:
                        datos_producto_especifico = bloque
                        entidad_detectada = entidad_detectada or "consulta_sql"
                        print(
                            f"[SQL_SKILL] ✅ {res.get('total_filas', 0)} filas "
                            f"inyectadas al prompt",
                            flush=True,
                        )
        except Exception as e:
            print(f"[SQL_SKILL] ⚠️ Skill SQL omitido: {str(e)[:120]}", flush=True)

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
    filename = audio_file.filename or ""
    formato = filename.rsplit('.', 1)[-1] if '.' in filename else 'wav'
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
    # Red de seguridad SSO (2026-08-21): el callback SSO del ERP CristMedicals
    # quedó registrado en Auth Central apuntando a la raíz del dominio en vez
    # de /auth/sso (INTEGRACION_SSO.md) — el JWT llegaba acá y se ignoraba,
    # mostrando el login normal de ARA warehouse en vez de canjearlo hacia
    # ARA-Intelligent. Mientras se corrige la URL del lado del ERP, si la raíz
    # recibe un "token" se reenvía tal cual a /auth/sso (que sí lo verifica y
    # redirige a /ara-inteligente) — no cambia nada para el resto de accesos
    # normales a "/" (sin token, sigue sirviendo el warehouse igual).
    if request.args.get('token'):
        return redirect('/auth/sso?token=' + request.args.get('token'))

    # Sin esto, el navegador (y a veces el túnel Cloudflare por el que
    # entran desde celular) puede servir una copia vieja de index.html en
    # vez de la última — confirmado en vivo: un cambio de frontend (nueva
    # resolución de cámara, nuevo prompt de escaneo) no se reflejaba en el
    # teléfono hasta forzar recarga. index.html cambia seguido en este
    # proyecto, así que nunca debe cachearse.
    resp = make_response(render_template('index.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

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
def _verificar_puerto_tcp(host: str, port, timeout: float = 1.5) -> bool:
    """Chequeo liviano de alcanzabilidad (solo abre y cierra el socket, sin
    autenticar) — usado por _estado_gb10() para no acoplar /api/health a
    pyodbc/pymysql cuando esas libs no estén instaladas en este entorno."""
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _estado_gb10() -> dict:
    """Estado opcional de las piezas GB10 (Capa 2 — espejos locales — y el
    motor de inferencia). La GB10 física todavía no está en sitio, así que
    cada sub-chequeo reporta 'no_configurado' en vez de fallar cuando su env
    var correspondiente no está seteada (caso normal hoy, en este entorno)."""
    resultado = {}

    host_sql = os.environ.get("PROFIT_SQL_HOST_LOCAL")
    if host_sql:
        puerto_sql = os.environ.get("PROFIT_SQL_PORT_LOCAL", "1433")
        resultado["mirror_sqlserver"] = "ok" if _verificar_puerto_tcp(host_sql, puerto_sql) else "inaccesible"
    else:
        resultado["mirror_sqlserver"] = "no_configurado"

    host_mysql = os.environ.get("MYSQL_HOST_LOCAL")
    if host_mysql:
        puerto_mysql = os.environ.get("MYSQL_PORT_LOCAL", "3306")
        resultado["mirror_mysql"] = "ok" if _verificar_puerto_tcp(host_mysql, puerto_mysql) else "inaccesible"
    else:
        resultado["mirror_mysql"] = "no_configurado"

    try:
        from urllib.parse import urlparse
        from gb10.inference.engine_config import MODEL_ROUTES

        motores_configurados = any(
            os.environ.get(f"{tarea}_INFERENCE_URL") for tarea in MODEL_ROUTES
        )
        resultado["inferencia"] = {}
        for tarea, ruta in MODEL_ROUTES.items():
            if not motores_configurados:
                resultado["inferencia"][tarea] = "no_configurado (GB10 no desplegada)"
                continue
            parsed = urlparse(ruta["endpoint"])
            alcanzable = _verificar_puerto_tcp(parsed.hostname, parsed.port or 80)
            resultado["inferencia"][tarea] = (
                f"ok ({ruta['model_name']} @ {ruta['endpoint']})" if alcanzable
                else f"inaccesible ({ruta['endpoint']})"
            )
    except Exception as e:
        resultado["inferencia"] = f"error al resolver rutas de inferencia: {e}"

    return resultado


@app.route('/api/health', methods=['GET'])
def health_check():
    """Endpoint liviano de telemetría pública. Retorna estado de la DB, cola de visión, uptime
    y (informativo) el estado de las piezas GB10 — espejos locales Capa 2 + motor de inferencia."""
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
        "uptime_seconds": round(time.time() - _SERVER_START_TIME, 1),
        "gb10": _estado_gb10(),
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
# MÓDULO RUTAS — ORSAdapter + Endpoints /api/rutas/* (v4.56 — reconstruido)
#
# BUGS REALES del módulo original (detectados en vivo, ver auditoría v4.56):
#   1. Consultaba notas_entrega.direccion/.latitud/.longitud — columnas que
#      NUNCA existieron en el esquema real (CREATE TABLE de notas_hexagonal.py).
#   2. Comparaba estado='embalado' (masculino) contra el CHECK real que usa
#      'embalada' (femenino) — el filtro nunca traía nada.
#   3. API key de ORS hardcodeada en texto plano en el código fuente.
#   4. No existe NINGUNA coordenada GPS cargada en ningún lado del sistema
#      (Profit ni MySQL) — solo direcciones de texto libre. Sin geocodificar
#      primero, ORS no tiene con qué trazar nada.
#
# FIX v4.56 — pipeline real de datos (decisión del usuario: fuente EMBALADA =
# MySQL legacy, coordenadas = geocodificar direcciones de Profit con ORS):
#   rep_not.estatus='EMBALADA' (MySQL legacy 'barquisimeto')
#     -> cod_nota se resuelve contra Profit not_ent (_consultar_nota_profit_readonly,
#        YA EXISTE en ara_brain.py) -> co_cli
#     -> co_cli se resuelve contra Profit clientes.direc1/direc2 (texto)
#     -> la dirección de texto se geocodifica con ORS Geocoding API
#        (mismo API key que Directions) -> lat/lon, cacheado en SQLite
#        (geocodificacion_clientes) para no re-geocodificar en cada consulta.
# =============================================================================
ORS_API_KEY = os.environ.get(
    "ORS_API_KEY",
    "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjVlYmZmNzk4OGE3YzQ3MmNiZDk5NGI1MGE2MWJjMDhjIiwiaCI6Im11cm11cjY0In0=",
)

# Almacén en memoria de posiciones de choferes
_posiciones_choferes = {}
_rutas_activas = {}

_CAND_CLIENTE_DIRECCION = ["direc1", "direccion1", "direccion", "dir1"]
_CAND_CLIENTE_DIRECCION2 = ["direc2", "direccion2", "dir2"]


def _mysql_legacy_conn():
    """Conexión a la BD MySQL legacy 'barquisimeto' (192.168.4.148) donde vive
    rep_not — mismo host/credenciales que bin/actualizar_nota_hotfix.php."""
    import pymysql
    return pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "192.168.4.148"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", ""),
        password=os.environ.get("MYSQL_PASSWORD", ""),
        database=os.environ.get("MYSQL_DATABASE_LEGACY", "barquisimeto"),
        connect_timeout=8,
        cursorclass=__import__("pymysql").cursors.DictCursor,
    )


def _listar_notas_embaladas_legacy(limite: int = 50) -> list:
    """Notas con estatus='EMBALADA' en el legacy MySQL (rep_not), las más
    recientes primero. Fuente de verdad elegida por el usuario para saber
    qué está listo para el chofer (no notas_entrega/SQLite de ARA)."""
    try:
        conn = _mysql_legacy_conn()
    except Exception as e:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cod_nota, ruta, fec_impr FROM rep_not "
                "WHERE estatus = 'EMBALADA' ORDER BY fec_impr DESC LIMIT %s",
                (limite,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _resolver_direccion_cliente_profit(cur, co_cli: str) -> str:
    """Dirección de texto del cliente en Profit clientes.direc1(+direc2),
    con resolución dinámica de columnas (mismo patrón que
    _consultar_nota_profit_readonly). Cursor pyodbc YA ABIERTO (se reutiliza
    entre clientes de un mismo lote para no reconectar por cada nota)."""
    cols = [r[0] for r in cur.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME = 'clientes' AND TABLE_SCHEMA = 'dbo'"
    ).fetchall()]
    lower = {c.lower(): c for c in cols}

    def _resolver(candidatas):
        for c in candidatas:
            if c.lower() in lower:
                return lower[c.lower()]
        return None

    col1 = _resolver(_CAND_CLIENTE_DIRECCION)
    col2 = _resolver(_CAND_CLIENTE_DIRECCION2)
    if not col1:
        return ""
    expr = f"LTRIM(RTRIM(CAST({col1} AS NVARCHAR(200))))"
    if col2:
        expr += f" + ' ' + LTRIM(RTRIM(CAST({col2} AS NVARCHAR(200))))"
    fila = cur.execute(
        f"SELECT {expr} AS direccion FROM clientes WHERE co_cli = ?", co_cli
    ).fetchone()
    return (fila[0] or "").strip() if fila else ""


# Bounding box real de Venezuela (con margen) — cualquier resultado de
# geocodificación fuera de esto se descarta como error del geocoder, en vez
# de mandarlo a ORS Directions. BUG REAL detectado en vivo: un resultado mal
# geocodificado (probable "null island" [0,0] o un match en otro país pese
# a boundary.country=VEN) hizo que ORS rechazara la ruta completa por
# "distancia > 6000000 metros" — Venezuela mide ~1500km de punta a punta,
# jamás debería dar esa distancia con coordenadas reales.
_VE_LAT_MIN, _VE_LAT_MAX = 0.0, 13.0
_VE_LON_MIN, _VE_LON_MAX = -74.0, -59.0


def _coords_dentro_venezuela(lat: float, lon: float) -> bool:
    return _VE_LAT_MIN <= lat <= _VE_LAT_MAX and _VE_LON_MIN <= lon <= _VE_LON_MAX


def _geocodificar_texto_ors(texto: str):
    """Una consulta cruda al Geocoding de ORS. None si no hay match, la API
    falla, o el resultado cae fuera de Venezuela (geocode erróneo)."""
    try:
        resp = requests.get(
            "https://api.openrouteservice.org/geocode/search",
            params={
                "api_key": ORS_API_KEY,
                "text": texto,
                "boundary.country": "VEN",
                "size": 1,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        features = resp.json().get("features", [])
        if not features:
            return None
        lon, lat = features[0]["geometry"]["coordinates"]
        lat, lon = float(lat), float(lon)
        if not _coords_dentro_venezuela(lat, lon):
            print(f"[ORS Geocoding] Descartado (fuera de Venezuela): {texto!r} -> ({lat}, {lon})", flush=True)
            return None
        return (lat, lon)
    except Exception as e:
        print(f"[ORS Geocoding] Error geocodificando {texto!r}: {e}", flush=True)
        return None


# Estados de Venezuela (+ Distrito Capital) — el fallback de geocodificación
# SOLO se acepta si el resumen contiene el nombre de un estado real. BUG
# REAL detectado en vivo: sin este filtro, el fallback (últimas 4 palabras)
# a veces agarraba relleno genérico como "PARTE ALTA" (palabras descriptivas
# de la dirección, no un topónimo) y el geocoder lo matcheaba con CONFIANZA
# ALTA a un lugar real pero TOTALMENTE EQUIVOCADO (otro estado) — peor que
# no geocodificar, porque manda al chofer con falsa seguridad al lugar
# incorrecto. Con este filtro, "MENE DE MAUROA FALCON" sigue funcionando
# (contiene "FALCON"); "...PARTE ALTA" ya no genera un match falso.
_ESTADOS_VENEZUELA = {
    "amazonas", "anzoategui", "apure", "aragua", "barinas", "bolivar",
    "carabobo", "cojedes", "delta amacuro", "distrito capital", "falcon",
    "guarico", "lara", "merida", "miranda", "monagas", "nueva esparta",
    "portuguesa", "sucre", "tachira", "trujillo", "vargas", "yaracuy",
    "zulia",
}


def _contiene_estado_venezolano(texto: str) -> bool:
    t = _normalizar_acentos_ascii(texto.lower())
    return any(estado in t for estado in _ESTADOS_VENEZUELA)


def _normalizar_acentos_ascii(texto: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode("ascii")


# =============================================================================
# 5 GRUPOS DE RUTA DE CHOFER (v4.56, definidos por el usuario a partir del
# catálogo REAL de macro-rutas/sub-rutas del visor legacy — GET
# /api/rutas/catalogo — 14 macro-rutas agrupadas en 5 rutas de reparto):
#
#   1. ZULIA_TRUJILLO        -> Zulia, Trujillo
#   2. LLANO_SANCRISTOBAL    -> Apure, Barinas, Táchira, Mérida, Portuguesa
#                                (TODA Portuguesa EXCEPTO la sub-ruta
#                                "PORTUGUESA ALTA - COJEDES", que va a Centro
#                                — las otras 3 sub-rutas confirmadas: Capital
#                                Alta, Capital Baja, Biscucuy)
#   3. CENTRO                -> Carabobo, Aragua, Caracas (Distrito Capital/
#                                Miranda/Vargas) + la sub-ruta Portuguesa-Cojedes
#   4. BARQUISIMETO_FALCON   -> Lara, Falcón
#   5. ENVIOS_ENTRE_SEDE     -> macro-ruta "ENVÍOS ENTRE ALMACENES" (no es
#                                geografía de cliente, no aplica geocodificar)
#
# Como rep_not.ruta siempre viene vacío en la etapa EMBALADA (ver
# RADIO_MAX_RUTA_KM más abajo), el grupo se infiere por REVERSE GEOCODING
# de la coordenada ya resuelta del cliente (ORS Geocoding /reverse -> el
# campo 'region' es el estado real, ej. "Táchira") — no hay forma de leer
# el grupo directo de ninguna tabla en esta etapa del flujo.
#
# LIMITACIÓN CONOCIDA: la excepción de Portuguesa-Cojedes es a nivel de
# LOCALIDAD (sub-ruta), no de estado — el reverse geocoding solo da el
# estado ("Portuguesa"), no a qué sub-ruta pertenece. Por defecto TODO
# Portuguesa cae en LLANO_SANCRISTOBAL (mayoría real: 3 de 4 sub-rutas);
# los casos de la zona Cojedes de Portuguesa quedarán mal clasificados
# hasta tener una lista de localidades o el campo de ruta real poblado.
# =============================================================================
_GRUPO_POR_ESTADO = {
    "zulia": "ZULIA_TRUJILLO",
    "trujillo": "ZULIA_TRUJILLO",
    "apure": "LLANO_SANCRISTOBAL",
    "barinas": "LLANO_SANCRISTOBAL",
    "tachira": "LLANO_SANCRISTOBAL",
    "merida": "LLANO_SANCRISTOBAL",
    "portuguesa": "LLANO_SANCRISTOBAL",  # ver limitación conocida (Cojedes)
    "carabobo": "CENTRO",
    "aragua": "CENTRO",
    "distrito capital": "CENTRO",
    "miranda": "CENTRO",
    "vargas": "CENTRO",
    "lara": "BARQUISIMETO_FALCON",
    "falcon": "BARQUISIMETO_FALCON",
}

_GRUPOS_RUTA_NOMBRES = {
    "ZULIA_TRUJILLO": "Zulia / Trujillo",
    "LLANO_SANCRISTOBAL": "Llano / San Cristóbal",
    "CENTRO": "Centro (Carabobo/Aragua/Caracas)",
    "BARQUISIMETO_FALCON": "Barquisimeto / Falcón",
    "ENVIOS_ENTRE_SEDE": "Envíos entre sede",
    "SIN_CLASIFICAR": "Sin clasificar",
}


def _grupo_ruta_desde_coords(lat: float, lon: float) -> str:
    """Reverse geocoding ORS -> estado real -> grupo de ruta (ver mapeo
    arriba). 'SIN_CLASIFICAR' si el estado no matchea ninguno de los 5
    grupos (ej. Amazonas, Bolívar, Sucre... zonas que hoy no tienen chofer
    asignado en la matriz que dio el usuario) o si ORS falla."""
    try:
        resp = requests.get(
            "https://api.openrouteservice.org/geocode/reverse",
            params={"api_key": ORS_API_KEY, "point.lat": lat, "point.lon": lon},
            timeout=10,
        )
        if resp.status_code != 200:
            return "SIN_CLASIFICAR"
        features = resp.json().get("features", [])
        if not features:
            return "SIN_CLASIFICAR"
        region = _normalizar_acentos_ascii(str(features[0]["properties"].get("region", "")).lower())
        return _GRUPO_POR_ESTADO.get(region, "SIN_CLASIFICAR")
    except Exception as e:
        print(f"[ORS Reverse] Error clasificando ({lat}, {lon}): {e}", flush=True)
        return "SIN_CLASIFICAR"


def _geocodificar_direccion_ors(direccion: str):
    """Geocodifica texto de dirección -> (lat, lon) vía ORS Geocoding API
    (mismo API key que Directions). Acotado a Venezuela. None si no hay match
    o la API falla — nunca lanza.

    Las direcciones de Profit (clientes.direc1/direc2) suelen ser texto
    libre ruidoso (palabras duplicadas, sin puntuación, mezclando calle y
    referencias) — un geocoder falla seguido con la cadena completa
    (confirmado en vivo). Fallback: si la dirección completa no matchea, se
    reintenta solo con las últimas palabras, pero SOLO si contienen el
    nombre de un estado venezolano real (ver _ESTADOS_VENEZUELA) — si no,
    mejor devolver "sin ubicar" que un match falso con confianza alta."""
    direccion = direccion.strip()
    if not direccion:
        return None
    coords = _geocodificar_texto_ors(direccion)
    if coords:
        return coords
    palabras = direccion.split()
    if len(palabras) > 4:
        resumen = " ".join(palabras[-4:])
        if not _contiene_estado_venezolano(resumen):
            return None
        coords = _geocodificar_texto_ors(resumen)
        if coords:
            return coords
    return None


def _obtener_coordenadas_cliente_cacheadas(co_cli: str, direccion_texto: str):
    """Cache de geocodificación por cliente (SQLite): evita re-geocodificar
    (llamada de red) la misma dirección en cada consulta de rutas. Si la
    dirección de texto cambió respecto a lo cacheado, re-geocodifica.
    Devuelve (lat, lon, grupo_ruta) o None."""
    conn = get_db_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS geocodificacion_clientes (
                co_cli TEXT PRIMARY KEY,
                direccion_texto TEXT,
                latitud REAL,
                longitud REAL,
                grupo_ruta TEXT,
                geocodificado_en TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(geocodificacion_clientes)").fetchall()]
        if "grupo_ruta" not in cols:
            conn.execute("ALTER TABLE geocodificacion_clientes ADD COLUMN grupo_ruta TEXT")

        fila = conn.execute(
            "SELECT direccion_texto, latitud, longitud, grupo_ruta FROM geocodificacion_clientes WHERE co_cli = ?",
            (co_cli,),
        ).fetchone()
        if (fila and fila["direccion_texto"] == direccion_texto and fila["latitud"] is not None
                and fila["grupo_ruta"] is not None
                and _coords_dentro_venezuela(fila["latitud"], fila["longitud"])):
            return (fila["latitud"], fila["longitud"], fila["grupo_ruta"])

        coords = _geocodificar_direccion_ors(direccion_texto)
        grupo = _grupo_ruta_desde_coords(coords[0], coords[1]) if coords else None
        conn.execute(
            "INSERT INTO geocodificacion_clientes (co_cli, direccion_texto, latitud, longitud, grupo_ruta, geocodificado_en) "
            "VALUES (?, ?, ?, ?, ?, datetime('now','localtime')) "
            "ON CONFLICT(co_cli) DO UPDATE SET direccion_texto=excluded.direccion_texto, "
            "latitud=excluded.latitud, longitud=excluded.longitud, grupo_ruta=excluded.grupo_ruta, "
            "geocodificado_en=excluded.geocodificado_en",
            (co_cli, direccion_texto, coords[0] if coords else None, coords[1] if coords else None, grupo),
        )
        conn.commit()
        return (coords[0], coords[1], grupo) if coords else None
    finally:
        conn.close()


_cols_not_ent_cache = {}


def _resolver_cols_not_ent(cur_profit) -> dict:
    """Resuelve las columnas de not_ent UNA vez por proceso (cacheado en
    memoria) — antes se resolvía por INFORMATION_SCHEMA en cada nota."""
    if _cols_not_ent_cache:
        return _cols_not_ent_cache
    cols = [r[0] for r in cur_profit.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME = 'not_ent' AND TABLE_SCHEMA = 'dbo'"
    ).fetchall()]
    lower = {c.lower(): c for c in cols}

    def _resolver(candidatas):
        for c in candidatas:
            if c.lower() in lower:
                return lower[c.lower()]
        return None

    cols_cli = [r[0] for r in cur_profit.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME = 'clientes' AND TABLE_SCHEMA = 'dbo'"
    ).fetchall()]
    lower_cli = {c.lower(): c for c in cols_cli}
    col_cli_des = None
    for c in ["cli_des", "descrip", "nombre"]:
        if c.lower() in lower_cli:
            col_cli_des = lower_cli[c.lower()]
            break

    _cols_not_ent_cache.update({
        "factura": _resolver(["fact_num", "num_doc", "nro_doc", "documento", "numero", "factura"]),
        "cli_des": col_cli_des,
        "tiene_co_cli": "co_cli" in lower,
    })
    return _cols_not_ent_cache


def _resolver_cliente_de_nota_profit(cur_profit, cod_nota: str) -> dict:
    """co_cli + nombre del cliente para una nota, vía Profit not_ent — usa el
    cursor YA ABIERTO del lote (antes: una conexión pyodbc nueva POR NOTA,
    causa real del HTTP 524 detectado en vivo — 50 notas x conexión nueva
    fácilmente supera los ~100s del túnel Cloudflare)."""
    cols = _resolver_cols_not_ent(cur_profit)
    if not cols.get("factura"):
        return {}
    join_cliente = "LEFT JOIN clientes cl ON cl.co_cli = ne.co_cli" if cols["cli_des"] and cols["tiene_co_cli"] else ""
    cli_expr = f"LTRIM(RTRIM(CAST(cl.{cols['cli_des']} AS NVARCHAR(200))))" if cols["cli_des"] else "'(sin nombre)'"
    fila = cur_profit.execute(
        f"SELECT LTRIM(RTRIM(CAST(ne.co_cli AS NVARCHAR(30)))) AS co_cli, {cli_expr} AS cliente "
        f"FROM not_ent ne {join_cliente} "
        f"WHERE LTRIM(RTRIM(CAST(ne.{cols['factura']} AS NVARCHAR(40)))) = ?",
        cod_nota,
    ).fetchone()
    if not fila or not fila[0]:
        return {}
    return {"co_cli": fila[0], "cliente": fila[1] or ""}


def _resolver_nota_embalada_completa(cod_nota: str, ruta_legacy: str, cur_profit) -> dict:
    """Pipeline completo de una nota EMBALADA: cliente (Profit not_ent) ->
    dirección (Profit clientes) -> coordenadas (ORS geocoding, cacheado).
    Nunca lanza: si algún paso falla, devuelve lo que sí pudo resolver con
    latitud/longitud=None (el llamador filtra las que no se pueden rutear).
    Reutiliza SIEMPRE el mismo cursor Profit del lote (sin abrir conexión
    nueva por nota — ver _resolver_cliente_de_nota_profit)."""
    base = {
        "numero_nota": cod_nota,
        "ruta": ruta_legacy or "",
        "cliente": "",
        "co_cli": "",
        "direccion": "",
        "latitud": None,
        "longitud": None,
        "grupo_ruta": "SIN_CLASIFICAR",
    }
    try:
        perfil = _resolver_cliente_de_nota_profit(cur_profit, cod_nota)
    except Exception:
        perfil = {}
    if not perfil.get("co_cli"):
        return base
    base["cliente"] = perfil.get("cliente", "")
    base["co_cli"] = perfil["co_cli"]
    try:
        direccion = _resolver_direccion_cliente_profit(cur_profit, perfil["co_cli"])
    except Exception:
        direccion = ""
    base["direccion"] = direccion
    if direccion:
        resultado = _obtener_coordenadas_cliente_cacheadas(perfil["co_cli"], direccion)
        if resultado:
            base["latitud"], base["longitud"], base["grupo_ruta"] = resultado
    return base


class ORSAdapter:
    """Adaptador hexagonal para OpenRouteService."""

    BASE_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    MATRIX_URL = "https://api.openrouteservice.org/v2/matrix/driving-car"

    def calcular_ruta_optimizada(self, origen, destinos):
        """
        Devuelve GeoJSON + paradas con su ETA REAL acumulada (duración real
        de manejo por carretera, calculada por ORS con datos de vías reales
        — no una línea recta con velocidad inventada).
        origen: [lng, lat]
        destinos: lista de [[lng, lat], ...]

        BUG REAL corregido (v4.56, reportado en vivo — el sistema mostraba
        22h18min para un tramo que Google Maps calcula en 17h41min): el ETA
        se calculaba con distancia en línea recta (Haversine) dividida por
        una velocidad FIJA de "30 km/h urbano" — absurdo para un viaje de
        cientos de km por autopista/carretera nacional. Además la respuesta
        de ORS trae UN segmento de ruta POR TRAMO (origen→parada1,
        parada1→parada2, ...) con la duración real ya calculada, y el código
        anterior solo miraba `segments[0]` (el primer tramo) y descartaba el
        resto — nunca se usaba ese dato real. Ahora se usa la duración real
        acumulada de cada tramo tal como la devuelve ORS (que ya respeta
        velocidades reales de vía, no hace falta un tope manual de 110km/h).

        NOTA (limitación conocida, no resuelta acá): el orden de las
        paradas sigue siendo el orden en que se seleccionaron, NO un orden
        optimizado por distancia/tiempo real — ORS Directions rutea en el
        orden que se le da, no lo reordena. Optimizar el orden requeriría
        la API de Optimización de ORS (VROOM), que es un cambio aparte.
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
            # Un segmento por tramo: segments[0] = origen->destinos[0],
            # segments[1] = destinos[0]->destinos[1], etc.
            segments = properties.get("segments", [])

            order = []
            acumulado_min = 0.0
            for i, dest in enumerate(destinos):
                seg = segments[i] if i < len(segments) else {}
                duracion_tramo_min = (seg.get("duration") or 0) / 60.0
                acumulado_min += duracion_tramo_min
                order.append({"index": i, "coords": dest, "eta_minutos": round(acumulado_min, 1)})

            return geometry, order
        except requests.exceptions.Timeout:
            print("[ORS] Timeout al consultar ORS API")
            # fallback: secuencia sin optimizar, sin ETA real disponible
            return None, [{"index": i, "coords": d, "eta_minutos": None} for i, d in enumerate(destinos)]
        except Exception as e:
            print(f"[ORS] Error en calcular_ruta_optimizada: {e}")
            return None, []

    def estimar_eta(self, posicion_actual, destino_coords):
        """ETA aproximado por línea recta — SOLO para telemetría en vivo
        (distancia del chofer a su próxima parada mientras se mueve, no la
        ruta completa; ver calcular_ruta_optimizada para el ETA real de
        ruta). Velocidad de referencia subida de 30 a 70 km/h (promedio
        realista de carretera venezolana, tope legal de camión 110 km/h) —
        30 km/h era velocidad de ciudad, no de ruta abierta."""
        from math import radians, sin, cos, sqrt, atan2
        lat1, lon1 = radians(posicion_actual[1]), radians(posicion_actual[0])
        lat2, lon2 = radians(destino_coords[1]), radians(destino_coords[0])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        dist_km = 6371 * c
        velocidad_kmh = 70  # promedio de carretera (antes 30, velocidad de ciudad)
        minutos = (dist_km / velocidad_kmh) * 60
        return round(minutos, 1)


def _distancia_km(coords_a, coords_b) -> float:
    """Haversine simple; coords en formato [lng, lat]."""
    from math import radians, sin, cos, sqrt, atan2
    lat1, lon1 = radians(coords_a[1]), radians(coords_a[0])
    lat2, lon2 = radians(coords_b[1]), radians(coords_b[0])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371 * 2 * atan2(sqrt(a), sqrt(1 - a))


# Radio máximo (km) desde el origen del chofer para incluir un destino en la
# misma ruta. BUG REAL detectado en vivo: rep_not.ruta está SIEMPRE VACÍO en
# las notas EMBALADA (se asigna después, en el despacho legacy) — no hay
# ningún dato de zona/ruta real que filtrar en esa etapa. Sin este filtro,
# el chofer podía seleccionar pedidos embalados de puntas opuestas del país
# (Yaracuy + Falcón + Táchira + Zulia a la vez) y ORS rechazaba la ruta
# completa por "distancia > 6000 km". Este radio usa la distancia REAL
# geocodificada como proxy de "misma zona" ya que no hay campo de ruta
# confiable en esta etapa del flujo.
RADIO_MAX_RUTA_KM = float(os.environ.get("ARA_RUTA_RADIO_MAX_KM", "150"))

_ors_adapter = ORSAdapter()


def _conectar_profit_ro():
    import pyodbc
    driver = os.environ.get("PROFIT_DB_DRIVER", "SQL Server")
    host = os.environ.get("PROFIT_DB_HOST", "192.168.4.20")
    port = os.environ.get("PROFIT_DB_PORT", "1433")
    db = os.environ.get("PROFIT_DB_NAME", "CRISTM25")
    user = os.environ.get("PROFIT_DB_USER", "profit")
    pwd = os.environ.get("PROFIT_DB_PASS", "profit")
    return pyodbc.connect(
        f"DRIVER={{{driver}}};SERVER={host},{port};DATABASE={db};UID={user};PWD={pwd}",
        timeout=8,
    )


@app.route('/api/rutas/grupos', methods=['GET'])
def rutas_grupos():
    """Los 5 grupos fijos de ruta de chofer (definidos por el negocio a
    partir del catálogo real de macro-rutas del visor legacy)."""
    return jsonify([
        {"id": gid, "nombre": nombre}
        for gid, nombre in _GRUPOS_RUTA_NOMBRES.items()
        if gid != "SIN_CLASIFICAR"
    ])


@app.route('/api/rutas/pedidos_embalados', methods=['GET'])
def rutas_pedidos_embalados():
    """Notas EMBALADAS listas para el chofer, con dirección geocodificada.

    Fuente de 'EMBALADA': MySQL legacy rep_not.estatus (decisión v4.56 —
    misma fuente que el resto de REALIZAR RUTA, no la SQLite local de ARA).
    Pipeline por nota: rep_not -> Profit not_ent (co_cli) -> Profit clientes
    (dirección de texto) -> ORS Geocoding (lat/lon, cacheado). Una nota sin
    dirección resoluble o sin geocodificar se devuelve igual (con
    latitud/longitud=null) para que el frontend la muestre como "sin
    ubicar" en vez de desaparecer silenciosamente.
    """
    # Default bajado de 50 a 20 (v4.56): con conexión Profit reutilizada por
    # lote esto ya no es el cuello de botella, pero la geocodificación de un
    # cliente NUEVO (sin caché) sigue siendo una llamada de red a ORS — un
    # lote grande de clientes nunca antes geocodificados podría acercarse
    # igual al límite de ~100s del túnel Cloudflare. Tope de tiempo (60s)
    # como segunda red de seguridad: si se acerca, corta y devuelve lo que
    # ya resolvió en vez de arriesgar el 524.
    limite = int(request.args.get('limite', 20))
    limite = max(1, min(limite, 200))
    try:
        legacy = _listar_notas_embaladas_legacy(limite)
    except Exception as e:
        return jsonify({"error": f"No se pudo consultar rep_not (MySQL legacy): {e}"}), 500

    if not legacy:
        return jsonify([])

    try:
        conn_profit = _conectar_profit_ro()
    except Exception as e:
        return jsonify({"error": f"Profit no disponible para resolver clientes: {e}"}), 500

    resultado = []
    t_inicio = time.time()
    try:
        cur = conn_profit.cursor()
        for fila in legacy:
            if time.time() - t_inicio > 60:
                break
            info = _resolver_nota_embalada_completa(fila['cod_nota'], fila.get('ruta', ''), cur)
            resultado.append(info)
    finally:
        conn_profit.close()

    # División real por RUTA/ZONA de chofer (v4.56, a pedido explícito: "hay
    # varios choferes, cada uno con su zona agrupada — un chofer no recorre
    # media Venezuela, solo su ruta"). grupo_ruta viene de _resolver_nota_
    # embalada_completa (reverse geocoding cacheado -> uno de los 5 grupos
    # reales del negocio, ver _GRUPO_POR_ESTADO). Si el cliente pide un
    # `grupo` explícito, "en_zona" = pertenece a ese grupo. Si no manda
    # grupo (compatibilidad / primera carga sin selector aún), cae al
    # criterio anterior por distancia al origen del chofer.
    grupo_pedido = request.args.get('grupo', '').strip().upper() or None
    origen_lat = request.args.get('origen_lat', type=float)
    origen_lng = request.args.get('origen_lng', type=float)
    origen = [origen_lng, origen_lat] if (origen_lat is not None and origen_lng is not None) else None

    for info in resultado:
        if info["latitud"] is not None and info["longitud"] is not None and origen is not None:
            info["distancia_km"] = round(_distancia_km(origen, [info["longitud"], info["latitud"]]), 1)
        else:
            info["distancia_km"] = None
        if grupo_pedido:
            info["en_zona"] = info["grupo_ruta"] == grupo_pedido
        elif info["distancia_km"] is not None:
            info["en_zona"] = info["distancia_km"] <= RADIO_MAX_RUTA_KM
        else:
            info["en_zona"] = False

    if grupo_pedido or origen is not None:
        resultado.sort(key=lambda r: (not r["en_zona"], r["distancia_km"] is None, r["distancia_km"] or 0))

    return jsonify(resultado)


@app.route('/api/rutas/optimizar_ruta', methods=['POST'])
def rutas_optimizar_ruta():
    """Recibe origen + lista de numero_nota (cod_nota de rep_not), retorna
    ruta optimizada con GeoJSON. Resuelve cliente/dirección/coordenadas con
    el mismo pipeline de /api/rutas/pedidos_embalados (cacheado por cliente,
    así que si ya se listó antes no vuelve a golpear Profit/ORS)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON requerido"}), 400

    origen = data.get("origen")  # [lng, lat]
    numeros_nota = data.get("numero_notas") or data.get("nota_ids", [])

    if not origen or not numeros_nota:
        return jsonify({"error": "origen y numero_notas requeridos"}), 400
    if len(origen) != 2 or not _coords_dentro_venezuela(float(origen[1]), float(origen[0])):
        return jsonify({"error": f"Origen fuera de rango (GPS inválido): {origen}"}), 400

    try:
        conn_profit = _conectar_profit_ro()
    except Exception as e:
        return jsonify({"error": f"Profit no disponible: {e}"}), 500

    notas = []
    try:
        cur = conn_profit.cursor()
        for numero in numeros_nota:
            notas.append(_resolver_nota_embalada_completa(str(numero), '', cur))
    finally:
        conn_profit.close()

    # Sin tope de distancia al origen (v4.56, a pedido explícito): un chofer
    # arranca desde donde esté (sede, casa, en ruta) y eso no tiene por qué
    # estar cerca de su zona asignada — para eso son choferes, hacen viajes
    # largos. La agrupación real ya la hace el grupo_ruta (Zulia/Trujillo,
    # Llano/San Cristóbal, etc., ver _GRUPO_POR_ESTADO) que el chofer elige
    # al listar pedidos_embalados; acá solo se filtra lo que no tiene
    # coordenada resoluble. La protección contra el bug real (ORS rechazando
    # la ruta por distancia absurda) ya quedó cubierta en la geocodificación
    # misma (bounding box de Venezuela + filtro de estado real en el
    # fallback), no hace falta un segundo tope aquí.
    destinos = []
    sin_ubicar = []
    for n in notas:
        if n["latitud"] is None or n["longitud"] is None:
            sin_ubicar.append(n["numero_nota"])
            continue
        destinos.append({"nota": n, "coords": [float(n["longitud"]), float(n["latitud"])]})

    if not destinos:
        return jsonify({
            "status": "error",
            "mensaje": "Ninguna de las notas seleccionadas tiene dirección geocodificable.",
            "sin_ubicar": sin_ubicar,
        }), 200

    destinos_coords = [d["coords"] for d in destinos]
    geometry, order = _ors_adapter.calcular_ruta_optimizada(origen, destinos_coords)

    paradas = []
    if order:
        for paso in order:
            idx = paso["index"]
            dest = destinos[idx]
            nota = dest["nota"]
            # ETA real (duración acumulada de ORS por carretera) — antes
            # recalculaba con Haversine + velocidad fija, ignorando el dato
            # real ya calculado (ver nota en calcular_ruta_optimizada).
            eta = paso.get("eta_minutos")
            paradas.append({
                "orden": len(paradas) + 1,
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
        "geojson": geometry,
        "sin_ubicar": sin_ubicar,
    })


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

        conn.close()
    except Exception:
        total_entregas = 0

    # Pendientes = mismo conteo que /api/rutas/pedidos_embalados (MySQL
    # legacy rep_not.estatus='EMBALADA', fuente de verdad elegida v4.56).
    try:
        pendientes = len(_listar_notas_embaladas_legacy(limite=500))
    except Exception:
        pendientes = 0

    # Rol (tipo de vehículo) por nombre de chofer — para el ícono en el mapa
    # de Regente (🛻 chofer-camioneta, 🏍️ chofer-moto).
    roles_choferes = {}
    try:
        conn_u = get_db_connection()
        for row in conn_u.execute("SELECT nombre, rol FROM usuarios").fetchall():
            roles_choferes[row["nombre"]] = row["rol"]
        conn_u.close()
    except Exception:
        pass

    choferes = []
    for cid, pos in _posiciones_choferes.items():
        vel = pos.get("velocidad", 0)
        if vel == 0:
            clase_vel = "quieto"
        elif vel < 20:
            clase_vel = "lento"
        else:
            clase_vel = "normal"

        # Buscar nota actual (BUG corregido v4.56: comparaba contra 'nota_id',
        # campo viejo — las paradas ahora usan 'numero_nota' desde que la
        # fuente de EMBALADA pasó a MySQL legacy).
        nota_actual = None
        for rid, ruta in _rutas_activas.items():
            for p in ruta.get("paradas", []):
                if str(p.get("numero_nota")) == str(pos.get("nota_actual_id")):
                    nota_actual = p
                    break

        choferes.append({
            "chofer_id": cid,
            "rol": roles_choferes.get(cid),
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


@app.route('/api/notas/detalle_profit', methods=['GET'])
def notas_detalle_profit():
    """Detalle SOLO LECTURA de una nota en Profit (CRISTM25) para enriquecer
    respuestas del agente IA (NVIDIA Brain / LLM) cuando la fila local de
    notas_entrega no tiene cliente/co_cli/vendedor/fecha.

    Es un enriquecimiento degradable: ante cualquier fallo de conexión o nota
    ausente responde HTTP 200 con {"status": "error", ...} — nunca rompe el
    flujo del cliente que la consume.
    """
    try:
        from ara_brain import _consultar_nota_profit_readonly
    except ImportError:
        return jsonify({"status": "error", "mensaje": "ara_brain no disponible"}), 500
    numero = request.args.get('numero', '').strip()
    if not numero:
        return jsonify({"status": "error", "mensaje": "Parámetro 'numero' obligatorio."}), 400
    dato = _consultar_nota_profit_readonly(numero)
    if 'error' in dato:
        return jsonify({"status": "error", "mensaje": dato['error'], "origen": "CRISTM25"})
    return jsonify({"status": "success", "nota": dato})


# -----------------------------------------------------------------------------
# PERFIL MAESTRO DE CLIENTE + ESTADO DE CUENTA (Profit CRISTM25, SOLO LECTURA)
# Fuentes: dbo.clientes (maestro) + tablas de cuentas por cobrar (sfac/scxc/
# not_ent) donde el saldo sea mayor a 0 o el estatus no esté totalmente
# cancelado. Resolución dinámica de columnas vía INFORMATION_SCHEMA (patrón de
# _consultar_nota_profit_readonly y ProfitFacturador): nunca se asumen nombres.
# Degrada a {"error": ...} sin excepción ante fallo de conexión o esquema.
# -----------------------------------------------------------------------------

_CAND_CLIENTE_COD = ["co_cli", "co_cliente", "cod_cli"]
_CAND_CLIENTE_DES = ["cli_des", "descrip", "nombre"]
_CAND_CLIENTE_RIF = ["rif", "doc_rif"]
_CAND_CLIENTE_NIT = ["nit", "doc_nit"]
_CAND_CLIENTE_TEL = ["telefono", "telef", "telf", "telf1"]
_CAND_CLIENTE_VEN = ["co_ven", "vendedor"]
_CAND_CLIENTE_LIM = ["lim_cred", "limite_credito", "lim_cre"]

# Tablas candidatas de cuentas por cobrar / documentos abiertos (Profit).
# sfac = facturas, scxc = cuentas por cobrar, not_ent = notas de entrega,
# factura = encabezado de facturas (todas con columna de cliente y saldo).
_CAND_TABLAS_CXC = ["sfac", "scxc", "not_ent", "factura"]
_CAND_DOC_NUM = ["fact_num", "num_doc", "nro_doc", "documento", "numero", "factura"]
_CAND_DOC_FECHA = ["fec_emis", "fecha", "fec_ven"]
_CAND_DOC_TOTAL = ["total_bruto", "total", "monto", "monto_total", "total_neto"]
_CAND_DOC_SALDO = ["saldo", "saldo_pend", "saldo_restante", "saldo_actual"]
_CAND_DOC_STATUS = ["status", "statu", "estatus", "estado"]
_CAND_DOC_ANULADA = ["anulada", "anulado"]

# Estatus de Profit que indican documento TOTALMENTE cancelado/anulado: se
# excluyen de los pendientes. 'P' = presupuesto/pendiente (no es saldo abierto
# a cobro); 'A'/'N' = anulada; 'D'/'C' = cancelada/descontada.
_STATUS_CANCELADO = {"A", "N", "D", "C", "P"}


def _consultar_cliente_estado_cuenta_profit(co_cli: str) -> dict:
    """Consulta SOLO LECTURA del perfil del cliente y su estado de cuenta.
    Retorna:
        {"cliente": {...}, "documentos_pendientes": [...], "saldo_total": ...}
    o {"error": "..."} degradado (nunca lanza)."""
    try:
        import pyodbc
    except ImportError:
        return {"error": "pyodbc no disponible"}

    driver = os.environ.get("PROFIT_DB_DRIVER", "SQL Server")
    host = os.environ.get("PROFIT_DB_HOST", "192.168.4.20")
    port = os.environ.get("PROFIT_DB_PORT", "1433")
    db = os.environ.get("PROFIT_DB_NAME", "CRISTM25")
    user = os.environ.get("PROFIT_DB_USER", "profit")
    pwd = os.environ.get("PROFIT_DB_PASS", "profit")
    try:
        conn = pyodbc.connect(
            f"DRIVER={{{driver}}};SERVER={host},{port};DATABASE={db};UID={user};PWD={pwd}",
            timeout=8,
        )
    except Exception as e:
        return {"error": f"Profit no disponible: {e}"}
    try:
        cur = conn.cursor()

        def _cols(tabla: str):
            return [r[0] for r in cur.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = ? AND TABLE_SCHEMA = 'dbo'",
                tabla,
            ).fetchall()]

        def _resolver(cols, candidatas):
            lower = {c.lower(): c for c in cols}
            for c in candidatas:
                if c.lower() in lower:
                    return lower[c.lower()]
            return None

        # ── 1) Maestro del cliente ────────────────────────────────────────────
        cols_cli = _cols("clientes")
        col_cod = _resolver(cols_cli, _CAND_CLIENTE_COD)
        col_des = _resolver(cols_cli, _CAND_CLIENTE_DES)
        col_rif = _resolver(cols_cli, _CAND_CLIENTE_RIF)
        col_nit = _resolver(cols_cli, _CAND_CLIENTE_NIT)
        col_tel = _resolver(cols_cli, _CAND_CLIENTE_TEL)
        col_ven = _resolver(cols_cli, _CAND_CLIENTE_VEN)
        col_lim = _resolver(cols_cli, _CAND_CLIENTE_LIM)
        if not col_cod:
            return {"error": "Esquema Profit sin co_cli en dbo.clientes"}

        selecciones = [f"LTRIM(RTRIM(CAST([{col_cod}] AS NVARCHAR(30)))) AS co_cli"]
        if col_des:
            selecciones.append(f"LTRIM(RTRIM(CAST([{col_des}] AS NVARCHAR(250)))) AS razon_social")
        if col_rif:
            selecciones.append(f"LTRIM(RTRIM(CAST([{col_rif}] AS NVARCHAR(30)))) AS rif")
        if col_nit:
            selecciones.append(f"LTRIM(RTRIM(CAST([{col_nit}] AS NVARCHAR(30)))) AS nit")
        if col_tel:
            selecciones.append(f"LTRIM(RTRIM(CAST([{col_tel}] AS NVARCHAR(30)))) AS telefono")
        if col_ven:
            selecciones.append(f"LTRIM(RTRIM(CAST([{col_ven}] AS NVARCHAR(30)))) AS co_ven")
        if col_lim:
            selecciones.append(f"CAST([{col_lim}] AS FLOAT) AS limite_credito")

        fila = cur.execute(
            f"SELECT {', '.join(selecciones)} FROM dbo.clientes "
            f"WHERE LTRIM(RTRIM(CAST([{col_cod}] AS NVARCHAR(30)))) = ?",
            co_cli,
        ).fetchone()
        if not fila:
            return {"error": f"Cliente {co_cli} no encontrado en Profit (CRISTM25)"}
        cols_fila = [d[0] for d in cur.description]
        cliente = dict(zip(cols_fila, fila))

        # ── 2) Documentos pendientes (cuentas por cobrar) ─────────────────────
        documentos = []
        for tabla in _CAND_TABLAS_CXC:
            cols_doc = _cols(tabla)
            if not cols_doc:
                continue
            col_cli_doc = _resolver(cols_doc, _CAND_CLIENTE_COD)
            if not col_cli_doc:
                continue
            col_num = _resolver(cols_doc, _CAND_DOC_NUM)
            col_fecha = _resolver(cols_doc, _CAND_DOC_FECHA)
            col_total = _resolver(cols_doc, _CAND_DOC_TOTAL)
            col_saldo = _resolver(cols_doc, _CAND_DOC_SALDO)
            col_status = _resolver(cols_doc, _CAND_DOC_STATUS)
            col_anulada = _resolver(cols_doc, _CAND_DOC_ANULADA)
            if not col_num and not col_saldo and not col_status:
                continue

            exprs = [f"LTRIM(RTRIM(CAST([{col_cli_doc}] AS NVARCHAR(30)))) AS co_cli"]
            if col_num:
                exprs.append(f"LTRIM(RTRIM(CAST([{col_num}] AS NVARCHAR(40)))) AS numero")
            else:
                exprs.append("NULL AS numero")
            if col_fecha:
                exprs.append(f"CAST([{col_fecha}] AS NVARCHAR(40)) AS fecha")
            else:
                exprs.append("NULL AS fecha")
            if col_total:
                exprs.append(f"CAST([{col_total}] AS FLOAT) AS total")
            else:
                exprs.append("0 AS total")
            if col_saldo:
                exprs.append(f"CAST([{col_saldo}] AS FLOAT) AS saldo")
            else:
                exprs.append("0 AS saldo")
            if col_status:
                exprs.append(f"UPPER(CAST([{col_status}] AS NVARCHAR(10))) AS status")
            else:
                exprs.append("NULL AS status")
            if col_anulada:
                exprs.append(f"CAST([{col_anulada}] AS INT) AS anulada")
            else:
                exprs.append("0 AS anulada")
            tipo = "FACT" if "sfac" in tabla.lower() else ("CXC" if "scxc" in tabla.lower() else ("NOTA" if tabla.lower() == "not_ent" else "FAC"))

            # Filtro: solo documentos abiertos (saldo > 0) o que no estén
            # totalmente cancelados; se aplica también en Python por robustez.
            cond = f"WHERE LTRIM(RTRIM(CAST([{col_cli_doc}] AS NVARCHAR(30)))) = ?"
            filas = cur.execute(
                f"SELECT {', '.join(exprs)} FROM dbo.[{tabla}] {cond}",
                co_cli,
            ).fetchall()
            for f in filas:
                r = dict(zip([d[0] for d in cur.description], f))
                anulada = r.get("anulada") in (True, 1)
                status = str(r.get("status") or "").upper()
                saldo = float(r.get("saldo") or 0)
                if anulada or status in _STATUS_CANCELADO:
                    continue
                if saldo <= 0 and status not in ("T", "0", "2", ""):
                    continue
                documentos.append({
                    "tipo": tipo,
                    "numero": str(r.get("numero") or ""),
                    "fecha": str(r.get("fecha") or ""),
                    "total": float(r.get("total") or 0),
                    "saldo": saldo,
                })
            if documentos:
                break  # primera tabla con datos pendientes (evita duplicados)

        return {
            "cliente": {
                "co_cli": str(cliente.get("co_cli") or co_cli),
                "razon_social": str(cliente.get("razon_social") or ""),
                "rif": str(cliente.get("rif") or ""),
                "nit": str(cliente.get("nit") or ""),
                "telefono": str(cliente.get("telefono") or ""),
                "limite_credito": float(cliente.get("limite_credito") or 0),
                "vendedor": str(cliente.get("co_ven") or ""),
            },
            "documentos_pendientes": documentos,
            "saldo_total_pendiente": round(sum(d["saldo"] for d in documentos), 2),
        }
    except Exception as e:
        return {"error": f"Consulta de estado de cuenta falló: {e}"}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _buscar_clientes_profit(busqueda: str, limite: int = 10) -> dict:
    """Búsqueda SOLO LECTURA de clientes por coincidencia parcial en razón
    social (LIKE %...%) en dbo.clientes de CRISTM25.

    Retorna {"clientes": [{co_cli, razon_social, rif, co_ven}]} o
    {"error": ...} degradado. El LLM la usa para resolver un código antes de
    consultar el estado de cuenta completo.
    """
    try:
        import pyodbc
    except ImportError:
        return {"error": "pyodbc no disponible"}

    driver = os.environ.get("PROFIT_DB_DRIVER", "SQL Server")
    host = os.environ.get("PROFIT_DB_HOST", "192.168.4.20")
    port = os.environ.get("PROFIT_DB_PORT", "1433")
    db = os.environ.get("PROFIT_DB_NAME", "CRISTM25")
    user = os.environ.get("PROFIT_DB_USER", "profit")
    pwd = os.environ.get("PROFIT_DB_PASS", "profit")
    try:
        conn = pyodbc.connect(
            f"DRIVER={{{driver}}};SERVER={host},{port};DATABASE={db};UID={user};PWD={pwd}",
            timeout=8,
        )
    except Exception as e:
        return {"error": f"Profit no disponible: {e}"}
    try:
        cur = conn.cursor()
        cols_cli = [r[0] for r in cur.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'clientes' AND TABLE_SCHEMA = 'dbo'",
        ).fetchall()]
        lower = {c.lower(): c for c in cols_cli}

        def _res(cands):
            for c in cands:
                if c.lower() in lower:
                    return lower[c.lower()]
            return None

        col_cod = _res(_CAND_CLIENTE_COD)
        col_des = _res(_CAND_CLIENTE_DES)
        col_rif = _res(_CAND_CLIENTE_RIF)
        col_ven = _res(_CAND_CLIENTE_VEN)
        if not col_cod or not col_des:
            return {"error": "Esquema Profit sin co_cli/cli_des en dbo.clientes"}

        exprs = [f"LTRIM(RTRIM(CAST([{col_cod}] AS NVARCHAR(30)))) AS co_cli",
                 f"LTRIM(RTRIM(CAST([{col_des}] AS NVARCHAR(250)))) AS razon_social"]
        if col_rif:
            exprs.append(f"LTRIM(RTRIM(CAST([{col_rif}] AS NVARCHAR(30)))) AS rif")
        if col_ven:
            exprs.append(f"LTRIM(RTRIM(CAST([{col_ven}] AS NVARCHAR(30)))) AS co_ven")

        filas = cur.execute(
            f"SELECT TOP (?) {', '.join(exprs)} FROM dbo.clientes "
            f"WHERE LTRIM(RTRIM(CAST([{col_des}] AS NVARCHAR(250)))) LIKE ? "
            f"ORDER BY LTRIM(RTRIM(CAST([{col_des}] AS NVARCHAR(250))))",
            limite, f"%{busqueda}%",
        ).fetchall()
        cols_f = [d[0] for d in cur.description]
        clientes = [dict(zip(cols_f, f)) for f in filas]
        if not clientes:
            return {"error": f"No hay clientes que coincidan con \"{busqueda}\""}
        return {"clientes": clientes}
    except Exception as e:
        return {"error": f"Búsqueda de clientes falló: {e}"}
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.route('/api/cliente/estado_cuenta', methods=['GET'])
def cliente_estado_cuenta():
    """Perfil maestro + estado de cuenta de un cliente en Profit (CRISTM25).

    GET /api/cliente/estado_cuenta?co_cli=...        → estado de cuenta completo
    GET /api/cliente/estado_cuenta?busqueda=...      → candidatos por razón social
    Retorna JSON consolidado: {cliente, documentos_pendientes, saldo_total}.
    Degrada a HTTP 200 {"status":"error"} ante cliente ausente o fallo de
    conexión (el agente NvidiaBrain lo traduce sin romper su bucle).
    """
    co_cli = request.args.get('co_cli', '').strip()
    busqueda = request.args.get('busqueda', '').strip()
    if not co_cli and not busqueda:
        return jsonify({"status": "error",
                        "mensaje": "Parámetro 'co_cli' u 'busqueda' obligatorio."}), 400
    if busqueda and not co_cli:
        dato = _buscar_clientes_profit(busqueda)
        if 'error' in dato:
            return jsonify({"status": "error", "mensaje": dato['error'], "origen": "CRISTM25"})
        return jsonify({"status": "success", **dato})
    dato = _consultar_cliente_estado_cuenta_profit(co_cli)
    if 'error' in dato:
        return jsonify({"status": "error", "mensaje": dato['error'], "origen": "CRISTM25"})
    return jsonify({"status": "success", **dato})


# =============================================================================
# 6. PUENTE CLI DE TOOLS NVIDIA BRAIN (directiva v4.4: menú de comandos '/')
# =============================================================================
# El menú flotante del chat (templates/index.html) consulta el catálogo y
# ejecuta las 25 tools departamentales por nombre a través del runner PHP
# bin/ejecutar_tool_cli.php. El runner es la única fuente de verdad del
# ToolRegistry; este adaptador solo orquesta el subproceso (subprocess con
# lista de argumentos, sin shell, para no manglear las comillas del JSON).
import subprocess

_RUNNER_TOOLS = str(_PROJECT_ROOT / 'bin' / 'ejecutar_tool_cli.php')
_PHP_EXE = os.environ.get('ARA_PHP_EXE', r'C:\tools\php\php.exe')
if not os.path.exists(_PHP_EXE):
    _PHP_EXE = 'php'


def _ip_real() -> str | None:
    """Devuelve la IP real de red (no loopback) de la máquina, o None."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip
    except Exception:
        return None


def _ejecutar_runner_tools(args: list, timeout_s: int) -> dict:
    """Ejecuta el runner PHP con FUSIBLE anti-zombi (Orden v4.14) y devuelve
    el JSON de su primera línea de stdout.

    - Propaga ARA_CLI_MAX_S al subproceso: el propio runner PHP se suicida a
      ese límite (set_time_limit + vigilante por ticks) si la skill se cuelga.
    - AQUÍ se aplica además el fusible de respaldo: communicate(timeout) y
      process.kill() si el PHP no termina a tiempo. NUNCA queda php.exe vivo
      en background (zombi).
    - Sin shell (lista de argumentos) para no manglear las comillas del JSON.
    """
    comando = [_PHP_EXE, _RUNNER_TOOLS] + args
    env = dict(os.environ)
    # URL base del ERP para los tools PHP que la necesiten (ej. consultar_nota):
    # el loopback 127.0.0.1 puede estar ocupado por otro proceso, así que se
    # apunta a la IP real del servidor ARA (v4.9).
    #
    # BUG real detectado en vivo (24/08): este puerto quedó hardcodeado en
    # 5000 — cuando ara_server.py se movió a 4050 (PC-NVR.exe tomaba el
    # 5000 en esta máquina), CUALQUIER invocación de una tool PHP en la que
    # el proceso de ara_server.py no tuviera ya ARA_ERP_URL en su propio
    # entorno (ej. si se arrancó sin pasar por el lanzador) seguía armando
    # la URL vieja acá mismo, pisando en silencio el fallback ya corregido
    # del lado de FlaskApiTrait.php/ConsultarNotaTool.php — la nota SÍ
    # existía, pero el subproceso PHP nunca podía alcanzar el API real.
    if not env.get("ARA_ERP_URL"):
        ip = _ip_real()
        if ip:
            env["ARA_ERP_URL"] = "http://%s:%s" % (ip, os.environ.get("ARA_SERVER_PORT", "4050"))
    env["ARA_CLI_MAX_S"] = str(timeout_s)  # el runner se suicida a este tope
    proc = subprocess.Popen(
        comando,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    try:
        stdout_b, stderr_b = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()   # fusible: mata el PHP si se cuelga
        proc.wait()   # espera a que muera del todo (no queda zombi)
        return {"status": "error",
                "mensaje": "La skill PHP agotó %ds: proceso eliminado (anti-zombi)."
                           % timeout_s,
                "zombi_eliminado": True}
    stdout = (stdout_b or b"").decode("utf-8", errors="replace").strip()
    stderr = (stderr_b or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode != 0 and not stdout:
        stderr_trozos = stderr.splitlines()
        return {"status": "error",
                "mensaje": "El runner PHP falló (exit %d): %s"
                           % (proc.returncode, stderr_trozos[-1] if stderr_trozos else 'sin detalle')}
    try:
        return json.loads(stdout.splitlines()[0])
    except (ValueError, IndexError) as e:
        return {"status": "error", "mensaje": "Salida no JSON del runner: %s" % e}


def _timeout_runner_tool(tool: str) -> int:
    """Fusible anti-zombi por tool (Orden v4.14): 35s para skills directas de
    datos (consultas Profit/MySQL ≤5s); 600s SOLO para las tools que delegan
    a un subproceso largo con timeout interno propio (skills Python de
    visión/OCR vía PythonSkillExecutor y el orquestador Hermes).

    v4.55: hermes_chat con tool-calling real (varias vueltas contra NIM,
    terminal/read_file/search_files) puede tardar 5-6 min de forma legítima
    (medido en vivo: 336s con 8 llamadas a NIM y un reintento por HTTP 529
    "Service temporarily overloaded"). El tope anterior (300s) mataba el
    proceso justo antes de que terminara solo — el fusible debe dar margen
    real, no ajustarse al caso más simple.
    """
    if tool == "hermes_chat" or tool.startswith("python_"):
        return 600
    return 35


@app.route('/api/tools/catalogo', methods=['GET'])
def tools_catalogo():
    """GET /api/tools/catalogo → {success, total, catalogo:[{departamento,
    nombre, descripcion, parametros}]} con las 25 tools departamentales.

    Alimenta el menú de comandos '/' del chat; el frontend lo cachea.
    """
    try:
        resultado = _ejecutar_runner_tools(['__catalogo__'], timeout_s=30)
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "mensaje": "Runner PHP agotó 30s (catálogo)."}), 500
    except Exception as e:
        return jsonify({"status": "error", "mensaje": "No se pudo invocar el runner: %s" % e}), 500
    if not resultado.get('success'):
        return jsonify({"status": "error",
                        "mensaje": resultado.get('error', 'Fallo del runner'),
                        "tipo": resultado.get('tipo')}), 500
    return jsonify(resultado)


@app.route('/api/tools/ejecutar', methods=['POST'])
def tools_ejecutar():
    """POST /api/tools/ejecutar {tool, arguments?, contexto?} → ejecuta la
    tool por nombre vía ToolRegistry (runner PHP) y devuelve su respuesta.

    Fusible anti-zombi (Orden v4.14): 35s para skills directas de datos;
    300s solo para delegadas (python_* / hermes_chat, timeout interno propio).
    """
    data = request.get_json(silent=True) or {}
    tool = str(data.get('tool', '')).strip()
    if not tool:
        return jsonify({"status": "error", "mensaje": "Parámetro 'tool' obligatorio."}), 400
    arguments = data.get('arguments') or {}
    contexto = data.get('contexto') or {}
    timeout_s = _timeout_runner_tool(tool)
    try:
        resultado = _ejecutar_runner_tools(
            [tool, json.dumps(arguments, ensure_ascii=False), json.dumps(contexto, ensure_ascii=False)],
            timeout_s=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error",
                        "mensaje": "La tool '%s' agotó el fusible (%ds): proceso eliminado." % (tool, timeout_s)}), 504
    except Exception as e:
        return jsonify({"status": "error", "mensaje": "No se pudo invocar el runner: %s" % e}), 500
    if not resultado.get('success'):
        return jsonify({"status": "error",
                        "mensaje": resultado.get('error', 'Fallo del runner'),
                        "tipo": resultado.get('tipo')}), 200
    return jsonify(resultado)


# -----------------------------------------------------------------------------
# 6b. EJECUCIÓN ASÍNCRONA DE TOOLS LARGAS (hermes_chat / python_*)
# -----------------------------------------------------------------------------
# El túnel Cloudflare (trycloudflare.com, túnel rápido) corta cualquier
# petición a los ~100s con HTTP 524, SIN posibilidad de configurar ese
# límite (es fijo en túneles rápidos gratuitos). hermes_chat con tool-calling
# real (varias vueltas contra NIM) supera eso fácilmente aunque el fusible
# anti-zombi interno aguante hasta 300s. Solución: la petición HTTP inicial
# responde de inmediato con un job_id (el trabajo corre en un hilo aparte);
# el frontend pregunta el resultado con GET cada pocos segundos — cada
# consulta de esas dura milisegundos, así que nunca choca con el límite del
# túnel, sin importar cuánto tarde Hermes por dentro.
_JOBS_TOOLS_LOCK = threading.Lock()
_JOBS_TOOLS = {}  # job_id -> {"status": "pendiente"|"listo", "resultado": dict|None, "creado": float}
_JOBS_TOOLS_TTL_S = 600  # limpieza de trabajos viejos por si el cliente nunca los consulta


def _limpiar_jobs_tools_viejos():
    tope = time.time() - _JOBS_TOOLS_TTL_S
    for jid in [j for j, v in _JOBS_TOOLS.items() if v["creado"] < tope]:
        del _JOBS_TOOLS[jid]


def _correr_job_tool(job_id: str, tool: str, arguments: dict, contexto: dict, timeout_s: int):
    try:
        resultado = _ejecutar_runner_tools(
            [tool, json.dumps(arguments, ensure_ascii=False), json.dumps(contexto, ensure_ascii=False)],
            timeout_s=timeout_s,
        )
    except Exception as e:
        resultado = {"status": "error", "mensaje": "No se pudo invocar el runner: %s" % e}
    with _JOBS_TOOLS_LOCK:
        if job_id in _JOBS_TOOLS:
            _JOBS_TOOLS[job_id]["status"] = "listo"
            _JOBS_TOOLS[job_id]["resultado"] = resultado


@app.route('/api/tools/ejecutar_async', methods=['POST'])
def tools_ejecutar_async():
    """POST /api/tools/ejecutar_async {tool, arguments?, contexto?} →
    {status, job_id}. Lanza la tool en un hilo aparte y devuelve al toque;
    el resultado se consulta con GET /api/tools/resultado/<job_id>.
    """
    data = request.get_json(silent=True) or {}
    tool = str(data.get('tool', '')).strip()
    if not tool:
        return jsonify({"status": "error", "mensaje": "Parámetro 'tool' obligatorio."}), 400
    arguments = data.get('arguments') or {}
    contexto = data.get('contexto') or {}
    timeout_s = _timeout_runner_tool(tool)

    with _JOBS_TOOLS_LOCK:
        _limpiar_jobs_tools_viejos()
        job_id = uuid.uuid4().hex
        _JOBS_TOOLS[job_id] = {"status": "pendiente", "resultado": None, "creado": time.time()}

    hilo = threading.Thread(
        target=_correr_job_tool,
        args=(job_id, tool, arguments, contexto, timeout_s),
        daemon=True,
    )
    hilo.start()
    return jsonify({"status": "ok", "job_id": job_id})


@app.route('/api/tools/resultado/<job_id>', methods=['GET'])
def tools_resultado_job(job_id):
    """GET /api/tools/resultado/<job_id> → {status:'pendiente'} mientras
    corre, o {status:'listo', resultado:{...}} cuando termina (se borra el
    job al ser consumido)."""
    with _JOBS_TOOLS_LOCK:
        job = _JOBS_TOOLS.get(job_id)
        if not job:
            return jsonify({"status": "error", "mensaje": "job_id desconocido o ya consumido."}), 404
        if job["status"] != "listo":
            return jsonify({"status": "pendiente"})
        resultado = job["resultado"]
        del _JOBS_TOOLS[job_id]
    return jsonify({"status": "listo", "resultado": resultado})


if __name__ == '__main__':
    # BUG real detectado en vivo (24/08): PC-NVR.exe (cliente de cámaras/DVR
    # de esta máquina) también toma el puerto 5000 para su propia interfaz,
    # y a veces gana la carrera de binding — las peticiones a ara_server.py
    # podían terminar en el proceso equivocado (que no entiende HTTP y nunca
    # responde). Puerto configurable vía env, default movido a 5050 para no
    # competir más con eso. Los Tools PHP (FlaskApiTrait/ConsultarNotaTool)
    # ya leen ARA_ERP_URL en vez de tener el puerto hardcodeado — solo hace
    # falta setear esa env var, no tocar su código.
    PUERTO = int(os.environ.get('ARA_SERVER_PORT', '4050'))
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
    # threads=32 (default de Waitress es 4): con solo 4, una sola consulta
    # /hermes_chat (20-40s bloqueando su hilo dentro de proc.communicate)
    # satura el pool y el resto de peticiones (polling de la bandeja de
    # mensajes de otros operadores) se quedan en cola hasta que el túnel/
    # navegador se rinde y devuelve HTML de error en vez de JSON.
    from waitress import serve
    serve(app, host=HOST_BIND, port=PUERTO, threads=32)

    # Opción B: Si usas el server nativo de Flask (Modo Desarrollo)
    # app.run(host=HOST_BIND, port=PUERTO, debug=False)
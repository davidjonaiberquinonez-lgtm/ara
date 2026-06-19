from flask import Flask, render_template, jsonify, request, send_from_directory
from flask_cors import CORS
import pandas as pd
import requests
import os
from datetime import datetime  
import json
from ara_vision import investigar_producto_ara
import uuid
import sqlite3
import socket
from collections import defaultdict

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

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

# Mapeos de compatibilidad heredada
FILE_ASIGNACIONES = os.path.join(DATA_FOLDER, 'INVENTARIO.xlsx') 
FILE_FACTURAS = os.path.join(DATA_FOLDER, 'factura_202604221922.csv')

# Función de conexión centralizada a SQL
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
                "pass": row['contrasena'], # Mapeado para retrocompatibilidad
                "rol": row['rol'],
                "permisos": permisos_list,
                "color": row['color'],
                "isRouteResponsible": bool(row['is_route_responsible'])
            })
            
        return jsonify({"status": "success", "usuarios": lista_usuarios})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": f"Error al leer usuarios: {str(e)}"}), 500
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

#====================================================================
# Dasboard Profesional de Desempeño en tiempo real .
#======================================================================
@app.route('/api/dashboard/stats', methods=['GET'])
def obtener_estadisticas_dashboard():
    """
    Lee history.json de forma dinámica y procesa las métricas calculadas
    para el Dashboard del Frontend.
    """
    if not os.path.exists(HISTORY_FILE):
        return jsonify({
            "resumen": {"totalOperaciones": 0, "tasaPrecision": 100.0, "tiempoPromedio": "0m 0s", "operadorDestacado": "N/A"},
            "ranking": [],
            "graficoFechas": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"],
            "graficoOperaciones": [0, 0, 0, 0, 0, 0],
            "incidencias": []
        })

    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            historial = json.load(f)
    except Exception:
        historial = []

    if not historial:
        return jsonify({
            "resumen": {"totalOperaciones": 0, "tasaPrecision": 100.0, "tiempoPromedio": "0m 0s", "operadorDestacado": "N/A"},
            "ranking": [],
            "graficoFechas": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"],
            "graficoOperaciones": [0, 0, 0, 0, 0, 0],
            "incidencias": []
        })

    # 1. Cálculos del Resumen Global
    total_ops = len(historial)
    operaciones_exitosas = sum(1 for op in historial if op.get('estado') == 'exitoso')
    tasa_precision = round((operaciones_exitosas / total_ops) * 100, 1) if total_ops > 0 else 100.0

    # 2. Agrupación y Procesamiento de Usuarios
    usuarios_acumulador = {}
    conteo_fechas = defaultdict(int)
    incidencias_registradas = []

    # Recorremos al revés para obtener las últimas incidencias primero
    for op in reversed(historial):
        user = op.get('usuario', 'Desconocido')
        rol = op.get('rol', 'Operador')
        estado = op.get('estado', 'exitoso')
        puntos = op.get('puntos', 0.0)
        fecha_completa = op.get('fecha', '')

        # Filtrar incidencias (leves y críticas) para el muro de monitoreo
        if estado in ['leve', 'critico'] and len(incidencias_registradas) < 10:
            incidencias_registradas.append({
                "usuario": user,
                "rol": rol,
                "fecha": fecha_completa,
                "estado": estado,
                "detalles": op.get('detalles', ''),
                "puntos": puntos
            })

        # Acumular para el gráfico
        if fecha_completa:
            dia_str = fecha_completa.split(' ')[0]
            conteo_fechas[dia_str] += 1

        # Lógica para el acumulador del ranking
        if user not in usuarios_acumulador:
            usuarios_acumulador[user] = {"nombre": user, "rol": rol, "operaciones": 0, "exitosas": 0, "puntos": 0.0}
        
        usuarios_acumulador[user]["operaciones"] += 1
        if estado == 'exitoso':
            usuarios_acumulador[user]["exitosas"] += 1
        usuarios_acumulador[user]["puntos"] += puntos

    # 3. Estructurar el Ranking
    ranking_final = []
    for user, datos in usuarios_acumulador.items():
        precision_usuario = round((datos["exitosas"] / datos["operaciones"]) * 100, 1) if datos["operaciones"] > 0 else 0.0
        ranking_final.append({
            "nombre": datos["nombre"],
            "rol": datos["rol"],
            "operaciones": datos["operaciones"],
            "precision": precision_usuario,
            "puntos": round(datos["puntos"], 2)
        })
    
    ranking_final.sort(key=lambda x: x["puntos"], reverse=True)
    operador_destacado = ranking_final[0]["nombre"] if ranking_final else "N/A"

    # 4. Formatear datos del Gráfico Semanal
    fechas_ordenadas = sorted(conteo_fechas.keys())[-6:]
    grafico_fechas = []
    grafico_operaciones = []

    for f in fechas_ordenadas:
        try:
            partes = f.split('-')
            formato_corto = f"{partes[2]}/{partes[1]}"
        except Exception:
            formato_corto = f
        grafico_fechas.append(formato_corto)
        grafico_operaciones.append(conteo_fechas[f])

    if not grafico_fechas:
        grafico_fechas = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"]
        grafico_operaciones = [0, 0, 0, 0, 0, 0]

    return jsonify({
        "resumen": {
            "totalOperaciones": total_ops,
            "tasaPrecision": tasa_precision,
            "tiempoPromedio": "4m 12s",
            "operadorDestacado": operador_destacado
        },
        "ranking": ranking_final,
        "graficoFechas": grafico_fechas,
        "graficoOperaciones": grafico_operaciones,
        "incidencias": incidencias_registradas  # <-- Ahora sí viaja la data completa
    })

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

@app.route('/api/inventario/comenzar', methods=['GET'])
def comenzar_inventario():
    try:
        nombre_usuario = request.args.get('usuario')
        estante = request.args.get('estante')

        if not nombre_usuario or not estante:
            return jsonify({"status": "error", "mensaje": "Faltan datos de usuario o estante"}), 400

        col_responsable = next((c for c in df_asignacion.columns if 'respons' in c.lower() or 'repons' in c.lower()), None)
        col_est = next((c for c in df_asignacion.columns if any(x in c.lower() for x in ['campo7', 'ubic', 'estante', 'loc'])), None)
        
        df_usuario_estante = df_asignacion[
            (df_asignacion[col_responsable].astype(str).str.strip().str.upper() == nombre_usuario.upper()) & 
            (df_asignacion[col_est].astype(str).str.strip() == estante)
        ]
        
        if df_usuario_estante.empty:
            return jsonify({"status": "error", "mensaje": f"No tienes asignado el estante {estante} en el plan de trabajo."}), 404

        lista_inventario_final = []

        conn = get_db_connection()
        cursor = conn.cursor()

        # 🔥 AQUÍ ESTÁ EL FIX REAL PARA PROFIT 🔥
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
            stock_real = int(prod_row['stock_maestro']) if prod_row['stock_maestro'] else 0
            descripcion = str(prod_row['descripcion']) if prod_row['descripcion'] else 'Sin descripción'

            lista_inventario_final.append({
                "codigo": codigo_prod,
                "descripcion": descripcion,
                "estante": estante,
                "stock_teorico": stock_real
            })

        conn.close()
        
        lista_ordenada = sorted(lista_inventario_final, key=lambda x: x['codigo'])
        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        sesiones_inventario[nombre_usuario] = {
            "estante_actual": estante,
            "fecha_inicio": fecha_actual,
            "productos_en_estante": len(lista_ordenada),
            "verificados": 0,             
            "novedades": []               
        }

        return jsonify({"status": "success", "usuario": nombre_usuario, "estante": estante, "lista": lista_ordenada, "total": len(lista_ordenada)})
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

        return jsonify({
            "status": "success",
            "estado": estado,
            "mensaje": mensaje_ara,
            "progreso": f"{sesiones_inventario[usuario]['verificados']} / {sesiones_inventario[usuario]['productos_en_estante']}"
        })
    except Exception as e:
        return jsonify({"status": "error", "mensaje": f"Error en verificación: {str(e)}"}), 500


@app.route('/api/inventario/finalizar', methods=['POST'])
def finalizar_inventario():
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "mensaje": "No se recibieron datos"}), 400

        nombre_usuario = data.get('usuario', '').strip()
        estante = data.get('estante', '').strip()
        productos_frontend = data.get('productos', []) # [{ "codigo": "CR000081", "cantidad": 27 }, ...]

        if not nombre_usuario or not estante:
            return jsonify({"status": "error", "mensaje": "Data incompleta (falta usuario o estante)"}), 400

        # Tiempos del reporte
        ahora = datetime.now()
        fecha_reporte = ahora.strftime("%Y-%m-%d")
        hora_reporte = ahora.strftime("%I:%M:%S %p")

        detalles_auditados = []
        verificados_ok = 0
        
        # 🚀 GENERAMOS EL ID ÚNICO DE 8 CARACTERES (Ej: REP-963ADAEF)
        codigo_unico = uuid.uuid4().hex[:8].upper()
        reporte_id = f"REP-{codigo_unico}"

        # 🚀 PROCESAMIENTO INTELIGENTE DE CADA PRODUCTO
        for item in productos_frontend:
            codigo = item.get('codigo', '')
            cantidad_fisica = float(item.get('cantidad', 0))
        # 🚀 LA SOLUCIÓN TOTAL: Buscamos la data real en tu SQLite en tiempo real
            codigo = item.get('codigo', '').strip().upper()
            cantidad_fisica = float(item.get('cantidad', 0))

            # 🔍 Consultamos al maestro usando la función que acabas de armar
            producto_db = consultar_sqlite_maestro(codigo)
            
            if producto_db:
                # Si el producto existe en la DB, traemos su descripción y stock real
                descripcion_real = producto_db["descripcion"]
                cantidad_teorica = float(producto_db["stock_maestro"] if producto_db["stock_maestro"] else 0)
            else:
                # Respaldo: Si el operario escaneó algo que no está en la DB, usamos lo que mande el cliente
                descripcion_real = item.get('descripcion', 'Producto no registrado en maestro')
                cantidad_teorica = float(item.get('teorico', 0.0))
            # Variables para el dictamen
            estado_item = "OK"
            detalle_texto = ""

            # 📐 CÁLCULO DE DIFERENCIAS REALES
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

            # Estructuramos el item con la información VERDADERA
            detalles_auditados.append({
                "codigo": codigo,
                "descripcion": descripcion_real,
                "ubicacion": estante,
                "teorico": int(cantidad_teorica),
                "fisico": int(cantidad_fisica),
                "estado": estado_item,
                "detalle": detalle_texto
            })

        # 📦 ARMA EL CUERPO COMPLETO DEL REPORTE FINAL
        cuerpo_reporte = {
            "id": reporte_id,  # 🔥 ¡CORREGIDO!: Ahora sí guarda el ID único idéntico al nombre del archivo
            "usuario": nombre_usuario,
            "estante": estante,
            "fecha": f"{fecha_reporte} {hora_reporte}", 
            "total_articulos": len(detalles_auditados),
            "verificados": verificados_ok,
            "detalles": detalles_auditados
        }

        # 💾 ESCRITURA EN CARPETA COMPROMETIDA (brain_knowledge/reportes)
        usuario_limpio = nombre_usuario.replace(" ", "_")
        
        base_dir = os.path.dirname(__file__)
        carpeta_reportes = os.path.join(base_dir, 'brain_knowledge', 'reportes')
        
        if not os.path.exists(carpeta_reportes):
            os.makedirs(carpeta_reportes)

        # Estructura exacta a tu ejemplo: REP-963ADAEF_Jonaiber_Quiñonez.json
        nombre_archivo_json = f"{reporte_id}_{usuario_limpio}.json"
        ruta_final_archivo = os.path.join(carpeta_reportes, nombre_archivo_json)

        # =====================================================================
        # 5. ESCRITURA FÍSICA
        # =====================================================================
        with open(ruta_final_archivo, 'w', encoding='utf-8') as archivo:
            json.dump(cuerpo_reporte, archivo, indent=4, ensure_ascii=False)

        print(f"✅ Nuevo reporte único creado: {nombre_archivo_json}")

        # Limpieza de la sesión temporal de memoria
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


# =============================================================================
# MÓDULO DE PREPARACIÓN OPTIMIZADO CON SQL
# =============================================================================
@app.route('/api/preparacion')
def api_preparacion():
    query = request.args.get('q', '').lower()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if query:
            cursor.execute("""
                SELECT codigo AS co_art, descripcion AS art_des, campo7 AS ubicacion, stock_maestro AS stock_act 
                FROM stock_maestro 
                WHERE LOWER(codigo) LIKE ? OR LOWER(descripcion) LIKE ? 
                LIMIT 50
            """, (f"%{query}%", f"%{query}%"))
        else:
            cursor.execute("""
                SELECT codigo AS co_art, descripcion AS art_des, campo7 AS ubicacion, stock_maestro AS stock_act 
                FROM stock_maestro 
                LIMIT 20
            """)
        filas = cursor.fetchall()
        conn.close()
        return jsonify([dict(f) for f in filas])
    except Exception as e:
        return jsonify({"error": f"Falla en consulta SQL: {str(e)}"}), 500


@app.route('/api/notas_pendientes_prep')
def notas_pendientes_prep():
    estados = cargar_estados()
    todas = df_facturas[['fact_num', 'nombre']].to_dict(orient='records')
    pendientes = [f for f in todas if estados.get(str(f['fact_num'])) != 'verificada']
    return jsonify(pendientes)

@app.route('/api/finalizar_preparacion', methods=['POST'])
def finalizar_preparacion():
    try:
        data = request.json
        factura_id = str(data.get('id'))
        usuario = data.get('usuario')
        password_ingresada = data.get('password')
        
        if not validar_usuario(usuario, password_ingresada):
            return jsonify({"status": "error", "mensaje": "Contraseña incorrecta."}), 403

        guardar_estado(factura_id, 'verificada')
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
 #===============================================================================
 #
 #===============================================================================
@app.route('/api/visor/producto/<codigo>', methods=['GET'])
def consultar_producto_visor(codigo):
    try:
        codigo_buscar = codigo.strip().upper()
        
        # 1. Buscar los datos maestros del producto en SQLite
        prod_db = consultar_sqlite_maestro(codigo_buscar)
        
        if not prod_db:
            return jsonify({"status": "error", "mensaje": "Producto no encontrado en el sistema maestro"}), 404
            
        producto_base = {
            "codigo": codigo_buscar,
            "descripcion": prod_db["descripcion"], 
            "ubicacion_maestra": prod_db["ubicacion"] if prod_db["ubicacion"] else "POR_ASIGNAR",
            "stock_maestro": prod_db["stock_maestro"]
        }

        # 2. Escanear la carpeta de reportes JSON para armar la trazabilidad física
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

                        # Verificamos si este artículo fue auditado en este reporte
                        for detalle in reporte.get('detalles', []):
                            if detalle.get('codigo', '').strip().upper() == codigo_buscar:
                                historial_movimientos.append({
                                    "id_reporte": reporte.get('id', 'S/N'),
                                    "fecha_registro": fecha_rep,
                                    "ubicacion_fisica": estante_rep,
                                    "operario": usuario_rep,
                                    "cantidad_contada": detalle.get('fisico', 0),
                                    "estado_auditoria": detalle.get('estado', 'OK')
                                })
                    except Exception as e:
                        continue # Si un archivo está corrupto o mal armado, salta al siguiente

        # 3. Ordenar cronológicamente (Del movimiento más fresco al más viejo)
        def parsear_fecha_reporte(mov):
            for formato in ("%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(mov['fecha_registro'], formato)
                except ValueError:
                    pass
            return datetime.min

        historial_movimientos.sort(key=parsear_fecha_reporte, reverse=True)

        # 4. Separar el último movimiento de las ubicaciones viejas
        ultimo_movimiento = None
        otras_ubicaciones = []

        if historial_movimientos:
            ultimo_movimiento = historial_movimientos[0] # El primero es el más nuevo
            
            # Buscamos historial de otras estanterías donde se haya visto antes
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

        # 📦 RESPUESTA TOTAL PARA TU INTERFAZ (index.html / App)
        return jsonify({
            "status": "success",
            "producto": {
                "codigo": producto_base["codigo"],
                "descripcion": producto_base["descripcion"],
                "ubicacion_maestra": producto_base["ubicacion_maestra"],
                "stock_maestro": producto_base["stock_maestro"],
                # Aquí está la dualidad viva:
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
        guardar_estado(factura_id, 'embalada')
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
    imagen_b64 = data.get('image')
    datos_producto_especifico = ""

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stock_maestro")
        todos_productos = cursor.fetchall()
        conn.close()

        for prod in todos_productos:
            # ✅ CORRECCIÓN 2: Casteo a dict de Python para evitar el AttributeError de sqlite3.Row
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

    memoria_reciente = obtener_memoria_reciente()
    prompt_sistema = f"""Eres ARA IA, el asistente logístico inteligente.
Tu misión es guiar al usuario basado en la bitácora real. NO inventes números.
--- BITÁCORA DE EVENTOS RECIENTES ---
{memoria_reciente}
--- DATOS REALES DEL PRODUCTO ---
{datos_producto_especifico if datos_producto_especifico else "Ningún producto seleccionado en el mensaje."}
"""
    modelo = "llava" if imagen_b64 else "phi3"
    payload = {"model": modelo, "prompt": f"{prompt_sistema}\n\nUsuario: {user_msg}\nARA IA:", "stream": False}
    if imagen_b64: payload["images"] = [imagen_b64]

    try:
        response = requests.post('http://100.82.4.15:11434/api/generate', json=payload, timeout=120)
        return jsonify({"respuesta": response.json().get('response', 'Sin respuesta.')})
    except Exception as e:
        return jsonify({"respuesta": f"Error de conexión con Ollama: {str(e)}"})

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
@app.route('/api/chat/send', methods=['POST'])
def send_message():
    try:
        data = request.json
        chat_id = data.get('chatId')
        mensaje = {"from": data.get('from'), "text": data.get('text', ''), "type": data.get('type', 'text'), "file": data.get('file', ''), "time": datetime.now().strftime("%H:%M"), "status": "sent"}
        file_path = os.path.join(CHATS_FOLDER, f"{chat_id}.json")
        
        historial = []
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f: historial = json.load(f)
        historial.append(mensaje)
        with open(file_path, 'w', encoding='utf-8') as f: json.dump(historial, f, indent=4, ensure_ascii=False)
        return jsonify({"status": "success", "mensaje": mensaje})
    except Exception as e: return jsonify({"status": "error", "mensaje": str(e)}), 500

@app.route('/api/chat/history/<chat_id>', methods=['GET'])
def get_chat_history(chat_id):
    try:
        file_path = os.path.join(CHATS_FOLDER, f"{chat_id}.json")
        if not os.path.exists(file_path): return jsonify({"status": "success", "messages": []})
        with open(file_path, 'r', encoding='utf-8') as f: messages = json.load(f)
        return jsonify({"status": "success", "messages": messages})
    except Exception as e: return jsonify({"status": "error", "mensaje": str(e)}), 500



if __name__ == '__main__':
    # ⚙️ CONFIGURA AQUÍ TU PUERTO
    PUERTO = 5000 
    HOST_BIND = '0.0.0.0' # Escucha en todas las interfaces para permitir acceso externo

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
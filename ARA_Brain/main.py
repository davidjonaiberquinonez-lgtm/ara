from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import pandas as pd
import requests
import os
import json
import openrouteservice
from openrouteservice.optimization import Job, Vehicle, optimize

app = Flask(__name__)
CORS(app)

# --- CONFIGURACIÓN DE RUTAS Y API KEYS ---
ORS_KEY = 'TeyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjVlYmZmNzk4OGE3YzQ3MmNiZDk5NGI1MGE2MWJjMDhjIiwiaCI6Im11cm11cjY0In0=' # Reemplaza con tu llave de OpenRouteService
client = openrouteservice.Client(key=ORS_KEY)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(BASE_DIR, 'data')
EXCEL_PATH = 'art_2026-04-27(1).xlsx'

# --- CARGA DE DATOS ---
def cargar_inventario():
    try:
        df = pd.read_excel(EXCEL_PATH)
        return df.fillna('')
    except Exception as e:
        print(f"⚠️ Error cargando el Excel: {e}")
        return pd.DataFrame()

df_inventario = cargar_inventario()

# Carpeta para el "Cerebro" de ARA (Aprendizaje / Memoria)
BRAIN_FOLDER = os.path.join(BASE_DIR, 'brain_knowledge')
if not os.path.exists(BRAIN_FOLDER):
    os.makedirs(BRAIN_FOLDER)

# --- FUNCIONES DE SOPORTE ---

def obtener_datos_excel():
    if not os.path.exists(EXCEL_PATH):
        return None
    try:
        df = pd.read_excel(EXCEL_PATH, engine='openpyxl')
        return df.fillna('').to_dict(orient='records')
    except Exception as e:
        print(f"Error Excel: {e}")
        return None

def guardar_en_memoria(usuario, pregunta, respuesta):
    """Guarda la información para que ARA tenga 'conocimiento en mano'"""
    log_path = os.path.join(BRAIN_FOLDER, 'history.json')
    nueva_data = {"usuario": usuario, "p": pregunta, "r": respuesta}
    
    historial = []
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                historial = json.load(f)
        except: historial = []
    
    historial.append(nueva_data)
    # Mantenemos las últimas 200 interacciones para no saturar el JSON
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(historial[-200:], f, indent=4, ensure_ascii=False)

def obtener_memoria_reciente():
    """Recupera lo que ARA ha aprendido recientemente"""
    log_path = os.path.join(BRAIN_FOLDER, 'history.json')
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Retorna las últimas 5 charlas como texto para el prompt
            return str(data[-5:])
    return "No hay historial previo."

# --- RUTAS DEL SISTEMA ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/preparacion')
def preparacion():
    try:
        # 1. Obtener la palabra de búsqueda desde el teléfono
        query_busqueda = request.args.get('q', '').lower()
        
        # 2. Usar el DataFrame global que ya cargaste al inicio del archivo
        df = df_inventario 

        if df.empty:
            return jsonify({"error": "El inventario está vacío o no se cargó el Excel"}), 500

        # 3. FILTRAR los datos en el servidor (Mucho más rápido que en el teléfono)
        # Buscamos que la palabra esté en la descripción (art_des) o en el código (co_art)
        if query_busqueda:
            # Convertimos a string para evitar errores con números y buscamos la coincidencia
            filtro = df[
                df['art_des'].astype(str).str.lower().str.contains(query_busqueda) | 
                df['co_art'].astype(str).str.lower().str.contains(query_busqueda)
            ]
            # Limitamos a los primeros 50 resultados para no saturar el teléfono
            resultados = filtro.head(50).to_dict(orient='records')
        else:
            # Si no hay búsqueda, enviamos los primeros 20 artículos como ejemplo
            resultados = df.head(20).to_dict(orient='records')

        return jsonify(resultados)

    except Exception as e:
        print(f"Error en ruta preparacion: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    user_msg = data.get('message', '')
    imagen_b64 = data.get('image') 
    
    # Contexto: Inventario + Lo que ARA ha "aprendido" (Memoria)
    datos_inv = obtener_datos_excel()
    contexto_inv = str(datos_inv[:5]) if datos_inv else "Sin datos"
    memoria = obtener_memoria_reciente()
    
    # Lógica de modelo
    modelo = "llava" if imagen_b64 else "phi3"
    
    prompt_completo = (
        f"Eres ARA IA, asistente logístico inteligente. "
        f"Memoria de aprendizaje reciente: {memoria}. "
        f"Contexto inventario: {contexto_inv}. "
        f"Pregunta del trabajador: {user_msg}"
    )

    payload = {
        "model": modelo,
        "prompt": prompt_completo,
        "stream": False
    }
    
    if imagen_b64:
        payload["images"] = [imagen_b64]

    try:
        # Petición a Ollama
        response = requests.post('http://100.82.4.15:11434/api/generate', json=payload, timeout=45)
        res_data = response.json()
        respuesta_ia = res_data.get('response', 'ARA está procesando...')

        # ARA guarda lo aprendido en su 'cerebro' JSON
        guardar_en_memoria("trabajador", user_msg, respuesta_ia)

        return jsonify({"respuesta": respuesta_ia})
    except Exception as e:
        return jsonify({"respuesta": f"Error: Verifica que Ollama esté corriendo. {str(e)}"})

# --- MÓDULO RUTAGRAMA EN TIEMPO REAL ---

@app.route('/api/rutas/optimizar', methods=['POST'])
def optimizar_rutas():
    """
    Recibe un JSON con puntos: {"puntos": [[lon, lat], [lon, lat], ...]}
    El primer punto se asume como el Depósito/Almacén.
    """
    data = request.json
    coords = data.get('puntos', [])

    if len(coords) < 2:
        return jsonify({"status": "error", "message": "Se necesitan al menos 2 puntos"}), 400

    try:
        # Definimos los puntos de entrega (Jobs)
        jobs = [Job(id=i, location=c) for i, c in enumerate(coords[1:])]
        
        # Definimos el vehículo (Sale y vuelve al punto 0)
        vehicles = [Vehicle(
            id=0,
            profile='driving-car',
            start=coords[0],
            end=coords[0]
        )]

        # OPTIMIZACIÓN vía ORS
        result = optimize(client, jobs=jobs, vehicles=vehicles)
        
        return jsonify({
            "status": "success",
            "optimizacion": result
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    # Tu IP: 192.168.1.39
    print(f"\n--- ARA BETA 1.1 ACTIVA (IP: 192.168.6.82) ---")
    app.run(host='192.168.6.82', port=5000, debug=True)
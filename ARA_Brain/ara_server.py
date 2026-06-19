from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import pandas as pd
import requests
import os

app = Flask(__name__)
CORS(app)

# --- CONFIGURACIÓN DE RUTAS ---
# Esto asegura que Python encuentre la carpeta /data/ sin importar dónde lo ejecutes
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(BASE_DIR, 'data')
FILE_NAME = 'art_2026-04-27(1).xlsx'
EXCEL_PATH = os.path.join(DATA_FOLDER, FILE_NAME)

def obtener_datos_excel():
    """Función para localizar y leer el archivo en la carpeta data"""
    # 1. Verificar si la carpeta data existe
    if not os.path.exists(DATA_FOLDER):
        print(f"--- ERROR: La carpeta '{DATA_FOLDER}' no existe ---")
        return None

    # 2. Verificar si el archivo específico existe
    if os.path.exists(EXCEL_PATH):
        try:
            print(f"--- Leyendo archivo: {FILE_NAME} ---")
            df = pd.read_excel(EXCEL_PATH, engine='openpyxl')
            return df.fillna('').to_dict(orient='records')
        except Exception as e:
            print(f"--- Error al procesar el Excel: {e} ---")
            return None
    else:
        # Si no lo encuentra, te muestra en la terminal qué archivos SÍ hay
        archivos_presentes = os.listdir(DATA_FOLDER)
        print(f"--- ARCHIVO NO ENCONTRADO ---")
        print(f"Buscaba: {FILE_NAME}")
        print(f"Archivos detectados en /data/: {archivos_presentes}")
        return None

# --- RUTAS ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/preparacion')
def api_preparacion():
    datos = obtener_datos_excel()
    if datos is not None:
        return jsonify(datos)
    else:
        return jsonify({
            "status": "error",
            "message": f"Archivo {FILE_NAME} no encontrado en carpeta /data/"
        }), 404

@app.route('/api/chat', methods=['POST'])
def chat():
    user_msg = request.json.get('message', '')
    datos = obtener_datos_excel()
    
    # Le pasamos solo una muestra a la IA para no saturarla
    contexto = str(datos[:5]) if datos else "No hay datos disponibles."
    
    prompt = f"Eres Ara IA, asistente logístico. Inventario actual: {contexto}. Pregunta: {user_msg}"

    try:
        response = requests.post('http://localhost:11434/api/generate', 
            json={
                "model": "deepseek-coder:1.3b",
                "prompt": prompt,
                "stream": False
            }, timeout=15)
        
        res_data = response.json()
        return jsonify({"respuesta": res_data.get('response', 'Sin respuesta de la IA.')})
    except Exception as e:
        return jsonify({"respuesta": "Error: Asegúrate de que Ollama esté activo en la PC."})

if __name__ == '__main__':
    # Usamos tu IP fija 192.168.1.49
    print(f"\n--- ARA BRAIN ACTIVO EN: http://192.168.6.123:5000 ---")
    app.run(host='192.168.6.123', port=5000, debug=True)
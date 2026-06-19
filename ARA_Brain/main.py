from flask import Flask, render_template, request, jsonify
import pandas as pd
import requests
import json

app = Flask(__name__)

# --- CONFIGURACIÓN ---
EXCEL_PATH = 'art_2026-04-27(1).xlsx'
OLLAMA_URL = "http://localhost:11434/api/generate" # URL estándar de Ollama
MODELO_IA = "deepseek-r1:7b" # Cambia a "llama3" si prefieres el otro

# --- CARGA DE DATOS ---
def cargar_inventario():
    try:
        df = pd.read_excel(EXCEL_PATH)
        return df.fillna('')
    except Exception as e:
        print(f"⚠️ Error cargando el Excel: {e}")
        return pd.DataFrame()

df_inventario = cargar_inventario()

# --- FUNCIONES AUXILIARES ---
def consultar_ollama(prompt):
    payload = {
        "model": MODELO_IA,
        "prompt": prompt,
        "stream": False
    }
    try:
        # Intentamos conectar con Ollama
        response = requests.post(OLLAMA_URL, json=payload, timeout=30)
        return response.json().get("response", "No pude procesar la respuesta.")
    except Exception as e:
        return f"Error: No se pudo conectar con Ollama en {OLLAMA_URL}. Verifica que esté activo."

# --- RUTAS ---

@app.route('/')
def index():
    return render_template('index.html')

# API para el Visor de Artículos (Búsqueda manual)
@app.route('/api/preparacion', methods=['GET'])
def get_preparacion():
    # Recargamos brevemente para asegurar datos frescos
    df = cargar_inventario()
    datos = df.to_dict(orient='records')
    return jsonify(datos)

# API para el Asistente ARA IA (Chat con DeepSeek)
@app.route('/api/ia/consultar', methods=['POST'])
def chat_ara():
    data = request.json
    pregunta_usuario = data.get("pregunta", "").lower()
    
    if not pregunta_usuario:
        return jsonify({"respuesta": "Dime, ¿en qué puedo ayudarte con el inventario?"})

    # 1. Lógica de Filtrado de Contexto
    # Si el usuario pregunta por un laboratorio o producto específico
    if "la sante" in pregunta_usuario:
        filtro = df_inventario[df_inventario['art_des'].str.contains("LA SANTE", case=False, na=False)]
        contexto_datos = filtro[['art_des', 'campo7', 'stock_act']].to_string()
    elif "atamel" in pregunta_usuario:
        filtro = df_inventario[df_inventario['art_des'].str.contains("ATAMEL", case=False, na=False)]
        contexto_datos = filtro[['art_des', 'campo7', 'stock_act']].to_string()
    else:
        # Si es una pregunta general, enviamos los primeros 30 items para ahorrar memoria
        contexto_datos = df_inventario[['art_des', 'campo7', 'stock_act']].head(30).to_string()

    # 2. Construir el Prompt Maestro
    prompt_final = f"""
    Eres ARA, el asistente inteligente de logística de David. 
    Tu tarea es responder preguntas sobre el inventario usando exclusivamente los DATOS proporcionados.
    Responde de forma natural, profesional y en español.

    DATOS ACTUALES DEL INVENTARIO:
    {contexto_datos}

    PREGUNTA DEL OPERADOR:
    {pregunta_usuario}

    Instrucciones críticas:
    - Si preguntan por cantidades totales, suma el 'stock_act' de los productos coincidentes.
    - Menciona ubicaciones (campo7) si te preguntan dónde están los productos.
    - Si el producto no aparece en los datos proporcionados, di que no lo encuentras en el sistema actual.
    """

    # 3. Obtener respuesta de la IA
    respuesta = consultar_ollama(prompt_final)
    
    return jsonify({"respuesta": respuesta})

if __name__ == '__main__':
    # Usamos port 5000 por defecto
    print(f"🚀 Sistema ARA_Brain activo. Usando modelo: {MODELO_IA}")
    app.run(debug=True, host='0.0.0.0', port=5000)
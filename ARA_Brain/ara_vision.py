import requests
import os
import sqlite3
from duckduckgo_search import DDGS

# =============================================================================
# CONFIGURACIÓN CENTRALIZADA
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'proyecto_ara.db')

# IP Unificada con tu servidor central de Ollama
OLLAMA_URL = "http://100.82.4.15:11434/api/generate"

def llamar_deepseek_ligero(prompt):
    """Cerebro de ARA (Phi3) para resumir información técnica o de la web"""
    payload = {
        "model": "phi3",
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": 150, "temperature": 0.1, "num_thread": 2}
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=15)
        # ✅ CORREGIDO: Eliminadas barras invertidas que rompían la sintaxis del string
        return response.json().get('response', '')
    except Exception as e:
        print(f"❌ Error en Ollama (ara_vision): {e}")
        return "Error conectando con el cerebro de ARA."

def buscar_en_sql_local(texto_buscado):
    """Busca el producto directamente en el Stock Maestro de SQLite"""
    try:
        if not texto_buscado or str(texto_buscado).strip() == "":
            print("⚠️ Búsqueda abortada: El texto de la imagen está vacío.")
            return None

        termino = f"%{str(texto_buscado).strip().upper()}%"
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Buscamos por código de artículo, código de barra o descripción
        cursor.execute('''
            SELECT * FROM stock_maestro 
            WHERE UPPER(codigo) LIKE ? 
               OR UPPER(codigo_barra) LIKE ? 
               OR UPPER(descripcion) LIKE ?
            LIMIT 1
        ''', (termino, termino, termino))
        
        fila = cursor.fetchone()
        conn.close()
        
        if fila:
            return dict(fila)
        return None
    except Exception as e:
        print(f"❌ Error en búsqueda local SQL: {e}")
        return None

def investigar_producto_ara(texto_extraido):
    """Flujo Inteligente: SQL Local -> Fallback Web -> Resumen LLM"""
    # 1. Intentar buscar en la base de datos local unificada
    producto = buscar_en_sql_local(texto_extraido)
    
    if producto:
        # ✅ CORREGIDO: Cambiado a f-string de triple comilla limpia (sin paréntesis de cierre peligrosos)
        mensaje = f"""📦 PRODUCTO LOCALIZADO EN MAESTRO SQL:
📝 Descripción: {producto.get('descripcion', 'Sin detalle')}
🔢 Código: {producto.get('codigo', 'N/A')}
🏪 Stock Piso (Despacho): {producto.get('stock_maestro', 0)} unds
📦 Stock Reserva (Bulto Cerrado): {producto.get('stock_bulto_cerrado', 0)} bultos
📍 Ubicación Estante: {producto.get('campo7', 'Sin Ubicación')}"""
        
        return {"tipo": "local", "mensaje": mensaje, "data": producto}

    # 2. Si no existe en el catálogo, salta el agente de IA a buscar en la Web
    print(f"🔍 {texto_extraido} no está en SQL. Activando rastreo en internet...")
    try:
        with DDGS() as ddgs:
            resultados_web = [r['body'] for r in ddgs.text(f"{texto_extraido} medicamento farmacia", max_results=3)]
            
        if resultados_web:
            contexto_web = "\n".join(resultados_web)
            # ✅ CORREGIDO: Estructura de prompt limpia y directa
            prompt = f"""Actúa como el asistente del almacén. El sistema escaneó un producto que no está en la base de datos: '{texto_extraido}'.
Basándote en esta información de internet, explica brevemente qué es y para qué sirve (máximo 3 líneas):

{contexto_web}"""
            
            respuesta_llm = llamar_deepseek_ligero(prompt)
            return {
                "tipo": "web", 
                "mensaje": f"🌐 No encontrado en inventario local, pero esto encontré en la red:\n\n{respuesta_llm}"
            }
    except Exception as e:
        print(f"⚠️ Falla en rastreo web: {e}")

    return {
        "tipo": "desconocido", 
        "mensaje": f"❌ El código o término '{texto_extraido}' no coincide con ningún registro local ni web."
    }
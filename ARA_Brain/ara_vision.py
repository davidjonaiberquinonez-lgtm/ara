import requests
import json
from duckduckgo_search import DDGS

def llamar_deepseek_ligero(prompt):
    """Llamada optimizada a DeepSeek 1.3b para no trabar la laptop"""
    url = "http://localhost:11434/api/generate"
    payload = {
        "model": "deepseek-coder:1.3b",
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": 100, # Respuesta corta y rápida
            "temperature": 0.1, # Menos alucinación, más precisión
            "num_thread": 2     # Limita el uso de núcleos para que no se pegue
        }
    }
    try:
        response = requests.post(url, json=payload)
        return response.json().get("response", "")
    except:
        return "Error conectando con el cerebro de ARA."

def investigar_producto_ara(texto_extraido):
    # 1. PASO: Intento de búsqueda en SQL local
    # (Aquí va tu función de conexión a SQL que definimos antes)
 def buscar_en_sql_inventario(nombre_producto):
    """Simulador temporal de inventario para la demostración"""
    # Aquí puedes poner un par de productos reales para probar el éxito local
    inventario_demo = {
        "ATAMEL": {"nombre": "Atamel 500mg", "cantidad": 50, "ubicacion": "Pasillo 1", "notas": "Lote nuevo"},
        "ASPIRINA": {"nombre": "Aspirina 100mg", "cantidad": 100, "ubicacion": "Pasillo 3", "notas": "Sin observaciones"}
    }
    
    # Limpiamos el texto para comparar
    busqueda = nombre_producto.upper().strip()
    
    if busqueda in inventario_demo:
        return inventario_demo[busqueda]
    
    # Si no está aquí, devolverá None y ARA saltará automáticamente a DuckDuckGo
    return None
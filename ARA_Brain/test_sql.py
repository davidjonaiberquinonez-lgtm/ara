import pyodbc
from config import DB_CONFIG

def probar_conexion():
    print("--- PROBANDO CONEXIÓN AL SERVIDOR PROFIT ---")
    try:
        # Construimos la cadena con los datos de tu config.py
        conn_str = (
            f"DRIVER={DB_CONFIG['driver']};"
            f"SERVER={DB_CONFIG['server']};"
            f"DATABASE={DB_CONFIG['database']};"
            f"UID={DB_CONFIG['user']};"
            f"PWD={DB_CONFIG['password']}"
        )
        
        print(f"Intentando conectar a: {DB_CONFIG['server']}...")
        conexion = pyodbc.connect(conn_str, timeout=5)
        
        print("✅ ¡CONEXIÓN EXITOSA!")
        print(f"Versión del Servidor: {conexion.getinfo(pyodbc.SQL_DBMS_VER)}")
        
        conexion.close()
        
    except Exception as e:
        print("❌ ERROR DE CONEXIÓN")
        print(f"Detalle: {e}")
        print("\nPosibles causas:")
        print("1. La IP o el Usuario son incorrectos.")
        print("2. El Servidor SQL no tiene habilitado el protocolo TCP/IP.")
        print("3. El Firewall de la empresa bloquea el puerto 1433.")

if __name__ == "__main__":
    probar_conexion()
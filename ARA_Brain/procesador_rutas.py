import pandas as pd
import pyodbc # La librería para hablar con SQL Server
from config import DB_CONFIG

def cargar_datos_reales():
    """Conecta a Profit Plus y extrae las facturas del día."""
    try:
        # Creamos la conexión
        conn_str = (
            f"DRIVER={DB_CONFIG['driver']};"
            f"SERVER={DB_CONFIG['server']};"
            f"DATABASE={DB_CONFIG['database']};"
            f"UID={DB_CONFIG['user']};"
            f"PWD={DB_CONFIG['password']}"
        )
        
        conn = pyodbc.connect(conn_str)
        
        # Aquí escribimos la consulta SQL (Query) para traer los pedidos
        # Este es un ejemplo, mañana lo ajustamos a tus tablas reales
        query = "SELECT fec_emis, doc_num, cli_des, zona, tot_neto FROM saFactura WHERE fec_emis >= CAST(GETDATE() AS DATE)"
        
        df = pd.read_sql(query, conn)
        conn.close()
        return df
        
    except Exception as e:
        print(f"[ERROR] No se pudo conectar a la base de datos: {e}")
        return None
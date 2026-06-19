# Configuración general de ARA_Brain
import os

# Rutas de carpetas (esto ayudará a procesador_rutas.py)
RUTA_BASE = os.path.dirname(os.path.abspath(__file__))
CARPETA_DATOS = os.path.join(RUTA_BASE, "datos")
CARPETA_RESULTADOS = os.path.join(RUTA_BASE, "resultados")

# Configuraciones de Pandas
FORMATO_FECHA = "%d/%m/%Y"

print("Configuración cargada correctamente.")
# CREDENCIALES DE ACCESO - PROYECTO ARA
DB_CONFIG = {
    'driver': '{SQL Server}', # O el que tengas instalado
    'server': '192.168.4.128:8000',                    # La IP del servidor de la empresa
    'database': 'PROFIT_ADMIN_O_NOMBRE',          # El nombre de la base de datos
    'user': '04',                                 # Tu usuario de SQL
    'password': 'admin'              # Tu contraseña
}
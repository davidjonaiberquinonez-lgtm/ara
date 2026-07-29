import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
# Importamos tu función del script anterior
from migrar_datos import migrar_a_sql, DATA_FOLDER, BRAIN_FOLDER

class ManejadorCambios(FileSystemEventHandler):
    def __init__(self):
        self.ultima_ejecucion = 0

    def procesar_cambio(self, event):
        # Evitar que se procesen directorios
        if event.is_directory:
            return

        # Evitar ejecuciones duplicadas seguidas (anti-bounce)
        ahora = time.time()
        if ahora - self.ultima_ejecucion < 2:
            return
            
        nombre_archivo = os.path.basename(event.src_path)
        ruta_completa = event.src_path
        
        # Filtro inteligente: reacciona a los archivos maestros o a nuevos JSONs en reportes_ubicacion
        es_archivo_maestro = nombre_archivo in ['Libro1.xlsx', 'usuarios.json']
        es_nuevo_reporte = nombre_archivo.endswith('.json') and 'reportes_ubicacion' in ruta_completa

        if es_archivo_maestro or es_nuevo_reporte:
            print(f"🔄 Cambio detectado en: {nombre_archivo} ({event.event_type.upper()}). Iniciando migración...")
            
            # Esperamos medio segundo a que el sistema termine de escribir el archivo por completo
            time.sleep(0.5) 
            
            try:
                migrar_a_sql()
                self.ultima_ejecucion = time.time()
            except Exception as e:
                print(f"❌ Error durante la automigración: {e}")

    # Capturamos modificaciones de archivos existentes
    def on_modified(self, event):
        self.procesar_cambio(event)

    # Capturamos cuando el sistema operativo crea un archivo JSON nuevo en la carpeta
    def on_created(self, event):
        self.procesar_cambio(event)

if __name__ == "__main__":
    event_handler = ManejadorCambios()
    observer = Observer()
    
    # Vigilamos data de forma plana
    observer.schedule(event_handler, path=DATA_FOLDER, recursive=False)
    
    # 🔥 CLAVE: recursive=True para que watchdog vigile la subcarpeta 'reportes_ubicacion'
    observer.schedule(event_handler, path=BRAIN_FOLDER, recursive=True)
    
    print("👀 Vigilante de archivos activado.")
    print("🤖 Esperando cambios en Libro1.xlsx, usuarios.json o nuevos reportes de ubicación...")
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
# Importamos tu función del script anterior
from migrar_datos import migrar_a_sql, DATA_FOLDER, BRAIN_FOLDER

class ManejadorCambios(FileSystemEventHandler):
    def __init__(self):
        self.ultima_ejecucion = 0

    def on_modified(self, event):
        # Evitar ejecuciones duplicadas seguidas (anti-bounce)
        ahora = time.time()
        if ahora - self.ultima_ejecucion < 2:
            return
            
        nombre_archivo = os.path.basename(event.src_path)
        
        # Si cambia el Excel de Profit o el JSON/JS de usuarios, disparamos la migración
        if nombre_archivo in ['Libro1.xlsx', 'usuarios.json']:
            print(f"🔄 Detectado cambio en: {nombre_archivo}. Iniciando migración automática...")
            
            # Esperamos medio segundo a que el sistema termine de escribir el archivo por completo
            time.sleep(0.5) 
            
            try:
                migrar_a_sql()
                self.ultima_ejecucion = time.time()
            except Exception as e:
                print(f"❌ Error durante la automigración: {e}")

if __name__ == "__main__":
    event_handler = ManejadorCambios()
    observer = Observer()
    
    # Vigilamos tanto la carpeta data como la carpeta brain_knowledge
    observer.schedule(event_handler, path=DATA_FOLDER, recursive=False)
    observer.schedule(event_handler, path=BRAIN_FOLDER, recursive=False)
    
    print("👀 Vigilante de archivos activado. Esperando cambios en Libro1.xlsx o usuarios.json...")
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
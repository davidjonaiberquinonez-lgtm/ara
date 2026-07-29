import procesador_rutas as pr

def iniciar_ara():
    print("================================")
    print("   ARA BRAIN - SISTEMA ACTIVO   ")
    print("================================")
    
    # 1. Cargamos los datos
    datos = pr.cargar_datos()
    print("\n[ÉXITO] Datos cargados correctamente.")
    print(datos)
    
    # 2. Mostramos el resumen por zonas (Aquí estaba el error de nombre)
    print("\n[PROCESANDO] Resumen de rutas por zona:")
    resumen = pr.agrupar_por_zona()
    print(resumen)

if __name__ == '__main__':
    iniciar_ara()
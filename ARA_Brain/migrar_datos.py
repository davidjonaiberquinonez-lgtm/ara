import os
import pandas as pd
import sqlite3
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(BASE_DIR, 'data')
BRAIN_FOLDER = os.path.join(BASE_DIR, 'brain_knowledge')
DB_PATH = os.path.join(DATA_FOLDER, 'proyecto_ara.db')

def migrar_a_sql():
    os.makedirs(DATA_FOLDER, exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # --- 1. MIGRACIÓN DE STOCK (LIBRO1.XLSX) ---
    file_stock = os.path.join(DATA_FOLDER, 'Libro1.xlsx')
    if os.path.exists(file_stock):
        print(f"-> Procesando archivo crudo de Profit: {file_stock}")
        df_raw = pd.read_excel(file_stock, header=None)
        
        header_idx = None
        for idx, fila in df_raw.iterrows():
            if "CODIGO" in fila.values:
                header_idx = idx
                break
                
        if header_idx is not None:
            df_stock = df_raw.iloc[header_idx + 1:].copy()
            df_stock.columns = df_raw.iloc[header_idx].values
            df_stock.columns = [str(c).strip().replace(' ', '_').lower() for c in df_stock.columns]
            
            # SOLUCIÓN AL OVERFLOW: Forzar columnas de ID a Texto (String)
            cols_a_texto = ['codigo', 'codigo_barra', 'campo7', 'descripcion']
            for col in cols_a_texto:
                if col in df_stock.columns:
                    df_stock[col] = df_stock[col].astype(str).str.strip().replace('nan', '')

            # Limpieza de filas vacías
            if 'codigo' in df_stock.columns:
                df_stock = df_stock[df_stock['codigo'] != '']
            
            # 🔥 REGLA DE ORO: Mapeamos la columna 'despacho' como nuestro stock_maestro de ARA
            if 'despacho_bqto' in df_stock.columns:
                print("📦 Columna 'despacho_bqto' detectada. Estableciéndola como Stock Maestro.")
                df_stock['stock_maestro'] = pd.to_numeric(df_stock['despacho_bqto'], errors='coerce').fillna(0).astype(int)
            elif 'stock_act' in df_stock.columns:
                # Fallback por si acaso viene con el nombre estándar de Profit en algunas pruebas
                df_stock['stock_maestro'] = pd.to_numeric(df_stock['stock_act'], errors='coerce').fillna(0).astype(int)
            else:
                df_stock['stock_maestro'] = 0

            # 📦 CONTROL DE TRASLADOS: Depósito de Bulto Cerrado
            # Buscamos de forma dinámica si hay una columna que represente el depósito de reserva/bultos
            col_bulto = next((c for c in df_stock.columns if 'bulto' in c or 'cerrado' in c or 'deposito' in c and c != 'despacho'), None)
            if col_bulto:
                print(f"🚛 Columna de traslados detectada: '{col_bulto}'. Mapeando a stock_bulto_cerrado.")
                df_stock['stock_bulto_cerrado'] = pd.to_numeric(df_stock[col_bulto], errors='coerce').fillna(0).astype(int)
            else:
                df_stock['stock_bulto_cerrado'] = 0 # Valor por defecto seguro
            
            # Guardar en SQL (reemplaza la tabla completa con la estructura limpia)
            df_stock.to_sql('stock_maestro', conn, if_exists='replace', index=False)
            print("✅ Stock Maestro y Bultos (Libro1) migrados al modelo relacional SQL con éxito.")
        else:
            print("⚠️ No se encontró la cabecera 'CODIGO'.")
    else:
        print("⚠️ No se encontró Libro1.xlsx.")

    # --- 2. MIGRACIÓN DE FACTURAS (CSV) ---
    file_facturas = os.path.join(DATA_FOLDER, 'factura_202604221922.csv')
    if os.path.exists(file_facturas):
        df_facturas = pd.read_csv(file_facturas, dtype=str) 
        df_facturas.columns = [c.strip().replace(' ', '_').lower() for c in df_facturas.columns]
        df_facturas.to_sql('facturas', conn, if_exists='replace', index=False)
        print("✅ Facturas migradas a SQL.")

    # --- 3. TABLA DE INVENTARIO ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventario_progreso (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT,
            detalle TEXT,
            cantidad INTEGER,
            usuario TEXT,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # --- 4. MIGRACIÓN DE USUARIOS (JSON) ---
    json_path = os.path.join(BRAIN_FOLDER, 'usuarios.json') 
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            datos = json.load(f)
            usuarios = list(datos.values()) if isinstance(datos, dict) else datos

        cursor.execute('DROP TABLE IF EXISTS usuarios')
        cursor.execute('''
            CREATE TABLE usuarios (
                id TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                contrasena TEXT NOT NULL,
                rol TEXT,
                color TEXT,
                permisos TEXT,
                is_route_responsible INTEGER
            )
        ''')

        for u in usuarios:
            if isinstance(u, dict):
                user_id = u.get('id')
                nombre = u.get('nombre')
                contrasena = u.get('pass') or u.get('contrasena')
                if user_id and nombre and contrasena:
                    cursor.execute('''
                        INSERT INTO usuarios VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (user_id, nombre, contrasena, u.get('rol', 'operador'), 
                          u.get('color', '#3b82f6'), json.dumps(u.get('permisos', [])),
                          1 if u.get('isRouteResponsible', False) else 0))
        
        print(f"✅ Usuarios migrados correctamente.")

    conn.commit()
    conn.close() 
    print("🚀 Sincronización completa.")

if __name__ == '__main__':
    migrar_a_sql()
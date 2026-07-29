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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
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
            
            # REGLA DE ORO: Mapeamos la columna 'despacho' como nuestro stock_maestro de ARA
            if 'despacho_bqto' in df_stock.columns:
                print(f"[STOCK] Columna 'despacho_bqto' detectada. Estableciendola como Stock Maestro.")
                df_stock['stock_maestro'] = pd.to_numeric(df_stock['despacho_bqto'], errors='coerce').fillna(0).astype(int)
            elif 'stock_act' in df_stock.columns:
                df_stock['stock_maestro'] = pd.to_numeric(df_stock['stock_act'], errors='coerce').fillna(0).astype(int)
            else:
                df_stock['stock_maestro'] = 0

            # CONTROL DE TRASLADOS: Deposito de Bulto Cerrado
            col_bulto = next((c for c in df_stock.columns if 'bulto' in c or 'cerrado' in c or 'deposito' in c and c != 'despacho'), None)
            if col_bulto:
                print(f"[TRASLADO] Columna de traslados detectada: '{col_bulto}'. Mapeando a stock_bulto_cerrado.")
                df_stock['stock_bulto_cerrado'] = pd.to_numeric(df_stock[col_bulto], errors='coerce').fillna(0).astype(int)
            else:
                df_stock['stock_bulto_cerrado'] = 0

            # --- GARANTIZAR ESQUEMA: CREAR TABLA + INDICE UNICO ---
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock_maestro (
                    codigo TEXT PRIMARY KEY,
                    descripcion TEXT,
                    campo7 TEXT,
                    stock_maestro INTEGER DEFAULT 0,
                    stock_bulto_cerrado INTEGER DEFAULT 0,
                    codigo_barra TEXT
                )
            """)
            conn.commit()

            for intento in range(3):
                try:
                    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_maestro_codigo ON stock_maestro(codigo)")
                    conn.commit()
                    break
                except Exception as e_idx:
                    err_msg = str(e_idx)
                    if "duplicate" in err_msg.lower() and intento < 2:
                        cursor.execute("DELETE FROM stock_maestro WHERE rowid NOT IN (SELECT MIN(rowid) FROM stock_maestro GROUP BY codigo)")
                        conn.commit()
                        n_dups = cursor.rowcount
                        print(f"Eliminados {n_dups} duplicados de stock_maestro para crear indice unico.")
                    elif intento < 2:
                        print(f"Error al crear indice unico (intento {intento+1}): {e_idx}")
                    else:
                        print(f"No se pudo crear indice unico tras {intento+1} intentos. Usando INSERT OR REPLACE.")

            # --- RESET MASIVO: TODO a stock 0 (sesgo Profit Plus omite articulos con 0) ---
            try:
                cursor.execute("UPDATE stock_maestro SET stock_maestro = 0, stock_bulto_cerrado = 0")
                reset_count = cursor.rowcount
                print(f"Reset masivo: {reset_count} articulos en stock 0.")
            except Exception:
                print("No se pudo hacer reset masivo (probablemente columnas no existen). Se insertara desde cero.")
                cursor.execute("DROP TABLE IF EXISTS stock_maestro")
                cursor.execute("""
                    CREATE TABLE stock_maestro (
                        codigo TEXT PRIMARY KEY,
                        descripcion TEXT,
                        campo7 TEXT,
                        stock_maestro INTEGER DEFAULT 0,
                        stock_bulto_cerrado INTEGER DEFAULT 0,
                        codigo_barra TEXT
                    )
                """)
                conn.commit()

            # --- UPSERT: actualizar solo los que vienen en el Excel ---
            cols_insert = ['codigo', 'descripcion', 'campo7', 'stock_maestro', 'stock_bulto_cerrado', 'codigo_barra']
            cols_existentes = [c for c in cols_insert if c in df_stock.columns]
            df_final = df_stock[cols_existentes].copy()

            placeholders = ', '.join(['?'] * len(cols_existentes))
            cols_str = ', '.join(cols_existentes)
            update_cols = [c for c in cols_existentes if c not in ('codigo', 'descripcion', 'campo7', 'codigo_barra')]
            update_clause = ', '.join([f"{c}=excluded.{c}" for c in update_cols])
            data_tuples = [tuple(row) for row in df_final.to_numpy()]

            try:
                sql_upsert = f"""
                    INSERT INTO stock_maestro ({cols_str}) VALUES ({placeholders})
                    ON CONFLICT(codigo) DO UPDATE SET {update_clause}
                """
                cursor.executemany(sql_upsert, data_tuples)
            except Exception as e_upsert:
                print(f"ON CONFLICT fallo, usando INSERT OR REPLACE: {e_upsert}")
                sql_replace = f"INSERT OR REPLACE INTO stock_maestro ({cols_str}) VALUES ({placeholders})"
                cursor.executemany(sql_replace, data_tuples)

            conn.commit()
            print(f"Procesados {cursor.rowcount} articulos desde Libro1.xlsx.")
        else:
            print("[WARN] No se encontro la cabecera 'CODIGO'.")
    else:
        print("[WARN] No se encontro Libro1.xlsx.")

    # --- 2. MIGRACIÓN DE FACTURAS (CSV) ---
    file_facturas = os.path.join(DATA_FOLDER, 'factura_202604221922.csv')
    if os.path.exists(file_facturas):
        df_facturas = pd.read_csv(file_facturas, dtype=str) 
        df_facturas.columns = [c.strip().replace(' ', '_').lower() for c in df_facturas.columns]
        df_facturas.to_sql('facturas', conn, if_exists='replace', index=False)
        print("[OK] Facturas migradas a SQL.")

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
        print("[OK] Usuarios migrados correctamente.")

    # --- 5. MIGRACIÓN DE REPORTES DE UBICACIÓN (LOGS JSON INDIVIDUALES) ---
    folder_reportes = os.path.join(BRAIN_FOLDER, 'reportes_ubicacion')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reportes_ubicacion (
            id TEXT PRIMARY KEY,
            usuario TEXT,
            co_art TEXT,
            desde TEXT,
            hacia TEXT,
            fecha TEXT
        )
    ''')

    if os.path.exists(folder_reportes):
        print(f"-> Escaneando historial de relocalizaciones en: {folder_reportes}")
        contador_movs = 0
        
        for archivo in os.listdir(folder_reportes):
            if archivo.endswith('.json'):
                archivo_path = os.path.join(folder_reportes, archivo)
                try:
                    with open(archivo_path, 'r', encoding='utf-8') as f:
                        data_mov = json.load(f)
                    
                    # Extraer parámetros mapeando 'codigo' del JSON al 'co_art' relacional
                    mov_id = data_mov.get('id')
                    usuario_cambio = data_mov.get('usuario')
                    co_art = data_mov.get('codigo')
                    desde = data_mov.get('desde')
                    hacia = data_mov.get('hacia')
                    fecha_cambio = data_mov.get('fecha')
                    
                    if mov_id and co_art:
                        cursor.execute('''
                            INSERT OR IGNORE INTO reportes_ubicacion (id, usuario, co_art, desde, hacia, fecha)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (mov_id, usuario_cambio, co_art, desde, hacia, fecha_cambio))
                        contador_movs += 1
                except Exception as err_file:
                    print(f"[WARN] Error procesando archivo {archivo}: {str(err_file)}")
                    
        print(f"[OK] Historial relacional sincronizado ({contador_movs} archivos indexados).")
    else:
        print("[WARN] No se encontro el directorio fisico de 'reportes_ubicacion'.")

    conn.commit()
    conn.close() 
    print("[OK] Sincronizacion completa.")

if __name__ == '__main__':
    migrar_a_sql()
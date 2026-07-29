import pandas as pd
import os

# Ajusta estas rutas a las tuyas
excel_path = 'data/art_2026-04-27(1).xlsx'
csv_path = 'data/factura_202604221922.csv'

print("--- ANALIZANDO EXCEL ---")
try:
    df_ex = pd.read_excel(excel_path)
    print("Columnas encontradas en el Excel:")
    print(df_ex.columns.tolist()) 
    print("\nPrimeras 3 filas:")
    print(df_ex.head(3))
except Exception as e:
    print(f"Error leyendo Excel: {e}")

print("\n" + "="*30 + "\n")

print("--- ANALIZANDO CSV ---")
try:
    # Probamos detectar el separador automáticamente
    df_csv = pd.read_csv(csv_path, sep=None, engine='python')
    print("Columnas encontradas en el CSV:")
    print(df_csv.columns.tolist())
    print("\nPrimeras 3 filas:")
    print(df_csv.head(3))
except Exception as e:
    print(f"Error leyendo CSV: {e}")

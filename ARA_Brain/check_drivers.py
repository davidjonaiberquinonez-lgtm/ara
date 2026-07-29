import pyodbc
print("--- DRIVERS INSTALADOS EN ESTA PC ---")
for driver in pyodbc.drivers():
    print(f"-> {driver}")
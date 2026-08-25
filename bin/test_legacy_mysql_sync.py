#!/usr/bin/env python3
"""
bin/test_legacy_mysql_sync.py — Arnés de verificación de la conexión NATIVA
MySQL del servidor legacy (192.168.4.148:3306) para ARA_SYNC.

Valida: 1) lectura de credenciales desde .env, 2) conexión y ping al MySQL,
3) pool de conexiones (limite MYSQL_CONNECTION_LIMIT=30), 4) dry-run de la
transaccion equivalente a visor/registro.php y chequeo/registro.php.

El dry-run NO escribe nada en la BD: solo reporta el SQL que ejecutaria.
Para ejecutar la confirmacion real use el flag --confirmar.

Uso: python bin/test_legacy_mysql_sync.py [--confirmar] [--nota 72161167]

Exit: 0 = OK | 1 = error de conexion/credenciales | 2 = error de esquema
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "preparacion"
))

CONFIRMAR = "--confirmar" in sys.argv
NOTA = "72161167"
for arg in sys.argv[1:]:
    if arg.startswith("--nota="):
        NOTA = arg.split("=", 1)[1]


def _cargar_env(ruta: str) -> None:
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8", errors="replace") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip().strip('"'))


def main() -> int:
    _cargar_env(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
    ))

    print(f"=== ARNES MySQL legacy ARA_SYNC ===")
    print(f"host={os.getenv('MYSQL_HOST', '(default)')} "
          f"port={os.getenv('MYSQL_PORT', '3306')} "
          f"user={os.getenv('MYSQL_USER', 'root')} "
          f"limit={os.getenv('MYSQL_CONNECTION_LIMIT', '30')}")
    if not os.getenv("MYSQL_PASSWORD"):
        print("AVISO: MYSQL_PASSWORD vacio en .env")
        return 1

    from preparacion.infrastructure.adapters.legacy_mysql_sync import LegacyMySQLConnector

    conector = LegacyMySQLConnector()

    # 1) Ping + esquema de tablas candidatas (solo lectura)
    res = conector.verificar()
    if res.get("status") != "ok":
        print(f"\n[1/4] CONEXION: FAIL -> {res.get('aviso_legacy')}")
        return 1
    print(f"\n[1/4] CONEXION: OK ({res['server']} @ {res['database']} "
          f"en {res['latencia_ms']}ms)")
    encontradas = 0
    for grupo, info in res.get("tablas", {}).items():
        encontradas += 1
        print(f"      - {grupo}: tabla `{info['tabla']}` "
              f"(cols: {', '.join(info['columnas'][:12])})")
    if encontradas == 0:
        print("      (ninguna tabla candidata encontrada -> exit 2)")
        return 2

    # 2) Pool: prestamo/liberacion hasta el limite + 1
    try:
        limite = conector._limit
        prestadas = []
        for i in range(limite + 2):
            prestadas.append(conector.obtener())
        for c in prestadas:
            conector.liberar(c)
        print(f"[2/4] POOL: OK (limite {limite}, {len(prestadas)} conexiones "
              f"creadas y liberadas, sobrantes cerradas)")
    except Exception as e:
        for c in prestadas if "prestadas" in dir() else []:
            try:
                c.close()
            except Exception:
                pass
        print(f"[2/4] POOL: FAIL -> {e}")
        return 1
    finally:
        conector.cerrar()

    # 3) Dry-run de las transacciones equivalentes a los PHP legacy
    res_despacho = conector.confirmar_despacho(NOTA, modo_real=CONFIRMAR)
    res_chequeo = conector.confirmar_chequeo(
        NOTA, "admin1", monto=0.0, total_items=0, modo_real=CONFIRMAR
    )
    print(f"\n[3/4] DESPACHO (visor/registro.php): {res_despacho.get('status')}")
    print(f"      tabla_nota={res_despacho.get('tabla_nota')} "
          f"tabla_log={res_despacho.get('tabla_log')}")
    print(f"      SQL: {res_despacho.get('sql_nota')}")
    print(f"[3/4] CHEQUEO (chequeo/registro.php): {res_chequeo.get('status')}")
    print(f"      tabla_nota={res_chequeo.get('tabla_nota')} "
          f"tabla_gestion={res_chequeo.get('tabla_gestion')}")

    # 4) Confirmacion real (opcional)
    if CONFIRMAR:
        ok_d = res_despacho.get("status") == "ok"
        ok_c = res_chequeo.get("status") == "ok"
        print(f"\n[4/4] CONFIRMACION REAL: despacho={'OK' if ok_d else 'FAIL'}, "
              f"chequeo={'OK' if ok_c else 'FAIL'}")
        if not (ok_d and ok_c):
            print(f"      detalle: {res_despacho.get('aviso_legacy')} | "
                  f"{res_chequeo.get('aviso_legacy')}")
            return 2
    else:
        print(f"\n[4/4] Dry-run completo (sin escritura). Re-ejecute con "
              f"--confirmar para escribir en BD real de la nota {NOTA}.")

    print("\n=== ARNES OK (exit 0) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

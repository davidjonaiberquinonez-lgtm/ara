#!/usr/bin/env python3
"""Prueba de carga contra ara_server.py — valida la cola de respuesta antes
de abrir el servicio a los usuarios (punto 4 del plan de GB10).

Dispara peticiones concurrentes mezclando los tres tipos de consulta que más
pesan en producción: búsqueda de stock, lectura de facturas de cliente y
consulta de cliente. No depende de la GB10 ni de los espejos locales —
corre contra CUALQUIER instancia viva de ara_server.py (dev o producción),
por eso se puede probar hoy mismo.

Uso:
    python gb10/stress_test.py                       # 15 peticiones, concurrencia 15, contra localhost:5000
    python gb10/stress_test.py -n 50 -c 20
    python gb10/stress_test.py --url http://192.168.4.X:5000
"""
import argparse
import concurrent.futures
import statistics
import time

import requests

DEFAULT_BASE_URL = "http://127.0.0.1:5000"

# Escenarios reales (no inventados): mismos endpoints/tools que usan los
# módulos de almacén, auditoría y despacho hoy en producción.
ESCENARIOS = [
    {"nombre": "stock_directo", "metodo": "GET", "ruta": "/api/stock/CR000278"},
    {"nombre": "stock_directo_2", "metodo": "GET", "ruta": "/api/stock/MD04668"},
    {"nombre": "cliente_consulta", "metodo": "GET", "ruta": "/api/cliente/consulta",
     "params": {"busqueda": "FARMACIA"}},
    {"nombre": "tool_buscar_inventario", "metodo": "POST", "ruta": "/api/tools/ejecutar",
     "json": {"tool": "buscar_inventario", "arguments": {"busqueda": "PANTENOL"}}},
    {"nombre": "tool_consultar_facturas_cliente", "metodo": "POST", "ruta": "/api/tools/ejecutar",
     "json": {"tool": "consultar_facturas_cliente", "arguments": {"co_cli": "FAR01680"}}},
]


def disparar(base_url: str, escenario: dict, timeout: float) -> dict:
    inicio = time.perf_counter()
    try:
        if escenario["metodo"] == "GET":
            r = requests.get(base_url + escenario["ruta"], params=escenario.get("params"), timeout=timeout)
        else:
            r = requests.post(base_url + escenario["ruta"], json=escenario.get("json"), timeout=timeout)
        ms = (time.perf_counter() - inicio) * 1000
        return {"nombre": escenario["nombre"], "ok": r.status_code < 500, "status": r.status_code, "ms": ms}
    except Exception as e:
        ms = (time.perf_counter() - inicio) * 1000
        return {"nombre": escenario["nombre"], "ok": False, "status": None, "ms": ms, "error": str(e)}


def percentil(valores: list, p: float) -> float:
    if not valores:
        return 0.0
    valores_ord = sorted(valores)
    k = (len(valores_ord) - 1) * (p / 100)
    f, c = int(k), min(int(k) + 1, len(valores_ord) - 1)
    if f == c:
        return valores_ord[f]
    return valores_ord[f] + (valores_ord[c] - valores_ord[f]) * (k - f)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_BASE_URL, help=f"Base URL de ara_server.py (default {DEFAULT_BASE_URL})")
    parser.add_argument("-n", "--peticiones", type=int, default=15, help="Total de peticiones a disparar (default 15)")
    parser.add_argument("-c", "--concurrencia", type=int, default=15, help="Peticiones simultáneas (default 15)")
    parser.add_argument("-t", "--timeout", type=float, default=30.0, help="Timeout por petición en segundos (default 30)")
    args = parser.parse_args()

    tareas = [ESCENARIOS[i % len(ESCENARIOS)] for i in range(args.peticiones)]

    print(f"Disparando {args.peticiones} peticiones (concurrencia {args.concurrencia}) contra {args.url} ...")
    inicio_total = time.perf_counter()
    resultados = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrencia) as ex:
        futuros = [ex.submit(disparar, args.url, t, args.timeout) for t in tareas]
        for f in concurrent.futures.as_completed(futuros):
            resultados.append(f.result())
    duracion_total = time.perf_counter() - inicio_total

    por_escenario = {}
    for r in resultados:
        por_escenario.setdefault(r["nombre"], []).append(r)

    print(f"\n{'ESCENARIO':<32} {'OK':>5} {'FALLOS':>7} {'p50 ms':>8} {'p95 ms':>8} {'max ms':>8}")
    total_ok = 0
    for nombre, filas in por_escenario.items():
        tiempos = [f["ms"] for f in filas]
        oks = sum(1 for f in filas if f["ok"])
        total_ok += oks
        fallos = len(filas) - oks
        print(f"{nombre:<32} {oks:>5} {fallos:>7} {percentil(tiempos, 50):>8.0f} {percentil(tiempos, 95):>8.0f} {max(tiempos):>8.0f}")

    errores = [r for r in resultados if not r["ok"]]
    if errores:
        print("\nDetalle de fallos:")
        for e in errores[:10]:
            print(f"  - {e['nombre']}: status={e.get('status')} error={e.get('error', '')}")

    print(f"\nTotal: {total_ok}/{len(resultados)} OK en {duracion_total:.2f}s "
          f"({len(resultados) / duracion_total:.1f} req/s efectivos)")


if __name__ == "__main__":
    main()

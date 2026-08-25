# -*- coding: utf-8 -*-
"""
Benchmark piloto: 100 pruebas de reconocimiento de empaques (4 escenarios).

Escenarios reales de almacén (25 casos cada uno):
    1. Iluminación perfecta y empaque frontal completo.
    2. Reflejo de luz / sombra parcial.
    3. Etiqueta/texto desgastado o parcialmente tapado (valida peso visual).
    4. Mismo laboratorio, diseño idéntico, diferente dosificación (valida
       precisión híbrida Visual + OCR).

Métricas: % Precisión Top-1, % Precisión Top-3 y Tiempo Promedio de Respuesta
(ms). Además valida el requisito de latencia (< 150 ms de coincidencia) y el
adaptador de ingesta PHP con su fallback de caché en disco.

Uso:  python visor_articulos/tests/piloto_100_reconocimiento.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from visor_articulos.adapters.inmemory_vector_adapter import InMemoryVectorAdapter, huella_desde_bytes
from visor_articulos.adapters.php_catalog_adapter import (
    PhpCatalogAdapter,
    cargar_o_ingerir,
)
from visor_articulos.application.visor_service import MotorBusquedaVisual
from visor_articulos.domain.articulo_matcher import UMBRAL_AMBIGUEDAD_VISUAL
from visor_articulos.tests.generador_empaques import (
    buscar_producto,
    construir_catalogo,
    generar_empaque,
    producto_a_bytes,
)
from visor_articulos.tests.ocr_sintetico import OcrSintetico

ESCENARIOS = [
    ("perfecto", "1. Iluminación perfecta (frontal completo)"),
    ("reflejo", "2. Reflejo de luz / sombra parcial"),
    ("desgastado", "3. Etiqueta desgastada / parcialmente tapada"),
    ("identico", "4. Diseño idéntico, distinta dosificación"),
]

PESO_VISUAL = 0.65


def seleccionar_casos(catalogo, escenario, n=25):
    """Determinista: mezcla de normales y gemelos según escenario."""
    normales = [p for p in catalogo if p.co_art.startswith("N")]
    gemelos = [p for p in catalogo if p.co_art.startswith("G")]
    casos = []
    if escenario == "desgastado":
        fuente = normales
    elif escenario == "identico":
        fuente = gemelos
    else:
        fuente = normales[:15] + gemelos[:10]
    for i in range(n):
        casos.append(fuente[i % len(fuente)])
    return casos


def ejecutar_bateria(catalogo, indice):
    motor = MotorBusquedaVisual(indice, peso_visual=PESO_VISUAL)
    filas = []
    globales = dict(top1=0, top3=0, casos=0, sum_match_ms=0.0, sum_total_ms=0.0, max_match_ms=0.0)
    for escenario, titulo in ESCENARIOS:
        casos = seleccionar_casos(catalogo, escenario)
        top1 = top3 = 0
        sum_match = 0.0
        sum_total = 0.0
        max_match = 0.0
        ok = 0
        for producto in casos:
            imagen = producto_a_bytes(producto, perturbacion=escenario)
            ocr = OcrSintetico(producto, escenario)
            motor.extractor_texto = ocr
            t0 = time.perf_counter()
            resultado = motor.buscar(imagen)
            total_ms = (time.perf_counter() - t0) * 1000.0
            match_ms = resultado.latencia_total_ms
            if resultado.acierta_con(producto.co_art, 1):
                top1 += 1
            if resultado.acierta_con(producto.co_art, 3):
                top3 += 1
            sum_match += match_ms
            sum_total += total_ms
            max_match = max(max_match, match_ms)
            ok += 1

        n = len(casos)
        filas.append(
            (titulo, n, top1 / n * 100.0, top3 / n * 100.0, sum_match / n, max_match)
        )
        globales["top1"] += top1
        globales["top3"] += top3
        globales["casos"] += ok
        globales["sum_match_ms"] += sum_match
        globales["sum_total_ms"] += sum_total
        globales["max_match_ms"] = max(globales["max_match_ms"], max_match)

    # Latencia pura de la búsqueda en RAM (motor visual, sin OCR)
    tiempos_ram = []
    for producto in catalogo[:100]:
        imagen = producto_a_bytes(producto, "perfecto")
        t0 = time.perf_counter()
        motor.buscar_bruto(imagen)
        tiempos_ram.append((time.perf_counter() - t0) * 1000.0)
    prom_ram = sum(tiempos_ram) / len(tiempos_ram)

    return filas, globales, prom_ram


def imprimir_tabla(filas, globales, prom_ram):
    linea = "+" + "-" * 62 + "+" + "-" * 7 + "+" + "-" * 8 + "+" + "-" * 8 + "+" + "-" * 14 + "+" + "-" * 12 + "+"
    print("\n" + "=" * 130)
    print("BENCHMARK PILOTO — RECONOCIMIENTO DE EMPAQUES (100 PRUEBAS)")
    print("=" * 130)
    print(linea)
    print("| ESCENARIO" + " " * 50 + "| CASOS | TOP-1 % | TOP-3 % |  PROMEDIO MS |   MAX MS  |")
    print(linea)
    for titulo, n, p1, p3, prom, mx in filas:
        print(f"| {titulo:<62}|{n:>7}|{p1:>8.1f}|{p3:>8.1f}|{prom:>14.2f}|{mx:>12.2f}|")
    print(linea)
    n = globales["casos"]
    print(
        f"| {'TOTAL GLOBAL':<62}|{n:>7}|"
        f"{globales['top1']/n*100.0:>8.1f}|{globales['top3']/n*100.0:>8.1f}|"
        f"{globales['sum_match_ms']/n:>14.2f}|{globales['max_match_ms']:>12.2f}|"
    )
    print(linea)
    print(f"\nBúsqueda visual pura en RAM (44 productos indexados): promedio {prom_ram:.3f} ms / consulta")


def diagnosticar_híbrido(catalogo, indice):
    """Escenario 4: mide la ambigüedad visual real y la decisión del OCR."""
    gemelos = [p for p in catalogo if p.co_art.startswith("G")][:25]
    motor_visual = MotorBusquedaVisual(indice, peso_visual=PESO_VISUAL)
    motor_hibrido = MotorBusquedaVisual(indice, peso_visual=PESO_VISUAL)

    aciertos_puro = aciertos_hibrido = desempates = 0
    margenes = []
    for producto in gemelos:
        imagen = producto_a_bytes(producto, "identico")
        puro = motor_visual.buscar_bruto(imagen)
        margen = puro[0].similitud - puro[1].similitud
        margenes.append(margen)
        aciertos_puro += int(puro[0].producto.co_art == producto.co_art)
        motor_hibrido.extractor_texto = OcrSintetico(producto, "identico")
        hib = motor_hibrido.buscar(imagen)
        aciertos_hibrido += int(hib.acierta_con(producto.co_art, 1))
        desempates += int(hib.uso_desempate_ocr)

    n = len(gemelos)
    margen_medio = sum(margenes) / n
    print(f"\nDIAGNÓSTICO HÍBRIDO (escenario 4, {n} pares de diseño idéntico):")
    print(f"  Top-1 SOLO visual (sin OCR):  {aciertos_puro/n*100.0:5.1f} %")
    print(f"  Margen visual top1-top2 medio: {margen_medio:.4f}  (< {UMBRAL_AMBIGUEDAD_VISUAL} = ambigüedad real)")
    print(f"  Top-1 Visual + OCR (híbrido): {aciertos_hibrido/n*100.0:5.1f} %  <- desempate por dosificación")
    print(f"  Casos donde se disparó el desempate OCR: {desempates}/{n}")
    ambiguedad_real = margen_medio < UMBRAL_AMBIGUEDAD_VISUAL
    return ambiguedad_real and desempates == n and aciertos_hibrido == n


def probar_adaptador_php(workdir):
    """Valida la ingesta PHP (1.1) con un servidor mock + fallback de caché."""
    print("\n" + "=" * 130)
    print("ADAPTADOR DE INGESTA PHP — servidor mock + fallback caché en disco")
    print("=" * 130)
    resultados = []

    catalogo = construir_catalogo()[:6]
    imagenes = {p.co_art: producto_a_bytes(p, "perfecto") for p in catalogo}

    class AdapterCristMock(PhpCatalogAdapter):
        """Simula el CDN de Crist Medicals apuntando al mock local."""

        def url_imagen_default(self, co_art):
            return f"http://127.0.0.1:{self._puerto}/crist/{co_art}.png"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/catalogo.php"):
                # Producto[0]: imagen_url ROTA (404) -> debe reintentar vía CDN.
                # Producto[1]: sin imagen_url -> debe usar la URL canónica del CDN.
                body = json.dumps(
                    {
                        "ok": True,
                        "productos": [
                            {
                                "co_art": p.co_art,
                                "descripcion": p.descripcion,
                                "laboratorio": p.laboratorio,
                                "dosis": p.dosis,
                                "imagen_url": (
                                    f"http://127.0.0.1:{self.server.server_port}/fotos/ROTA_{p.co_art}.png"
                                    if i == 0 else
                                    (f"http://127.0.0.1:{self.server.server_port}/fotos/{p.co_art}.png"
                                     if i > 1 else "")
                                ),
                            }
                            for i, p in enumerate(catalogo)
                        ],
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.startswith("/fotos/"):
                co = os.path.basename(self.path).replace(".png", "").replace("ROTA_", "")
                if "ROTA_" in os.path.basename(self.path):
                    self.send_response(404)
                    self.end_headers()
                    return
                contenido = imagenes.get(co, b"")
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(contenido)))
                self.end_headers()
                self.wfile.write(contenido)
            elif self.path.startswith("/crist/"):
                co = os.path.basename(self.path).replace(".png", "")
                contenido = imagenes.get(co, b"")
                self.send_response(200 if contenido else 404)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(contenido)))
                self.end_headers()
                self.wfile.write(contenido)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    cache_dir = os.path.join(workdir, "cache_vectorial")
    import shutil

    if os.path.isdir(cache_dir):
        shutil.rmtree(cache_dir)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    hilo = threading.Thread(target=server.serve_forever, daemon=True)
    hilo.start()
    puerto = server.server_port
    AdapterCristMock._puerto = puerto

    try:
        # 0) URL canónica del CDN real de Crist Medicals
        adapter_real = PhpCatalogAdapter()
        url_canonica = adapter_real.url_imagen_default("AMP00045")
        resultados.append(
            ("CDN: URL canónica construida correctamente",
             url_canonica == "https://imagenes.cristmedicals.com/imagenes-v3/imagenes/AMP00045.jpg",
             url_canonica)
        )

        # 1) Ingesta con PHP disponible (imagen ROTA + sin imagen_url -> CDN)
        indice = InMemoryVectorAdapter(cache_dir=cache_dir)
        adapter = AdapterCristMock(url=f"http://127.0.0.1:{puerto}/catalogo.php")
        n = adapter.ingestar_en_indice(indice)
        resultados.append(("ingesta php: productos indexados (todos vía CDN)", n == 6, f"indexados={n}"))
        cache = indice.guardar_cache()
        resultados.append(("ingesta php: caché guardada en disco", os.path.isfile(cache), cache))

        # 2) Búsqueda real con el catálogo ingerido vía PHP
        motor = MotorBusquedaVisual(indice, peso_visual=PESO_VISUAL)
        objetivo = catalogo[0]
        res = motor.buscar(producto_a_bytes(objetivo, "perfecto"))
        resultados.append(("búsqueda tras ingesta PHP: top-1 correcto", res.codigo_obtenido == objetivo.co_art, res.codigo_obtenido))

        # 3) Fallback: PHP caído -> caché en disco
        server.shutdown()
        indice2 = InMemoryVectorAdapter(cache_dir=cache_dir)
        estado = cargar_o_ingerir(indice2, adapter=AdapterCristMock(url=f"http://127.0.0.1:{puerto}/catalogo.php"))
        resultados.append(
            ("fallback: origen cache_disco", estado["origen"] == "cache_disco", estado["origen"])
        )
        resultados.append(
            ("fallback: productos restaurados", estado["productos_indexados"] == 6, str(estado["productos_indexados"]))
        )
        res2 = motor.__class__(indice2, peso_visual=PESO_VISUAL).buscar(producto_a_bytes(objetivo, "perfecto"))
        resultados.append(
            ("fallback: búsqueda funcional tras restaurar caché", res2.codigo_obtenido == objetivo.co_art, res2.codigo_obtenido)
        )

        # 4) PHP caído SIN caché -> origen vacío (no detiene nada)
        cache_vacio = os.path.join(workdir, "cache_vacio")
        if os.path.isdir(cache_vacio):
            shutil.rmtree(cache_vacio)
        indice3 = InMemoryVectorAdapter(cache_dir=cache_vacio)
        estado3 = cargar_o_ingerir(indice3, adapter=PhpCatalogAdapter(url=f"http://127.0.0.1:{puerto}/catalogo.php"))
        resultados.append(
            ("sin caché ni PHP: origen vacío sin excepción", estado3["origen"] == "vacio", estado3["origen"])
        )
    finally:
        hilo.join(timeout=2)

    return resultados


def main():
    raiz = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    catalogo = construir_catalogo()
    print(f"Catálogo sintético: {len(catalogo)} productos ({sum(1 for p in catalogo if p.co_art.startswith('N'))} normales, {sum(1 for p in catalogo if p.co_art.startswith('G'))} gemelos)")

    # Índice en RAM con huellas del catálogo (frontal perfecto como referencia)
    indice = InMemoryVectorAdapter()
    for p in catalogo:
        indice.registrar_producto(p, producto_a_bytes(p, "perfecto"))

    t0 = time.perf_counter()
    filas, globales, prom_ram = ejecutar_bateria(catalogo, indice)
    imprimir_tabla(filas, globales, prom_ram)
    hibrido_ok = diagnosticar_híbrido(catalogo, indice)
    duracion_total_s = time.perf_counter() - t0

    # ── Validaciones ─────────────────────────────────────────────────────────
    print("\nVALIDACIONES:")
    n = globales["casos"]
    prom_match = globales["sum_match_ms"] / n
    checks = [
        ("Latencia promedio de coincidencia < 150 ms", prom_match < 150.0, f"{prom_match:.2f} ms"),
        ("Latencia máxima de coincidencia < 150 ms", globales["max_match_ms"] < 150.0, f"{globales['max_match_ms']:.2f} ms"),
        ("Búsqueda visual en RAM < 10 ms (objetivo)", prom_ram < 10.0, f"{prom_ram:.3f} ms"),
        ("Top-1 global >= 85 %", globales["top1"] / n * 100.0 >= 85.0, f"{globales['top1']/n*100.0:.1f} %"),
        ("Top-3 global >= 95 %", globales["top3"] / n * 100.0 >= 95.0, f"{globales['top3']/n*100.0:.1f} %"),
        ("Top-3 escenario 4 (híbrido) >= 95 %", filas[3][3] >= 95.0, f"{filas[3][3]:.1f} %"),
        ("Híbrido desempata diseño idéntico (OCR aporta)", hibrido_ok, "visual < híbrido y desempates = casos"),
    ]
    ok_total = True
    for nombre, cond, detalle in checks:
        marca = "OK " if cond else "FAIL"
        if not cond:
            ok_total = False
        print(f"  [{marca}] {nombre} -> {detalle}")

    # ── Adaptador PHP (1.1) ─────────────────────────────────────────────────
    php_checks = probar_adaptador_php(os.path.join(raiz, "visor_articulos", "tests", "_tmp_php"))
    for nombre, cond, detalle in php_checks:
        marca = "OK " if cond else "FAIL"
        if not cond:
            ok_total = False
        print(f"  [{marca}] {nombre} -> {detalle}")

    print(f"\nDuración total de la batería de 100 pruebas: {duracion_total_s:.2f} s")
    print("RESULTADO: " + ("TODAS LAS VALIDACIONES PASARON" if ok_total else "HUBO VALIDACIONES FALLIDAS"))
    sys.exit(0 if ok_total else 1)


if __name__ == "__main__":
    main()

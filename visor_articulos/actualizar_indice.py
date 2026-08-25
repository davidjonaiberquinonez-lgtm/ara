#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Actualiza el índice visual CLIP con los artículos que TODAVÍA no tienen
imagen indexada — no reprocesa los que ya están (idempotente, retomable).

Motivo: `cargar_o_ingerir()` (arranque normal de ara_server.py) solo intenta
la ingesta completa vía PHP; si esa consulta falla, restaura la última caché
en disco TAL CUAL (sin ampliarla) y nunca llega al fallback de BD local
mientras la caché ya tenga algo — por eso el índice quedó fijo en 1146/12022
artículos. Este script sí recorre `stock_maestro` completo, se salta lo que
ya está en caché, y solo intenta bajar/indexar lo que falta.

Uso:
    python visor_articulos/actualizar_indice.py                # todo lo faltante
    python visor_articulos/actualizar_indice.py --limite 500    # solo 500 este run (retomable)
    python visor_articulos/actualizar_indice.py --lote 300      # tamaño de lote de descarga/guardado

CERO reentrenamiento de modelo — usa el mismo CLIP ya cargado
(openai/clip-vit-base-patch32) solo para calcular embeddings de las
imágenes nuevas que sí se logran descargar del CDN.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from visor_articulos.adapters.clip_vector_adapter import ClipVectorAdapter
from visor_articulos.adapters.php_catalog_adapter import (
    PREFIJOS_CDN_PRIORITARIOS,
    PhpCatalogAdapter,
)
from visor_articulos.domain.articulo_matcher import Producto

DB_PATH = os.environ.get(
    "ARA_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "ara", "ARA_Brain", "data", "proyecto_ara.db"),
)
CACHE_DIR = os.environ.get(
    "ARA_VISOR_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cache_vectorial_clip"),
)


def _codigos_faltantes(ya_indexados: set) -> list:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        filas = conn.execute(
            """
            SELECT codigo, descripcion, campo7, codigo_barra
            FROM stock_maestro
            WHERE codigo IS NOT NULL AND trim(codigo) != ''
            ORDER BY CASE WHEN substr(codigo, 1, 3) IN ({marcas}) THEN 0 ELSE 1 END,
                     codigo
            """.format(marcas=", ".join("?" * len(PREFIJOS_CDN_PRIORITARIOS))),
            PREFIJOS_CDN_PRIORITARIOS,
        ).fetchall()
    finally:
        conn.close()
    return [f for f in filas if str(f["codigo"]).strip().upper() not in ya_indexados]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limite", type=int, default=None, help="Máximo de códigos faltantes a intentar en este run (default: todos)")
    parser.add_argument("--lote", type=int, default=300, help="Tamaño de lote de descarga/guardado de caché (default 300)")
    args = parser.parse_args()

    indice = ClipVectorAdapter(cache_dir=CACHE_DIR)
    ya_habia = indice.cargar_cache()
    ya_indexados = {c.upper() for c in indice._orden}
    print(f"[ACTUALIZAR_INDICE] Caché actual: {ya_habia} productos ya indexados.")

    faltantes = _codigos_faltantes(ya_indexados)
    total_faltantes = len(faltantes)
    print(f"[ACTUALIZAR_INDICE] Faltan {total_faltantes} de {ya_habia + total_faltantes} artículos en stock_maestro.")

    if args.limite:
        faltantes = faltantes[: args.limite]
        print(f"[ACTUALIZAR_INDICE] Limitado a {len(faltantes)} este run (--limite).")

    if not faltantes:
        print("[ACTUALIZAR_INDICE] Nada que hacer — índice ya completo.")
        return

    adapter = PhpCatalogAdapter()
    nuevos = 0
    sin_imagen_cdn = 0
    inicio = time.perf_counter()

    for i in range(0, len(faltantes), args.lote):
        lote_filas = faltantes[i : i + args.lote]
        lote_productos = [
            Producto(
                co_art=str(f["codigo"]).strip(),
                descripcion=str(f["descripcion"] or ""),
                codigo_barra=str(f["codigo_barra"] or ""),
                ubicacion=str(f["campo7"] or ""),
                imagen_url=adapter.url_imagen_default(str(f["codigo"]).strip()),
            )
            for f in lote_filas
        ]
        imagenes = adapter.descargar_imagenes(lote_productos)
        for p in lote_productos:
            contenido = imagenes.get(p.co_art)
            if contenido:
                indice.registrar_producto(p, contenido)
                nuevos += 1
            else:
                sin_imagen_cdn += 1

        indice.guardar_cache()
        procesados = min(i + args.lote, len(faltantes))
        transcurrido = time.perf_counter() - inicio
        print(
            f"[ACTUALIZAR_INDICE] {procesados}/{len(faltantes)} procesados "
            f"(+{nuevos} nuevos, {sin_imagen_cdn} sin imagen en CDN) — {transcurrido:.0f}s",
            flush=True,
        )

    print(
        f"\n[ACTUALIZAR_INDICE] Listo. Índice: {ya_habia} -> {ya_habia + nuevos} productos "
        f"({nuevos} nuevos esta corrida, {sin_imagen_cdn} códigos siguen sin imagen en el CDN)."
    )


if __name__ == "__main__":
    main()

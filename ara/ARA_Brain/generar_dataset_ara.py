# -*- coding: utf-8 -*-
"""
generar_dataset_ara.py
Genera dataset_mini_ara.jsonl en formato Alpaca para fine-tuning local.
Conecta a proyecto_ara.db y extrae pares [instruction, input, output]
desde stock_maestro, notas_entrega, movimientos_preparador, log_puntos.
"""
import os
import sqlite3
import json
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'proyecto_ara.db')
OUTPUT_PATH = os.path.join(BASE_DIR, 'dataset_mini_ara.jsonl')
MAX_EJEMPLOS = 500


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def generar_ejemplos_productos(conn) -> list[dict]:
    """Genera pares instruction/input/output basados en stock_maestro."""
    ejemplos = []
    rows = conn.execute(
        "SELECT codigo, descripcion, stock_maestro, stock_bulto_cerrado, campo7, deposito_bqto "
        "FROM stock_maestro WHERE stock_maestro > 0 OR stock_bulto_cerrado > 0 "
        "LIMIT ?", (MAX_EJEMPLOS // 2,)
    ).fetchall()

    for r in rows:
        codigo = r["codigo"]
        desc = r["descripcion"] or "Sin descripción"
        stock = float(r["stock_maestro"] or 0)
        bulto = float(r["stock_bulto_cerrado"] or 0)
        ubi = r["campo7"] or "Sin asignar"
        dep = r["deposito_bqto"] or "N/A"

        ejemplos.append({
            "instruction": f"¿Cuál es el stock y ubicación del artículo {codigo}?",
            "input": codigo,
            "output": (
                f"El artículo {codigo} ({desc}) tiene {int(stock)} unidades en stock de piso, "
                f"{int(bulto)} bultos cerrados, ubicado en {ubi} (depósito: {dep})."
            )
        })
        ejemplos.append({
            "instruction": f"Dame la ubicación física del producto {codigo}",
            "input": codigo,
            "output:": f"El artículo {codigo} ({desc}) está ubicado en {ubi}."
        })
    return ejemplos


def generar_ejemplos_notas(conn) -> list[dict]:
    """Genera pares basados en notas_entrega."""
    ejemplos = []
    rows = conn.execute(
        "SELECT ne.id, ne.numero_nota, ne.cliente, ne.estado, ne.preparador_id, "
        "       ne.usuario_chequeador, ne.usuario_embalador, ne.numero_cajas, "
        "       ne.fecha_creacion, ne.items_count "
        "FROM notas_entrega ne ORDER BY ne.fecha_creacion DESC LIMIT ?",
        (MAX_EJEMPLOS // 3,)
    ).fetchall()

    for r in rows:
        nota = r["numero_nota"]
        cliente = r["cliente"] or "Sin cliente"
        estado = r["estado"]
        prep = r["preparador_id"] or "N/A"
        chq = r["usuario_chequeador"] or "N/A"
        emb = r["usuario_embalador"] or "N/A"
        cajas = r["numero_cajas"] or "N/A"
        items = r["items_count"] or 0
        fecha = r["fecha_creacion"] or ""

        ejemplos.append({
            "instruction": f"¿Cuál es el estado de la nota {nota}?",
            "input": nota,
            "output": (
                f"La nota {nota} del cliente {cliente} está en estado '{estado}'. "
                f"Tiene {items} ítems y {cajas} cajas. "
                f"Preparador: {prep}, Chequeador: {chq}, Embalador: {emb}. "
                f"Creada el {fecha}."
            )
        })
    return ejemplos


def generar_ejemplos_movimientos(conn) -> list[dict]:
    """Genera pares basados en movimientos_preparador."""
    ejemplos = []
    rows = conn.execute(
        "SELECT mp.nota_id, mp.co_art, mp.descripcion, mp.cantidad, mp.usuario, "
        "       mp.accion, mp.origen, mp.destino, mp.timestamp, ne.numero_nota "
        "FROM movimientos_preparador mp "
        "LEFT JOIN notas_entrega ne ON ne.id = mp.nota_id "
        "ORDER BY mp.timestamp DESC LIMIT ?",
        (MAX_EJEMPLOS // 3,)
    ).fetchall()

    for r in rows:
        co_art = r["co_art"]
        desc = r["descripcion"] or "producto"
        usuario = r["usuario"] or "N/A"
        accion = r["accion"] or "movimiento"
        cant = r["cantidad"] or 0
        nota = r["numero_nota"] or "N/A"

        ejemplos.append({
            "instruction": f"¿Quién movió el artículo {co_art} y qué se hizo?",
            "input": co_art,
            "output": (
                f"El usuario {usuario} realizó '{accion}' de {cant} unidades de {desc} "
                f"({co_art}) en la nota {nota}."
            )
        })
    return ejemplos


def generar_ejemplos_personal(conn) -> list[dict]:
    """Genera pares basados en log_puntos y métricas de personal."""
    ejemplos = []
    rows = conn.execute(
        "SELECT usuario, modulo, SUM(puntos_ganados) as total_puntos, COUNT(*) as total_acciones "
        "FROM log_puntos GROUP BY usuario, modulo ORDER BY total_puntos DESC LIMIT 30"
    ).fetchall()

    for r in rows:
        usuario = r["usuario"]
        modulo = r["modulo"]
        pts = float(r["total_puntos"] or 0)
        acciones = r["total_acciones"] or 0

        ejemplos.append({
            "instruction": f"¿Cuántos puntos tiene {usuario} en {modulo}?",
            "input": usuario,
            "output": (
                f"El operador {usuario} tiene {int(pts)} puntos en {modulo} "
                f"con un total de {acciones} acciones registradas."
            )
        })
    return ejemplos


def generar_ejemplos_dominio_general() -> list[dict]:
    """Pares para reforzar el bloqueo de temas no-logísticos."""
    return [
        {
            "instruction": "¿Quién ganó el mundial?",
            "input": "",
            "output": "Acceso denegado. Mini ARA opera exclusivamente para la gestión logística e industrial del almacén."
        },
        {
            "instruction": "Háblame de la historia de Roma",
            "input": "",
            "output": "Acceso denegado. Mini ARA opera exclusivamente para la gestión logística e industrial del almacén."
        },
        {
            "instruction": "Resuelve esta ecuación: 2x + 5 = 15",
            "input": "",
            "output": "Acceso denegado. Mini ARA opera exclusivamente para la gestión logística e industrial del almacén."
        },
        {
            "instruction": "¿Cómo se hace una pizza?",
            "input": "",
            "output": "Acceso denegado. Mini ARA opera exclusivamente para la gestión logística e industrial del almacén."
        },
        {
            "instruction": "Dime el clima de hoy",
            "input": "",
            "output": "Acceso denegado. Mini ARA opera exclusivamente para la gestión logística e industrial del almacén."
        }
    ]


def main():
    conn = get_db()
    ejemplos = []

    try:
        ejemplos += generar_ejemplos_productos(conn)
        print(f"Productos: {len(generar_ejemplos_productos(conn))} ejemplos")
        # Re-fetch since the function already returned
        ejemplos = generar_ejemplos_productos(conn)
        ejemplos += generar_ejemplos_notas(conn)
        ejemplos += generar_ejemplos_movimientos(conn)
        ejemplos += generar_ejemplos_personal(conn)
        ejemplos += generar_ejemplos_dominio_general()
    finally:
        conn.close()

    # Escribir JSONL
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        for ex in ejemplos:
            f.write(json.dumps(ex, ensure_ascii=False) + '\n')

    print(f"Dataset generado: {OUTPUT_PATH}")
    print(f"Total ejemplos: {len(ejemplos)}")


if __name__ == '__main__':
    main()

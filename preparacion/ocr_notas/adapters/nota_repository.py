import os
import sqlite3
import time
from typing import Optional

from ..domain import NotaEntregaOCR
from ..ports import NotaRepository

DIRECTORIO_ACTUAL = os.path.dirname(os.path.abspath(__file__))
RAIZ_PROYECTO = os.path.dirname(DIRECTORIO_ACTUAL)
DB_PATH = os.path.join(RAIZ_PROYECTO, "..", "..", "ara", "ARA_Brain", "data", "proyecto_ara.db")


class SqliteNotaRepository(NotaRepository):
    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or DB_PATH

    def _get_conn(self):
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def crear_nota(self, ocr: NotaEntregaOCR) -> dict:
        conn = self._get_conn()
        try:
            now = time.strftime("%Y-%m-%d %H:%M:%S")

            existente = conn.execute(
                "SELECT id, estado FROM notas_entrega WHERE numero_nota = ?",
                (ocr.numero_nota,),
            ).fetchone()

            if existente:
                nota_id = existente["id"]
                if existente["estado"] == "pendiente":
                    conn.execute(
                        "UPDATE notas_entrega SET estado = 'preparando', "
                        "preparador_id = 'OCR', fecha_creacion = ? WHERE id = ?",
                        (now, nota_id),
                    )
                    conn.commit()
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO notas_entrega
                        (numero_nota, cliente, estado, preparador_id,
                         fecha_creacion, items_count, es_prueba, auto_chequeado)
                    VALUES (?, ?, 'preparando', 'OCR', ?, ?, 0, 0)
                    """,
                    (
                        ocr.numero_nota,
                        ocr.nombre_cliente or ocr.codigo_cliente or "OCR CLIENTE",
                        now,
                        len(ocr.items),
                    ),
                )
                nota_id = cursor.lastrowid
                conn.commit()

            items_insertados = []
            for it in ocr.items:
                cursor = conn.execute(
                    """
                    INSERT INTO detalle_nota
                        (nota_id, co_art, descripcion, cantidad_solicitada,
                         cantidad_preparada, unidad_medida, estado)
                    VALUES (?, ?, ?, ?, 0, ?, 'pendiente')
                    """,
                    (
                        nota_id,
                        it.codigo_producto or "SIN-CODIGO",
                        it.descripcion,
                        it.cantidad_solicitada,
                        it.unidad,
                    ),
                )
                items_insertados.append(
                    {
                        "id": cursor.lastrowid,
                        "co_art": it.codigo_producto or "SIN-CODIGO",
                        "descripcion": it.descripcion,
                        "cantidad_solicitada": it.cantidad_solicitada,
                        "unidad": it.unidad,
                    }
                )
            conn.commit()

            conn.execute(
                """
                INSERT INTO movimientos_preparador
                    (nota_id, co_art, descripcion, cantidad, unidad_medida,
                     usuario, accion, origen, destino, timestamp)
                VALUES ('OCR-import', 'VARIOS', ?, ?, 'UND', 'OCR',
                         'ocr_import', 'escaneo', 'preparando', ?)
                """,
                (
                    f"Nota {ocr.numero_nota} ({len(ocr.items)} items)",
                    len(ocr.items),
                    now,
                ),
            )
            conn.commit()

            codigos = [it.codigo_producto for it in ocr.items if it.codigo_producto]
            placeholders = ",".join("?" for _ in codigos)
            if codigos:
                conn.execute(
                    f"""
                    UPDATE reportes_ubicacion
                    SET procesado_profit = 0
                    WHERE co_art IN ({placeholders}) AND procesado_profit = 1
                    """,
                    codigos,
                )
                conn.commit()

            nota = dict(
                conn.execute(
                    "SELECT * FROM notas_entrega WHERE id = ?", (nota_id,)
                ).fetchone()
            )
            return {"nota": nota, "items": items_insertados, "total": len(items_insertados)}
        finally:
            conn.close()

    def verificar_disponibilidad(self, ocr: NotaEntregaOCR) -> list:
        conn = self._get_conn()
        resultados = []
        try:
            for it in ocr.items:
                if not it.codigo_producto:
                    resultados.append(
                        {
                            "codigo": None,
                            "descripcion": it.descripcion,
                            "disponible": None,
                            "ubicacion": None,
                            "mensaje": "Código no detectado por OCR",
                        }
                    )
                    continue

                row = conn.execute(
                    """
                    SELECT codigo, descripcion, stock_maestro, campo7
                    FROM stock_maestro
                    WHERE codigo = ? OR codigo_barra = ?
                    LIMIT 1
                    """,
                    (it.codigo_producto, it.codigo_producto),
                ).fetchone()

                if row:
                    resultados.append(
                        {
                            "codigo": row["codigo"],
                            "descripcion": row["descripcion"],
                            "disponible": row["stock_maestro"],
                            "ubicacion": row["campo7"] or "Sin asignar",
                            "suficiente": (row["stock_maestro"] or 0) >= it.cantidad_solicitada,
                            "mensaje": (
                                "Stock suficiente"
                                if (row["stock_maestro"] or 0) >= it.cantidad_solicitada
                                else f"Stock insuficiente: {row['stock_maestro']} vs {it.cantidad_solicitada}"
                            ),
                        }
                    )
                else:
                    resultados.append(
                        {
                            "codigo": it.codigo_producto,
                            "descripcion": it.descripcion,
                            "disponible": None,
                            "ubicacion": None,
                            "mensaje": "Producto no encontrado en stock_maestro",
                        }
                    )
        finally:
            conn.close()
        return resultados

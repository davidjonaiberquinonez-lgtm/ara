"""Utilidades de identificación de notas del almacén de SAN CRISTÓBAL (S/C).

Las notas de San Cristóbal llegan con un prefijo alfabético de serie en el
código de barras (ej. `A0467959`): el alias visual conserva la `A`, pero la
consulta a Profit (CRISTM25) se hace SOLO con la parte numérica (`467959`),
puesto que `not_dep.fact_num` almacena el número sin el prefijo de serie.

DISCRIMINACIÓN AUTOMÁTICA DE ALMACÉN (regla de negocio de picking):
  - num_nota >  5000000  → BARQUISIMETO (Profit BQTO, almacén de despacho '04')
  - num_nota <= 5000000  → SAN CRISTÓBAL (Profit SQL CRISTM25, despacho '01'/'02')
"""

import re


def resolver_almacen_picking(num_nota_raw: str) -> dict:
    """Resuelve el almacén de origen de una nota SOLO por su número.

    Limpia el código (extrae solo dígitos) y aplica el umbral 5000000.
    Retorna {"origen", "db", "co_alma_list", "num_nota"} con los códigos de
    almacén de DESPACHO que filtran los renglones de picking:
      - SAN_CRISTOBAL → CRISTM25, co_alma IN ('01','02')
      - BARQUISIMETO  → PROFIT_BQTO, co_alma '04'
    """
    numeros = re.sub(r"\D", "", str(num_nota_raw))
    num_int = int(numeros) if numeros else 0

    if num_int <= 5000000:
        return {
            "origen": "SAN_CRISTOBAL",
            "db": "CRISTM25",
            "co_alma_list": ["01", "02"],  # Búsqueda/Filtro S/C
            "num_nota": str(num_int),
        }
    return {
        "origen": "BARQUISIMETO",
        "db": "PROFIT_BQTO",
        "co_alma_list": ["04"],  # Despacho BQTO
        "num_nota": str(num_int),
    }


def es_nota_san_cristobal(num_nota: str) -> bool:
    """True si el código tiene el patrón de serie S/C: 'A' + solo dígitos.

    Cubre el prefijo 'A0' ('A0467959') y el prefijo 'A' genérico ('A467959').
    Los códigos puramente numéricos retornan False: el llamador decide si
    corresponden a la serie S/C probando la fuente SQL directa.
    """
    s = str(num_nota or "").strip()
    if not s:
        return False
    u = s.upper()
    if not u.startswith("A"):
        return False
    resto = u[1:]
    return bool(resto) and resto.isdigit()


def normalizar_nota_sc(num_nota: str) -> dict:
    """Normaliza el código de barras de una posible nota S/C.

    Retorna {"es_sc": bool, "numero": str, "alias": str}:
      - 'numero': parte numérica sin ceros a la izquierda ('0467959' -> '467959'),
        usada como llave en not_dep/reng_nd de CRISTM25.
      - 'alias' : código tal cual lo escaneó el operador (se persiste en
        notas_entrega.numero_nota para que el re-escaneo coincida).
    """
    s = str(num_nota or "").strip()
    if not s:
        return {"es_sc": False, "numero": "", "alias": ""}
    u = s.upper()
    if u.startswith("A") and u[1:].isdigit():
        numero = u[1:].lstrip("0") or "0"
        return {"es_sc": True, "numero": numero, "alias": s}
    return {"es_sc": False, "numero": s, "alias": s}

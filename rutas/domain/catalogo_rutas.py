"""Clasificador simétrico de Macro-Rutas (dominio puro, sin dependencias externas).

Desacople hexagonal: el parseo del HTML legacy vive en la infraestructura
(legacy_route_adapter); aquí solo vive la lógica de negocio que normaliza un
nombre de sub-ruta y lo agrupa en su Macro-Ruta Padre.
"""
from typing import Dict, List, Tuple

# Reglas de clasificación simétrica: (claves a buscar, Macro-Ruta Padre).
# Se evalúan en orden; la primera coincidencia (case-insensitive) decide.
REGLAS_MACRO_RUTAS: List[Tuple[Tuple[str, ...], str]] = [
    # Rutas de TRANSFERENCIA ENTRE ALMACENES (v4.31) — "ENVIOS BARQUISIMETO"/
    # "ENVIOS S/C" del sistema legacy real (legacy_visor/RUTA_VISOR/index.php,
    # values 'barquisimeto1/BQTO-SC' y 'barquisimeto2/SC-BQTO'). Regla PRIMERO
    # en la lista: sin esto "ENVIOS BARQUISIMETO" caería en la macro
    # "BARQUISIMETO" por contener esa palabra, mezclándose con rutas
    # geográficas normales — son un tipo de ruta distinto (mercancía entre
    # almacenes, no reparto a clientes).
    (("ENVIOS",), "ENVÍOS ENTRE ALMACENES"),
    (("ARAGUA",), "ARAGUA"),
    (("CARACAS",), "CARACAS"),
    (("BARQUISIMETO",), "BARQUISIMETO"),
    (("CARABOBO",), "CARABOBO"),
    (("BARINAS",), "BARINAS"),
    (("PORTUGUESA",), "PORTUGUESA"),
    (("TRUJILLO",), "TRUJILLO"),
    (("ZULIA",), "ZULIA"),
    (("MERIDA",), "MERIDA"),
    (("FALCON",), "FALCON"),
    (("APURE",), "APURE"),
    (("FRONTERA", "TACHIRA", "PANAMERICANA"), "FRONTERA / TACHIRA"),
]

MACRO_RUTA_OTROS = "RUTAS ESPECIALES / OTROS"


def clasificar_macro_ruta(nombre_sub_ruta: str) -> str:
    """Retorna la Macro-Ruta Padre de una sub-ruta limpia (ej: 'ARAGUA - ZARAZA' → 'ARAGUA')."""
    nombre = str(nombre_sub_ruta or "").upper()
    for claves, macro in REGLAS_MACRO_RUTAS:
        if any(clave in nombre for clave in claves):
            return macro
    return MACRO_RUTA_OTROS


def agrupar_en_macro_rutas(sub_rutas: List[str]) -> Dict[str, List[str]]:
    """Agrupa simétricamente sub-rutas limpias en Macro-Rutas.

    - Preserva el orden de aparición dentro de cada macro.
    - Las Macro-Rutas conocidas salen primero (orden de REGLAS_MACRO_RUTAS)
      y 'RUTAS ESPECIALES / OTROS' siempre al final.
    - Elimina duplicados.
    """
    grupos: Dict[str, List[str]] = {}
    for nombre in sub_rutas:
        nombre = str(nombre or "").strip()
        if not nombre:
            continue
        macro = clasificar_macro_ruta(nombre)
        lista = grupos.setdefault(macro, [])
        if nombre not in lista:
            lista.append(nombre)

    orden = [macro for _, macro in REGLAS_MACRO_RUTAS if macro in grupos]
    orden += [macro for macro in grupos if macro not in orden]
    return {macro: grupos[macro] for macro in orden}

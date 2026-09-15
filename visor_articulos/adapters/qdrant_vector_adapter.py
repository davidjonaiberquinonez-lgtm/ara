# -*- coding: utf-8 -*-
"""Adaptador: índice vectorial REAL en la GDX (Qdrant + CLIP en su GPU).

Reemplaza ClipVectorAdapter (CPU local, catálogo re-ingerido en RAM en cada
arranque desde el PHP/CDN de la empresa) por la base ya poblada en la GDX
con el catálogo real de fotos de producto (10.490 imágenes, 192.168.4.24,
03/09/2026 — ver `ingesta_visual.py` en el repo GDX SPARK GB10).

A diferencia de ClipVectorAdapter, este adaptador NO mantiene copia propia
en RAM: cada búsqueda manda la foto capturada al embed server de la GDX
(mismo modelo open_clip ViT-B-32/laion2b_s34b_b79k usado para indexar TODO
el catálogo — condición obligatoria para que la similitud coseno tenga
sentido: comparar contra un espacio de embeddings distinto da resultados
sin significado) y consulta Qdrant por HTTP.

Configurable por env (ARA_VISOR_GDX_EMBED_URL / ARA_VISOR_GDX_QDRANT_URL /
ARA_VISOR_QDRANT_COLECCION) para no hardcodear la IP de la GDX si cambia.
"""
from __future__ import annotations

import os
from typing import List, Optional

import requests
from qdrant_client import QdrantClient

from visor_articulos.domain.articulo_matcher import Producto, VisualIndexPort, VisualMatch

GDX_EMBED_URL = os.environ.get("ARA_VISOR_GDX_EMBED_URL", "http://192.168.4.4:8500/embed")
GDX_QDRANT_URL = os.environ.get("ARA_VISOR_GDX_QDRANT_URL", "http://192.168.4.4:6333")
QDRANT_COLECCION = os.environ.get("ARA_VISOR_QDRANT_COLECCION", "productos_visual")
TIMEOUT_EMBED_S = float(os.environ.get("ARA_VISOR_GDX_EMBED_TIMEOUT_S", "10"))
TIMEOUT_QDRANT_S = float(os.environ.get("ARA_VISOR_GDX_QDRANT_TIMEOUT_S", "10"))


class ErrorMotorVisualGdx(Exception):
    """El embed server o Qdrant de la GDX no respondieron — el llamador
    (visor_routes.py, dentro de su try/except general) decide qué hacer;
    no se silencia acá porque un fallo de red real no debe disfrazarse de
    'sin coincidencia visual'."""


class QdrantVectorAdapter(VisualIndexPort):
    def __init__(
        self,
        embed_url: Optional[str] = None,
        qdrant_url: Optional[str] = None,
        coleccion: Optional[str] = None,
    ):
        self.embed_url = embed_url or GDX_EMBED_URL
        self.coleccion = coleccion or QDRANT_COLECCION
        self._cliente = QdrantClient(url=qdrant_url or GDX_QDRANT_URL, timeout=TIMEOUT_QDRANT_S)
        self._sesion = requests.Session()

    def _embeber(self, imagen: bytes) -> List[float]:
        try:
            r = self._sesion.post(
                self.embed_url,
                files={"imagen": ("captura.jpg", imagen)},
                timeout=TIMEOUT_EMBED_S,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            raise ErrorMotorVisualGdx(f"Embed server de la GDX no disponible ({self.embed_url}): {e}") from e
        return r.json()["vector"]

    def buscar_similares_visuales(self, imagen_capturada: bytes, top_k: int = 5) -> List[VisualMatch]:
        vector = self._embeber(imagen_capturada)
        try:
            resultados = self._cliente.query_points(
                collection_name=self.coleccion, query=vector, limit=top_k
            ).points
        except Exception as e:
            raise ErrorMotorVisualGdx(f"Qdrant de la GDX no disponible ({self.coleccion}): {e}") from e
        return [
            VisualMatch(producto=Producto(co_art=r.payload["codigo"]), similitud=float(r.score))
            for r in resultados
            if r.payload and r.payload.get("codigo")
        ]

    def registrar_producto(self, producto: Producto, imagen: bytes) -> None:
        """No-op a propósito: el catálogo visual vive en Qdrant, poblado
        directo en la GDX desde las fotos reales de red — no se re-ingiere
        producto por producto desde ARA_PROYECT. Para agregar productos
        nuevos, correr ingesta_visual.py de nuevo en la GDX."""
        return

    def precargar_catalogo(self) -> int:
        return self.cantidad_productos

    def guardar_cache(self) -> str:
        return ""

    def cargar_cache(self) -> int:
        return 0

    @property
    def cantidad_productos(self) -> int:
        try:
            return self._cliente.count(collection_name=self.coleccion, exact=True).count
        except Exception:
            return 0

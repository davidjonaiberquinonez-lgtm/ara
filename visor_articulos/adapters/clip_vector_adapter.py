# -*- coding: utf-8 -*-
"""Adaptador: índice vectorial con embeddings semánticos CLIP (openai/clip-vit-base-patch32).

Mismo contrato que InMemoryVectorAdapter (VisualIndexPort): registrar_producto,
buscar_similares_visuales, guardar_cache/cargar_cache, cantidad_productos —
intercambiable sin tocar php_catalog_adapter.py ni visor_service.py.

Por qué CLIP en vez de (o además de) la huella perceptual: CLIP entiende
similitud SEMÁNTICA (forma de la caja, tipo de envase, texto/logo, color de
etiqueta como conjunto) mientras que la huella perceptual actual compara
histogramas de brillo/color/textura crudos — más sensible a ángulo, luz y
fondo. CLIP es el mismo modelo de la propuesta original del usuario; se
eligió ViT-B/32 (512 dim) en vez de ViT-L/14 (768 dim, ~3x más pesado) porque
esta máquina no tiene GPU dedicada (medido en vivo: ~150ms/imagen en CPU con
B/32, holgado para indexar ~2.300 productos e inferencia en tiempo real por
foto). Migrar a ViT-L/14 + TensorRT queda como mejora futura cuando el
servidor GB10 esté disponible — el contrato no cambia, solo la constante
MODELO_CLIP y la dimensión del índice.
"""
from __future__ import annotations

import io
import json
import os
import threading
from typing import Dict, List, Optional

import numpy as np
from PIL import Image

from visor_articulos.domain.articulo_matcher import Producto, VisualIndexPort, VisualMatch

MODELO_CLIP = os.environ.get("ARA_CLIP_MODELO", "openai/clip-vit-base-patch32")

# Reconstrucción amortizada de la matriz (mismo patrón que InMemoryVectorAdapter).
_BLOQUE_MATRIZ = 512

_lock_modelo = threading.Lock()
_modelo = None
_procesador = None


def _cargar_modelo():
    """Carga perezosa y compartida del modelo CLIP (una sola vez por proceso)."""
    global _modelo, _procesador
    if _modelo is not None:
        return _modelo, _procesador
    with _lock_modelo:
        if _modelo is None:
            from transformers import CLIPModel, CLIPProcessor
            _modelo = CLIPModel.from_pretrained(MODELO_CLIP)
            _modelo.eval()
            _procesador = CLIPProcessor.from_pretrained(MODELO_CLIP)
    return _modelo, _procesador


def calcular_embedding_clip(imagen: Image.Image) -> np.ndarray:
    """Embedding semántico CLIP normalizado L2 (float32, listo para producto interno)."""
    import torch
    modelo, procesador = _cargar_modelo()
    if imagen.mode != "RGB":
        imagen = imagen.convert("RGB")
    entradas = procesador(images=imagen, return_tensors="pt")
    with torch.no_grad():
        salida = modelo.vision_model(**entradas)
        vector = modelo.visual_projection(salida.pooler_output)
    vector = vector.squeeze(0).numpy().astype(np.float32)
    norma = np.linalg.norm(vector)
    if norma > 0:
        vector = vector / norma
    return vector


def embedding_desde_bytes(imagen_bytes: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(imagen_bytes)) as im:
        return calcular_embedding_clip(im)


class ClipVectorAdapter(VisualIndexPort):
    """Índice vectorial en RAM con embeddings CLIP — mismo diseño que
    InMemoryVectorAdapter (matriz NxD + producto interno), pero con vectores
    semánticos en vez de huella perceptual hecha a mano."""

    def __init__(self, cache_dir: Optional[str] = None):
        self._lock = threading.RLock()
        self._productos: Dict[str, Producto] = {}
        self._orden: List[str] = []
        self._pendientes: List[np.ndarray] = []
        self._matriz: Optional[np.ndarray] = None
        self._matriz_sucia: bool = True
        self.cache_dir = cache_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache_vectorial_clip"
        )

    def precargar_catalogo(self) -> int:
        """Sin carpeta local de imágenes propia (mismo hueco que
        InMemoryVectorAdapter tendría sin `data/imagenes/`): siempre 0. Existe
        solo para cumplir el contrato que usa cargar_o_ingerir() en su cadena
        de fallbacks (PHP -> caché disco -> carpeta local -> BD local)."""
        return 0

    # ------------------------------------------------------------------ ingesta

    def _reconstruir_matriz(self) -> None:
        if not self._pendientes:
            self._matriz = None
        else:
            self._matriz = np.vstack(self._pendientes).astype(np.float32)
        self._matriz_sucia = False

    def _asegurar_matriz(self) -> None:
        if self._matriz_sucia:
            self._reconstruir_matriz()

    def registrar_producto(self, producto: Producto, imagen: bytes) -> None:
        embedding = embedding_desde_bytes(imagen)
        with self._lock:
            if producto.co_art in self._productos:
                i = self._orden.index(producto.co_art)
                self._pendientes[i] = embedding
                self._matriz_sucia = True
            else:
                self._productos[producto.co_art] = producto
                self._orden.append(producto.co_art)
                self._pendientes.append(embedding)
                self._matriz_sucia = True
                if len(self._pendientes) >= _BLOQUE_MATRIZ:
                    self._reconstruir_matriz()

    # ------------------------------------------------------------------ búsqueda

    def buscar_similares_visuales(
        self, imagen_capturada: bytes, top_k: int = 5
    ) -> List[VisualMatch]:
        query = embedding_desde_bytes(imagen_capturada)
        return self.buscar_por_embedding(query, top_k=top_k)

    def buscar_por_embedding(self, embedding: np.ndarray, top_k: int = 5) -> List[VisualMatch]:
        with self._lock:
            self._asegurar_matriz()
            matriz = self._matriz
            if matriz is None or matriz.shape[0] == 0:
                return []
            similitudes = matriz @ embedding
            k = min(top_k, int(matriz.shape[0]))
            idx = np.argpartition(similitudes, -k)[-k:]
            idx = idx[np.argsort(similitudes[idx])[::-1]]
            return [
                VisualMatch(
                    producto=self._productos[self._orden[i]],
                    similitud=float(np.clip(similitudes[i], 0.0, 1.0)),
                )
                for i in idx
            ]

    # ----------------------------------------------------------- caché en disco

    def guardar_cache(self) -> str:
        with self._lock:
            self._asegurar_matriz()
            if self._matriz is None:
                return ""
            os.makedirs(self.cache_dir, exist_ok=True)
            ruta_npz = os.path.join(self.cache_dir, "embeddings.npz")
            ruta_meta = os.path.join(self.cache_dir, "metadatos.json")
            np.savez_compressed(ruta_npz, matriz=self._matriz)
            metadatos = {
                "version": 1,
                "modelo": MODELO_CLIP,
                "orden": self._orden,
                "productos": {
                    codigo: {
                        "co_art": p.co_art,
                        "descripcion": p.descripcion,
                        "laboratorio": p.laboratorio,
                        "dosis": p.dosis,
                        "codigo_barra": p.codigo_barra,
                        "imagen_url": p.imagen_url,
                        "ubicacion": p.ubicacion,
                    }
                    for codigo, p in self._productos.items()
                },
            }
            with open(ruta_meta, "w", encoding="utf-8") as f:
                json.dump(metadatos, f, ensure_ascii=False)
            return ruta_npz

    def cargar_cache(self) -> int:
        ruta_npz = os.path.join(self.cache_dir, "embeddings.npz")
        ruta_meta = os.path.join(self.cache_dir, "metadatos.json")
        if not (os.path.isfile(ruta_npz) and os.path.isfile(ruta_meta)):
            return 0
        with open(ruta_meta, "r", encoding="utf-8") as f:
            metadatos = json.load(f)
        datos = np.load(ruta_npz)
        matriz = datos["matriz"]
        with self._lock:
            self._productos = {}
            self._orden = list(metadatos["orden"])
            for codigo, info in metadatos["productos"].items():
                self._productos[codigo] = Producto(**info)
            self._matriz = matriz.astype(np.float32)
            self._pendientes = [matriz[i] for i in range(int(matriz.shape[0]))]
            self._matriz_sucia = False
        return len(self._productos)

    @property
    def cantidad_productos(self) -> int:
        return len(self._orden)

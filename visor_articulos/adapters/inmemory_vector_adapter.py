# -*- coding: utf-8 -*-
"""Adaptador: índice vectorial en memoria (huellas perceptuales en RAM)."""
from __future__ import annotations

import io
import json
import os
import threading
from typing import Dict, List, Optional

import numpy as np
from PIL import Image

from visor_articulos.domain.articulo_matcher import Producto, VisualIndexPort, VisualMatch

# Dimensiones de la huella perceptual
_SIZE_GRIS = 32  # cuadrícula de luminancia 32x32 -> 1024 dims
_BINS_HIST_HUE = 12
_BINS_HIST_SAT = 8
_BINS_HIST_VAL = 8
_BINS_TEXTURA = 8

DIM_HUELLA = _SIZE_GRIS * _SIZE_GRIS + _BINS_HIST_HUE + _BINS_HIST_SAT + _BINS_HIST_VAL + _BINS_TEXTURA

# Reconstrucción amortizada de la matriz: cada N registros se rebuilda la
# matriz (evita el np.vstack O(N^2) fila a fila con catálogos de 5.000 ítems).
_BLOQUE_MATRIZ = 512


def _normalizar_bloques(gris_32: np.ndarray, tam_bloque: int = 8) -> np.ndarray:
    """Normalización local por bloques (z-score por celda 8x8).

    Cancela gradientes suaves de iluminación (reflejo/sombra) sin destruir el
    contenido local (manchas, etiquetas), a diferencia de una ecualización global.
    """
    n = _SIZE_GRIS // tam_bloque
    bloques = gris_32.reshape(n, tam_bloque, n, tam_bloque)
    media = bloques.mean(axis=(1, 3), keepdims=True)
    desv = bloques.std(axis=(1, 3), keepdims=True)
    normalizado = (bloques - media) / (desv + 1e-6)
    return normalizado.reshape(_SIZE_GRIS, _SIZE_GRIS)


def calcular_huella_imagen(imagen: Image.Image) -> np.ndarray:
    """Embedding perceptual compacto (numpy puro, sin OpenCV)."""
    if imagen.mode != "RGB":
        imagen = imagen.convert("RGB")

    # 1) Estructura espacial: luminancia 32x32 con normalización local
    gris = imagen.convert("L").resize((_SIZE_GRIS, _SIZE_GRIS), Image.BOX)
    gris_32 = np.asarray(gris, dtype=np.float32) / 255.0
    estructura = _normalizar_bloques(gris_32).reshape(-1)

    # 2) Color: histogramas HSV cuantizados (H robusto al brillo)
    hsv = np.asarray(imagen.convert("HSV").resize((_SIZE_GRIS, _SIZE_GRIS), Image.BOX), dtype=np.float32)
    h = hsv[..., 0] / 255.0
    s = hsv[..., 1] / 255.0
    v = hsv[..., 2] / 255.0
    hist_h, _ = np.histogram(h, bins=_BINS_HIST_HUE, range=(0.0, 1.0))
    hist_s, _ = np.histogram(s, bins=_BINS_HIST_SAT, range=(0.0, 1.0))
    hist_v, _ = np.histogram(v, bins=_BINS_HIST_VAL, range=(0.0, 1.0))
    color = np.concatenate(
        [
            hist_h.astype(np.float32),
            hist_s.astype(np.float32),
            hist_v.astype(np.float32),
        ]
    )
    color = color / max(color.sum(), 1e-6)

    # 3) Textura: histograma del gradiente numérico sobre la cuadrícula 32x32
    gx = np.abs(np.diff(gris_32, axis=1))[:-1, :]  # (31,31)
    gy = np.abs(np.diff(gris_32, axis=0))[:, :-1]  # (31,31)
    magnitud = np.sqrt(gx.astype(np.float64) ** 2 + gy.astype(np.float64) ** 2)
    hist_t, _ = np.histogram(magnitud, bins=_BINS_TEXTURA, range=(0.0, np.sqrt(2)))
    textura = hist_t.astype(np.float32) / max(hist_t.sum(), 1e-6)

    huella = np.concatenate([estructura, color, textura])
    norma = np.linalg.norm(huella)
    if norma > 0:
        huella = huella / norma
    return huella.astype(np.float32)


def huella_desde_bytes(imagen_bytes: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(imagen_bytes)) as im:
        return calcular_huella_imagen(im)


class InMemoryVectorAdapter(VisualIndexPort):
    """Índice vectorial en RAM con búsqueda por producto interno (matmul numpy).

    - Precarga automática de un catálogo de imágenes locales (`data/imagenes/`).
    - Matriz NxD en memoria: `buscar_similares_visuales` = 1 producto interno.
    - Persistencia de huellas en disco para el fallback de ingesta PHP.
    """

    def __init__(self, carpeta_imagenes: Optional[str] = None, cache_dir: Optional[str] = None):
        self._lock = threading.RLock()
        self._productos: Dict[str, Producto] = {}
        self._orden: List[str] = []
        # Huellas alineadas con `_orden`; la matriz numpy se reconstruye de
        # forma amortizada (cada _BLOQUE_MATRIZ registros o al consultar).
        self._pendientes: List[np.ndarray] = []
        self._matriz: Optional[np.ndarray] = None
        self._matriz_sucia: bool = True
        self.carpeta_imagenes = carpeta_imagenes or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "imagenes"
        )
        self.cache_dir = cache_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache_vectorial"
        )

    # ------------------------------------------------------------------ ingesta

    def _reconstruir_matriz(self) -> None:
        """Rebuilda la matriz NxD a partir de las huellas pendientes (1 matmul)."""
        if not self._pendientes:
            self._matriz = None
        else:
            self._matriz = np.vstack(self._pendientes).astype(np.float32)
        self._matriz_sucia = False

    def _asegurar_matriz(self) -> None:
        if self._matriz_sucia:
            self._reconstruir_matriz()

    def registrar_producto(self, producto: Producto, imagen: bytes) -> None:
        huella = huella_desde_bytes(imagen)
        with self._lock:
            if producto.co_art in self._productos:
                i = self._orden.index(producto.co_art)
                self._pendientes[i] = huella
                self._matriz_sucia = True
            else:
                self._productos[producto.co_art] = producto
                self._orden.append(producto.co_art)
                self._pendientes.append(huella)
                self._matriz_sucia = True
                if len(self._pendientes) >= _BLOQUE_MATRIZ:
                    self._reconstruir_matriz()

    def registrar_producto_desde_archivo(self, producto: Producto, ruta: str) -> bool:
        if not os.path.isfile(ruta):
            return False
        with open(ruta, "rb") as f:
            self.registrar_producto(producto, f.read())
        return True

    def precargar_catalogo(self) -> int:
        """Precarga huellas desde `data/imagenes/*.{png,jpg,jpeg}` nombrados `{co_art}.ext`."""
        cargados = 0
        if not os.path.isdir(self.carpeta_imagenes):
            return 0
        for nombre in sorted(os.listdir(self.carpeta_imagenes)):
            base, ext = os.path.splitext(nombre)
            if ext.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            if self.registrar_producto_desde_archivo(Producto(co_art=base), os.path.join(self.carpeta_imagenes, nombre)):
                cargados += 1
        return cargados

    # ------------------------------------------------------------------ búsqueda

    def buscar_similares_visuales(
        self, imagen_capturada: bytes, top_k: int = 5
    ) -> List[VisualMatch]:
        query = huella_desde_bytes(imagen_capturada)
        return self.buscar_por_huella(query, top_k=top_k)

    def buscar_por_huella(self, huella: np.ndarray, top_k: int = 5) -> List[VisualMatch]:
        with self._lock:
            self._asegurar_matriz()
            matriz = self._matriz
            if matriz is None or matriz.shape[0] == 0:
                return []
            similitudes = matriz @ huella  # producto interno (vectores L2-norm)
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
        """Persiste huellas + metadatos para el fallback de ingesta PHP."""
        with self._lock:
            self._asegurar_matriz()
            if self._matriz is None:
                return ""
            os.makedirs(self.cache_dir, exist_ok=True)
            ruta_npz = os.path.join(self.cache_dir, "huellas.npz")
            ruta_meta = os.path.join(self.cache_dir, "metadatos.json")
            np.savez_compressed(ruta_npz, matriz=self._matriz)
            metadatos = {
                "version": 1,
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
        """Carga caché vectorial desde disco. Retorna productos restaurados (0 si no hay)."""
        ruta_npz = os.path.join(self.cache_dir, "huellas.npz")
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

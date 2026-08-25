# -*- coding: utf-8 -*-
"""Generador de empaques sintéticos (frontales) para el benchmark piloto.

Modela el problema real del almacén:
    - 5 laboratorios con diseño de caja propio (color + patrón + banda).
    - Productos normales: nombre genérico, dosis y barcode únicos.
    - Pares gemelos: mismo laboratorio, diseño idéntico, mismo nombre genérico,
      SOLO cambian la dosificación y el código de barras -> exigen OCR para
      desempatar (escenario 4).
    - Perturbaciones: reflejo/sombra (escenario 2) y etiqueta desgastada/tapada
      (escenario 3).
"""
from __future__ import annotations

import hashlib
import io
import random
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFilter

from visor_articulos.domain.articulo_matcher import Producto

ANCHO, ALTO = 240, 340


def _semilla(*partes: str) -> int:
    """Semilla determinista y estable entre procesos (hash() es aleatorio por proceso)."""
    return int(hashlib.md5("|".join(partes).encode("utf-8")).hexdigest()[:8], 16)

# Diseños por laboratorio: (color base, color banda, patrón fondo)
DISEÑOS_LAB = {
    "LAB-A": ((200, 40, 40), (120, 20, 20), "diagonal"),
    "LAB-B": ((30, 90, 200), (15, 50, 130), "barras"),
    "LAB-C": ((30, 150, 80), (15, 90, 50), "puntos"),
    "LAB-D": ((230, 130, 30), (160, 85, 15), "diagonal"),
    "LAB-E": ((130, 60, 180), (80, 30, 120), "barras"),
}

NOMBRES_NORMALES = [
    ("ACETAMINOFEN", "500 MG"),
    ("IBUPROFENO", "400 MG"),
    ("AMOXICILINA", "250 MG"),
    ("OMEPRAZOL", "20 MG"),
    ("LOSARTAN", "50 MG"),
    ("METFORMINA", "850 MG"),
    ("DICLOFENACO", "75 MG"),
    ("RANITIDINA", "150 MG"),
    ("CEFALEXINA", "500 MG"),
    ("CLORFENIRAMINA", "4 MG"),
    ("NAPROXENO", "250 MG"),
    ("PIRACETAM", "800 MG"),
    ("GLIBENCLAMIDA", "5 MG"),
    ("KETOPROFENO", "100 MG"),
    ("SULFAMETOXAZOL", "400 MG"),
]

NOMBRES_GEMELOS = [
    ("ACETAMINOFEN", "250 MG", "500 MG"),
    ("IBUPROFENO", "200 MG", "400 MG"),
    ("OMEPRAZOL", "20 MG", "40 MG"),
    ("LOSARTAN", "50 MG", "100 MG"),
]


def _barcode_patron(co_art: str) -> List[int]:
    """Anchuras de barras deterministas a partir del código."""
    h = hashlib.md5(co_art.encode("utf-8")).digest()
    anchos = [2 + (h[i] % 4) for i in range(14)]
    return anchos


def _dibujar_base(draw: ImageDraw.ImageDraw, lab: str) -> None:
    color, banda, patron = DISEÑOS_LAB[lab]
    draw.rectangle([10, 10, ANCHO - 10, ALTO - 10], fill=(250, 250, 252), outline=color, width=4)
    rng = random.Random(_semilla(lab))
    if patron == "diagonal":
        for x in range(-ALTO, ANCHO, 34):
            draw.polygon(
                [(x, ALTO), (x + 16, ALTO), (x + 16 + 26, 0), (x + 26, 0)],
                fill=tuple(min(255, c + 14) for c in color),
            )
    elif patron == "barras":
        for x in range(24, ANCHO - 24, 30):
            draw.rectangle([x, 24, x + 10, ALTO - 24], fill=tuple(min(255, c + 14) for c in color))
    else:
        rng = random.Random(_semilla(lab, "puntos"))
        for _ in range(26):
            cx, cy = rng.randint(30, ANCHO - 30), rng.randint(40, ALTO - 60)
            draw.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=tuple(min(255, c + 14) for c in color))
    draw.rectangle([10, 10, ANCHO - 10, 64], fill=banda)
    draw.rectangle([10, ALTO - 96, ANCHO - 10, ALTO - 10], fill=banda)


def _dibujar_texto(draw: ImageDraw.ImageDraw, texto: str, y: int, size: int = 24, fill=(20, 20, 20)) -> None:
    """Texto centralizado sin depender de fuentes externas (bitmap escalado)."""
    fuente = ImageFont_alt(texto, size)
    draw.text(((ANCHO - fuente[0]) // 2, y), texto, fill=fill)


def ImageFont_alt(texto: str, size: int):
    from PIL import ImageFont

    f = ImageFont.load_default(size)
    bbox = f.getbbox(texto)
    ancho = (bbox[2] - bbox[0]) if bbox else 0
    return (ancho, f)


def _dibujar_barcode(draw: ImageDraw.ImageDraw, co_art: str, y: int = 268) -> None:
    # Los pares gemelos (diseño idéntico) comparten el mismo patrón visual de
    # barras: solo la dosificación los distingue (fuerza el desempate OCR).
    clave = co_art
    if co_art.startswith("G"):
        clave = co_art[:-1]  # G{lab}{n}A/B -> mismo patrón para el par
    anchos = _barcode_patron(clave)
    x = 40
    alterna = True
    for a in anchos:
        if alterna:
            draw.rectangle([x, y, x + a, y + 52], fill=(15, 15, 15))
        x += a + 3
        alterna = not alterna


def generar_empaque(producto: Producto, perturbacion: str = "perfecto") -> Image.Image:
    """Renderiza el frontal del empaque. perturbacion: perfecto|reflejo|desgastado."""
    lab = producto.laboratorio or "LAB-A"
    img = Image.new("RGB", (ANCHO, ALTO), (245, 245, 248))
    draw = ImageDraw.Draw(img)
    _dibujar_base(draw, lab)

    color, _, _ = DISEÑOS_LAB[lab]
    # Banda superior: laboratorio
    draw.text((24, 22), lab, fill=(255, 255, 255))
    # Cuerpo: nombre genérico
    nombre = producto.descripcion.split()[0] if producto.descripcion else producto.co_art
    _dibujar_texto(draw, nombre[:14], 96, size=26, fill=color)
    # Zona de dosificación (texto pequeño: a 32x32 apenas aporta píxeles ->
    # los pares gemelos quedan visualmente ambiguos, exige OCR)
    _dibujar_texto(draw, producto.dosis or "S/D", 152, size=16, fill=(60, 60, 60))
    _dibujar_texto(draw, "UN SOLO USO", 200, size=14, fill=(110, 110, 110))
    # Código de barras
    _dibujar_barcode(draw, producto.co_art)

    if perturbacion == "reflejo":
        img = _aplicar_reflejo_sombra(img)
    elif perturbacion == "desgastado":
        img = _aplicar_desgaste(img, producto)

    return img


def _aplicar_reflejo_sombra(img: Image.Image) -> Image.Image:
    """Reflejo parcial: banda de luz diagonal suave + sombra lateral leve."""
    ref = Image.new("RGBA", (ANCHO, ALTO), (0, 0, 0, 0))
    d = ImageDraw.Draw(ref)
    # Franja de reflejo estrecha con desvanecido
    for i in range(26):
        x0 = 60 + i
        d.line([(x0, 0), (x0 - 60, ALTO)], fill=(255, 255, 255, max(0, 90 - i * 2)), width=3)
    sombra = Image.new("RGBA", (ANCHO, ALTO), (0, 0, 0, 0))
    ds = ImageDraw.Draw(sombra)
    ds.rectangle([int(ANCHO * 0.80), 0, ANCHO, ALTO], fill=(0, 0, 0, 70))
    base = img.convert("RGBA")
    base = Image.alpha_composite(base, ref)
    base = Image.alpha_composite(base, sombra)
    return base.convert("RGB")


def _aplicar_desgaste(img: Image.Image, producto: Producto) -> Image.Image:
    """Etiqueta desgastada: mancha que tapa la zona de dosificación + ruido + blur leve."""
    rng = random.Random(_semilla(producto.co_art, "desgaste"))
    resultado = img.convert("RGB")
    # Mancha tapando la etiqueta central (dosis + subtítulo)
    mancha = Image.new("RGBA", (ANCHO, ALTO), (0, 0, 0, 0))
    dm = ImageDraw.Draw(mancha)
    color_lab, _, _ = DISEÑOS_LAB.get(producto.laboratorio or "LAB-A", ((200, 200, 200), (0, 0, 0), ""))
    for _ in range(rng.randint(4, 6)):
        x, y = rng.randint(30, ANCHO - 90), rng.randint(130, 225)
        w, h = rng.randint(60, 130), rng.randint(18, 34)
        dm.rounded_rectangle([x, y, x + w, y + h], radius=8, fill=tuple(color_lab) + (235,))
    resultado = Image.alpha_composite(resultado.convert("RGBA"), mancha).convert("RGB")
    # Ruido sal y pimienta ligero
    px = resultado.load()
    for _ in range(1400):
        x, y = rng.randint(0, ANCHO - 1), rng.randint(0, ALTO - 1)
        v = rng.choice((0, 255))
        px[x, y] = (v, v, v)
    return resultado.filter(ImageFilter.GaussianBlur(0.6))


def producto_a_bytes(producto: Producto, perturbacion: str = "perfecto") -> bytes:
    buf = io.BytesIO()
    generar_empaque(producto, perturbacion).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Construcción del catálogo sintético
# ---------------------------------------------------------------------------


def construir_catalogo() -> List[Producto]:
    """44 productos: 20 normales + 24 gemelos (12 pares en 3 laboratorios)."""
    catalogo: List[Producto] = []
    labs = ["LAB-A", "LAB-B", "LAB-C", "LAB-D", "LAB-E"]

    for i, lab in enumerate(labs):
        for j in range(4):
            idx = i * 4 + j
            nombre, dosis = NOMBRES_NORMALES[idx % len(NOMBRES_NORMALES)]
            co = f"N{lab[-1]}{idx + 1:03d}"
            catalogo.append(
                Producto(
                    co_art=co,
                    descripcion=f"{nombre} {dosis}",
                    laboratorio=lab,
                    dosis=dosis,
                    codigo_barra=f"750{idx:08d}{co}",
                )
            )

    labs_gemelos = ["LAB-A", "LAB-B", "LAB-C"]
    gi = 0
    for lab in labs_gemelos:
        for nombre, d1, d2 in NOMBRES_GEMELOS:
            g1 = Producto(
                co_art=f"G{lab[-1]}{gi}A",
                descripcion=f"{nombre} {d1}",
                laboratorio=lab,
                dosis=d1,
                codigo_barra=f"7701{gi:04d}A",
            )
            g2 = Producto(
                co_art=f"G{lab[-1]}{gi}B",
                descripcion=f"{nombre} {d2}",
                laboratorio=lab,
                dosis=d2,
                codigo_barra=f"7701{gi:04d}B",
            )
            catalogo.extend([g1, g2])
            gi += 1
    return catalogo


def buscar_producto(catalogo: List[Producto], co_art: str) -> Optional[Producto]:
    for p in catalogo:
        if p.co_art == co_art:
            return p
    return None

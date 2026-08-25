# -*- coding: utf-8 -*-
"""Suite pytest del motor híbrido del visor de artículos.

Reutiliza la batería de 100 reconocimientos y el diagnóstico híbrido del
harness, y añade pruebas unitarias de dominio y adaptadores. Uso:

    py -3.14 -m pytest visor_articulos/tests/test_piloto_suite.py -v
"""
from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from visor_articulos.adapters.inmemory_vector_adapter import (
    InMemoryVectorAdapter,
    calcular_huella_imagen,
    huella_desde_bytes,
)
from visor_articulos.adapters.php_catalog_adapter import (
    BASE_IMAGENES_CRIST,
    PhpCatalogAdapter,
    cargar_o_ingerir,
)
from visor_articulos.application.visor_service import MotorBusquedaVisual
from visor_articulos.domain.articulo_matcher import (
    BOOST_BARCODE,
    PESO_TEXTO_DEFAULT,
    PESO_VISUAL_DEFAULT,
    UMBRAL_AMBIGUEDAD_VISUAL,
    Producto,
    TextoExtraido,
    VisualMatch,
    desempatar_por_ocr,
    score_hibrido,
)
from visor_articulos.tests.generador_empaques import construir_catalogo, producto_a_bytes
from visor_articulos.tests.piloto_100_reconocimiento import (
    ESCENARIOS,
    PESO_VISUAL,
    diagnosticar_híbrido,
    ejecutar_bateria,
)

CATALOGO = construir_catalogo()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def indice(tmp_path_factory):
    """Índice con el catálogo completo ingerido (compartido por la batería)."""
    cache_dir = str(tmp_path_factory.mktemp("cache_vectorial"))
    indice = InMemoryVectorAdapter(cache_dir=cache_dir)
    for producto in CATALOGO:
        indice.registrar_producto(producto, producto_a_bytes(producto, "perfecto"))
    indice.guardar_cache()
    assert indice.cantidad_productos == 44
    return indice


# ---------------------------------------------------------------------------
# Batería de 100 reconocimientos (métricas estándar del piloto)
# ---------------------------------------------------------------------------


def test_bateria_100_metricas_estandar(indice):
    """Top-1 >= 85 %, Top-3 >= 95 %, latencia < 150 ms, RAM < 15 ms.

    El umbral de RAM pura (matmul numpy) se fija en 15 ms porque la carga
    del CPU en CI/ejecución compartida fluctúa (12-13 ms observados); el
    requisito de negocio es < 150 ms por consulta, con margen enorme.
    """
    filas, globales, prom_ram = ejecutar_bateria(CATALOGO, indice)
    n = globales["casos"]
    assert n == 100
    assert globales["top1"] / n >= 0.85
    assert globales["top3"] / n >= 0.95
    assert globales["sum_match_ms"] / n < 150.0
    assert globales["max_match_ms"] < 150.0
    assert prom_ram < 15.0


def test_escenario_identico_desempata_por_dosis(indice):
    """Escenario 4 (diseño idéntico): el híbrido debe cerrar Top-3 al 100 %."""
    filas, _, _ = ejecutar_bateria(CATALOGO, indice)
    fila_identico = dict(zip(("titulo", "n", "top1", "top3", "prom", "max"), filas[3]))
    assert fila_identico["top3"] >= 95.0


def test_escenario_desgastado_top1(indice):
    """Escenario 3 (etiqueta desgastada): Top-1 robusto a la perturbación."""
    filas, _, _ = ejecutar_bateria(CATALOGO, indice)
    fila_desgastado = dict(zip(("titulo", "n", "top1", "top3", "prom", "max"), filas[2]))
    assert fila_desgastado["top1"] >= 85.0


def test_diagnostico_hibrido_ambiguedad_real_desempate(indice):
    """La ambigüedad visual es real (margen < umbral) y el OCR desempata 24/24."""
    assert diagnosticar_híbrido(CATALOGO, indice)


def test_bateria_reproducible(indice):
    """Con semillas deterministas los aciertos de la batería son estables."""
    f1, g1, _ = ejecutar_bateria(CATALOGO, indice)
    f2, g2, _ = ejecutar_bateria(CATALOGO, indice)
    assert g1["top1"] == g2["top1"]
    assert g1["top3"] == g2["top3"]
    assert [f[1:3] for f in f1] == [f[1:3] for f in f2]


# ---------------------------------------------------------------------------
# Dominio: fusión visual + OCR
# ---------------------------------------------------------------------------


def _producto(co="NA001", dosis="500 mg", barcode="750000000000NA001"):
    return Producto(
        co_art=co,
        descripcion="PARACETAMOL 500 mg",
        laboratorio="LAB-A",
        dosis=dosis,
        codigo_barra=barcode,
    )


def test_score_hibrido_respeta_pesos():
    p = _producto()
    texto = TextoExtraido(texto="PARACETAMOL", dosis="500 mg", confianza=0.9)
    esperado = PESO_VISUAL_DEFAULT * 0.80 + PESO_TEXTO_DEFAULT * 1.0
    assert score_hibrido(p, 0.80, texto) == pytest.approx(esperado)


def test_score_hibrido_boost_barcode():
    p = _producto()
    texto = TextoExtraido(codigo_barra=p.codigo_barra, confianza=1.0)
    base = PESO_VISUAL_DEFAULT * 0.50 + PESO_TEXTO_DEFAULT * 0.0
    esperado = min(1.0, base + BOOST_BARCODE * 1.0)
    assert score_hibrido(p, 0.50, texto) == pytest.approx(esperado)
    assert score_hibrido(p, 0.50, texto) > score_hibrido(
        p, 0.50, TextoExtraido(confianza=1.0)
    )


def test_desempate_ocr_solo_con_ambiguedad_real():
    p1 = _producto("NA001", dosis="500 mg")
    p2 = _producto("NA002", dosis="250 mg", barcode="750000000000NA002")
    candidatos = [VisualMatch(p1, 0.9000), VisualMatch(p2, 0.8600)]
    # Sin barcode leído: aísla la lógica del umbral de ambigüedad
    texto_fiable = TextoExtraido(confianza=0.9)

    # Margen 0.04 = exactamente el umbral -> no ambiguo, sin desempate OCR
    reordenado = desempatar_por_ocr(candidatos, texto_fiable)
    assert reordenado[0].producto.co_art == p1.co_art

    # Margen < umbral -> el OCR reordena por coincidencia de dosis
    ambiguos = [VisualMatch(p1, 0.9000), VisualMatch(p2, 0.8999)]
    texto_dosis_p2 = TextoExtraido(texto="PARACETAMOL", dosis="250 mg", confianza=0.9)
    reordenado = desempatar_por_ocr(ambiguos, texto_dosis_p2)
    assert reordenado[0].producto.co_art == p2.co_art


def test_desempate_ocr_boost_barcode_siempre_activo():
    """El boost de barcode aplica también sin ambigüedad (evidencia determinante)."""
    p1, p2 = _producto("NA001"), _producto("NA002", barcode="750000000000NA002")
    candidatos = [VisualMatch(p1, 0.9000), VisualMatch(p2, 0.8600)]
    texto_barcode_p2 = TextoExtraido(confianza=0.9, codigo_barra=p2.codigo_barra)
    reordenado = desempatar_por_ocr(candidatos, texto_barcode_p2)
    assert reordenado[0].producto.co_art == p2.co_art


def test_desempate_ocr_ignora_confianza_baja():
    p1, p2 = _producto("NA001"), _producto("NA002", barcode="750000000000NA002")
    ambiguos = [VisualMatch(p1, 0.9000), VisualMatch(p2, 0.8999)]
    texto_baja = TextoExtraido(confianza=0.30)
    reordenado = desempatar_por_ocr(ambiguos, texto_baja)
    assert reordenado[0].producto.co_art == p1.co_art


# ---------------------------------------------------------------------------
# Adaptadores: CDN de Crist Medicals y cadena de fallbacks
# ---------------------------------------------------------------------------


def test_url_canonica_crist_medicals():
    adapter = PhpCatalogAdapter()
    assert adapter.url_imagen_default("AMP00045") == (
        f"{BASE_IMAGENES_CRIST}AMP00045.jpg"
    )


def test_fallback_ingesta_vacio_sin_excepcion(tmp_path, monkeypatch):
    """Sin PHP ni caché ni carpeta local ni BD local -> origen 'vacio'."""
    monkeypatch.setattr(
        "visor_articulos.adapters.php_catalog_adapter.DB_PATH_LOCAL",
        str(tmp_path / "bd_inexistente.db"),
    )
    indice = InMemoryVectorAdapter(cache_dir=str(tmp_path / "cache"))
    adapter = PhpCatalogAdapter(url="http://127.0.0.1:1/catalogo.php")
    estado = cargar_o_ingerir(indice, adapter=adapter)
    assert estado["origen"] == "vacio"
    assert estado["productos_indexados"] == 0


def test_cache_roundtrip_persistente(tmp_path):
    """Guardar caché en disco y restaurarla en un índice nuevo."""
    cache_dir = str(tmp_path / "cache_vectorial")
    indice = InMemoryVectorAdapter(cache_dir=cache_dir)
    for producto in CATALOGO[:6]:
        indice.registrar_producto(producto, producto_a_bytes(producto, "perfecto"))
    ruta = indice.guardar_cache()
    assert os.path.isfile(ruta)

    indice2 = InMemoryVectorAdapter(cache_dir=cache_dir)
    assert indice2.cargar_cache() == 6
    res = indice2.buscar_por_huella(
        huella_desde_bytes(producto_a_bytes(CATALOGO[0], "perfecto")), top_k=1
    )
    assert res[0].producto.co_art == CATALOGO[0].co_art


def test_huella_determinista(tmp_path):
    """La misma imagen produce exactamente la misma huella (reproducibilidad)."""
    imagen = producto_a_bytes(CATALOGO[0], "perfecto")
    h1 = huella_desde_bytes(imagen)
    h2 = huella_desde_bytes(imagen)
    import numpy as np

    assert np.array_equal(h1, h2)
    assert h1.shape == (1060,)


def test_motor_sin_catalogo_responde_vacio():
    """Búsqueda con índice vacío -> resultado sin coincidencia (sin excepción)."""
    motor = MotorBusquedaVisual(InMemoryVectorAdapter(), peso_visual=PESO_VISUAL)
    res = motor.buscar(producto_a_bytes(CATALOGO[0], "perfecto"))
    assert res.codigo_obtenido is None or res.codigo_obtenido == ""


# ---------------------------------------------------------------------------
# Router Flask: enganche síncrono del motor en memoria (POST /api/visor/buscar)
# ---------------------------------------------------------------------------


def _mock_nim(monkeypatch, visor_routes):
    """Mockea `procesar_imagen_visor` en la ruta: NIM sin match (low_confidence).

    El flujo real (NVIDIA NIM + búsqueda jerárquica en stock_maestro) se
    valida en probes/E2E; aquí solo se ejercita el contrato del router con su
    respaldo en memoria.
    """
    monkeypatch.setattr(
        visor_routes,
        "procesar_imagen_visor",
        lambda img: {
            "status": "low_confidence",
            "ocr_texto": "",
            "datos_vision": {},
            "productos_encontrados": [],
            "mensaje": "mock-nim-sin-match",
        },
    )


def test_router_nim_prioritario(tmp_path, monkeypatch):
    """Cuando NVIDIA NIM identifica el producto, la ruta responde de inmediato."""
    import io
    import sqlite3

    from flask import Flask

    from visor_articulos.adapters.inmemory_vector_adapter import InMemoryVectorAdapter
    from visor_articulos.application import visor_routes

    monkeypatch.setattr(
        visor_routes,
        "procesar_imagen_visor",
        lambda img: {
            "status": "success",
            "motor": "ara_vision_nim",
            "ocr_texto": '{"marca": "X"}',
            "datos_vision": {"marca": "X"},
            "productos_encontrados": [
                {
                    "codigo": "NIM001",
                    "descripcion": "PRODUCTO NIM 500 MG",
                    "score_busqueda": 1.0,
                    "stock_maestro": 7,
                    "campo7": "1MD01-P1",
                    "historial_ubicaciones": [],
                    "ubicacion_pendiente": None,
                    "imagen_url": "",
                }
            ],
            "total_coincidencias": 1,
            "mensaje": "Producto: PRODUCTO NIM 500 MG",
        },
    )
    visor_routes.DB_PATH = str(tmp_path / "visor_test_nim.db")  # no debe usarse

    motor = MotorBusquedaVisual(InMemoryVectorAdapter())
    motor._estado_ingesta = {"origen": "test", "productos_indexados": 0}
    app = Flask(__name__)
    visor_routes.register_visor_routes(app, motor=motor)
    r = app.test_client().post(
        "/api/visor/buscar",
        data={"image": (io.BytesIO(b"\xff\xd8\xff"), "nim.jpg")},
        content_type="multipart/form-data",
    )
    data = r.get_json()
    assert r.status_code == 200
    assert data["motor"] == "ara_vision_nim"
    assert data["productos_encontrados"][0]["codigo"] == "NIM001"
    assert data["productos_encontrados"][0]["score_busqueda"] == 1.0
    assert data["latencia_nim_ms"] >= 0.0


@pytest.fixture()
def app_router(tmp_path, monkeypatch):
    """App Flask con el router real y un motor sintético aislado (BD temporal).

    NVIDIA NIM se mockea en el seam de la ruta (responde low_confidence):
    la ruta cae al motor híbrido en memoria, que es lo que ejercitan los
    tests de contrato del router.
    """
    import sqlite3

    from flask import Flask

    from visor_articulos.application import visor_routes

    _mock_nim(monkeypatch, visor_routes)

    db_tmp = str(tmp_path / "visor_test.db")
    conn = sqlite3.connect(db_tmp)
    conn.executescript(
        """
        CREATE TABLE stock_maestro (
            codigo TEXT, descripcion TEXT, stock_maestro INTEGER,
            stock_bulto_cerrado INTEGER, campo7 TEXT, codigo_barra TEXT,
            stock_act INTEGER, despacho_bqto INTEGER, deposito_bqto INTEGER,
            imagen_url TEXT
        );
        CREATE TABLE reportes_ubicacion (
            id INTEGER PRIMARY KEY, co_art TEXT, usuario TEXT, desde TEXT,
            hacia TEXT, fecha TEXT, procesado_profit INTEGER
        );
        INSERT INTO stock_maestro VALUES
            ('NA001', 'ACETAMINOFEN 500 MG', 10, 2, '1MD01-P1', '750000000000NA001', 8, 1, 1, ''),
            ('NA002', 'PARACETAMOL 500 MG', 5, 0, '1MD02-P2', '750000000000NA002', 5, 0, 0, '');
        INSERT INTO reportes_ubicacion VALUES
            (1, 'NA001', 'operador1', '1MD01-P0', '1MD01-P1', '2026-08-01 10:00', 1),
            (2, 'NA001', 'operador2', '1MD01-P1', '1MD02-P2', '2026-08-02 11:00', 0);
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(visor_routes, "DB_PATH", db_tmp)
    visor_routes._FICHAS_CACHE.clear()

    indice = InMemoryVectorAdapter(cache_dir=str(tmp_path / "cache"))
    for p in CATALOGO:
        indice.registrar_producto(p, producto_a_bytes(p, "perfecto"))
    motor = MotorBusquedaVisual(indice, peso_visual=PESO_VISUAL)
    motor._estado_ingesta = {"origen": "test", "productos_indexados": indice.cantidad_productos}

    app = Flask(__name__)
    visor_routes.register_visor_routes(app, motor=motor)
    return app.test_client()


def test_router_registra_endpoints(app_router):
    r = app_router.get("/api/visor/estado")
    assert r.status_code == 200
    assert r.get_json()["productos_indexados"] == 44


def test_router_buscar_contrato_completo(app_router):
    import io

    imagen = producto_a_bytes(CATALOGO[0], "perfecto")
    r = app_router.post(
        "/api/visor/buscar",
        data={"image": (io.BytesIO(imagen), "captura.jpg")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "success"
    assert data["motor"] == "visor_hibrido"
    assert data["total_coincidencias"] >= 1
    ficha = data["productos_encontrados"][0]
    assert ficha["codigo"] == CATALOGO[0].co_art
    assert "stock_maestro" in ficha and "campo7" in ficha and "imagen_url" in ficha
    assert "historial_ubicaciones" in ficha and "ubicacion_pendiente" in ficha
    assert len(data["top_candidatos"]) == 5
    assert data["latencia_total_ms"] < 10.0


def test_router_buscar_por_base64(app_router):
    import base64

    b64 = base64.b64encode(producto_a_bytes(CATALOGO[1], "perfecto")).decode()
    r = app_router.post("/api/visor/buscar", json={"image": b64})
    assert r.status_code == 200
    data = r.get_json()
    assert data["productos_encontrados"][0]["codigo"] == CATALOGO[1].co_art


def test_router_error_sin_imagen(app_router):
    r = app_router.post("/api/visor/buscar", json={})
    assert r.status_code == 400
    assert r.get_json()["status"] == "error"


def test_router_enriquece_stock_y_pendiente(tmp_path, monkeypatch):
    """Con códigos que SÍ existen en la BD temporal, la ficha trae stock e historial."""
    import io
    import sqlite3

    from flask import Flask

    from visor_articulos.adapters.inmemory_vector_adapter import InMemoryVectorAdapter
    from visor_articulos.application import visor_routes

    _mock_nim(monkeypatch, visor_routes)

    db_tmp = str(tmp_path / "visor_test2.db")
    conn = sqlite3.connect(db_tmp)
    conn.executescript(
        """
        CREATE TABLE stock_maestro (
            codigo TEXT, descripcion TEXT, stock_maestro INTEGER,
            stock_bulto_cerrado INTEGER, campo7 TEXT, codigo_barra TEXT,
            stock_act INTEGER, despacho_bqto INTEGER, deposito_bqto INTEGER,
            imagen_url TEXT
        );
        CREATE TABLE reportes_ubicacion (
            id INTEGER PRIMARY KEY, co_art TEXT, usuario TEXT, desde TEXT,
            hacia TEXT, fecha TEXT, procesado_profit INTEGER
        );
        INSERT INTO stock_maestro VALUES
            ('NA001', 'ACETAMINOFEN 500 MG', 10, 2, '1MD01-P1', '750000000000NA001', 8, 1, 1, '');
        INSERT INTO reportes_ubicacion VALUES
            (1, 'NA001', 'operador1', '1MD01-P0', '1MD01-P1', '2026-08-01 10:00', 0);
        """
    )
    conn.commit()
    conn.close()
    visor_routes.DB_PATH = db_tmp
    visor_routes._FICHAS_CACHE.clear()

    indice = InMemoryVectorAdapter(cache_dir=str(tmp_path / "cache2"))
    p1 = next(p for p in CATALOGO if p.co_art == "NA001")
    p1.descripcion = "ACETAMINOFEN 500 MG"
    p1.ubicacion = "1MD01-P1"
    indice.registrar_producto(p1, producto_a_bytes(p1, "perfecto"))
    motor = MotorBusquedaVisual(indice, peso_visual=PESO_VISUAL)
    motor._estado_ingesta = {"origen": "test", "productos_indexados": 1}

    app = Flask(__name__)
    visor_routes.register_visor_routes(app, motor=motor)
    client = app.test_client()
    r = client.post(
        "/api/visor/buscar",
        data={"image": (io.BytesIO(producto_a_bytes(p1, "perfecto")), "c.jpg")},
        content_type="multipart/form-data",
    )
    ficha = r.get_json()["productos_encontrados"][0]
    assert ficha["stock_maestro"] == 10
    assert ficha["stock_bqto"] == 2
    assert ficha["campo7"] == "1MD01-P1"
    assert ficha["historial_ubicaciones"] == [
        {"usuario": "operador1", "desde": "1MD01-P0", "hacia": "1MD01-P1", "fecha": "2026-08-01 10:00"}
    ]
    assert ficha["ubicacion_pendiente"] == "1MD01-P1"


def test_router_sin_catalogo_responde_503(monkeypatch):
    """Catálogo vacío (origen 'vacio') -> 503 controlado sin excepción."""
    from flask import Flask

    from visor_articulos.application import visor_routes

    _mock_nim(monkeypatch, visor_routes)

    motor = MotorBusquedaVisual(InMemoryVectorAdapter())
    motor._estado_ingesta = {"origen": "vacio", "productos_indexados": 0}
    app = Flask(__name__)
    visor_routes.register_visor_routes(app, motor=motor)
    r = app.test_client().post("/api/visor/buscar", json={"image": "AAAA"})
    assert r.status_code == 503
    assert r.get_json()["motor"] == "visor_hibrido"


# ---------------------------------------------------------------------------
# Recalibración anti falsos positivos por laboratorio (ara_vision.py)
# Caso real: MD00001 (ACETAMINOFEN 500MG, BIOVENEZUELA) vs MD00826
# (ACIDO FOLICO 5MG, BIOVENEZUELA). Solo comparten laboratorio: deben
# quedar por debajo de UMBRAL_DESCARTE_COTEJO y devolver 0.0.
# ---------------------------------------------------------------------------


@pytest.fixture()
def vision_acido_folico():
    return {
        "codigo_barra": "8904324100922",
        "marca": "Acido Folico",
        "principio_activo": "ACIDO FOLICO",
        "concentracion": "5MG",
        "forma_farmaceutica": "TABLETA",
        "laboratorio": "BIOVENEZUELA",
    }


def test_cotejo_no_confunde_md00001_con_md00826(vision_acido_folico):
    """Vision de ACIDO FOLICO 5MG NO debe aprobar ACETAMINOFEN 500MG:
    coincidencia única en laboratorio (BIOVENEZUELA) => penalización x0.20
    y score 0.0 (por debajo del umbral de descarte)."""
    from ara.ARA_Brain.ara_vision import _score_cotejo_jerarquico

    acetaminofen = (
        "ACETAMINOFEN 500MG (ACETABIOFEN) BLIST X 10 TAB "
        "(BIOVENEZUELA) F.V 07/2028"
    )
    score = _score_cotejo_jerarquico(
        vision_acido_folico, acetaminofen, laboratorio_candidato="BIOVENEZUELA"
    )
    assert score == 0.0


def test_cotejo_correspondencia_principio_concentracion(vision_acido_folico):
    """La misma visión SÍ debe aprobar su propio registro (MD00826):
    principio_activo + concentracion => banda P3 >= 0.65."""
    from ara.ARA_Brain.ara_vision import _score_cotejo_jerarquico

    acido_folico = (
        "ACIDO FOLICO 5MG (ACIDOFOLIN) BLIST X 30 TAB "
        "(BIOVENEZUELA) F.V 07/2028"
    )
    score = _score_cotejo_jerarquico(
        vision_acido_folico, acido_folico, laboratorio_candidato="BIOVENEZUELA"
    )
    assert score >= 0.65


def test_cotejo_penaliza_solo_laboratorio_con_campos_llenos():
    """Regla de seguridad: marca+PA+concentracion llenos y solo coincide el
    laboratorio => score 0.0 (nunca un falso positivo por laboratorio)."""
    from ara.ARA_Brain.ara_vision import _score_cotejo_jerarquico

    vision = {
        "marca": "IBUPROFENO",
        "principio_activo": "IBUPROFENO",
        "concentracion": "400MG",
        "laboratorio": "BIOVENEZUELA",
    }
    otra = "DICLOFENACO 75MG (ANTIFLOGISTICO) (BIOVENEZUELA)"
    score = _score_cotejo_jerarquico(vision, otra, laboratorio_candidato="BIOVENEZUELA")
    assert score == 0.0


def test_busqueda_sql_vision_descarta_md00826_para_md00001(vision_acido_folico):
    """Integración con la BD real: la búsqueda SQL de ACIDO FOLICO 5MG no
    devuelve el ACETAMINOFEN 500MG (BIOVENEZUELA) como match."""
    import sqlite3

    from ara.ARA_Brain.ara_vision import DB_PATH, _buscar_producto_sql_vision

    assert os.path.isfile(DB_PATH)
    resultados = _buscar_producto_sql_vision(vision_acido_folico)
    codigos = [r["codigo"] for r in resultados]
    if codigos:
        assert "MD00001" not in codigos
    for r in resultados:
        assert r["score_busqueda"] >= 0.65

# -*- coding: utf-8 -*-
"""Servicio TTS Kokoro 82M (kokoro-onnx) para el Módulo de Preparación/Picking.

- Carga perezosa del motor onnx (latencia de arranque fuera del flujo crítico).
- Instalación automática de `kokoro-onnx` si falta (subprocess pip).
- Descarga automática del modelo 82M (`kokoro-v1.0.onnx`) y voces
  (`voices-v1.0.bin`, es: `ef_dora` / `em_alex`) desde HuggingFace.
- Caché de audio en memoria + disco (WAV) por hash de (texto, voz) para
  frases recurrentes: no se regeneran audios repetidos (latencia < 100 ms
  para frases ya cacheadas).
- Registro de rutas Flask: POST /api/tts/kokoro (WAV) y GET /api/tts/estado.

Si el motor no está disponible (sin instalación/red/modelo), las rutas
responden 503 con JSON y el frontend cae a SpeechSynthesis del navegador.
"""
import hashlib
import io
import os
import subprocess
import sys
import threading
import time
import traceback
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import jsonify, request, send_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELOS_DIR = os.path.join(BASE_DIR, "models")
CACHE_DIR = os.path.join(BASE_DIR, "cache_tts")
os.makedirs(MODELOS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# --- Modelo Kokoro 82M (multilingüe; voces en español: ef_dora / em_alex) ---
# Repo oficial de kokoro-onnx (GitHub Releases), verificado accesible.
MODELO_URL = os.environ.get(
    "KOKORO_MODELO_URL",
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/kokoro-v1.0.onnx",
)
VOCES_URL = os.environ.get(
    "KOKORO_VOCES_URL",
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/voices-v1.0.bin",
)
MODELO_PATH = os.path.join(MODELOS_DIR, "kokoro-v1.0.onnx")
VOCES_PATH = os.path.join(MODELOS_DIR, "voices-v1.0.bin")

# Voces en español disponibles en voices-v1.0.bin (kokoro-onnx)
VOCES_ES = ("ef_dora", "em_alex")
VOZ_DEFECTO = os.environ.get("KOKORO_VOZ", "ef_dora")

# Frases de estado automatizadas del picking (PASO 2 del dominio)
ESTADO_AUDIO_ANADIDO = "Artículo añadido"
ESTADO_AUDIO_COMPLETADO = "Artículo completado"
ESTADO_AUDIO_MAXIMO = "Máximo de unidades alcanzado para este artículo"
ESTADO_AUDIO_ERROR_NOTA = "El artículo no corresponde a esta nota"

_LOCK = threading.Lock()
_ENGINE = None  # instancia perezosa de kokoro_onnx.Kokoro
_ENGINE_DISPOSITIVO = False
_ESTADO_INSTALACION = {"instalado": False, "modelo": False, "error": None}

# Ejecución asíncrona NO bloqueante de la síntesis (CPU-bound onnx). La
# generación corre en hilo aparte: la ruta responde 503 con timeout estricto
# de 800 ms si la CPU está saturada, y el frontend conmuta a voz local.
_TTS_EXECUTOR = ThreadPoolExecutor(max_workers=2)
_TTS_TIMEOUT_S = float(os.environ.get("KOKORO_TIMEOUT_S", "0.8"))


# ---------------------------------------------------------------------------
# Instalación / descarga automática
# ---------------------------------------------------------------------------

def _pip_install_kokoro():
    """Instala kokoro-onnx + onnxruntime si no están disponibles."""
    try:
        import kokoro_onnx  # noqa: F401
        _ESTADO_INSTALACION["instalado"] = True
        return True
    except ImportError:
        pass
    print("[KOKORO_TTS] Instalando kokoro-onnx (auto)...", flush=True)
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "kokoro-onnx"],
            timeout=600,
        )
        _ESTADO_INSTALACION["instalado"] = True
        return True
    except Exception as e:
        _ESTADO_INSTALACION["error"] = f"pip install kokoro-onnx falló: {e}"
        print(f"[KOKORO_TTS] {_ESTADO_INSTALACION['error']}", flush=True)
        return False


def _descargar(url: str, destino: str) -> bool:
    """Descarga un archivo (streaming) si no existe o está incompleto."""
    if os.path.exists(destino) and os.path.getsize(destino) > 1_000_000:
        return True
    print(f"[KOKORO_TTS] Descargando {os.path.basename(destino)} desde el repositorio...", flush=True)
    try:
        with requests.get(url, stream=True, timeout=(20, 1800)) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            descargado = 0
            with open(destino, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
                    descargado += len(chunk)
                    if total:
                        pct = int(descargado * 100 / max(total, 1))
                        if pct % 25 == 0:
                            print(
                                f"[KOKORO_TTS] {os.path.basename(destino)}: {pct}% "
                                f"({descargado // (1 << 20)}MB)",
                                flush=True,
                            )
        _ESTADO_INSTALACION["modelo"] = True
        return True
    except Exception as e:
        _ESTADO_INSTALACION["error"] = f"Descarga {os.path.basename(destino)} falló: {e}"
        print(f"[KOKORO_TTS] {_ESTADO_INSTALACION['error']}", flush=True)
        return False


def asegurar_modelos() -> bool:
    """Asegura pip + modelo + voces. Idempotente; no lanza excepciones."""
    if not _pip_install_kokoro():
        return False
    if not (_descargar(MODELO_URL, MODELO_PATH) and _descargar(VOCES_URL, VOCES_PATH)):
        return False
    return True


# ---------------------------------------------------------------------------
# Motor perezoso + síntesis con caché
# ---------------------------------------------------------------------------

def _obtener_engine():
    """Crea (una sola vez) el motor Kokoro onnx. Retorna None si no puede."""
    global _ENGINE, _ENGINE_DISPOSITIVO
    if _ENGINE is not None:
        return _ENGINE
    with _LOCK:
        if _ENGINE is not None:
            return _ENGINE
        if not (os.path.exists(MODELO_PATH) and os.path.exists(VOCES_PATH)):
            _ENGINE_DISPOSITIVO = False
            return None
        try:
            from kokoro_onnx import Kokoro

            _ESTADO_INSTALACION["instalado"] = True
            t0 = time.perf_counter()
            _ENGINE = Kokoro(MODELO_PATH, VOCES_PATH)
            _ENGINE_DISPOSITIVO = True
            print(
                f"[KOKORO_TTS] Motor 82M cargado en "
                f"{(time.perf_counter() - t0) * 1000:.0f} ms",
                flush=True,
            )
        except Exception as e:
            _ENGINE_DISPOSITIVO = False
            _ESTADO_INSTALACION["error"] = f"Kokoro no pudo inicializar: {e}"
            print(f"[KOKORO_TTS] {_ESTADO_INSTALACION['error']}", flush=True)
            traceback.print_exc()
        return _ENGINE


def sintetizar(texto: str, voz: str = VOZ_DEFECTO) -> bytes | None:
    """Sintetiza texto a WAV (caché en memoria + disco). None si no disponible.

    Latencia objetivo < 100 ms para frases repetidas (caché por hash).
    """
    texto = (texto or "").strip()
    if not texto:
        return None
    if voz not in VOCES_ES:
        voz = VOZ_DEFECTO

    clave = hashlib.sha1(f"{voz}|{texto}".encode("utf-8")).hexdigest()
    ruta_cache = os.path.join(CACHE_DIR, f"{clave}.wav")

    # 1. Caché en disco (persistente entre procesos)
    if os.path.exists(ruta_cache):
        with open(ruta_cache, "rb") as f:
            return f.read()

    engine = _obtener_engine()
    if engine is None:
        return None

    # 2. Síntesis + caché en memoria
    if hasattr(engine, "_cache_mem"):
        audio = engine._cache_mem.get(clave)
        if audio is not None:
            return audio
    try:
        muestras, sr = engine.create(texto, voice=voz, speed=1.0)
        import numpy as np
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(np.asarray(muestras * 32767, dtype=np.int16).tobytes())
        audio = buf.getvalue()
    except Exception as e:
        _ESTADO_INSTALACION["error"] = f"Síntesis falló: {e}"
        print(f"[KOKORO_TTS] {_ESTADO_INSTALACION['error']}", flush=True)
        traceback.print_exc()
        return None

    with _LOCK:
        if not hasattr(engine, "_cache_mem"):
            engine._cache_mem = {}
        engine._cache_mem[clave] = audio
    with open(ruta_cache, "wb") as f:
        f.write(audio)
    return audio


def estado() -> dict:
    """Estado operativo del servicio TTS."""
    return {
        "disponible": _ENGINE_DISPOSITIVO,
        "motor": "kokoro-82m" if _ENGINE_DISPOSITIVO else None,
        "instalado": _ESTADO_INSTALACION["instalado"],
        "modelo_descargado": os.path.exists(MODELO_PATH) and os.path.exists(VOCES_PATH),
        "voz_default": VOZ_DEFECTO,
        "voces_es": list(VOCES_ES),
        "error": _ESTADO_INSTALACION["error"],
        "cache_en_disco": len(os.listdir(CACHE_DIR)),
    }


# ---------------------------------------------------------------------------
# Rutas Flask
# ---------------------------------------------------------------------------

def register_tts_routes(app):
    """Registra los endpoints del servicio TTS Kokoro sobre la app Flask."""

    @app.route("/api/tts/estado", methods=["GET"])
    def tts_estado():
        return jsonify({"status": "ok", **estado()})

    @app.route("/api/tts/kokoro", methods=["POST"])
    def tts_kokoro():
        """POST {texto, voz} -> audio/wav (WAV) o 503 con JSON si no disponible.

        Patrón asíncrono NO bloqueante: la síntesis corre en hilo aparte con
        timeout estricto de 800 ms. Si el motor tarda (CPU saturada durante
        el picking) responde 503 de inmediato — la interfaz nunca espera ni
        congela la tarjeta — y la síntesis continúa en segundo plano para
        calentar la caché de la siguiente vez.
        """
        data = request.get_json(silent=True) or {}
        texto = str(data.get("texto") or "").strip()
        voz = str(data.get("voz") or VOZ_DEFECTO).strip()
        if not texto:
            return (
                jsonify(
                    {
                        "status": "error",
                        "success": False,
                        "mensaje": "Falta el campo 'texto'.",
                    }
                ),
                400,
            )
        t0 = time.perf_counter()
        try:
            future = _TTS_EXECUTOR.submit(sintetizar, texto, voz)
            audio = future.result(timeout=_TTS_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            print(
                f"[KOKORO_TTS] ⏱️ Timeout {_TTS_TIMEOUT_S * 1000:.0f} ms en "
                f"'{texto[:40]}' — 503 (el hilo sigue en segundo plano, "
                f"cache en caliente).",
                flush=True,
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "success": False,
                        "timeout": True,
                        "mensaje": (
                            "Kokoro TTS tardó más de "
                            f"{_TTS_TIMEOUT_S * 1000:.0f} ms (CPU ocupada). "
                            "El navegador usará voz local."
                        ),
                    }
                ),
                503,
            )
        if audio is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "success": False,
                        "mensaje": (
                            "Motor TTS Kokoro no disponible (instale kokoro-onnx y "
                            "descargue los modelos). El navegador usará voz local."
                        ),
                        "tts": estado(),
                    }
                ),
                503,
            )
        print(
            f"[KOKORO_TTS] TTS '{texto[:40]}' ({voz}) en "
            f"{(time.perf_counter() - t0) * 1000:.1f} ms - {len(audio)} bytes",
            flush=True,
        )
        return send_file(
            io.BytesIO(audio),
            mimetype="audio/wav",
            as_attachment=False,
            download_name="kokoro.wav",
        )

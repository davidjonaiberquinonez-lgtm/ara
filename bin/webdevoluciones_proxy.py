# -*- coding: utf-8 -*-
"""Relay de WebDevoluciones: corre en TU PC (que sí tiene salida a
webdevoluciones.cristmedicals.com) y reenvía las peticiones que le
mande el servidor de Agente IA (que esa web le filtra el IP). Agente IA
le pega a este relay por la red local; el relay hace la llamada real a
internet con la API key y devuelve la respuesta tal cual.

La API key SOLO vive acá (variable de entorno en TU pc, nunca en el
servidor de Agente IA ni en el código).

Para arrancarlo:
  1. Instalar dependencias (una sola vez):  pip install flask requests
  2. Configurar la clave (una sola vez, PowerShell):
       [Environment]::SetEnvironmentVariable("WEBDEVOLUCIONES_API_KEY", "TU_CLAVE_AQUI", "User")
     (cerrar y abrir la terminal para que la tome, o usar una consola nueva)
  3. Correrlo:  python webdevoluciones_proxy.py
     Se queda escuchando en el puerto 5057 de TODAS las interfaces de
     red (0.0.0.0), así que el servidor de Agente IA (en la misma red
     local) le puede pegar por tu IP de LAN.
"""
import json
import os
import subprocess
import urllib.parse

from flask import Flask, jsonify, request

API_KEY = os.environ.get("WEBDEVOLUCIONES_API_KEY", "").strip()
BASE = "https://webdevoluciones.cristmedicals.com/api/external/v1"
PUERTO = 5057

# Solo estos 3 nombres se pueden pedir -- nada de pasar cualquier ruta
# arbitraria a internet con la clave puesta.
RUTAS_PERMITIDAS = {"stats", "novedades", "reposiciones"}

app = Flask(__name__)


@app.route("/<nombre>", methods=["GET"])
def reenviar(nombre):
    if nombre not in RUTAS_PERMITIDAS:
        return jsonify({"error": f"Ruta no permitida: {nombre}"}), 404
    if not API_KEY:
        return jsonify({"error": "Falta WEBDEVOLUCIONES_API_KEY en el entorno de este PC."}), 500

    qs = urllib.parse.urlencode(request.args)
    url = f"{BASE}/{nombre}" + (f"?{qs}" if qs else "")

    # BUG real detectado en vivo (02/10), dos capas:
    #  1) el script original mandaba "Authorization: Bearer ..." - el
    #     servidor pide el header "X-Api-Key" (texto exacto del error).
    #  2) incluso ya con el header bien, la libreria "requests" de Python
    #     seguia recibiendo 403 (pagina HTML generica de un WAF tipo
    #     Imunify360) mientras que "curl" contra la MISMA URL con el MISMO
    #     header daba 200 - es bloqueo por huella TLS del cliente, no por
    #     el header. Se usa el curl.exe del sistema (via subprocess) en vez
    #     de "requests", que es el unico camino confirmado que pasa.
    try:
        resultado = subprocess.run(
            [
                "curl.exe", "-s", "-w", "\n__HTTP_STATUS__:%{http_code}",
                url, "-H", f"X-Api-Key: {API_KEY}", "--max-time", "20",
            ],
            capture_output=True, text=True, timeout=25,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout contactando webdevoluciones (curl > 25s)."}), 502

    salida = resultado.stdout
    marcador = "\n__HTTP_STATUS__:"
    if marcador not in salida:
        return jsonify({"error": f"curl falló: {resultado.stderr[:300]}"}), 502
    cuerpo, status_txt = salida.rsplit(marcador, 1)
    try:
        status = int(status_txt.strip())
    except ValueError:
        status = 502

    try:
        return jsonify(json.loads(cuerpo)), status
    except ValueError:
        # No vino JSON (ej. pagina de error HTML de un WAF) -- se devuelve
        # tal cual para no esconder el problema, pero como texto plano.
        return (cuerpo, status, {"Content-Type": "text/plain; charset=utf-8"})


@app.route("/salud", methods=["GET"])
def salud():
    return jsonify({"ok": True, "api_key_configurada": bool(API_KEY)})


if __name__ == "__main__":
    import socket
    try:
        ip_lan = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip_lan = "(no se pudo detectar, revisa ipconfig)"
    print(f"[webdevoluciones_proxy] escuchando en el puerto {PUERTO}")
    print(f"[webdevoluciones_proxy] IP LAN detectada: {ip_lan}")
    print(f"[webdevoluciones_proxy] API key configurada: {'sí' if API_KEY else 'NO -- faltó configurarla'}")
    print(f"[webdevoluciones_proxy] probar: http://{ip_lan}:{PUERTO}/salud")
    app.run(host="0.0.0.0", port=PUERTO)

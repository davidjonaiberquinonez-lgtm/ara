#!/usr/bin/env python3
"""
reparar_entorno.py - Automatización completa para limpiar y configurar el entorno ARA
Ejecuta: python reparar_entorno.py
"""

import os
import re
import sys
import subprocess
import shutil
from pathlib import Path
from typing import List, Tuple, Optional


# ─── Configuración ──────────────────────────────────────────────────
BASE_DIR = Path(r"C:\ARA_PROYECT")
SERVER_FILE = BASE_DIR / "ara" / "ARA_Brain" / "ara_server.py"
PDF_ROUTE_FILE = BASE_DIR / "pdf_route.py"
PUERTO = 5000

# ─── Utilidades ─────────────────────────────────────────────────────
def run_cmd(cmd: List[str], check: bool = False) -> Tuple[int, str, str]:
    """Ejecuta comando y retorna (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, "", str(e)


def log_ok(msg: str): print(f"✅ {msg}")
def log_warn(msg: str): print(f"⚠️  {msg}")
def log_err(msg: str): print(f"❌ {msg}")
def log_info(msg: str): print(f"ℹ️  {msg}")


# ─── 1. Matar procesos en puerto 5000 ───────────────────────────────
def matar_procesos_puerto(puerto: int = PUERTO) -> bool:
    log_info(f"Buscando procesos en puerto {puerto}...")
    rc, out, err = run_cmd(["netstat", "-ano"])
    if rc != 0:
        log_err(f"netstat falló: {err}")
        return False

    pids = set()
    for line in out.splitlines():
        if f":{puerto}" in line and "LISTENING" in line:
            parts = line.split()
            if parts:
                pid = parts[-1]
                if pid.isdigit():
                    pids.add(int(pid))

    if not pids:
        log_ok(f"No hay procesos escuchando en puerto {puerto}")
        return True

    for pid in pids:
        log_info(f"Matando PID {pid}...")
        rc, _, err = run_cmd(["taskkill", "/PID", str(pid), "/F"])
        if rc == 0:
            log_ok(f"PID {pid} terminado")
        else:
            log_warn(f"No se pudo matar PID {pid}: {err.strip()}")

    # Verificación final
    rc, out, _ = run_cmd(["netstat", "-ano"])
    for line in out.splitlines():
        if f":{puerto}" in line and "LISTENING" in line:
            log_err(f"Puerto {puerto} sigue ocupado tras taskkill")
            return False

    log_ok(f"Puerto {puerto} liberado")
    return True


# ─── 2. Eliminar ruta POST antigua ──────────────────────────────────
def eliminar_ruta_post_antigua(filepath: Path) -> bool:
    log_info("Eliminando ruta POST antigua /api/reporte/pdf...")

    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as e:
        log_err(f"Leyendo {filepath}: {e}")
        return False

    # Patrón: desde la línea con @app.route('/api/reporte/pdf', methods=['POST'])
    # hasta la siguiente definición de función @app.route o hasta el final del bloque
    pattern = re.compile(
        r"(?s)"  # DOTALL
        r"(#\s*=+\s*\n#\s*NUEVO ENDPOINT: REPORTE PDF DE GAMIFICACIÓN\s*\n#\s*=+\s*\n)?"
        r"@app\.route\(['\"]/api/reporte/pdf['\"],\s*methods=\[['\"]POST['\"]\]\)\s*\n"
        r"def generar_reporte_pdf\(\):.*?"
        r"(?=\n@app\.route|\nif __name__ ==|$)",
        re.MULTILINE
    )

    new_content, count = pattern.subn("", content)

    if count == 0:
        log_warn("No se encontró bloque POST antiguo (¿ya eliminado?)")
        return True

    # Limpiar líneas en blanco excesivas
    new_content = re.sub(r"\n{3,}", "\n\n", new_content)

    try:
        backup = filepath.with_suffix(".py.backup")
        shutil.copy2(filepath, backup)
        filepath.write_text(new_content, encoding="utf-8")
        log_ok(f"Ruta POST eliminada ({count} ocurrencia(s)). Backup en {backup.name}")
        return True
    except Exception as e:
        log_err(f"Escribiendo {filepath}: {e}")
        return False


# ─── 3. Inyectar registro pdf_route ─────────────────────────────────
def inyectar_registro_pdf_route(filepath: Path) -> bool:
    log_info("Inyectando registro de pdf_route...")

    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as e:
        log_err(f"Leyendo {filepath}: {e}")
        return False

    # Buscar línea CORS(app
    pattern = re.compile(r"(CORS\(app[^)]*\)\s*\n)")
    match = pattern.search(content)
    if not match:
        log_err("No se encontró línea CORS(app)")
        return False

    injection = (
        "from pdf_route import register_pdf_route\n"
        "register_pdf_route(app)\n"
    )

    new_content = content[:match.end()] + injection + content[match.end():]

    try:
        filepath.write_text(new_content, encoding="utf-8")
        log_ok("Registro pdf_route inyectado tras CORS(app)")
        return True
    except Exception as e:
        log_err(f"Escribiendo {filepath}: {e}")
        return False


# ─── 4. Inyectar mapa de rutas debug ────────────────────────────────
def inyectar_mapa_rutas(filepath: Path) -> bool:
    log_info("Inyectando bloque de depuración app.url_map...")

    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as e:
        log_err(f"Leyendo {filepath}: {e}")
        return False

    # Buscar la línea anterior a if __name__ == '__main__':
    pattern = re.compile(r"(\n)(?=if __name__ == ['\"]__main__['\"]:)")
    match = pattern.search(content)
    if not match:
        log_err("No se encontró bloque if __name__ == '__main__':")
        return False

    debug_block = (
        "\n# ── DEBUG: Mapa de rutas registrado ──────────────────────────────\n"
        "with app.app_context():\n"
        "    print(\"\\n\" + \"=\"*60)\n"
        "    print(\"🗺️  MAPA DE RUTAS FLASK ACTIVO\")\n"
        "    print(\"=\"*60)\n"
        "    for rule in sorted(app.url_map.iter_rules(), key=lambda r: str(r)):\n"
        "        methods = ','.join(sorted(m for m in rule.methods if m not in ('HEAD', 'OPTIONS')))\n"
        "        print(f\"  {methods:12s} {rule.rule}\")\n"
        "    print(\"=\"*60 + \"\\n\")\n"
        "# ────────────────────────────────────────────────────────────────\n"
    )

    new_content = content[:match.start()] + debug_block + content[match.start():]

    try:
        filepath.write_text(new_content, encoding="utf-8")
        log_ok("Bloque debug inyectado antes de if __name__")
        return True
    except Exception as e:
        log_err(f"Escribiendo {filepath}: {e}")
        return False


# ─── 5. Forzar modo debug (comentar Waitress, activar app.run) ──────
def forzar_modo_debug(filepath: Path) -> bool:
    log_info("Cambiando a modo debug (app.run)...")

    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as e:
        log_err(f"Leyendo {filepath}: {e}")
        return False

    # Comentar bloque Waitress y descomentar/append app.run
    # Buscar el bloque final
    waitress_pattern = re.compile(
        r"(#\s*Opción A: Si usas Waitress \(Producción limpia\)\s*\n)"
        r"(from waitress import serve\s*\n)"
        r"(serve\(app, host=HOST_BIND, port=PUERTO\)\s*\n)"
        r"(\s*#\s*Opción B: Si usas el server nativo de Flask \(Modo Desarrollo\)\s*\n)"
        r"(#\s*app\.run\(host=HOST_BIND, port=PUERTO, debug=False\)\s*)",
        re.MULTILINE
    )

    replacement = (
        "# Opción A: Si usas Waitress (Producción limpia) - COMENTADO PARA DEBUG\n"
        "# from waitress import serve\n"
        "# serve(app, host=HOST_BIND, port=PUERTO)\n\n"
        "# Opción B: Si usas el server nativo de Flask (Modo Desarrollo) - ACTIVO\n"
        "app.run(host=HOST_BIND, port=PUERTO, debug=True, use_reloader=True)\n"
    )

    new_content, count = waitress_pattern.subn(replacement, content)

    if count == 0:
        # Intento alternativo: buscar serve(app y reemplazar
        alt_pattern = re.compile(
            r"(from waitress import serve\s*\n)"
            r"(serve\(app[^)]*\))",
            re.MULTILINE
        )
        new_content, count = alt_pattern.subn(
            "# \\1# \\2\napp.run(host=HOST_BIND, port=PUERTO, debug=True, use_reloader=True)",
            content
        )

    if count == 0:
        log_warn("No se encontró bloque Waitress exacto; intentando reemplazo genérico...")
        # Último recurso: asegurar que app.run exista al final
        if "app.run(" not in content:
            new_content = content.rstrip() + "\n\napp.run(host='0.0.0.0', port=5000, debug=True, use_reloader=True)\n"
            count = 1

    try:
        filepath.write_text(new_content, encoding="utf-8")
        log_ok("Modo debug activado (app.run con debug=True, use_reloader=True)")
        return True
    except Exception as e:
        log_err(f"Escribiendo {filepath}: {e}")
        return False


# ─── Verificación final: mapa de rutas ──────────────────────────────
def verificar_mapa_rutas(filepath: Path) -> bool:
    log_info("Verificando que /api/reporte/pdf tenga método GET...")

    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as e:
        log_err(f"Leyendo {filepath}: {e}")
        return False

    # Buscar en el mapa de rutas inyectado si se ejecuta (no podemos ejecutar, chequeo estático)
    if "register_pdf_route(app)" not in content:
        log_err("register_pdf_route no encontrado en archivo")
        return False

    if "@app.route('/api/reporte/pdf', methods=['POST'])" in content:
        log_err("¡Ruta POST antigua AÚN PRESENTE!")
        return False

    log_ok("Verificación estática OK: POST eliminado, registro inyectado")
    return True


# ─── Main ───────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("🔧 REPARADOR ENTORNO ARA - Iniciando")
    print("=" * 60)

    steps = [
        ("Matar procesos puerto 5000", lambda: matar_procesos_puerto()),
        ("Eliminar ruta POST antigua", lambda: eliminar_ruta_post_antigua(SERVER_FILE)),
        ("Inyectar registro pdf_route", lambda: inyectar_registro_pdf_route(SERVER_FILE)),
        ("Inyectar mapa rutas debug", lambda: inyectar_mapa_rutas(SERVER_FILE)),
        ("Forzar modo debug", lambda: forzar_modo_debug(SERVER_FILE)),
        ("Verificación final", lambda: verificar_mapa_rutas(SERVER_FILE)),
    ]

    all_ok = True
    for name, fn in steps:
        print(f"\n▶ {name}...")
        try:
            ok = fn()
            if not ok:
                all_ok = False
                log_err(f"Fallo en: {name}")
        except Exception as e:
            all_ok = False
            log_err(f"Excepción en {name}: {e}")

    print("\n" + "=" * 60)
    if all_ok:
        print("🎉 TODAS LAS TAREAS COMPLETADAS CON ÉXITO")
        print("=" * 60)
        print("\n📋 PRÓXIMOS PASOS:")
        print("   1. Ejecuta: python ara/ARA_Brain/ara_server.py")
        print("   2. Verifica en consola el mapa de rutas (debe salir GET /api/reporte/pdf)")
        print("   3. Prueba en navegador: http://192.168.1.44:5000/api/reporte/pdf?fecha_inicio=2026-07-14&fecha_fin=2026-07-20")
    else:
        print("❌ ALGUNAS TAREAS FALLARON - Revisa logs arriba")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
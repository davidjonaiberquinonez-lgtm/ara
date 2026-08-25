# -*- coding: utf-8 -*-
"""Token de sesión interno de ARA Warehouse (firmado, HS256) — módulo
compartido entre ara_server.py y rutas/infrastructure/web/route_router.py.

BUG DE SEGURIDAD real detectado en vivo (24/08, auditoría del adaptador de
ARA Warehouse para DIP): varios endpoints de reportería (/api/reportes/
discrepancias, /api/reportes/trazabilidad, /api/rutas_hex/reporte-
finalizadas) recibían `es_admin` y `usuario_activo` como query params
mandados por el CLIENTE, sin verificar nada server-side — cualquiera podía
mandar `es_admin=true` y ver los reportes de todos los operadores sin haber
iniciado sesión como admin. `/api/login` tampoco emitía ningún token: el
frontend se limitaba a guardar la respuesta del login en localStorage y
reenviarla tal cual, sin que el servidor pudiera distinguir un dato real de
uno inventado por el propio navegador.

Este módulo agrega lo mínimo necesario para cerrar ese hueco: un token
firmado (mismo mecanismo — PyJWT/HS256 — que ya usa sso_erp_routes.py para
el SSO del CRM) que /api/login emite una vez con el `rol` REAL del usuario
tomado de la base de datos, y que los endpoints sensibles deben verificar
para derivar `es_admin`/`usuario_activo` — nunca confiando en lo que mande
el cliente en la query string.
"""
import os
import time

import jwt

SESION_SECRET = os.environ.get("ARA_SESSION_SECRET", "")
SESION_TTL_SEGUNDOS = 12 * 60 * 60  # 12 horas — un turno de trabajo largo.
SESION_ISSUER = "ara-warehouse"

# Clave de servicio para conectores server-a-server (ej. DIP) que necesitan
# leer /api/reportes/* de forma continua — un operador humano no tiene un
# token de 12h que le sirva para eso. Mismo patrón X-API-Key que ya usa el
# watchdog SQL (WATCHDOG_API_KEY).
DIP_SERVICE_KEY = os.environ.get("DIP_SERVICE_KEY", "")


def emitir_token_sesion(uid: str, nombre: str, rol: str, permisos: list | None = None) -> str:
    """Firma un token de sesión con el rol y permisos REALES leídos de la
    BD en el momento del login. El frontend lo guarda y lo reenvía en cada
    request sensible — el server nunca vuelve a confiar en un `es_admin`
    que venga del cliente.

    Se incluyen `permisos` (no solo `rol`) porque el criterio real de
    "es admin" en el frontend es `rol==='admin' OR rol==='supervisor' OR
    permisos.includes('*')` — un token que solo llevara `rol` degradaría
    silenciosamente a los supervisores/usuarios con permiso '*' que no
    tengan rol='admin' exacto."""
    if not SESION_SECRET:
        raise RuntimeError("ARA_SESSION_SECRET no está configurada en el entorno del server.")
    payload = {
        "id": uid,
        "nombre": nombre,
        "rol": rol,
        "permisos": permisos or [],
        "iss": SESION_ISSUER,
        "iat": int(time.time()),
        "exp": int(time.time()) + SESION_TTL_SEGUNDOS,
    }
    return jwt.encode(payload, SESION_SECRET, algorithm="HS256")


def verificar_token_sesion(request) -> dict | None:
    """Extrae y verifica el token de sesión de la request (header
    Authorization: Bearer <token>, o querystring ?token=<token> como
    respaldo para los fetch() GET existentes del frontend). Devuelve
    {id, nombre, rol} si es válido, None en cualquier otro caso — nunca
    lanza, para que el endpoint que lo llama decida cómo responder
    (típicamente 401)."""
    # Conector server-a-server (DIP): clave de servicio fija en vez de un
    # token de usuario con expiración de turno.
    if DIP_SERVICE_KEY and request.headers.get("X-API-Key", "") == DIP_SERVICE_KEY:
        return {"id": "dip_service", "nombre": "DIP", "rol": "admin", "permisos": ["*"], "es_admin": True}

    if not SESION_SECRET:
        return None

    token = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):].strip()
    if not token:
        token = (request.args.get("token") or "").strip()
    if not token:
        return None

    try:
        payload = jwt.decode(
            token, SESION_SECRET, algorithms=["HS256"], issuer=SESION_ISSUER
        )
        rol = payload.get("rol", "")
        permisos = payload.get("permisos", []) or []
        return {
            "id": payload.get("id", ""),
            "nombre": payload.get("nombre", ""),
            "rol": rol,
            "permisos": permisos,
            # Mismo criterio que ya usaba el frontend (rol admin/supervisor
            # o permiso comodín '*') — ahora verificado server-side contra
            # datos firmados, no contra lo que mande el cliente.
            "es_admin": rol in ("admin", "supervisor") or "*" in permisos,
        }
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

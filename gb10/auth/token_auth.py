"""Autenticación ligera para endpoints de la GDX — STAGED, no importado todavía.

2 modos, elegidos por env GDX_AUTH_MODE (default 'bearer', el más simple):
  - 'bearer': un solo token estático en el header Authorization: Bearer <token>.
    Cero dependencias nuevas, cero estado, ideal para LAN interna confiable.
  - 'jwt': token con expiración e claims (sub, exp), firmado HS256. Requiere
    PyJWT (agregado a requirements_gb10.txt, NO instalado todavía).

No reemplaza CustomerAuthenticator.php (autenticación de clientes por
teléfono/WhatsApp) — esto es para proteger los endpoints internos de la GDX
(inferencia, adaptadores) entre servicios, no para clientes finales.

Uso futuro (NO activar hoy):
    from token_auth import verificar_request
    if not verificar_request(request.headers.get('Authorization')):
        return {'error': 'no autorizado'}, 401
"""
import hmac
import os
import time


def _modo() -> str:
    return os.getenv("GDX_AUTH_MODE", "bearer").strip().lower()


def _verificar_bearer_estatico(header_valor: str) -> bool:
    if not header_valor or not header_valor.startswith("Bearer "):
        return False
    token_recibido = header_valor[len("Bearer "):].strip()
    token_esperado = os.getenv("GDX_AUTH_TOKEN", "")
    if not token_esperado:
        return False
    # comparación en tiempo constante — evita timing attack en la validación
    return hmac.compare_digest(token_recibido, token_esperado)


def _verificar_jwt(header_valor: str) -> bool:
    try:
        import jwt  # PyJWT — import local: solo se necesita si GDX_AUTH_MODE=jwt
    except ImportError:
        raise RuntimeError(
            "GDX_AUTH_MODE=jwt requiere PyJWT (pip install -r gb10/requirements_gb10.txt)"
        )

    if not header_valor or not header_valor.startswith("Bearer "):
        return False
    token = header_valor[len("Bearer "):].strip()
    secreto = os.getenv("GDX_JWT_SECRET", "")
    if not secreto:
        return False
    try:
        jwt.decode(token, secreto, algorithms=["HS256"])
        return True
    except jwt.PyJWTError:
        return False


def verificar_request(header_authorization: str) -> bool:
    modo = _modo()
    if modo == "jwt":
        return _verificar_jwt(header_authorization)
    return _verificar_bearer_estatico(header_authorization)


def generar_jwt(sub: str, ttl_s: int = 3600) -> str:
    """Solo para GDX_AUTH_MODE=jwt. Emite un token de servicio, no de usuario final."""
    import jwt  # PyJWT

    secreto = os.getenv("GDX_JWT_SECRET", "")
    if not secreto:
        raise RuntimeError("GDX_JWT_SECRET no configurado")
    payload = {"sub": sub, "iat": int(time.time()), "exp": int(time.time()) + ttl_s}
    return jwt.encode(payload, secreto, algorithm="HS256")

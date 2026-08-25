# -*- coding: utf-8 -*-
"""SSO del ERP CristMedicals hacia ARA-Intelligent (INTEGRACION_SSO.md,
provisto por el usuario, 2026-08-21).

Flujo (documentado por el ERP): el usuario hace clic en "ARA Intelligent"
desde el sidebar del ERP → el ERP genera un JWT HS256 firmado (60s de vida) →
redirige a GET /auth/sso?token=<JWT> → acá se verifica firma/exp/iss y se
crea/actualiza el perfil del usuario, DESACOPLADO por completo de la tabla
`usuarios` interna de ARA (esa es del módulo de almacén/rutas — el ERP trae
sus propios roles/permisos/metadata, que no tienen nada que ver con esos).

Sesión del lado del navegador: ARA-Intelligent guarda la sesión en
localStorage (`ara_sesion_usuario`), no en cookies de servidor — por eso el
intercambio es en dos pasos:
  1. GET /auth/sso?token=<JWT>  → verifica, guarda el perfil, genera un
     código de UN SOLO USO de corta vida y redirige a
     /ara-inteligente?sso=<codigo> (nunca se mete el JWT ni el perfil crudo
     en la URL final que puede quedar en el historial del navegador).
  2. GET /api/sso/consumir?codigo=<codigo> → el JS de ara_inteligente.html
     lo llama al cargar, recibe el perfil UNA vez (se borra al leerlo) y arma
     el localStorage igual que el login propio de ARA.
"""
import json
import os
import sqlite3
import time
import uuid

import jwt
from flask import jsonify, redirect, request

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ARA_DB_PATH", os.path.join(_BASE_DIR, "data", "proyecto_ara.db"))

ISSUER_ESPERADO = "cristmedicals-erp"
CODIGO_TTL_SEGUNDOS = 30  # ventana para que el navegador consuma el código de canje


def _conectar() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def _migrar() -> None:
    conn = _conectar()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sso_usuarios (
                employee_id TEXT PRIMARY KEY,
                sub TEXT,
                email TEXT,
                nombre TEXT,
                avatar_url TEXT,
                roles_json TEXT,
                permissions_json TEXT,
                metadata_json TEXT,
                actualizado_en DATETIME DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS sso_codigos_temp (
                codigo TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                creado_en REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sso_config (
                clave TEXT PRIMARY KEY,
                valor TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _limpiar_codigos_vencidos(conn: sqlite3.Connection) -> None:
    limite = time.time() - (CODIGO_TTL_SEGUNDOS * 4)  # margen generoso, solo para no acumular basura
    conn.execute("DELETE FROM sso_codigos_temp WHERE creado_en < ?", (limite,))


def register_sso_routes(app):
    _migrar()

    # BUG real reportado en vivo (24/08): Auth Central del ERP tiene el
    # callback de ARA registrado apuntando a /sso (sin el prefijo /auth/),
    # a diferencia de lo documentado en INTEGRACION_SSO.md (/auth/sso) —
    # mismo patrón que el otro desajuste ya visto (algunos registros
    # apuntaban a la raíz del dominio). En vez de depender de que alguien
    # corrija la config de Auth Central, se agrega este alias: mismo
    # comportamiento que la red de seguridad de "/" en ara_server.py, pero
    # para esta ruta puntual.
    @app.route("/sso", methods=["GET"])
    def sso_alias_sin_auth():
        token = request.args.get("token", "")
        if token:
            return redirect("/auth/sso?token=" + token)
        return redirect("/ara-inteligente?sso_error=sin_token")

    # BUG real reportado en vivo (24/08, segunda vuelta): Auth Central trata
    # https://ara.cristmedicals.com/ara-inteligente como la URL BASE de la
    # app y le concatena /auth/sso encima — construye
    # https://ara.cristmedicals.com/ara-inteligente/auth/sso, que acá no
    # existe (nuestras rutas viven en la raíz del dominio, no bajo
    # /ara-inteligente/). Mismo criterio que los otros alias: en vez de
    # depender de que corrijan Auth Central, se cubre la variante real que
    # están mandando.
    @app.route("/ara-inteligente/auth/sso", methods=["GET"])
    def sso_alias_bajo_ara_inteligente():
        token = request.args.get("token", "")
        if token:
            return redirect("/auth/sso?token=" + token)
        return redirect("/ara-inteligente?sso_error=sin_token")

    @app.route("/auth/sso", methods=["GET"])
    def sso_callback():
        token = request.args.get("token", "")
        if not token:
            return redirect("/ara-inteligente?sso_error=" + "sin_token")

        secret = os.environ.get("ERP_SSO_SECRET", "").strip()
        if not secret:
            print("[SSO] ERP_SSO_SECRET no configurada — no se puede verificar el token del ERP.")
            return redirect("/ara-inteligente?sso_error=" + "sso_no_configurado")

        try:
            payload = jwt.decode(
                token,
                key=secret,
                algorithms=["HS256"],
                issuer=ISSUER_ESPERADO,
            )
        except jwt.ExpiredSignatureError:
            return redirect("/ara-inteligente?sso_error=" + "expirado")
        except jwt.InvalidTokenError as e:
            print(f"[SSO] Token inválido: {e}")
            return redirect("/ara-inteligente?sso_error=" + "invalido")

        employee_id = str(payload.get("employee_id") or "").strip()
        if not employee_id:
            return redirect("/ara-inteligente?sso_error=" + "sin_employee_id")

        nombre = str(payload.get("full_name") or employee_id)
        email = str(payload.get("email") or "")
        avatar_url = payload.get("avatar_url")
        roles = payload.get("roles") or []
        permissions = payload.get("permissions") or []
        metadata = payload.get("metadata") or {}
        project_id = str(payload.get("project_id") or "").strip()

        conn = _conectar()
        try:
            # Captura el project_id de ARA en Auth Central directo del propio
            # JWT ya verificado (a pedido del usuario: "nosotros mismos
            # tenemos el proyecto ARA, buscalo" — viaja en cada token real
            # del ERP, no hace falta que nadie lo busque a mano). Se guarda
            # UNA vez y sirve para el chequeo silencioso de sesión
            # (GET /api/central-auth/session-check?project_id=...).
            if project_id:
                conn.execute(
                    "INSERT INTO sso_config (clave, valor) VALUES ('project_id', ?) "
                    "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                    (project_id,),
                )
            conn.execute(
                """
                INSERT INTO sso_usuarios (employee_id, sub, email, nombre, avatar_url, roles_json, permissions_json, metadata_json, actualizado_en)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
                ON CONFLICT(employee_id) DO UPDATE SET
                    sub = excluded.sub,
                    email = excluded.email,
                    nombre = excluded.nombre,
                    avatar_url = excluded.avatar_url,
                    roles_json = excluded.roles_json,
                    permissions_json = excluded.permissions_json,
                    metadata_json = excluded.metadata_json,
                    actualizado_en = datetime('now','localtime')
                """,
                (
                    employee_id,
                    str(payload.get("sub") or ""),
                    email,
                    nombre,
                    avatar_url,
                    json.dumps(roles, ensure_ascii=False),
                    json.dumps(permissions, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )

            # Código de canje de un solo uso: el perfil completo viaja server-side
            # (SQLite), NUNCA en la URL final — evita dejar datos del usuario
            # navegables en el historial del navegador.
            codigo = uuid.uuid4().hex
            usuario_sesion = {
                # Prefijo "sso_" para nunca chocar con los IDs numéricos de la
                # tabla `usuarios` interna de ARA (almacén/rutas) — son
                # espacios de identidad completamente separados a propósito.
                "id": f"sso_{employee_id}",
                "nombre": nombre,
                "email": email,
                "avatar_url": avatar_url,
                "roles": roles,
                "permissions": permissions,
                "metadata": metadata,
            }
            _limpiar_codigos_vencidos(conn)
            conn.execute(
                "INSERT INTO sso_codigos_temp (codigo, payload_json, creado_en) VALUES (?, ?, ?)",
                (codigo, json.dumps(usuario_sesion, ensure_ascii=False), time.time()),
            )
            conn.commit()
        finally:
            conn.close()

        return redirect(f"/ara-inteligente?sso={codigo}")

    @app.route("/api/sso/consumir", methods=["GET"])
    def sso_consumir():
        codigo = request.args.get("codigo", "").strip()
        if not codigo:
            return jsonify({"status": "error", "mensaje": "Falta el código."}), 400

        conn = _conectar()
        try:
            fila = conn.execute(
                "SELECT payload_json, creado_en FROM sso_codigos_temp WHERE codigo = ?", (codigo,)
            ).fetchone()
            if fila is not None:
                # Un solo uso: se borra apenas se lee, exista o no haya vencido.
                conn.execute("DELETE FROM sso_codigos_temp WHERE codigo = ?", (codigo,))
                conn.commit()
        finally:
            conn.close()

        if fila is None:
            return jsonify({"status": "error", "mensaje": "Código de acceso inválido o ya usado."}), 400
        if time.time() - fila["creado_en"] > CODIGO_TTL_SEGUNDOS:
            return jsonify({"status": "error", "mensaje": "El enlace de acceso expiró. Volvé a entrar desde el ERP."}), 400

        try:
            usuario = json.loads(fila["payload_json"])
        except Exception:
            return jsonify({"status": "error", "mensaje": "Perfil de sesión corrupto."}), 500

        return jsonify({"status": "success", "usuario": usuario})

    @app.route("/api/sso/project_id", methods=["GET"])
    def sso_project_id():
        """Expone el project_id de ARA en Auth Central (capturado solo de
        JWTs reales ya verificados en /auth/sso) para que el frontend arme
        la llamada del chequeo silencioso de sesión sin tener que hardcodear
        el UUID en el HTML. No es un dato sensible (identifica el proyecto,
        no a un usuario) — se puede exponer sin autenticación."""
        conn = _conectar()
        try:
            fila = conn.execute("SELECT valor FROM sso_config WHERE clave = 'project_id'").fetchone()
        finally:
            conn.close()
        if fila is None or not fila["valor"]:
            return jsonify({
                "status": "error",
                "mensaje": "Todavía no se capturó ningún project_id — hace falta un login real desde el ERP primero.",
            }), 404
        return jsonify({"status": "success", "project_id": fila["valor"]})

# -*- coding: utf-8 -*-
"""Notificaciones push del navegador (Web Push + VAPID) — infraestructura
genérica para que la PWA de ARA reciba avisos aunque no esté abierta
(requiere el Service Worker con handler 'push', ver static/sw.js).

Este módulo NO dispara ninguna notificación por sí solo — solo expone
`enviar_push_usuario()` para que el código de negocio (rutas, notas,
discrepancias, etc.) la llame cuando de verdad corresponda avisarle a un
usuario. Conectar triggers reales (ej. "nota lista para chequear") es una
decisión de producto que queda pendiente — no se adivinó ningún evento acá.

Llave VAPID: se genera UNA sola vez y se guarda en
data/vapid_private.pem — si se pierde o se regenera, TODAS las
suscripciones existentes de los navegadores quedan inválidas (firmaron
contra la llave pública vieja) y cada usuario tendría que volver a activar
notificaciones. Por eso `Vapid01.from_file()` nunca regenera si el archivo
ya existe.
"""
import json
import os
import sqlite3

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from py_vapid.utils import b64urlencode
from pywebpush import WebPushException, webpush

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "proyecto_ara.db")
VAPID_PRIV_PATH = os.path.join(BASE_DIR, "data", "vapid_private.pem")

# Contacto administrativo real requerido por el estándar VAPID (claim
# 'sub') — los proveedores push (Chrome/FCM) lo usan para contactar al
# operador del servicio si hace falta (ej. abuso). Configurable por env.
VAPID_CONTACTO = os.environ.get("ARA_PUSH_CONTACT", "mailto:soporte@cristmedicals.com")


def _conexion():
    return sqlite3.connect(DB_PATH, timeout=10.0)


def _asegurar_tabla():
    conn = _conexion()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS push_subscripciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id TEXT NOT NULL,
                endpoint TEXT NOT NULL UNIQUE,
                suscripcion_json TEXT NOT NULL,
                creado_en TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


_asegurar_tabla()
_VAPID = Vapid01.from_file(VAPID_PRIV_PATH)
_VAPID_PRIVATE_PEM = _VAPID.private_pem().decode("utf-8")


def obtener_llave_publica_b64url() -> str:
    """Llave pública VAPID en el formato que espera el navegador
    (PushManager.subscribe({applicationServerKey: <esto>}))."""
    raw = _VAPID.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return b64urlencode(raw)


def guardar_suscripcion(usuario_id: str, suscripcion: dict) -> None:
    """Guarda (o actualiza) la suscripción push de un usuario. `suscripcion`
    es el objeto JSON crudo que devuelve PushManager.subscribe() del lado
    del navegador (trae endpoint + keys.p256dh + keys.auth)."""
    endpoint = suscripcion.get("endpoint", "")
    if not endpoint:
        raise ValueError("Suscripción sin 'endpoint' — payload inválido del navegador.")
    conn = _conexion()
    try:
        conn.execute(
            """
            INSERT INTO push_subscripciones (usuario_id, endpoint, suscripcion_json)
            VALUES (?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                usuario_id = excluded.usuario_id,
                suscripcion_json = excluded.suscripcion_json
            """,
            (usuario_id, endpoint, json.dumps(suscripcion, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def eliminar_suscripcion(endpoint: str) -> None:
    """Borra una suscripción — se llama cuando el navegador la reporta como
    caducada/inválida (HTTP 404/410 al intentar enviarle un push)."""
    conn = _conexion()
    try:
        conn.execute("DELETE FROM push_subscripciones WHERE endpoint = ?", (endpoint,))
        conn.commit()
    finally:
        conn.close()


def enviar_push_usuario(usuario_id: str, titulo: str, cuerpo: str, url: str = "/") -> dict:
    """Envía una notificación push a TODAS las suscripciones activas del
    usuario (puede tener varias: celular + PC). Best-effort: una
    suscripción caducada se borra sola y no frena el envío a las demás.

    Retorna {"enviadas": int, "caducadas": int} — nunca lanza excepción
    hacia quien llama (mismo criterio best-effort que el resto de los
    loggers/notificadores del proyecto)."""
    conn = _conexion()
    try:
        filas = conn.execute(
            "SELECT endpoint, suscripcion_json FROM push_subscripciones WHERE usuario_id = ?",
            (usuario_id,),
        ).fetchall()
    finally:
        conn.close()

    payload = json.dumps({"titulo": titulo, "cuerpo": cuerpo, "url": url}, ensure_ascii=False)
    enviadas = 0
    caducadas = 0
    for endpoint, suscripcion_json in filas:
        try:
            suscripcion = json.loads(suscripcion_json)
            webpush(
                subscription_info=suscripcion,
                data=payload,
                vapid_private_key=_VAPID_PRIVATE_PEM,
                vapid_claims={"sub": VAPID_CONTACTO},
            )
            enviadas += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                eliminar_suscripcion(endpoint)
                caducadas += 1
            else:
                print(f"[PUSH] Fallo enviando a {endpoint[:40]}...: {e}", flush=True)
        except Exception as e:
            print(f"[PUSH] Error inesperado enviando push: {e}", flush=True)

    return {"enviadas": enviadas, "caducadas": caducadas}

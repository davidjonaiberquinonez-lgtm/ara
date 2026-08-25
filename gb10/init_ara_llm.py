#!/usr/bin/env python3
"""Inicializa la Capa 3 (ARA_LLM, SQLite local en la GB10)."""
import os
import sqlite3

from dotenv import load_dotenv

load_dotenv(".env.gb10")

db_path = os.getenv("LLM_DB_PATH", "./ara_llm.db")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# WAL: permite lectores concurrentes mientras otro proceso escribe logs/contexto,
# sin el error "database is locked" que afecta a SQLite en modo journal por defecto.
cur.execute("PRAGMA journal_mode=WAL;")
cur.execute("PRAGMA busy_timeout=5000;")
# synchronous=NORMAL: seguro en WAL (solo se pierde el último commit ante un
# crash del SO, nunca corrupción) y evita el fsync en cada escritura que
# frena la alta concurrencia de lectura que van a meter los 3 modelos.
cur.execute("PRAGMA synchronous=NORMAL;")

cur.executescript(
    """
CREATE TABLE IF NOT EXISTS contexto_llm (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cache_adaptadores (
    clave TEXT PRIMARY KEY,
    valor TEXT,
    expira_en DATETIME,
    creado_en DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS logs_operacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nivel TEXT NOT NULL,
    modulo TEXT,
    mensaje TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS circuit_breakers (
    servicio TEXT PRIMARY KEY,
    estado TEXT DEFAULT 'CLOSED',
    fallos INTEGER DEFAULT 0,
    ultimo_fallo DATETIME,
    abierto_hasta DATETIME
);

CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    texto TEXT,
    vector BLOB,
    metadata TEXT,
    creado_en DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""
)
conn.commit()
modo_activo = cur.execute("PRAGMA journal_mode;").fetchone()[0]
conn.close()
print(f"ARA_LLM inicializado en {db_path} (journal_mode={modo_activo})")

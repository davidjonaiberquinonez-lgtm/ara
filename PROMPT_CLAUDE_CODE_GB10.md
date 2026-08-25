# Claude Code — Mandato: ARA Brain en NVIDIA GB10

> **Contexto:** Este es un proyecto de middleware Python (ARA Brain) que opera un sistema de preparación de pedidos farmacéuticos. La Fase 1 (anti-fuga de conexiones MySQL) ya está cerrada y validada en producción. Ahora se debe desplegar la estación edge NVIDIA GB10 con arquitectura de 3 capas.

---

## 🏗️ Arquitectura Objetivo (NO NEGOCIABLE)

```
CAPA 1 — PRUEB25 @ 192.168.4.148:3306
  └─ Fuente única de verdad ERP Profit. SOLO LECTURA. Sync cada 5-15 min.

CAPA 2 — ARA_TEST @ localhost (GB10)
  └─ Espejo local 100% de PRUEB25. Aquí corren los adaptadores PHP/Python.
  └─ Esquema real validado: rep_not.cod_nota, rep_not.estatus, gestion, asignaciones_preparacion

CAPA 3 — ARA_LLM @ ./ara_llm.db (SQLite local en GB10)
  └─ Datos limpios para IA: contexto, embeddings, memoria, logs, circuit breakers.
```

---

## 📋 Inventario Inicial (ejecutar primero)

Abre terminal en VS Code y ejecuta:

```bash
# 1. ¿Existe ARA Brain?
ls -la ara_server.py 2>/dev/null || echo "NO EXISTE"
ls -la venv/ 2>/dev/null || echo "NO EXISTE venv"

# 2. ¿Python disponible?
python3 --version || python --version

# 3. ¿MySQL/MariaDB local?
mysql --version 2>/dev/null || echo "NO EXISTE MySQL client"
mysql -u root -e "SHOW DATABASES;" 2>/dev/null || echo "MySQL server no responde"

# 4. ¿SQLite?
sqlite3 --version 2>/dev/null || echo "NO EXISTE sqlite3"

# 5. ¿project_context.md?
cat project_context.md 2>/dev/null | head -50 || echo "NO EXISTE project_context.md"
```

**Reporta los resultados antes de continuar.**

---

## 🔧 TAREA 1: Crear archivo .env en raíz del proyecto

Crea el archivo `.env` con este contenido exacto (ajusta contraseñas):

```ini
# CAPA 1 — PRUEB25 (solo lectura, sync)
MYSQL_HOST_PRUEB25=192.168.4.148
MYSQL_PORT_PRUEB25=3306
MYSQL_DB_PRUEB25=PRUEB25
MYSQL_USER_PRUEB25=jonaiber
MYSQL_PASSWORD_PRUEB25=CAMBIAR_ESTO
MYSQL_CONNECTION_LIMIT_PRUEB25=10

# CAPA 2 — ARA_TEST (runtime local)
MYSQL_HOST_LOCAL=127.0.0.1
MYSQL_PORT_LOCAL=3306
MYSQL_DB_LOCAL=ARA_TEST
MYSQL_USER_LOCAL=root
MYSQL_PASSWORD_LOCAL=CAMBIAR_ESTO
MYSQL_CONNECTION_LIMIT_LOCAL=30
MYSQL_POOL_RECYCLE=3600
MYSQL_POOL_PRE_PING=true
MYSQL_ALLOW_PERSISTENT=false

# CAPA 3 — ARA_LLM (SQLite)
LLM_DB_TYPE=sqlite
LLM_DB_PATH=./ara_llm.db

# ARA Brain
ARA_MODE=EDGE
ARA_MASTER_URL=http://192.168.4.217:5000
ARA_LOCAL_HOST=0.0.0.0
ARA_LOCAL_PORT=5000

# Sync
SYNC_ENABLED=true
SYNC_INTERVAL_SECONDS=300
SYNC_TABLES=rep_not,gestion,asignaciones_preparacion

# Debug
ARA_DRY_RUN=0
LOG_LEVEL=INFO
```

> ⚠️ **NUNCA commitear este archivo.** Agregar `.env` a `.gitignore` si no está.

---

## 🔧 TAREA 2: Crear CAPA 2 (ARA_TEST)

**Si MySQL/MariaDB NO está instalado en la GB10:**
Instálalo primero (según el OS de la GB10: Ubuntu/Debian/CentOS/Windows).

**Si MySQL SÍ está instalado:**
Ejecuta en terminal:

```bash
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS ARA_TEST CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

Luego, replicar esquema desde PRUEB25. Si tienes acceso de red:

```bash
# Opción A: mysqldump (si está disponible)
mysqldump -h 192.168.4.148 -u jonaiber -p --no-data PRUEB25 > /tmp/schema_ara_test.sql
mysql -u root -p ARA_TEST < /tmp/schema_ara_test.sql

# Opción B: Script Python (si no hay mysqldump)
```

Si no hay `mysqldump`, crea un script Python `replicar_esquema.py`:

```python
#!/usr/bin/env python3
import os, pymysql

SRC = dict(host="192.168.4.148", port=3306, db="PRUEB25",
           user="jonaiber", password=os.getenv("MYSQL_PASSWORD_PRUEB25"))
DST = dict(host="127.0.0.1", port=3306, db="ARA_TEST",
           user="root", password=os.getenv("MYSQL_PASSWORD_LOCAL"))

TABLES = ["rep_not", "gestion", "asignaciones_preparacion"]

src = pymysql.connect(**SRC)
dst = pymysql.connect(**DST)

cur_src = src.cursor()
cur_dst = dst.cursor()

for tbl in TABLES:
    cur_src.execute(f"SHOW CREATE TABLE {tbl}")
    create_sql = cur_src.fetchone()[1]
    cur_dst.execute(f"DROP TABLE IF EXISTS {tbl}")
    cur_dst.execute(create_sql)
    print(f"Tabla {tbl} replicada.")

dst.commit()
src.close(); dst.close()
print("Esquema ARA_TEST listo.")
```

Ejecutar:
```bash
python3 replicar_esquema.py
```

Validar:
```bash
mysql -u root -p -e "USE ARA_TEST; SHOW TABLES; DESCRIBE rep_not;"
```
Debe mostrar: `rep_not`, `gestion`, `asignaciones_preparacion` con columnas `cod_nota`, `estatus`.

---

## 🔧 TAREA 3: Crear CAPA 3 (ARA_LLM)

Crear script `init_ara_llm.py`:

```python
#!/usr/bin/env python3
import sqlite3, os

db_path = os.getenv("LLM_DB_PATH", "./ara_llm.db")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.executescript("""
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
""")
conn.commit()
conn.close()
print(f"ARA_LLM inicializado en {db_path}")
```

Ejecutar:
```bash
python3 init_ara_llm.py
```

Validar:
```bash
sqlite3 ara_llm.db ".tables"
```
Debe mostrar: `cache_adaptadores`, `circuit_breakers`, `contexto_llm`, `embeddings`, `logs_operacion`.

---

## 🔧 TAREA 4: Adaptar configuración de ARA Brain

Busca el archivo de configuración de ARA (probablemente `config.py`, `settings.py`, o similar en `project_context.md`).

**Modificaciones obligatorias:**

1. **Cargar `.env`:** Asegurar que ARA lee variables de entorno desde `.env` (usar `python-dotenv` si no lo hace).

2. **Separar conexiones por capa:**
   - Adaptadores PHP/Python → `MYSQL_HOST_LOCAL` (CAPA 2)
   - Sync programado → `MYSQL_HOST_PRUEB25` (CAPA 1)
   - LLM/IA → `LLM_DB_PATH` (CAPA 3)

3. **Anti-zombi en conexiones Python (SQLAlchemy/PyMySQL):**
   ```python
   # Ejemplo para SQLAlchemy
   engine = create_engine(
       f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}",
       pool_recycle=3600,
       pool_pre_ping=True,
       pool_size=10,
       max_overflow=20,
   )
   ```

4. **Guard de estados:** Implementar o verificar que existe:
   ```python
   ESTADOS_FINALES = {'PREPARACION', 'CHEQUEO', 'CHEQUEADA', 'EMBALADA'}
   if estatus in ESTADOS_FINALES:
       raise ValueError(f"Nota en estado final ({estatus}), no se puede modificar.")
   ```

5. **Modo EDGE:** Asegurar que `ARA_MODE=EDGE` desactiva comportamientos de master (escritura directa a PRUEB25, por ejemplo).

---

## 🔧 TAREA 5: Adaptar actualizar_nota.php para CAPA 2

Si `actualizar_nota.php` corre en la GB10, debe apuntar a `localhost/ARA_TEST`, no a `192.168.4.148`.

Cambiar:
```php
// ANTES (remoto)
$DB_HOST = '192.168.4.148';
$DB_NAME = 'barquisimeto';

// DESPUÉS (local CAPA 2)
$DB_HOST = '127.0.0.1';
$DB_NAME = 'ARA_TEST';
```

Mantener el hotfix anti-zombi:
```php
ini_set('mysql.allow_persistent', 'Off');
ini_set('mysqli.allow_persistent', 'Off');
PDO::ATTR_PERSISTENT => false
finally { $pdo = null; gc_collect_cycles(); }
```

---

## 🔧 TAREA 6: Crear sync programado (CAPA 1 → CAPA 2)

Crear `sync_cron.py`:

```python
#!/usr/bin/env python3
"""Sync PRUEB25 → ARA_TEST cada SYNC_INTERVAL_SECONDS."""
import os, time, pymysql
from datetime import datetime

SRC = dict(
    host=os.getenv("MYSQL_HOST_PRUEB25"),
    port=int(os.getenv("MYSQL_PORT_PRUEB25", 3306)),
    db=os.getenv("MYSQL_DB_PRUEB25"),
    user=os.getenv("MYSQL_USER_PRUEB25"),
    password=os.getenv("MYSQL_PASSWORD_PRUEB25"),
)
DST = dict(
    host=os.getenv("MYSQL_HOST_LOCAL"),
    port=int(os.getenv("MYSQL_PORT_LOCAL", 3306)),
    db=os.getenv("MYSQL_DB_LOCAL"),
    user=os.getenv("MYSQL_USER_LOCAL"),
    password=os.getenv("MYSQL_PASSWORD_LOCAL"),
)
TABLES = os.getenv("SYNC_TABLES", "rep_not,gestion,asignaciones_preparacion").split(",")
INTERVAL = int(os.getenv("SYNC_INTERVAL_SECONDS", 300))

def sync_table(src_cur, dst_cur, table):
    src_cur.execute(f"SELECT * FROM {table}")
    rows = src_cur.fetchall()
    cols = [d[0] for d in src_cur.description]
    placeholders = ",".join(["%s"] * len(cols))
    col_names = ",".join(cols)
    dst_cur.execute(f"TRUNCATE TABLE {table}")
    if rows:
        dst_cur.executemany(f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})", rows)

def main():
    while True:
        try:
            src = pymysql.connect(**SRC)
            dst = pymysql.connect(**DST)
            for tbl in TABLES:
                sync_table(src.cursor(), dst.cursor(), tbl.strip())
            dst.commit()
            src.close(); dst.close()
            print(f"[{datetime.now()}] Sync OK")
        except Exception as e:
            print(f"[{datetime.now()}] Sync ERROR: {e}")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
```

Ejecutar en background:
```bash
nohup python3 sync_cron.py > sync.log 2>&1 &
# o con systemd, o con screen/tmux
```

---

## 🔧 TAREA 7: Validación Final

Ejecutar `validate_3capas.py` (proporcionado por el usuario o crearlo):

```bash
python3 validate_3capas.py
```

Esperado:
```
[HH:MM:SS] === CAPA 1: PRUEB25 ===
[HH:MM:SS] ✅ CAPA 1 OK
[HH:MM:SS] === CAPA 2: ARA_TEST ===
[HH:MM:SS] ✅ CAPA 2 OK
[HH:MM:SS] === CAPA 3: ARA_LLM ===
[HH:MM:SS] ✅ CAPA 3 OK
[HH:MM:SS] RESULTADO: 3/3 capas operativas
[HH:MM:SS] 🚀 Todo listo. ARA Brain puede arrancar.
```

Si pasa, arrancar ARA:
```bash
source venv/bin/activate  # o .\venv\Scripts\activate
python ara_server.py
```

---

## 🚫 RESTRICCIONES

- **NUNCA** escribir en PRUEB25 desde la GB10.
- **NUNCA** hardcodear contraseñas en código fuente.
- **NO** modificar esquema de PRUEB25 ni ARA_TEST.
- Si un archivo no existe, **detenerse y preguntar** antes de crearlo.

---

## 📤 Reporte Final

Al terminar, genera un archivo `REPORTE_GB10.md` con:
- Qué se encontró vs. qué se creó
- Archivos modificados
- Validaciones ejecutadas
- Cualquier incidencia

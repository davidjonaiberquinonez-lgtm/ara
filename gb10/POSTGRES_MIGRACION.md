# SQLite → PostgreSQL para ARA_LLM (Capa 3) — a considerar, NO decidido

`ara_llm.db` (SQLite) hoy: `contexto_llm`, `cache_adaptadores`, `logs_operacion`,
`circuit_breakers`, `embeddings`. Funciona bien para 1 proceso local. Motivo
para considerar Postgres en la GDX: chat + auditorías + OCR + notas corriendo
en paralelo van a escribir a `contexto_llm`/`logs_operacion` concurrentemente
— SQLite serializa escrituras (un solo writer a la vez, lock de archivo);
Postgres no tiene ese techo.

## Cuándo SÍ vale la pena migrar
- Si el volumen de escrituras concurrentes a `contexto_llm`/`logs_operacion`
  empieza a mostrar `database is locked` en los logs de `ara_server.py`.
- Si `embeddings` crece lo suficiente para necesitar `pgvector` (búsqueda
  vectorial real) en vez de guardar el BLOB y comparar en Python.

## Cuándo NO hace falta todavía
- Si el cuello de botella real termina siendo la inferencia (GPU), no la BD
  — no migrar BD para resolver un problema que no era de la BD.
- `cache_adaptadores`/`circuit_breakers` son de bajo volumen, no necesitan
  Postgres por sí solos.

## Si se decide migrar (checklist para cuando se apruebe, no ahora)
1. `psycopg[binary]` (ya listado, comentado, en `requirements_gb10.txt`).
2. Traducir el DDL de `init_ara_llm.py` a Postgres: mismos 5 nombres de
   tabla/columna, `AUTOINCREMENT`→`GENERATED ALWAYS AS IDENTITY`,
   `DATETIME`→`TIMESTAMPTZ`, `BLOB`→`BYTEA` (o `pgvector` para `embeddings`).
3. Variables `PG_*` ya están en `.env.gb10.example` (comentadas).
4. Todo el código que hoy abre `sqlite3.connect(LLM_DB_PATH)` directo tendría
   que pasar por una capa mínima de abstracción (mismo espíritu que
   `gb10/inference/engine_config.py`) para no hardcodear el driver — **no
   existe esa capa todavía**, se crearía en el momento de la migración real,
   no antes (no construir abstracción para una decisión no tomada).
5. Correr `validate_3capas.py` apuntando a Postgres antes de cortar SQLite.

## Recomendación
No migrar antes de tener la GDX corriendo con carga real. Migrar sin datos
de carga real es resolver un problema hipotético.

# Cómo un agente se crea sus propias herramientas (Auto-Skill) — guía para integrar en "Agente IA"

Esto explica el mecanismo que **ARA Coder ya tiene funcionando** (en
`C:\ara_coder_service`) para que un agente, cuando no tiene una herramienta
para algo, no se rinda — resuelva la consulta una vez con una tool genérica
de solo lectura, y si funcionó, se escriba a sí mismo una herramienta nueva,
nombrada y reutilizable, para la próxima vez. Está pensado para que "Agente
IA" pueda copiar el mismo patrón (no hace falta el mismo lenguaje/stack,
sí las mismas garantías de seguridad).

Referencia real, ya funcionando en producción: `core/agent_loop.py` +
`tools/db_tools.py` en `C:\ara_coder_service`. Las herramientas que ya generó
solo este mes están en `C:\ARA_PROYECT\app\Services\NvidiaBrain\Tools\AutoGeneradas\`
(decenas de archivos `*Tool.php`) — son la prueba de que el mecanismo funciona.

---

## 1. La idea en una frase

El agente SIEMPRE tiene, como mínimo, una herramienta genérica de "ejecutar
un SELECT de solo lectura" contra cada fuente de datos que use. Cuando el
modelo resuelve una pregunta con esa tool genérica y **trae resultados
reales**, el sistema:

1. Toma la consulta SQL que funcionó.
2. La **parametriza** (saca los valores literales de esta pregunta puntual
   y los vuelve parámetros — para que sirva para cualquier valor futuro, no
   solo para el caso de hoy).
3. Le pide al LLM un nombre descriptivo + palabras de activación.
4. Genera el archivo de la herramienta nueva (en el caso de ARA, una clase
   PHP que implementa `AgentToolInterface`).
5. **Valida la sintaxis antes de registrarla** (`php -l` en este caso) — si
   falla, se borra el archivo y no se registra nada roto.
6. La agrega a un catálogo (`generated_skills/catalog.json`), y decide si
   queda auto-aprobada o si necesita aprobación manual.

La próxima vez que alguien pregunte algo parecido, el agente ya tiene esa
tool nombrada y puede usarla directo — sin volver a improvisar SQL.

---

## 2. Reglas de seguridad — NO NEGOCIABLES, van primero

Esto es lo más importante de todo el documento. El caso de hoy (el agente
dijo "no tengo herramienta para lotes próximos a vencer" y se quedó ahí) es
mucho mejor que el error contrario: un agente que SÍ tiene tool calling pero
sin estas barreras puede terminar escribiendo o borrando algo en producción
por accidente. Ninguna de estas reglas es opcional:

### 2.1 Solo lectura, siempre, verificado en código — no basta con pedírselo al modelo en el prompt

Cualquier tool que ejecute SQL dinámico (generado por el LLM) tiene que
validar la consulta ANTES de mandarla a la base, con una función tipo
`_es_select_seguro()` (ver `tools/db_tools.py:61`):

- Debe **empezar** con `SELECT` (regex `^\s*SELECT\b`, case-insensitive).
- Debe **rechazar** si contiene, en cualquier parte, alguna de estas
  palabras (regex con `\b`, case-insensitive):
  `INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, MERGE, GRANT,
  REVOKE, ATTACH, DETACH, PRAGMA, VACUUM, REPLACE, EXEC, EXECUTE, DECLARE,
  USE, BACKUP, RESTORE` (la lista exacta varía un poco entre el lado PHP y
  el Python de ARA Coder — usar la unión de ambas, mejor pecar de estricto).
- Debe **rechazar** múltiples sentencias separadas por `;` seguidas de más
  SQL (evita `SELECT 1; DROP TABLE x`).
- Debe **rechazar** procedimientos almacenados / extensiones (`sp_`, `xp_`
  en SQL Server).

Si algo no es 100% seguro que es un SELECT limpio, se rechaza con error —
nunca se ejecuta "total, a ver qué pasa".

### 2.2 Nunca dejar procesos/conexiones huérfanas

Toda conexión a una base se abre y se cierra en el **mismo bloque**,
garantizado incluso si algo falla a mitad:

- Python: `try: ... finally: conn.close()`.
- PHP: mismo patrón, o un `forceDisconnect()` en el destructor de la clase
  wrapper (ver `ConnectionWrapper::__destruct()` en ARA_PROYECT) que cierre
  la conexión de verdad en el servidor, no solo a nivel de objeto en memoria
  — importante si la base soporta connection pooling: **desactivarlo**
  (`ConnectionPooling=0` en el DSN de SQL Server, por ejemplo), si no el
  cierre "real" en el servidor no ocurre y quedan sesiones dormidas.
- Nunca lanzar un proceso hijo (subprocess, `php -l`, lo que sea) sin
  esperar su salida o sin timeout — un proceso que se cuelga esperando
  indefinidamente es tan huérfano como una conexión sin cerrar.

### 2.3 Limitar el tamaño de cualquier resultado

Inyectar un tope de filas en la consulta antes de ejecutarla (`SELECT TOP
100` en SQL Server, `LIMIT` en MySQL/SQLite) — nunca confiar en que el LLM
lo va a pedir él solo.

### 2.4 Validar la sintaxis del archivo generado ANTES de registrarlo

Cuando el mecanismo escribe el archivo de la tool nueva a disco, corre un
linter (`php -l` en este caso) sobre ese archivo. Si falla, se borra el
archivo y no se agrega al catálogo — nunca queda una herramienta rota
disponible para usarse.

### 2.5 Nunca auto-aprobar si quedó un literal escondido

Bug real que ya pasó acá (26/08, documentado en el código): la
parametrización solo reconocía `=` y `LIKE`, así que un `WHERE co_cli IN
('FAR00181', ...)` o `fec_emis >= '2026-08-24'` quedaba con el valor
literal metido adentro, pero el sistema lo contaba como "0 parámetros =
seguro auto-aprobar". Resultado: una skill que parecía genérica pero
siempre devolvía lo mismo, sin importar la pregunta real.

Regla: después de parametrizar, si **sobrevive cualquier comilla de string**
en la consulta, es señal de un literal no reconocido — nunca auto-aprobar
en ese caso, sin importar cuántos parámetros sí se hayan detectado
correctamente. Que quede pendiente de revisión manual.

### 2.6 Nunca pisar una herramienta existente en silencio

Antes de escribir el archivo nuevo, comprobar que no exista ya un archivo o
un nombre de función igual (o casi igual) — si existe, sumarle un sufijo
numérico. Nunca sobreescribir una skill previa sin que quede explícito.

---

## 3. Flujo recomendado (adaptable a cualquier stack)

```
1. Pregunta del usuario → el LLM recibe la lista de tools disponibles,
   incluida SIEMPRE la genérica de "SELECT de solo lectura" por cada
   fuente de datos.
2. Si el LLM la usa y la consulta:
     a) pasa la validación de seguridad (§2.1) → se ejecuta con TOP/LIMIT
     b) trae resultados reales (no un error, no 0 filas silencioso)
   → se guarda como candidata a sintetizar skill.
3. Al final del turno, si hubo una consulta candidata:
     a) parametrizar (sacar literales → parámetros nombrados)
     b) pedir nombre de clase / función / descripción al LLM (segunda
        llamada corta, no la principal)
     c) chequear colisión de nombre contra el catálogo existente
     d) generar el código de la tool nueva a partir de una plantilla fija
        (mismo esqueleto siempre, solo cambia el SQL y los parámetros)
     e) escribir el archivo, correr el linter/validador de sintaxis
     f) si falla → borrar el archivo, no registrar nada
     g) si pasa → pedir palabras de activación, decidir auto-aprobación
        (§2.5), agregar al catálogo
4. Próxima pregunta parecida: el motor de detección de intención mira las
   palabras de activación de cada skill del catálogo (aprobada) antes de
   ir al LLM genérico — si matchea, usa la skill directo.
```

---

## 4. Qué mirar en el código real si quieren copiar el patrón literal

| Pieza | Dónde está (ARA Coder / ARA_PROYECT) |
|---|---|
| Validación SELECT-only (Python) | `C:\ara_coder_service\tools\db_tools.py` función `_es_select_seguro` |
| Validación SELECT-only (PHP) | `C:\ARA_PROYECT\app\Services\ConnectionWrapper.php` método `isSelectOnly` |
| Cierre garantizado de conexión (PHP) | `ConnectionWrapper::forceDisconnect()` / `__destruct()` |
| Ciclo ReAct + disparo de síntesis | `C:\ara_coder_service\core\agent_loop.py` (buscar `_intentar_sintetizar_skill`) |
| Parametrización de la query | `agent_loop.py` función `_parametrizar_query` |
| Generación del código PHP de la tool nueva | `agent_loop.py` función `_generar_php_tool` |
| Validación de sintaxis antes de registrar | `agent_loop.py` función `_smoke_test_php` (corre `php -l`) |
| Catálogo de skills generadas | `C:\ara_coder_service\generated_skills\catalog.json` |
| Ejemplos reales ya generados | `C:\ARA_PROYECT\app\Services\NvidiaBrain\Tools\AutoGeneradas\*.php` |

---

## 5. Resumen para pegarle directo al agente nuevo como instrucción

> Cuando no tengas una herramienta específica para lo que te piden, no
> respondas que no podés: si tenés una tool genérica de "ejecutar SELECT de
> solo lectura" contra la base correspondiente, usala para resolver la
> pregunta de una vez. Si la consulta te devolvió datos reales, generá una
> herramienta nueva y nombrada que reproduzca esa misma consulta mejor
> parametrizada — así la próxima vez no hace falta improvisar SQL de
> nuevo. Reglas que NUNCA podés romper: (1) la consulta SIEMPRE tiene que
> ser un SELECT puro, verificado con código antes de ejecutar, nunca por
> confianza en el prompt; (2) cerrá SIEMPRE la conexión a la base pase lo
> que pase (try/finally); (3) limitá SIEMPRE la cantidad de filas; (4)
> antes de guardar una herramienta nueva, validá que el código generado
> sea sintácticamente válido — si no, descartala; (5) si al parametrizar
> queda algún valor literal sin reconocer, NO auto-apruebes esa
> herramienta, dejala para revisión manual.

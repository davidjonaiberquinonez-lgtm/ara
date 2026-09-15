# Desplegar ARA + ARA Coder en 192.168.4.23 y exponer en `ara.cristmedicals.com`

Guía operativa para dejar ARA funcionando en la máquina `192.168.4.23`, con ARA
Coder al lado, y publicarlo hacia afuera vía Caddy (que ya corre EN la .23, no
en la .217) bajo el dominio `ara.cristmedicals.com`.

⚠️ **Este dominio ya existe y ya apunta a esta red** — `ara.cristmedicals.com`
resuelve hoy a `52.204.0.132`, y el `Caddyfile` de la .217
(`C:\tools\caddy\Caddyfile`) ya tiene un bloque para ese mismo dominio
apuntando a `127.0.0.1:5000` (puerto viejo y muerto ahí — ARA se movió de
5000 a 4050 en la .217 el 24/08, ese bloque quedó huérfano). Esto **no es un
dominio nuevo, es una migración**: hay que mover a dónde apunta, no solo
"agregarlo" en la .23. Ver el punto 7 para los pasos exactos.

Origen de los archivos: ya existe una copia completa en
`\\192.168.4.23\Users\CALOJULIO\Documents\PROYECTOS MIGRADOS DEL 217\` con
`ARA_PROYECT`, `ara_coder_service`, `GDX_SPARK_GB10` y `Servidor_de_Retenciones`
(incluye `venv/`, `node_modules/` y `.env`, comprimidos con 7-Zip el 12/09).
Esa carpeta es solo el punto de caída del backup — **hay que moverlos a rutas
fijas** antes de arrancar nada, porque el código tiene rutas absolutas
hardcodeadas (`C:\ARA_PROYECT\...`, `C:\tools\php\php.exe`, etc.) que no van a
existir si se dejan dentro de "PROYECTOS MIGRADOS DEL 217".

---

## 0. Mapa de dependencias (para no olvidar nada)

```
Caddy (en la .23, puerto 80/443)
  └─ ara.cristmedicals.com  →  reverse_proxy 127.0.0.1:4050 (solo rutas OCR/públicas)

ARA — ara_server.py (puerto 4050, Flask/Waitress)
  ├─ venv propio:        C:\ARA_PROYECT\ara\venv
  ├─ .env:                C:\ARA_PROYECT\.env
  ├─ Blueprints en el MISMO puerto 4050 (no son procesos aparte):
  │    ├─ /api/visor/*    → OCR de productos (visor_articulos) — ⚠️ SIN auth
  │    └─ /api/publico/*  → cotizaciones/facturas/notas — protegida con
  │                          header X-API-Key = ARA_API_PUBLICA_KEY
  ├─ PHP CLI:              C:\tools\php\php.exe (fallback: "php" del PATH)
  │    └─ usado por las Tools AutoGeneradas / NvidiaBrain (SQL Server vía PHP)
  ├─ SQL Server CRISTM25:  192.168.4.20:1433 (login profit) — LAN
  ├─ MySQL barquisimeto:   192.168.4.148:3306 (login jonaiber) — LAN
  ├─ GDX vLLM texto:       192.168.4.4:8000 (Qwen3.6-27B) — LAN
  ├─ GDX vLLM visión:      192.168.4.4:8001 (Qwen2.5-VL-7B) — LAN
  ├─ Ollama LOCAL:         127.0.0.1:11434 (modelo qwen2.5-coder:3b)
  ├─ Ollama por Tailscale: 100.82.4.15:11434 (⚠️ IP de Tailscale, ver §5)
  └─ APIs en la nube:      NVIDIA NIM, DeepSeek, OpenRouteService (claves en .env)

ARA Coder — server.py (puerto 8010)
  ├─ Usa el MISMO python que ARA: C:\ARA_PROYECT\ara\venv\Scripts\python.exe
  │    (ara_coder_service NO tiene venv propio)
  ├─ SQLite:               C:\ARA_PROYECT\ara\ARA_Brain\data\proyecto_ara.db
  │    (viaja dentro de la copia de ARA_PROYECT, no hace falta nada extra)
  ├─ PHP CLI:               C:\tools\php\php.exe
  ├─ GDX (chat/tools):      http://192.168.4.4:8000/v1/chat/completions
  └─ DeepSeek (fallback):   ⚠️ clave hardcodeada en config.py, ver §6
```

---

## 1. Mover a rutas fijas en la .23

Conectado por RDP/escritorio en la .23 (o vía la sesión de opencode que corra
ahí), mover — no dejar dentro de "PROYECTOS MIGRADOS DEL 217":

```
"PROYECTOS MIGRADOS DEL 217\ARA_PROYECT"        ->  C:\ARA_PROYECT
"PROYECTOS MIGRADOS DEL 217\ara_coder_service"  ->  C:\ara_coder_service
```

(`GDX_SPARK_GB10` y `Servidor_de_Retenciones` no son parte de este despliegue,
quedan donde están salvo que se pidan aparte.)

---

## 2. Software de sistema que necesita la .23 (no viaja con el venv)

El `venv` se copió completo a propósito para no reinstalar paquetes Python,
pero **los drivers de sistema NO viajan con el venv** — si faltan, paquetes
como `pyodbc` o `mysqlclient` fallan al importar aunque estén instalados:

- [ ] **ODBC Driver 17 o 18 para SQL Server** (Microsoft) instalado en la .23.
- [ ] **PHP CLI** en `C:\tools\php\php.exe` (o agregado al PATH), con la
      extensión `sqlsrv`/`pdo_sqlsrv` habilitada — igual que en la .217,
      porque las Tools de NvidiaBrain hablan con SQL Server vía PHP.
- [ ] **Ollama** instalado y corriendo local (`127.0.0.1:11434`) con el modelo
      `qwen2.5-coder:3b` descargado (`ollama pull qwen2.5-coder:3b`) — es el
      motor primario "Fast-Fail" del chat de ARA. Si no está, ARA sigue
      funcionando pero cae directo a los fallbacks en la nube.
- [ ] **Caddy** (ya está instalado en la .23 según lo confirmado).

Verificación rápida después de mover las carpetas (desde la .23):
```powershell
C:\ARA_PROYECT\ara\venv\Scripts\python.exe -c "import pyodbc, pymysql, requests; print('OK')"
```
Si tira error de DLL faltante, es el ODBC Driver — instalarlo y repetir.

---

## 3. Variables de entorno — dos capas distintas, no confundir

**Capa A — ya viene resuelta**, dentro de `C:\ARA_PROYECT\.env` (viajó con la
copia): `NVIDIA_API_KEY_1..5`, `DEEPSEEK_API_KEY`, `DEEPSEEK_VISION_MODEL`,
`PROFIT_DB_*`, `MYSQL_*`, `NVIDIA_BRAIN_MODELS`, `OLLAMA_MODEL`, `ORS_API_KEY`,
`ARA_ERP_URL`, `VIGILAR_STOCK_INTERVALO_S`. No hay que tocar nada acá salvo
que cambie algo del entorno de la .23 en sí (por ejemplo si Ollama corre en
otro puerto).

**Capa B — variables de USUARIO de Windows, NO están en el `.env`** — se
pierden en la copia porque son del sistema operativo, no archivos. Hay que
volver a definirlas a mano en la .23 (PowerShell como Administrador, o Panel
de Control → Variables de entorno), porque `bin/iniciar_ara_server.ps1` las
lee de ahí:

```powershell
[System.Environment]::SetEnvironmentVariable('ERP_SSO_SECRET',      '<valor real>', 'User')
[System.Environment]::SetEnvironmentVariable('ARA_SESSION_SECRET',  '<valor real>', 'User')
[System.Environment]::SetEnvironmentVariable('DIP_SERVICE_KEY',     '<valor real>', 'User')
[System.Environment]::SetEnvironmentVariable('ARA_ERP_URL',         'http://192.168.4.23:4050', 'User')
[System.Environment]::SetEnvironmentVariable('ARA_API_PUBLICA_KEY', '<valor real>', 'User')
[System.Environment]::SetEnvironmentVariable('ARA_SERVER_PORT',     '4050', 'User')
```

Los valores reales (`<valor real>`) solo los tiene David — si se arrancan sin
esto, ARA levanta igual pero el SSO y `/api/publico/*` van a fallar en
silencio (secreto vacío).

⚠️ Importante: `ARA_ERP_URL` debe apuntar a la IP/puerto de la propia .23
(`http://192.168.4.23:4050` o `http://127.0.0.1:4050`), **no** dejar el valor
que tenía en la .217 — si no, las Tools PHP van a intentar llamar al backend
Flask de la máquina equivocada.

---

## 4. Levantar ARA (puerto 4050)

```powershell
powershell -File C:\ARA_PROYECT\bin\iniciar_ara_server.ps1
```

Verificar: `http://127.0.0.1:4050/` responde, y en consola no debe aparecer
"venv equivocado" ni error de import.

## 5. Levantar ARA Coder (puerto 8010)

```powershell
cd C:\ara_coder_service
C:\ARA_PROYECT\ara\venv\Scripts\python.exe server.py
```

Verificar: `http://127.0.0.1:8010/health` responde `{"status":"ok",...}`.

⚠️ **Ollama por Tailscale**: `ara_server.py` y `main.py` tienen llamadas
hardcodeadas a `http://100.82.4.15:11434` (esa es una IP de Tailscale, no de
la LAN 192.168.4.x). Si la .23 no está unida a esa misma red Tailscale, esas
llamadas van a fallar — el código tiene manejo de error así que no tumba el
servidor, pero conviene confirmar si hace falta instalar Tailscale ahí
también o si ese camino se puede ignorar en la .23.

---

## 6. ⚠️ Antes de exponer nada a internet

1. ✅ **RESUELTO (14/09)**: `/api/visor/buscar` y `/api/visor/estado` (el OCR
   de productos) ya exigen el header `X-API-Key` contra la misma
   `ARA_API_PUBLICA_KEY` que usa `/api/publico/*` (mismo patrón, ver
   `visor_articulos/application/visor_routes.py`). El frontend interno
   (`templates/index.html`) ya la manda sola — Flask la inyecta al renderizar
   `index()` en `ara_server.py`. Verificado en vivo en la .217: sin key o con
   key incorrecta → 401; con la key real → sigue funcionando igual que antes.
   Si algo externo (Caddy en la .23, un script) llama a `/api/visor/*`
   directo, ahora necesita mandar ese mismo header.
2. **Hay una clave real de DeepSeek hardcodeada como fallback** en tres
   archivos: `ara/ARA_Brain/chat_routes.py:68`, `ara/ARA_Brain/ara_vision.py:76`
   y `ara_coder_service/config.py:88`. A pedido explícito de David esto queda
   tal cual — la rotación/limpieza de esa clave la maneja él manualmente por
   su cuenta, no tocar desde acá.

---

## 7. Caddy en la .23 — mover el sitio, no solo "agregarlo"

Como `ara.cristmedicals.com` ya existe y ya apunta a esta red (§ arriba,
resuelve a `52.204.0.132`), hay dos partes separadas a mover, no solo editar
un Caddyfile:

**7.1 — En la .217 (dejar de reclamar el dominio ahí):**
Quitar o comentar el bloque viejo en `C:\tools\caddy\Caddyfile` (apunta a
`127.0.0.1:5000`, puerto muerto desde el 24/08) para que esa Caddy deje de
responder por `ara.cristmedicals.com` — si no, puede quedar sirviendo un 502
mientras el tráfico siga llegando ahí.

**7.2 — En la .23 (agregar el sitio real):**
Editar el `Caddyfile` de la .23 y agregar, **sin exponer todo el puerto
4050**, solo lo necesario:

```caddyfile
ara.cristmedicals.com {
	@publico path /api/visor/* /api/publico/*
	reverse_proxy @publico 127.0.0.1:4050
	respond 404
}
```

Esto deja pasar únicamente `/api/visor/*` (OCR) y `/api/publico/*`
(cotizaciones/notas/facturas) — el resto de ARA (chat interno, SSO, atención
al cliente, etc.) queda inalcanzable desde afuera aunque alguien adivine la
ruta. Es un punto de partida deliberadamente restrictivo: se puede ampliar
más adelante ruta por ruta, una vez auditada cada una.

Recargar Caddy:
```powershell
caddy reload --config C:\ruta\al\Caddyfile
```
(o el comando/servicio equivalente que ya usan para `ara.cristmedicals.com`
en esa misma máquina).

**7.3 — En el router / DNS (el paso que realmente "muda" el dominio):**
- [x] Confirmado por David: `52.204.0.132` SÍ es la IP pública del router de
      esta red (coincide con rango de Amazon, pero es la WAN asignada por el
      ISP, no infraestructura AWS). Con esto confirmado, el único cambio
      pendiente es: en el router, mover el reenvío de puertos 80/443 de
      `192.168.4.217` a `192.168.4.23`.
- [ ] El firewall de Windows en la .23 permite entrante en 4050/8010 SOLO
      desde `127.0.0.1` (para que todo el tráfico externo pase obligado por
      Caddy y no pegue directo al puerto Flask).

Con el reenvío apuntando a la .23, Caddy renueva el certificado TLS solo
(Let's Encrypt) para el mismo dominio, sin pasos manuales adicionales.

---

## 8. Checklist final

- [ ] Carpetas movidas a `C:\ARA_PROYECT` y `C:\ara_coder_service` (no dentro
      de "PROYECTOS MIGRADOS DEL 217")
- [ ] ODBC Driver 17/18, PHP CLI con sqlsrv, Ollama con `qwen2.5-coder:3b`
- [ ] `python -c "import pyodbc, pymysql, requests"` sin error
- [ ] Variables de USUARIO de Windows seteadas (§3, capa B) con los valores
      reales — `ARA_ERP_URL` apuntando a la .23
- [ ] ARA arriba en `http://127.0.0.1:4050/`
- [ ] ARA Coder arriba en `http://127.0.0.1:8010/health`
- [ ] Conectividad LAN confirmada: SQL Server .20:1433, MySQL .148:3306, GDX
      .4:8000 y .4:8001
- [x] Auth agregada a `/api/visor/*` (§6.1) — ya en el código, viaja con la
      próxima copia a la .23
- [ ] Clave DeepSeek: queda como está, manejo manual de David (§6.2)
- [ ] Bloque viejo de `ara.cristmedicals.com` quitado del Caddyfile de la
      .217 (apuntaba a `127.0.0.1:5000`, puerto muerto)
- [ ] Bloque `ara.cristmedicals.com` agregado al Caddyfile de la .23,
      restringido a `/api/visor/*` y `/api/publico/*`
- [ ] Reenvío de puertos 80/443 movido de la .217 a la .23 en el router
      (confirmado: `52.204.0.132` es la IP pública real de esta red)
- [ ] `https://ara.cristmedicals.com/api/visor/estado` responde desde
      afuera de la LAN

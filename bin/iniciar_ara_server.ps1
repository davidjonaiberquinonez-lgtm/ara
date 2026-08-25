# ═══════════════════════════════════════════════════════════════════════════
# LANZADOR DE ara_server.py — usa SIEMPRE el venv correcto de ARA, sin
# importar qué "python" resuelva el PATH de la terminal donde se corra esto.
# ─────────────────────────────────────────────────────────────────────────────
# BUG real detectado en vivo (24/08): en el PATH del usuario,
# hermes-agent\venv\Scripts aparece ANTES que cualquier otro Python — un
# simple "python ara_server.py" en una terminal nueva cae ahí por accidente,
# y ese venv (de otro proyecto) no tiene flask_cors ni el resto de las
# dependencias de ARA instaladas. Este script elimina el problema de raíz
# para quien lo use: nunca depende de qué "python" gane la carrera del PATH.
#
# Uso:
#   powershell -File C:\ARA_PROYECT\bin\iniciar_ara_server.ps1
# ═══════════════════════════════════════════════════════════════════════════

$python = "C:\ARA_PROYECT\ara\venv\Scripts\python.exe"
$carpeta = "C:\ARA_PROYECT\ara\ARA_Brain"

if (-not (Test-Path $python)) {
    Write-Error "No se encontró el venv de ARA en $python"
    exit 1
}

# Secretos/config que ara_server.py necesita — se pierden si no se setean en
# CADA sesión de PowerShell nueva (las variables de entorno de usuario
# persistidas no se reflejan automáticamente en una sesión ya abierta antes
# de setearlas, y este script puede correr desde cualquier terminal).
$env:ERP_SSO_SECRET       = [System.Environment]::GetEnvironmentVariable('ERP_SSO_SECRET', 'User')
$env:ARA_SESSION_SECRET   = [System.Environment]::GetEnvironmentVariable('ARA_SESSION_SECRET', 'User')
$env:DIP_SERVICE_KEY      = [System.Environment]::GetEnvironmentVariable('DIP_SERVICE_KEY', 'User')
$env:ARA_ERP_URL          = [System.Environment]::GetEnvironmentVariable('ARA_ERP_URL', 'User')
if (-not $env:ARA_SERVER_PORT) { $env:ARA_SERVER_PORT = [System.Environment]::GetEnvironmentVariable('ARA_SERVER_PORT', 'User') }

Set-Location $carpeta
& $python ara_server.py

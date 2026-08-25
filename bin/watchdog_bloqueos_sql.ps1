# ═══════════════════════════════════════════════════════════════════════════
# WATCHDOG ANTI-BLOQUEO SQL SERVER (v4.47)
# ─────────────────────────────────────────────────────────────────────────────
# Corre bin/sql_kill_switch.php cada vez que se programa esta tarea. Ese
# script ya trae las 2 reglas de seguridad (KICKSERVER sleeping >10min, y
# BLOQUEADOR ACTIVO: cualquier SPID que esté bloqueando a otra sesión ahora
# mismo por más de 15s, verificado justo antes de matar — nunca mata
# contención normal/momentánea).
#
# Motivación (incidente real 2026-08-12): una app Node.js (node-mssql) en
# 192.168.4.23 dejaba transacciones sin COMMIT/ROLLBACK, bloqueando en
# cascada a 10+ sesiones de Profit Plus por más de 2 minutos cada vez. Este
# watchdog corta esa cascada automáticamente en vez de esperar a que alguien
# lo note y lo mate a mano.
#
# Instalación en un equipo que nunca se apaga: correr UNA vez, como
# Administrador, bin\instalar_watchdog_bloqueos_sql.ps1 — registra la Tarea
# Programada (cada 2 min, indefinida, sobrevive reinicios) automáticamente.
# Este archivo (watchdog_bloqueos_sql.ps1) es el que la tarea ejecuta; no se
# corre a mano salvo para probarlo suelto.
# ═══════════════════════════════════════════════════════════════════════════

$ErrorActionPreference = 'SilentlyContinue'
$php  = 'C:\tools\php\php.exe'
$script = 'C:\ARA_PROYECT\bin\sql_kill_switch.php'
$log  = 'C:\ARA_PROYECT\ara\ARA_Brain\data\watchdog_bloqueos_sql.log'
$hora = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

if (-not (Test-Path $php)) {
    Add-Content -Path $log -Value "$hora - ERROR: no se encontró php.exe en $php"
    exit 1
}
if (-not (Test-Path $script)) {
    Add-Content -Path $log -Value "$hora - ERROR: no se encontró sql_kill_switch.php en $script"
    exit 1
}

$salida = & $php $script 2>&1
$resultado = $salida | Out-String

try {
    $json = $resultado | ConvertFrom-Json
    if ($json.killed -gt 0) {
        Add-Content -Path $log -Value "$hora - Watchdog bloqueos: MATADOS $($json.killed) SPID(s) de $($json.scanned) detectado(s) - $($json.spids -join ', ')"
    } else {
        Add-Content -Path $log -Value "$hora - Watchdog bloqueos ejecutado (0 candidatos)"
    }
    # BUG real detectado en vivo (24/08): sql_kill_switch.php ya calculaba
    # 'fallidos' (un candidato que seguía vivo pese al KILL — el caso MÁS
    # crítico de auditar, un proceso zombie que la mitigación no logró
    # cerrar) pero este wrapper nunca lo escribía a ningún log, así que se
    # perdía en silencio y el dashboard no lo podía mostrar aunque quisiera.
    if ($json.fallidos -and $json.fallidos.Count -gt 0) {
        foreach ($f in $json.fallidos) {
            Add-Content -Path $log -Value "$hora - Watchdog bloqueos: FALLO AL MATAR SPID $($f.spid) - $($f.error)"
        }
    }
} catch {
    Add-Content -Path $log -Value "$hora - Watchdog bloqueos: salida no parseable: $resultado"
}

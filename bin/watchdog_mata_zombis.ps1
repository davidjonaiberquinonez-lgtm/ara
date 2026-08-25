# ═══════════════════════════════════════════════════════════════════════════
# WATCHDOG ANTI-ZOMBI PHP (Orden de Emergencia v4.14)
# ─────────────────────────────────────────────────────────────────────────────
# Mata procesos php.exe / php-cgi.exe que lleven MÁS de 60 segundos corriendo
# (skills que quedaron colgadas consultando a Profit se eliminan solas).
#
# EXCLUSIÓN controlada: se respetan los subprocesos largos LEGÍTIMOS que
# delegan internamente y tienen timeout propio (skills Python_* y el
# orquestador hermes_chat vía el runner). Esos se auto-suicidan ≤300s:
# si un php.exe delegado lleva más de 300s vivo se considera zombi y se mata.
#
# Programación recomendada (Programador de Tareas de Windows, cada 5 min):
#   powershell -NoProfile -ExecutionPolicy Bypass -File "C:\ARA_PROYECT\bin\watchdog_mata_zombis.ps1"
# ═══════════════════════════════════════════════════════════════════════════

$ErrorActionPreference = 'SilentlyContinue'
$limiteNormalS   = 60
$limiteDelegadoS = 300
$log             = 'C:\ARA_PROYECT\ara\ARA_Brain\data\watchdog_zombis.log'
$matados         = @()

# Excluye del límite normal a los procesos largos legítimos 'php <runner> python_*' / 'hermes_chat'
# (el propio runner los auto-limita vía ARA_CLI_MAX_S y PythonSkillExecutor)
Get-CimInstance Win32_Process -Filter "Name='php.exe' OR Name='php-cgi.exe'" | ForEach-Object {
    $cmd = $_.CommandLine
    $esDelegado = ($cmd -match 'ejecutar_tool_cli\.php.*\b(python_|hermes_chat)\b')
    $limite = if ($esDelegado) { $limiteDelegadoS } else { $limiteNormalS }
    $proc = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
    if ($proc) {
        $segVivos = [int]((Get-Date) - $proc.StartTime).TotalSeconds
        if ($segVivos -gt $limite) {
            Stop-Process -Id $proc.Id -Force
            $matados += ("{0} (pid {1}, {2}s, delegado={3})" -f $_.Name, $_.ProcessId, $segVivos, $esDelegado)
        }
    }
}

$hora = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
if ($matados.Count -gt 0) {
    $detalle = $matados -join ' | '
    Add-Content -Path $log -Value "$hora - Watchdog: MATADOS $($matados.Count) zombi(s): $detalle"
} else {
    Add-Content -Path $log -Value "$hora - Watchdog ejecutado (0 zombis)"
}
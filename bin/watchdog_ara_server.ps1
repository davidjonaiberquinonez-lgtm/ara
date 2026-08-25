# ═══════════════════════════════════════════════════════════════════════════
# WATCHDOG DE ARA_SERVER.PY — auto-recuperación del bug de waitress en Windows
# ─────────────────────────────────────────────────────────────────────────────
# BUG real detectado en vivo (24/08): waitress usa un socket loopback interno
# ("trigger", ver ara/venv/Lib/site-packages/waitress/trigger.py) para
# despertar su hilo principal cuando otro hilo termina una respuesta. Algún
# evento de red (lo más probable en esta máquina: Proton VPN conectando/
# desconectando, que reconfigura el stack completo) puede cerrar ese socket
# mientras el proceso sigue vivo — desde ese instante NINGUNA respuesta se
# completa nunca más (WinError 10038 en cada request), aunque el proceso
# sigue "vivo" y sigue logueando peticiones entrantes. No hay forma de
# reparar ese socket sin reiniciar el proceso completo.
#
# Por eso un chequeo de "¿el proceso existe?" NO alcanza — hace falta un
# health-check HTTP real. Este script corre en loop, en una ventana de
# terminal a mano (no como Tarea Programada/servicio — a pedido del usuario
# mientras el proyecto está en fase de pruebas activas), y reinicia
# ara_server.py solo si detecta 2 chequeos seguidos fallidos.
#
# Uso: abrir una terminal aparte y dejar corriendo:
#   powershell -File C:\ARA_PROYECT\bin\watchdog_ara_server.ps1
# Ctrl+C para detenerlo.
# ═══════════════════════════════════════════════════════════════════════════

# Puerto movido de 5000 a 5050 (24/08): PC-NVR.exe (cliente de cámaras) toma
# el 5000 en esta máquina y a veces gana la carrera de binding.
$url = "http://127.0.0.1:4050/"
$intervaloSeg = 30
$fallosSeguidosParaReiniciar = 2
$log = "C:\ARA_PROYECT\ara\ARA_Brain\data\watchdog_ara_server.log"
$pythonExe = "C:\ARA_PROYECT\ara\venv\Scripts\python.exe"
$workDir = "C:\ARA_PROYECT\ara\ARA_Brain"
# Necesario para que ara_server.py arranque bien (verificación SSO) — mismo
# valor ya configurado como variable de entorno persistente en esta máquina.
$erpSsoSecret = "c998235e15856686ad221c6f872f653de64da58207dc71a56bc0ddb628c9463f"

function Log([string]$msg) {
    $hora = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $log -Value "$hora - $msg"
    Write-Output "$hora - $msg"
}

function Test-AraServer {
    try {
        $codigo = & curl.exe -s -o NUL -w "%{http_code}" --max-time 8 $url 2>$null
        return $codigo -eq "200"
    } catch {
        return $false
    }
}

function Reiniciar-AraServer {
    Log "Health-check fallido $fallosSeguidosParaReiniciar veces seguidas - reiniciando ara_server.py..."
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.ExecutablePath -eq $pythonExe -and $_.CommandLine -match 'ara_server\.py' } |
        ForEach-Object {
            Log "Matando proceso zombie PID $($_.ProcessId)."
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Seconds 2
    $env:ERP_SSO_SECRET = $erpSsoSecret
    Start-Process -FilePath $pythonExe -ArgumentList "ara_server.py" -WorkingDirectory $workDir -WindowStyle Hidden
    Start-Sleep -Seconds 5
    if (Test-AraServer) {
        Log "Reinicio OK - ara_server.py vuelve a responder."
    } else {
        Log "ALERTA: se reinició el proceso pero SIGUE sin responder. Revisar a mano."
    }
}

Log "Watchdog de ara_server.py iniciado (chequeo cada $intervaloSeg s, $url)."
$fallosSeguidos = 0
while ($true) {
    if (Test-AraServer) {
        if ($fallosSeguidos -gt 0) {
            Log "ara_server.py volvió a responder solo (sin necesidad de reiniciar)."
        }
        $fallosSeguidos = 0
    } else {
        $fallosSeguidos++
        Log "Health-check fallido ($fallosSeguidos/$fallosSeguidosParaReiniciar)."
        if ($fallosSeguidos -ge $fallosSeguidosParaReiniciar) {
            Reiniciar-AraServer
            $fallosSeguidos = 0
        }
    }
    Start-Sleep -Seconds $intervaloSeg
}

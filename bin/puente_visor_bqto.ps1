<#
.SYNOPSIS
    Puente HTTP para el visor legacy de rutas (192.168.4.148:8000/visor/) —
    corre en una máquina de la red de BQTO para que las consultas salgan con
    IP de origen de esa sede.

.DESCRIPTION
    El Apache de 192.168.4.148:8000 filtra los datos según la IP de origen de
    quien consulta (verificado en vivo, v4.53): la MISMA ruta física (ej.
    "CARABOBO - ALTA PUERTO CABELLO") trae solo notas S/C consultada desde la
    red de S/C, pero trae notas S/C + notas BQTO reales (72163xxx) mezcladas
    consultada desde una máquina de la red de BQTO. Es la fuente real de
    rutas para BQTO — no existe en ningún otro lado (ni Profit ni un sistema
    propio de BQTO).

    Este script expone un mini servidor HTTP local (HttpListener nativo de
    .NET, sin dependencias externas — no requiere PHP/Python instalados) que
    ARA (corriendo en la red de S/C) puede consultar vía Tailscale. Solo
    reenvía GET/POST de solo-lectura hacia el visor — nunca escribe ni cierra
    despachos.

.PARAMETER Puerto
    Puerto donde escucha el puente (default 5099).

.EXAMPLE
    # Ejecutar como Administrador (HttpListener con prefijo "+" lo requiere):
    powershell -ExecutionPolicy Bypass -File puente_visor_bqto.ps1

.NOTES
    Dejar corriendo persistente: registrar como Tarea Programada de Windows
    (Acción: powershell.exe -ExecutionPolicy Bypass -File "ruta\puente_visor_bqto.ps1",
    Desencadenador: al iniciar sesión / al iniciar el sistema), mismo patrón
    que los watchdogs del proyecto ARA (ver bin/watchdog_*.ps1 como referencia).
#>
param(
    [int]$Puerto = 5099
)

$VisorBase = "http://192.168.4.148:8000/visor"

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://+:$Puerto/")
try {
    $listener.Start()
} catch {
    Write-Host "ERROR al escuchar en el puerto $Puerto. ¿Corriste PowerShell como Administrador?" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
Write-Host "Puente del visor BQTO escuchando en puerto $Puerto (Ctrl+C para detener)..." -ForegroundColor Green

function Responder-Json($response, $objeto, [int]$statusCode = 200) {
    $response.StatusCode = $statusCode
    $response.ContentType = "application/json; charset=utf-8"
    $json = $objeto | ConvertTo-Json -Compress -Depth 5
    $buffer = [System.Text.Encoding]::UTF8.GetBytes($json)
    $response.ContentLength64 = $buffer.Length
    $response.OutputStream.Write($buffer, 0, $buffer.Length)
    $response.OutputStream.Close()
}

while ($listener.IsListening) {
    $context = $listener.GetContext()
    $request = $context.Request
    $response = $context.Response

    try {
        if ($request.Url.AbsolutePath -eq "/catalogo" -and $request.HttpMethod -eq "GET") {
            $html = (Invoke-WebRequest -Uri "$VisorBase/index.php" -UseBasicParsing -TimeoutSec 10).Content
            Responder-Json $response @{ status = "success"; html = $html }
        }
        elseif ($request.Url.AbsolutePath -eq "/lista" -and $request.HttpMethod -eq "POST") {
            $reader = New-Object System.IO.StreamReader($request.InputStream)
            $bodyRaw = $reader.ReadToEnd()
            $reader.Close()
            $datos = $bodyRaw | ConvertFrom-Json

            $responsable = [string]$datos.responsable
            $ruta = [string]$datos.ruta
            if ([string]::IsNullOrWhiteSpace($responsable) -or [string]::IsNullOrWhiteSpace($ruta)) {
                Responder-Json $response @{ status = "error"; mensaje = "Falta responsable o ruta" } 400
                continue
            }

            $sesion = New-Object Microsoft.PowerShell.Commands.WebRequestSession
            $formBody = @{ responsable = $responsable; ruta = $ruta; consulta = "Empezar" }
            Invoke-WebRequest -Uri "$VisorBase/index.php" -Method POST -Body $formBody -WebSession $sesion -UseBasicParsing -TimeoutSec 15 | Out-Null
            $resp = Invoke-WebRequest -Uri "$VisorBase/lista.php" -WebSession $sesion -UseBasicParsing -TimeoutSec 15
            Responder-Json $response @{ status = "success"; html = $resp.Content }
        }
        else {
            Responder-Json $response @{ status = "error"; mensaje = "Ruta no encontrada: $($request.Url.AbsolutePath)" } 404
        }
    } catch {
        try {
            Responder-Json $response @{ status = "error"; mensaje = $_.Exception.Message } 502
        } catch {
            # el cliente pudo haber cerrado la conexión antes de poder responder
        }
    }
}

$listener.Stop()

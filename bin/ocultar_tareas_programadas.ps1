# Correr como Administrador (clic derecho -> "Ejecutar como administrador",
# o PowerShell admin: powershell -File C:\ARA_PROYECT\bin\ocultar_tareas_programadas.ps1)
#
# Cambia el LogonType de Interactive -> S4U en las tareas programadas de ARA
# y proyectos relacionados, para que corran SIN ventana visible (S4U corre
# fuera del escritorio interactivo, sin necesitar contraseña guardada).
# Mismo modo que ya usan "DIP - ARA Warehouse" y "DIP - Watchdog SQL".
#
# Respaldo ya guardado en: C:\ARA_PROYECT\logs\respaldo_tareas_20260922\
# Para revertir una tarea puntual:
#   Register-ScheduledTask -TaskName "<nombre>" -Xml (Get-Content "C:\ARA_PROYECT\logs\respaldo_tareas_20260922\<nombre>.xml" -Raw) -Force

$objetivo = @(
    'ARAWatchdogBloqueosSQL',
    'DIP - Auditoria IA', 'DIP - Central Mensajes', 'DIP - Central Telefonica',
    'DIP - Central Telefonica (reproceso semanal)', 'DIP - Crist Medicals CRM',
    'DIP - GDX Spark GB10', 'DIP - Pedidos OCR',
    'Central Telefonica - Respaldo diario DB', 'Central Telefonica - Vigilante de servicios'
)

foreach ($n in $objetivo) {
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if (-not $t) {
        Write-Output "$n : NO EXISTE, se salta"
        continue
    }
    $prin = New-ScheduledTaskPrincipal -UserId $t.Principal.UserId -LogonType S4U -RunLevel $t.Principal.RunLevel
    try {
        Set-ScheduledTask -TaskName $n -Principal $prin -ErrorAction Stop | Out-Null
        $d = Get-ScheduledTask -TaskName $n
        Write-Output "$n : OK -> LogonType=$($d.Principal.LogonType)"
    } catch {
        Write-Output "$n : ERROR -> $($_.Exception.Message)"
    }
}

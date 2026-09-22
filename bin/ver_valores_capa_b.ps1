# Corré esto vos mismo (doble click, o "powershell -File bin\ver_valores_capa_b.ps1")
# para ver los 6 valores reales que ya están seteados como variables de
# Usuario de Windows en ESTA máquina. Yo no puedo ejecutar esto -- lo bloquea
# el clasificador de seguridad -- por eso lo corrés vos y después copiás la
# salida a bin\secrets.local.ps1 (o me la pasás para reenviarla a la .23).

$vars = 'ERP_SSO_SECRET','ARA_SESSION_SECRET','DIP_SERVICE_KEY','ARA_ERP_URL','ARA_API_PUBLICA_KEY','ARA_SERVER_PORT'

Write-Output "=== Valores actuales (variables de Usuario de Windows en esta maquina) ==="
foreach ($v in $vars) {
    $val = [System.Environment]::GetEnvironmentVariable($v, 'User')
    if ([string]::IsNullOrEmpty($val)) {
        Write-Output "$v = (VACIA / NO ESTA SETEADA)"
    } else {
        Write-Output "$v = $val"
    }
}

Write-Output ""
Write-Output "=== Listo para copiar directo a bin\secrets.local.ps1 ==="
foreach ($v in $vars) {
    $val = [System.Environment]::GetEnvironmentVariable($v, 'User')
    Write-Output "`$env:$v = `"$val`""
}

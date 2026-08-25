<?php
// Monitorea cuántas conexiones PHP abre por segundo hacia la .20
$target = "192.168.4.20";
$duration = 30; // segundos
$log = __DIR__ . "/../logs/flood_" . date('Ymd_His') . ".txt";
if (!is_dir(dirname($log))) mkdir(dirname($log), 0777, true);

$fh = fopen($log, 'w');
$start = time();

while (time() - $start < $duration) {
    $ts = date('H:i:s');
    // PowerShell: netstat filtrado
    $cmd = 'netstat -an | findstr "' . $target . ':1433" | findstr /C:"ESTABLISHED"';
    exec($cmd, $output);
    
    $count = count($output);
    $line = "[$ts] Conexiones ESTABLISHED a ${target}:1433 = $count";
    fwrite($fh, $line . PHP_EOL);
    echo $line . PHP_EOL;
    
    // Si hay más de 10 conexiones, listarlas
    if ($count > 10) {
        foreach ($output as $conn) {
            fwrite($fh, "  >> $conn" . PHP_EOL);
        }
    }
    
    sleep(1);
}

fclose($fh);
echo "Log guardado en: $log" . PHP_EOL;
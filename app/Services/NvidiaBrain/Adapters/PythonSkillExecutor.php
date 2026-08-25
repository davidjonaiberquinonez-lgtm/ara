<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Adapters;

/**
 * Superconector universal PHP ↔ Skills de Python (ARA_SYNC v4.1).
 *
 * Ejecuta cualquier script de C:\ARA_PROYECT\skills\ (detector de mal
 * surtido, OCR de facturas, cola de surtido, quiebres, discrepancia de
 * traslados, etc.) invocando el intérprete Python local (`py -3.14` o
 * `python`) con el puente `python_skill_bridge.py` de esta misma carpeta:
 *
 *   1. Los parámetros se transfieren en JSON por STDIN (nunca por argv
 *      abiertos en el sistema: solo el puente recibe el payload).
 *   2. El script ejecuta la acción solicitada por kwargs (función = acción).
 *   3. La respuesta estructurada se captura por STDOUT y se parsea.
 *
 * Contrato de retorno SIEMPRE plano y serializable (jamás excepciones hacia
 * el motor PHP):
 *   ['success' => true,  'data' => ...]      → éxito
 *   ['success' => false, 'error' => '...']   → fallo controlado
 *
 * Seguridad de subproceso: el subproceso Python se lanza con proc_open(),
 * se mata por timeout (proc_terminate) si excede el límite y tanto stdout
 * como stderr se aíslan del flujo del agente.
 *
 * Uso:
 *   $resultado = PythonSkillExecutor::runSkill(
 *       'auditoria/detector_malsurtido',
 *       ['ruta_log' => '...'],
 *       'analizar_log_surtido'
 *   );
 */
final class PythonSkillExecutor
{
    /** Timeout por defecto de la skill (visión OCR puede tardar). */
    private const TIMEOUT_DEFAULT_S = 90;

    /** Tope absoluto de seguridad por invocación. */
    private const TIMEOUT_MAX_S = 300;

    /** @var string|null Intérprete detectado (cache). */
    private static ?string $interprete = null;

    /**
     * Ejecuta una skill de Python con parámetros JSON por stdin.
     *
     * @param string $scriptRelativePath Ruta relativa del script dentro de
     *                                   skills/, p.ej. 'auditoria/detector_malsurtido'.
     * @param array  $params             Parámetros posicionales/nombrados de la acción
     *                                   (kwargs de la función Python).
     * @param string $accion             Función a invocar dentro del script. Si va
     *                                   vacía, el wrapper decide el default.
     *
     * @return array Siempre serializable:
     *               ['success' => true, 'data' => mixed] |
     *               ['success' => false, 'error' => string, ...]
     */
    public static function runSkill(string $scriptRelativePath, array $params = [], string $accion = ''): array
    {
        try {
            $raiz = self::raizSkills();
            if ($raiz === null || !is_dir($raiz)) {
                return ['success' => false, 'error' => 'No se encontró la carpeta de skills (ARA_SKILLS_DIR o ' . $raiz . ').'];
            }

            $modulo = trim(str_replace(['\\', '.py'], ['/', ''], $scriptRelativePath), '/');
            if ($modulo === '') {
                return ['success' => false, 'error' => 'Ruta de skill vacía.'];
            }
            $accion = trim($accion);
            if ($accion === '') {
                return ['success' => false, 'error' => 'No se indicó la acción (función Python) a invocar.'];
            }

            $timeoutS = (int) ($params['_timeout_s'] ?? self::TIMEOUT_DEFAULT_S);
            unset($params['_timeout_s']);
            $timeoutS = min(max($timeoutS, 5), self::TIMEOUT_MAX_S);

            $puente = __DIR__ . DIRECTORY_SEPARATOR . 'python_skill_bridge.py';
            if (!is_file($puente)) {
                return ['success' => false, 'error' => 'Puente Python no encontrado: ' . $puente];
            }

            $payload = ['modulo' => $modulo, 'accion' => $accion, 'params' => $params];
            $json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
            if ($json === false) {
                return ['success' => false, 'error' => 'Payload de la skill no serializable a JSON.'];
            }

            $cmd = self::interprete() . ' ' . escapeshellarg($puente);
            $descriptors = [
                0 => ['pipe', 'r'],
                1 => ['pipe', 'w'],
                2 => ['pipe', 'w'],
            ];
            $proceso = proc_open($cmd, $descriptors, $pipes, $raiz, null);
            if (!is_resource($proceso)) {
                return [
                    'success' => false,
                    'error'   => 'No se pudo iniciar el intérprete Python (' . $cmd . ').',
                ];
            }

            fwrite($pipes[0], $json);
            fclose($pipes[0]);

            stream_set_blocking($pipes[1], false);
            stream_set_blocking($pipes[2], false);
            $stdout = '';
            $stderr = '';
            $tope = microtime(true) + $timeoutS;

            while (true) {
                $stdout .= (string) stream_get_contents($pipes[1]);
                $stderr .= (string) stream_get_contents($pipes[2]);
                $estado = proc_get_status($proceso);
                if (!$estado['running']) {
                    break;
                }
                if (microtime(true) > $tope) {
                    proc_terminate($proceso);
                    foreach ($pipes as $p) {
                        if (is_resource($p)) {
                            fclose($p);
                        }
                    }
                    proc_close($proceso);
                    return [
                        'success' => false,
                        'error'   => sprintf('Timeout (%ds) ejecutando la skill "%s" (%s).', $timeoutS, $modulo, $accion),
                        'stdout'  => self::aUtf8($stdout),
                        'stderr'  => self::aUtf8($stderr),
                    ];
                }
                usleep(20000);
            }

            $stdout .= (string) stream_get_contents($pipes[1]);
            $stderr .= (string) stream_get_contents($pipes[2]);
            fclose($pipes[1]);
            fclose($pipes[2]);
            proc_close($proceso);

            return self::interpretar($stdout, $stderr, $modulo, $accion);
        } catch (\Throwable $e) {
            return [
                'success' => false,
                'error'   => 'PythonSkillExecutor: ' . $e->getMessage(),
                'tipo'    => get_class($e),
            ];
        }
    }

    /**
     * Parse de la salida del puente: última línea JSON de stdout.
     */
    private static function interpretar(string $stdout, string $stderr, string $modulo, string $accion): array
    {
        $lineas = preg_split('/\r\n|\r|\n/', $stdout) ?: [];
        $ultima = '';
        foreach (array_reverse($lineas) as $linea) {
            $linea = trim($linea);
            if ($linea === '') {
                continue;
            }
            $ultima = $linea;
            break;
        }

        if ($ultima !== '') {
            $decodificada = json_decode($ultima, true);
            if (is_array($decodificada) && array_key_exists('success', $decodificada)) {
                return self::normalizar($decodificada);
            }
        }

        return [
            'success' => false,
            'error'   => sprintf(
                'La skill "%s" (%s) no respondió JSON válido. stderr: %s',
                $modulo,
                $accion,
                trim(self::aUtf8($stderr)) !== '' ? self::aUtf8($stderr) : '(vacío)'
            ),
            'stdout' => self::aUtf8($stdout),
        ];
    }

    /**
     * Garantiza la forma estándar {"success": true, "data": ...} | {"success": false, "error": ...}.
     */
    private static function normalizar(array $salida): array
    {
        $ok = (bool) ($salida['success'] ?? false);
        $normalizada = ['success' => $ok];
        if ($ok) {
            $normalizada['data'] = $salida['data'] ?? null;
        } else {
            $normalizada['error'] = self::aUtf8((string) ($salida['error'] ?? 'Error desconocido de la skill Python.'));
            if (isset($salida['tipo'])) {
                $normalizada['tipo'] = self::aUtf8((string) $salida['tipo']);
            }
        }
        return $normalizada;
    }

    /**
     * Raíz de skills: env ARA_SKILLS_DIR o la carpeta skills/ del proyecto.
     */
    private static function raizSkills(): ?string
    {
        $env = getenv('ARA_SKILLS_DIR');
        if ($env !== false && trim($env) !== '') {
            return trim($env);
        }
        $raiz = dirname(__DIR__, 4) . DIRECTORY_SEPARATOR . 'skills';
        return $raiz;
    }

    /**
     * Detección del intérprete Python (cacheada): ARA_PYTHON > py -3.14 > py > python.
     */
    private static function interprete(): string
    {
        if (self::$interprete !== null) {
            return self::$interprete;
        }
        $env = getenv('ARA_PYTHON');
        $candidatos = ($env !== false && trim($env) !== '')
            ? [trim($env)]
            : ['py -3.14', 'py', 'python'];
        foreach ($candidatos as $candidato) {
            $exe = strtok($candidato, ' ');
            $comando = 'where ' . escapeshellarg((string) $exe) . ' >nul 2>nul';
            exec($comando, $_, $codigo);
            if ($codigo === 0) {
                self::$interprete = $candidato;
                return $candidato;
            }
        }
        self::$interprete = 'python';
        return self::$interprete;
    }

    /**
     * Normaliza cualquier string a UTF-8 (CP1252 de Profit no debe romper JSON).
     */
    private static function aUtf8(string $texto): string
    {
        $limpio = trim($texto);
        if ($limpio === '') {
            return '';
        }
        if (mb_check_encoding($limpio, 'UTF-8')) {
            return $limpio;
        }
        $convertido = mb_convert_encoding($limpio, 'UTF-8', 'CP1252');
        return is_string($convertido) ? $convertido : $limpio;
    }
}

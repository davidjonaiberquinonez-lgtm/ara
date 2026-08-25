<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Orquestador;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;

/**
 * Puente ARA → Hermes Agent (directiva v4.6).
 *
 * Convierte a Hermes (NousResearch hermes-agent v0.20, instalado en
 * %LOCALAPPDATA%\hermes con NVIDIA NIM + DeepSeek V4 Flash) en una tool
 * orquestadora del catálogo ARA: cualquier llamada de la IA del frontend,
 * del motor NvidiaBrain o de un cliente externo llega al runner CLI
 * (`bin/ejecutar_tool_cli.php hermes_chat ...`) y esta tool la delega a
 * `hermes chat -q <mensaje> -Q` en modo programático (salida limpia en
 * stdout: respuesta + línea "session_id: <id>").
 *
 * La sesión de Hermes se puede continuar pasando session_id → --resume.
 *
 * Seguridad de subproceso: proc_open con array de argumentos (sin shell,
 * sin escapeshellarg — lección v4.4: rompe comillas JSON en Windows),
 * timeout con proc_terminate, HERMES_HOME explícito, stdout/stderr aislados.
 */
final class HermesChatTool implements AgentToolInterface
{
    /** Binario por defecto de Hermes (Windows, instalador oficial). */
    private const HERMES_BIN_DEFAULT = 'C:\Users\Personal\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe';

    /** HERMES_HOME por defecto (Windows, instalador oficial). */
    private const HERMES_HOME_DEFAULT = 'C:\Users\Personal\AppData\Local\hermes';

    private const TIMEOUT_DEFAULT_S = 300;
    private const TIMEOUT_MAX_S     = 600;

    /**
     * cwd aislado del subproceso: Hermes restaura sesiones por cwd y las
     * sesiones previas del repo contaminan la respuesta programática.
     *
     * BUG REAL detectado en vivo (v4.53): hermes-agent v0.20 SE CUELGA
     * INDEFINIDAMENTE (nunca responde, ni error ni éxito) cuando su cwd NO
     * es un repositorio git válido — confirmado reproduciendo el cuelgue
     * exacto (timeout 40s sin respuesta) y luego resolviéndolo con un
     * simple `git init` en ese mismo directorio (respuesta instantánea
     * después). Esto explicaba el "Respuesta no-JSON del servidor (HTTP
     * 524)" que veía el usuario: el timeout de 240s de esta tool nunca se
     * cumplía tan rápido como el límite de ~100s del túnel Cloudflare, así
     * que el túnel cortaba primero — pero el problema real era que el
     * subproceso jamás iba a responder, con o sin túnel de por medio. Fix:
     * asegurar que el directorio aislado sea SIEMPRE un repo git (vacío,
     * sin remote, sin contaminar sesiones reales) antes de invocar Hermes.
     */
    private static function cwdAislado(): string
    {
        $env = getenv('ARA_HERMES_CWD');
        if ($env !== false && trim($env) !== '') {
            $dir = trim($env);
        } else {
            $tmp = getenv('TEMP') ?: getenv('TMP') ?: 'C:\Windows\Temp';
            $dir = rtrim($tmp, '\\') . '\opencode\hermes_ara_work';
        }
        if (!is_dir($dir)) {
            @mkdir($dir, 0777, true);
        }
        if (!is_dir($dir . DIRECTORY_SEPARATOR . '.git')) {
            self::inicializarRepoGitVacio($dir);
        }
        self::asegurarContextoAgents($dir);
        return $dir;
    }

    /**
     * AGENTS.md persistente en el cwd aislado: Hermes lo auto-inyecta como
     * contexto en cada sesión (mecanismo nativo de hermes-agent, salvo
     * --ignore-rules). Le da rol fijo de "Motor PRO" de ARA — análisis y
     * reportes pesados a pedido del usuario, no consultas rápidas de
     * negocio (esas ya las resuelven las 33 tools departamentales directas
     * en segundos; Hermes tarda minutos porque razona en varias vueltas).
     * Se escribe una sola vez (idempotente, mismo patrón que el git init).
     */
    private static function asegurarContextoAgents(string $dir): void
    {
        $ruta = $dir . DIRECTORY_SEPARATOR . 'AGENTS.md';
        if (is_file($ruta)) {
            return;
        }
        $contenido = <<<MD
# Rol: Motor PRO de ARA (análisis y reportes pesados)

Sos el motor de análisis profundo de ARA, el middleware de Crist Medicals
(distribuidora farmacéutica, sedes San Cristóbal y Barquisimeto). Te invocan
SOLO cuando el usuario pide explícitamente algo pesado: análisis, reportes,
investigación cruzando varias fuentes, o trabajo que requiere razonar en
varios pasos. Las consultas rápidas de negocio (saldo de cliente, stock de
un artículo, quiebres, ubicación, progreso de ruta, etc.) ya las resuelve
ARA directo con sus 33 tools departamentales en segundos — si te llega algo
así de simple, resolvelo igual pero sabé que normalmente no debería llegar
a vos por lo lento que sos en comparación (varios minutos vs. segundos).

## Puente al catálogo de tools de ARA

Podés ejecutar cualquiera de las 33 tools departamentales de ARA (Almacén,
Compras, Recepción, Auditoría, Despacho) vía el runner CLI:

    php C:\ARA_PROYECT\bin\ejecutar_tool_cli.php <nombre_tool> '<json_args>' '<json_contexto>'

Usalo con el toolset `terminal` cuando necesites datos reales de Profit/ERP
que no tengas ya en el mensaje del usuario.

## Estilo de respuesta

- Directo, en español. Sin relleno ni disculpas.
- Si el análisis requiere varios pasos, hacelos, pero no narres cada paso
  intermedio al usuario final — entregá el resultado.
- Si algo no se puede determinar con los datos disponibles, decilo
  explícito en vez de inventar.
MD;
        @file_put_contents($ruta, $contenido);
    }

    /**
     * `git init` sin shell (proc_open con array de argumentos, mismo patrón
     * de seguridad que el resto de la tool) — best-effort: si falla (git no
     * instalado, permisos), Hermes seguirá colgándose y el error quedará
     * igual de visible que antes de este fix, no se oculta nada.
     */
    private static function inicializarRepoGitVacio(string $dir): void
    {
        $descriptors = [1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
        $proceso = @proc_open(['git', 'init', '-q'], $descriptors, $pipes, $dir);
        if (!is_resource($proceso)) {
            return;
        }
        @fclose($pipes[1]);
        @fclose($pipes[2]);
        @proc_close($proceso);
    }

    public function getName(): string
    {
        return 'hermes_chat';
    }

    public function getDescription(): string
    {
        return '⭐ MOTOR PRO — analisis y reportes PESADOS, solo cuando el '
             . 'usuario lo pide explicitamente (ej. "hazme un analisis '
             . 'completo de...", "investigame a fondo...", "necesito un '
             . 'reporte detallado de..."). Delega a Hermes Agent (v0.20, '
             . 'DeepSeek V4 Flash via NVIDIA NIM), que razona en varias '
             . 'vueltas y puede usar el catalogo completo de tools de ARA. '
             . 'TARDA 2-6 MINUTOS: NO usar para consultas rapidas de '
             . 'negocio (saldo, stock, quiebre, ubicacion, progreso de '
             . 'ruta, etc.) — esas ya las resuelven las tools directas en '
             . 'segundos. Parametros: mensaje (texto a enviar, requerido), '
             . 'modelo (id opcional para este turno), session_id (id de '
             . 'sesion previa para continuar el hilo) y timeout_s (limite '
             . 'opcional, default 300, tope 600).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'mensaje' => [
                    'type'        => 'string',
                    'description' => 'Texto de la consulta a enviar a Hermes (requerido).',
                ],
                'modelo' => [
                    'type'        => 'string',
                    'description' => 'Modelo a usar en este turno (default: el configurado en ~hermes/config.yaml).',
                ],
                'session_id' => [
                    'type'        => 'string',
                    'description' => 'ID de sesion previa de Hermes para continuar el hilo (--resume).',
                ],
                'timeout_s' => [
                    'type'        => 'integer',
                    'description' => 'Timeout en segundos (default 240, tope 300).',
                ],
            ],
            'required' => ['mensaje'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $mensaje = trim((string) ($arguments['mensaje'] ?? ''));
        if ($mensaje === '') {
            // Captura automática de argumentos (v4.8): si no llega 'mensaje',
            // se toma el texto directo de claves alternativas o se unen todos
            // los valores string/númericos (array posicional, texto plano).
            $mensaje = trim((string) ($arguments['query'] ?? ''));
        }
        if ($mensaje === '') {
            $mensaje = trim((string) ($arguments['text'] ?? ''));
        }
        if ($mensaje === '') {
            $partes = [];
            foreach ($arguments as $k => $v) {
                if (is_array($v)) {
                    foreach ($v as $sub) {
                        if (is_string($sub) || is_numeric($sub)) {
                            $partes[] = trim((string) $sub);
                        }
                    }
                } elseif (is_string($v) || is_numeric($v)) {
                    $partes[] = trim((string) $v);
                }
            }
            $mensaje = implode(' ', array_values(array_filter($partes, static fn ($p) => $p !== '')));
        }
        if ($mensaje === '') {
            return ['ok' => false, 'error' => 'Falta el parametro "mensaje" (texto de la consulta).'];
        }

        $modelo = trim((string) ($arguments['modelo'] ?? ''));
        $sessionId = trim((string) ($arguments['session_id'] ?? ''));
        $timeoutS = (int) ($arguments['timeout_s'] ?? self::TIMEOUT_DEFAULT_S);
        $timeoutS = min(max($timeoutS, 10), self::TIMEOUT_MAX_S);

        $hermesBin = getenv('ARA_HERMES_BIN');
        if ($hermesBin === false || trim($hermesBin) === '') {
            $hermesBin = self::HERMES_BIN_DEFAULT;
        } else {
            $hermesBin = trim($hermesBin);
        }
        if (!is_file($hermesBin)) {
            return ['ok' => false, 'error' => 'Binario de Hermes no encontrado: ' . $hermesBin . ' (definir ARA_HERMES_BIN).'];
        }

        $hermesHome = getenv('HERMES_HOME');
        if ($hermesHome === false || trim($hermesHome) === '') {
            $hermesHome = self::HERMES_HOME_DEFAULT;
        }

        // args en array: sin shell, sin escapeshellarg (lección v4.4).
        // --no-restore-cwd: sin restauración de sesión previa del directorio
        // (Hermes restaura sesiones cwd-based y mezcla ruido de contexto).
        $args = [$hermesBin, 'chat', '-q', $mensaje, '-Q', '--no-restore-cwd'];
        if ($modelo !== '') {
            $args[] = '-m';
            $args[] = $modelo;
        }
        if ($sessionId !== '') {
            $args[] = '--resume';
            $args[] = $sessionId;
        }

        $descriptors = [
            0 => ['pipe', 'r'],
            1 => ['pipe', 'w'],
            2 => ['pipe', 'w'],
        ];
        $envSubproceso = [];
        foreach (($_ENV + $_SERVER) as $k => $v) {
            if (is_string($v)) {
                $envSubproceso[$k] = $v;
            }
        }
        $envSubproceso['HERMES_HOME'] = $hermesHome;

        $proceso = proc_open($args, $descriptors, $pipes, self::cwdAislado(), $envSubproceso);
        if (!is_resource($proceso)) {
            return ['ok' => false, 'error' => 'No se pudo iniciar Hermes (' . $hermesBin . ').'];
        }

        // Red de seguridad (detectada en vivo v4.56): en Windows,
        // stream_set_blocking(false) sobre pipes de proc_open NO es fiable —
        // si Hermes deja de producir salida, stream_get_contents() del loop
        // de abajo puede quedar bloqueado y el "Maximum execution time
        // exceeded" (fatal error del set_time_limit externo) interrumpe el
        // script ANTES de llegar al proc_terminate() normal, dejando
        // hermes.exe huérfano corriendo indefinidamente (confirmado: siguió
        // vivo 11+ min tras el fatal error, hubo que matarlo a mano). Este
        // shutdown function corre SIEMPRE (incluso tras ese fatal error) y
        // garantiza que el hijo muera con el padre, pase lo que pase.
        $pidHijo = null;
        $estadoInicial = @proc_get_status($proceso);
        if (is_array($estadoInicial)) {
            $pidHijo = $estadoInicial['pid'] ?? null;
        }
        register_shutdown_function(static function () use ($proceso, $pidHijo): void {
            if (is_resource($proceso)) {
                $estado = @proc_get_status($proceso);
                if (is_array($estado) && $estado['running']) {
                    @proc_terminate($proceso);
                }
            }
            if ($pidHijo !== null && stripos(PHP_OS, 'WIN') === 0) {
                $procKill = @proc_open(
                    ['taskkill', '/F', '/T', '/PID', (string) $pidHijo],
                    [1 => ['pipe', 'w'], 2 => ['pipe', 'w']],
                    $pipesKill
                );
                if (is_resource($procKill)) {
                    @proc_close($procKill);
                }
            }
        });

        fclose($pipes[0]);

        stream_set_blocking($pipes[1], false);
        stream_set_blocking($pipes[2], false);
        $stdout = '';
        $stderr = '';
        $tope = microtime(true) + $timeoutS;
        $timeout = false;

        while (true) {
            $stdout .= (string) stream_get_contents($pipes[1]);
            $stderr .= (string) stream_get_contents($pipes[2]);
            $estado = proc_get_status($proceso);
            if (!$estado['running']) {
                break;
            }
            if (microtime(true) > $tope) {
                $timeout = true;
                break;
            }
            usleep(20000);
        }

        if ($timeout) {
            proc_terminate($proceso);
        }
        $stdout .= (string) stream_get_contents($pipes[1]);
        $stderr .= (string) stream_get_contents($pipes[2]);
        fclose($pipes[1]);
        fclose($pipes[2]);
        $exitCode = $timeout ? -1 : proc_close($proceso);

        if ($timeout) {
            return [
                'ok'    => false,
                'error' => sprintf('Timeout (%ds) esperando respuesta de Hermes.', $timeoutS),
            ];
        }

        $salida = $this->parsear($stdout, $stderr);

        if ($exitCode !== 0) {
            return [
                'ok'      => false,
                'error'   => 'Hermes salio con codigo ' . $exitCode . '. stderr: ' . trim($this->aUtf8($stderr)),
                'stdout'  => $salida['respuesta'],
            ];
        }

        return [
            'ok'         => true,
            'respuesta'  => $salida['respuesta'],
            'session_id' => $salida['session_id'],
        ];
    }

    public function getDefinition(): array
    {
        return [
            'name'        => $this->getName(),
            'description' => $this->getDescription(),
            'parameters'  => $this->getParameters(),
        ];
    }

    /**
     * Extrae respuesta y session_id de stdout+stderr de `hermes chat -Q`.
     * En modo TTY el session_id va en stdout; en subproceso no-TTY va en stderr.
     * Formato: "<respuesta...>\nsession_id: <id>\n"
     */
    private function parsear(string $stdout, string $stderr): array
    {
        $texto = $this->aUtf8($stdout);
        $sessionId = '';
        foreach ([$texto, $this->aUtf8($stderr)] as $flujo) {
            if (preg_match('/session_id:\s*(\S+)/', $flujo, $m)) {
                $sessionId = $m[1];
                break;
            }
        }
        $texto = preg_replace('/\n?\s*session_id:\s*\S+\s*/', '', $texto);
        return [
            'respuesta'  => trim((string) $texto),
            'session_id' => $sessionId,
        ];
    }

    /**
     * Normaliza cualquier string a UTF-8 (CP1252/ANSI no debe romper JSON).
     */
    private function aUtf8(string $texto): string
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

<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;

/**
 * Registro de herramientas del agente + validación de permisos por rol.
 *
 * Responsabilidades:
 *  1. Almacenar herramientas que implementan AgentToolInterface.
 *  2. Convertirlas al formato "tools" de OpenAI v1 (getDefinitions()).
 *  3. Despachar ejecuciones de forma SEGURA (executeTool) con bloque
 *     try/catch: cualquier \Throwable se transforma en un error estructurado
 *     que el LLM puede leer y reintentar, sin romper el bucle del agente.
 *  4. Aplicar la matriz de permisos por rol/módulo (IA_Permisos_Roles):
 *     solo se exponen y ejecutan las herramientas permitidas para el rol.
 *
 * Permisos:
 *  - setPermisos(array $nombres): lista explícita de herramientas permitidas.
 *    El token '*' (o rol ADMINISTRADOR con Herramientas_Permitidas_JSON
 *    '["*"]') autoriza TODAS las herramientas.
 *  - Si no se configura permisos, TODAS las herramientas registradas quedan
 *    disponibles (comportamiento abierto; útil en desarrollo/tests).
 *
 * Desacoplado de frameworks: la carga de permisos desde SQL Server se hace
 * por inyección (un callable) para no acoplar esta clase a PDO/ADO.
 */
final class ToolRegistry
{
    /** @var array<string,AgentToolInterface> Nombre => herramienta. */
    private array $tools = [];

    /** @var array<int,string>|null Herramientas permitidas para el rol activo. */
    private ?array $herramientasPermitidas = null;

    /** @var bool True cuando se configuró permisos (controla el modo estricto). */
    private bool $permisosConfigurados = false;

    /**
     * Registra una herramienta. Si el nombre ya existe, la reemplaza.
     */
    public function registerTool(AgentToolInterface $tool): self
    {
        $this->tools[$tool->getName()] = $tool;
        return $this;
    }

    /**
     * Escaneo recursivo y registro automático de Tools departamentales.
     *
     * Recorre SOLO las subcarpetas departamentales de Tools/ (Almacen/,
     * Compras/, Recepcion/, Auditoria/, Despacho/) con
     * RecursiveDirectoryIterator, require_once de cada archivo .php y registro
     * automático de toda clase que implemente AgentToolInterface. Sin registro
     * manual en arreglos: cualquier .php nuevo en una subcarpeta queda
     * activo al siguiente escaneo.
     *
     * Regla de ecosistema (v4.1): la raíz de Tools/ NO puede contener
     * archivos .php sueltos; todo archivo fuera de las 5 subcarpetas
     * departamentales (p.ej. helpers o utilidades) se ignora aquí.
     *
     * Namespace derivado de la ruta (PSR-4-like):
     *   Tools/Almacen/StockSurtidoPrioritarioTool.php
     *     → App\Services\NvidiaBrain\Tools\Almacen\StockSurtidoPrioritarioTool
     *
     * @param string $dir Ruta absoluta de la carpeta Tools/.
     *
     * @return self
     */
    public function loadFromDirectory(string $dir): self
    {
        if (!is_dir($dir)) {
            throw new \InvalidArgumentException(
                'ToolRegistry::loadFromDirectory() la carpeta no existe: ' . $dir
            );
        }

        // 'AutoGeneradas' (v4.60): skills PHP sintetizadas por ARA Coder
        // (servicio independiente, puerto 8010) — mismo mecanismo de
        // auto-descubrimiento, para que el motor Automático las encuentre
        // sin registro manual apenas ARA Coder las valida y las escribe ahí.
        $departamentos = ['Almacen', 'Auditoria', 'Compras', 'Despacho', 'Orquestador', 'Recepcion', 'Common', 'AutoGeneradas'];
        $namespaceBase = __NAMESPACE__ . '\\Tools\\';
        $dir = realpath($dir) ?: $dir;
        $dirReal = rtrim(str_replace('\\', '/', $dir), '/') . '/';

        $iterador = new \RecursiveIteratorIterator(
            new \RecursiveDirectoryIterator($dir, \FilesystemIterator::SKIP_DOTS)
        );

        $cargadas = [];
        foreach ($iterador as $archivo) {
            if (!$archivo instanceof \SplFileInfo) {
                continue;
            }
            if ($archivo->getExtension() !== 'php' || !$archivo->isFile()) {
                continue;
            }
            $rutaReal = str_replace('\\', '/', $archivo->getPathname());
            $relativa = substr($rutaReal, strlen($dirReal), -4); // sin '.php'
            if ($relativa === '' || strpos($relativa, '/') === false) {
                // Archivos en la raíz de Tools/ (PROHIBIDO en v4.1): se omiten.
                continue;
            }
            $departamento = explode('/', $relativa)[0];
            if (!in_array($departamento, $departamentos, true)) {
                // Fuera de las subcarpetas departamentales: no se escanea.
                continue;
            }

            $partes = explode('/', $relativa);
            $partes = array_map(
                static fn (string $p): string => mb_strtoupper(mb_substr($p, 0, 1)) . mb_substr($p, 1),
                $partes
            );
            $clase = $namespaceBase . implode('\\', $partes);

            if (!class_exists($clase, false) && is_file($archivo->getPathname())) {
                require_once $archivo->getPathname();
            }
            if (!class_exists($clase, false)) {
                continue; // archivo sin clase esperada (p.ej. helper suelto)
            }
            $implementa = in_array(AgentToolInterface::class, class_implements($clase) ?: [], true);
            if (!$implementa) {
                continue;
            }
            try {
                /** @var AgentToolInterface $instancia */
                $instancia = new $clase();
                $this->registerTool($instancia);
                $cargadas[] = $clase;
            } catch (\Throwable $e) {
                // Una herramienta rota NO debe tumbar el registro del resto.
                error_log(sprintf(
                    '[ToolRegistry] Tool "%s" no registrable: %s',
                    $clase,
                    $e->getMessage()
                ));
            }
        }

        if ($cargadas !== []) {
            error_log(sprintf(
                '[ToolRegistry] %d tool(s) departamental(es) registradas: %s',
                count($cargadas),
                implode(', ', $cargadas)
            ));
        }
        return $this;
    }

    /**
     * Registra varias herramientas a la vez.
     *
     * @param iterable<AgentToolInterface> $tools
     */
    public function registerTools(iterable $tools): self
    {
        foreach ($tools as $tool) {
            if (!$tool instanceof AgentToolInterface) {
                throw new \InvalidArgumentException(
                    'ToolRegistry::registerTools() espera iterable de AgentToolInterface.'
                );
            }
            $this->registerTool($tool);
        }
        return $this;
    }

    /**
     * Configura la matriz de permisos del rol activo.
     *
     * @param array<int,string> $nombres Herramientas permitidas.
     *                                   ['*'] autoriza todas.
     */
    public function setPermisos(array $nombres): self
    {
        $this->herramientasPermitidas = array_values($nombres);
        $this->permisosConfigurados = true;
        return $this;
    }

    /**
     * Limpia la restricción de permisos (modo abierto).
     */
    public function sinPermisos(): self
    {
        $this->herramientasPermitidas = null;
        $this->permisosConfigurados = false;
        return $this;
    }

    /** @return array<string,AgentToolInterface> Mapa nombre => herramienta. */
    public function all(): array
    {
        return $this->tools;
    }

    /** @return array<int,string> Nombres de todas las herramientas registradas. */
    public function names(): array
    {
        return array_keys($this->tools);
    }

    /** @return bool True si existe la herramienta (sin importar permisos). */
    public function has(string $name): bool
    {
        return isset($this->tools[$name]);
    }

    /**
     * ¿La herramienta está permitida para el rol activo?
     */
    public function estaPermitida(string $name): bool
    {
        if (!$this->permisosConfigurados || $this->herramientasPermitidas === null) {
            return true;
        }
        if (in_array('*', $this->herramientasPermitidas, true)) {
            return true;
        }
        return in_array($name, $this->herramientasPermitidas, true);
    }

    /**
     * Obtiene una herramienta por nombre (sujeta a permisos).
     *
     * @throws NvidiaBrainException Si no existe o no está permitida.
     */
    public function get(string $name): AgentToolInterface
    {
        if (!$this->has($name)) {
            throw NvidiaBrainException::payload(sprintf(
                'Herramienta no registrada: "%s". Disponibles: %s',
                $name,
                implode(', ', $this->names())
            ));
        }
        if (!$this->estaPermitida($name)) {
            throw NvidiaBrainException::permission(sprintf(
                'El rol activo no tiene permiso para ejecutar la herramienta "%s".',
                $name
            ));
        }
        return $this->tools[$name];
    }

    /**
     * Genera las definiciones JSON de OpenAI v1 (campo "tools").
     *
     * Solo incluye las herramientas PERMITIDAS para el rol activo.
     *
     * @return array<int,array<string,mixed>>
     */
    public function getDefinitions(): array
    {
        $definitions = [];
        foreach ($this->tools as $tool) {
            if (!$this->estaPermitida($tool->getName())) {
                continue;
            }
            $definitions[] = [
                'type' => 'function',
                'function' => [
                    'name'        => $tool->getName(),
                    'description' => $tool->getDescription(),
                    'parameters'  => $tool->getParameters(),
                ],
            ];
        }
        return $definitions;
    }

    /**
     * Despacha de forma SEGURA una llamada a herramienta solicitada por el LLM.
     *
     * Bloque try/catch completo: cualquier \Throwable de la ejecución se
     * convierte en un error estructurado JSON (para que la IA lo lea y
     * reintente con parámetros corregidos), NUNCA en excepción hacia el motor.
     *
     * @param string $name      Nombre de la herramienta.
     * @param array  $arguments Argumentos decodificados del modelo.
     * @param array  $contexto  Contexto de sesión (usuario/rol/módulo/ruta).
     *
     * @return array Forma OpenAI v1 de un mensaje "tool":
     *               ['role' => 'tool', 'tool_call_id' => ?, 'content' => '<JSON>']
     */
    public function executeTool(string $name, array $arguments, array $contexto): array
    {
        $callId = (string) ($contexto['tool_call_id'] ?? '');

        // 1) Permisos.
        if (!$this->estaPermitida($name)) {
            return $this->toolMessage(
                $callId,
                ['ok' => false, 'error' => sprintf('Sin permiso para ejecutar la herramienta "%s".', $name), 'tool' => $name]
            );
        }

        // 2) Existencia.
        if (!$this->has($name)) {
            return $this->toolMessage(
                $callId,
                ['ok' => false, 'error' => sprintf('Herramienta "%s" no registrada.', $name), 'tool' => $name]
            );
        }

        // 3) Ejecución con captura total.
        try {
            $resultado = $this->tools[$name]->execute($arguments, $contexto);
            if (!is_array($resultado)) {
                $resultado = ['ok' => false, 'error' => sprintf(
                    'La herramienta "%s" retornó un tipo inválido (%s); debe devolver array.',
                    $name,
                    gettype($resultado)
                )];
            }
        } catch (\Throwable $e) {
            // Error estructurado para que la IA corrija parámetros y reintente.
            $resultado = [
                'ok'       => false,
                'error'    => $e->getMessage(),
                'tool'     => $name,
                'exception'=> get_class($e),
            ];
        }

        return $this->toolMessage($callId, $resultado);
    }

    /**
     * Construye un mensaje "tool" de OpenAI v1 con contenido JSON string.
     *
     * @param array $resultado
     */
    private function toolMessage(string $callId, array $resultado): array
    {
        try {
            $content = json_encode($resultado, NvidiaBrainClient::JSON_FLAGS);
        } catch (\JsonException $e) {
            // BUG REAL (detectado en vivo): Profit/SQL Server a veces devuelve
            // texto en CP1252 (ej. direcciones/nombres con tildes mal
            // codificadas) — json_encode con JSON_THROW_ON_ERROR truena con
            // "Malformed UTF-8 characters" y el usuario solo veía "Resultado
            // no serializable" sin dato alguno. bin/ejecutar_tool_cli.php ya
            // tenía este saneamiento (r_normalizar_utf8) para el runner CLI;
            // aquí faltaba para el flujo de function-calling del LLM. Se
            // reintenta forzando CP1252->UTF-8 en cada string antes de tirar
            // la toalla.
            try {
                $content = json_encode(self::normalizarUtf8($resultado), NvidiaBrainClient::JSON_FLAGS);
            } catch (\JsonException $e2) {
                $content = json_encode([
                    'ok'    => false,
                    'error' => 'Resultado no serializable: ' . $e2->getMessage(),
                ]);
            }
        }
        return [
            'role'         => 'tool',
            'tool_call_id' => $callId,
            'content'      => $content,
        ];
    }

    /**
     * Normaliza cualquier string a UTF-8 válido (CP1252 de Profit no debe
     * romper JSON) — misma lógica que r_normalizar_utf8() en
     * bin/ejecutar_tool_cli.php, replicada aquí porque ese archivo es un
     * script CLI standalone (no una clase importable).
     */
    private static function normalizarUtf8(mixed $v): mixed
    {
        if (is_string($v)) {
            return mb_check_encoding($v, 'UTF-8')
                ? $v
                : mb_convert_encoding($v, 'UTF-8', 'Windows-1252');
        }
        if (is_array($v)) {
            foreach ($v as $k => $item) {
                $v[$k] = self::normalizarUtf8($item);
            }
        }
        return $v;
    }
}

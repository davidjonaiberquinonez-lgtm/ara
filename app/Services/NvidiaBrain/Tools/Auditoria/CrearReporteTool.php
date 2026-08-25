<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Auditoria;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;

require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Herramienta de ejemplo: genera archivos físicos de reporte (CSV, JSON, TXT).
 *
 * Escribe en el directorio storage/exports/ (configurable) y retorna la URL
 * pública de descarga para la interfaz del ERP. Implementa AgentToolInterface
 * y puede registrarse directamente en el ToolRegistry:
 *
 *   $registry->registerTool(new CrearReporteTool());
 *
 * Seguridad:
 *  - El nombre de archivo se SANITIZA con basename() + regex de caracteres
 *    seguros para impedir path traversal (../, rutas absolutas).
 *  - La ruta final se valida contra el directorio de destino.
 *  - Solo se permiten las extensiones CSV/JSON/TXT.
 */
final class CrearReporteTool implements AgentToolInterface
{
    /** @var string Directorio físico de exportaciones. */
    private string $exportDir;

    /** @var string Prefijo de la URL pública de descarga. */
    private string $baseUrl;

    /** Extensiones permitidas → mime/content para el reporte. */
    private const EXTENSIONES_PERMITIDAS = [
        'csv'  => 'text/csv; charset=utf-8',
        'json' => 'application/json; charset=utf-8',
        'txt'  => 'text/plain; charset=utf-8',
    ];

    /**
     * @param string $exportDir Directorio de escritura (debe existir y tener
     *                          permisos de escritura). Default:
     *                          <raíz>/storage/exports.
     * @param string $baseUrl   Base de la URL pública de descarga.
     */
    public function __construct(
        string $exportDir = '',
        string $baseUrl = '/storage/exports'
    ) {
        $this->exportDir = $exportDir !== ''
            ? rtrim($exportDir, '/\\')
            : $this->defaultExportDir();
        $this->baseUrl = rtrim($baseUrl, '/');
    }

    public function getName(): string
    {
        return 'crear_reporte_archivo';
    }

    public function getDescription(): string
    {
        return 'Genera un archivo físico de reporte (CSV, JSON o TXT) en el servidor '
             . 'y retorna la URL pública de descarga. Úsala cuando el usuario pida '
             . 'exportar datos a un archivo (reporte, listado, respaldo). '
             . 'Parámetros: formato (csv|json|txt), nombre_archivo (sin extensión) '
             . 'y datos (array de filas).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'formato' => [
                    'type'        => 'string',
                    'enum'        => ['csv', 'json', 'txt'],
                    'description' => 'Formato del archivo a generar.',
                ],
                'nombre_archivo' => [
                    'type'        => 'string',
                    'description' => 'Nombre base del archivo SIN extensión. Se sanitiza.',
                ],
                'datos' => [
                    'type'        => 'array',
                    'description' => 'Filas de datos (array de objetos) a exportar.',
                ],
            ],
            'required' => ['formato', 'nombre_archivo', 'datos'],
        ];
    }

    /**
     * {@inheritDoc}
     */
    public function execute(array $arguments, array $contexto): array
    {
        $formato = strtolower(trim((string) ($arguments['formato'] ?? 'csv')));
        $nombreBase = (string) ($arguments['nombre_archivo'] ?? 'reporte');
        $datos = $arguments['datos'] ?? [];

        // ── 1) Validación de formato ──────────────────────────────────────────
        if (!isset(self::EXTENSIONES_PERMITIDAS[$formato])) {
            return [
                'ok'    => false,
                'error' => sprintf(
                    'Formato "%s" no soportado. Use: %s',
                    $formato,
                    implode(', ', array_keys(self::EXTENSIONES_PERMITIDAS))
                ),
            ];
        }

        // ── 2) Sanitización del nombre (antipath-traversal) ───────────────────
        $nombreLimpio = basename($nombreBase);
        $nombreLimpio = preg_replace('/[^A-Za-z0-9_\-]/', '_', (string) $nombreLimpio) ?? 'reporte';
        if ($nombreLimpio === '' || $nombreLimpio === '.' || $nombreLimpio === '..') {
            $nombreLimpio = 'reporte';
        }
        $nombreLimpio = substr($nombreLimpio, 0, 80); // tope de longitud
        $nombreArchivo = $nombreLimpio . '.' . $formato;

        // ── 3) Escritura segura dentro del directorio ─────────────────────────
        if (!is_dir($this->exportDir)) {
            if (!@mkdir($this->exportDir, 0755, true) && !is_dir($this->exportDir)) {
                return ['ok' => false, 'error' => 'No se pudo crear el directorio de exportaciones: ' . $this->exportDir];
            }
        }
        if (!is_writable($this->exportDir)) {
            return ['ok' => false, 'error' => 'El directorio de exportaciones no tiene permisos de escritura.'];
        }

        $rutaFinal = $this->exportDir . DIRECTORY_SEPARATOR . $nombreArchivo;
        // Doble verificación: la ruta final debe quedar dentro de exportDir.
        if (!str_starts_with(realpath(dirname($rutaFinal)) ?: '', realpath($this->exportDir) ?: '')) {
            return ['ok' => false, 'error' => 'Nombre de archivo inválido (ruta fuera del directorio permitido).'];
        }

        // ── 4) Serialización según formato ────────────────────────────────────
        try {
            switch ($formato) {
                case 'json':
                    $contenido = json_encode($datos, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
                    if ($contenido === false) {
                        return ['ok' => false, 'error' => 'Los datos no son serializables a JSON: ' . json_last_error_msg()];
                    }
                    break;

                case 'txt':
                    $contenido = $this->serializarTxt($datos);
                    break;

                default: // csv
                    $contenido = $this->serializarCsv($datos);
            }
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => 'Error al serializar el reporte: ' . $e->getMessage()];
        }

        // ── 5) Escritura atómica (temp + rename) ──────────────────────────────
        $temp = $rutaFinal . '.tmp';
        $bytes = @file_put_contents($temp, $contenido);
        if ($bytes === false) {
            return ['ok' => false, 'error' => 'No se pudo escribir el archivo de reporte.'];
        }
        if (!@rename($temp, $rutaFinal)) {
            @unlink($temp);
            return ['ok' => false, 'error' => 'No se pudo finalizar el archivo de reporte.'];
        }

        return [
            'ok'        => true,
            'archivo'   => $nombreArchivo,
            'formato'   => $formato,
            'bytes'     => $bytes,
            'url'       => $this->baseUrl . '/' . $nombreArchivo,
            'mensaje'   => sprintf('Reporte generado: %s (%d bytes)', $nombreArchivo, $bytes),
            'card'      => CardBuilder::iniciar('📄', 'REPORTE GENERADO')
                ->campo('ARCHIVO', $nombreArchivo)
                ->campo('FORMATO', strtoupper($formato))
                ->campo('BYTES', (int) $bytes, 'numero')
                ->campo('URL', $this->baseUrl . '/' . $nombreArchivo)
                ->footer('Descarga disponible en: ' . $this->baseUrl . '/' . $nombreArchivo)
                ->tarjeta(),
        ];
    }

    /**
     * Serializa filas a CSV (primera fila = encabezados).
     */
    private function serializarCsv(array $datos): string
    {
        $out = fopen('php://temp', 'r+');
        if ($out === false) {
            throw new \RuntimeException('No se pudo abrir buffer temporal para CSV.');
        }
        try {
            if ($datos !== [] && is_array($datos[0])) {
                fputcsv($out, array_keys($datos[0]));
                foreach ($datos as $fila) {
                    if (is_array($fila)) {
                        fputcsv($out, array_values($fila));
                    }
                }
            }
            rewind($out);
            $contenido = (string) stream_get_contents($out);
        } finally {
            fclose($out);
        }
        return $contenido;
    }

    /**
     * Serializa filas a texto plano (clave: valor por línea).
     */
    private function serializarTxt(array $datos): string
    {
        if ($datos === []) {
            return "(sin datos)\n";
        }
        $lineas = [];
        foreach ($datos as $i => $fila) {
            if (!is_array($fila)) {
                continue;
            }
            $lineas[] = sprintf('[Registro %d]', $i + 1);
            foreach ($fila as $clave => $valor) {
                $lineas[] = sprintf('  %s: %s', $clave, is_scalar($valor) ? (string) $valor : json_encode($valor));
            }
            $lineas[] = '';
        }
        return implode("\n", $lineas);
    }

    /**
     * Directorio de exportación por defecto: <raíz proyecto>/storage/exports.
     */
    private function defaultExportDir(): string
    {
        // app/Services/NvidiaBrain/Tools → 4 niveles hasta la raíz del proyecto.
        $raiz = dirname(__DIR__, 3);
        return $raiz . DIRECTORY_SEPARATOR . 'storage' . DIRECTORY_SEPARATOR . 'exports';
    }
}

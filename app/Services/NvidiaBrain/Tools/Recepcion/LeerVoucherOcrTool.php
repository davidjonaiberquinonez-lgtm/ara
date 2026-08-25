<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Recepcion;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\NvidiaBrainClient;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;

require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Extrae datos de un voucher/comprobante bancario desde una imagen local.
 *
 * Lee la imagen (PNG/JPEG/WebP/BMP, máx. 15 MB), la codifica en base64 y la
 * envía a NVIDIA NIM Cloud (motor primario, mismo proveedor que ara_vision.py)
 * para obtener un JSON estructurado con: banco, referencia, monto y fecha.
 *
 * MODELOS: se consultó en vivo GET /v1/models de NVIDIA NIM (17/08) — de los
 * candidatos "vision" reales del catálogo, solo estos dos existen y sirven
 * para extracción de texto/campos (los demás del catálogo o son embeddings
 * VLM sin generación de texto, o no están confirmados estables):
 *   1. meta/llama-3.2-11b-vision-instruct  (primario — mismo modelo #1 del
 *      pool ya probado en producción por ara_vision.py)
 *   2. meta/llama-3.2-90b-vision-instruct  (respaldo, más grande/lento)
 * Rotación: por cada modelo se prueban las 5 API keys del pool
 * (NVIDIA_API_KEY_1..5) antes de pasar al siguiente modelo — cubre 429
 * (rate limit por key) sin descartar el modelo, y 401/403/404/500/timeout
 * sí avanzan al siguiente modelo.
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]          → éxito
 *   ['success' => false, 'error' => 'mensaje']     → error controlado
 *
 * Configuración (variables de entorno, con defaults):
 *   NVIDIA_VISION_BASE_URL   Servidor OpenAI v1 de visión (default: NVIDIA
 *                            NIM Cloud, https://integrate.api.nvidia.com —
 *                            SIN '/v1', NvidiaBrainClient lo agrega solo).
 *   NVIDIA_VISION_MODEL      Si se define, es el ÚNICO modelo probado (se
 *                            salta el pool de 2 modelos de arriba).
 *   NVIDIA_VISION_API_KEY    Si se define, es la ÚNICA key probada (se salta
 *                            el pool de NVIDIA_API_KEY_1..5).
 *   NVIDIA_VISION_TIMEOUT    Timeout de respuesta por intento, en segundos
 *                            (default: 30 — igual que el resto de llamadas
 *                            cloud de NvidiaBrainClient::DEFAULT_TIMEOUT_CLOUD).
 *
 * Para volver a un servidor local (Ollama/vLLM), basta con definir
 * NVIDIA_VISION_BASE_URL a esa URL — el pool de modelos/keys de NIM se
 * ignora automáticamente si NVIDIA_VISION_BASE_URL no es la de NIM.
 *
 * Uso:
 *   $registry->registerTool(new LeerVoucherOcrTool());
 */
final class LeerVoucherOcrTool implements AgentToolInterface
{
    /** MIME de imágenes aceptadas. */
    private const MIME_VALIDOS = ['image/png', 'image/jpeg', 'image/webp', 'image/bmp'];

    /** Tamaño máximo de imagen (15 MB). */
    private const TAMANO_MAX_BYTES = 15 * 1024 * 1024;

    /** NVIDIA NIM Cloud — mismo proveedor que ara_vision.py. */
    private const NIM_BASE_URL = 'https://integrate.api.nvidia.com';

    /** Pool de modelos de visión NIM confirmados en vivo (GET /v1/models, 17/08). */
    private const MODELOS_VISION_NIM = [
        'meta/llama-3.2-11b-vision-instruct',
        'meta/llama-3.2-90b-vision-instruct',
    ];

    /** Códigos HTTP que disparan rotación de API key (no de modelo). */
    private const HTTP_ROTAR_KEY = [429, 401, 403, 503];

    private const PROMPT_EXTRACCION = <<<'PROMPT'
Eres un OCR de comprobantes bancarios. Analiza la imagen del voucher/recibo de
pago y devuelve SOLAMENTE un JSON válido, sin texto adicional ni marcas de
bloque, con esta estructura exacta:
{"banco": "nombre del banco o null", "referencia": "número de referencia o null",
"monto": 1234.56, "fecha": "AAAA-MM-DD", "tipo_pago": "efectivo"|"transferencia"|"punto_de_venta"|null}
Si un dato no es legible en la imagen usa null. El monto debe ser un número.
PROMPT;

    public function getName(): string
    {
        return 'leer_voucher_ocr';
    }

    public function getDescription(): string
    {
        return 'Lee un comprobante de pago o voucher bancario desde una imagen '
             . 'local usando el modelo multimodal de visión y extrae en JSON: '
             . 'banco, referencia, monto y fecha. Úsala cuando el usuario '
             . 'adjunte un recibo o voucher (ruta del archivo en el servidor) '
             . 'para conciliar un pago. Parámetro: ruta_imagen (ruta absoluta '
             . 'del archivo de imagen, PNG/JPEG/WebP/BMP, máx. 15 MB).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'ruta_imagen' => [
                    'type'        => 'string',
                    'description' => 'Ruta absoluta del archivo de imagen del voucher en el servidor.',
                ],
                'idioma' => [
                    'type'        => 'string',
                    'enum'        => ['es', 'en'],
                    'description' => 'Idioma del comprobante (default: es).',
                ],
            ],
            'required' => ['ruta_imagen'],
        ];
    }

    /**
     * {@inheritDoc}
     */
    public function execute(array $arguments, array $contexto): array
    {
        $ruta = trim((string) ($arguments['ruta_imagen'] ?? ''));
        if ($ruta === '') {
            return ['success' => false, 'error' => 'Parámetro "ruta_imagen" es obligatorio.'];
        }

        // ── 1) Validación del archivo de imagen ───────────────────────────────
        $rutaReal = realpath($ruta);
        if ($rutaReal === false) {
            return ['success' => false, 'error' => 'La imagen no existe en el servidor: ' . $ruta];
        }
        if (!is_file($rutaReal) || !is_readable($rutaReal)) {
            return ['success' => false, 'error' => 'La ruta no es un archivo legible: ' . $rutaReal];
        }
        $tamano = @filesize($rutaReal);
        if ($tamano === false || $tamano > self::TAMANO_MAX_BYTES) {
            return ['success' => false, 'error' => 'La imagen supera el tamaño máximo permitido (15 MB).'];
        }

        $mime = $this->detectarMime($rutaReal);
        if ($mime === null) {
            return [
                'success' => false,
                'error'   => 'Formato de imagen no soportado. Use: PNG, JPEG, WebP o BMP.',
            ];
        }

        $binario = @file_get_contents($rutaReal);
        if ($binario === false) {
            return ['success' => false, 'error' => 'No se pudo leer el archivo de imagen.'];
        }
        $dataUri = 'data:' . $mime . ';base64,' . base64_encode($binario);

        // ── 2) Llamada al modelo multimodal de visión (NVIDIA NIM primario) ───
        $baseUrl = self::env('NVIDIA_VISION_BASE_URL', self::NIM_BASE_URL);
        $esNim = rtrim($baseUrl, '/') === rtrim(self::NIM_BASE_URL, '/');
        $timeout = max(1, (int) self::env('NVIDIA_VISION_TIMEOUT', '30'));

        $modeloForzado = self::env('NVIDIA_VISION_MODEL', '');
        $keyForzada = self::env('NVIDIA_VISION_API_KEY', '');

        if ($esNim && $modeloForzado === '' && $keyForzada === '') {
            // Pool completo: 2 modelos NIM confirmados x hasta 5 API keys.
            $modelos = self::MODELOS_VISION_NIM;
            $keys = $this->poolApiKeys();
        } else {
            // NVIDIA_VISION_MODEL/NVIDIA_VISION_API_KEY definidas explícitamente
            // (o baseUrl apunta a un servidor NO-NIM, ej. Ollama/vLLM local):
            // se respeta la config manual, sin pool ni rotación.
            $modelos = [$modeloForzado !== '' ? $modeloForzado : ($esNim ? self::MODELOS_VISION_NIM[0] : 'llava')];
            $keys = [$keyForzada];
        }

        $payloadBase = [
            'messages'    => [
                [
                    'role'    => 'user',
                    'content' => [
                        ['type' => 'text', 'text' => self::PROMPT_EXTRACCION],
                        ['type' => 'image_url', 'image_url' => ['url' => $dataUri]],
                    ],
                ],
            ],
            'stream'      => false,
            'temperature' => 0.1,
            'max_tokens'  => 512,
        ];

        $res = null;
        $ultimoError = 'ningún modelo/key configurado';
        foreach ($modelos as $modelo) {
            foreach ($keys as $apiKey) {
                $client = new NvidiaBrainClient(
                    baseUrl: $baseUrl,
                    apiKey: $apiKey !== '' ? $apiKey : null,
                    model: $modelo,
                    timeoutS: $timeout
                );
                $intento = $client->request('POST', '/chat/completions', ['model' => $modelo] + $payloadBase);
                if ($intento['success']) {
                    $res = $intento;
                    break 2;
                }
                $ultimoError = $intento['error'] ?? 'error desconocido';
                $codigo = (int) ($intento['http_status'] ?? 0);
                if (!in_array($codigo, self::HTTP_ROTAR_KEY, true)) {
                    // Error NO relacionado con la key (404 modelo inexistente,
                    // 500, timeout) — probar otra key del mismo modelo no
                    // ayudaría; se pasa directo al siguiente modelo del pool.
                    continue 2;
                }
                // 429/401/403/503: puede ser específico de esta key — se
                // prueba la siguiente key antes de descartar el modelo.
            }
        }

        if ($res === null) {
            return [
                'success' => false,
                'error'   => 'El modelo de visión no respondió: ' . $ultimoError,
            ];
        }

        // ── 3) Extracción y parseo del JSON estructurado ──────────────────────
        $contenido = $res['data']['choices'][0]['message']['content'] ?? null;
        if ($contenido === null) {
            return ['success' => false, 'error' => 'El modelo de visión no devolvió contenido OCR.'];
        }
        if (is_array($contenido)) {
            $piezas = [];
            foreach ($contenido as $parte) {
                if (is_array($parte) && isset($parte['text'])) {
                    $piezas[] = (string) $parte['text'];
                }
            }
            $contenido = implode('', $piezas);
        }
        $contenido = trim((string) $contenido);

        $json = $this->extraerJson($contenido);
        if ($json === null) {
            return [
                'success' => false,
                'error'   => 'El modelo de visión devolvió una respuesta que no pudo interpretarse como JSON: ' . substr($contenido, 0, 200),
            ];
        }

        $voucher = [
            'banco'     => $json['banco'] ?? null,
            'referencia'=> $json['referencia'] ?? null,
            'monto'     => isset($json['monto']) && is_numeric($json['monto']) ? (float) $json['monto'] : null,
            'fecha'     => $json['fecha'] ?? null,
            'tipo_pago' => $json['tipo_pago'] ?? null,
        ];

        return [
            'success' => true,
            'data'    => [
                'voucher'     => $voucher,
                'texto_ocr'   => substr($contenido, 0, 500),
                'imagen'      => basename($rutaReal),
                'modelo'      => $modelo,
            ],
            'card'    => CardBuilder::iniciar('🧾', 'VOUCHER LEÍDO (OCR)')
                ->campo('BANCO', (string) ($voucher['banco'] ?? 'N/D'))
                ->campo('REFERENCIA', (string) ($voucher['referencia'] ?? 'N/D'))
                ->campo('MONTO', $voucher['monto'], 'moneda')
                ->campo('FECHA', (string) ($voucher['fecha'] ?? 'N/D'))
                ->campo('TIPO PAGO', (string) ($voucher['tipo_pago'] ?? 'N/D'))
                ->campo('IMAGEN', basename($rutaReal))
                ->campo('MODELO', $modelo)
                ->footer('Fuente: visión local ' . $modelo)
                ->tarjeta(),
        ];
    }

    /**
     * Detecta el MIME con finfo; fallback a extensión si finfo no está.
     */
    private function detectarMime(string $ruta): ?string
    {
        if (function_exists('finfo_open')) {
            $finfo = finfo_open(FILEINFO_MIME_TYPE);
            if ($finfo !== false) {
                $mime = finfo_file($finfo, $ruta);
                finfo_close($finfo);
                if (is_string($mime) && in_array($mime, self::MIME_VALIDOS, true)) {
                    return $mime;
                }
            }
        }
        $ext = strtolower(pathinfo($ruta, PATHINFO_EXTENSION));
        $porExtension = [
            'png'  => 'image/png',
            'jpg'  => 'image/jpeg',
            'jpeg' => 'image/jpeg',
            'webp'=> 'image/webp',
            'bmp'  => 'image/bmp',
        ];
        return $porExtension[$ext] ?? null;
    }

    /**
     * Pool de API keys NVIDIA NIM (NVIDIA_API_KEY_1..5), en el orden en que
     * estén definidas. Filtra vacías/inválidas (prefijo 'nvapi-' esperado).
     * Si ninguna está configurada, retorna [''] para que el llamador falle
     * con un mensaje claro en vez de romper el foreach.
     *
     * @return list<string>
     */
    private function poolApiKeys(): array
    {
        $keys = [];
        for ($i = 1; $i <= 5; $i++) {
            $k = trim((string) (getenv("NVIDIA_API_KEY_{$i}") ?: ''));
            if ($k !== '' && str_starts_with($k, 'nvapi-') && !in_array($k, $keys, true)) {
                $keys[] = $k;
            }
        }
        return $keys !== [] ? $keys : [''];
    }

    /**
     * Extrae el primer objeto JSON del texto del modelo (tolerante a bloques).
     */
    private function extraerJson(string $texto): ?array
    {
        $limpio = preg_replace('/^```(?:json)?\s*/i', '', $texto);
        $limpio = preg_replace('/\s*```$/', '', (string) $limpio);
        if ($limpio === null) {
            return null;
        }
        $inicio = strpos($limpio, '{');
        $final  = strrpos($limpio, '}');
        if ($inicio === false || $final === false || $final <= $inicio) {
            return null;
        }
        $trozo = substr($limpio, $inicio, $final - $inicio + 1);
        $decodificado = json_decode($trozo, true);
        if (!is_array($decodificado)) {
            return null;
        }
        return $decodificado;
    }

    /**
     * Lee una variable de entorno con fallback y tolera valores vacíos.
     */
    private static function env(string $clave, string $default): string
    {
        $valor = getenv($clave);
        if (is_string($valor) && trim($valor) !== '') {
            return trim($valor);
        }
        return $default;
    }
}

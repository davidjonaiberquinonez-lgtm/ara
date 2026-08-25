<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain;

/**
 * Orquestador de alto nivel "ARA BRAIN" — CLIENTE UNIFICADO DE IA con
 * POOL DE PROVEEDORES Y FAILOVER.
 *
 * Envuelve a NvidiaBrainClient y expone el pool de proveedores:
 *
 *   PROVEEDOR 1 (PRIMARIO OBLIGATORIO - LOCAL)
 *      Ollama http://localhost:11434/v1 — qwen2.5-coder:3b
 *      (3s conexión / 25s respuesta; Fast-Fail local)
 *           │  conexión rechazada, timeout o error de servidor
 *           ▼
 *   PROVEEDOR 2 (SECUNDARIO / FALLBACK CLOUD)
 *      NVIDIA NIM https://integrate.api.nvidia.com/v1
 *      (5s conexión / 20s respuesta)
 *
 * CIRCUIT BREAKER de sesión: si el puerto 11434 no responde o NVIDIA devuelve
 * HTTP 529/503, el endpoint se blacklista ($blacklistedEndpoints estático) y
 * no se vuelve a intentar en la misma ejecución PHP.
 *
 * Configuración:
 *  - Sin baseUrl (o 'ollama'/'local'): pool Ollama → NVIDIA.
 *  - baseUrl explícita (URL): modo legacy de un solo endpoint (el
 *    comportamiento histórico de NvidiaBrain/NvidiaBrainClient se conserva).
 *  - Env: OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT_S,
 *    OLLAMA_CONNECT_TIMEOUT_S, NVIDIA_BRAIN_BASE_URL, NVIDIA_BRAIN_API_KEY,
 *    NVIDIA_BRAIN_MODEL, NVIDIA_BRAIN_MODELS.
 *
 * Retorno (contrato idéntico al del cliente):
 *   [
 *     'success' => bool,
 *     'data'    => array,
 *     'error'   => ?string,
 *     'model'   => string,           // modelo que respondió
 *     'provider' => string,          // 'ollama' | 'nvidia' | 'legacy'
 *     'failover_history' => array,   // proveedores fallidos (si hubo rotación)
 *   ]
 */
final class NvidiaBrain
{
    /** @var NvidiaBrainClient Cliente HTTP de bajo nivel. */
    private NvidiaBrainClient $client;

    /** @var list<string> Pool de modelos resuelto. */
    private array $modelPool;

    /** @var int Índice de rotación persistente (Round-Robin global). */
    private int $poolIndex = 0;

    /** @var bool Logs de depuración en error_log(). */
    private bool $debug;

    /**
     * @param string|null          $baseUrl        Alias de proveedor o URL base.
     *                                             null|''|'ollama'|'local' → POOL
     *                                             (Ollama local PRIMARIO + NVIDIA
     *                                             NIM fallback). 'nvidia'|'nim' →
     *                                             solo NVIDIA. Cualquier URL →
     *                                             modo legacy de un solo endpoint.
     * @param string|null          $apiKey         Bearer token si se exige.
     * @param string|null          $model          Modelo primario (opcional).
     * @param list<string>|null    $modelPool      Pool de modelos (legacy; null = env/código).
     * @param int                  $timeoutS       Timeout de respuesta (s), default 90.
     * @param int                  $connectTimeout Timeout de conexión (s), default 15.
     * @param int                  $maxRetries     Reintentos rápidos por modelo (default 3).
     * @param bool                 $debug          Logs en error_log().
     */
    public function __construct(
        ?string $baseUrl = null,
        ?string $apiKey = null,
        ?string $model = null,
        ?array $modelPool = null,
        int $timeoutS = NvidiaBrainClient::DEFAULT_TIMEOUT,
        int $connectTimeout = NvidiaBrainClient::DEFAULT_CONNECT_TIMEOUT,
        int $maxRetries = NvidiaBrainClient::DEFAULT_MAX_RETRIES,
        bool $debug = false
    ) {
        $this->client = new NvidiaBrainClient(
            baseUrl: $baseUrl,
            apiKey: $apiKey,
            model: $model,
            timeoutS: $timeoutS,
            connectTimeout: $connectTimeout,
            maxRetries: $maxRetries,
            debug: $debug
        );
        $this->modelPool = $modelPool ?? $this->client->modelPool();
        $this->debug = $debug;
    }

    /**
     * Chat completion con Tool Calling y failover de PROVEEDORES.
     *
     * El pool se resuelve dentro del cliente: Ollama local (qwen2.5-coder:3b)
     * como primario obligatorio y NVIDIA NIM (meta/llama-3.1-8b-instruct)
     * como fallback de emergencia. Los $messages y $tools se reenvían intactos
     * entre proveedores: el hilo de la conversación y el estado del Tool
     * Calling se conservan.
     *
     * @param array<int,array<string,mixed>> $messages Historial de mensajes.
     * @param array<int,array<string,mixed>> $tools    Definiciones de tools.
     * @param string|array|null              $toolChoice 'auto' | 'none' | array.
     * @param array<string,mixed>            $overrides Parámetros extra.
     *
     * @return array<string,mixed> Contrato {success, data, error, model,
     *                             provider, failover_history}.
     */
    public function chatCompletions(array $messages, array $tools = [], string|array|null $toolChoice = 'auto', array $overrides = []): array
    {
        return $this->client->chatCompletions($messages, $tools, $toolChoice, $overrides);
    }

    /**
     * Siguiente modelo del pool (Round-Robin) para rotación manual.
     */
    public function siguienteModelo(): string
    {
        $modelo = $this->modelPool[$this->poolIndex % count($this->modelPool)];
        ++$this->poolIndex;
        return $modelo;
    }

    /** @return list<string> Pool de modelos resuelto. */
    public function getModelPool(): array
    {
        return $this->modelPool;
    }

    /** @return NvidiaBrainClient Cliente de bajo nivel (diagnóstico/composición). */
    public function getClient(): NvidiaBrainClient
    {
        return $this->client;
    }

    /**
     * Pool de proveedores resuelto (sin API keys): Ollama Local (primario) y
     * NVIDIA NIM Cloud (fallback), con su estado de blacklist de sesión.
     *
     * @return list<array<string,mixed>>
     */
    public function getProviders(): array
    {
        return $this->client->getProviders();
    }

    /** @return string|null Proveedor ('ollama'|'nvidia'|'legacy') que respondió. */
    public function getActiveProvider(): ?string
    {
        return $this->client->getActiveProvider();
    }

    /** @return list<string> Endpoints blacklisted en la sesión. */
    public function getBlacklistedEndpoints(): array
    {
        return NvidiaBrainClient::getBlacklistedEndpoints();
    }

    /** @return string URL base configurada. */
    public function getBaseUrl(): string
    {
        return $this->client->getBaseUrl();
    }

    /** @return string|null Modelo primario configurado en el constructor. */
    public function getPrimaryModel(): ?string
    {
        return $this->client->getPrimaryModel();
    }

    /** Log de depuración condicionado. */
    private function log(string $message): void
    {
        if ($this->debug) {
            error_log('[NvidiaBrain] ' . $message);
        }
    }
}

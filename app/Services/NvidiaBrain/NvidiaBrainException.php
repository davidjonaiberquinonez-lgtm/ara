<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain;

/**
 * Excepción de dominio del cliente/agente NVIDIA Brain.
 *
 * Provee contexto rico para diagnóstico y una política de reintento clara:
 * los errores transitorios (timeout cURL, 408, 429, 5xx) son marcados como
 * "retryable" y el cliente los reintenta; los errores de programación o de
 * payload no se reintentan.
 */
class NvidiaBrainException extends \RuntimeException
{
    /** @var int|null Código HTTP de la respuesta (null en fallos de red). */
    protected ?int $httpStatus;

    /** @var bool Marca si el error es candidato a reintento. */
    protected bool $retryable;

    /** @var float Segundos sugeridos antes de reintentar (backoff base). */
    protected float $retryDelayS;

    public function __construct(string $message, ?int $httpStatus = null, bool $retryable = false, float $retryDelayS = 1.0)
    {
        parent::__construct($message, $httpStatus ?? 0);
        $this->httpStatus = $httpStatus;
        $this->retryable = $retryable;
        $this->retryDelayS = max(0.0, $retryDelayS);
    }

    /** Fallo de red/cURL (sin respuesta HTTP). */
    public static function connection(string $message, int $curlErrno): self
    {
        // CURLE_OPERATION_TIMEDOUT (28) / CURLE_COULDNT_CONNECT (7) son
        // transitorios típicos en servidores locales recargados.
        $retryable = in_array($curlErrno, [7, 28, 35, 55, 56], true);
        return new self($message, null, $retryable, 1.0);
    }

    /** Respuesta HTTP no satisfactoria. */
    public static function http(string $message, int $status): self
    {
        // 408/429 = rate limit, 529 = service temporarily overloaded (NVIDIA
        // NIM), cualquier 5xx = error transitorio del servidor.
        $retryable = in_array($status, [408, 429, 529], true) || ($status >= 500 && $status <= 599);
        // Si el servidor manda Retry-After, respetarlo.
        $delay = 1.0;
        if ($status === 429) {
            $delay = 3.0;
        }
        return new self($message, $status, $retryable, $delay);
    }

    /** Payload/JSON inválido: no se reintenta. */
    public static function payload(string $message, ?int $status = null): self
    {
        return new self($message, $status, false, 0.0);
    }

    /** Error de autorización: no se reintenta. */
    public static function permission(string $message): self
    {
        return new self($message, 403, false, 0.0);
    }

    public function getHttpStatus(): ?int
    {
        return $this->httpStatus;
    }

    public function isRetryable(): bool
    {
        return $this->retryable;
    }

    public function getRetryDelayS(): float
    {
        return $this->retryDelayS;
    }
}

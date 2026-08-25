<?php

declare(strict_types=1);

namespace App\Services;

/**
 * LegacyChequeoService — Autochequeo de notas pequeñas (< 3 ítems) contra el
 * sistema Legacy (gestion.php).
 *
 * Cierra el ciclo que el formulario manual de la mesa de chequeo ejecuta al
 * guardar una nota "por chequear":
 *
 *   1. POST {baseUrl}/index.php    → nota + consulta="Buscar"; captura PHPSESSID.
 *   2. GET  {baseUrl}/registro.php → con la cookie de sesión; extrae los campos
 *                                    ocultos 'monto' y 'descp' del formulario.
 *   3. POST {baseUrl}/registro.php → con la cookie + responsable "Mesa 0 - User
 *                                    {idUsuario}", monto, descp y registro="Guardar".
 *   4. Validación: la respuesta HTML debe contener "Nota Lista para procesar"
 *      o el audio "<audio src="sond/finaliza.mp"".
 *
 * Si la confirmación es exitosa, la nota deja de aparecer en la lista
 * "por chequear" de gestion.php (criterio de aceptación) y se devuelve el
 * estado local "COMPLETADO_AUTOCHEQUEO" para que el llamador (ARA) lo
 * persista. En fallo se devuelve el HTML/HTTP truncado para log, sin lanzar
 * excepciones: un timeout o una caída del Legacy NUNCA congela la app.
 *
 * Diseño:
 *  - PHP nativo (ext-curl + ext-json), sin frameworks.
 *  - URL base inyectable por constructor (default: servidor Legacy real).
 *  - Cookie de sesión preservada con cookie-jar temporal (PHPSESSID).
 *  - Regla de negocio < 3 ítems evaluada por debeAutochequear()/autochequear().
 */
final class LegacyChequeoService
{
    /** Umbral de la regla de negocio: notas con MENOS de 3 ítems se autochequean. */
    public const UMBRAL_ITEMS = 3;

    /** Estado local que el llamador debe persistir tras un autochequeo exitoso. */
    public const ESTADO_COMPLETADO_AUTOCHEQUEO = 'COMPLETADO_AUTOCHEQUEO';

    /** Prefijo del responsable inyectado en registro.php. */
    public const RESPONSABLE_PREFIJO = 'Mesa 0 - User ';

    /** Marcadores de éxito en la respuesta HTML de registro.php. */
    private const MARCADOR_OK_EXACTO = 'Nota Lista para procesar';
    private const MARCADOR_OK_AUDIO  = '<audio src="sond/finaliza.mp"';

    /** @var string URL base del Legacy (sin '/' final). */
    private string $baseUrl;

    /** @var int Timeout total de cada petición HTTP en segundos. */
    private int $timeoutS;

    /** @var callable|null Logger opcional (mensaje, array). */
    private $logger;

    public function __construct(string $baseUrl = 'http://192.168.4.148:8000', int $timeoutS = 10, ?callable $logger = null)
    {
        $this->baseUrl = rtrim($baseUrl, '/');
        $this->timeoutS = max(1, $timeoutS);
        $this->logger = $logger;
    }

    /** @param callable(string $mensaje, array $contexto): void $logger */
    public function setLogger(callable $logger): void
    {
        $this->logger = $logger;
    }

    /** Regla de negocio: ¿la nota merece autochequeo (< 3 ítems)? */
    public function debeAutochequear(int $totalItems): bool
    {
        return $totalItems < self::UMBRAL_ITEMS;
    }

    /**
     * Autochequea una nota pequeña contra el Legacy.
     *
     * @param string      $numNota    N° de nota/factura a registrar.
     * @param int|string  $idUsuario  ID del preparador activo (inyectado por ARA).
     * @param int         $totalItems Cantidad total de ítems de la nota.
     *
     * @return array{
     *   ok: bool,
     *   es_nota_menor_3: bool,
     *   estado?: string,
     *   nota?: string,
     *   responsable?: string,
     *   monto?: string,
     *   descp?: string,
     *   pasos?: array,
     *   error?: string,
     *   http?: int,
     *   html?: string,
     * }
     */
    public function autochequear(string $numNota, int|string $idUsuario, int $totalItems = -1): array
    {
        $numNota = trim((string) $numNota);
        if ($numNota === '') {
            return ['ok' => false, 'es_nota_menor_3' => false, 'error' => 'Número de nota vacío.'];
        }

        // Regla de negocio: total_items < 3 dispara el autochequeo directo.
        if ($totalItems >= 0 && !$this->debeAutochequear($totalItems)) {
            return [
                'ok'              => false,
                'es_nota_menor_3' => false,
                'error'           => sprintf(
                    'La nota %s tiene %d ítems (>= %d): requiere validación manual en mesa.',
                    $numNota,
                    $totalItems,
                    self::UMBRAL_ITEMS
                ),
            ];
        }

        $responsable = self::RESPONSABLE_PREFIJO . $idUsuario;
        $pasos = [];
        $cookieJar = tempnam(sys_get_temp_dir(), 'legacy_cheq_');
        if ($cookieJar === false) {
            return ['ok' => false, 'es_nota_menor_3' => true, 'error' => 'No se pudo crear el cookie-jar temporal.'];
        }

        try {
            // Paso 1 — POST index.php (búsqueda de la nota, abre la sesión PHPSESSID).
            $htmlIndex = $this->post('/index.php', [
                'nota'     => $numNota,
                'consulta' => 'Buscar',
            ], $cookieJar);
            $pasos[] = ['paso' => 1, 'url' => '/index.php', 'http' => 200, 'chars' => strlen($htmlIndex)];

            // Paso 2 — GET registro.php con la cookie de sesión.
            $htmlRegistro = $this->get('/registro.php', $cookieJar);
            $pasos[] = ['paso' => 2, 'url' => '/registro.php', 'http' => 200, 'chars' => strlen($htmlRegistro)];

            // Extraer campos ocultos del formulario (monto, descp).
            $monto = $this->extraerCampo($htmlRegistro, 'monto');
            $descp = $this->extraerCampo($htmlRegistro, 'descp');

            // Paso 3 — POST registro.php con el responsable "Mesa 0 - User {id}".
            $htmlFinal = $this->post('/registro.php', [
                'responsable' => $responsable,
                'monto'       => $monto,
                'descp'       => $descp,
                'registro'    => 'Guardar',
            ], $cookieJar);
            $pasos[] = ['paso' => 3, 'url' => '/registro.php', 'http' => 200, 'chars' => strlen($htmlFinal)];

            // Paso 4 — Validación de la confirmación Legacy.
            if ($this->esConfirmacionExitosa($htmlFinal)) {
                $resultado = [
                    'ok'              => true,
                    'es_nota_menor_3' => true,
                    'estado'          => self::ESTADO_COMPLETADO_AUTOCHEQUEO,
                    'nota'            => $numNota,
                    'responsable'     => $responsable,
                    'monto'           => $monto,
                    'descp'           => $descp,
                    'pasos'           => $pasos,
                ];
                $this->log('Autochequeo < ' . self::UMBRAL_ITEMS . ' ítems confirmado en Legacy.', $resultado);
                return $resultado;
            }

            $resultado = [
                'ok'              => false,
                'es_nota_menor_3' => true,
                'estado'          => 'FALLO_AUTOCHEQUEO',
                'nota'            => $numNota,
                'responsable'     => $responsable,
                'error'           => 'El Legacy no confirmó el autochequeo (no se encontraron los marcadores de éxito).',
                'html'            => $this->recortar($htmlFinal),
                'pasos'           => $pasos,
            ];
            $this->log('Autochequeo NO confirmado por el Legacy.', $resultado);
            return $resultado;
        } catch (\Throwable $e) {
            $resultado = [
                'ok'              => false,
                'es_nota_menor_3' => true,
                'estado'          => 'ERROR_AUTOCHEQUEO',
                'nota'            => $numNota,
                'responsable'     => $responsable,
                'error'           => $e->getMessage(),
                'http'            => $this->ultimoHttp,
                'html'            => $this->ultimoHtml,
                'pasos'           => $pasos,
            ];
            $this->log('Excepción en el autochequeo Legacy.', $resultado);
            return $resultado;
        } finally {
            if (is_file($cookieJar)) {
                @unlink($cookieJar);
            }
        }
    }

    /** @var int Último código HTTP observado (para reportes de error). */
    private int $ultimoHttp = 0;

    /** @var string Último cuerpo HTML truncado observado (para reportes de error). */
    private string $ultimoHtml = '';

    /**
     * POST form-urlencoded manteniendo la sesión (cookie-jar) del Legacy.
     *
     * @param array<string,string> $campos
     *
     * @throws \RuntimeException Fallo de red, timeout o HTTP no-2xx.
     */
    private function post(string $path, array $campos, string $cookieJar): string
    {
        return $this->curl($path, $campos, $cookieJar);
    }

    /** GET manteniendo la sesión (cookie-jar) del Legacy. */
    private function get(string $path, string $cookieJar): string
    {
        return $this->curl($path, null, $cookieJar);
    }

    /**
     * Ejecuta la petición cURL con el cookie-jar compartido (PHPSESSID).
     *
     * @param array<string,string>|null $campos null = GET, array = POST.
     *
     * @throws \RuntimeException curl_init falló, curl_exec falló, o HTTP no-2xx.
     */
    private function curl(string $path, ?array $campos, string $cookieJar): string
    {
        $ch = curl_init();
        if ($ch === false) {
            throw new \RuntimeException('curl_init() falló: extensión cURL no disponible.');
        }

        $opciones = [
            CURLOPT_URL            => $this->baseUrl . $path,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_FOLLOWLOCATION => true,
            CURLOPT_MAXREDIRS      => 3,
            CURLOPT_CONNECTTIMEOUT => 5,
            CURLOPT_TIMEOUT        => $this->timeoutS,
            CURLOPT_COOKIEJAR      => $cookieJar,
            CURLOPT_COOKIEFILE     => $cookieJar,
            CURLOPT_USERAGENT      => 'ARA-LegacyChequeoService/1.0',
        ];

        if ($campos !== null) {
            $opciones[CURLOPT_POST] = true;
            $opciones[CURLOPT_POSTFIELDS] = http_build_query($campos);
        }

        curl_setopt_array($ch, $opciones);
        $raw = curl_exec($ch);
        $http = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
        $error = curl_error($ch);
        curl_close($ch);

        $this->ultimoHttp = $http;
        $this->ultimoHtml = $this->recortar((string) $raw);

        if ($raw === false) {
            throw new \RuntimeException(sprintf(
                'Error cURL hacia %s%s: %s',
                $this->baseUrl,
                $path,
                $error
            ));
        }
        if ($http < 200 || $http >= 300) {
            throw new \RuntimeException(sprintf(
                'El Legacy respondió HTTP %d en %s.',
                $http,
                $this->baseUrl . $path
            ));
        }

        return (string) $raw;
    }

    /** Extrae el value de un <input> por su atributo name (tolerante a mayúsculas). */
    private function extraerCampo(string $html, string $name): string
    {
        if (!preg_match('/<input\b[^>]*\bname=["\']' . preg_quote($name, '/') . '["\'][^>]*>/i', $html, $m)) {
            return '';
        }
        if (!preg_match('/\bvalue=["\']([^"\']*)["\']/i', $m[0], $v)) {
            return '';
        }
        return html_entity_decode($v[1], ENT_QUOTES | ENT_HTML5, 'UTF-8');
    }

    /** El Legacy confirmó el guardado: marcador exacto o audio de finalización. */
    private function esConfirmacionExitosa(string $html): bool
    {
        return stripos($html, self::MARCADOR_OK_EXACTO) !== false
            || stripos($html, self::MARCADOR_OK_AUDIO) !== false;
    }

    /** Trunca el HTML para logs (evita volcados gigantes en la bitácora). */
    private function recortar(string $html, int $max = 800): string
    {
        $limpio = trim(strip_tags((string) $html));
        $limpio = preg_replace('/\s+/u', ' ', $limpio) ?? $limpio;
        if (mb_strlen($limpio) <= $max) {
            return $limpio;
        }
        return mb_substr($limpio, 0, $max) . '…';
    }

    /** Log opcional (best-effort, nunca lanza). */
    private function log(string $mensaje, array $contexto): void
    {
        if ($this->logger === null) {
            return;
        }
        try {
            ($this->logger)($mensaje, $contexto);
        } catch (\Throwable $e) {
            // El logger jamás interrumpe el flujo de autochequeo.
        }
    }
}

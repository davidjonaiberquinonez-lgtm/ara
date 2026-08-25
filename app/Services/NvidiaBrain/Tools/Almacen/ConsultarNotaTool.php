<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use App\Services\NvidiaBrain\Tools\Common\Envelope;
use PDO;
use PDOException;

require_once __DIR__ . '/../Common/Envelope.php';

/**
 * Herramienta "consultar_nota": obtiene encabezado y renglones de una nota
 * de entrega del ERP ARA por su número (ej. "72160754" o "A0467959").
 *
 * Fuente primaria (directiva CTO v4.11): MySQL legacy — rep_not (cabecera:
 * estatus, fechas, ruta) + gestion (chequeo VERIFICADA, preparador/chequeador,
 * horas). Solo lectura; SIEMPRE se cierra la conexión.
 * Fuente secundaria (fallback y detalle): API ARA (/api/notas/lista y
 * /api/notas/detalle) para renglones y datos de cliente. Si el API no
 * responde y el legacy sí tiene la nota, se responde igual con la cabecera.
 *
 * Diseño:
 *  - PHP nativo (ext-curl + ext-json + PDO), sin dependencias de frameworks.
 *  - Nunca lanza excepciones por "nota no encontrada": devuelve ok=false con
 *    mensaje claro para que el LLM lo traduzca al usuario.
 */
final class ConsultarNotaTool implements AgentToolInterface
{
    use AlmacenDbTrait;

    /** @var string URL base del API del ERP ARA (sin '/' final). */
    private string $baseUrl;

    /** @var int Timeout total de cada petición HTTP en segundos. */
    private int $timeoutS;

    /**
     * BUG corregido (a pedido del usuario: "revisa por qué la api de mysql
     * está caído" — reportado con card mostrando "ORIGEN: mysql_legacy (API
     * caído)"): el API SÍ estaba respondiendo (HTTP 200 en /api/notas/lista),
     * solo que la nota consultada no aparecía en esa lista — el código
     * confundía "sin match" con "API caído" porque ambos casos dejaban
     * $nota === null. Este flag distingue "el API respondió" (aunque sin
     * coincidencia) de "el API no respondió" (excepción de red/HTTP).
     */
    private bool $apiRespondio = false;

    public function __construct(string $baseUrl = '', int $timeoutS = 10)
    {
        // La URL base es inyectable; si no se pasa, se lee la env ARA_ERP_URL
        // (la define el server ARA al invocar el runner; evita 127.0.0.1:5000
        // cuando el puerto loopback está ocupado por otro proceso).
        if ($baseUrl === '') {
            $envUrl = getenv('ARA_ERP_URL');
            // Fallback actualizado (24/08): ara_server.py se movió de 5000 a
            // 4050 (PC-NVR.exe tomaba el 5000 en esta máquina) — con el
            // fallback viejo, si ARA_ERP_URL no llegaba a estar seteada en el
            // proceso que invoca esta tool, buscarNota() apuntaba a un
            // puerto muerto en silencio: la nota SÍ existía, pero el API
            // nunca respondía → cabecera/renglones en N/A y "(API caído)"
            // aunque el legacy MySQL (preparador/embalador) sí funcionara.
            $baseUrl = is_string($envUrl) && trim($envUrl) !== '' ? trim($envUrl) : 'http://127.0.0.1:4050';
        }
        $this->baseUrl = rtrim($baseUrl, '/');
        $this->timeoutS = max(1, $timeoutS);
    }

    public function getName(): string
    {
        return 'consultar_nota';
    }

    public function getDescription(): string
    {
        return 'Obtiene los datos, cliente, estatus y renglones de una nota de '
             . 'entrega/despacho por su número. Úsala cuando el usuario pida '
             . 'consultar una nota (ej. "consulta la nota 72160754", "dame datos '
             . 'de la nota X") o escriba un número aislado de 7-8 dígitos. '
             . 'Parámetro: num_nota (obligatorio).';
    }

    public function getParameters(): array
    {
        return [
            'type'       => 'object',
            'properties' => [
                'num_nota' => [
                    'type'        => 'string',
                    'description' => 'Número de la nota de entrega a consultar (ej. "A0467959").',
                ],
            ],
            'required'   => ['num_nota'],
        ];
    }

    /**
     * Ejecuta la herramienta con los argumentos del modelo y el contexto de
     * sesión inyectado por el motor.
     *
     * @param array $arguments Argumentos JSON decodificados a array asociativo.
     * @param array $contexto  Contexto de sesión (usuario_id, nombre_usuario,
     *                         rol, modulo, ruta_id, tool_call_id).
     *
     * @return array Resultado serializable: ['ok' => true, ...datos] en éxito
     *               o ['ok' => false, 'error' => '...'] en fallo controlado.
     *
     * @throws \RuntimeException Fallo de red/HTTP; el ToolRegistry lo captura
     *                           y lo convierte en error seguro para el LLM.
     */
    public function execute(array $arguments, array $contexto): array
    {
        $numNota = trim((string) ($arguments['num_nota'] ?? ''));
        if ($numNota === '') {
            // Captura automática de argumentos (v4.8): si el modelo/frontend no
            // envió 'num_nota', se reconstruye el texto crudo desde cualquier
            // clave (raw_input, text, array posicional 0/1/2...) y se extrae la
            // primera secuencia numérica de 7-8 dígitos (el formato de notas).
            $rawText = self::reunirTexto($arguments);
            if (preg_match('/([0-9]{7,8})/', $rawText, $m)) {
                $numNota = $m[1];
            } else {
                $numNota = $rawText;
            }
            $numNota = trim($numNota);
        }
        if ($numNota === '') {
            return ['ok' => false, 'error' => 'El parámetro "num_nota" es obligatorio.'];
        }

        // 1) FUENTE PRIMARIA (CTO v4.11): MySQL legacy rep_not + gestion.
        //    Solo lectura, conexión cerrada siempre; null si no existe o si la
        //    BD no responde (ahí sí se cae al API).
        $legacy = $this->desdeMySQLLegacy($numNota);

        // 2) FUENTE SECUNDARIA: API ARA (detalle de renglones y cliente).
        //    Fallback progresivo: si el API no responde, se usa lo que dio el
        //    legacy (cabecera con estatus real) en lugar de fallar la skill.
        $nota = null;
        $items = [];
        $apiDisponible = false;
        try {
            $nota = $this->buscarNota($numNota);
        } catch (\RuntimeException $e) {
            $nota = null;
        }
        if ($nota !== null) {
            $apiDisponible = true;
            // 2b) Renglones de la nota (el detalle puede traer cabecera fresca).
            $id = (int) ($nota['id'] ?? 0);
            if ($id > 0) {
                $detalle = $this->getJson('/api/notas/detalle/' . $id);
                if (is_array($detalle)) {
                    if (is_array($detalle['nota'] ?? null)) {
                        $nota = $detalle['nota'];
                    }
                    foreach ($detalle['items'] ?? [] as $it) {
                        if (!is_array($it)) {
                            continue;
                        }
                        $items[] = [
                            'co_art'    => self::primerValor($it, ['co_art', 'codigo'], 'N/A'),
                            'art_des'   => self::primerValor($it, ['art_des', 'descripcion'], 'N/A'),
                            'total_art' => (float) ($it['total_art'] ?? $it['cantidad'] ?? $it['cantidad_solicitada'] ?? 0),
                            'preparada' => (float) ($it['cantidad_preparada'] ?? 0),
                            'unidad'    => (string) ($it['unidad_medida'] ?? ''),
                            'co_alma'   => self::primerValor($it, ['co_alma'], 'N/A'),
                            'ubicacion' => self::primerValor($it, ['campo7', 'ubicacion'], 'N/A'),
                            'reng_num'  => (int) ($it['reng_num'] ?? 0),
                        ];
                    }
                }
            }
        }

        // 3) Si ninguna fuente encontró la nota → no existe en el sistema.
        if ($nota === null && $legacy === null) {
            $msg = sprintf('La nota "%s" no existe en el ERP ni en el sistema de gestión.', $numNota);
            $card = CardBuilder::iniciar('🚫', 'NOTA NO ENCONTRADA')
                ->linea($msg)
                ->footer('Fuente: MySQL rep_not/gestion + API ARA')
                ->tarjeta();
            $env = Envelope::error($msg, 'NOTA_INEXISTENTE');
            return Envelope::conCard($env, $card);
        }

        // 4) Enriquecimiento Profit (best-effort): si el cliente local viene
        //    vacío (notas BQTO sin datos de clientes en SQLite), consulta el
        //    detalle SOLO LECTURA de PRUEB25 y completa solo los campos vacíos.
        //    Nunca rompe el flujo: ante error (red, HTTP, nota ausente), el
        //    mapeo final conserva 'N/A' en lugar de fallar la herramienta.
        if (is_array($nota)
            && ((string) ($nota['co_cli'] ?? '') === ''
                || (string) ($nota['cliente'] ?? '') === ''
                || (string) ($nota['cli_des'] ?? '') === '')) {
            $digitosNota = (string) preg_replace('/\D/', '', (string) ($nota['numero_nota'] ?? $numNota));
            if ($digitosNota !== '') {
                try {
                    $profit = $this->getJson('/api/notas/detalle_profit?numero=' . rawurlencode(ltrim($digitosNota, '0')));
                } catch (\RuntimeException $e) {
                    $profit = null;
                }
                if (is_array($profit) && ($profit['status'] ?? '') === 'success' && is_array($profit['nota'] ?? null)) {
                    foreach ($profit['nota'] as $k => $v) {
                        if ($v === null || $v === '') {
                            continue;
                        }
                        $actual = (string) ($nota[$k] ?? '');
                        if ($actual === '') {
                            $nota[$k] = $v;
                        }
                    }
                }
            }
        }

        // 5) Mapeo seguro de claves: API + legacy (el estatus del legacy manda
        //    porque es el flujo real de chequeo/registro.php).
        $coCliente  = self::primerValor($nota ?? [], ['co_cli', 'co_cliente', 'co_clien', 'cliente_cod'], 'N/A');
        $razonSocial = self::primerValor($nota ?? [], ['cli_des', 'razon_social', 'nom_cli', 'cliente'], 'N/A');
        $coVendedor = self::primerValor($nota ?? [], ['co_ven', 'vendedor'], 'N/A');
        $fechaEmis  = self::primerValor($nota ?? [], ['fec_emis', 'fecha', 'fecha_emision', 'fecha_creacion'], 'N/A');
        $coAlmacen  = self::primerValor($nota ?? [], ['co_alma', 'almacen', 'almacen_origen'], 'N/A');
        $estatus    = $legacy !== null
            ? (string) $legacy['estado']
            : self::primerValor($nota ?? [], ['estatus', 'status', 'estado'], 'Desconocido');

        $origen = 'mysql_legacy';
        if ($legacy === null) {
            $origen = 'ara_api';
        } elseif (!$apiDisponible) {
            // Distingue "el API no respondió" (red/HTTP caído: sí es un
            // problema real) de "el API respondió pero no tenía esta nota en
            // /api/notas/lista" (normal, ej. notas viejas o de otra sede que
            // no están en esa fuente — la cabecera del legacy sigue siendo
            // válida, no hay nada caído).
            $origen = $this->apiRespondio
                ? 'mysql_legacy (nota sin detalle en API)'
                : 'mysql_legacy (API caído)';
        }

        $totalUnidades = array_sum(array_map(
            static fn (array $it): float => (float) $it['total_art'],
            $items
        ));

        $id = (int) ($nota['id'] ?? 0);
        $numeroResp = (string) ($nota['numero_nota'] ?? ($legacy['cod'] ?? $numNota));

        // ── Fase 4.17 (AD1): líneas normalizadas con precio unitario y total
        //    por línea. Si la fuente no expone precio, total_linea se deriva
        //    de total_art (sin precio) para no inventar datos.
        $lineas = [];
        foreach ($items as $it) {
            $cantidad = (float) ($it['total_art'] ?? 0);
            $precioU  = (float) ($it['precio_unitario'] ?? $it['precio'] ?? $it['pu'] ?? 0);
            $lineas[] = [
                'co_art'         => (string) ($it['co_art'] ?? ''),
                'descripcion'    => (string) ($it['art_des'] ?? ''),
                'cantidad'       => $cantidad,
                'precio_unitario'=> $precioU > 0 ? $precioU : null,
                'total_linea'    => $precioU > 0 ? round($cantidad * $precioU, 2) : $cantidad,
            ];
        }

        $estadoNormalizado = self::normalizarEstado((string) $estatus);

        $respuesta = [
            'status'         => 'OK',
            'num_nota'       => $numeroResp,
            'fecha_emision'  => (string) $fechaEmis,
            'co_cliente'     => (string) $coCliente,
            'nombre_cliente' => (string) $razonSocial,
            'total_items'    => count($items),
            'estado'         => $estadoNormalizado,
            'vendedor'       => (string) $coVendedor,
            'lineas'         => $lineas,
            'co_vendedor'    => (string) $coVendedor,
            'co_almacen'     => (string) $coAlmacen,
            'estatus'        => (string) $estatus,
            'total_bultos'   => (int) (is_array($nota) ? ($nota['total_bultos'] ?? count($items)) : count($items)),
            'articulos'      => $items,
            'origen'         => $origen,
            // Compatibilidad con consumidores existentes (harness/LLM).
            'ok'             => true,
            'id'             => $id,
            'cliente'        => (string) $razonSocial,
            'total_articulos'=> count($items),
            'total_unidades' => $totalUnidades,
            // Cuerpo visible (directiva v4.13): tarjeta CardBuilder.
            'card'           => $this->formatAsCard([
                'num_nota'   => $numeroResp,
                'cliente'    => (string) $razonSocial,
                'co_cliente' => (string) $coCliente,
                'co_almacen' => (string) $coAlmacen,
                'estatus'    => (string) $estatus,
                'fecha'      => (string) $fechaEmis,
                'items'      => $items,
                'total_unidades' => $totalUnidades,
                'legacy'     => $legacy,
                'origen'     => $origen,
            ]),
        ];
        if ($legacy !== null && isset($legacy['gestion']) && is_array($legacy['gestion'])) {
            $respuesta['chequeo'] = $legacy['gestion'];
        }

        // Envelope estándar v4.17: success/data/message/timestamp. Los datos
        // completos (fase anterior + normalizados) viven en 'data'; se
        // conservan todas las claves raíz previas para no romper consumidores.
        $data = [
            'num_nota'      => $numeroResp,
            'fecha'         => (string) $fechaEmis,
            'cliente'       => ['co_cli' => (string) $coCliente, 'nombre' => (string) $razonSocial],
            'total_items'   => count($items),
            'estado'        => $estadoNormalizado,
            'vendedor'      => (string) $coVendedor,
            'lineas'        => $lineas,
            'origen'        => $origen,
            'estatus_crudo' => (string) $estatus,
            'detalle'       => $respuesta,
        ];
        return Envelope::conCard(
            Envelope::ok($data, 'Nota ' . $numeroResp . ' consultada (' . count($items) . ' ítem(s), estado ' . $estadoNormalizado . ').'),
            $respuesta['card']
        );
    }

    /**
     * Normaliza el estatus crudo del legacy/API a la convención de la orden
     * (Pendiente / Modificado / Procesado). Regla: no conocemos vocabulario
     * real distinto; se mapea por coincidencia de subcadena y se deja el valor
     * crudo en caso desconocido (nunca se inventa estado).
     */
    private static function normalizarEstado(string $estatus): string
    {
        $e = strtoupper(trim($estatus));
        if ($e === '') {
            return 'Desconocido';
        }
        if (str_contains($e, 'PROCES') || str_contains($e, 'CHEQUE')) {
            return 'Procesado';
        }
        if (str_contains($e, 'MODIF')) {
            return 'Modificado';
        }
        if (str_contains($e, 'PEND') || str_contains($e, 'ACTIV') || str_contains($e, 'IMPRES')) {
            return 'Pendiente';
        }
        return $estatus;
    }

    /**
     * FUENTE PRIMARIA (CTO v4.11): busca la nota en el MySQL legacy.
     *
     * Cabecera en rep_not (estatus/fechas/ruta) + fila de gestion (chequeo
     * VERIFICADA, preparador/chequeador, horas). Solo lectura, conexión
     * cerrada siempre (finally). Devuelve null si la BD no responde o la
     * nota no existe en ninguna variante del número.
     *
     * @return array{cod:string,estado:string,fec_cre?:string,fec_imp?:string,
     *               ruta?:string,gestion?:array<string,mixed>}|null
     */
    private function desdeMySQLLegacy(string $numNota): ?array
    {
        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            return null;
        }
        try {
            $tabla = $this->resolverTabla($pdo, ['rep_not', 'notas', 'nota']);
            if ($tabla === null) {
                return null;
            }
            $cols = $this->resolverColumnas($pdo, $tabla, [
                'cod'     => ['cod_nota', 'num_nota', 'cd_barr', 'numero_nota'],
                'estado'  => ['estatus', 'estado', 'status'],
                'fec_cre' => ['fec_creacion', 'fecha', 'fec_emis'],
                'fec_imp' => ['fec_impr', 'fec_profit'],
                'ruta'    => ['ruta'],
            ]);
            if ($cols['cod'] === null || $cols['estado'] === null) {
                return null;
            }

            $digitos = (string) preg_replace('/\D/', '', $numNota);
            $variantes = [trim($numNota)];
            if ($digitos !== '') {
                $variantes[] = $digitos;
                $variantes[] = 'A' . $digitos;
            }
            $variantes = array_values(array_unique($variantes));

            $select = 'SELECT ' . self::q($cols['cod']) . ' AS cod'
                . ', ' . self::q($cols['estado']) . ' AS estado'
                . ($cols['fec_cre'] !== null ? ', ' . self::q($cols['fec_cre']) . ' AS fec_cre' : '')
                . ($cols['fec_imp'] !== null ? ', ' . self::q($cols['fec_imp']) . ' AS fec_imp' : '')
                . ($cols['ruta'] !== null ? ', ' . self::q($cols['ruta']) . ' AS ruta' : '')
                . ' FROM ' . self::q($tabla) . ' WHERE ' . self::q($cols['cod']) . ' = ? LIMIT 1';

            $fila = null;
            foreach ($variantes as $v) {
                $stmt = $pdo->prepare($select);
                $stmt->execute([$v]);
                $fila = $stmt->fetch(PDO::FETCH_ASSOC);
                if (is_array($fila)) {
                    break;
                }
            }
            if (!is_array($fila)) {
                return null;
            }

            $resultado = [
                'cod'    => (string) ($fila['cod'] ?? $numNota),
                'estado' => self::aUtf8((string) ($fila['estado'] ?? 'DESCONOCIDO')),
            ];
            foreach (['fec_cre', 'fec_imp', 'ruta'] as $campo) {
                if (isset($fila[$campo]) && trim((string) $fila[$campo]) !== '') {
                    $resultado[$campo] = self::aUtf8((string) $fila[$campo]);
                }
            }

            // Trazabilidad física: gestion (chequeo/preparación/embalaje).
            $resultado['gestion'] = $this->gestionEnLegacy($pdo, (string) $resultado['cod']);
            return $resultado;
        } finally {
            $pdo = null;
        }
    }

    /**
     * Fila de trazabilidad física (gestion) de la nota en el legacy:
     * verifi_pre/verifi_cheq/verifi_emb, y preparador/chequeador/embalador
     * (ID + nombre resuelto contra `usuarios`) con su hora por fase.
     *
     * BUG corregido (a pedido del usuario, "no trae nombres e ID de los
     * operadores"): el mapeo de columnas SOLO cubría preparador y chequeador
     * — faltaba por completo el embalador (num_emb/verifi_emb/hora3, columnas
     * reales verificadas en producción), y ninguno de los tres devolvía el
     * NOMBRE del operador, solo el número crudo (num_prep/num_cheq/num_emb),
     * que no le sirve de nada al usuario sin resolverlo contra `usuarios`.
     *
     * @return array<string,mixed>|null
     */
    private function gestionEnLegacy(PDO $pdo, string $codNota): ?array
    {
        $tabla = $this->resolverTabla($pdo, ['gestion', 'gestion_log', 'log_gestion']);
        if ($tabla === null) {
            return null;
        }
        $cols = $this->resolverColumnas($pdo, $tabla, [
            'cod'       => ['cd_barr', 'cod_nota', 'num_nota'],
            'prep'      => ['num_prep', 'preparador', 'preparador_id'],
            'hora1'     => ['hora', 'hora_preparacion', 'fecha_prep'],
            'verif_pre' => ['verifi_pre'],
            'chq'       => ['num_cheq', 'chequeador', 'chequeador_id'],
            'hora2'     => ['hora2', 'hora_chequeo', 'fecha_cheq'],
            'verif'     => ['verifi_cheq', 'verificacion', 'verifi'],
            'tip'       => ['tip_pre', 'tipo_preparacion'],
            'emb'       => ['num_emb', 'embalador', 'embalador_id'],
            'hora3'     => ['hora3', 'hora_embalaje', 'fecha_emb'],
            'verif_emb' => ['verifi_emb'],
            'tip_emb'   => ['tip_emb', 'tipo_embalaje'],
        ]);
        if ($cols['cod'] === null) {
            return null;
        }
        $campos = ['cod', 'prep', 'hora1', 'verif_pre', 'chq', 'hora2', 'verif', 'tip', 'emb', 'hora3', 'verif_emb', 'tip_emb'];
        $select = 'SELECT ' . implode(', ', array_filter(array_map(
            static fn (string $rol) => $cols[$rol] !== null ? self::q($cols[$rol]) . ' AS ' . $rol : null,
            $campos
        )))
            . ' FROM ' . self::q($tabla) . ' WHERE ' . self::q($cols['cod']) . ' = ? LIMIT 1';
        try {
            $stmt = $pdo->prepare($select);
            $stmt->execute([$codNota]);
            $fila = $stmt->fetch(PDO::FETCH_ASSOC);
            if (!is_array($fila)) {
                return null;
            }

            $idPrep = isset($fila['prep']) ? trim((string) $fila['prep']) : '';
            $idChq  = isset($fila['chq']) ? trim((string) $fila['chq']) : '';
            $idEmb  = isset($fila['emb']) ? trim((string) $fila['emb']) : '';
            $nombres = $this->resolverNombresOperadores($pdo, array_filter([$idPrep, $idChq, $idEmb], static fn (string $n) => $n !== '' && $n !== '0'));

            $g = [
                'verificada'        => strtoupper(trim((string) ($fila['verif'] ?? ''))) === 'VERIFICADA',
                'preparador_id'     => $idPrep,
                'preparador'        => $idPrep !== '' ? ($nombres[$idPrep] ?? $idPrep) : '',
                'preparador_verificado' => strtoupper(trim((string) ($fila['verif_pre'] ?? ''))) === 'VERIFICADA',
                'chequeador_id'     => $idChq,
                'chequeador'        => $idChq !== '' ? ($nombres[$idChq] ?? $idChq) : '',
                'embalador_id'      => $idEmb,
                'embalador'         => $idEmb !== '' ? ($nombres[$idEmb] ?? $idEmb) : '',
                'embalador_verificado' => strtoupper(trim((string) ($fila['verif_emb'] ?? ''))) === 'VERIFICADA',
                'hora_preparacion'  => self::fechaLegible((string) ($fila['hora1'] ?? '')),
                'hora_chequeo'      => self::fechaLegible((string) ($fila['hora2'] ?? '')),
                'hora_embalaje'     => self::fechaLegible((string) ($fila['hora3'] ?? '')),
            ];
            if (isset($fila['tip']) && trim((string) $fila['tip']) !== '') {
                $g['tipo_preparacion'] = self::aUtf8((string) $fila['tip']);
            }
            if (isset($fila['tip_emb']) && trim((string) $fila['tip_emb']) !== '') {
                $g['tipo_embalaje'] = self::aUtf8((string) $fila['tip_emb']);
            }
            return $g;
        } catch (PDOException $e) {
            return null;
        }
    }

    /**
     * Resuelve número de operador → nombre real contra la tabla `usuarios`
     * del MySQL legacy (numero/nombre). Best-effort: si la tabla no existe o
     * el número no está registrado, se conserva el número crudo (nunca se
     * inventa un nombre ni se rompe la consulta de la nota).
     *
     * @param array<int,string> $numeros
     *
     * @return array<string,string> numero => nombre
     */
    private function resolverNombresOperadores(PDO $pdo, array $numeros): array
    {
        $numeros = array_values(array_unique(array_filter($numeros, static fn (string $n) => $n !== '')));
        if ($numeros === []) {
            return [];
        }
        $tabla = $this->resolverTabla($pdo, ['usuarios']);
        if ($tabla === null) {
            return [];
        }
        $cols = $this->resolverColumnas($pdo, $tabla, [
            'numero' => ['numero', 'num_trab', 'num_usuario'],
            'nombre' => ['nombre', 'nombre_completo'],
        ]);
        if ($cols['numero'] === null || $cols['nombre'] === null) {
            return [];
        }
        $marks = implode(',', array_fill(0, count($numeros), '?'));
        $sql = 'SELECT ' . self::q($cols['numero']) . ' AS numero, ' . self::q($cols['nombre']) . ' AS nombre'
            . ' FROM ' . self::q($tabla) . ' WHERE ' . self::q($cols['numero']) . ' IN (' . $marks . ')';
        try {
            $stmt = $pdo->prepare($sql);
            $stmt->execute($numeros);
            $mapa = [];
            foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $fila) {
                $mapa[trim((string) $fila['numero'])] = self::aUtf8((string) ($fila['nombre'] ?? ''));
            }
            return $mapa;
        } catch (PDOException $e) {
            return [];
        }
    }

    /**
     * Tarjeta (Card) estándar v4.13 (CardBuilder): cabecera, cliente/almacén,
     * estatus (con sección de chequeo del legacy si existe), total de renglones
     * y detalle ítem por ítem (máx 8 visibles).
     *
     * @param array<string,mixed> $d
     */
    public function formatAsCard(array $d): string
    {
        $numNota  = (string) ($d['num_nota'] ?? 'N/A');
        $cliente  = (string) ($d['cliente'] ?? 'N/A');
        $coCli    = (string) ($d['co_cliente'] ?? '');
        $almacen  = (string) ($d['co_almacen'] ?? 'N/A');
        $estatus  = (string) ($d['estatus'] ?? 'Desconocido');
        $items    = is_array($d['items'] ?? null) ? $d['items'] : [];

        $card = CardBuilder::iniciar('📄', 'NOTA DE ENTREGA / DESPACHO: #' . $numNota)
            ->seccion('Cabecera')
            ->campo('CLIENTE', $cliente . ($coCli !== '' ? ' (' . $coCli . ')' : ''))
            ->campo('ALMACÉN', $almacen)
            ->campo('FECHA EMISIÓN', (string) ($d['fecha'] ?? 'N/A'))
            ->campo('ESTATUS', $estatus, 'estado')
            ->campo('ORIGEN', (string) ($d['origen'] ?? 'N/A'));

        $legacy = $d['legacy'] ?? null;
        if (is_array($legacy) && isset($legacy['gestion']) && is_array($legacy['gestion'])) {
            $g = $legacy['gestion'];
            $card->seccion('Operadores (gestion)')
                ->linea(($g['verificada'] ?? false) ? '✅ Chequeo verificado' : '⏳ Chequeo pendiente de verificación');
            $operador = static function (string $etiqueta, string $id, string $nombre): string {
                if ($id === '' || $id === '0') {
                    return $etiqueta . ': — (sin registro)';
                }
                return $etiqueta . ': ' . ($nombre !== '' && $nombre !== $id ? $nombre . ' (ID ' . $id . ')' : 'ID ' . $id);
            };
            $card->linea($operador('👤 Preparador', (string) ($g['preparador_id'] ?? ''), (string) ($g['preparador'] ?? '')));
            $card->linea($operador('🔎 Chequeador', (string) ($g['chequeador_id'] ?? ''), (string) ($g['chequeador'] ?? '')));
            $card->linea($operador('📦 Embalador', (string) ($g['embalador_id'] ?? ''), (string) ($g['embalador'] ?? '')));
            if (($g['hora_preparacion'] ?? '') !== '' || ($g['hora_chequeo'] ?? '') !== '' || ($g['hora_embalaje'] ?? '') !== '') {
                $card->linea('prep ' . ($g['hora_preparacion'] !== '' ? $g['hora_preparacion'] : '—')
                    . ' → chq ' . ($g['hora_chequeo'] !== '' ? $g['hora_chequeo'] : '—')
                    . ' → emb ' . ($g['hora_embalaje'] !== '' ? $g['hora_embalaje'] : '—'));
            }
        }

        $card->seccion('Renglones');
        $card->campo('TOTAL', count($items) . ' ítem(s) · ' . self::fmtNumero($d['total_unidades'] ?? 0) . ' und', 'texto');
        if ($items === []) {
            $card->linea('_(Sin renglones de detalle disponibles.)_');
        } else {
            foreach (array_slice($items, 0, 8) as $i => $it) {
                $rengNum = (int) ($it['reng_num'] ?? 0);
                $reng = $rengNum > 0 ? $rengNum : $i + 1;
                $card->linea(sprintf(
                    '%3d. [%-12s] %s — Cant: %s und | Prep: %s',
                    $reng,
                    (string) ($it['co_art'] ?? 'N/A'),
                    (string) ($it['art_des'] ?? 'N/A'),
                    self::fmtNumero($it['total_art'] ?? 0),
                    self::fmtNumero($it['preparada'] ?? 0)
                ));
            }
            $ocultos = count($items) - 8;
            if ($ocultos > 0) {
                $card->linea('⏩ +' . $ocultos . ' renglón(es) más (usa paginación para el detalle completo).');
            }
        }

        return $card
            ->footer('Fuente: MySQL rep_not/gestion + API ARA · NOLOCK')
            ->tarjeta();
    }

    /**
     * Busca la nota en el API probando variantes del número (con/sin prefijo).
     *
     * La coincidencia final se hace sobre la forma numérica pura sin ceros a
     * la izquierda, de modo que "A0467959", "467959" y "A467959" encuentren
     * la misma nota, y "72160754" su alias "A72160754".
     *
     * @return array|null Fila de nota coincidente o null si no existe.
     */
    private function buscarNota(string $numNota): ?array
    {
        $digitos  = (string) preg_replace('/\D/', '', $numNota);
        $sinCeros = ltrim($digitos, '0');

        $variantes = [$numNota];
        if ($digitos !== '' && $digitos !== $numNota) {
            $variantes[] = $digitos;
        }
        if ($sinCeros !== '' && $sinCeros !== $digitos) {
            $variantes[] = $sinCeros;
        }
        if ($digitos !== '') {
            $variantes[] = 'A' . $digitos;
            $variantes[] = 'A' . $sinCeros;
        }
        $variantes = array_values(array_unique($variantes));

        $coincide = static function (array $row, string $v): bool {
            $numRow = (string) ($row['numero_nota'] ?? '');
            if ($numRow === '') {
                return false;
            }
            if (strcasecmp($numRow, $v) === 0) {
                return true;
            }
            $digRow  = (string) preg_replace('/\D/', '', $numRow);
            $digV    = (string) preg_replace('/\D/', '', $v);
            return $digRow !== '' && $digV !== ''
                && ltrim($digRow, '0') === ltrim($digV, '0');
        };

        foreach ($variantes as $v) {
            $lista = $this->getJson('/api/notas/lista?busqueda=' . rawurlencode($v));
            if (!is_array($lista)) {
                continue;
            }
            // El API respondió con una lista válida (aunque venga vacía o sin
            // match para esta variante) — no está "caído".
            $this->apiRespondio = true;
            foreach ($lista as $row) {
                if (is_array($row) && isset($row['numero_nota']) && $coincide($row, $v)) {
                    return $row;
                }
            }
        }

        return null;
    }

    /**
     * Reúne el texto plano de cualquier forma de argumentos: array posicional
     * ([0 => '...']), claves de texto directo (text/raw_input/raw/query...),
     * valores string/númericos sueltos y arrays anidados de 1 nivel.
     */
    private static function reunirTexto(array $arguments): string
    {
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
            } elseif (is_bool($v)) {
                continue;
            }
        }
        return implode(' ', array_values(array_filter($partes, static fn ($p) => $p !== '')));
    }

    /**
     * Formatea un número: entero si no tiene decimales, si no 2 decimales.
     */
    private static function fmtNumero(mixed $valor): string
    {
        if ($valor === null || $valor === '') {
            return '0';
        }
        $n = (float) $valor;
        return $n === floor($n)
            ? number_format($n, 0, ',', '.')
            : number_format($n, 2, ',', '.');
    }

    /**
     * Fecha legible: '' para timestamps nulos de MySQL (0000-00-00...).
     */
    private static function fechaLegible(string $valor): string
    {
        $v = trim($valor);
        if ($v === '' || $v === '0000-00-00 00:00:00' || $v === '0000-00-00') {
            return '';
        }
        return $v;
    }

    /**
     * Retorna el primer valor NO vacío entre las claves candidatas.
     *
     * Trata el string vacío y null como "ausente" (a diferencia de ??), de
     * modo que la respuesta al LLM nunca lleva campos vacíos cuando existe
     * un fallback con dato real.
     *
     * @param array<string,mixed> $data
     * @param array<int,string>   $claves
     */
    private static function primerValor(array $data, array $claves, string $default): string
    {
        foreach ($claves as $k) {
            $v = $data[$k] ?? null;
            if ($v !== null && trim((string) $v) !== '') {
                return (string) $v;
            }
        }
        return $default;
    }

    /**
     * GET JSON al API ARA con timeout controlado.
     *
     * @throws \RuntimeException Fallo de red o HTTP no-2xx.
     */
    private function getJson(string $path): mixed
    {
        $ch = curl_init();
        if ($ch === false) {
            throw new \RuntimeException('curl_init() falló: extensión cURL no disponible.');
        }

        try {
            curl_setopt_array($ch, [
                CURLOPT_URL            => $this->baseUrl . $path,
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_TIMEOUT        => $this->timeoutS,
                CURLOPT_CONNECTTIMEOUT => 5,
                CURLOPT_HTTPHEADER     => ['Accept: application/json'],
            ]);

            $raw = curl_exec($ch);
            $status = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);

            if ($raw === false) {
                throw new \RuntimeException(sprintf(
                    'Error cURL al consultar el ERP (%s): %s',
                    $this->baseUrl . $path,
                    curl_error($ch)
                ));
            }
            if ($status < 200 || $status >= 300) {
                throw new \RuntimeException(sprintf(
                    'El ERP ARA respondió HTTP %d en %s.',
                    $status,
                    $this->baseUrl . $path
                ));
            }

            return json_decode((string) $raw, true);
        } finally {
            curl_close($ch);
        }
    }
}

<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Adapters;

use App\Services\NvidiaBrain\NvidiaBrainClient;
use App\Services\NvidiaBrain\Security\CustomerAuthenticator;
use App\Services\NvidiaBrain\Tools\Auditoria\ConsultarSaldoClienteTool;
use App\Services\NvidiaBrain\Tools\Despacho\ConsultarClienteTool;
use App\Services\NvidiaBrain\ToolRegistry;
use PDO;
use PDOException;

/**
 * Adaptador del canal de atención al cliente (WhatsApp).
 *
 * Flujo:
 *   1. Recibe $identificador (RIF/Código) y $factorSecundario (últimos 4
 *      dígitos del teléfono, o el número de origen de WhatsApp).
 *   2. Autentica con CustomerAuthenticator contra la tabla "clientes" de
 *      Profit Plus. Si falla → respuesta JSON amigable solicitando la
 *      corrección de los datos (nunca vacía, nunca "No sé").
 *   3. Si es válido → LIMITA las consultas de ConsultarSaldoClienteTool
 *      únicamente al co_cli autenticado: el identificador que reciba la
 *      herramienta es SIEMPRE el co_cli verificado, ignorando cualquier
 *      otro dato del mensaje (imposible consultar a otro cliente).
 *
 * Regla de seguridad absoluta: jamás se entrega información de otros
 * clientes (refuerzo del System Prompt).
 */
final class WhatsappClienteAdapter extends BaseAdapter
{
    private CustomerAuthenticator $authenticator;

    private ?PDO $dbProfit;

    /**
     * @param NvidiaBrainClient|null     $client        Cliente LLM (Ollama local).
     * @param ToolRegistry|null          $registry      Registro de tools.
     * @param array<string,mixed>        $contexto      Contexto de sesión.
     * @param CustomerAuthenticator|null $authenticator Autenticador de cliente.
     * @param PDO|null                   $dbProfit      Conexión a Profit Plus;
     *                                                  si es null se abre bajo
     *                                                  demanda (PROFIT_SQL_*).
     */
    public function __construct(
        ?NvidiaBrainClient $client = null,
        ?ToolRegistry $registry = null,
        array $contexto = [],
        ?CustomerAuthenticator $authenticator = null,
        ?PDO $dbProfit = null
    ) {
        parent::__construct($client, $registry, $contexto);
        $this->authenticator = $authenticator ?? new CustomerAuthenticator();
        $this->dbProfit = $dbProfit;
    }

    protected function getSystemPrompt(): string
    {
        return 'Eres el asistente oficial de atención al cliente de la droguería. '
             . 'Sé cortés, preciso y jamás entregues información de otros clientes.';
    }

    /**
     * {@inheritDoc}
     *
     * Contexto esperado:
     *   - identificador      (string, requerido) RIF / Cédula / co_cli.
     *   - factor_secundario  (string, requerido) últimos 4 dígitos del teléfono.
     *   - origen_whatsapp    (string, opcional) número de origen del mensaje.
     */
    public function procesar(string $mensaje, array $contexto = []): array
    {
        $datos = array_merge($this->contexto, $contexto);
        $identificador = trim((string) ($datos['identificador'] ?? ''));
        $factor = trim((string) ($datos['factor_secundario'] ?? ''));
        $origen = trim((string) ($datos['origen_whatsapp'] ?? ''));

        $faltantes = $this->parametrosFaltantes(['identificador', 'factor_secundario'], $datos);
        if ($faltantes !== []) {
            return $this->respuestaFaltanParametros(
                $faltantes,
                'verificar tu identidad como cliente'
            );
        }

        // ── 1) Autenticación estricta ─────────────────────────────────────────
        try {
            $pdo = $this->dbProfit ?? $this->conectar();
        } catch (PDOException $e) {
            return $this->responder(
                'No pudimos verificar tu identidad en este momento por un problema '
                . 'temporal del sistema. Por favor intenta de nuevo en unos minutos.',
                ['error_tecnico' => self::sanitizar_utf8($e->getMessage())],
                'Base de datos de clientes no disponible: ' . $e->getMessage()
            );
        }

        $cliente = $this->authenticator->validarCliente($pdo, $identificador, $factor, $origen);
        if ($cliente === null || !isset($cliente['co_cli']) || $cliente['co_cli'] === '') {
            return $this->responder(
                'No pudimos verificar tu identidad. Por favor verifica el RIF o '
                . 'código de cliente y confirma que el número de teléfono '
                . 'registrado termina en los 4 dígitos que enviaste. '
                . 'Los datos deben corresponder al mismo cliente.',
                [
                    'formato_requerido' => [
                        'identificador'     => 'RIF, cédula o código de cliente (co_cli)',
                        'factor_secundario' => 'Últimos 4 dígitos del teléfono registrado',
                    ],
                ],
                'Autenticación fallida: cliente no encontrado o factor secundario incorrecto.'
            );
        }

        // ── 2) Identidad validada: SOLO se consulta el co_cli autenticado ─────
        $coCli = (string) $cliente['co_cli'];

        $tool = new ConsultarSaldoClienteTool();
        $resultado = $tool->execute(
            ['identificador_cliente' => $coCli],
            ['usuario' => 'cliente:' . $coCli, 'rol' => 'CLIENTE']
        );

        if (!($resultado['success'] ?? false)) {
            $detalle = (string) ($resultado['error'] ?? 'No se pudo consultar el saldo.');
            return $this->responder(
                'Pudimos verificar tu identidad, pero no fue posible obtener tu '
                . 'saldo en este momento. Intenta de nuevo en unos minutos.',
                ['cliente' => $cliente, 'detalle' => self::sanitizar_utf8($detalle)],
                'Error consultando saldo: ' . $detalle
            );
        }

        // ── 3) Pedidos/documentos pendientes (ConsultarClienteTool, best-effort:
        //      si falla, la respuesta sigue con el saldo que ya se obtuvo) ──────
        $documentosPendientes = [];
        try {
            $clienteTool = new ConsultarClienteTool();
            $resultadoCliente = $clienteTool->execute(['co_cli' => $coCli], [
                'usuario' => 'cliente:' . $coCli, 'rol' => 'CLIENTE',
            ]);
            if (($resultadoCliente['ok'] ?? false) && !($resultadoCliente['es_busqueda'] ?? false)) {
                $documentosPendientes = $resultadoCliente['documentos_pendientes'] ?? [];
            }
        } catch (\Throwable $e) {
            // No bloquea la respuesta de saldo ya obtenida.
        }

        // ── 4) Fraseo final con el LLM (cortés) + fallback determinista ───────
        $saldo = $resultado['data']['cliente'] ?? [];
        $respuesta = $this->frasearSaldo($mensaje, $cliente, $saldo, $documentosPendientes);

        return $this->responder($respuesta, [
            'cliente'               => $cliente,
            'saldo'                 => self::sanitizar_utf8($resultado['data']),
            'documentos_pendientes' => self::sanitizar_utf8($documentosPendientes),
        ]);
    }

    /**
     * Redacta la respuesta final con el LLM; fallback determinista si el
     * modelo no responde (jamás se entrega vacío ni "No sé").
     *
     * @param array<string,mixed>       $cliente
     * @param array<string,mixed>       $saldo
     * @param array<int,array<string,mixed>> $documentosPendientes Pedidos/documentos
     *        activos (ConsultarClienteTool) — complementa el saldo con el
     *        detalle de qué está pendiente, no solo el monto total.
     */
    private function frasearSaldo(string $mensaje, array $cliente, array $saldo, array $documentosPendientes = []): string
    {
        $nombre = (string) ($cliente['nombre'] ?? 'cliente');
        $saldoActual = (string) ($saldo['saldo_actual'] ?? 'no disponible');
        $limite = isset($saldo['limite_credito']) ? (string) $saldo['limite_credito'] : 'no disponible';
        $cartera = '';
        if (isset($saldo['cartera']) && is_array($saldo['cartera'])) {
            $cartera = sprintf(
                ' Facturas pendientes: %d (Bs. %s).',
                (int) ($saldo['cartera']['facturas_pendientes'] ?? 0),
                (string) ($saldo['cartera']['saldo_pendiente'] ?? 0)
            );
        }

        $pedidos = '';
        $listaPedidos = '';
        if ($documentosPendientes !== []) {
            $pedidos = sprintf(' Pedidos/documentos activos: %d.', count($documentosPendientes));
            $lineas = [];
            foreach (array_slice($documentosPendientes, 0, 5) as $doc) {
                $tipo = (string) ($doc['tipo'] ?? 'Documento');
                $num = (string) ($doc['numero'] ?? '');
                $fecha = (string) ($doc['fecha'] ?? '');
                $docSaldo = (string) ($doc['saldo'] ?? '');
                $lineas[] = trim("{$tipo} {$num} · {$fecha} · saldo {$docSaldo}");
            }
            $listaPedidos = $lineas !== [] ? (PHP_EOL . 'Detalle: ' . implode('; ', $lineas)) : '';
        }

        $datos = sprintf(
            'Cliente: %s (%s). Saldo actual: %s. Límite de crédito: %s.%s%s%s',
            $nombre,
            (string) ($cliente['co_cli'] ?? ''),
            $saldoActual,
            $limite,
            $cartera,
            $pedidos,
            $listaPedidos
        );

        $res = $this->preguntar(
            'El cliente autenticado preguntó: "' . substr($mensaje, 0, 300) . '"'
            . PHP_EOL . 'Datos verificados de su cuenta: ' . $datos
            . PHP_EOL . 'Responde de forma cortés y precisa, en español. Usa SOLO estos '
            . 'datos verificados — nunca inventes montos, fechas ni números de documento.',
            [],
            'none',
            ['temperature' => 0.2, 'max_tokens' => 250]
        );

        if ($res['success'] && $res['respuesta'] !== '') {
            return $res['respuesta'];
        }

        return sprintf(
            'Hola %s, su saldo actual es Bs. %s con un límite de crédito de Bs. %s.%s%s',
            $nombre,
            $saldoActual,
            $limite,
            $cartera,
            $pedidos
        );
    }
}

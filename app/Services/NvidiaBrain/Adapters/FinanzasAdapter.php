<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Adapters;

use App\Services\NvidiaBrain\NvidiaBrainClient;
use App\Services\NvidiaBrain\Tools\Recepcion\ConciliarFacturaTool;
use App\Services\NvidiaBrain\Tools\Recepcion\LeerVoucherOcrTool;
use App\Services\NvidiaBrain\ToolRegistry;
use PDO;
use PDOException;
use Throwable;

/**
 * Adaptador del departamento de Finanzas (pagos y comprobantes).
 *
 * Flujo:
 *   1. Si se adjunta una imagen de soporte (ruta_imagen), invoca
 *      LeerVoucherOcrTool para extraer {banco, referencia, monto, fecha}.
 *   2. Con los datos de la conciliación (numero_factura, monto_conciliado,
 *      referencia_bancaria — del voucher o explícitos), invoca
 *      ConciliarFacturaTool BAJO UNA TRANSACCIÓN EXPLÍCITA DE PDO
 *      (beginTransaction / commit / rollBack): cualquier excepción revierte
 *      la operación y se reporta como "operación revertida".
 *
 * Regla de auditoría (System Prompt): ante la mínima duda en una referencia
 * o monto, la conciliación se marca como pendiente para revisión humana.
 *
 * Parámetros requeridos (si faltan se listan en la respuesta):
 *   - numero_factura (string)  número de la factura a conciliar.
 *   - monto_conciliado (float) monto del pago (o extraído del voucher).
 *   - referencia_bancaria (string) referencia del pago (o del voucher).
 *   - ruta_imagen (string, opcional) imagen del voucher para OCR.
 */
final class FinanzasAdapter extends BaseAdapter
{
    private ?PDO $pdo;

    /**
     * @param NvidiaBrainClient|null $client   Cliente LLM (Ollama local).
     * @param ToolRegistry|null      $registry Registro de tools.
     * @param array<string,mixed>    $contexto Contexto de sesión.
     * @param PDO|null               $pdo      Conexión PDO (transacción
     *                                         explícita); si es null se abre
     *                                         bajo demanda (PROFIT_SQL_*).
     */
    public function __construct(
        ?NvidiaBrainClient $client = null,
        ?ToolRegistry $registry = null,
        array $contexto = [],
        ?PDO $pdo = null
    ) {
        parent::__construct($client, $registry, $contexto);
        $this->pdo = $pdo;
    }

    protected function getSystemPrompt(): string
    {
        return 'Eres el auditor contable de la droguería. Procesa conciliaciones '
             . 'de pago con precisión absoluta. Ante la mínima duda en una '
             . 'referencia o monto, marca el registro como pendiente para '
             . 'revisión humana.';
    }

    /**
     * {@inheritDoc}
     */
    public function procesar(string $mensaje, array $contexto = []): array
    {
        $datos = array_merge($this->contexto, $contexto);

        $faltantes = $this->parametrosFaltantes(
            ['numero_factura', 'monto_conciliado', 'referencia_bancaria'],
            $datos
        );
        if ($faltantes !== []) {
            // Si hay imagen, aún se puede completar con el OCR.
            $rutaImagen = trim((string) ($datos['ruta_imagen'] ?? ''));
            if ($rutaImagen !== '') {
                $datos = $this->completarConVoucher($datos, $rutaImagen);
                $faltantes = $this->parametrosFaltantes(
                    ['numero_factura', 'monto_conciliado', 'referencia_bancaria'],
                    $datos
                );
            }
            if ($faltantes !== []) {
                return $this->respuestaFaltanParametros(
                    $faltantes,
                    'procesar la conciliación del pago'
                );
            }
        }

        $numero = trim((string) $datos['numero_factura']);
        $monto = (float) $datos['monto_conciliado'];
        $referencia = trim((string) $datos['referencia_bancaria']);
        $notas = trim((string) ($datos['notas'] ?? ''));

        // ── Transacción EXPLÍCITA de PDO alrededor de la conciliación ────────
        $transaccionIniciada = false;
        try {
            $pdo = $this->pdo ?? $this->conectar();
            if (!$pdo->inTransaction()) {
                $pdo->beginTransaction();
                $transaccionIniciada = true;
            }

            $tool = new ConciliarFacturaTool();
            $resultado = $tool->execute([
                'numero_factura'    => $numero,
                'monto_conciliado'  => $monto,
                'referencia_bancaria'=> $referencia,
                'notas'             => $notas,
            ], ['rol' => 'FINANZAS', 'usuario' => $datos['usuario'] ?? null]);

            if (!($resultado['success'] ?? false)) {
                if ($transaccionIniciada && $pdo->inTransaction()) {
                    $pdo->rollBack();
                }
                $detalle = (string) ($resultado['error'] ?? 'Conciliación rechazada.');
                return $this->responder(
                    'La conciliación no pudo completarse (operación revertida). '
                    . 'Revise los datos e intente de nuevo.',
                    ['conciliacion' => ['error' => self::sanitizar_utf8($detalle)]],
                    $detalle
                );
            }

            if ($transaccionIniciada) {
                $pdo->commit();
            }
        } catch (Throwable $e) {
            if ($transaccionIniciada && $pdo instanceof PDO && $pdo->inTransaction()) {
                try {
                    $pdo->rollBack();
                } catch (Throwable) {
                    // rollBack fallido: la conexión quedó rota; se reporta igual.
                }
            }
            return $this->responder(
                'Ocurrió un error procesando la conciliación; la operación fue '
                . 'revertida por completo. Intente nuevamente.',
                ['error_tecnico' => self::sanitizar_utf8($e->getMessage())],
                'Excepción en conciliación: ' . $e->getMessage()
            );
        }

        return $this->responder($this->resumenConciliacion($resultado), [
            'conciliacion' => self::sanitizar_utf8($resultado['data'] ?? []),
            'voucher'      => isset($datos['voucher']) ? self::sanitizar_utf8($datos['voucher']) : null,
        ]);
    }

    /**
     * Completa los datos de la conciliación con el OCR del voucher.
     *
     * @param array<string,mixed> $datos
     *
     * @return array<string,mixed>
     */
    private function completarConVoucher(array $datos, string $rutaImagen): array
    {
        try {
            $tool = new LeerVoucherOcrTool();
            $ocr = $tool->execute(['ruta_imagen' => $rutaImagen], ['rol' => 'FINANZAS']);
            if (!($ocr['success'] ?? false)) {
                return $datos;
            }
            $voucher = $ocr['data']['voucher'] ?? [];
            $datos['voucher'] = $voucher;
            // El OCR solo completa campos que aún falten; nunca pisa los
            // valores explícitos del operador.
            if (empty(trim((string) ($datos['referencia_bancaria'] ?? ''))) && !empty($voucher['referencia'])) {
                $datos['referencia_bancaria'] = (string) $voucher['referencia'];
            }
            if (empty(trim((string) ($datos['monto_conciliado'] ?? ''))) && isset($voucher['monto']) && is_numeric($voucher['monto'])) {
                $datos['monto_conciliado'] = (float) $voucher['monto'];
            }
            if (empty(trim((string) ($datos['notas'] ?? '')))) {
                $notas = [];
                if (!empty($voucher['banco'])) {
                    $notas[] = 'Banco: ' . (string) $voucher['banco'];
                }
                if (!empty($voucher['fecha'])) {
                    $notas[] = 'Fecha voucher: ' . (string) $voucher['fecha'];
                }
                if ($notas !== []) {
                    $datos['notas'] = implode(' | ', $notas);
                }
            }
        } catch (Throwable $e) {
            error_log('[FinanzasAdapter] OCR voucher falló: ' . $e->getMessage());
        }
        return $datos;
    }

    /**
     * Resumen legible de la conciliación exitosa.
     *
     * @param array<string,mixed> $resultado Resultado de ConciliarFacturaTool.
     */
    private function resumenConciliacion(array $resultado): string
    {
        $data = $resultado['data'] ?? [];
        $factura = $data['factura'] ?? [];
        $conciliacion = $data['conciliacion'] ?? [];
        $mensaje = (string) ($data['mensaje'] ?? '');

        return sprintf(
            'Conciliación procesada: %s',
            $mensaje !== ''
                ? $mensaje
                : sprintf(
                    'Factura %s (Bs. %s) con referencia %s.',
                    (string) ($factura['numero'] ?? '?'),
                    (string) ($factura['monto'] ?? '?'),
                    (string) ($conciliacion['referencia_bancaria'] ?? '?')
                )
        );
    }
}

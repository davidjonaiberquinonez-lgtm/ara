<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Security;

use PDO;
use PDOException;

/**
 * Validación estricta de identidad para el canal de atención al cliente.
 *
 * Verifica que el solicitante sea dueño legítimo de la cuenta consultada:
 *  1. Normaliza el identificador (RIF / Cédula / co_cli): mayúsculas y sin
 *     espacios, puntos o guiones.
 *  2. Consulta preparada sobre la tabla "clientes" de Profit Plus resolviendo
 *     por coincidencia en co_cli o rif.
 *  3. Factor secundario: los ÚLTIMOS 4 DÍGITOS del campo "telef" del cliente,
 *     o el número de origen de WhatsApp si el cliente lo registró (mismo
 *     número de teléfono, verificación tolerante a prefijos).
 *
 * Retorna null en cualquier fallo (no lanza excepciones hacia el llamador):
 * el adaptador de WhatsApp responde de forma amigable pidiendo corrección.
 *
 * Esquema Profit: "clientes" con co_cli, rif, cli_des, telef; los nombres de
 * columna se resuelven dinámicamente vía INFORMATION_SCHEMA (patrón del
 * módulo) para tolerar variantes del esquema.
 */
final class CustomerAuthenticator
{
    /** Sinónimos de columna por campo lógico (primer candidato existente gana). */
    private const CAMPOS = [
        'id'     => ['co_cli', 'cli_id', 'id_cliente', 'cliente_id', 'Id'],
        'rif'    => ['RIF', 'rif', 'cliente_rif', 'no_rif', 'cl_rif'],
        'nombre' => ['cli_des', 'nombre', 'razon_social', 'descripcion', 'cliente'],
        'telef'  => ['telef', 'telefono', 'telefonos', 'tel', 'tlf', 'telefono1'],
    ];

    /**
     * Valida la identidad del cliente con factor secundario.
     *
     * @param PDO    $dbProfit         Conexión PDO a Profit Plus (SQL Server).
     * @param string $identificador    RIF, Cédula o co_cli (tolerante a
     *                                 separadores y mayúsculas/minúsculas).
     * @param string $factorSecundario Últimos 4 dígitos del teléfono del
     *                                 cliente (o el número completo).
     * @param string $origenWhatsApp   Número de origen del mensaje de
     *                                 WhatsApp (alternativa al telef).
     *
     * @return array<string,mixed>|null Datos del cliente si la identidad
     *                                 valida; null si no se encontró o el
     *                                 factor secundario no coincide.
     */
    public function validarCliente(
        PDO $dbProfit,
        string $identificador,
        string $factorSecundario,
        string $origenWhatsApp = ''
    ): ?array {
        $norm = $this->normalizarIdentificador($identificador);
        if ($norm === '') {
            return null;
        }
        $factor = $this->ultimos4Digitos($factorSecundario);
        if ($factor === '' && $this->ultimos4Digitos($origenWhatsApp) === '') {
            return null;
        }

        try {
            $cols = $this->resolverColumnas($dbProfit);
            if ($cols['id'] === null || $cols['telef'] === null) {
                return null;
            }

            $sql = 'SELECT TOP 1 '
                 . self::q($cols['id']) . ' AS co_cli'
                 . ($cols['rif'] !== null ? ', ' . self::q($cols['rif']) . ' AS rif' : '')
                 . ($cols['nombre'] !== null ? ', ' . self::q($cols['nombre']) . ' AS nombre' : '')
                 . ', ' . self::q($cols['telef']) . ' AS telef'
                 . ' FROM ' . self::q('clientes')
                 . ' WHERE ' . $this->coincidenciaNormalizada($cols['id']) . ' = ?';
            $params = [$norm];

            if ($cols['rif'] !== null) {
                $sql .= ' OR ' . $this->coincidenciaNormalizada($cols['rif']) . ' = ?';
                $params[] = $norm;
            }
            $sql .= ' ORDER BY ' . self::q($cols['id']);

            $stmt = $dbProfit->prepare($sql);
            $stmt->execute($params);
            $fila = $stmt->fetch(PDO::FETCH_ASSOC);
        } catch (PDOException $e) {
            error_log('[CustomerAuthenticator] Error consultando clientes: ' . $e->getMessage());
            return null;
        }

        if (!is_array($fila) || $fila === []) {
            return null;
        }

        // ── Factor secundario: últimos 4 dígitos del telef o del origen ──────
        $telef = $this->ultimos4Digitos((string) ($fila['telef'] ?? ''));
        $origen = $this->ultimos4Digitos($origenWhatsApp);
        $coincide = $factor !== '' && ($factor === $telef || $factor === $origen);

        if (!$coincide) {
            return null;
        }

        $cliente = [
            'co_cli'  => self::aUtf8((string) ($fila['co_cli'] ?? '')),
            'rif'     => isset($fila['rif']) ? self::aUtf8((string) $fila['rif']) : null,
            'nombre'  => isset($fila['nombre']) ? self::aUtf8((string) $fila['nombre']) : null,
            'telef'   => self::aUtf8((string) ($fila['telef'] ?? '')),
        ];

        return array_filter($cliente, static fn ($v): bool => $v !== '' && $v !== null);
    }

    /**
     * Normaliza el identificador: mayúsculas y sin espacios, puntos ni guiones.
     */
    public function normalizarIdentificador(string $identificador): string
    {
        $limpio = str_replace([' ', '.', '-'], '', trim($identificador));
        return strtoupper($limpio);
    }

    /**
     * Extrae los últimos 4 dígitos de un número de teléfono (tolera formato
     * internacional, paréntesis y espacios). '' si no hay 4 dígitos.
     */
    public function ultimos4Digitos(string $numero): string
    {
        $digitos = preg_replace('/\D/', '', $numero) ?? '';
        if (strlen($digitos) < 4) {
            return '';
        }
        return substr($digitos, -4);
    }

    /**
     * Resuelve los nombres reales de columna de la tabla clientes.
     *
     * @return array<string, string|null>
     */
    private function resolverColumnas(PDO $pdo): array
    {
        $existentes = [];
        try {
            $stmt = $pdo->prepare(
                'SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ?'
            );
            $stmt->execute(['clientes']);
            foreach ($stmt->fetchAll(PDO::FETCH_COLUMN) as $col) {
                if (is_string($col)) {
                    $existentes[$col] = true;
                }
            }
        } catch (PDOException $e) {
            error_log('[CustomerAuthenticator] INFORMATION_SCHEMA no disponible: ' . $e->getMessage());
            // Degradación: supone el esquema canónico de Profit Plus.
            $existentes = ['co_cli' => true, 'rif' => true, 'cli_des' => true, 'telef' => true];
        }

        $resultado = [];
        foreach (self::CAMPOS as $campo => $candidatos) {
            $resultado[$campo] = null;
            foreach ($candidatos as $candidato) {
                if (isset($existentes[$candidato])) {
                    $resultado[$campo] = $candidato;
                    break;
                }
            }
        }
        return $resultado;
    }

    /**
     * Expresión SQL que compara una columna ignorando espacios, puntos,
     * guiones y mayúsculas (misma normalización que normalizarIdentificador).
     */
    private function coincidenciaNormalizada(string $columna): string
    {
        $col = self::q($columna);
        return "REPLACE(REPLACE(REPLACE(REPLACE(UPPER($col),' ',''),'.',''),'-',''),' ','')";
    }

    /**
     * Envuelve un identificador validado en corchetes de SQL Server.
     */
    private static function q(string $identificador): string
    {
        if (preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $identificador) !== 1) {
            throw new \RuntimeException('Identificador SQL inválido: ' . $identificador);
        }
        return '[' . $identificador . ']';
    }

    /**
     * Normaliza texto del ERP a UTF-8 (los datos vienen en CP1252/Latin-1).
     */
    private static function aUtf8(string $texto): string
    {
        $limpio = trim($texto);
        if (mb_check_encoding($limpio, 'UTF-8')) {
            return $limpio;
        }
        $convertido = mb_convert_encoding($limpio, 'UTF-8', 'CP1252');
        return is_string($convertido) ? $convertido : $limpio;
    }
}

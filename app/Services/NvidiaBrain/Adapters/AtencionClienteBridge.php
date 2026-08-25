<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Adapters;

use PDO;
use PDOException;

/**
 * Puente PHP → SQLite del módulo ATENCIÓN AL CLIENTE (bandeja Python).
 *
 * El webhook de WhatsApp (PHP, WhatsAppWebhookController) recibe y procesa
 * el mensaje, pero la BANDEJA que ve el agente humano vive en el módulo
 * Python (`ara/ARA_Brain/atencion_cliente_routes.py`, mismo esquema en
 * `atencion_cliente_schema.sql`). En vez de una llamada HTTP intermedia
 * entre PHP y Python, este puente escribe DIRECTO en el mismo archivo
 * SQLite (`ara/ARA_Brain/data/proyecto_ara.db`) que ya comparten ambos
 * lados — mismo patrón que usa el resto del proyecto para no duplicar
 * estado entre stacks.
 *
 * Cada operación es best-effort: si esta escritura falla (SQLite bloqueada,
 * tabla no migrada todavía, etc.) NUNCA debe romper la respuesta al cliente
 * de WhatsApp — el llamador debe envolver las llamadas en try/catch y
 * seguir con el flujo normal del webhook aunque el puente falle.
 */
final class AtencionClienteBridge
{
    private PDO $pdo;

    public function __construct(?string $dbPath = null)
    {
        $ruta = $dbPath ?? (__DIR__ . '/../../../../ara/ARA_Brain/data/proyecto_ara.db');
        $this->pdo = new PDO('sqlite:' . $ruta);
        $this->pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $this->pdo->exec('PRAGMA busy_timeout = 5000');
        $this->pdo->exec('PRAGMA foreign_keys = ON');
    }

    /**
     * Resuelve el número Meta asignado (fila de meta_numeros) a partir del
     * phone_number_id que llegó en el payload de Meta. Null si ese número
     * todavía no fue asignado a ningún agente (mensaje huérfano — se loguea
     * y se ignora, no se puede saber a quién mostrárselo).
     *
     * @return array<string,mixed>|null
     */
    public function resolverAgentePorNumero(string $phoneNumberId): ?array
    {
        if ($phoneNumberId === '') {
            return null;
        }
        $stmt = $this->pdo->prepare(
            'SELECT * FROM meta_numeros WHERE phone_number_id = ? AND activo = 1'
        );
        $stmt->execute([$phoneNumberId]);
        $fila = $stmt->fetch(PDO::FETCH_ASSOC);
        return is_array($fila) ? $fila : null;
    }

    /**
     * Busca o crea la conversación (co_cli, phone_number_id). Nueva
     * conversación siempre arranca en modo='ia'.
     *
     * @return array<string,mixed>
     */
    public function buscarOCrearConversacion(
        string $coCli,
        string $nombreCliente,
        string $telefonoCliente,
        string $phoneNumberId
    ): array {
        $stmt = $this->pdo->prepare(
            'SELECT * FROM atencion_conversaciones WHERE co_cli = ? AND phone_number_id = ?'
        );
        $stmt->execute([$coCli, $phoneNumberId]);
        $fila = $stmt->fetch(PDO::FETCH_ASSOC);
        if (is_array($fila)) {
            return $fila;
        }

        $ins = $this->pdo->prepare(
            'INSERT INTO atencion_conversaciones (co_cli, nombre_cliente, telefono_cliente, phone_number_id) '
            . 'VALUES (?, ?, ?, ?)'
        );
        $ins->execute([$coCli, $nombreCliente, $telefonoCliente, $phoneNumberId]);
        $id = (int) $this->pdo->lastInsertId();

        $stmt2 = $this->pdo->prepare('SELECT * FROM atencion_conversaciones WHERE id = ?');
        $stmt2->execute([$id]);
        $nueva = $stmt2->fetch(PDO::FETCH_ASSOC);
        return is_array($nueva) ? $nueva : [
            'id' => $id, 'co_cli' => $coCli, 'modo' => 'ia', 'phone_number_id' => $phoneNumberId,
        ];
    }

    /** Registra el mensaje entrante del cliente (siempre, sin importar el modo). */
    public function registrarMensajeCliente(int $conversacionId, string $texto): void
    {
        $this->pdo->prepare(
            "INSERT INTO atencion_mensajes (conversacion_id, remitente, tipo, contenido, estado) "
            . "VALUES (?, 'cliente', 'texto', ?, 'entregado')"
        )->execute([$conversacionId, $texto]);
        $this->actualizarConversacion($conversacionId, $texto, incrementarUnread: true);
    }

    /** Registra la respuesta de la IA (solo cuando modo='ia'). */
    public function registrarMensajeIA(int $conversacionId, string $texto, string $origenDatos = ''): void
    {
        $this->pdo->prepare(
            "INSERT INTO atencion_mensajes (conversacion_id, remitente, tipo, contenido, origen_datos, estado) "
            . "VALUES (?, 'ia', 'texto', ?, ?, 'entregado')"
        )->execute([$conversacionId, $texto, $origenDatos]);
        $this->actualizarConversacion($conversacionId, $texto, incrementarUnread: false);
    }

    private function actualizarConversacion(int $conversacionId, string $ultimoMensaje, bool $incrementarUnread): void
    {
        $extra = $incrementarUnread ? ', unread_count = unread_count + 1' : '';
        $stmt = $this->pdo->prepare(
            'UPDATE atencion_conversaciones '
            . 'SET ultimo_mensaje = ?, fecha_actualizacion = CURRENT_TIMESTAMP' . $extra
            . ' WHERE id = ?'
        );
        $stmt->execute([mb_substr($ultimoMensaje, 0, 200), $conversacionId]);
    }
}

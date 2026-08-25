<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use App\Services\NvidiaBrain\Tools\Common\Envelope;
use PDO;
use PDOException;

require_once __DIR__ . '/../Common/Envelope.php';
require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * GESTIÓN CRUD de notas de entrega en estado Pendiente (AD1 / v4.17).
 *
 * Operaciones (parámetro accion):
 *  - crear     (POST): crea una nota Pendiente en las tablas locales app_
 *    (MySQL legacy, NUNCA Profit). Valida que co_cli exista en saCliente
 *    (clientes, PRUEB25, SELECT-only) y que haya stock disponible por línea
 *    (st_almac/art, PRUEB25). Registra en app_log_notas.
 *  - modificar (PUT): actualiza la nota SOLO si estado=Pendiente. Si estado
 *    es Modificado o Procesado, rechaza con código HTTP_409_CONFLICTO. Tras
 *    modificar, la nota pasa a estado=Modificado y se audita en app_log_notas.
 *  - eliminar  (DELETE): borra la nota SOLO si estado=Pendiente; registra en
 *    app_log_notas y app_log_eliminaciones (motivo + monto).
 *
 * Persistencia: MySQL legacy vía AlmacenDbTrait (tablas app_notas_gestion,
 * app_notas_gestion_items, app_log_notas, app_log_eliminaciones). Profit
 * (PRUEB25) se consulta SOLO lectura para validar cliente y stock.
 *
 * Envelope estándar v4.17: success/data/message/timestamp (+ card, + ok).
 * La validación de stock es best-effort: si la tabla de stock no se resuelve,
 * se degrada con advertencia (nunca bloquea la operación por schema ausente).
 */
final class GestionNotasTool implements AgentToolInterface
{
    use AlmacenDbTrait;

    /** Tablas candidatas de clientes (saCliente) en PRUEB25. */
    private const TABLAS_CLIENTES = ['ClientesDrogueria', 'clientes', 'saCliente'];

    /** Tablas candidatas de stock en PRUEB25. */
    private const TABLAS_STOCK = ['st_almac', 'existencia', 'saStock', 'art'];

    public function getName(): string
    {
        return 'gestion_notas';
    }

    public function getDescription(): string
    {
        return 'Gestión CRUD de notas de entrega en estado Pendiente: crear, '
             . 'modificar y eliminar notas usando las tablas locales app_ (el ERP '
             . 'Profit se mantiene solo lectura). Valida el cliente (saCliente) y '
             . 'el stock disponible por línea antes de crear. Al modificar o '
             . 'eliminar exige estado=Pendiente; si la nota está Modificada o '
             . 'Procesada rechaza con HTTP 409. Toda operación queda auditada en '
             . 'app_log_notas. Parámetros: accion (crear|modificar|eliminar), '
             . 'num_nota, co_cli, lineas [{co_art,cantidad}], usuario, motivo.';
    }

    public function getParameters(): array
    {
        return [
            'type'       => 'object',
            'properties' => [
                'accion' => [
                    'type'        => 'string',
                    'enum'        => ['crear', 'modificar', 'eliminar'],
                    'description' => 'Operación CRUD a ejecutar (obligatoria).',
                ],
                'num_nota' => [
                    'type'        => 'string',
                    'description' => 'Número de la nota (obligatorio en modificar/eliminar; en crear se auto-genera).',
                ],
                'co_cli' => [
                    'type'        => 'string',
                    'description' => 'Código de cliente en saCliente (obligatorio en crear/modificar).',
                ],
                'vendedor' => [
                    'type'        => 'string',
                    'description' => 'Código de vendedor (opcional).',
                ],
                'fecha_emision' => [
                    'type'        => 'string',
                    'description' => 'Fecha de emisión ISO (opcional; default hoy).',
                ],
                'lineas' => [
                    'type'        => 'array',
                    'description' => 'Lista de líneas [{co_art, cantidad, precio_unitario?}] (obligatorio en crear).',
                ],
                'usuario' => [
                    'type'        => 'string',
                    'description' => 'Usuario que ejecuta la operación (para auditoría).',
                ],
                'motivo' => [
                    'type'        => 'string',
                    'description' => 'Motivo de la eliminación (solo en eliminar).',
                ],
            ],
            'required'   => ['accion'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $accion = strtolower(trim((string) ($arguments['accion'] ?? '')));
        if (!in_array($accion, ['crear', 'modificar', 'eliminar'], true)) {
            return Envelope::error('Parámetro "accion" debe ser crear, modificar o eliminar.');
        }

        $numNota   = trim((string) ($arguments['num_nota'] ?? ''));
        $coCli     = trim((string) ($arguments['co_cli'] ?? ''));
        $vendedor  = trim((string) ($arguments['vendedor'] ?? ''));
        $fechaEmis = trim((string) ($arguments['fecha_emision'] ?? date('Y-m-d H:i:s')));
        $usuario   = trim((string) ($arguments['usuario'] ?? $contexto['nombre_usuario'] ?? 'ara'));
        $motivo    = trim((string) ($arguments['motivo'] ?? ''));
        $lineas    = is_array($arguments['lineas'] ?? null) ? $arguments['lineas'] : [];

        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            return Envelope::error('MySQL legacy no disponible para gestión de notas: ' . self::aUtf8($e->getMessage()));
        }
        $this->garantizarTablas($pdo);

        switch ($accion) {
            case 'crear':
                return $this->crear($pdo, $coCli, $vendedor, $fechaEmis, $lineas, $usuario);
            case 'modificar':
                return $this->modificar($pdo, $numNota, $coCli, $vendedor, $lineas, $usuario);
            case 'eliminar':
                return $this->eliminar($pdo, $numNota, $usuario, $motivo);
        }
        return Envelope::error('Operación no soportada.');
    }

    // ───────────────────────────────────────────────────────────────────────
    // OPERACIONES CRUD
    // ───────────────────────────────────────────────────────────────────────

    private function crear(PDO $pdo, string $coCli, string $vendedor, string $fechaEmis, array $lineas, string $usuario): array
    {
        if ($coCli === '') {
            return Envelope::error('Parámetro "co_cli" es obligatorio para crear una nota.');
        }
        if ($lineas === []) {
            return Envelope::error('Debe enviar al menos una línea en "lineas" [{co_art, cantidad}].');
        }

        // 1) Validar que co_cli exista en saCliente (Profit, solo lectura).
        $cliente = $this->validarCliente($coCli);
        if ($cliente === null) {
            return Envelope::error('El cliente "' . $coCli . '" no existe en saCliente (PRUEB25).', 'CLIENTE_INEXISTENTE');
        }

        // 2) Validar stock disponible por línea (best-effort).
        $advertencias = [];
        foreach ($lineas as $i => $linea) {
            $coArt = trim((string) ($linea['co_art'] ?? $linea['codigo'] ?? ''));
            $cantidad = (float) ($linea['cantidad'] ?? 0);
            $val = $this->validarStock($coArt, $cantidad);
            if ($val === false) {
                return Envelope::error(
                    'Sin stock suficiente para el artículo "' . $coArt . '" (solicitado ' . $cantidad . ').',
                    'STOCK_INSUFICIENTE'
                );
            }
            if ($val === null) {
                $advertencias[] = 'Stock no verificable para "' . $coArt . '" (tabla de stock ausente); se registra la línea igual.';
            }
        }

        // 3) Insertar cabecera + líneas en tablas locales app_.
        try {
            $docNum = $this->generarNumero($pdo);
            $totalMonto = 0.0;
            $totalItems = 0;
            foreach ($lineas as $linea) {
                $cantidad = (float) ($linea['cantidad'] ?? 0);
                $precio = (float) ($linea['precio_unitario'] ?? 0);
                $totalMonto += $cantidad * $precio;
                $totalItems += (int) $cantidad;
            }

            $pdo->beginTransaction();
            $st = $pdo->prepare(
                'INSERT INTO `app_notas_gestion` '
                . '(`doc_num`,`co_cli`,`cli_des`,`fecha_emision`,`vendedor`,`estado`,'
                . '`total_items`,`total_monto`,`creada_por`) '
                . 'VALUES (?,?,?,?,?,?,?,?,?)'
            );
            $st->execute([
                $docNum, $coCli, $cliente['cli_des'], $fechaEmis, $vendedor,
                'Pendiente', $totalItems, $totalMonto, $usuario,
            ]);
            $notaId = (int) $pdo->lastInsertId();

            $st = $pdo->prepare(
                'INSERT INTO `app_notas_gestion_items` '
                . '(`nota_id`,`co_art`,`art_des`,`cantidad`,`precio_unitario`,`total_linea`) '
                . 'VALUES (?,?,?,?,?,?)'
            );
            foreach ($lineas as $linea) {
                $coArt = trim((string) ($linea['co_art'] ?? $linea['codigo'] ?? ''));
                $cantidad = (float) ($linea['cantidad'] ?? 0);
                $precio = (float) ($linea['precio_unitario'] ?? 0);
                $st->execute([$notaId, $coArt, '', $cantidad, $precio, $cantidad * $precio]);
            }
            $pdo->commit();

            $this->logAuditoria($pdo, $docNum, 'CREADA', $usuario, 'Nota creada con ' . count($lineas) . ' línea(s)');
        } catch (PDOException $e) {
            if ($pdo->inTransaction()) {
                $pdo->rollBack();
            }
            return Envelope::error('Error al crear la nota: ' . self::aUtf8($e->getMessage()));
        }
        $pdo = null;

        return Envelope::conCard(
            Envelope::ok([
                'accion'       => 'crear',
                'num_nota'     => $docNum,
                'co_cli'       => $coCli,
                'estado'       => 'Pendiente',
                'total_items'  => $totalItems,
                'total_monto'  => $totalMonto,
                'advertencias' => $advertencias,
            ], 'Nota ' . $docNum . ' creada en estado Pendiente.'),
            $this->cardGestion('📝', 'NOTA CREADA', $docNum, 'Pendiente', $totalItems, $totalMonto, $usuario)
        );
    }

    private function modificar(PDO $pdo, string $numNota, string $coCli, string $vendedor, array $lineas, string $usuario): array
    {
        if ($numNota === '') {
            return Envelope::error('Parámetro "num_nota" es obligatorio para modificar.');
        }

        $nota = $this->buscarNota($pdo, $numNota);
        if ($nota === null) {
            return Envelope::error('La nota "' . $numNota . '" no existe en la gestión local app_.', 'NOTA_INEXISTENTE');
        }
        $estado = strtolower(trim((string) ($nota['estado'] ?? '')));
        if ($estado !== 'pendiente') {
            return Envelope::conflicto('No se puede modificar una nota en estado ' . ucfirst($estado));
        }

        if ($coCli !== '' && $coCli !== (string) ($nota['co_cli'] ?? '')) {
            if ($this->validarCliente($coCli) === null) {
                return Envelope::error('El cliente "' . $coCli . '" no existe en saCliente (PRUEB25).', 'CLIENTE_INEXISTENTE');
            }
        }

        // Validación de stock si se cambian líneas (best-effort).
        $advertencias = [];
        if ($lineas !== []) {
            foreach ($lineas as $linea) {
                $coArt = trim((string) ($linea['co_art'] ?? $linea['codigo'] ?? ''));
                $cantidad = (float) ($linea['cantidad'] ?? 0);
                $val = $this->validarStock($coArt, $cantidad);
                if ($val === false) {
                    return Envelope::error('Sin stock suficiente para el artículo "' . $coArt . '".', 'STOCK_INSUFICIENTE');
                }
                if ($val === null) {
                    $advertencias[] = 'Stock no verificable para "' . $coArt . '".';
                }
            }
        }

        try {
            $pdo->beginTransaction();
            $totalMonto = 0.0;
            $totalItems = 0;
            if ($lineas !== []) {
                $del = $pdo->prepare('DELETE FROM `app_notas_gestion_items` WHERE `nota_id` = ?');
                $del->execute([(int) $nota['id']]);
                $ins = $pdo->prepare(
                    'INSERT INTO `app_notas_gestion_items` '
                    . '(`nota_id`,`co_art`,`art_des`,`cantidad`,`precio_unitario`,`total_linea`) '
                    . 'VALUES (?,?,?,?,?,?)'
                );
                foreach ($lineas as $linea) {
                    $coArt = trim((string) ($linea['co_art'] ?? $linea['codigo'] ?? ''));
                    $cantidad = (float) ($linea['cantidad'] ?? 0);
                    $precio = (float) ($linea['precio_unitario'] ?? 0);
                    $totalMonto += $cantidad * $precio;
                    $totalItems += (int) $cantidad;
                    $ins->execute([(int) $nota['id'], $coArt, '', $cantidad, $precio, $cantidad * $precio]);
                }
            } else {
                $totalMonto = (float) ($nota['total_monto'] ?? 0);
                $totalItems = (int) ($nota['total_items'] ?? 0);
            }
            $upd = $pdo->prepare(
                'UPDATE `app_notas_gestion` SET `estado` = ?, `total_items` = ?, '
                . '`total_monto` = ?, `vendedor` = ? WHERE `id` = ?'
            );
            $upd->execute(['Modificado', $totalItems, $totalMonto, $vendedor !== '' ? $vendedor : ($nota['vendedor'] ?? ''), (int) $nota['id']]);
            $pdo->commit();
            $this->logAuditoria($pdo, $numNota, 'MODIFICADA', $usuario, 'Nota actualizada (líneas: ' . count($lineas) . ')');
        } catch (PDOException $e) {
            if ($pdo->inTransaction()) {
                $pdo->rollBack();
            }
            return Envelope::error('Error al modificar la nota: ' . self::aUtf8($e->getMessage()));
        }
        $pdo = null;

        return Envelope::conCard(
            Envelope::ok([
                'accion'       => 'modificar',
                'num_nota'     => $numNota,
                'estado'       => 'Modificado',
                'total_items'  => $totalItems,
                'total_monto'  => $totalMonto,
                'advertencias' => $advertencias,
            ], 'Nota ' . $numNota . ' modificada; estado ahora Modificado.'),
            $this->cardGestion('✏️', 'NOTA MODIFICADA', $numNota, 'Modificado', $totalItems, $totalMonto, $usuario)
        );
    }

    private function eliminar(PDO $pdo, string $numNota, string $usuario, string $motivo): array
    {
        if ($numNota === '') {
            return Envelope::error('Parámetro "num_nota" es obligatorio para eliminar.');
        }
        $nota = $this->buscarNota($pdo, $numNota);
        if ($nota === null) {
            return Envelope::error('La nota "' . $numNota . '" no existe en la gestión local app_.', 'NOTA_INEXISTENTE');
        }
        $estado = strtolower(trim((string) ($nota['estado'] ?? '')));
        if ($estado !== 'pendiente') {
            return Envelope::conflicto('No se puede eliminar una nota en estado ' . ucfirst($estado));
        }

        $montoTotal = (float) ($nota['total_monto'] ?? 0);
        try {
            $pdo->beginTransaction();
            $delItems = $pdo->prepare('DELETE FROM `app_notas_gestion_items` WHERE `nota_id` = ?');
            $delItems->execute([(int) $nota['id']]);
            $del = $pdo->prepare('DELETE FROM `app_notas_gestion` WHERE `id` = ?');
            $del->execute([(int) $nota['id']]);

            $st = $pdo->prepare(
                'INSERT INTO `app_log_eliminaciones` '
                . '(`nota_id`,`doc_num`,`usuario`,`fecha_eliminacion`,`motivo`,`monto_total`) '
                . 'VALUES (?,?,?,NOW(),?,?)'
            );
            $st->execute([$numNota, $numNota, $usuario, $motivo, $montoTotal]);
            $pdo->commit();
            $this->logAuditoria($pdo, $numNota, 'ELIMINADA', $usuario, $motivo !== '' ? $motivo : 'Eliminación por operador');
        } catch (PDOException $e) {
            if ($pdo->inTransaction()) {
                $pdo->rollBack();
            }
            return Envelope::error('Error al eliminar la nota: ' . self::aUtf8($e->getMessage()));
        }
        $pdo = null;

        return Envelope::conCard(
            Envelope::ok([
                'accion'       => 'eliminar',
                'num_nota'     => $numNota,
                'monto_total'  => $montoTotal,
                'motivo'       => $motivo,
            ], 'Nota ' . $numNota . ' eliminada (monto ' . number_format($montoTotal, 2, ',', '.') . ').'),
            $this->cardGestion('🗑️', 'NOTA ELIMINADA', $numNota, 'Eliminada', (int) ($nota['total_items'] ?? 0), $montoTotal, $usuario)
        );
    }

    // ───────────────────────────────────────────────────────────────────────
    // HELPERS
    // ───────────────────────────────────────────────────────────────────────

    /** Busca la nota en app_notas_gestion (local). */
    private function buscarNota(PDO $pdo, string $numNota): ?array
    {
        try {
            $st = $pdo->prepare(
                'SELECT * FROM `app_notas_gestion` WHERE `doc_num` = ? ORDER BY `id` DESC LIMIT 1'
            );
            $st->execute([$numNota]);
            $fila = $st->fetch(PDO::FETCH_ASSOC);
            return is_array($fila) ? $fila : null;
        } catch (PDOException $e) {
            return null;
        }
    }

    /**
     * Valida que co_cli exista en saCliente (Profit PRUEB25, solo lectura).
     *
     * @return array{co_cli:string,cli_des:string}|null null si no existe o si
     *         la tabla de clientes no se puede resolver.
     */
    private function validarCliente(string $coCli): ?array
    {
        $wrapper = new ConnectionWrapper();
        $tabla = $wrapper->resolverTabla(self::TABLAS_CLIENTES);
        if ($tabla === null) {
            return null;
        }
        $cols = $wrapper->resolverColumnas($tabla, [
            'id'    => ['co_cli', 'Id', 'cliente_id'],
            'nombre'=> ['cli_des', 'nombre', 'razon_social'],
        ]);
        if ($cols['id'] === null) {
            return null;
        }
        try {
            $filas = $wrapper->querySafe(
                'SELECT TOP 1 ' . $wrapper->qPara($cols['id']) . ' AS co_cli'
                . ($cols['nombre'] !== null ? ', ' . $wrapper->qPara($cols['nombre']) . ' AS cli_des' : '')
                . ' FROM ' . $wrapper->qPara($tabla) . ' WITH (NOLOCK)'
                . ' WHERE RTRIM(LTRIM(' . $wrapper->qPara($cols['id']) . ')) = ?',
                [$coCli],
                0
            );
            if ($filas === []) {
                return null;
            }
            return [
                'co_cli'  => trim((string) ($filas[0]['co_cli'] ?? $coCli)),
                'cli_des' => trim((string) ($filas[0]['cli_des'] ?? '')),
            ];
        } catch (\Throwable $e) {
            return null;
        }
    }

    /**
     * Valida stock disponible (best-effort).
     *
     * @return bool|null true=ok, false=insuficiente, null=no verificable.
     */
    private function validarStock(string $coArt, float $cantidad): ?bool
    {
        if ($coArt === '' || $cantidad <= 0) {
            return true;
        }
        $wrapper = new ConnectionWrapper();
        $tabla = $wrapper->resolverTabla(self::TABLAS_STOCK);
        if ($tabla === null) {
            return null;
        }
        $cols = $wrapper->resolverColumnas($tabla, [
            'art'   => ['co_art', 'cod_art', 'articulo'],
            'stock' => ['stock_act', 'disponible', 'stock', 'existencia', 'existencias', 'stock_actual'],
        ]);
        if ($cols['art'] === null || $cols['stock'] === null) {
            return null;
        }
        try {
            $filas = $wrapper->querySafe(
                'SELECT TOP 1 ' . $wrapper->qPara($cols['stock']) . ' AS stock'
                . ' FROM ' . $wrapper->qPara($tabla) . ' WITH (NOLOCK)'
                . ' WHERE RTRIM(LTRIM(' . $wrapper->qPara($cols['art']) . ')) = ?',
                [$coArt],
                0
            );
            if ($filas === []) {
                return null; // artículo no existe en stock: no verificable
            }
            $stock = (float) ($filas[0]['stock'] ?? 0);
            return $stock >= $cantidad;
        } catch (\Throwable $e) {
            return null;
        }
    }

    /** Registra un evento en app_log_notas (auditoría local). */
    private function logAuditoria(PDO $pdo, string $notaId, string $accion, string $usuario, string $detalle): void
    {
        try {
            $st = $pdo->prepare(
                'INSERT INTO `app_log_notas` (`nota_id`,`accion`,`usuario`,`fecha`,`detalle`) '
                . 'VALUES (?,?,?,NOW(),?)'
            );
            $st->execute([$notaId, $accion, $usuario, $detalle]);
        } catch (PDOException $e) {
            error_log('[GestionNotasTool] logAuditoria falló: ' . $e->getMessage());
        }
    }

    /** Genera un número de nota secuencial ARA<YYYYMMDD><n>. */
    private function generarNumero(PDO $pdo): string
    {
        try {
            $fila = $pdo->query('SELECT COUNT(*) AS n FROM `app_notas_gestion`')->fetch(PDO::FETCH_ASSOC);
            $n = ((int) ($fila['n'] ?? 0)) + 1;
        } catch (PDOException $e) {
            $n = random_int(1, 99999);
        }
        return 'ARA' . date('Ymd') . str_pad((string) $n, 4, '0', STR_PAD_LEFT);
    }

    /** Crea las tablas locales app_ si no existen (idempotente, best-effort). */
    private function garantizarTablas(PDO $pdo): void
    {
        $ddl = [
            'app_log_notas' => 'CREATE TABLE IF NOT EXISTS `app_log_notas` ('
                . '`id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,'
                . '`nota_id` VARCHAR(40) NOT NULL, `accion` VARCHAR(30) NOT NULL,'
                . '`usuario` VARCHAR(80) NOT NULL DEFAULT "", `fecha` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,'
                . '`detalle` VARCHAR(500) NULL, KEY `idx_aln_nota` (`nota_id`)) '
                . 'ENGINE=InnoDB DEFAULT CHARSET=utf8mb4',
            'app_log_eliminaciones' => 'CREATE TABLE IF NOT EXISTS `app_log_eliminaciones` ('
                . '`id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, `nota_id` VARCHAR(40) NOT NULL,'
                . '`doc_num` VARCHAR(40) NOT NULL DEFAULT "", `usuario` VARCHAR(80) NOT NULL DEFAULT "",'
                . '`fecha_eliminacion` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, `motivo` VARCHAR(255) NULL,'
                . '`monto_total` DECIMAL(18,2) NOT NULL DEFAULT 0) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4',
            'app_notas_gestion' => 'CREATE TABLE IF NOT EXISTS `app_notas_gestion` ('
                . '`id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, `doc_num` VARCHAR(40) NOT NULL,'
                . '`co_cli` VARCHAR(20) NOT NULL, `cli_des` VARCHAR(200) NULL, `fecha_emision` DATETIME NULL,'
                . '`vendedor` VARCHAR(20) NULL, `estado` VARCHAR(20) NOT NULL DEFAULT "Pendiente",'
                . '`total_items` INT NOT NULL DEFAULT 0, `total_monto` DECIMAL(18,2) NOT NULL DEFAULT 0,'
                . '`creada_por` VARCHAR(80) NOT NULL DEFAULT "", `creada_en` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,'
                . 'KEY `idx_ang_estado` (`estado`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4',
            'app_notas_gestion_items' => 'CREATE TABLE IF NOT EXISTS `app_notas_gestion_items` ('
                . '`id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, `nota_id` BIGINT UNSIGNED NOT NULL,'
                . '`co_art` VARCHAR(30) NOT NULL, `art_des` VARCHAR(200) NULL, `cantidad` DECIMAL(18,2) NOT NULL DEFAULT 0,'
                . '`precio_unitario` DECIMAL(18,4) NOT NULL DEFAULT 0, `total_linea` DECIMAL(18,4) NOT NULL DEFAULT 0,'
                . 'KEY `idx_angi_nota` (`nota_id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4',
        ];
        foreach ($ddl as $ddlSql) {
            try {
                $pdo->exec($ddlSql);
            } catch (PDOException $e) {
                error_log('[GestionNotasTool] CREATE TABLE falló: ' . $e->getMessage());
            }
        }
    }

    private function cardGestion(string $emoji, string $titulo, string $numNota, string $estado, int $items, float $monto, string $usuario): string
    {
        return CardBuilder::iniciar($emoji, $titulo)
            ->campo('NOTA', $numNota)
            ->campo('ESTADO', $estado, 'estado')
            ->campo('ÍTEMS', $items, 'numero')
            ->campo('MONTO', $monto, 'moneda')
            ->campo('USUARIO', $usuario !== '' ? $usuario : 'ara')
            ->footer('Fuente: MySQL app_ (Profit PRUEB25 solo lectura)')
            ->tarjeta();
    }

    /** Definition consolidada (nombre + descripción + esquema) para el LLM. */
    public function getDefinition(): array
    {
        return [
            'name'        => $this->getName(),
            'description' => $this->getDescription(),
            'parameters'  => $this->getParameters(),
        ];
    }
}

<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDOException;

/**
 * Busca productos en el inventario del ERP (SQL Server).
 *
 * Consulta la tabla InventarioDrogueria (ERP Droguería); si no existe, hace
 * fallback a la tabla "art" del Profit para que la herramienta siga operativa.
 * Los nombres de columna se resuelven dinámicamente vía INFORMATION_SCHEMA
 * (sin esquema fijo), buscando sinónimos de: código, nombre de producto,
 * principio activo, stock y precio.
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]          → éxito
 *   ['success' => false, 'error' => 'mensaje']     → error controlado
 *
 * Configuración (variables de entorno, con defaults):
 *   PROFIT_SQL_HOST/PORT/USER/PASS/NAME/DRIVER  (fallback PROFIT_DB_* y luego
 *   defaults: 192.168.4.20:1433, profit/profit, PRUEB25, "SQL Server").
 *
 * Uso:
 *   $registry->registerTool(new BuscarInventarioTool());
 */
final class BuscarInventarioTool implements AgentToolInterface
{
    /** Tablas candidatas de inventario, en orden de preferencia. */
    private const TABLAS = ['InventarioDrogueria', 'art'];

    /** Sinónimos de columna por campo lógico (primer candidato existente gana). */
    private const CAMPOS = [
        'codigo'      => ['codigo', 'co_art', 'id_producto', 'cod_art', 'producto_id'],
        'nombre'      => ['NombreProducto', 'art_des', 'nombre_producto', 'descripcion', 'nombre'],
        'principio'   => ['PrincipioActivo', 'prin_act', 'principio_activo', 'campo1'],
        'vencimiento' => ['fv', 'fec_vto', 'fecha_vto', 'vencimiento', 'fecha_vencimiento'],
        'stock'       => ['disponible', 'stock', 'stock_act', 'existencia', 'existencias', 'stock_actual'],
        'precio'      => ['precio', 'precio1', 'precio_venta', 'precio_compra', 'precio_unitario'],
        'comprometido'=> ['stock_comprometido', 'stock_com', 'comprometido', 'reservado', 'comprom'],
        'linea'       => ['co_lin', 'linea', 'cod_linea', 'id_linea'],
        'sublinea'    => ['co_subl', 'sub_linea', 'sublinea', 'cod_subl', 'id_sublinea'],
        'fec_mov'     => ['fec_lac', 'ultima_fecha_movimiento', 'fecha_ultimo_mov', 'fec_ult_mov', 'fec_ing', 'fecha_mod'],
    ];

    /** Máximo de resultados devueltos al LLM. */
    private const LIMITE = 50;

    public function getName(): string
    {
        return 'buscar_inventario';
    }

    public function getDescription(): string
    {
        return 'Busca productos en el inventario del ERP (SQL Server) por nombre de '
             . 'producto o principio activo y devuelve código, stock disponible y '
             . 'precio. Úsala cuando el usuario pregunte por disponibilidad, '
             . 'existencias, stock o precio de medicamentos/productos. '
             . 'Parámetros: busqueda (texto parcial a buscar, obligatorio) y '
             . 'solo_disponibles (true para filtrar solo artículos con stock > 0).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'busqueda' => [
                    'type'        => 'string',
                    'description' => 'Nombre de producto o principio activo a buscar (búsqueda parcial, insensible a mayúsculas).',
                ],
                'co_art' => [
                    'type'        => 'string',
                    'description' => 'Código de artículo. Prioriza coincidencia exacta; si no hay resultados hace fallback a LIKE (co_art XXX%).',
                ],
                'q' => [
                    'type'        => 'string',
                    'description' => 'Texto a buscar en la descripción (art_des, LIKE %q%).',
                ],
                'co_lin' => [
                    'type'        => 'string',
                    'description' => 'Filtro por línea de producto (co_lin).',
                ],
                'co_subl' => [
                    'type'        => 'string',
                    'description' => 'Filtro por sub-línea de producto (co_subl).',
                ],
                'solo_disponibles' => [
                    'type'        => 'boolean',
                    'description' => 'Si es true, solo devuelve artículos con stock mayor a 0.',
                ],
            ],
            'required' => [],
        ];
    }

    /**
     * {@inheritDoc}
     */
    public function execute(array $arguments, array $contexto): array
    {
        // Captura flexible (texto plano, v4.9): 'busqueda' → posición 0 →
        // 'query' → texto crudo reunido de cualquier clave (el frontend manda
        // {text: "evigax"} para "/buscar_inventario evigax").
        $busqueda = trim((string) ($arguments['busqueda'] ?? $arguments[0] ?? $arguments['query'] ?? ''));
        if ($busqueda === '') {
            $busqueda = trim(self::reunirTexto($arguments));
        }
        // Fase 4.17 (AD2): nuevos filtros de búsqueda.
        $coArt  = trim((string) ($arguments['co_art'] ?? ''));
        $q      = trim((string) ($arguments['q'] ?? ''));
        $coLin  = trim((string) ($arguments['co_lin'] ?? ''));
        $coSubl = trim((string) ($arguments['co_subl'] ?? ''));

        // Bug real (visto en ara_inteligente_mensajes: "[22001] Datos tipo
        // String, se truncarán por la derecha" desde este mismo tool) — el
        // LLM a veces manda un parámetro de búsqueda larguísimo (repite el
        // pedido del usuario entero en vez de solo el término), y el driver
        // ODBC infiere el ancho del parámetro por la columna destino
        // (co_art/nombre suelen ser CHAR/VARCHAR cortos en Profit) y tira
        // error en vez de simplemente no encontrar nada. Cortamos ANTES de
        // que llegue al driver — 50 caracteres es de sobra para cualquier
        // nombre de producto o código real.
        $busqueda = mb_substr($busqueda, 0, 50);
        $coArt    = mb_substr($coArt, 0, 50);
        $q        = mb_substr($q, 0, 50);
        $coLin    = mb_substr($coLin, 0, 50);
        $coSubl   = mb_substr($coSubl, 0, 50);
        $soloDisponibles = (bool) ($arguments['solo_disponibles'] ?? false);

        if ($busqueda === '' && $coArt === '' && $q === '' && $coLin === '' && $coSubl === '') {
            return [
                'success' => false,
                'error'   => 'Debe enviar al menos un criterio: "busqueda" (nombre/principio activo), "co_art" (código), "q" (descripción), "co_lin" o "co_subl".',
            ];
        }

        // ── 1) Conexión protegida (ConnectionWrapper v4.14: SELECT-only,
        //       NOLOCK forzado, timeout 30s, cache local) y resolución de tabla ──
        $wrapper = new ConnectionWrapper();
        $tabla = $wrapper->resolverTabla(self::TABLAS);
        if ($tabla === null) {
            try {
                $wrapper->queryProfit('SELECT 1');
            } catch (PDOException $e) {
                return ['success' => false, 'error' => 'No se pudo conectar a SQL Server: ' . self::aUtf8($e->getMessage())];
            }
            return [
                'success' => false,
                'error'   => sprintf(
                    'Ninguna tabla de inventario encontrada (buscadas: %s). Verifique la conexión o la configuración PROFIT_SQL_*.',
                    implode(', ', self::TABLAS)
                ),
            ];
        }

        $cols = $wrapper->resolverColumnas($tabla, self::CAMPOS);
        if ($cols['codigo'] === null || $cols['nombre'] === null) {
            return [
                'success' => false,
                'error'   => sprintf(
                    'La tabla %s no tiene una columna de código o nombre de producto (sinónimos esperados: %s).',
                    $tabla,
                    implode(', ', array_merge(self::CAMPOS['codigo'], self::CAMPOS['nombre']))
                ),
            ];
        }

        // ── 2) Construcción de la consulta (AD2) ─────────────────────────────
        $where = [];
        $params = [];

        if ($coArt !== '') {
            // Prioridad: coincidencia EXACTA de código primero. El literal del
            // primer intento va escapado y se incrusta con parámetros; si no
            // hay resultados, se reintenta con LIKE 'XXX%' (fallback).
            $coArtEsc = $this->escaparLike($coArt);
            $sql = 'SELECT TOP ' . self::LIMITE . ' ' . $this->seleccionar($tabla, $cols)
                 . ' FROM ' . self::q($tabla) . ' WITH (NOLOCK)'
                 . ' WHERE RTRIM(LTRIM(' . self::q($cols['codigo']) . ')) = ?';
            $params = [$coArt];
            if ($coLin !== '' && $cols['linea'] !== null) {
                $sql .= ' AND RTRIM(LTRIM(' . self::q($cols['linea']) . ')) = ?';
                $params[] = $coLin;
            }
            if ($coSubl !== '' && $cols['sublinea'] !== null) {
                $sql .= ' AND RTRIM(LTRIM(' . self::q($cols['sublinea']) . ')) = ?';
                $params[] = $coSubl;
            }
            try {
                $filas = $wrapper->queryProfit($sql, $params);
            } catch (PDOException $e) {
                return ['success' => false, 'error' => 'Error al consultar inventario: ' . self::aUtf8($e->getMessage())];
            }
            if ($filas === []) {
                // Fallback: LIKE 'XXX%' sobre el código.
                $sql = 'SELECT TOP ' . self::LIMITE . ' ' . $this->seleccionar($tabla, $cols)
                     . ' FROM ' . self::q($tabla) . ' WITH (NOLOCK)'
                     . ' WHERE RTRIM(LTRIM(' . self::q($cols['codigo']) . ")) LIKE ? ESCAPE '\\'";
                $params = [$coArtEsc . '%'];
                if ($coLin !== '' && $cols['linea'] !== null) {
                    $sql .= ' AND RTRIM(LTRIM(' . self::q($cols['linea']) . ')) = ?';
                    $params[] = $coLin;
                }
                if ($coSubl !== '' && $cols['sublinea'] !== null) {
                    $sql .= ' AND RTRIM(LTRIM(' . self::q($cols['sublinea']) . ')) = ?';
                    $params[] = $coSubl;
                }
                try {
                    $filas = $wrapper->queryProfit($sql, $params);
                } catch (PDOException $e) {
                    return ['success' => false, 'error' => 'Error al consultar inventario (fallback): ' . self::aUtf8($e->getMessage())];
                }
            }
        } else {
            // Búsqueda por nombre (busqueda), descripción (q), línea y sub-línea.
            if ($busqueda !== '') {
                $patron = $this->escaparLike($busqueda);
                $where[] = '(' . self::q($cols['nombre']) . " LIKE ? ESCAPE '\\'"
                    . ($cols['principio'] !== null ? ' OR ' . self::q($cols['principio']) . " LIKE ? ESCAPE '\\'" : '')
                    . ')';
                $params[] = '%' . $patron . '%';
                if ($cols['principio'] !== null) {
                    $params[] = '%' . $patron . '%';
                }
            }
            if ($q !== '') {
                $patron = $this->escaparLike($q);
                $where[] = self::q($cols['nombre']) . " LIKE ? ESCAPE '\\'";
                $params[] = '%' . $patron . '%';
            }
            if ($coLin !== '' && $cols['linea'] !== null) {
                $where[] = 'RTRIM(LTRIM(' . self::q($cols['linea']) . ')) = ?';
                $params[] = $coLin;
            }
            if ($coSubl !== '' && $cols['sublinea'] !== null) {
                $where[] = 'RTRIM(LTRIM(' . self::q($cols['sublinea']) . ')) = ?';
                $params[] = $coSubl;
            }
            if ($soloDisponibles && $cols['stock'] !== null) {
                $where[] = '(' . self::q($cols['stock']) . ' IS NOT NULL AND ' . self::q($cols['stock']) . ' > 0)';
            }

            $sql = 'SELECT TOP ' . self::LIMITE . ' ' . $this->seleccionar($tabla, $cols)
                 . ' FROM ' . self::q($tabla) . ' WITH (NOLOCK)';
            if ($where !== []) {
                $sql .= ' WHERE ' . implode(' AND ', $where);
            }
            if ($busqueda !== '') {
                // Primero las coincidencias exactas, luego por nombre.
                $sql .= ' ORDER BY CASE WHEN ' . self::q($cols['nombre']) . " LIKE '" . $this->escaparLike($busqueda) . "' ESCAPE '\\' THEN 0 ELSE 1 END, "
                      . self::q($cols['nombre']);
            } else {
                $sql .= ' ORDER BY ' . self::q($cols['nombre']);
            }
            try {
                $filas = $wrapper->querySafe($sql, $params);
            } catch (\InvalidArgumentException $e) {
                return ['success' => false, 'error' => 'Error al consultar inventario: ' . $e->getMessage()];
            } catch (PDOException $e) {
                return ['success' => false, 'error' => 'Error al consultar inventario: ' . self::aUtf8($e->getMessage())];
            }
        }

        // ── 3) Normalización de resultados (AD2: stock_disponible calculado) ─
        $productos = [];
        foreach ($filas as $fila) {
            $stockActual = $fila['stock'] ?? null;
            $stockComprometido = $cols['comprometido'] !== null ? ($fila['comprometido'] ?? null) : null;
            $stockActual = ($stockActual !== null && $stockActual !== '') ? (float) $stockActual : null;
            $stockComprometido = ($stockComprometido !== null && $stockComprometido !== '') ? (float) $stockComprometido : 0.0;
            $stockDisponible = $stockActual !== null
                ? max(0.0, $stockActual - $stockComprometido)
                : null;
            $productos[] = [
                'co_art'               => self::aUtf8($fila['codigo'] ?? ''),
                'art_des'              => self::aUtf8($fila['nombre'] ?? ''),
                'principio_activo'     => self::aUtf8($fila['principio_activo'] ?? ''),
                'vencimiento'          => self::aUtf8($fila['vencimiento'] ?? ''),
                'stock_actual'         => $stockActual,
                'stock_comprometido'   => $stockComprometido,
                'stock_disponible'     => $stockDisponible,
                'co_lin'               => $cols['linea'] !== null ? self::aUtf8($fila['linea'] ?? '') : null,
                'co_subl'              => $cols['sublinea'] !== null ? self::aUtf8($fila['sublinea'] ?? '') : null,
                'precio_base'          => self::aUtf8($fila['precio'] ?? '') !== '' ? (float) $fila['precio'] : null,
                'ultima_fecha_movimiento' => $cols['fec_mov'] !== null ? self::aUtf8($fila['fec_mov'] ?? '') : null,
            ];
        }

        if ($productos === []) {
            $criterio = $coArt !== '' ? 'co_art="' . $coArt . '"' : ($busqueda !== '' ? 'busqueda="' . $busqueda . '"' : 'filtros');
            return [
                'success' => true,
                'data'    => [
                    'tabla_origen'      => $tabla,
                    'solo_disponibles'  => $soloDisponibles,
                    'total_encontrados' => 0,
                    'productos'         => [],
                ],
                'message' => 'Sin registros',
                'timestamp' => date('c'),
                'card'    => $this->formatAsCard($criterio, []),
            ];
        }

        return [
            'success' => true,
            'data'    => [
                'tabla_origen'      => $tabla,
                'solo_disponibles'  => $soloDisponibles,
                'total_encontrados' => count($productos),
                'productos'         => $productos,
            ],
            'message' => count($productos) . ' producto(s) encontrado(s).',
            'timestamp' => date('c'),
            'card'    => $this->formatAsCard($coArt !== '' ? $coArt : $busqueda, $productos),
        ];
    }

    /**
     * Tarjeta (Card) estándar v4.13 vía CardBuilder: encabezado con contador,
     * un bloque por producto (máx 5 visibles) y footer de fuente. El array
     * estructurado queda disponible para el razonamiento del LLM.
     */
    public function formatAsCard(string $busqueda, array $productos): string
    {
        $count = count($productos);
        if ($count === 0) {
            return CardBuilder::iniciar('⚠️', 'SIN RESULTADOS')
                ->linea('No se encontraron productos coincidentes con "' . $busqueda . '".')
                ->footer('Fuente: Profit PRUEB25 (NOLOCK)')
                ->tarjeta();
        }

        $card = CardBuilder::iniciar('📦', 'RESULTADOS DE INVENTARIO: "' . $busqueda . '" (' . $count . ' Ítems)')
            ->seccion('Productos');
        foreach (array_slice($productos, 0, 5) as $p) {
            $card->linea(self::fmtNumero($p['stock_disponible'] ?? $p['stock'] ?? null) . ' und · ' . $p['co_art'] . ' · ' . $p['art_des']);
            if (($p['vencimiento'] ?? '') !== '') {
                $card->linea('VTO: ' . $p['vencimiento'] . ($p['principio_activo'] !== '' ? ' · PRIN: ' . $p['principio_activo'] : ''));
            } elseif (($p['principio_activo'] ?? '') !== '') {
                $card->linea('PRIN: ' . $p['principio_activo']);
            }
            $card->linea('💵 ' . self::fmtPrecio($p['precio_base'] ?? $p['precio'] ?? null));
        }
        $ocultos = $count - 5;
        if ($ocultos > 0) {
            $card->linea('⏩ +' . $ocultos . ' producto(s) más en la consulta.');
        }
        return $card
            ->footer('Fuente: Profit PRUEB25 (NOLOCK)')
            ->tarjeta();
    }

    /**
     * Construye la lista SELECT con alias estables, usando solo columnas resueltas.
     */
    private function seleccionar(string $tabla, array $cols): string
    {
        $sel = [];
        foreach ([
            'codigo'       => 'codigo',
            'nombre'       => 'nombre',
            'principio'    => 'principio_activo',
            'vencimiento'  => 'vencimiento',
            'stock'        => 'stock',
            'precio'       => 'precio',
            'comprometido' => 'comprometido',
            'linea'        => 'linea',
            'sublinea'     => 'sublinea',
            'fec_mov'      => 'fec_mov',
        ] as $campo => $alias) {
            if ($cols[$campo] !== null) {
                $sel[] = self::q($cols[$campo]) . ' AS ' . $alias;
            }
        }
        return implode(', ', $sel);
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
     * Formatea un número de stock: entero si no tiene decimales, si no 2.
     */
    private static function fmtNumero(mixed $valor): string
    {
        if ($valor === null || $valor === '') {
            return 'N/A';
        }
        $n = (float) $valor;
        return $n === floor($n)
            ? number_format($n, 0, ',', '.')
            : number_format($n, 2, ',', '.');
    }

    /**
     * Formatea un precio con 2 decimales (separador de miles español).
     */
    private static function fmtPrecio(mixed $valor): string
    {
        if ($valor === null || $valor === '') {
            return 'N/A';
        }
        return number_format((float) $valor, 2, ',', '.');
    }

    /**
     * Escapa comodines LIKE (%, _ y la barra de escape) para búsqueda literal.
     */
    private function escaparLike(string $texto): string
    {
        return str_replace(
            ['\\', '%', '_'],
            ['\\\\', '\\%', '\\_'],
            $texto
        );
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
     * Normaliza texto del ERP a UTF-8 (los datos vienen en CP1252/Latin-1):
     * recorta espacios de columnas char y convierte si no es UTF-8 válido.
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

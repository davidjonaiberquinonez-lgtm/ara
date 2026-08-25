<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Security;

/**
 * Control de Acceso Basado en Roles (RBAC) para herramientas del agente.
 *
 * Matriz estática de permisos por rol de negocio (sin dependencias):
 * cada rol tiene una lista cerrada de herramientas autorizadas y
 * esHerramientaPermitida() decide si un rol puede invocar una herramienta.
 *
 * Herramientas del catálogo:
 *   - buscar_inventario          (Visor/almacén: stock y ubicaciones)
 *   - consultar_saldo_cliente    (Cartera, saldo y límite de crédito)
 *   - conciliar_factura          (Conciliación bancaria de facturas)
 *   - leer_voucher_ocr           (OCR de comprobantes de pago)
 *
 * Reglas:
 *   - ROL_TECNOLOGIA accede a TODAS las herramientas (soporte total).
 *   - ROL_CLIENTE solo consulta su propio saldo (el adaptador WhatsApp
 *     fuerza el co_cli autenticado; este manager solo valida la tool).
 *   - El comodín '*' autoriza cualquier herramienta (equivalente al
 *     comportamiento de ToolRegistry con permisos '["*"]').
 *
 * Desacoplado de frameworks: estático, sin estado; encaja en controladores,
 * adaptadores y reglas de ToolRegistry.
 */
final class RolePermissionManager
{
    /** Almacén/Depósito: consulta de stock y ubicaciones. */
    public const ROL_ALMACEN = 'ALMACEN';

    /** Ventas: stock + saldo de clientes. */
    public const ROL_VENTAS = 'VENTAS';

    /** Logística: stock (apoyo a despacho y rutas). */
    public const ROL_LOGISTICA = 'LOGISTICA';

    /** Finanzas: saldo de clientes, conciliación y lectura de vouchers. */
    public const ROL_FINANZAS = 'FINANZAS';

    /** Tecnología: acceso total a todas las herramientas. */
    public const ROL_TECNOLOGIA = 'TECNOLOGIA';

    /** Cliente externo: consulta de su propio saldo únicamente. */
    public const ROL_CLIENTE = 'CLIENTE';

    /** Herramientas permitidas por rol (matriz RBAC). */
    public const HERRAMIENTAS_POR_ROL = [
        self::ROL_ALMACEN    => ['buscar_inventario'],
        self::ROL_VENTAS     => ['buscar_inventario', 'consultar_saldo_cliente'],
        self::ROL_LOGISTICA  => ['buscar_inventario'],
        self::ROL_FINANZAS   => ['consultar_saldo_cliente', 'conciliar_factura', 'leer_voucher_ocr'],
        self::ROL_TECNOLOGIA => ['buscar_inventario', 'consultar_saldo_cliente', 'conciliar_factura', 'leer_voucher_ocr'],
        self::ROL_CLIENTE    => ['consultar_saldo_cliente'],
    ];

    /**
     * Valida si un rol tiene autorizada la herramienta solicitada.
     *
     * @param string $rol         Rol del operador (constante ROL_* o texto).
     * @param string $nombreTool  Nombre de la herramienta (getName() del tool).
     *
     * @return bool true si el rol contiene la herramienta en su lista.
     */
    public static function esHerramientaPermitida(string $rol, string $nombreTool): bool
    {
        $rolLimpio = self::normalizarRol($rol);
        $tool = trim($nombreTool);
        if ($tool === '') {
            return false;
        }

        $herramientas = self::HERRAMIENTAS_POR_ROL[$rolLimpio] ?? [];
        if ($herramientas === ['*']) {
            return true;
        }
        if (in_array('*', $herramientas, true)) {
            return true;
        }
        return in_array($tool, $herramientas, true);
    }

    /**
     * Devuelve la lista de herramientas autorizadas para un rol.
     *
     * @return list<string> Lista cerrada del rol; [] si el rol no existe.
     */
    public static function herramientasDelRol(string $rol): array
    {
        return self::HERRAMIENTAS_POR_ROL[self::normalizarRol($rol)] ?? [];
    }

    /**
     * Lista las herramientas DENEGADAS de una solicitud (útil para informar
     * al operador qué puede ejecutar en su lugar).
     *
     * @param list<string> $solicitadas Herramientas que se intentaron usar.
     *
     * @return list<string> Herramientas no autorizadas para el rol.
     */
    public static function herramientasDenegadas(string $rol, array $solicitadas): array
    {
        $denegadas = [];
        foreach ($solicitadas as $tool) {
            if (!self::esHerramientaPermitida($rol, $tool)) {
                $denegadas[] = $tool;
            }
        }
        return $denegadas;
    }

    /**
     * Verifica que el rol exista en la matriz (para validación de entrada).
     */
    public static function rolExiste(string $rol): bool
    {
        return isset(self::HERRAMIENTAS_POR_ROL[self::normalizarRol($rol)]);
    }

    /**
     * Normaliza el rol: recorta, mayúsculas y sin acentos (tolerante a
     * 'administrador', 'Tecnología', 'tecnologia', etc.).
     */
    private static function normalizarRol(string $rol): string
    {
        $limpio = trim(mb_strtoupper($rol, 'UTF-8'));
        // Sin acentos para tolerar "TECNOLOGÍA" / "LOGÍSTICA".
        $sinAcentos = strtr($limpio, [
            'Á' => 'A', 'É' => 'E', 'Í' => 'I', 'Ó' => 'O', 'Ú' => 'U',
        ]);
        return $sinAcentos;
    }
}

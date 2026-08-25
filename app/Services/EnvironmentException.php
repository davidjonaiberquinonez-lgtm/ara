<?php

declare(strict_types=1);

namespace App\Services;

use RuntimeException;

/**
 * Excepción de configuración de entorno (Fase 2.5 T4).
 *
 * Se lanza cuando las variables de entorno del proyecto apuntan a una
 * configuración NO permitida, por ejemplo PROFIT_SQL_NAME/PROFIT_DB_NAME
 * resueltas a "CRISTM25" (la BD de producción, prohibida desde la Fase 2.4:
 * todo el stack PHP debe leer de la BD de pruebas PRUEB25).
 *
 * El mensaje SIEMPRE explica qué variable es la culpable y el valor hallado,
 * para que la corrección sea directa (editar .env / variables del servicio).
 */
final class EnvironmentException extends RuntimeException
{
}

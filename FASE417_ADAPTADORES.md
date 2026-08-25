# FASE 4.17 — AJUSTE DE ADAPTADORES (PRUEB25 / PROFIT)

**Fecha:** 2026-08-11 · **Arnés:** `bin/test_v4_17_adaptadores.php` → **22/22 PASS** (~16.4 s)

Adaptadores PHP departamentales ajustados al ERP de pruebas **PRUEB25** (SQL Server, solo lectura, NOLOCK) y al **envelope estándar v4.17** (`success/data/message/timestamp` + `card` Markdown + `ok`). Los datos de *gestión* (notas, eliminaciones, impresiones, consultas, GestionNotas) persisten en el **MySQL legacy** (tablas `app_*`, barquisimeto).

---

## 1. Tablas y columnas de Profit (PRUEB25) utilizadas

La resolución es siempre **dinámica** vía `ConnectionWrapper::resolverTabla()` / `resolverColumnas()` (primera tabla/columna existente de la lista de candidatas gana). Columnas reales observadas en PRUEB25:

### `factura` (cartera de facturas — ConsultarSaldoCliente AD5)
| Campo lógico | Candidatas | En PRUEB25 resuelve |
|---|---|---|
| cliente | `co_cli`, `Id`, `cliente_id`, `id_cliente` | `co_cli` |
| doc | `fact_num`, `num_doc`, `documento`, `doc_num` | `fact_num` |
| fecha | `fecha_emision`, `fec_emis`, `fec_lac`, `fecha`, `fec_ven` | `fec_emis` |
| monto | `monto`, `monto_total`, `total_neto`, `total_venta`, `neto` | `tot_neto` (vía aliasing en query) |
| saldo | `saldo`, `monto_pendiente`, `pendiente`, `saldo_pendiente`, `saldo_restante` | `saldo` |

Otras columnas de `factura` presentes en PRUEB25 (contexto): `fec_venc`, `tot_bruto`, `glob_desc`, `tot_reca`, `porc_gdesc`, `monto_dev`, `anulada`, `impresa`, `tasa`, `moneda`, `co_us_in`, `fe_us_in`, `co_us_mo`, `fe_us_mo`, `co_us_el`, `fe_us_el`, `ptovta`, `telefono`, `numcon`, `rowguid`.

### `clientes` (ficha / saldo / crédito — ConsultarSaldoCliente AD5 y ConsultarCliente AD7)
| Campo lógico | Candidatas | En PRUEB25 resuelve |
|---|---|---|
| id | `co_cli`, `cli_id`, `id_cliente`, `cliente_id`, `Id` | `co_cli` |
| rif | `RIF`, `rif`, `cliente_rif`, `no_rif`, `cl_rif` | `RIF` |
| nombre | `cli_des`, `nombre`, `razon_social`, `descripcion`, `cliente` | `cli_des` |
| limite | `mont_cre`, `limite`, `limite_credito`, `credito_max`, `cupo` | `mont_cre` |
| saldo | `saldo`, `saldo_actual`, `deuda`, `deuda_actual` | `saldo` |
| **descuento_unico** | `descuento_unico`, `descto`, `descuento`, `por_descuento`, `por_desc`, **`desc_glob`**, **`desc_ppago`** | `desc_glob` (documentado; siempre presente aunque sea null) |
| direccion | `direccion`, `direc1`, `direccion1`, `dir1`, `direccion_fiscal` | dinámico |
| telefono | `telefono`, `telef`, `telf1`, `telefono1`, `fax`, `movil` | dinámico |

Otras columnas de `clientes` presentes: `saldo_ini`, `desc_ppago`, `desc_glob`, `sincredito`.

### Tasa de cambio (AD5) — `tasaDelDia()`
| Campo lógico | Candidatas | En PRUEB25 resuelve |
|---|---|---|
| tabla | `saTasaHistorico`, `tasa_historico`, `tasa_hist`, `saTasaCambio` | `saTasaHistorico` |
| tasa | `tasa`, `precio`, `valor`, `tasa_venta` | `tasa` |
| fecha | `fec_visi`, `fecha`, `fec`, `fec_emis` | `fec_visi` |
| moneda | `co_mone`, `moneda`, `id_moneda` | `co_mone` |

Query: `SELECT TOP 1 <tasa>, <fecha>, <moneda> FROM <tabla> WHERE <fecha> <= ? ORDER BY <fecha> DESC`. Si `fechaTasa != fecha actual` → `tasa_estimada = true`.

### Stock / inventario (BuscarInventario AD2 y PythonComprasSkill AD9)
| Campo lógico | Candidatas | Notas |
|---|---|---|
| tabla stock | `st_almac`, `existencia`, `art`, `inventario`, `stock` | primer existente |
| código | `co_art`, `cod_art`, `art_cod`, `c_art`, `articulo` | — |
| descripción | `art_des`, `descripcion`, `descrip`, `nombre`, `producto` | — |
| stock actual | `stock`, `existencia`, `exist`, `st_act`, `stock_actual` | — |
| comprometido | `stock_comprometido`, `comprometido`, `stock_apartado`, `apartado`, `stock_reserva`, `reserva` | — |
| línea | `co_lin`, `linea`, `id_linea` | — |
| sublínea | `co_subl`, `sublinea`, `id_sublinea` | — |
| precio | `precio`, `precio_base`, `p_venta`, `pv`, `precio1`, `costo`, `precio_vta` | — |
| vencimiento | `fec_venc`, `vencimiento`, `fecha_venc` | — |
| último movimiento | `ultima_fecha_movimiento`, `fec_ult_mov`, `ult_mov`, `fecha_ultima` | — |

`stock_disponible = max(0, stock_actual − stock_comprometido)`.

### Rotación 30 días (AD9) — `rotacion30d()`
| Campo lógico | Candidatas | Notas |
|---|---|---|
| tabla renglón | `reng_nde`, `reng_ndd` | — |
| cabecera | `notas`, `saNotaEntrega`, `repNotaEntrega`, `nota` | — |
| cantidad | `cantidad`, `cant`, `cant_real`, `total_art` | — |
| artículo (reng) | `co_art`, `cod_art`, `articulo` | — |
| fecha (cab) | `fecha`, `fec_emis`, `fecha_emision`, `fec` | — |
| número (cab) | `nota_num`, `num_nota`, `nro_nota`, `numero` | — |

Query: renglón JOIN cabecera con `fecha >= DATEADD(day,-30,GETDATE())`, `TOP 20000`. `promedio_diario_salida = unidades_30d / 30`.

---

## 2. Tools v4.17 y su esquema de salida

| Tool | Departamento | Claves nuevas clave |
|---|---|---|
| `consultar_nota` | Almacén | `estado_actual`, `estado_final`, `timeline`, `lineas[]`, `nombre_cliente`, `normalizarEstado()` |
| `gestion_notas` | Almacén | CRUD Pendiente; persiste en `app_notas_gestion[_items]`; auditoría `app_log_notas`/`app_log_eliminaciones`; 409 vía `Envelope::conflicto` |
| `buscar_inventario` | Almacén | `productos[]` con `co_art/art_des/principio_activo/vencimiento/stock_actual/stock_comprometido/stock_disponible/co_lin/co_subl/precio_base/ultima_fecha_movimiento`; `"Sin registros"` si vacío |
| `detector_errores_nota` | Almacén | `metricas_del_dia` `{total_notas_hasta_momento, errores_duplicados, errores_impresion, estado_escaneo, ultima_nota_procesada, timestamp_escaneo, duplicados[], pendientes_impresion[]}` |
| `monitor_modificaciones_eliminaciones` | Almacén | `fuente`, `modificaciones[]`, `eliminaciones[]`, `eventos[]` (compat), `total` |
| `trazabilidad_vida_util_nota` | Almacén | `ciclo_vida[]` `{estado,fase,fecha,usuario,tiempo_en_estado_min}`, `tiempo_total_ciclo_min`, `alertas[]` DEMORA (umbrales 60/90/30 min) |
| `flujo_notas_tiempo_real` | Almacén | `flujo_del_dia` `{fecha_consulta, hora_ultima_actualizacion, notas_del_dia, total_items_despachados, total_monto_despachado_bs, ultima_nota, tendencia, por_hora[]}` |
| `rendimiento_preparadores_smart_assign` | Almacén | `operadores_del_dia[]`, `cuellos_de_botella[]` (>20 ops), `alerta_rendimiento[]` (>30 min) |
| `consultar_cliente` | Despacho | `telefonos[]`, `direccion`, `saldo_actual`, `credito_disponible`, `ultimas_consultas[]` (app_log_consultas) |
| `consultar_saldo_cliente` | Auditoría | `cliente.descuento_unico` (siempre presente), `cartera.facturas[]` con `monto_usd`/`dias_vencidos`/`tasa_estimada`, `cartera.saldo_pendiente_usd` |
| `python_compras_skill` | Compras | `priorizados` `{alta,media,baja,total}`, `sugerido_comprar`, `rotacion30d()`, sub-resultado `skill` conservado |

---

## 3. Tablas `app_*` (MySQL legacy) creadas por v4.17

`database/v4_17_app_tablas.sql` (idempotente, barquisimeto):

- `app_log_notas` — auditoría de creación/modificación de notas (`tipo` CREADA/MODIFICADA, `num_nota`, `usuario`, `fecha`, `detalle`).
- `app_log_eliminaciones` — `doc_num`, `nota_id`, `usuario`, `fecha_eliminacion`, `motivo`, `monto_total`.
- `app_log_impresiones` — impresiones de notas pendientes (`num_nota`, `fecha_impresion`, `usuario`).
- `app_log_consultas` — consultas de clientes (`co_cli`, `usuario`, `fecha_consulta`, `detalle`).
- `app_notas_gestion` / `app_notas_gestion_items` — CRUD de GestionNotasTool.

---

## 4. Script curl de verificación (HTTP)

Punto de entrada HTTP: **`POST /api/tools/ejecutar`** del middleware ARA (Python, `ara/ARA_Brain/ara_server.py:3516`), que delega al runner PHP `bin/ejecutar_tool_cli.php` (ToolRegistry → `loadFromDirectory()`). Cuerpo: `{tool, arguments?, contexto?}`.

```bash
# ── 1) Catálogo de tools registradas ────────────────────────────────
curl -s http://127.0.0.1:5000/api/tools/catalogo | php -r "echo count(json_decode(stream_get_contents(STDIN), true)['catalogo'] ?? []), PHP_EOL;"

# ── 2) Consultar nota real (degradación honesta si no existe) ───────
curl -s -X POST http://127.0.0.1:5000/api/tools/ejecutar \
  -H "Content-Type: application/json" \
  -d '{"tool":"consultar_nota","arguments":{"num_nota":"72000051"}}'

# ── 3) Cartera de facturas + descuento_unico (AD5) ──────────────────
curl -s -X POST http://127.0.0.1:5000/api/tools/ejecutar \
  -H "Content-Type: application/json" \
  -d '{"tool":"consultar_saldo_cliente","arguments":{"identificador_cliente":"FAR01361"}}'

# ── 4) Ficha cliente + crédito disponible (AD7) ─────────────────────
curl -s -X POST http://127.0.0.1:5000/api/tools/ejecutar \
  -H "Content-Type: application/json" \
  -d '{"tool":"consultar_cliente","arguments":{"co_cli":"FAR01361"}}'

# ── 5) Inventario por co_art (exacto → LIKE fallback) ───────────────
curl -s -X POST http://127.0.0.1:5000/api/tools/ejecutar \
  -H "Content-Type: application/json" \
  -d '{"tool":"buscar_inventario","arguments":{"co_art":"MD00001","solo_disponibles":true}}'

# ── 6) Compras priorizadas con rotación 30d (AD9) ───────────────────
curl -s -X POST http://127.0.0.1:5000/api/tools/ejecutar \
  -H "Content-Type: application/json" \
  -d '{"tool":"python_compras_skill","arguments":{}}'

# ── 7) Flujo de notas en tiempo real (AD8) ──────────────────────────
curl -s -X POST http://127.0.0.1:5000/api/tools/ejecutar \
  -H "Content-Type: application/json" \
  -d '{"tool":"flujo_notas_tiempo_real","arguments":{}}'
```

> **Smoke real esperado en PRUEB25:** herramientas de diagnóstico responden `success=true` con degradación honesta (p. ej. "La nota no existe en el ERP") y `card` Markdown; los guards devuelven `success=false` controlado si faltan parámetros obligatorios. Todas las respuestas son SELECT-only/NOLOCK sobre PRUEB25; los `app_*` se escriben SOLO en el MySQL legacy.

---

## 5. Resultados de verificación

| Suite | Resultado | Notas |
|---|---|---|
| `bin/test_v4_17_adaptadores.php` | **22/22 PASS** (16.4 s) | Envelope, 11 adaptadores registrados, contratos, guards, lógica pura, diagnóstico real |
| `bin/test_notas_tiempo_real_tools.php` | 11/15 PASS | 4 FAIL **preexistentes** (drift previo): notas-8/9 `GestionSuperEsteroideSearchTool::derivarUbicacion/derivarValidacion` no existen en el archivo actual; notas-10/12 exigen nota real inexistente en PRUEB25. Sin regresión causada por v4.17. |
| `php -l` | OK | 10 tools + Envelope + arnés |

**Fix en vivo durante la pasada:** `python_compras_skill` (faltaba helper `micro()`; ahora propio) y `consultar_saldo_cliente` (`array_filter` borraba `descuento_unico` cuando era null; la clave se reinserta explícitamente y el mapa admite `desc_glob`/`desc_ppago` reales de PRUEB25).

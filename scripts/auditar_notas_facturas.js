#!/usr/bin/env node
/**
 * auditar_notas_facturas.js — COTEJAMIENTO ESTRICTO Pedido ↔ Factura (Profit ERP)
 *
 * Módulo REALIZAR RUTA · Macros de cotejamiento · Consultas SQL de Cotejamiento.
 *
 * Reglas de negocio (estrictas):
 *   1. Mapeo de columnas Profit: num_nota / num_fac (ó num_fact / num_fat) y
 *      num_reglon (ó renglon_num / reng_num). Los alias se resuelven contra
 *      INFORMATION_SCHEMA (igual que visor/lista.php), nunca se asumen.
 *   2. Filtro estricto por estado:
 *        - OMITIR/SALTAR todo registro cuyo status/tipo contenga 'P'
 *          (Presupuesto / Pendiente / Pedido no procesado).
 *        - EXIGIR únicamente registros marcados con 'T' (Totalizado/Facturado).
 *      WHERE (num_nota = @nota OR num_fac = @factura) AND status = 'T'
 *            AND status NOT LIKE '%P%'
 *   3. Cotejo renglón por renglón: cada ítem escaneado debe coincidir en
 *      producto (co_art) Y num_reglon entre el pedido (num_nota) y su factura
 *      (num_fac).
 *
 * Uso:
 *   node scripts/auditar_notas_facturas.js                       → auditoría de las 10 notas
 *   node scripts/auditar_notas_facturas.js 72159681 72159692     → notas custom
 *   node scripts/auditar_notas_facturas.js --json                → salida JSON en stdout
 *
 * Variables de entorno (cargadas desde C:\ARA_PROYECT\.env si existe):
 *   PROFIT_DB_HOST, PROFIT_DB_INSTANCE, PROFIT_DB_PORT,
 *   PROFIT_DB_USER, PROFIT_DB_PASS, PROFIT_DB_NAME
 *   (respaldo: PROFIT_SERVER / PROFIT_DB / PROFIT_USER / PROFIT_PASS)
 */
"use strict";

// Cargador minimo de .env (sin dependencias): lee C:\ARA_PROYECT\.env y
// puebla process.env solo para las claves no definidas.
const fs = require("fs");
const path = require("path");
function cargarEnv() {
  const ruta = path.resolve(__dirname, "..", ".env");
  if (!fs.existsSync(ruta)) return;
  const lineas = fs.readFileSync(ruta, "utf8").split(/\r?\n/);
  for (const linea of lineas) {
    const limpia = linea.trim();
    if (!limpia || limpia.startsWith("#")) continue;
    const eq = limpia.indexOf("=");
    if (eq <= 0) continue;
    const clave = limpia.slice(0, eq).trim();
    if (!process.env[clave]) process.env[clave] = limpia.slice(eq + 1).trim();
  }
}
cargarEnv();

const NOTAS_AUDITORIA = [
  "72159681", "72159682", "72159683", "72159684", "72159685",
  "72159686", "72159687", "72159688", "72159689", "72159690",
];

const DB_CONFIG = {
  server: process.env.PROFIT_DB_HOST || process.env.PROFIT_SERVER || "192.168.4.20",
  database: process.env.PROFIT_DB_NAME !== "NOMBRE_DE_TU_BD_PROFIT" && process.env.PROFIT_DB_NAME
    ? process.env.PROFIT_DB_NAME
    : process.env.PROFIT_DB || "ProfitPlus",
  port: Number(process.env.PROFIT_DB_PORT) || 1433,
  user: process.env.PROFIT_DB_USER || process.env.PROFIT_USER || "profit",
  password: process.env.PROFIT_DB_PASS || process.env.PROFIT_PASS || "profit",
  options: {
    encrypt: false,
    trustServerCertificate: true,
    requestTimeout: 30000,
  },
  pool: { max: 1, min: 0, idleTimeoutMillis: 15000 },
};

// Columnas candidatas por campo (resolución dinámica en orden de prioridad).
const COLS = {
  nota: ["num_nota", "not_num", "nota", "numero_nota", "nro_nota", "codigobarra"],
  factura: ["num_fac", "num_fact", "num_fat", "fact_num", "factura", "nro_factura"],
  reglon: ["num_reglon", "renglon_num", "reng_num", "nro_reglon"],
  status: ["status", "statu", "estatus", "estado"],
  producto: ["co_art", "cod_art", "codigo", "codigo_articulo", "art_cod"],
  cantidad: ["cantidad", "total_art", "cant", "unidades"],
};

// Tablas candidatas del encabezado y del detalle (renglones).
const TABLAS_NOTAS = ["rep_not", "notas", "notas_entrega", "nota", "rep_nota"];
const TABLAS_RENGLONES = ["rep_not_reglon", "rep_renglon_not", "renglones", "detalle_nota", "renglon_nota"];

let sql = null; // módulo mssql (cargado perezosamente)

function configurarPool() {
  return new sql.ConnectionPool(DB_CONFIG);
}

async function obtenerColumnas(pool, tabla) {
  const r = await pool.request().query(
    `SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
     WHERE TABLE_NAME = '${tabla.replace(/'/g, "")}'
       AND TABLE_SCHEMA = 'dbo'`
  );
  return (r.recordset || []).map((c) => String(c.COLUMN_NAME).toLowerCase());
}

function resolver(cols, candidatas) {
  for (const c of candidatas) {
    if (cols.includes(c.toLowerCase())) return c;
  }
  return null;
}

async function resolverEsquema(pool) {
  let tablaNotas = null;
  let tablaRenglon = null;
  for (const t of TABLAS_NOTAS) {
    const cols = await obtenerColumnas(pool, t);
    if (cols.length > 0) { tablaNotas = { nombre: t, cols }; break; }
  }
  for (const t of TABLAS_RENGLONES) {
    const cols = await obtenerColumnas(pool, t);
    if (cols.length > 0) { tablaRenglon = { nombre: t, cols }; break; }
  }
  if (!tablaNotas) throw new Error(`Ninguna tabla candidata de notas existe: ${TABLAS_NOTAS.join(", ")}`);
  if (!tablaRenglon) throw new Error(`Ninguna tabla candidata de renglones existe: ${TABLAS_RENGLONES.join(", ")}`);

  const colNota = resolver(tablaNotas.cols, COLS.nota);
  const colFactura = resolver(tablaNotas.cols, COLS.factura);
  const colStatusN = resolver(tablaNotas.cols, COLS.status);
  const colReglon = resolver(tablaRenglon.cols, COLS.reglon);
  const colStatusR = resolver(tablaRenglon.cols, COLS.status);
  const colProducto = resolver(tablaRenglon.cols, COLS.producto);
  if (!colNota) throw new Error(`Sin columna de nota en ${tablaNotas.nombre} (candidatas: ${COLS.nota.join(", ")})`);
  if (!colReglon || !colProducto) {
    throw new Error(`Sin columna de renglón/producto en ${tablaRenglon.nombre} — requeridas para el cotejo 1-1`);
  }
  return { tablaNotas: tablaNotas.nombre, tablaRenglon: tablaRenglon.nombre, colNota, colFactura, colStatusN, colReglon, colStatusR, colProducto };
}

// Filtro estricto SQL: status = 'T' y nunca contiene 'P'.
// status == 'T' → Totalizado/Facturado (llave válida).
// status  LIKE '%P%' → Presupuesto/Pendiente/Pedido no procesado → SE OMITE.
function whereEstricto(colStatus, identNota, identFactura) {
  if (colStatus) {
    return `(${identNota} = @nota OR ${identFactura} = @factura)
            AND UPPER(CAST(${colStatus} AS NVARCHAR(10))) = 'T'
            AND UPPER(CAST(${colStatus} AS NVARCHAR(10))) NOT LIKE '%P%'`;
  }
  return `${identNota} = @nota OR ${identFactura} = @factura`;
}

async function auditarNota(pool, esq, nota) {
  const res = {
    nota,
    factura: null,
    estadoT: "SIN DATOS",
    renglonesCoincidentes: null,
    estatus: "ERROR",
    detalle: [],
  };
  const [cn, cf] = [esq.colNota, esq.colFactura || esq.colNota];

  // 1) Encabezado: ¿la nota tiene factura asignada y está en 'T'?
  const qEnc = pool.request().input("nota", sql.NVarChar, nota);
  if (esq.colFactura) qEnc.input("factura", sql.NVarChar, nota);
  const enc = await qEnc.query(
    `SELECT [${cn}] AS num_nota, ${esq.colFactura ? `[${esq.colFactura}] AS num_fac` : "[null] AS num_fac"},
            ${esq.colStatusN ? `UPPER(CAST([${esq.colStatusN}] AS NVARCHAR(10))) AS status` : "'?' AS status"}
     FROM [${esq.tablaNotas}]
     WHERE ${whereEstricto(esq.colStatusN, `[${cn}]`, esq.colFactura ? `[${esq.colFactura}]` : `[${cn}]`)}`
  );
  const filaEnc = (enc.recordset || [])[0];
  if (!filaEnc) {
    res.estadoT = "NO_T";
    res.estatus = "SIN_FACTURA_O_PENDIENTE";
    return res;
  }
  const factura = String(filaEnc.num_fac || "").trim() || nota;
  res.factura = factura;
  res.estadoT = String(filaEnc.status || "").trim() || "T";

  // 2) Renglones de la nota (pedido) y de la factura en estado 'T' (sin 'P').
  const colReng = `[${esq.colReglon}]`;
  const colProd = `[${esq.colProducto}]`;
  const filtroT = esq.colStatusR
    ? `AND UPPER(CAST([${esq.colStatusR}] AS NVARCHAR(10))) = 'T'
        AND UPPER(CAST([${esq.colStatusR}] AS NVARCHAR(10))) NOT LIKE '%P%'`
    : "";
  const condRenglones = (ref) =>
    `[${esq.colReglon}] = ${ref}` + (esq.colStatusR ? ` OR [${esq.colStatusR}] IN ('T')` : "");

  const qReng = pool.request()
    .input("nota", sql.NVarChar, nota)
    .input("factura", sql.NVarChar, factura);
  const renglones = await qReng.query(
    `SELECT ${colProd} AS producto, ${colReng} AS num_reglon, 'PEDIDO' AS origen
     FROM [${esq.tablaRenglon}]
     WHERE ${condRenglones("@nota")}
     ${filtroT}
     UNION ALL
     SELECT ${colProd} AS producto, ${colReng} AS num_reglon, 'FACTURA' AS origen
     FROM [${esq.tablaRenglon}]
     WHERE ${condRenglones("@factura")}
     ${filtroT}`
  );

  const pedidos = (renglones.recordset || []).filter((r) => r.origen === "PEDIDO");
  const facturas = (renglones.recordset || []).filter((r) => r.origen === "FACTURA");
  const setFact = new Set(facturas.map((f) => `${String(f.producto).trim().toUpperCase()}|${String(f.num_reglon).trim()}`));

  // Cotejo 1-1: cada renglón del pedido debe existir en la factura con el
  // MISMO producto y el MISMO num_reglon.
  let coincidentes = 0;
  const faltantes = [];
  for (const p of pedidos) {
    const clave = `${String(p.producto).trim().toUpperCase()}|${String(p.num_reglon).trim()}`;
    if (setFact.has(clave)) coincidentes++;
    else faltantes.push(`${p.producto} (renglón ${p.num_reglon})`);
  }

  res.renglonesCoincidentes = `${coincidentes}/${pedidos.length}`;
  res.detalle = faltantes.slice(0, 5);
  res.estatus =
    pedidos.length > 0 && coincidentes === pedidos.length
      ? "OK_COINCIDE_1_1"
      : pedidos.length === 0
        ? "SIN_RENGLONES_PEDIDO"
        : "DIVERGENCIA_RENGLONES";
  return res;
}

function imprimirTabla(resultados) {
  const filas = resultados.map((r) => ({
    "Nota": r.nota,
    "Factura Encontrada": r.factura || "—",
    "Estado 'T'": r.estadoT,
    "Renglones Coincidentes": r.renglonesCoincidentes ?? "—",
    "Estatus": r.estatus,
  }));
  console.table(filas);
  const ok = resultados.filter((r) => r.estatus === "OK_COINCIDE_1_1").length;
  console.log(
    `\nResumen: ${ok}/${resultados.length} notas con cotejo 1-1 OK (filtro estricto: solo 'T', sin 'P').`
  );
  for (const r of resultados) {
    if (r.estatus !== "OK_COINCIDE_1_1") {
      console.log(`  ! Nota ${r.nota}: ${r.estatus} ${r.detalle.length ? "→ faltan " + r.detalle.join(", ") : ""}`);
    }
  }
}

async function main() {
  const args = process.argv.slice(2);
  const modoJson = args.includes("--json");
  const notas = args.filter((a) => !a.startsWith("--"));
  const listaNotas = notas.length ? notas : NOTAS_AUDITORIA;

  try {
    sql = require("mssql");
  } catch (e) {
    console.error(
      "[auditoria] El módulo 'mssql' no está instalado. Ejecute:  npm install mssql  dentro de scripts/"
    );
    process.exit(2);
  }

  let pool = null;
  try {
    pool = await configurarPool().connect();
    console.log(`[auditoria] Conectado a ${DB_CONFIG.server}/${DB_CONFIG.database} (Profit ERP).`);
  } catch (e) {
    // El servidor pudo estar inalcanzable o las credenciales rechazadas: se
    // imprime igual la tabla de auditoría marcando cada nota como SIN_CONEXION.
    console.error(`[auditoria] No se pudo conectar a SQL Server (${DB_CONFIG.server}): ${e.message}`);
    console.error("[auditoria] Verifique PROFIT_SERVER/PROFIT_DB/PROFIT_USER/PROFIT_PASS y el driver ODBC 17/18 de SQL Server.");
    const filas = listaNotas.map((n) => ({
      "Nota": n,
      "Factura Encontrada": "—",
      "Estado 'T'": "—",
      "Renglones Coincidentes": "—",
      "Estatus": "SIN_CONEXION",
    }));
    if (modoJson) {
      console.log(JSON.stringify(filas, null, 2));
    } else {
      console.table(filas);
      console.log(`\nResumen: 0/${listaNotas.length} notas auditadas (sin conexión a Profit).`);
    }
    process.exit(1);
  }

  try {
    const esq = await resolverEsquema(pool);
    console.log(`[auditoria] Esquema resuelto: ${esq.tablaNotas} (nota=${esq.colNota}, factura=${esq.colFactura || "—"}, status=${esq.colStatusN || "—"}) · ${esq.tablaRenglon} (renglón=${esq.colReglon}, producto=${esq.colProducto}, status=${esq.colStatusR || "—"})`);

    const resultados = [];
    for (const nota of listaNotas) {
      try {
        resultados.push(await auditarNota(pool, esq, nota));
      } catch (e) {
        resultados.push({ nota, factura: null, estadoT: "ERROR", renglonesCoincidentes: null, estatus: `ERROR: ${e.message}`, detalle: [] });
      }
    }
    if (modoJson) {
      console.log(JSON.stringify(resultados, null, 2));
    } else {
      imprimirTabla(resultados);
    }
  } finally {
    await pool.close();
  }
}

main().catch((e) => {
  console.error("[auditoria] Error fatal:", e);
  process.exit(1);
});

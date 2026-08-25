#!/usr/bin/env node
/**
 * auditar_profit_10_notas.js — AUDITORIA Y COTEJAMIENTO ESTRICTO de 10 notas
 *                              (Profit Plus / SQL Server, driver mssql-tedious)
 *
 * Modulo REALIZAR RUTAS · Cotejamiento Pedido <-> Nota <-> Factura.
 *
 * Esquema real (BD CRISTM25 - descubierto por INFORMATION_SCHEMA, nunca
 * asumido; el script se adapta a cualquier esquema candidato):
 *   - not_ent:     notas de entrega.  fact_num = numero de nota, status = '2'
 *                  (totalizada; '0' = pendiente). Sin 'P' en este esquema.
 *   - factura:     facturas.          fact_num, status, anulada, impresa.
 *   - reng_fac:    renglones de factura. Cuando la factura nace de una nota:
 *                  tipo_doc = 'E', num_doc = nota, reng_doc = renglon de la
 *                  nota (num_reglon), reng_num = renglon de la factura,
 *                  co_art = producto, total_art = cantidad.
 *
 * Filtro estricto SQL (regla de negocio del modulo):
 *   - EXIGIR estado totalizado: status = 'T' o '2' (Totalizado/Facturado).
 *   - EXCLUIR todo registro cuyo estado contenga 'P' (Presupuesto/Pendiente).
 *   - Descartar documentos anulados.
 *   - Cotejo 1-1: producto (co_art) Y numero de renglon (num_reglon/reng_doc
 *     vs reng_num) identicos entre la nota y su factura 'T'.
 *
 * Uso:
 *   node scripts/auditar_profit_10_notas.js                   -> auditoria de las 10 notas
 *   node scripts/auditar_profit_10_notas.js 72159681 72159684  -> notas custom
 *   node scripts/auditar_profit_10_notas.js --db CRISTM25      -> BD explicita
 *   node scripts/auditar_profit_10_notas.js --json             -> salida JSON
 *   node scripts/auditar_profit_10_notas.js --escanear <nota> <co_art> <renglon>
 *       -> verificacion en tiempo real del escaneo (PASO 3): alerta si la
 *          nota no tiene factura totalizada o si producto/renglon divergen.
 *
 * Variables de entorno (C:\ARA_PROYECT\.env):
 *   PROFIT_DB_HOST, PROFIT_DB_INSTANCE, PROFIT_DB_PORT,
 *   PROFIT_DB_USER, PROFIT_DB_PASS, PROFIT_DB_NAME
 */
"use strict";

const fs = require("fs");
const path = require("path");

// ---- Cargador minimo de .env (sin dependencias) ---------------------------
function cargarEnv() {
  const ruta = path.resolve(__dirname, "..", ".env");
  if (!fs.existsSync(ruta)) return;
  for (const linea of fs.readFileSync(ruta, "utf8").split(/\r?\n/)) {
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

const PLACEHOLDER_BD = "NOMBRE_DE_TU_BD_PROFIT";

function configBase() {
  return {
    server: process.env.PROFIT_DB_HOST || "192.168.4.20",
    port: Number(process.env.PROFIT_DB_PORT) || 1433,
    user: process.env.PROFIT_DB_USER || "profit",
    password: process.env.PROFIT_DB_PASS || "profit",
    options: {
      encrypt: false,
      trustServerCertificate: true,
      requestTimeout: 45000,
      connectTimeout: 15000,
      appName: "ARA_auditoria_10_notas",
    },
    pool: { max: 1, min: 0, idleTimeoutMillis: 20000 },
  };
}

// ---- Resolucion dinamica de esquema ----------------------------------------
const TABLAS = {
  notas: ["not_ent", "saNotaEntregaVenta", "notas", "nota_entrega"],
  facturas: ["factura", "saFacturaVenta", "facturas"],
  reng_fac: ["reng_fac", "saRenglonFactura", "renglon_factura", "renglones_factura"],
  reng_ped: ["reng_ped", "saRenglonPedido", "renglon_pedido", "renglones_pedido"],
};

const COLS = {
  numero: ["fact_num", "num_nota", "num_fac", "fac_num", "not_num"],
  reng_num: ["reng_num", "num_reglon", "renglon_num", "nro_reglon", "reng"],
  reng_doc: ["reng_doc", "num_reglon_doc", "renglon_doc"],
  num_doc: ["num_doc", "doc_num", "nota_doc"],
  tipo_doc: ["tipo_doc", "doc_tipo"],
  co_art: ["co_art", "cod_art", "codigo", "codigo_articulo"],
  cantidad: ["total_art", "cantidad", "cant", "unidades"],
  pendiente: ["pendiente", "pendiente2"],
  status: ["status", "statu", "estatus", "estado"],
  anulada: ["anulada", "anulado", "anul"],
  impresa: ["impresa", "impreso"],
  cliente: ["co_cli", "co_cliente", "cliente"],
  ref_web: ["campo5", "campo1", "ref"],
};

async function obtenerColumnas(pool, tabla) {
  const r = await pool.request().query(
    `SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
     WHERE TABLE_NAME = '${tabla.replace(/'/g, "")}' AND TABLE_SCHEMA = 'dbo'`
  );
  return (r.recordset || []).map((c) => String(c.COLUMN_NAME).toLowerCase());
}

function resolver(cols, candidatas) {
  if (!cols) return null;
  for (const c of candidatas) {
    if (cols.includes(c.toLowerCase())) return c;
  }
  return null;
}

async function resolverEsquema(pool) {
  const esquema = { tablas: {} };
  for (const [clave, candidatas] of Object.entries(TABLAS)) {
    let encontrada = null;
    for (const t of candidatas) {
      const cols = await obtenerColumnas(pool, t);
      if (cols.length > 0) { encontrada = { nombre: t, cols }; break; }
    }
    esquema.tablas[clave] = encontrada;
  }
  const tNotas = esquema.tablas.notas;
  const tFac = esquema.tablas.facturas;
  const tRf = esquema.tablas.reng_fac;
  if (!tNotas || !tFac || !tRf) {
    throw new Error(
      `Esquema Profit incompleto (notas=${tNotas ? tNotas.nombre : "-"}, facturas=${tFac ? tFac.nombre : "-"}, reng_fac=${tRf ? tRf.nombre : "-"}).`
    );
  }
  const col = (t, key) => (t ? resolver(t.cols, COLS[key]) : null);
  esquema.cols = {
    num_nota: col(tNotas, "numero"),
    status_nota: col(tNotas, "status"),
    anulada_nota: col(tNotas, "anulada"),
    ref_web: col(tNotas, "ref_web"),
    num_fac: col(tFac, "numero"),
    status_fac: col(tFac, "status"),
    anulada_fac: col(tFac, "anulada"),
    impresa_fac: col(tFac, "impresa"),
    cliente_fac: col(tFac, "cliente"),
    reng_num: col(tRf, "reng_num"),
    reng_doc: col(tRf, "reng_doc"),
    num_doc: col(tRf, "num_doc"),
    tipo_doc: col(tRf, "tipo_doc"),
    co_art: col(tRf, "co_art"),
    cantidad: col(tRf, "cantidad"),
    pendiente: col(tRf, "pendiente"),
  };
  const req = ["num_nota", "num_fac", "reng_num", "co_art"];
  for (const k of req) {
    if (!esquema.cols[k]) throw new Error(`Columna clave no resuelta: ${k}.`);
  }
  return esquema;
}

// Regla estricta del modulo (mapeo empirico del esquema CRISTM25):
//   - not_ent:  status '2' = nota TOTALIZADA (facturada); '0' = pendiente.
//   - factura:  status '0' = factura VIGENTE (facturada, 620K regs); '2' =
//               excepcion historica (4 regs); anulada = flag booleano.
//   - Cualquier estado que contenga 'P' (Presupuesto/Pendiente) se EXCLUYE.
function esNotaTotalizada(v) {
  const s = String(v || "").toUpperCase();
  if (s.includes("P")) return false;
  return s === "T" || s === "2";
}

function esFacturaValida(status, anulada) {
  if (esVerdadero(anulada)) return false;
  const s = String(status || "").toUpperCase();
  if (s.includes("P")) return false;
  return s === "T" || s === "0" || s === "2";
}

function esVerdadero(v) {
  return v === true || v === 1 || String(v).toUpperCase() === "TRUE";
}

// ---- Auditoria de una nota -------------------------------------------------
async function auditarNota(pool, esq, nota) {
  const r = {
    NUM_NOTA: nota,
    "NUM_FAC / NUM_FAT": null,
    ESTADO: "SIN_DATOS",
    "RENGLONES COINCIDENTES": null,
    "ESTATUS COTEJO": "ERROR",
    detalle: [],
  };
  const c = esq.cols;
  const Q = (t) => `[${esq.tablas[t].nombre}]`;

  // 1) Encabezado de la nota.
  const h = await pool.request().input("nota", sql.NVarChar, nota).query(
    `SELECT [${c.num_nota}] AS numero,
            ${c.status_nota ? `UPPER(CAST([${c.status_nota}] AS NVARCHAR(10))) AS status` : "'?' AS status"},
            ${c.anulada_nota ? `[${c.anulada_nota}] AS anulada` : "0 AS anulada"},
            ${c.ref_web ? `[${c.ref_web}] AS ref_web` : "'' AS ref_web"}
     FROM ${Q("notas")} WHERE [${c.num_nota}] = @nota`
  );
  const notaFila = (h.recordset || [])[0];
  if (!notaFila) { r["ESTATUS COTEJO"] = "SIN_NOTA"; return r; }
  if (esVerdadero(notaFila.anulada)) { r.ESTADO = "ANULADA"; r["ESTATUS COTEJO"] = "NOTA_ANULADA"; return r; }
  r.ESTADO = String(notaFila.status || "").trim() || "?";
  if (!esNotaTotalizada(notaFila.status)) { r["ESTATUS COTEJO"] = "NOTA_NO_TOTALIZADA"; return r; }

  // 2) Renglones de la nota: renglones de factura que referencian la nota
  //    (tipo_doc='E', num_doc=nota). reng_doc = num_reglon de la nota.
  const qN = pool.request().input("nota", sql.NVarChar, nota);
  const condTipo = c.tipo_doc ? `AND UPPER(CAST([${c.tipo_doc}] AS NVARCHAR(10))) IN ('E','N','')` : "";
  const nf = await qN.query(
    `SELECT [${c.num_fac}] AS factura, [${c.reng_doc}] AS reglon_nota,
            [${c.reng_num}] AS reglon_fac, [${c.co_art}] AS producto,
            ${c.cantidad ? `[${c.cantidad}] AS cantidad` : "NULL AS cantidad"}
     FROM ${Q("reng_fac")}
     WHERE [${c.num_doc}] = @nota ${condTipo}`
  );
  let renglonesNota = (nf.recordset || []).filter((x) => x.reglon_nota !== null && x.reglon_nota !== 0);
  if (!renglonesNota.length) {
    // Fallback: renglones directos con fact_num = nota.
    const qN2 = await pool.request().input("nota", sql.NVarChar, nota).query(
      `SELECT [${c.num_fac}] AS factura, [${c.reng_num}] AS reglon_nota,
              [${c.reng_num}] AS reglon_fac, [${c.co_art}] AS producto,
              ${c.cantidad ? `[${c.cantidad}] AS cantidad` : "NULL AS cantidad"}
       FROM ${Q("reng_fac")} WHERE [${c.num_fac}] = @nota`
    );
    renglonesNota = (qN2.recordset || []).map((x) => ({ ...x, reglon_nota: x.reglon_nota }));
  }
  if (!renglonesNota.length) { r["ESTATUS COTEJO"] = "SIN_RENGLONES_NOTA"; return r; }

  // 3) Facturas vinculadas (estado totalizado, no anuladas).
  const facturas = [...new Set(renglonesNota.map((x) => String(x.factura).trim()).filter(Boolean))];
  let facturasT = [];
  for (const f of facturas) {
    const qF = await pool.request().input("fac", sql.NVarChar, f).query(
      `SELECT [${c.num_fac}] AS numero,
              ${c.status_fac ? `UPPER(CAST([${c.status_fac}] AS NVARCHAR(10))) AS status` : "'T' AS status"},
              ${c.anulada_fac ? `[${c.anulada_fac}] AS anulada` : "0 AS anulada"}
       FROM ${Q("facturas")} WHERE [${c.num_fac}] = @fac`
    );
    const fila = (qF.recordset || [])[0];
    if (fila && esFacturaValida(fila.status, fila.anulada)) facturasT.push(f);
  }
  if (!facturasT.length) {
    r["NUM_FAC / NUM_FAT"] = facturas.join(", ") || null;
    r["ESTATUS COTEJO"] = "SIN_FACTURA_T";
    return r;
  }
  r["NUM_FAC / NUM_FAT"] = facturasT.join(", ");

  // 4) Renglones de las facturas 'T'.
  const setFac = new Set();
  for (const f of facturasT) {
    const qRf = await pool.request().input("fac", sql.NVarChar, f).query(
      `SELECT [${c.reng_num}] AS renglon, [${c.co_art}] AS producto
       FROM ${Q("reng_fac")} WHERE [${c.num_fac}] = @fac`
    );
    for (const x of qRf.recordset || []) {
      setFac.add(`${String(x.producto).trim().toUpperCase()}|${String(x.renglon).trim()}`);
    }
  }

  // 5) Cotejo 1-1: producto + num_reglon identico (reglon_nota vs reng_fac).
  let coincidentes = 0;
  const faltantes = [];
  for (const p of renglonesNota) {
    const clave = `${String(p.producto).trim().toUpperCase()}|${String(p.reglon_nota).trim()}`;
    if (setFac.has(clave)) coincidentes++;
    else faltantes.push(`${p.producto} (renglon ${p.reglon_nota})`);
  }
  r["RENGLONES COINCIDENTES"] = `${coincidentes}/${renglonesNota.length}`;
  r.detalle = faltantes.slice(0, 5);
  r["ESTATUS COTEJO"] =
    coincidentes === renglonesNota.length ? "OK_COINCIDE_1_1" : "DIVERGENCIA_RENGLONES";
  return r;
}

// ---- PASO 3: verificacion en tiempo real del escaneo -----------------------
async function verificarEscaneo(pool, esq, nota, coArt, renglon) {
  const c = esq.cols;
  const Q = (t) => `[${esq.tablas[t].nombre}]`;
  const numero = String(coArt || "").trim().toUpperCase();
  const reng = String(renglon || "").trim();
  if (!numero || !reng) {
    console.error("[escanear] Uso: --escanear <nota> <co_art> <renglon>");
    process.exit(2);
  }

  // Renglones de la nota y facturas vinculadas.
  const qN = await pool.request().input("nota", sql.NVarChar, nota).query(
    `SELECT [${c.num_fac}] AS factura, [${c.reng_doc}] AS reglon_nota, [${c.co_art}] AS producto
     FROM ${Q("reng_fac")} WHERE [${c.num_doc}] = @nota`
  );
  const filas = (qN.recordset || []).filter((x) => x.reglon_nota !== null && x.reglon_nota !== 0);
  const facturas = [...new Set(filas.map((f) => String(f.factura).trim()).filter(Boolean))];
  if (!facturas.length) {
    console.log(`[ESCANEO] INCONSISTENTE | Nota ${nota} sin renglones en reng_fac (no existe o sin detalle). ALERTA: verificar nota en Profit.`);
    return { ok: false, motivo: "SIN_RENGLONES_NOTA" };
  }
  let facturasT = [];
  for (const f of facturas) {
    const qF = await pool.request().input("fac", sql.NVarChar, f).query(
      `SELECT ${c.status_fac ? `UPPER(CAST([${c.status_fac}] AS NVARCHAR(10))) AS status` : "'T' AS status"},
              ${c.anulada_fac ? `[${c.anulada_fac}] AS anulada` : "0 AS anulada"}
       FROM ${Q("facturas")} WHERE [${c.num_fac}] = @fac`
    );
    const fila = (qF.recordset || [])[0];
    if (fila && esFacturaValida(fila.status, fila.anulada)) facturasT.push(f);
  }
  if (!facturasT.length) {
    console.log(`[ESCANEO] INCONSISTENTE | Nota ${nota} con renglones pero sin factura totalizada 'T' (tiene: ${facturas.join(", ")}). ALERTA: nota aun no facturada.`);
    return { ok: false, motivo: "SIN_FACTURA_T", facturas };
  }
  const numFac = facturasT[0];

  // La nota dice que este renglon debe llevar este producto.
  const esperado = filas.find((f) => String(f.reglon_nota).trim() === reng && String(f.factura).trim() === numFac);
  if (!esperado) {
    console.log(`[ESCANEO] INCONSISTENTE | Nota ${nota} -> Factura ${numFac} (T): renglon ${reng} NO existe en la nota. ALERTA: bulto fuera de ruta.`);
    return { ok: false, motivo: "RENGLON_INEXISTENTE", factura: numFac };
  }
  const productoEsperado = String(esperado.producto).trim().toUpperCase();
  if (productoEsperado !== numero) {
    console.log(`[ESCANEO] INCONSISTENTE | Nota ${nota} -> Factura ${numFac} (T): renglon ${reng} espera ${productoEsperado} y se escaneo ${numero}. ALERTA: articulo incorrecto.`);
    return { ok: false, motivo: "PRODUCTO_NO_COINCIDE", factura: numFac, esperado: productoEsperado };
  }
  console.log(`[ESCANEO] CONSISTENTE | Nota ${nota} -> Factura ${numFac} (T): ${numero} coincide en renglon ${reng}.`);
  return { ok: true, motivo: "COINCIDE_1_1", factura: numFac };
}

// ---- Salida ----------------------------------------------------------------
function imprimirTabla(resultados) {
  const filas = resultados.map((r) => ({
    "NUM_NOTA": r.NUM_NOTA,
    "NUM_FAC / NUM_FAT": r["NUM_FAC / NUM_FAT"] || "-",
    "ESTADO": r.ESTADO,
    "RENGLONES COINCIDENTES": r["RENGLONES COINCIDENTES"] ?? "-",
    "ESTATUS COTEJO": r["ESTATUS COTEJO"],
  }));
  console.table(filas);
  const ok = resultados.filter((r) => r["ESTATUS COTEJO"] === "OK_COINCIDE_1_1").length;
  console.log(`Resumen: ${ok}/${resultados.length} notas con cotejo 1-1 OK (filtro estricto totalizado 'T', sin 'P').`);
  for (const r of resultados) {
    if (r["ESTATUS COTEJO"] !== "OK_COINCIDE_1_1") {
      const extra = r.detalle && r.detalle.length ? " -> faltan " + r.detalle.join(", ") : "";
      console.log(`  ! Nota ${r.NUM_NOTA}: ${r["ESTATUS COTEJO"]}${extra}`);
    }
  }
}

// ---- main ------------------------------------------------------------------
async function main() {
  const args = process.argv.slice(2);
  const modoJson = args.includes("--json");
  const idxEscanear = args.indexOf("--escanear");
  const idxDb = args.indexOf("--db");
  const dbExplicita = idxDb >= 0 ? args[idxDb + 1] : null;
  const notas = args.filter((a) => !a.startsWith("--") && (idxDb < 0 || a !== args[idxDb + 1]));
  const listaNotas = notas.length ? notas : NOTAS_AUDITORIA;

  try {
    sql = require("mssql");
  } catch (e) {
    console.error("[auditoria] El modulo 'mssql' no esta instalado. Ejecute:  npm install mssql  dentro de scripts/");
    process.exit(2);
  }

  const cfg = configBase();
  let pool = null;
  try {
    let nombreBD = dbExplicita;
    if (!nombreBD) {
      const n = (process.env.PROFIT_DB_NAME || "").trim();
      nombreBD = n && n !== PLACEHOLDER_BD ? n : null;
    }
    if (!nombreBD) {
      // PROFIT_DB_NAME placeholder: conecta al default del login, enumera BDs y
      // elige la primera que tenga el esquema (not_ent + factura + reng_fac).
      pool = await new sql.ConnectionPool(cfg).connect();
      const r = await pool.request().query(
        `SELECT name FROM sys.databases WHERE database_id > 4 AND name NOT IN ('master','tempdb','model','msdb') ORDER BY name`
      );
      let elegida = null;
      for (const d of r.recordset || []) {
        const t1 = await pool.request().query(
          `SELECT COUNT(*) AS n FROM ${d.name}.INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME IN ('not_ent','factura','reng_fac')`
        );
        if (Number(t1.recordset[0].n) >= 3) { elegida = d.name; break; }
      }
      if (!elegida) throw new Error("Ninguna BD de usuario tiene el esquema Profit (not_ent/factura/reng_fac).");
      console.log(`[auditoria] PROFIT_DB_NAME placeholder -> descubierta BD: ${elegida}`);
      await pool.close();
      cfg.database = elegida;
      pool = await new sql.ConnectionPool(cfg).connect();
    } else {
      cfg.database = nombreBD;
      pool = await new sql.ConnectionPool(cfg).connect();
    }
    console.log(`[auditoria] Conectado a ${cfg.server}:${cfg.port}/${cfg.database} (Profit ERP).`);
  } catch (e) {
    console.error(`[auditoria] No se pudo conectar a SQL Server (${cfg.server}:${cfg.port}): ${e.message}`);
    console.error("[auditoria] Verifique PROFIT_DB_HOST/PROFIT_DB_PORT/PROFIT_DB_USER/PROFIT_DB_PASS/PROFIT_DB_NAME en .env");
    const filas = listaNotas.map((n) => ({
      "NUM_NOTA": n, "NUM_FAC / NUM_FAT": "-", "ESTADO": "-",
      "RENGLONES COINCIDENTES": "-", "ESTATUS COTEJO": "SIN_CONEXION",
    }));
    if (modoJson) console.log(JSON.stringify(filas, null, 2));
    else console.table(filas);
    process.exit(1);
  }

  try {
    const esq = await resolverEsquema(pool);
    console.log(
      `[auditoria] Esquema: ${esq.tablas.notas.nombre}(num=${esq.cols.num_nota}, status=${esq.cols.status_nota || "-"}) + ` +
      `${esq.tablas.facturas.nombre}(num=${esq.cols.num_fac}, status=${esq.cols.status_fac || "-"}) + ` +
      `${esq.tablas.reng_fac.nombre}(reng_num=${esq.cols.reng_num}, reng_doc=${esq.cols.reng_doc || "-"}, num_doc=${esq.cols.num_doc || "-"}, co_art=${esq.cols.co_art})`
    );

    if (idxEscanear >= 0) {
      const [nota, coArt, renglon] = args.slice(idxEscanear + 1, idxEscanear + 4);
      await verificarEscaneo(pool, esq, nota, coArt, renglon);
      return;
    }

    const resultados = [];
    for (const nota of listaNotas) {
      try {
        resultados.push(await auditarNota(pool, esq, nota));
      } catch (e) {
        resultados.push({ NUM_NOTA: nota, "NUM_FAC / NUM_FAT": null, ESTADO: "ERROR", "RENGLONES COINCIDENTES": null, "ESTATUS COTEJO": `ERROR: ${e.message}`, detalle: [] });
      }
    }
    if (modoJson) console.log(JSON.stringify(resultados, null, 2));
    else imprimirTabla(resultados);
  } finally {
    await pool.close();
  }
}

let sql = null;
main().catch((e) => {
  console.error("[auditoria] Error fatal:", e);
  process.exit(1);
});

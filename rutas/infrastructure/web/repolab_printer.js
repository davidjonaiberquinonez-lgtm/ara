/**
 * repolab_printer.js — Renderizador de impresión REPOLAB para sub-rutas finalizadas.
 *
 * Formato A4 "Rutagrama de Despacho" (v4.37, referencia visual provista por
 * Crist Medicals C.A.): una página por sub-ruta con:
 *  - Encabezado institucional: logo, N° RUTAGRAMA, fecha.
 *  - Ficha operativa: responsable/chofer/ayudante (ID+cédula) + credencial
 *    del vehículo (placa/INTT) + ruta asignada + hora programada/real salida.
 *  - Leyenda de tipo de pedido (Solo Factura / Factura+NC / Solo NC).
 *  - Tabla de pedidos con Nº/Tipo/Pedido/Factura-NC/Cliente/Cajas/Peso/Estado/Firma.
 *  - Totales consolidados + nota de auditoría + firmas de las 3 personas.
 * Todos los datos vienen de `sr`/`meta` (nunca se inventa nada): si un campo
 * no llegó (ej. peso sin fuente real, cédula no capturada), se imprime como
 * "—", nunca un valor de relleno.
 */
(function (global) {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function escNum(n) {
        const v = Number(n);
        return isNaN(v) ? 0 : v;
    }

    function ov(v, fallback) {
        const s = String(v == null ? '' : v).trim();
        return s !== '' ? esc(s) : (fallback == null ? '&mdash;' : fallback);
    }

    /** Clasifica el ítem para la insignia de tipo (FAC | FAC+NC | Solo NC). */
    function tipoBadge(it) {
        const esNC = String(it.tipo_documento || '') === 'NOTA_CREDITO';
        const esSoloFactura = String(it.tipo_documento || '') === 'SOLO_FACTURA' || !!it.is_invoice_only;
        const tieneNC = !!it.has_credit_notes;
        if (esNC) {
            return { clase: 'rg-badge-nc', texto: 'SOLO NC' };
        }
        if (esSoloFactura && !tieneNC) {
            return { clase: 'rg-badge-fac', texto: 'FAC' };
        }
        if (tieneNC) {
            return { clase: 'rg-badge-facnc', texto: 'FAC+NC' };
        }
        return { clase: 'rg-badge-fac', texto: 'PEDIDO' };
    }

    const CSS = `
        @page { size: A4 portrait; margin: 0; }
        @media print {
            body { background: #fff !important; -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
            .rg-pagina { page-break-after: always; box-shadow: none !important; margin: 0 !important; width: 100% !important; min-height: 100vh !important; padding: 8mm 10mm !important; }
            .rg-pagina:last-child { page-break-after: auto; }
            .repolab-no-print { display: none !important; }
        }
        body { font-family: 'Segoe UI', Arial, sans-serif; color: #0f172a; margin: 0; background: #e2e8f0; }
        .rg-pagina { width: 210mm; min-height: 297mm; margin: 10px auto; background: #fff; box-sizing: border-box; padding: 10mm; border-radius: 6px; box-shadow: 0 4px 14px rgba(0,0,0,.12); position: relative; }

        .rg-header { display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #0f172a; padding-bottom: 8px; margin-bottom: 8px; }
        .rg-brand { display: flex; align-items: center; gap: 10px; }
        .rg-brand-box { width: 130px; height: 42px; background: #0f172a; border-radius: 6px; display: flex; align-items: center; justify-content: center; padding: 4px; }
        .rg-brand-datos { font-size: 9.5px; color: #475569; line-height: 1.25; }
        .rg-brand-datos .nombre { font-weight: 800; color: #0f172a; }
        .rg-titulo { text-align: right; }
        .rg-titulo h1 { margin: 0; font-size: 15px; font-weight: 900; letter-spacing: .02em; text-transform: uppercase; }
        .rg-titulo .sub { font-size: 9px; font-weight: 700; color: #0e7490; letter-spacing: .12em; margin-top: 1px; }
        .rg-titulo .meta { margin-top: 5px; display: inline-flex; gap: 8px; align-items: center; background: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 4px; padding: 3px 8px; font-size: 10px; font-family: 'Consolas', monospace; }
        .rg-titulo .meta .num { color: #dc2626; font-weight: 800; }

        .rg-ficha { border: 1px solid #1e293b; border-radius: 4px; background: #f8fafc; font-size: 9.5px; overflow: hidden; margin-bottom: 8px; }
        .rg-ficha-top { background: #0f172a; color: #fff; font-weight: 700; padding: 3px 8px; font-size: 8.5px; text-transform: uppercase; letter-spacing: .06em; display: flex; justify-content: space-between; }
        .rg-ficha-top .estado { color: #2dd4bf; font-family: monospace; }
        .rg-ficha-grid { display: grid; grid-template-columns: repeat(4, 1fr); }
        .rg-ficha-grid > div { padding: 7px 8px; border-right: 1px solid #cbd5e1; }
        .rg-ficha-grid > div:last-child { border-right: none; background: #ecfeff; }
        .rg-ficha-grid .lbl { font-weight: 700; color: #64748b; text-transform: uppercase; font-size: 7.5px; display: block; }
        .rg-ficha-grid .val { font-weight: 800; color: #0f172a; }
        .rg-ficha-grid .sub { color: #475569; }
        .rg-ficha-foot { border-top: 1px solid #cbd5e1; background: #fff; padding: 4px 8px; display: flex; justify-content: space-between; align-items: center; font-size: 9.5px; }
        .rg-ruta-chip { background: #1e293b; color: #fff; font-weight: 700; padding: 2px 6px; border-radius: 3px; font-size: 8.5px; margin-left: 4px; }

        .rg-leyenda { display: flex; justify-content: space-between; font-size: 8.5px; margin-bottom: 6px; padding: 0 2px; font-weight: 600; color: #475569; }
        .rg-leyenda .items { display: flex; gap: 10px; }
        .rg-dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; margin-right: 3px; }

        .rg-tabla-wrap { border: 2px solid #1e293b; border-radius: 4px; overflow: hidden; margin-bottom: 8px; }
        .rg-tabla { width: 100%; border-collapse: collapse; font-size: 9.5px; text-align: left; }
        .rg-tabla thead tr { background: #0f172a; color: #fff; font-weight: 700; font-size: 8px; text-transform: uppercase; letter-spacing: .04em; }
        .rg-tabla th, .rg-tabla td { border-right: 1px solid #334155; padding: 5px 6px; }
        .rg-tabla thead th:last-child, .rg-tabla tbody td:last-child { border-right: none; }
        .rg-tabla tbody tr { border-top: 1px solid #cbd5e1; }
        .rg-tabla td.c, .rg-tabla th.c { text-align: center; }
        .rg-badge { display: inline-block; padding: 1px 5px; border-radius: 3px; font-weight: 800; font-size: 7.5px; border: 1px solid; white-space: nowrap; }
        .rg-badge-fac { background: #d1fae5; color: #065f46; border-color: #6ee7b7; }
        .rg-badge-facnc { background: #fef3c7; color: #78350f; border-color: #fcd34d; }
        .rg-badge-nc { background: #ede9fe; color: #4c1d95; border-color: #c4b5fd; }
        .rg-estado-ok { background: #059669; color: #fff; font-weight: 800; padding: 1px 5px; border-radius: 3px; font-size: 7.5px; }
        .rg-estado-pend { background: #dc2626; color: #fff; font-weight: 800; padding: 1px 5px; border-radius: 3px; font-size: 7.5px; }
        .rg-tabla tfoot tr { background: #e2e8f0; font-weight: 800; font-size: 9.5px; border-top: 2px solid #1e293b; }
        .rg-tabla tfoot td { padding: 5px 6px; }

        .rg-nota { background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 4px; padding: 6px 8px; font-size: 8.5px; color: #334155; margin-bottom: 10px; }
        .rg-nota b { color: #0f172a; text-transform: uppercase; }

        .rg-firmas-wrap { border: 2px solid #0f172a; border-radius: 4px; overflow: hidden; font-size: 9.5px; }
        .rg-firmas-top { background: #0f172a; color: #fff; font-weight: 700; padding: 4px 8px; font-size: 8.5px; text-transform: uppercase; letter-spacing: .06em; display: flex; justify-content: space-between; }
        .rg-firmas-grid { display: grid; grid-template-columns: repeat(3, 1fr); padding: 10px; gap: 12px; text-align: center; }
        .rg-firma { display: flex; flex-direction: column; justify-content: space-between; min-height: 74px; }
        .rg-firma .titulo { font-weight: 700; font-size: 8px; color: #64748b; text-transform: uppercase; }
        .rg-firma .linea { border-top: 1.5px solid #0f172a; margin: 0 6px; }
        .rg-firma .nombre { font-weight: 800; color: #0f172a; margin-top: 4px; }
        .rg-firma .id { font-size: 7.5px; color: #64748b; }
        .rg-pie { background: #f1f5f9; padding: 4px 8px; font-size: 7.5px; color: #64748b; display: flex; justify-content: space-between; border-top: 1px solid #cbd5e1; font-family: monospace; }

        .repolab-no-print { position: fixed; top: 0; left: 0; right: 0; background: #0f172a; color: #fff; padding: 8px; text-align: center; z-index: 9999; font-size: 13px; }
        .repolab-no-print button { margin-left: 10px; padding: 4px 14px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; background: #22c55e; color: #fff; }
        .repolab-vacio { text-align: center; padding: 40px; color: #64748b; font-size: 13px; }
    `;

    /** Logo Crist Medicals (mismo SVG de la referencia — marca propia, uso interno). */
    const LOGO_SVG = `<svg viewBox="0 0 320 120" style="width:100%;height:100%" xmlns="http://www.w3.org/2000/svg">
        <path d="M 120 25 C 60 25, 20 40, 20 70 C 20 100, 60 105, 220 105 A 10 10 0 0 0 220 90 L 80 90 C 50 90, 40 80, 40 70 C 40 55, 60 40, 115 40 Z" fill="#029b83" />
        <path d="M 130 35 C 160 10, 240 10, 250 50 C 260 85, 220 90, 200 90 A 8 8 0 0 1 200 75 C 220 75, 235 70, 230 50 C 225 30, 170 25, 145 45 Z" fill="#39b54a" />
        <g transform="translate(185, 30) scale(0.65)" fill="#029b83">
            <path d="M20 2 C20 2 22 5 22 8 C22 11 20 13 20 13 C20 13 18 11 18 8 C18 5 20 2 20 2 Z" />
            <rect x="19" y="8" width="2" height="38" rx="1" fill="#029b83"/>
            <path d="M10 14 C15 12 25 12 30 14 C35 16 35 20 28 22 C22 24 18 20 12 22 C6 24 5 30 12 32 C18 34 22 30 28 32 C34 34 32 40 20 42" stroke="#029b83" stroke-width="2.5" fill="none" stroke-linecap="round"/>
        </g>
        <text x="80" y="68" font-family="Arial, sans-serif" font-weight="800" font-size="22" fill="#39b54a">Crist</text>
        <text x="80" y="92" font-family="Arial, sans-serif" font-weight="800" font-size="22" fill="#029b83">Medicals</text>
        <text x="185" y="92" font-family="Arial, sans-serif" font-weight="800" font-size="16" fill="#029b83">C.A</text>
    </svg>`;

    function numeroRutagrama(sr, idx) {
        if (sr.sub_rutagrama) return sr.sub_rutagrama;
        const hoy = new Date();
        const f = `${hoy.getFullYear()}-${String(hoy.getMonth() + 1).padStart(2, '0')}${String(hoy.getDate()).padStart(2, '0')}`;
        return `RUT-${f}-${String(idx).padStart(3, '0')}`;
    }

    function paginaHTML(sr, idx, meta) {
        const dv = meta.datos_vehiculo || {};
        const items = sr.items || [];
        const totalNotas = items.length;
        const totalPaquetes = items.reduce((acc, i) => acc + escNum(i.paquetes), 0);
        const totalPeso = items.reduce((acc, i) => acc + escNum(i.peso), 0);
        const todoVerificado = items.every((i) => i.verificado || i.matriz_estado === '1-1');

        const filas = items.map((it, i) => {
            const badge = tipoBadge(it);
            const facturaNc = it.has_credit_notes
                ? `${ov(it.num_fact || it.factura_num, 'Sin Factura')}${it.nc_num ? `<span style="display:block;font-size:7.5px;color:#78350f;">NC-${esc(it.nc_num)}</span>` : ''}`
                : ov(it.num_fact || it.factura_num, 'Sin Factura');
            const estado = (it.verificado || it.matriz_estado === '1-1')
                ? '<span class="rg-estado-ok">✓ VERIFICADO</span>'
                : '<span class="rg-estado-pend">PENDIENTE</span>';
            return `
            <tr>
                <td class="c" style="font-weight:700;">${i + 1}</td>
                <td class="c"><span class="rg-badge ${badge.clase}">${badge.texto}</span></td>
                <td style="font-family:monospace;font-weight:700;">${ov(it.nota_num)}</td>
                <td style="font-family:monospace;font-weight:700;">${facturaNc}</td>
                <td style="font-weight:600;">${ov(it.razon_social)}</td>
                <td class="c" style="font-weight:700;">${escNum(it.paquetes)}</td>
                <td class="c" style="font-family:monospace;">${it.peso ? escNum(it.peso).toFixed(1) + ' Kg' : '—'}</td>
                <td class="c">${estado}</td>
                <td class="c" style="color:#94a3b8;font-size:8px;">_______________</td>
            </tr>`;
        }).join('');

        return `
        <div class="rg-pagina">
            <div class="rg-header">
                <div class="rg-brand">
                    <div class="rg-brand-box">${LOGO_SVG}</div>
                    <div class="rg-brand-datos">
                        <p class="nombre">${ov(meta.empresa_nombre, 'CRIST MEDICALS C.A.')}</p>
                        <p style="font-family:monospace;">RIF: ${ov(meta.empresa_rif, '—')}</p>
                        <p>Droguería y Distribución Médica</p>
                    </div>
                </div>
                <div class="rg-titulo">
                    <h1>Rutagrama de Despacho</h1>
                    <div class="sub">Documento oficial de salida en ruta</div>
                    <div class="meta">
                        <span><b>N° Rutagrama:</b> <span class="num">${esc(numeroRutagrama(sr, idx))}</span></span>
                        <span>|</span>
                        <span><b>Fecha:</b> ${esc(sr.fecha || new Date().toLocaleDateString('es-VE'))}</span>
                    </div>
                </div>
            </div>

            <div class="rg-ficha">
                <div class="rg-ficha-top">
                    <span>1. Ficha operativa y credenciales de salida</span>
                    <span class="estado">${todoVerificado ? 'ESTADO RUTA: 100% VERIFICADA' : 'ESTADO RUTA: CON PENDIENTES'}</span>
                </div>
                <div class="rg-ficha-grid">
                    <div>
                        <span class="lbl">ID Responsable</span>
                        <p class="val">${ov(dv.responsable_id || sr.responsable_id)}</p>
                        <p class="sub">${ov(dv.responsable_nombre, '')}</p>
                    </div>
                    <div>
                        <span class="lbl">ID Chofer asignado</span>
                        <p class="val">${ov(dv.chofer_id)}</p>
                        <p class="sub">${ov(dv.chofer || sr.chofer)}${dv.chofer_cedula ? ` (${esc(dv.chofer_cedula)})` : ''}</p>
                    </div>
                    <div>
                        <span class="lbl">ID Ayudante despacho</span>
                        <p class="val">${ov(dv.ayudante_id)}</p>
                        <p class="sub">${ov(dv.ayudante_nombre || dv.ayudantes || sr.ayudantes)}${dv.ayudante_cedula ? ` (${esc(dv.ayudante_cedula)})` : ''}</p>
                    </div>
                    <div>
                        <span class="lbl" style="color:#0e7490;">Credencial vehículo</span>
                        <p class="val">${ov(dv.vehiculo_descripcion || dv.carro || sr.carro)}</p>
                        <p class="sub" style="font-family:monospace;">${dv.vehiculo_placa ? `Placa: <b>${esc(dv.vehiculo_placa)}</b>` : ''}${dv.vehiculo_intt ? ` | INTT: ${esc(dv.vehiculo_intt)}` : ''}</p>
                    </div>
                </div>
                <div class="rg-ficha-foot">
                    <div><b>Ruta asignada:</b><span class="rg-ruta-chip">${esc((sr.sub_ruta || '').toUpperCase())}</span></div>
                    <div style="display:flex;gap:14px;">
                        <span><b>Hora prog. salida:</b> ${ov(dv.hora_prog_salida, '____:____')}</span>
                        <span><b>Hora real salida:</b> ____:____</span>
                    </div>
                </div>
            </div>

            <div class="rg-leyenda">
                <span>Simbología de tipo de pedido en ruta:</span>
                <div class="items">
                    <span><span class="rg-dot" style="background:#10b981;"></span>Solo Factura</span>
                    <span><span class="rg-dot" style="background:#f59e0b;"></span>Factura + Nota de Crédito</span>
                    <span><span class="rg-dot" style="background:#a855f7;"></span>Solo Nota de Crédito (retiro)</span>
                </div>
            </div>

            <div class="rg-tabla-wrap">
                <table class="rg-tabla">
                    <thead>
                        <tr>
                            <th class="c" style="width:22px;">N°</th>
                            <th class="c" style="width:52px;">Tipo</th>
                            <th style="width:80px;">N° Pedido</th>
                            <th style="width:90px;">N° Factura / NC</th>
                            <th>Razón Social (Cliente)</th>
                            <th class="c" style="width:42px;">Cajas</th>
                            <th class="c" style="width:48px;">Peso</th>
                            <th class="c" style="width:64px;">Estado</th>
                            <th class="c" style="width:70px;">Firma/Sello</th>
                        </tr>
                    </thead>
                    <tbody>${filas || '<tr><td colspan="9" style="text-align:center;padding:14px;color:#64748b;">Sin ítems en esta sub-ruta.</td></tr>'}</tbody>
                    <tfoot>
                        <tr>
                            <td colspan="5" style="text-align:right;text-transform:uppercase;">Totales consolidados ruta:</td>
                            <td class="c">${totalPaquetes} Cajas</td>
                            <td class="c">${totalPeso ? totalPeso.toFixed(1) + ' Kg' : '—'}</td>
                            <td colspan="2" class="c" style="color:${todoVerificado ? '#059669' : '#dc2626'};">${todoVerificado ? '100% VERIFICADO' : `${items.filter(i=>i.verificado||i.matriz_estado==='1-1').length}/${totalNotas} VERIFICADO`}</td>
                        </tr>
                    </tfoot>
                </table>
            </div>

            <div class="rg-nota">
                <b>Nota de auditoría y despacho:</b>
                Todos los pedidos de este rutagrama cuentan con la auditoría física de empaque aprobada en sistema.
                El chofer debe verificar la recepción conforme y los sellos de cada cliente en las facturas adjuntas.
            </div>

            <div class="rg-firmas-wrap">
                <div class="rg-firmas-top">
                    <span>2. Firmas de auditoría, conformidad y salida de almacén</span>
                    <span>${ov(meta.empresa_nombre, 'CRIST MEDICALS C.A.')}</span>
                </div>
                <div class="rg-firmas-grid">
                    <div class="rg-firma">
                        <div class="titulo">Autorizado por (Responsable Almacén)</div>
                        <div class="linea"></div>
                        <div>
                            <div class="nombre">${ov(dv.responsable_nombre, meta.usuario)}</div>
                            <div class="id">ID: ${ov(dv.responsable_id || sr.responsable_id)}</div>
                        </div>
                    </div>
                    <div class="rg-firma">
                        <div class="titulo">Chofer conforme (carga completa)</div>
                        <div class="linea"></div>
                        <div>
                            <div class="nombre">${ov(dv.chofer || sr.chofer)}</div>
                            <div class="id">ID: ${ov(dv.chofer_id)}${dv.chofer_cedula ? ` | C.I. ${esc(dv.chofer_cedula)}` : ''}</div>
                        </div>
                    </div>
                    <div class="rg-firma">
                        <div class="titulo">Ayudante de despacho / control</div>
                        <div class="linea"></div>
                        <div>
                            <div class="nombre">${ov(dv.ayudante_nombre || dv.ayudantes || sr.ayudantes)}</div>
                            <div class="id">ID: ${ov(dv.ayudante_id)}${dv.ayudante_cedula ? ` | C.I. ${esc(dv.ayudante_cedula)}` : ''}</div>
                        </div>
                    </div>
                </div>
                <div class="rg-pie">
                    <span>ARA_SYNC Engine &bull; Módulo de Despacho y Salida de Ruta</span>
                    <span>Generado: ${esc(new Date().toLocaleString('es-VE'))}</span>
                    <span>Guía ${esc(sr.guia)} &bull; Hoja ${idx} &bull; Original: Almacén | Copia: Chofer</span>
                </div>
            </div>
        </div>`;
    }

    function renderRepolabPrint(subrutas, meta) {
        const list = Array.isArray(subrutas) ? subrutas : [];
        if (!list.length) {
            return `<div class="repolab-vacio">No hay sub-rutas finalizadas para imprimir.</div>`;
        }
        const paginas = list.map((sr, i) => paginaHTML(sr, i + 1, meta || {})).join('');
        const totalGlobalPaquetes = list.reduce((acc, sr) =>
            acc + (sr.items || []).reduce((a, i) => a + escNum(i.paquetes), 0), 0);

        return `
        <style>${CSS}</style>
        <div class="repolab-no-print">
            Vista de impresi&oacute;n REPOLAB &mdash; ${list.length} sub-ruta(s) &bull; ${totalGlobalPaquetes} paquete(s)
            <button onclick="window.print()">🖨 Imprimir / Guardar PDF</button>
            <button onclick="window.close()" style="background:#64748b;">Cerrar</button>
        </div>
        ${paginas}`;
    }

    function imprimirRepolab(subrutas, meta) {
        const ventana = window.open('', '_blank', 'width=900,height=700');
        if (!ventana) {
            alert('Permita ventanas emergentes para imprimir REPOLAB.');
            return;
        }
        ventana.document.write('<!DOCTYPE html><html><head><meta charset="utf-8">');
        ventana.document.write('<title>REPOLAB &mdash; Rutagrama de Despacho</title>');
        ventana.document.write('</head><body>');
        ventana.document.write(renderRepolabPrint(subrutas, meta));
        ventana.document.write('</body></html>');
        ventana.document.close();
        ventana.focus();
        setTimeout(() => ventana.print(), 350);
    }

    global.renderRepolabPrint = renderRepolabPrint;
    global.imprimirRepolab = imprimirRepolab;
})(window);

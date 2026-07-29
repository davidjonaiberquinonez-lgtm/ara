# -*- coding: utf-8 -*-
"""
Módulo de generación de Reporte PDF - Sistema ARA
Endpoint: /api/reporte/pdf  (GET y POST)
"""
import os
import io
import sqlite3
from datetime import datetime, timedelta

from flask import request, send_file, make_response, jsonify

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT


# =============================================================================
# CONFIGURACIÓN DE RUTA A LA BASE DE DATOS
# =============================================================================
# Ruta absoluta basada en este archivo: ara/ARA_Brain/pdf_route.py
# ->BASE_DIR = ara/ARA_Brain  ->  DB_PATH = ara/ARA_Brain/data/proyecto_ara.db
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'proyecto_ara.db')


def get_db_connection():
    """Conexión a proyecto_ara.db con filas tipo dict."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# =============================================================================
# CONSULTAS A log_puntos
# =============================================================================
def obtener_incidencias_rango(fecha_inicio: str, fecha_fin: str) -> list:
    """Detalle de operaciones en el rango de fechas."""
    conn = get_db_connection()
    try:
        query = """
            SELECT 
                fecha_registro,
                usuario,
                modulo,
                referencia_id,
                cantidad_renglones,
                puntos_ganados
            FROM log_puntos
            WHERE date(fecha_registro) BETWEEN date(?) AND date(?)
            ORDER BY fecha_registro DESC, usuario
        """
        cursor = conn.execute(query, (fecha_inicio, fecha_fin))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def obtener_resumen_kpis(fecha_inicio: str, fecha_fin: str) -> dict:
    """KPIs globales del rango de fechas."""
    conn = get_db_connection()
    try:
        query = """
            SELECT 
                COUNT(*)                 AS total_operaciones,
                SUM(cantidad_renglones) AS total_renglones,
                SUM(puntos_ganados)      AS total_puntos,
                COUNT(DISTINCT usuario)  AS usuarios_activos
            FROM log_puntos
            WHERE date(fecha_registro) BETWEEN date(?) AND date(?)
        """
        cursor = conn.execute(query, (fecha_inicio, fecha_fin))
        row = cursor.fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


# =============================================================================
# GENERACIÓN PDF CON REPORTLAB (ORIENTACIÓN HORIZONTAL)
# =============================================================================
def generar_pdf_reporte(incidencias: list, kpis: dict,
                        fecha_inicio: str, fecha_fin: str) -> io.BytesIO:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title=f"Reporte ARA {fecha_inicio} a {fecha_fin}"
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=20,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#1a1a2e')
    )
    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Heading2'],
        fontSize=11,
        spaceAfter=12,
        alignment=TA_LEFT,
        textColor=colors.HexColor('#16213e')
    )

    elements = []

    # ---- Título ----
    elements.append(Paragraph("REPORTE DE RENDIMIENTO - SISTEMA ARA", title_style))
    elements.append(Paragraph(f"Período: {fecha_inicio} al {fecha_fin}", subtitle_style))
    elements.append(Paragraph(
        f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        styles['Normal']
    ))
    elements.append(Spacer(1, 12))

    # ---- KPIs ----
    kpi_data = [
        ['KPI', 'Valor'],
        ['Total Operaciones', f"{kpis.get('total_operaciones') or 0:,}"],
        ['Total Renglones',  f"{kpis.get('total_renglones') or 0:,}"],
        ['Total Puntos',     f"{kpis.get('total_puntos') or 0:,.2f}"],
        ['Usuarios Activos', f"{kpis.get('usuarios_activos') or 0:,}"],
    ]
    kpi_table = Table(kpi_data, colWidths=[8 * cm, 6 * cm])
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a1a2e')),
        ('TEXTCOLOR',  (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN',      (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f0f0f5')),
        ('GRID',       (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1),
            [colors.white, colors.HexColor('#f0f0f5')]),
    ]))
    elements.append(kpi_table)
    elements.append(Spacer(1, 20))

    # ---- Detalle ----
    elements.append(Paragraph("DETALLE DE OPERACIONES", subtitle_style))

    if incidencias:
        headers = ['Fecha', 'Usuario', 'Módulo',
                   'Referencia', 'Renglones', 'Puntos']
        table_data = [headers]
        for inc in incidencias:
            table_data.append([
                str(inc['fecha_registro'])[:10] if inc['fecha_registro'] else '-',
                str(inc['usuario']) if inc['usuario'] is not None else '-',
                str(inc['modulo']).capitalize() if inc['modulo'] else '-',
                str(inc['referencia_id']) if inc['referencia_id'] is not None else '-',
                str(inc['cantidad_renglones']) if inc['cantidad_renglones'] is not None else '0',
                f"{inc['puntos_ganados']:.2f}" if inc['puntos_ganados'] is not None else '0.00',
            ])

        col_widths = [3 * cm, 3.5 * cm, 3 * cm, 4 * cm, 2.5 * cm, 2.5 * cm]
        detail_table = Table(table_data, colWidths=col_widths, repeatRows=1)
        detail_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#16213e')),
            ('TEXTCOLOR',  (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN',      (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID',       (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),
                [colors.white, colors.HexColor('#f8f9fa')]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(detail_table)
    else:
        elements.append(Paragraph(
            "No hay operaciones registradas en el período seleccionado.",
            styles['Normal']
        ))

    doc.build(elements)
    buffer.seek(0)
    return buffer


# =============================================================================
# REGISTRO DE LA RUTA EN LA APP FLASK
# =============================================================================
def register_pdf_route(app):
    @app.route(
        '/api/reporte/pdf',
        methods=['GET', 'POST'],
        strict_slashes=False
    )
    def reporte_pdf():
        # 1) Captura de parámetros según método
        if request.method == 'POST':
            if request.is_json:
                params = request.get_json(silent=True) or {}
            else:
                params = request.form or {}
        else:
            params = request.args

        fecha_inicio = params.get('fecha_inicio')
        fecha_fin = params.get('fecha_fin')

        # 2) Defaults: últimos 30 días
        if not fecha_fin:
            fecha_fin = datetime.now().strftime('%Y-%m-%d')
        if not fecha_inicio:
            fecha_inicio = (datetime.now() - timedelta(days=30))\
                .strftime('%Y-%m-%d')

        # 3) Validación de formato YYYY-MM-DD
        try:
            datetime.strptime(fecha_inicio, '%Y-%m-%d')
            datetime.strptime(fecha_fin, '%Y-%m-%d')
        except (ValueError, TypeError):
            return jsonify({
                'error': 'Formato de fecha inválido. Use YYYY-MM-DD'
            }), 400

        # 4) Consultas a la BD
        try:
            incidencias = obtener_incidencias_rango(fecha_inicio, fecha_fin)
            kpis = obtener_resumen_kpis(fecha_inicio, fecha_fin)
        except sqlite3.Error as e:
            return jsonify({'error': f'Error de base de datos: {e}'}), 500

        # 5) Generación del PDF
        try:
            pdf_buffer = generar_pdf_reporte(
                incidencias, kpis, fecha_inicio, fecha_fin
            )
        except Exception as e:
            return jsonify({'error': f'Error generando PDF: {e}'}), 500

        # 6) Respuesta con headers para preview inline
        response = make_response(send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=False,
            download_name=f'reporte_rendimiento_{fecha_inicio}_{fecha_fin}.pdf'
        ))
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = (
            f'inline; filename="reporte_rendimiento_'
            f'{fecha_inicio}_{fecha_fin}.pdf"'
        )
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

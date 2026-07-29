import os
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

# Canvas para numeración dinámica y encabezado profesional
class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#64748b"))

        # Encabezado (Página 2 en adelante)
        if self._pageNumber > 1:
            self.drawString(36, 760, "PROYECTO ARA — DOSSIER TÉCNICO Y ARQUITECTURA DE ESCALABILIDAD")
            self.setStrokeColor(colors.HexColor("#e2e8f0"))
            self.setLineWidth(0.75)
            self.line(36, 752, 576, 752)

        # Pie de página
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.75)
        self.line(36, 45, 576, 45)

        self.setFont("Helvetica", 8)
        self.drawString(36, 30, "Documento de Arquitectura & Estrategia Empresarial | Confidencial")
        page_text = f"Página {self._pageNumber} de {page_count}"
        self.drawRightString(576, 30, page_text)
        self.restoreState()

def generar_pdf_ara(filename="Reporte_Ejecutivo_Proyecto_ARA_v3.10.pdf"):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=54,
        bottomMargin=54
    )

    styles = getSampleStyleSheet()

    # Paleta de Colores
    c_primary = colors.HexColor("#0f172a")       # Slate 900
    c_secondary = colors.HexColor("#1e40af")     # Blue 800
    c_accent = colors.HexColor("#0284c7")        # Sky 600
    c_text = colors.HexColor("#334155")          # Slate 700
    c_bg_light = colors.HexColor("#f8fafc")      # Slate 50
    c_border = colors.HexColor("#cbd5e1")        # Slate 300
    c_white = colors.white
    c_gold = colors.HexColor("#f59e0b")          # Amber 500
    c_green = colors.HexColor("#10b981")         # Emerald 500

    # Estilos de Texto
    styles.add(ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=c_white,
        spaceAfter=6
    ))

    styles.add(ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#93c5fd")
    ))

    styles.add(ParagraphStyle(
        'SectionHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=17,
        textColor=c_primary,
        spaceBefore=12,
        spaceAfter=6,
        keepWithNext=True
    ))

    styles.add(ParagraphStyle(
        'SubSectionHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10.5,
        leading=14,
        textColor=c_secondary,
        spaceBefore=8,
        spaceAfter=4,
        keepWithNext=True
    ))

    styles.add(ParagraphStyle(
        'BodyCustom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=c_text,
        spaceAfter=6
    ))

    styles.add(ParagraphStyle(
        'BulletCustom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=12,
        textColor=c_text,
        leftIndent=12,
        firstLineIndent=-8,
        spaceAfter=4
    ))

    styles.add(ParagraphStyle(
        'TableText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11,
        textColor=c_text
    ))

    styles.add(ParagraphStyle(
        'TableHeaderText',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=c_white,
        alignment=1
    ))

    story = []

    # =====================================================================
    # PORTADA
    # =====================================================================
    header_content = [
        [Paragraph("PROYECTO ARA — AI WAREHOUSE & LOGISTICS MIDDLEWARE", styles['DocTitle'])],
        [Paragraph("Dossier Técnico, Arquitectura Hexagonal, Telemetría GPS, Motor Local Edge y Control Profit Plus", styles['DocSubTitle'])]
    ]

    header_table = Table(header_content, colWidths=[540])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), c_primary),
        ('PADDING', (0,0), (-1,-1), 14),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,1), (-1,1), 14),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 10))

    # Meta datos rápidos
    meta_data = [
        [
            Paragraph("<b>Estado:</b> v3.10 Enterprise Ready", styles['BodyCustom']),
            Paragraph("<b>Fecha:</b> Julio 2026", styles['BodyCustom']),
            Paragraph("<b>Arquitectura:</b> Hexagonal + Hybrid AI + Real-Time Telemetry", styles['BodyCustom'])
        ]
    ]
    meta_table = Table(meta_data, colWidths=[180, 180, 180])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), c_bg_light),
        ('BOX', (0,0), (-1,-1), 0.5, c_border),
        ('PADDING', (0,0), (-1,-1), 5),
        ('ALIGN', (0,0), (-1,-1), 'CENTER')
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 12))

    # =====================================================================
    # 1. CAPACIDADES OPERATIVAS Y MÓDULOS DE VANGUARDIA (v3.10)
    # =====================================================================
    story.append(Paragraph("1. Capacidades Operativas y Módulos de Vanguardia (v3.10)", styles['SectionHeader']))
    story.append(HRFlowable(width="100%", thickness=1.5, color=c_secondary, spaceAfter=8))

    story.append(Paragraph(
        "Proyecto ARA es un middleware logístico de alto rendimiento que integra inteligencia artificial "
        "multimodal, visión por computador, telemetría GPS en tiempo real y un motor de trazabilidad "
        "hexagonal. A continuación se describen los módulos implementados hasta v3.10.",
        styles['BodyCustom']
    ))

    # A. State Machine Estricta
    story.append(Paragraph("A. State Machine Estricta — Control de Notas de Entrega (v3.1)", styles['SubSectionHeader']))
    story.append(Paragraph(
        "• <b>Ciclo de Vida Validado:</b> Las notas de entrega transitan por 7 estados atómicos: "
        "<b>pendiente → preparando → preparada → chequeada → embalada → entregada → devuelta</b>. "
        "Cada transición exige precondiciones específicas (ej: ninguna nota se embala sin estar chequeada).",
        styles['BulletCustom']
    ))
    story.append(Paragraph(
        "• <b>Trazabilidad Hexagonal:</b> El sistema detecta automáticamente si la consulta refiere a una "
        "NOTA (extrayendo cliente, estado, cajas, operadores) o a un ARTÍCULO (stock, ubicación, "
        "reubicaciones, últimas 3 notas asociadas).",
        styles['BulletCustom']
    ))
    story.append(Paragraph(
        "• <b>Feedback Loop:</b> Cada inferencia del asistente puede ser aprobada (✅) o reportada como "
        "error (❌) por el operador, alimentando la tabla <font face='Courier' size='8'>log_ia_feedback</font> "
        "para mejora continua del modelo.",
        styles['BulletCustom']
    ))

    # B. Rutas, Telemetría GPS & ORS
    story.append(Paragraph("B. Rutas, Telemetría GPS & ORS (v3.0)", styles['SubSectionHeader']))
    story.append(Paragraph(
        "• <b>Optimización de Despacho:</b> Integración con OpenRouteService (GeoJSON) para cálculo de "
        "rutas óptimas y ETA con fórmula Haversine. Mapas interactivos Leaflet.js con marcadores "
        "dinámicos de chofer y regente.",
        styles['BulletCustom']
    ))
    story.append(Paragraph(
        "• <b>Monitoreo en Vivo:</b> Webhooks GPS desde dispositivos móviles actualizan posición cada "
        "15 segundos. Dashboard con telemetría en tiempo real para supervisión de flota.",
        styles['BulletCustom']
    ))

    # C. Motor de Auditoría 360° & Context Injection
    story.append(Paragraph("C. Motor de Auditoría 360° & Context Injection (v3.2)", styles['SubSectionHeader']))
    story.append(Paragraph(
        "• <b>Consultas en Lenguaje Natural:</b> El operador puede preguntar en español, por ejemplo: "
        "'¿quién chequeó la nota FACT-001?', y el sistema responde inyectando en tiempo real el contexto "
        "del usuario preparador, chequeador, embalador, stock actual e historial de movimientos.",
        styles['BulletCustom']
    ))
    story.append(Paragraph(
        "• <b>Context Injection Automático:</b> El System Prompt del asistente incorpora datos vivos de "
        "la sesión: nombre del operador, rol, notas activas, estado del stock y alertas críticas.",
        styles['BulletCustom']
    ))

    # D. IA Híbrida
    story.append(Paragraph("D. IA Híbrida — Key Pool NVIDIA NIM + Mini ARA Edge (v3.3–v3.5)", styles['SubSectionHeader']))
    story.append(Paragraph(
        "• <b>Key Pool NVIDIA NIM:</b> Pool de 5 API keys con rotación automática y failover ante "
        "errores 503/429/401/403/timeout. Usa DeepSeek V4 Flash como modelo principal. "
        "Mecanismo de backoff de 5 segundos por clave fallida con restauración progresiva.",
        styles['BulletCustom']
    ))
    story.append(Paragraph(
        "• <b>Mini ARA Local Edge:</b> Motor offline basado en <font face='Courier' size='8'>qwen2.5-coder:3b</font> "
        "(Ollama) + <font face='Courier' size='8'>faster-whisper</font> para transcripción de notas de voz de "
        "choferes. Inferencia local &lt; 2 segundos, dataset generado automáticamente desde la base de "
        "conocimiento del almacén.",
        styles['BulletCustom']
    ))
    story.append(Paragraph(
        "• <b>Visión Local LLaVA:</b> El endpoint <font face='Courier' size='8'>analizar_foto_producto()</font> "
        "envía la imagen a Ollama LLaVA, extrae JSON estructurado, busca en stock_maestro y retorna "
        "ficha técnica con últimos movimientos. Sin dependencia de API externa.",
        styles['BulletCustom']
    ))

    # E. Integración Profit Plus & Reubicación SPA
    story.append(Paragraph("E. Integración Profit Plus & Reubicación Física SPA (v3.6–v3.10)", styles['SubSectionHeader']))
    story.append(Paragraph(
        "• <b>Semáforo Profit Plus:</b> Columna <font face='Courier' size='8'>procesado_profit</font> en "
        "<font face='Courier' size='8'>reportes_ubicacion</font> con semáforo amarillo (0=Pendiente) y "
        "verde (1=Procesado). Los reportes de discrepancias y trazabilidad filtran por este campo y aplican "
        "RBAC case-insensitive (<font face='Courier' size='8'>LOWER(usuario)</font>).",
        styles['BulletCustom']
    ))
    story.append(Paragraph(
        "• <b>Flujo SPA 3 Pasos:</b> El submódulo 'Cambio de Ubicación' implementa navegación tipo app "
        "con tres vistas independientes: Categorías → Estantes (tarjetas azules con código físico real "
        "como 1CR01-P0) → Productos. Conmutación estricta sin acumulación DOM y botones de retroceso "
        "contextuales.",
        styles['BulletCustom']
    ))
    story.append(Paragraph(
        "• <b>Doble Escritura JSON:</b> Cada cambio de ubicación se persiste simultáneamente en SQLite "
        "(tabla <font face='Courier' size='8'>reportes_ubicacion</font>) y como archivo JSON en "
        "<font face='Courier' size='8'>brain_knowledge/reportes_ubicacion/</font> con nomenclatura "
        "<font face='Courier' size='8'>MOV-[ID]_[USUARIO].json</font>.",
        styles['BulletCustom']
    ))

    story.append(Spacer(1, 10))

    # =====================================================================
    # 2. MATRIZ DE ARQUITECTURA TÉCNICA Y ESCALABILIDAD
    # =====================================================================
    story.append(PageBreak())
    story.append(Paragraph("2. Matriz de Arquitectura Técnica y Escalabilidad", styles['SectionHeader']))
    story.append(HRFlowable(width="100%", thickness=1.5, color=c_secondary, spaceAfter=8))

    arq_data = [
        [
            Paragraph("<b>Módulo / Componente</b>", styles['TableHeaderText']),
            Paragraph("<b>Tecnología Implementada</b>", styles['TableHeaderText']),
            Paragraph("<b>Impacto / Métrica Key</b>", styles['TableHeaderText'])
        ],
        [
            Paragraph("<b>Persistencia & Concurrencia</b>", styles['TableText']),
            Paragraph("SQLite WAL Mode + PRAGMA busy_timeout=5000 + lock de escritura con reintento.", styles['TableText']),
            Paragraph("<b>Cero bloqueos SQLITE_BUSY.</b> Lecturas concurrentes sin espera.", styles['TableText'])
        ],
        [
            Paragraph("<b>Inferencia IA Edge</b>", styles['TableText']),
            Paragraph("Key Pool 5× NVIDIA NIM (DeepSeek V4 Flash) + Mini ARA Local (Qwen 3B + Faster-Whisper).", styles['TableText']),
            Paragraph("<b>Inferencia offline &lt; 2s.</b> Failover automático en 503/429/timeout.", styles['TableText'])
        ],
        [
            Paragraph("<b>Rutas & Geolocalización</b>", styles['TableText']),
            Paragraph("OpenRouteService API + Leaflet.js + Webhooks GPS (cada 15s).", styles['TableText']),
            Paragraph("<b>Rutas óptimas y ETA en tiempo real.</b> Haversine para distancias.", styles['TableText'])
        ],
        [
            Paragraph("<b>Control de Negocio</b>", styles['TableText']),
            Paragraph("State Machine Hexagonal (7 estados) + Semáforo Profit Plus (procesado_profit).", styles['TableText']),
            Paragraph("<b>Garantía de estados válidos</b> e integración ERP bidireccional.", styles['TableText'])
        ],
        [
            Paragraph("<b>Seguridad & RBAC</b>", styles['TableText']),
            Paragraph("Control de acceso por rol (admin/operador) con filtro case-insensitive vía LOWER().", styles['TableText']),
            Paragraph("Reportes de trazabilidad y discrepancias con datos restringidos por usuario.", styles['TableText'])
        ],
        [
            Paragraph("<b>Visor OCR Físico</b>", styles['TableText']),
            Paragraph("WebRTC + file input con capture='environment'. OCR procesado por Ollama LLaVA.", styles['TableText']),
            Paragraph("<b>Captura y parseo instantáneo</b> de notas físicas sin scanner dedicado.", styles['TableText'])
        ]
    ]

    arq_table = Table(arq_data, colWidths=[140, 220, 180])
    arq_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_secondary),
        ('ALIGN', (0,0), (-1,0), 'CENTER'),
        ('BOX', (0,0), (-1,-1), 0.5, c_border),
        ('INNERGRID', (0,0), (-1,-1), 0.5, c_border),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [c_white, c_bg_light])
    ]))
    story.append(arq_table)
    story.append(Spacer(1, 12))

    # =====================================================================
    # 3. MATRIZ COMPARATIVA DE MERCADO
    # =====================================================================
    story.append(Paragraph("3. Matriz Comparativa: Proyecto ARA v3.10 vs. Mercado", styles['SectionHeader']))
    story.append(HRFlowable(width="100%", thickness=1.5, color=c_secondary, spaceAfter=8))

    comp_data = [
        [
            Paragraph("<b>Criterio</b>", styles['TableHeaderText']),
            Paragraph("<b>Proyecto ARA v3.10</b>", styles['TableHeaderText']),
            Paragraph("<b>WMS Tradicionales (SAP/Oracle)</b>", styles['TableHeaderText']),
            Paragraph("<b>Lectores RF / Básicos</b>", styles['TableHeaderText'])
        ],
        [
            Paragraph("<b>Identificación y Visión</b>", styles['TableText']),
            Paragraph("<b>IA Multimodel (NIM/LLaVA):</b> Reconoce empaques, OCR, códigos dañados y notas físicas.", styles['TableText']),
            Paragraph("Lectura rígida de código de barras perfecto.", styles['TableText']),
            Paragraph("Inoperante sin etiqueta física legible.", styles['TableText'])
        ],
        [
            Paragraph("<b>Rutas y Telemetría</b>", styles['TableText']),
            Paragraph("<b>Optimización ORS + GPS en vivo.</b> Dashboard Leaflet con ETA y posición del chofer.", styles['TableText']),
            Paragraph("Módulos rígidos o plugins externos costosos.", styles['TableText']),
            Paragraph("Sin capacidad de ruteo.", styles['TableText'])
        ],
        [
            Paragraph("<b>Resiliencia / Offline</b>", styles['TableText']),
            Paragraph("<b>Failover local a Mini ARA Edge + SQLite WAL.</b> Operación continua sin internet.", styles['TableText']),
            Paragraph("Dependencia total de servidor central.", styles['TableText']),
            Paragraph("Totalmente inoperante sin conexión.", styles['TableText'])
        ],
        [
            Paragraph("<b>Auditoría e Integración ERP</b>", styles['TableText']),
            Paragraph("<b>Semáforo Profit Plus + trazabilidad 360°</b> en lenguaje natural con Context Injection.", styles['TableText']),
            Paragraph("Reportes estáticos y transacciones rígidas.", styles['TableText']),
            Paragraph("Sin capacidad de auditoría.", styles['TableText'])
        ],
        [
            Paragraph("<b>Costo Total</b>", styles['TableText']),
            Paragraph("<b>Ultra económico:</b> Corre en laptops, Chromebooks o Docker. Cero licencias.", styles['TableText']),
            Paragraph("Licencias millonarias + servidores dedicados + consultoría.", styles['TableText']),
            Paragraph("Costo medio en hardware RF propietario.", styles['TableText'])
        ],
        [
            Paragraph("<b>Flexibilidad</b>", styles['TableText']),
            Paragraph("<b>100% modular:</b> Backend Python REST API de código abierto, fácil integración.", styles['TableText']),
            Paragraph("Cambios requieren consultoría externa costosa y meses.", styles['TableText']),
            Paragraph("Firmware cerrado, nula personalización.", styles['TableText'])
        ]
    ]

    comp_table = Table(comp_data, colWidths=[105, 145, 150, 140])
    comp_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_primary),
        ('BOX', (0,0), (-1,-1), 0.5, c_border),
        ('INNERGRID', (0,0), (-1,-1), 0.5, c_border),
        ('PADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [c_white, c_bg_light])
    ]))
    story.append(comp_table)
    story.append(Spacer(1, 12))

    # =====================================================================
    # 4. ROADMAP ESTRATÉGICO
    # =====================================================================
    story.append(PageBreak())
    story.append(Paragraph("4. Roadmap Estratégico & Conclusión", styles['SectionHeader']))
    story.append(HRFlowable(width="100%", thickness=1.5, color=c_secondary, spaceAfter=8))

    # Corto Plazo
    roadmap_data = [
        [
            Paragraph("<b>Plazo</b>", styles['TableHeaderText']),
            Paragraph("<b>Objetivo</b>", styles['TableHeaderText']),
            Paragraph("<b>Tecnología</b>", styles['TableHeaderText']),
            Paragraph("<b>Impacto Esperado</b>", styles['TableHeaderText'])
        ],
        [
            Paragraph("<b>Corto Plazo</b>", styles['TableText']),
            Paragraph("Modelos ONNX/TensorRT cuantizados para ejecución 100% offline en Android PDAs.", styles['TableText']),
            Paragraph("ONNX Runtime, TensorRT, cuantización INT8.", styles['TableText']),
            Paragraph("Inferencia en dispositivo sin conexión a servidor.", styles['TableText'])
        ],
        [
            Paragraph("<b>Mediano Plazo</b>", styles['TableText']),
            Paragraph("Abstracción a PostgreSQL + Redis para escalar a 10,000+ conexiones concurrentes.", styles['TableText']),
            Paragraph("PostgreSQL, Redis Cluster, SQLAlchemy async.", styles['TableText']),
            Paragraph("Escalado horizontal y alta disponibilidad.", styles['TableText'])
        ],
        [
            Paragraph("<b>Largo Plazo</b>", styles['TableText']),
            Paragraph("Algoritmo A* de picking en 3D para optimización de rutas dentro del almacén.", styles['TableText']),
            Paragraph("A* pathfinding, mapa 3D del almacén, pesos dinámicos.", styles['TableText']),
            Paragraph("Reducción del 35% en tiempo de caminata del operador.", styles['TableText'])
        ]
    ]

    roadmap_table = Table(roadmap_data, colWidths=[90, 180, 130, 140])
    roadmap_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_secondary),
        ('ALIGN', (0,0), (-1,0), 'CENTER'),
        ('BOX', (0,0), (-1,-1), 0.5, c_border),
        ('INNERGRID', (0,0), (-1,-1), 0.5, c_border),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [c_white, c_bg_light])
    ]))
    story.append(roadmap_table)
    story.append(Spacer(1, 14))

    # Cuadro de Conclusión Ejecutiva
    conclusion_items = [
        "<b>Arquitectura Hexagonal Probada:</b> El sistema ha demostrado operar 24/7 con cero caídas "
        "desde su implementación inicial, manejando cientos de transacciones diarias sin bloqueos "
        "gracias a SQLite WAL y el lock de escritura con reintento.",

        "<b>IA Híbrida Lista para Producción:</b> La combinación de Key Pool NVIDIA NIM (fallover "
        "automático entre 5 claves) + Mini ARA Edge (offline &lt; 2s) garantiza disponibilidad "
        "permanente del asistente inteligente, incluso en escenarios de desconexión total de internet.",

        "<b>Integración ERP a Costo Cero:</b> El semáforo Profit Plus y la doble escritura SQLite+JSON "
        "permiten sincronización bidireccional con Profit sin necesidad de costosos middleware "
        "ni licencias adicionales.",

        "<b>Trazabilidad Total y Transparencia:</b> La State Machine Hexagonal con 7 estados "
        "validados, el feedback loop sobre inferencias y el RBAC case-insensitive garantizan "
        "que cada operación quede registrada y auditada.",
    ]

    for item in conclusion_items:
        story.append(Paragraph(f"✅ {item}", styles['BulletCustom']))

    story.append(Spacer(1, 10))

    conclusion_data = [
        [
            Paragraph(
                "<b>Conclusión Ejecutiva:</b> Proyecto ARA v3.10 demuestra que un middleware logístico "
                "construido con Python, Flask, SQLite y JS Vanilla puede competir —y superar— en "
                "capacidades a sistemas corporativos que requieren inversiones millonarias en licencias, "
                "infraestructura y consultoría. Su arquitectura hexagonal, motor de IA híbrida con "
                "failover local, trazabilidad de estados atómicos y telemetría GPS en tiempo real "
                "lo posicionan como una solución Enterprise Ready, escalable y de mantenimiento "
                "casi nulo. El sistema está preparado para escalar a 10,000+ conexiones mediante "
                "PostgreSQL + Redis, y su capa de visión local con LLaVA permite operaciones "
                "offline sin depender de APIs externas. ARA no es solo un WMS: es el cerebro "
                "logístico del almacén del futuro.",
                styles['BodyCustom']
            )
        ]
    ]
    conclusion_table = Table(conclusion_data, colWidths=[540])
    conclusion_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#e0f2fe")),
        ('BOX', (0,0), (-1,-1), 1, c_accent),
        ('PADDING', (0,0), (-1,-1), 8),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
    ]))
    story.append(conclusion_table)

    # Línea de cierre
    story.append(Spacer(1, 14))
    closing_data = [
        [
            Paragraph(
                "<i>Documento generado automáticamente por Proyecto ARA — Julio 2026.</i>",
                ParagraphStyle('Closing', parent=styles['Normal'], fontSize=7.5, leading=10,
                               textColor=colors.HexColor("#94a3b8"), alignment=1)
            )
        ]
    ]
    closing_table = Table(closing_data, colWidths=[540])
    closing_table.setStyle(TableStyle([
        ('PADDING', (0,0), (-1,-1), 4),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
    ]))
    story.append(closing_table)

    # Generar PDF
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Documento generado con exito: {filename}")

if __name__ == "__main__":
    generar_pdf_ara()

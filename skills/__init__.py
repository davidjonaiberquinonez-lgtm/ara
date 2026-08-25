"""Ecosistema de Skills de IA Local del Almacén (Proyecto ARA).

Subsistemas:

* ``stock_bulto_cerrado`` — Cola de surtido prioritario y reporte de quiebres
  para compras (S/C y BQTO).
* ``recepcion`` — Recepción de mercancía vía visión OCR (facturas/notas de
  crédito a PDF normalizado).
* ``auditoria`` — Detección de mal surtido en logs y discrepancia en
  traslados inter-sedes.

Todos los skills son best-effort (nunca lanzan) y resuelven el esquema de
la base de datos dinámicamente vía INFORMATION_SCHEMA (ver ``base``).
"""

from skills.base import conectar, cerrar  # noqa: F401  (re-export útil)

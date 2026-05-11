# -*- coding: utf-8 -*-
"""Dispatcher de formatos de PDF.

Detecta automáticamente el formato del PDF leyendo el texto de la primera
página y delega al parser correspondiente:

- "Off-line Preview" (Pepperi)            -> pdf_parser.parse_proforma()
- "Factura de Cliente" + "SAP Business One" -> pdf_parser_sap.parse()

Si no matchea ninguno, lanza ParseError con mensaje claro.

Ambos parsers devuelven el MISMO shape de datos:
    {"format": str, "client": str|None, "items": [{...}]}
de modo que el resto del pipeline (búsqueda en Dropbox, copia, reporte)
no necesita ramificar lógica por formato.
"""

import pdfplumber

import pdf_parser
import pdf_parser_sap
from pdf_parser import ParseError


def parse(path):
    """Detecta el formato del PDF y llama al parser correspondiente.

    Raises:
        ParseError: si el PDF está protegido, no se puede abrir, o el
                    formato no es reconocido.
    """
    try:
        pdf = pdfplumber.open(path)
    except Exception as e:
        raise ParseError(f"No se pudo abrir el PDF: {e}")

    with pdf:
        if getattr(pdf, "is_encrypted", False):
            raise ParseError("El PDF está protegido con contraseña")
        first_text = (pdf.pages[0].extract_text() or "").lower()

    is_factura_sap = (
        "factura de cliente" in first_text
        and "sap business one" in first_text
    )
    # SAP "Proforma de Cliente" y "Cotizacion de Cliente" tienen sus propios
    # parsers dentro de pdf_parser.py (formato distinto al de Factura).
    is_proforma_sap = "proforma de cliente" in first_text
    is_cotizacion_sap = (
        "cotizacion de cliente" in first_text
        or "cotización de cliente" in first_text
    )
    # Pepperi: el texto del PDF NO contiene "Off-line Preview" (eso solo está
    # en el nombre del archivo). Detectamos por las secciones típicas:
    # "Informacion General", "Detalles del Pedido", "Informacion de la Cuenta".
    is_pepperi = (
        "off-line" in first_text
        or "visualización off-line" in first_text
        or "visualizacion off-line" in first_text
        or "detalles del pedido" in first_text
        or "informacion de la cuenta" in first_text
        or "información de la cuenta" in first_text
    )

    if is_factura_sap:
        return pdf_parser_sap.parse(path)

    # Los 3 formatos restantes los maneja pdf_parser.parse_proforma() que tiene
    # su propio dispatcher interno (pepperi / sap_proforma / sap_cotizacion).
    if is_pepperi or is_proforma_sap or is_cotizacion_sap:
        return pdf_parser.parse_proforma(path)

    raise ParseError(
        "Formato de PDF no reconocido.\n\n"
        "La app soporta:\n"
        "  • Proformas de Pepperi (Off-line Preview)\n"
        "  • Facturas de Cliente de SAP Business One\n"
        "  • Proformas / Cotizaciones de SAP Business One"
    )

# -*- coding: utf-8 -*-
"""Parsea PDFs de proforma y extrae items (sku, cantidad).

Formatos soportados:
- 'pepperi'         -> Pepperi 'Off-line Preview' / 'Visualizacion off-line'
- 'sap_proforma'    -> SAP Business One 'Proforma de Cliente'
- 'sap_cotizacion'  -> SAP Business One 'Cotizacion de Cliente'

Devuelve dict:
    {
        "format": "<nombre_formato>",
        "client": "Inversiones Jase Hp S.r.l." | None,
        "items": [
            {"sku": "GP14952/A/TAN", "qty": 72, "suspect": False, "raw": "GP14952/A/TAN"},
            ...
        ]
    }

- "sku"     : SKU limpio listo para usar
- "qty"     : cantidad de pares pedidos (entero)
- "suspect" : True si el parser cree que el SKU puede tener basura
              de la columna descripcion intercalada (caso PDF 3).
              El finder usara "raw" para hacer fuzzy match si el SKU
              literal no existe en disco.
- "raw"     : texto crudo de la palabra que contenia el SKU.

Raises:
    ParseError: PDF protegido, formato desconocido, o sin items.
"""

import re
from collections import defaultdict

import pdfplumber


# Patron de SKU al inicio de un string. Greedy: agarra hasta la siguiente
# minuscula o caracter no [A-Z0-9/]
SKU_PREFIX_RE = re.compile(r"^([A-Z]{2,4}\d+(?:/[A-Z0-9]+){1,2})")
# Patron estricto: la palabra ENTERA es un SKU (sin basura al final).
SKU_EXACT_RE = re.compile(r"^[A-Z]{2,4}\d+(?:/[A-Z0-9]+){1,2}$")
# Continuacion de SKU partido: 1-4 letras solas (ej "LK", "D", "E")
SKU_TAIL_RE = re.compile(r"^[A-Z]{1,4}$")


class ParseError(Exception):
    """Algo salio mal parseando el PDF (protegido, formato desconocido, vacio)."""


def parse_proforma(path):
    """Punto de entrada. Devuelve dict {format, items}. Ver docstring del modulo."""
    try:
        pdf = pdfplumber.open(path)
    except Exception as e:
        # pdfplumber lanza si esta encriptado/corrupto
        raise ParseError(f"No se pudo abrir el PDF: {e}")

    with pdf:
        if getattr(pdf, "is_encrypted", False):
            raise ParseError("El PDF esta protegido con contrasena")

        fmt = _detect_format(pdf)
        if fmt is None:
            raise ParseError(
                "Formato de PDF no reconocido. "
                "Soporto Pepperi 'Off-line Preview' y SAP 'Proforma'/'Cotizacion'."
            )

        parser = {
            "pepperi": _parse_pepperi_page,
            "sap_proforma": _parse_sap_proforma_page,
            "sap_cotizacion": _parse_sap_cotizacion_page,
        }[fmt]

        client = _extract_client_name(pdf, fmt)

        items = []
        for page in pdf.pages:
            items.extend(parser(page))

    if not items:
        raise ParseError(
            "No se encontraron codigos de producto en el PDF. "
            "Verifica que sea una proforma con tabla de productos."
        )

    return {"format": fmt, "client": client, "items": items}


# -----------------------------------------------------------------------------
# Deteccion de formato
# -----------------------------------------------------------------------------

def _detect_format(pdf):
    """Devuelve nombre del formato leyendo texto de la 1a pagina."""
    text = (pdf.pages[0].extract_text() or "").lower()
    if "off-line" in text or "visualizacion off-line" in text or "visualización off-line" in text:
        return "pepperi"
    if "proforma de cliente" in text:
        return "sap_proforma"
    if "cotizacion de cliente" in text or "cotización de cliente" in text:
        return "sap_cotizacion"
    # Fallback: si tiene 'detalles del pedido' y 'imagen|referencia|...' es Pepperi
    if "detalles del pedido" in text:
        return "pepperi"
    return None


# -----------------------------------------------------------------------------
# Extraccion del nombre del cliente
# -----------------------------------------------------------------------------

# Codigo de cliente tipico al inicio (PA26-27, CR23-44, etc.) - lo sacamos
# para quedarnos con el NOMBRE limpio.
_CLIENT_CODE_RE = re.compile(r"^[A-Z]{2,4}\d+[\-\d]*$")


def _extract_client_name(pdf, fmt):
    """Extrae el nombre del cliente segun el formato del PDF.

    Devuelve string limpio o None si no se pudo identificar.
    """
    text = pdf.pages[0].extract_text() or ""

    if fmt == "pepperi":
        # Linea tipica: "Cliente Inversiones Jase Hp S.r.l."
        # PERO el PDF tiene 2 columnas y extract_text las concatena, asi que
        # puede salir como "Cliente Inversiones Jase Hp S.r.l. ID -4205".
        # Cortamos en el primer marcador del header derecho.
        # OJO: tambien aparece "Codigo de Cliente CR23-44" en otra linea -
        # esa la descartamos porque no arranca con "Cliente".
        right_col_markers = re.compile(
            r"\s+(ID\s|Codigo\s|Fecha\b|Vendedor\b|Pais\b|Telefono\b|Marca\b|Lugar\b)"
        )
        for line in text.split("\n"):
            line = line.strip()
            m = re.match(r"^Cliente[:\s]+(.+)$", line)
            if not m:
                continue
            value = m.group(1).strip()
            # Cortar la 2da columna si se metió pegada
            cut = right_col_markers.search(value)
            if cut:
                value = value[:cut.start()].strip()
            # Si lo que queda es solo un codigo (CR23-44), descartar
            if _CLIENT_CODE_RE.match(value):
                continue
            if value:
                return value
        return None

    if fmt == "sap_proforma":
        # Linea tipica: "Cliente PA26-27 NICOLAS BEDOYA Fecha Vencimiento: 05/05/2026"
        # Queremos "NICOLAS BEDOYA" (sin el codigo, sin la fecha al final).
        for line in text.split("\n"):
            line = line.strip()
            m = re.match(r"^Cliente[:\s]+(.+)$", line)
            if not m:
                continue
            rest = m.group(1)
            # Cortar "Fecha..." si esta al final (pueden venir Fecha Vencimiento, etc.)
            rest = re.split(r"\s+Fecha\b", rest, maxsplit=1)[0]
            # Sacar codigo de cliente al inicio si lo hay
            parts = rest.split(maxsplit=1)
            if parts and _CLIENT_CODE_RE.match(parts[0]) and len(parts) >= 2:
                return parts[1].strip()
            return rest.strip()
        return None

    if fmt == "sap_cotizacion":
        # Linea tipica: "Nombre: SUPER SNKRS, S.A. Consignado:"
        for line in text.split("\n"):
            line = line.strip()
            m = re.match(r"^Nombre[:\s]+(.+)$", line)
            if not m:
                continue
            rest = m.group(1)
            # Cortar etiquetas que vienen al lado en la misma linea
            for marker in (" Consignado", " Despachado", " Vendedor"):
                if marker in rest:
                    rest = rest.split(marker)[0]
            return rest.strip()
        return None

    return None


# -----------------------------------------------------------------------------
# Helpers comunes
# -----------------------------------------------------------------------------

def _group_words_by_row(words, y_tol=3):
    """Agrupa palabras por fila Y (con tolerancia). Devuelve [(y, [words]), ...] ordenado por Y."""
    rows = defaultdict(list)
    for w in words:
        key = round(w["top"] / y_tol) * y_tol
        rows[key].append(w)
    out = []
    for y in sorted(rows.keys()):
        out.append((y, sorted(rows[y], key=lambda w: w["x0"])))
    return out


def _find_header_y(words, header_text="Referencia"):
    """Y del header indicado, o None."""
    for w in words:
        if w["text"] == header_text:
            return w["top"]
    return None


def _first_int_after(row, x_min):
    """Primer entero (no decimal) en la fila con x0 > x_min. Devuelve int o 0."""
    for w in row:
        if w["x0"] <= x_min:
            continue
        t = w["text"].replace(",", "").replace("$", "").strip()
        if t.isdigit():
            return int(t)
    return 0


# -----------------------------------------------------------------------------
# Parser: Pepperi
# -----------------------------------------------------------------------------

def _parse_pepperi_page(page):
    """Tabla limpia: cada fila tiene SKU exacto y cantidad como entero.

    Nota: paginas siguientes a la 1ra pueden NO reimprimir el header de
    columna en Pepperi. Procesamos toda fila que tenga un SKU exacto.
    """
    items = []
    words = page.extract_words(x_tolerance=1, y_tolerance=2)
    header_y = _find_header_y(words, "Referencia")
    skip_above = (header_y + 5) if header_y is not None else -1

    for y, row in _group_words_by_row(words):
        if y <= skip_above:
            continue
        # Encontrar SKU exacto en la fila
        sku_word = next((w for w in row if SKU_EXACT_RE.match(w["text"])), None)
        if not sku_word:
            continue
        sku = sku_word["text"]
        qty = _first_int_after(row, sku_word["x1"])
        items.append({"sku": sku, "qty": qty, "suspect": False, "raw": sku})
    return items


# -----------------------------------------------------------------------------
# Parser: SAP Proforma (PDF 2)
# -----------------------------------------------------------------------------

def _parse_sap_proforma_page(page):
    """SAP Proforma. Cada producto es un BLOQUE vertical de varias filas Y.

    En el bloque tipico:
      y=360  SNH208/A/WHTBGEB  WHITE  BEIGE        <- fila SKU + color (parte 1)
      y=363  1                                     <- bultos
      y=366  11.700  0.120  Zapatillas ... 12 14.00 $ 168.00  <- datos
      y=369  LK   BLACK                            <- continuacion SKU + color (parte 2)

    Estrategia:
    - Detectar X de las columnas Referencia y Cant (Pares) por sus headers.
    - Por cada palabra-SKU en col Referencia, buscar continuacion (1-4 mayus
      alineada al X del SKU, 3-15px abajo) y cantidad (entero en col Cant,
      dentro de +/-15px del SKU).
    """
    items = []
    words = page.extract_words(x_tolerance=1, y_tolerance=2)
    ref_word = next((w for w in words if w["text"] == "Referencia"), None)
    qty_word = next((w for w in words if w["text"] in ("Cantidad", "Cant")), None)
    if ref_word is None or qty_word is None:
        return items

    ref_center = (ref_word["x0"] + ref_word["x1"]) / 2
    qty_center = (qty_word["x0"] + qty_word["x1"]) / 2

    # SKUs en columna Referencia (centro X cerca del header)
    sku_words = [
        w for w in words
        if SKU_PREFIX_RE.match(w["text"])
        and abs((w["x0"] + w["x1"]) / 2 - ref_center) < 50
    ]
    sku_words.sort(key=lambda w: w["top"])

    for sku_word in sku_words:
        sku = sku_word["text"]
        y_sku = sku_word["top"]

        # Continuacion: palabra de 1-4 mayus en col Referencia, justo debajo
        for w in words:
            if w is sku_word:
                continue
            dy = w["top"] - y_sku
            if not (2 < dy < 15):
                continue
            if abs(w["x0"] - sku_word["x0"]) > 5:
                continue
            if SKU_TAIL_RE.match(w["text"]):
                sku = sku + w["text"]
                break

        m = SKU_PREFIX_RE.match(sku)
        if not m:
            continue
        sku_clean = m.group(1)

        # Cantidad: entero en col Cant, dentro del bloque (-3 a +15 en Y)
        qty = 0
        for w in words:
            dy = w["top"] - y_sku
            if not (-3 <= dy <= 15):
                continue
            if abs((w["x0"] + w["x1"]) / 2 - qty_center) > 25:
                continue
            t = w["text"].replace(",", "").strip()
            if t.isdigit() and "." not in w["text"]:
                qty = int(t)
                break

        items.append({"sku": sku_clean, "qty": qty, "suspect": False, "raw": sku})
    return items


# -----------------------------------------------------------------------------
# Parser: SAP Cotizacion (PDF 3)
# -----------------------------------------------------------------------------

# Patron de "contaminacion" tipica: 'Zapatillas' o 'Botas' (capitalizada + minusc.)
_CONTAM_RE = re.compile(r"[A-Z][a-z]")

def _parse_sap_cotizacion_page(page):
    """SAP Cotizacion: SKU puede estar pegado a 'Zapatillas...' con chars intercalados.

    Ejemplo crudo: 'SNM073/A/BGEGRYGZDapatillas' (real: 'SNM073/A/BGEGRYGD').
    Estrategia:
    - Tomamos el match SKU_PREFIX_RE como SKU "limpio" tentativo.
    - Si el resto contiene 'apatilla' o letra-mayus + minusculas (Zapatillas/Botas/etc),
      flag suspect=True. El finder probara variantes contra disco usando "raw".
    """
    items = []
    words = page.extract_words(x_tolerance=1, y_tolerance=2)
    ref_word = next((w for w in words if w["text"] == "Referencia"), None)
    qty_word = next((w for w in words if w["text"] in ("Cantidad", "Cant")), None)
    if ref_word is None or qty_word is None:
        return items

    ref_center = (ref_word["x0"] + ref_word["x1"]) / 2
    qty_center = (qty_word["x0"] + qty_word["x1"]) / 2

    sku_words = [
        w for w in words
        if SKU_PREFIX_RE.match(w["text"])
        and abs((w["x0"] + w["x1"]) / 2 - ref_center) < 60
    ]
    sku_words.sort(key=lambda w: w["top"])

    for sku_word in sku_words:
        raw = sku_word["text"]
        m = SKU_PREFIX_RE.match(raw)
        sku = m.group(1)
        rest = raw[len(sku):]
        suspect = bool(rest) and bool(_CONTAM_RE.search(rest) or rest[0].islower())

        # Cantidad: entero en col Cant cerca del SKU
        y_sku = sku_word["top"]
        qty = 0
        for w in words:
            dy = w["top"] - y_sku
            if not (-3 <= dy <= 8):
                continue
            if abs((w["x0"] + w["x1"]) / 2 - qty_center) > 25:
                continue
            t = w["text"].replace(",", "").strip()
            if t.isdigit() and "." not in w["text"]:
                qty = int(t)
                break

        items.append({"sku": sku, "qty": qty, "suspect": suspect, "raw": raw})
    return items


# -----------------------------------------------------------------------------
# Smoke test
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from pathlib import Path
    # Smoke test: pasá paths de PDFs como args, o por default toma cualquier
    # PDF en ~/Downloads cuyo nombre contenga "proforma", "off-line", etc.
    paths = sys.argv[1:]
    if not paths:
        downloads = Path.home() / "Downloads"
        if downloads.is_dir():
            for p in downloads.iterdir():
                if p.suffix.lower() == ".pdf":
                    paths.append(str(p))
                    if len(paths) >= 3:
                        break
    for p in paths:
        print(f"\n=== {p.split('/')[-1]} ===")
        try:
            res = parse_proforma(p)
        except ParseError as e:
            print(f"  ParseError: {e}")
            continue
        print(f"  format: {res['format']}    items: {len(res['items'])}")
        suspect_count = sum(1 for it in res["items"] if it["suspect"])
        if suspect_count:
            print(f"  sospechosos: {suspect_count}")
        for it in res["items"]:
            mark = "?" if it["suspect"] else " "
            extra = f"   raw={it['raw']!r}" if it["suspect"] else ""
            print(f"  {mark} qty={it['qty']:>4}  sku={it['sku']}{extra}")
# -*- coding: utf-8 -*-
"""Parser para PDFs de SAP Business One 'Factura de Cliente'.

Estructura del PDF:
- Múltiples páginas con el mismo header en cada una.
- Una tabla de productos que se continúa entre páginas.
- Última página con totales al final (Peso Total, Sub Total, etc.).

Cada línea de producto tiene formato (texto extraído):
    CODIGO  DESCRIPCION  TALLAS  COLOR  CANTIDAD  CTNS  $ PRECIO  $ TOTAL

Caso especial - código pegado a descripción:
Cuando el código es largo (típicamente cuando termina en SURTIDO), queda
pegado a la descripción sin espacio:
    "MR098/A/SURTIDOZapatillas para Hombre Hombre 39-44 SURTIDO 216 6 $ 7.75 $ 1,674.00"
    "BB018/B/SURTIDOSandalias para Damas Dama 36 - 41 SURTIDO 180 5 $ 3.75 $ 675.00"

El regex de código es greedy y captura los caracteres de color. Si después
del match viene una minúscula, la última letra mayúscula capturada es en
realidad la primera letra de la descripción (Zapatillas, Sandalias, etc).
Se descarta esa letra final del código.

Caso especial - SURTID vs SURTIDO:
A veces el color queda truncado como "SURTID" en lugar de "SURTIDO". Los
normalizamos siempre a "SURTIDO" para que el match con archivos en Dropbox
sea consistente.

Devuelve el mismo shape que pdf_parser.parse_proforma():
    {
        "format": "sap_factura",
        "client": "INVERSIONES JASE HP S.R.L." | None,
        "items": [
            {"sku": "GP15094/A/BLK", "qty": 72, "suspect": False, "raw": "GP15094/A/BLK"},
            ...
        ]
    }
"""

import re

import pdfplumber

# Reutilizamos ParseError de pdf_parser para mantener una sola excepción
# en toda la app.
from pdf_parser import ParseError


# --- Regex --------------------------------------------------------------------

# Código al inicio de línea: 2-4 letras (prefijo marca) + dígitos +
# /A o /B (variante) + barra + letras (color).
# El [A-Z]+ es greedy: si el código es "SURTIDOZapatillas", capturará
# "SURTIDOZ" porque la Z (de Zapatillas) también es uppercase. El caller
# detecta este caso y trimea el último char (ver _parse_product_line).
_CODE_RE = re.compile(r"^([A-Z]{2,4}\d+/[AB]/[A-Z]+)")

# Cantidad: entero, seguido de otro entero (ctns), seguido de $ precio,
# seguido de $ total, al FINAL de la línea. Los precios pueden tener
# coma como separador de miles ("$ 1,674.00").
# El primer grupo capturado es la cantidad de pares.
_QTY_RE = re.compile(
    r"(\d+)\s+\d+\s+\$\s*[\d.,]+\s+\$\s*[\d.,]+\s*$"
)

# Cliente: "Cliente CR23-44 INVERSIONES JASE HP S.R.L. Fecha Vencimiento:"
# Capturamos el nombre del cliente (entre el código y "Fecha Vencimiento").
_CLIENT_RE = re.compile(
    r"^\s*Cliente\s+\S+\s+(.+?)(?:\s+Fecha\s+Vencimiento|\s*$)",
    re.IGNORECASE,
)


# --- API pública --------------------------------------------------------------

def parse(path):
    """Punto de entrada. Lee un PDF de Factura SAP y devuelve dict con
    format, client e items.

    Raises:
        ParseError: si el PDF no se puede abrir, está protegido, o no
                    contiene líneas de producto reconocibles.
    """
    try:
        pdf = pdfplumber.open(path)
    except Exception as e:
        raise ParseError(f"No se pudo abrir el PDF: {e}")

    items = []
    client = None
    seen_skus = set()

    with pdf:
        if getattr(pdf, "is_encrypted", False):
            raise ParseError("El PDF está protegido con contraseña")

        for page in pdf.pages:
            text = page.extract_text() or ""

            # Cliente: extraemos de la primera página que lo tenga.
            if client is None:
                for line in text.split("\n"):
                    m = _CLIENT_RE.match(line)
                    if m:
                        client = m.group(1).strip()
                        break

            # Productos: cada línea que matchee el regex.
            for line in text.split("\n"):
                item = _parse_product_line(line)
                if item is None:
                    continue
                # Dedup: el header se repite en cada página pero los productos
                # no - igual nos protegemos por si extract_text retorna
                # duplicados.
                key = (item["sku"], item["qty"])
                if key in seen_skus:
                    continue
                seen_skus.add(key)
                items.append(item)

    if not items:
        raise ParseError(
            "No se encontraron códigos de producto en el PDF. "
            "Verificá que sea una Factura de Cliente de SAP con tabla "
            "de productos."
        )

    return {
        "format": "sap_factura",
        "client": client,
        "items": items,
    }


# --- Helpers internos ---------------------------------------------------------

def _parse_product_line(line):
    """Intenta parsear UNA línea como un item de producto.
    Devuelve dict del item o None si la línea no es un producto.
    """
    line = line.strip()
    if not line:
        return None

    m = _CODE_RE.match(line)
    if not m:
        return None

    code = m.group(1)
    end_pos = m.end()

    # Si lo que viene después del código es una minúscula, la última letra
    # del código capturada es en realidad la primera letra de la descripción
    # (ej "SURTIDOZapatillas" → código real "SURTIDO", primera letra "Z"
    # de "Zapatillas").
    if end_pos < len(line) and line[end_pos].islower():
        code = code[:-1]

    # Normalizar SURTID -> SURTIDO. A veces el color queda truncado visualmente.
    if code.endswith("/SURTID"):
        code = code + "O"

    # Validar que el código resultante sea sano (exactamente 2 barras).
    if code.count("/") != 2:
        return None

    # Extraer cantidad del resto de la línea.
    rest = line[end_pos:]
    qty = 0
    qm = _QTY_RE.search(rest)
    if qm:
        try:
            qty = int(qm.group(1))
        except (ValueError, IndexError):
            qty = 0

    return {
        "sku": code,
        "qty": qty,
        "suspect": False,
        "raw": code,
    }


# --- Smoke test cuando se corre directo --------------------------------------

if __name__ == "__main__":
    import sys
    from pathlib import Path
    paths = sys.argv[1:]
    if not paths:
        downloads = Path.home() / "Downloads"
        if downloads.is_dir():
            for p in downloads.iterdir():
                if p.suffix.lower() == ".pdf":
                    paths.append(str(p))
                    if len(paths) >= 3:
                        break

    for path in paths:
        print(f"\n=== {Path(path).name} ===")
        try:
            res = parse(path)
        except ParseError as e:
            print(f"  ParseError: {e}")
            continue
        print(f"  cliente : {res['client']}")
        print(f"  items   : {len(res['items'])}")
        for it in res["items"][:8]:
            print(f"    qty={it['qty']:>4}  sku={it['sku']}")
        if len(res["items"]) > 8:
            print(f"    ... ({len(res['items']) - 8} más)")

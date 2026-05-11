# -*- coding: utf-8 -*-
"""Mapeo de prefijos de marca y parseo de SKUs de proformas Pepperi.

Un SKU típico viene en formato:
    PREFIJO + NUMERO + /VARIANTE + /COLOR
Ejemplo: GP14952/B/WHT
  - prefijo "GP"   -> marca GOSSIP
  - numero "14952"
  - variante "B"
  - color "WHT"

El match de prefijo es GREEDY: probamos primero el prefijo mas largo.
Asi "PPGM14952" matchea "PPGM" y no "PP".
"""

import re


# Prefijo -> nombre canonico de la marca
BRAND_MAP = {
    "GP": "GOSSIP",
    "GPN": "GOSSIP",
    "EM": "EMILY",
    "EI": "EMILY",
    "EN": "EMILY",
    "VXH": "VOX",
    "VXM": "VOX",
    "VXN": "VOX",
    "VXJ": "VOX",
    "BB": "B'LINDA",
    "BM": "BRUNO MARC",
    "MR": "MARCO ROSSO",
    "GSM": "G-SPORT",
    "TM": "TOP MODA",
    "SNH": "SNEAKERS SUPPLY",
    "SNM": "SNEAKERS SUPPLY",
    "SNJ": "SNEAKERS SUPPLY",
    "PPGH": "POLO PREMIER GROUP",
    "PPGM": "POLO PREMIER GROUP",
    "PPGN": "POLO PREMIER GROUP",
    "PPGJ": "POLO PREMIER GROUP",
}

# Posibles nombres de la carpeta de cada marca en Dropbox.
# La estructura del Dropbox no es 100% consistente:
#   - GRUPALES usa nombres largos (GOSSIP, EMILY, B´LINDA con acento agudo, ...)
#   - INDIVIDUALES a veces usa nombres cortos (SN, PPG) y a veces largos (GOSSIP)
#   - G-SPORT aparece como "G-SPORT" en 2026 y "GSPORT" en 2025
# Probamos cada candidato en orden hasta encontrar uno que exista.
BRAND_FOLDERS = {
    "GOSSIP": ["GOSSIP"],
    "EMILY": ["EMILY"],
    "VOX": ["VOX"],
    "B'LINDA": ["B´LINDA", "B'LINDA", "BLINDA", "B´LINDA"],
    "BRUNO MARC": ["BRUNO MARC"],
    "MARCO ROSSO": ["MARCO ROSSO"],
    "G-SPORT": ["G-SPORT", "GSPORT", "G SPORT"],
    "TOP MODA": ["TOP MODA"],
    "SNEAKERS SUPPLY": ["SNEAKERS SUPPLY", "SN"],
    "POLO PREMIER GROUP": ["POLO PREMIER GROUP", "PPG"],
}


def folder_candidates(brand):
    """Devuelve lista de nombres posibles de carpeta para la marca dada.
    Si la marca no esta en el mapa, devuelve [brand] como fallback."""
    return BRAND_FOLDERS.get(brand, [brand])


# Codigos de color que significan "surtido": el bulto trae varios colores
# mezclados, no un color especifico. Para estos NO buscamos foto individual,
# va directo a la grupal de la referencia.
SURTIDO_COLORS = {
    "SURT", "SURTIDO", "SURTIDOS",
    "SUR", "SDO", "STDO", "STD",
    "MIX", "MIXED",
}


def is_surtido(parsed):
    """True si el SKU representa un surtido (bulto multicolor).
    Para estos siempre se manda la foto grupal."""
    if not parsed:
        return False
    color = (parsed.get("color") or "").upper()
    return color in SURTIDO_COLORS

# Prefijos ordenados por longitud descendente -> match greedy
_PREFIXES_BY_LENGTH = sorted(BRAND_MAP.keys(), key=len, reverse=True)


def match_prefix(code):
    """Devuelve el prefijo de marca mas largo que matchea, o None.

    Solo aceptamos un prefijo si lo que sigue arranca con un digito,
    para evitar falsos matches tipo 'GP' contra 'GPSOMETHING'.
    """
    if not code:
        return None
    code_upper = code.strip().upper()
    for prefix in _PREFIXES_BY_LENGTH:
        if code_upper.startswith(prefix):
            rest = code_upper[len(prefix):]
            if rest and rest[0].isdigit():
                return prefix
    return None


def parse_sku(code):
    """Parsea un SKU como 'GP14952/B/WHT'.

    Devuelve un dict con keys: prefix, number, variant, color, brand.
    Si el prefijo no se reconoce o el formato es invalido, devuelve None.

    variant y/o color pueden ser None si no vienen en el SKU.
    """
    if not code:
        return None
    code_upper = code.strip().upper()
    prefix = match_prefix(code_upper)
    if not prefix:
        return None

    rest = code_upper[len(prefix):]
    # rest tipico: "14952/B/WHT", "14952B/WHT", "14952"
    m = re.match(r"^(\d+)(.*)$", rest)
    if not m:
        return None
    number = m.group(1)
    tail = m.group(2)

    parts = [p for p in tail.split("/") if p]
    variant = parts[0] if len(parts) >= 1 else None
    color = parts[1] if len(parts) >= 2 else None

    return {
        "prefix": prefix,
        "number": number,
        "variant": variant,
        "color": color,
        "brand": BRAND_MAP[prefix],
    }


def base_filename(parsed):
    """Nombre del archivo grupal (sin extension): PREFIJO+NUMERO.

    Ejemplo: parsed de 'GP14952/B/WHT' -> 'GP14952'
    """
    return f"{parsed['prefix']}{parsed['number']}"


def full_filename(parsed):
    """Nombre del archivo individual (sin extension): PREFIJO+NUMERO+VARIANTE+COLOR.

    Ejemplo: parsed de 'GP14952/B/WHT' -> 'GP14952BWHT'
    Devuelve None si falta variante o color.
    """
    if not parsed.get("variant") or not parsed.get("color"):
        return None
    return f"{parsed['prefix']}{parsed['number']}{parsed['variant']}{parsed['color']}"


# --- Smoke test cuando se ejecuta directo ---
if __name__ == "__main__":
    casos = [
        "GP14952/B/WHT",
        "PPGM14952/A/BLK",   # prueba match greedy: debe ser PPGM, no PP
        "GPN1058/A/RED",     # GPN, no GP
        "EM2001/B/CAM",
        "VXH3030/A/NVY",
        "BB777/B/BRN",
        "TM12/A/GRY",
        "SNH9001/B/WHT",
        "GP14952",           # sin variante/color
        "XYZ1234/A/RED",     # marca desconocida
        "GP",                # invalido
        "",                  # vacio
    ]
    for c in casos:
        p = parse_sku(c)
        if p is None:
            print(f"{c!r:30} -> None")
        else:
            print(
                f"{c!r:30} -> {p['brand']:20} | "
                f"base={base_filename(p):10} | full={full_filename(p)}"
            )
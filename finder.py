# -*- coding: utf-8 -*-
"""Busqueda de fotos en la estructura de Dropbox.

==================================================================
REGLA DE ORO: ESTE MODULO ES SOLO LECTURA SOBRE DROPBOX.
==================================================================
Nunca llamar a rename / move / delete / write sobre archivos en
Dropbox. Si necesitas crear archivos nuevos, va a ~/Desktop.
Solo se permiten: iterdir, rglob, is_file, is_dir, stem, name,
suffix - todas read-only.

Estructura de Dropbox (solo lectura desde esta app):

    {dropbox}/GRUPALES/[ano]/[MARCA]/[archivo].{jpg|jpeg|png}
    {dropbox}/INDIVIDUALES/[ano]/[MARCA]/[subcarpeta_opcional]/[archivo].{jpg|jpeg|png}

Notas:
- El nombre de la carpeta de marca puede variar (ej: 'B´LINDA' con acento agudo,
  'G-SPORT' vs 'GSPORT', 'SN' vs 'SNEAKERS SUPPLY'). brands.folder_candidates()
  da la lista de variantes a probar.
- Las extensiones se comparan case-insensitive.
- En INDIVIDUALES la busqueda es recursiva.
- Para SKUs marcados como sospechosos por el parser (PDF SAP Cotizacion con
  chars intercalados con la descripcion), si el match literal falla, se intenta
  un fuzzy match contra los archivos del PREFIJO+NUMERO+VARIANTE.

Funciones publicas:
    find_grupal(parsed, year, dbx)
    find_individual(parsed, year, dbx, raw=None, suspect=False)
    find_grupal_with_fallback(parsed, year, dbx)

Cada una devuelve un Path al archivo encontrado, o None si nada matchea.
"""

import difflib
from pathlib import Path

from brands import folder_candidates


VALID_EXTS = {"jpg", "jpeg", "png"}
FUZZY_MIN_RATIO = 0.6  # umbral de similitud para aceptar match fuzzy


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _has_valid_ext(filename):
    """True si filename termina en .jpg/.jpeg/.png (case insensitive)."""
    if "." not in filename:
        return False
    return filename.rsplit(".", 1)[1].lower() in VALID_EXTS


def _years_descending(parent):
    """Subcarpetas de `parent` cuyo nombre es un ano de 4 digitos, mayor a menor."""
    if not parent.is_dir():
        return []
    years = []
    for sub in parent.iterdir():
        if sub.is_dir() and sub.name.isdigit() and len(sub.name) == 4:
            years.append(sub)
    years.sort(key=lambda p: int(p.name), reverse=True)
    return years


def _resolve_brand_dir(year_dir, brand):
    """Devuelve el primer Path existente probando los nombres alternativos
    de la marca dentro de year_dir, o None."""
    if not year_dir.is_dir():
        return None
    for cand in folder_candidates(brand):
        d = year_dir / cand
        if d.is_dir():
            return d
    return None


def _find_exact_in_dir(dirpath, target_stem, recursive):
    """Busca un archivo de imagen cuyo stem (sin extension) sea igual a target_stem
    (case insensitive). Si recursive, baja a subcarpetas."""
    if not dirpath.is_dir():
        return None
    target = target_stem.lower()
    iterator = dirpath.rglob("*") if recursive else dirpath.iterdir()
    for entry in iterator:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        if not _has_valid_ext(entry.name):
            continue
        if entry.stem.lower() == target:
            return entry
    return None


def _find_unique_prefix_match(dirpath, target_stem, recursive):
    """Busca un archivo cuyo stem EMPIECE con target_stem (case insensitive)
    y sea UNICO. Si hay un solo match -> Path. Si hay 0 o >1 -> None.

    Usado para resolver casos tipo proforma 'KHA' vs disco 'KHAKI'. La
    unicidad evita falsos positivos: si solo existe GP15069AKHAKI cuando
    pides GP15069AKHA, claramente es el mismo color. Pero si pides
    GP15069AB y hubiera ABLK y ABLKPT, no matcheamos (ambiguo)."""
    if not dirpath.is_dir():
        return None
    target_low = target_stem.lower()
    matches = []
    iterator = dirpath.rglob("*") if recursive else dirpath.iterdir()
    for entry in iterator:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        if not _has_valid_ext(entry.name):
            continue
        stem_low = entry.stem.lower()
        if stem_low.startswith(target_low):
            matches.append(entry)
            if len(matches) > 1:
                return None  # ambiguo, abortamos
    return matches[0] if matches else None


def _iter_image_files(dirpath, recursive):
    """Itera archivos de imagen del directorio."""
    if not dirpath.is_dir():
        return
    iterator = dirpath.rglob("*") if recursive else dirpath.iterdir()
    for entry in iterator:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        if _has_valid_ext(entry.name):
            yield entry


# -----------------------------------------------------------------------------
# Grupal: GRUPALES/[ano]/[MARCA]/{PREFIJO+NUMERO}.{ext}
# -----------------------------------------------------------------------------

def find_grupal(parsed, year, dropbox_root):
    """Busca foto grupal de la referencia.

    Convencion principal:
        GRUPALES/<año>/<MARCA>/<PREFIJO+NUMERO>.jpg     (ej GP14952.jpg)

    Convencion alternativa (algunas marcas como VOX):
        GRUPALES/<año>/<MARCA>/<PREFIJO+NUMERO+VARIANTE>.jpg
        Ej: VXN001N.jpg, VXN001INF.jpg
        En esos casos hay una grupal por variante (N, INF, A, etc).

    Estrategia:
        1. Buscar match exacto <PREFIJO+NUMERO>
        2. Si falla y el SKU trae variante, buscar <PREFIJO+NUMERO+VARIANTE>

    Args:
        parsed: dict de brands.parse_sku()
        year:   int (ej 2025) o None para recorrer todos los anos disponibles
        dropbox_root: Path a la raiz de Dropbox

    Devuelve Path o None.
    """
    base = f"{parsed['prefix']}{parsed['number']}"
    variant = parsed.get("variant")
    targets = [base]
    if variant:
        targets.append(f"{base}{variant}")  # VXN001N

    grupales = dropbox_root / "GRUPALES"
    years = [grupales / str(year)] if year is not None else _years_descending(grupales)

    for year_dir in years:
        brand_dir = _resolve_brand_dir(year_dir, parsed["brand"])
        if brand_dir is None:
            continue
        for tgt in targets:
            result = _find_exact_in_dir(brand_dir, tgt, recursive=False)
            if result:
                return result
    return None


# -----------------------------------------------------------------------------
# Individual: INDIVIDUALES/[ano]/[MARCA]/.../{PREFIJO+NUMERO+VARIANTE+COLOR}.{ext}
# -----------------------------------------------------------------------------

def find_individual(parsed, year, dropbox_root, raw=None, suspect=False):
    """Busca foto individual del SKU completo.

    Si suspect=True y no encontramos match literal, intentamos fuzzy match
    contra los archivos del PREFIJO+NUMERO+VARIANTE para resolver el caso
    de PDFs SAP Cotizacion donde chars del SKU se mezclan con la descripcion.

    Devuelve Path o None.
    """
    if not parsed.get("variant") or not parsed.get("color"):
        return None
    target = (
        f"{parsed['prefix']}{parsed['number']}"
        f"{parsed['variant']}{parsed['color']}"
    )
    individuales = dropbox_root / "INDIVIDUALES"
    years = [individuales / str(year)] if year is not None else _years_descending(individuales)

    # Pasada 1: match literal
    for year_dir in years:
        brand_dir = _resolve_brand_dir(year_dir, parsed["brand"])
        if brand_dir is None:
            continue
        result = _find_exact_in_dir(brand_dir, target, recursive=True)
        if result:
            return result

    # Pasada 2: prefix match unico. La proforma puede usar abreviatura
    # (ej "KHA") y el archivo en disco el nombre completo (ej "KHAKI").
    # Solo aceptamos si hay UN solo archivo que empiece con el target,
    # para evitar agarrar el color equivocado por casualidad.
    if len(parsed["color"]) >= 2:  # min 2 letras de color para evitar matches absurdos
        for year_dir in years:
            brand_dir = _resolve_brand_dir(year_dir, parsed["brand"])
            if brand_dir is None:
                continue
            result = _find_unique_prefix_match(brand_dir, target, recursive=True)
            if result:
                return result

    # Pasada 3 (solo si suspect): fuzzy match
    if suspect:
        best = None
        best_ratio = FUZZY_MIN_RATIO
        target_low = target.lower()
        prefix_var = f"{parsed['prefix']}{parsed['number']}{parsed['variant']}".lower()
        for year_dir in years:
            brand_dir = _resolve_brand_dir(year_dir, parsed["brand"])
            if brand_dir is None:
                continue
            for entry in _iter_image_files(brand_dir, recursive=True):
                stem_low = entry.stem.lower()
                # Solo candidatos que comparten PREFIJO+NUMERO+VARIANTE
                if not stem_low.startswith(prefix_var):
                    continue
                ratio = difflib.SequenceMatcher(None, stem_low, target_low).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best = entry
            # Si ya encontramos algo en este ano, no seguimos a anos mas viejos
            if best is not None:
                return best
    return None


# -----------------------------------------------------------------------------
# Listar (variante, color) existentes en disco para una referencia base
# -----------------------------------------------------------------------------

def list_existing_colors_for_ref(parsed_ref, year, dropbox_root):
    """Devuelve set de tuplas (variante, color) cuyas fotos individuales
    existen en INDIVIDUALES para la referencia base.

    Sirve para el modo "Grupal si está completa": comparamos los colores que
    el cliente pidió contra los colores que en realidad existen en disco
    para esa referencia. Si lo pedido cubre todo lo que existe, mandamos la
    grupal. Si no, mandamos individuales.

    Args:
        parsed_ref:    dict de brands.parse_sku() (usamos prefix, number, brand)
        year:          int o None (None = todos los años, union)
        dropbox_root:  Path

    Devuelve:
        set de tuplas (variante_upper, color_upper). set() vacío si no hay
        carpeta o no se encuentra nada.

    Asumimos que el nombre de archivo individual es:
        PREFIJO + NUMERO + VARIANTE(1 letra) + COLOR(rest).ext
    Ej: GP14952ABLK -> ('A', 'BLK')
        SNH230EBGEOLV -> ('E', 'BGEOLV')
    """
    base = f"{parsed_ref['prefix']}{parsed_ref['number']}"
    base_low = base.lower()
    individuales = dropbox_root / "INDIVIDUALES"
    if year is not None:
        years = [individuales / str(year)]
    else:
        years = _years_descending(individuales)

    result = set()
    for year_dir in years:
        brand_dir = _resolve_brand_dir(year_dir, parsed_ref["brand"])
        if brand_dir is None:
            continue
        for entry in _iter_image_files(brand_dir, recursive=True):
            stem = entry.stem
            if not stem.lower().startswith(base_low):
                continue
            tail = stem[len(base):]   # despues de "GP14952" viene "ABLK"
            if len(tail) >= 2 and tail[0].isalpha():
                variant = tail[0].upper()
                color = tail[1:].upper()
                # color debe ser puramente alfanumerico (no permite separadores raros)
                if color and all(c.isalnum() for c in color):
                    result.add((variant, color))
    return result


# -----------------------------------------------------------------------------
# Grupal con fallback a cualquier individual de la referencia base
# (DEPRECATED - quedó del modo viejo. No se usa en MODE_COMPLETE.)
# -----------------------------------------------------------------------------

def find_grupal_with_fallback(parsed, year, dropbox_root):
    """Primero busca grupal. Si no hay, devuelve la primera foto individual
    cuyo nombre comience con PREFIJO+NUMERO (cualquier variante / color).
    """
    g = find_grupal(parsed, year, dropbox_root)
    if g:
        return g

    base_low = f"{parsed['prefix']}{parsed['number']}".lower()
    individuales = dropbox_root / "INDIVIDUALES"
    years = [individuales / str(year)] if year is not None else _years_descending(individuales)

    for year_dir in years:
        brand_dir = _resolve_brand_dir(year_dir, parsed["brand"])
        if brand_dir is None:
            continue
        # Tomamos la primera foto que matchee (orden alfabetico de path para reproducibilidad)
        candidates = sorted(_iter_image_files(brand_dir, recursive=True))
        for entry in candidates:
            if entry.stem.lower().startswith(base_low):
                return entry
    return None


# -----------------------------------------------------------------------------
# Smoke test contra el Dropbox real
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from dropbox import find_dropbox_root
    from brands import parse_sku

    dbx = find_dropbox_root()
    print(f"Dropbox: {dbx}")

    casos = [
        # (sku, modo, year, descripcion)
        ("GP14269/A/BLK", "grupal",  2026, "Existe GP14269.jpg en GRUPALES/2026/GOSSIP"),
        ("GP14269/A/BLK", "grupal",  None, "Existe en algun ano"),
        ("GP14269/A/BLK", "grupal",  2025, "Quizas no exista en 2025"),
        ("GP1058/A/BLK",  "individual", 2025, "Existe GP1058ABLK.jpg en INDIVIDUALES/2025/GOSSIP"),
        ("GP1058/A/BLK",  "individual", None, "Existe en algun ano"),
        ("SNH246/A/BLK",  "individual", 2026, "Existe SNH246ABLK.jpg en INDIVIDUALES/2026/SN"),
        ("PPGH077/A/CHBLK", "individual", 2025, "Posible match en INDIVIDUALES/2025/PPG"),
        ("XX99999/A/BLK", "grupal",  None, "Marca no reconocida -> None"),
        ("GP99999/A/BLK", "grupal",  None, "No existe -> None"),
        ("GP99999/A/BLK", "fallback", None, "Sin grupal, busca cualquier individual"),
    ]
    for sku_str, modo, year, desc in casos:
        parsed = parse_sku(sku_str)
        if parsed is None:
            print(f"  [{modo:10}] {sku_str:25} year={year} -> None (marca no reconocida)")
            continue
        if modo == "grupal":
            res = find_grupal(parsed, year, dbx)
        elif modo == "individual":
            res = find_individual(parsed, year, dbx)
        elif modo == "fallback":
            res = find_grupal_with_fallback(parsed, year, dbx)
        rel = res.relative_to(dbx) if res else None
        print(f"  [{modo:10}] {sku_str:25} year={year}: {rel}")

    # Test fuzzy match para SKU sospechoso
    print("\n--- Fuzzy match (SAP Cotizacion sospechosos) ---")
    fuzzy_cases = [
        ("SNM073/A/BGEGRYGZD", "SNM073/A/BGEGRYGZDapatillas", 2025),
        ("SNH204/A/OFFWHTOZL", "SNH204/A/OFFWHTOZLapatillas", 2025),
        ("SNH151/A/BRNBLKORZ", "SNH151/A/BRNBLKORZapatillas", 2025),
    ]
    for sku_str, raw, year in fuzzy_cases:
        parsed = parse_sku(sku_str)
        if parsed is None:
            print(f"  {sku_str}: marca no reconocida")
            continue
        res = find_individual(parsed, year, dbx, raw=raw, suspect=True)
        rel = res.relative_to(dbx) if res else None
        print(f"  raw={raw!r}\n     -> {rel}")
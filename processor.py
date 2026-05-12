# -*- coding: utf-8 -*-
"""Orquestador principal: PDF -> carpeta de fotos lista para WhatsApp.

Flujo:
1. Parsear el PDF (pdf_parser.parse_proforma)
2. Resolver Dropbox (dropbox.find_dropbox_root)
3. Crear carpeta destino en ~/Desktop/Fotos de Proformas/Proforma_<timestamp>/
4. Copiar el PDF de la proforma como "01_<nombre>.pdf" (queda primero en Finder).
5. Para cada item, buscar foto segun modo + ano (finder)
6. Dedup: en modo grupal, una foto por referencia base; en individual, una por SKU
7. Copiar archivos a destino (shutil.copy desde Dropbox solo lectura)
8. Escribir "02_reporte.txt" con resumen y faltantes (queda 2do, despues del PDF).
9. Devolver dict con resultados

==================================================================
REGLA DE ORO: NUNCA modificar la carpeta de Dropbox.
==================================================================
- Esta app SOLO LEE del Dropbox sincronizado.
- Las copias salen DESDE Dropbox HACIA ~/Desktop.
- Nada de rename, move, delete, write back en archivos de Dropbox.
- _copy_to_dest() valida que el destino NUNCA cae adentro del Dropbox
  como guard final.
"""

import datetime
import os
import shutil
from pathlib import Path

from brands import parse_sku, base_filename, full_filename, is_surtido
import pdf_parser       # mantenido por compat: ParseError + smoke tests
import pdf_dispatch     # detecta el formato y dispatches al parser correcto
import finder
import dropbox as dropbox_mod


# Modos
MODE_GRUPAL     = "grupal"      # 1 grupal por referencia
MODE_INDIVIDUAL = "individual"  # 1 individual por SKU pedido
MODE_COMPLETE   = "complete"    # grupal si pediste TODOS los colores existentes,
                                 # si no individuales de los pedidos.

# Compat: el id viejo "fallback" se mapea a MODE_COMPLETE para no romper si
# alguien tiene la app vieja corriendo con ese valor en cache.
MODE_FALLBACK = MODE_COMPLETE

VALID_MODES = (MODE_GRUPAL, MODE_INDIVIDUAL, MODE_COMPLETE)


def _format_modo_label(modo):
    return {
        MODE_GRUPAL:     "Solo grupales",
        MODE_INDIVIDUAL: "Solo individuales",
        MODE_COMPLETE:   "Grupal si está completa",
    }.get(modo, modo)


_FS_BAD_CHARS = '/\\:*?"<>|'


def _sanitize_for_folder(name):
    """Saca caracteres prohibidos en nombres de archivo y limita largo."""
    cleaned = "".join((c if c not in _FS_BAD_CHARS else " ") for c in name)
    cleaned = " ".join(cleaned.split())  # collapse whitespace
    return cleaned[:60].strip()


def _make_dest_folder(client_name=None, now=None, dest_root=None,
                       explicit_name=None):
    """Crea <dest_root>/<carpeta>/ y devuelve Path.

    Prioridad para el nombre:
      1. `explicit_name` si se paso (lo usa el orquestador en batch para
         asignar -1, -2, -3 cuando hay duplicados de cliente).
      2. `client_name` sanitizado.
      3. "Proforma" como fallback.

    Si la carpeta ya existe, agrega sufijo "-2", "-3", etc.

    `dest_root` por default es ~/Desktop/Fotos de Proformas/.
    """
    if dest_root:
        parent = Path(dest_root)
    else:
        parent = Path.home() / "Desktop" / "Fotos de Proformas"
    parent.mkdir(parents=True, exist_ok=True)

    if explicit_name:
        sanitized = _sanitize_for_folder(explicit_name)
        base_name = sanitized if sanitized else "Proforma"
    elif client_name:
        sanitized = _sanitize_for_folder(client_name)
        base_name = sanitized if sanitized else "Proforma"
    else:
        base_name = "Proforma"

    dest = parent / base_name
    suffix = 2
    while dest.exists():
        dest = parent / f"{base_name}-{suffix}"
        suffix += 1
    dest.mkdir(parents=True, exist_ok=False)
    return dest


def _dedup_key(parsed, modo):
    """Clave de deduplicacion para los modos simples (no usado en MODE_COMPLETE,
    que tiene su propio agrupamiento por referencia).
    - MODE_GRUPAL    : 1 entrada por referencia base (PREFIJO+NUMERO)
    - MODE_INDIVIDUAL: 1 entrada por SKU completo, EXCEPTO si es surtido
                       (todos los surtidos de la misma ref se colapsan en
                       una sola entrada porque van a usar la grupal).
    """
    if modo == MODE_INDIVIDUAL and not is_surtido(parsed):
        return (
            parsed["prefix"],
            parsed["number"],
            parsed.get("variant"),
            parsed.get("color"),
        )
    return (parsed["prefix"], parsed["number"])


def _is_under_dropbox(path):
    """True si `path` esta dentro de cualquier carpeta de Dropbox sincronizada.

    Probamos contra todos los path candidatos de dropbox.CANDIDATE_PATHS para
    cubrir las distintas ubicaciones (~/Dropbox, ~/Library/CloudStorage/...).
    """
    try:
        path = Path(path).resolve()
    except OSError:
        return False
    for candidate in dropbox_mod.CANDIDATE_PATHS:
        try:
            if candidate.exists():
                cand_resolved = candidate.resolve()
                # path esta dentro si su parents incluye al candidate
                if cand_resolved == path or cand_resolved in path.parents:
                    return True
        except OSError:
            continue
    return False


def _is_cloud_only_file(path):
    """True si el archivo existe pero no tiene datos locales (es online-only
    en iCloud / Dropbox Smart Sync). Detectado por st_blocks==0 con size>0."""
    try:
        st = os.stat(path)
        return st.st_size > 0 and st.st_blocks == 0
    except OSError:
        return False


def _format_copy_error(exc, src, target):
    """Convierte una OSError de shutil.copy2 en un mensaje accionable."""
    if isinstance(exc, PermissionError):
        return ("Sin permiso para escribir en la carpeta destino. "
                "Probá elegir otra carpeta (botón Cambiar...)")
    if isinstance(exc, FileNotFoundError):
        # Esto pasa si el archivo de Dropbox "desapareció" entre que lo
        # encontramos y lo copiamos (raro pero posible si Dropbox lo movió).
        return f"El archivo origen ya no existe: {src.name}"
    if isinstance(exc, OSError) and exc.errno == 28:  # ENOSPC
        return "No hay espacio en el disco destino."
    return f"Error al copiar: {exc}"


def _copy_to_dest(src, dest, missing, item, brand_subfolder=None):
    """Copia src -> dest/[brand_subfolder]/<nombre>, evitando colisiones
    con sufijo numérico. Si falla la copia, registra en missing y devuelve
    False. Devuelve True si copió.

    Si `brand_subfolder` se especifica, las fotos van a una subcarpeta
    con ese nombre (típicamente la marca: GOSSIP, B'LINDA, etc.). Si es
    None, van al root del dest.

    GUARD: rechaza la operación si `dest` cae adentro de la carpeta de
    Dropbox. Blinda la regla de oro: NUNCA escribimos en Dropbox.

    Nota: si `src` es cloud-only (Dropbox Smart Sync / iCloud), shutil.copy2
    lo lee, lo cual fuerza la descarga. Puede ser lento.
    """
    if _is_under_dropbox(dest):
        raise RuntimeError(
            f"REGLA DE ORO ROTA: el destino {dest} esta adentro de Dropbox. "
            f"Esta app solo lee de Dropbox; nunca escribe ahi."
        )

    if brand_subfolder:
        target_dir = dest / _sanitize_for_folder(brand_subfolder)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            if item is not None:
                missing.append({
                    "sku": item["sku"], "qty": item.get("qty", 0),
                    "reason": f"No pude crear subcarpeta de marca: {e}",
                })
            return False
    else:
        target_dir = dest

    target = target_dir / src.name
    if target.exists():
        stem, ext = target.stem, target.suffix
        i = 2
        while True:
            candidate = target_dir / f"{stem}_{i}{ext}"
            if not candidate.exists():
                target = candidate
                break
            i += 1
    try:
        shutil.copy2(src, target)
        return True
    except OSError as e:
        if item is not None:
            missing.append({
                "sku": item["sku"], "qty": item.get("qty", 0),
                "reason": _format_copy_error(e, src, target),
            })
        return False


def process(pdf_path, modo, on_progress=None,
            use_grupal_when_no_individuals=False,
            dest_root=None, dest_folder_name=None,
            cancel_event=None):
    """Procesa una proforma de inicio a fin.

    Args:
        pdf_path:     str/Path al PDF
        modo:         "grupal" | "individual" | "complete"
        on_progress:  callable(actual:int, total:int, mensaje:str) - opcional
        cancel_event: threading.Event opcional. Si se setea durante el
                      proceso, el runner corta entre SKUs y retorna lo
                      copiado hasta ese punto. El dict resultante tiene
                      "cancelled": True.

    Returns:
        dict con keys:
            "dest"        : Path de la carpeta destino
            "format"      : formato del PDF detectado
            "total_skus"  : total de items en la proforma
            "copied"      : cantidad de fotos copiadas
            "missing"     : lista de dicts {"sku","qty","reason"} no encontrados
            "report_path" : Path del reporte.txt
            "cancelled"   : True si el usuario cancelo a mitad del proceso

    Raises:
        ValueError                       : modo invalido
        pdf_parser.ParseError            : PDF protegido / formato desconocido / vacio
        dropbox_mod.DropboxNotFoundError : no se encontro Dropbox
    """
    if modo not in VALID_MODES:
        raise ValueError(f"Modo invalido: {modo!r}. Validos: {VALID_MODES}")

    # year=None -> internamente buscamos en todos los años (mas reciente primero).
    # El parametro year se quito del UI; si en el futuro se quiere volver a
    # exponer, se puede pasar aca.
    year = None

    def progress(i, n, msg=""):
        if on_progress:
            on_progress(i, n, msg)

    progress(0, 1, "Leyendo PDF...")
    parsed_result = pdf_dispatch.parse(str(pdf_path))
    items = parsed_result["items"]
    fmt = parsed_result["format"]
    client = parsed_result.get("client")
    total_skus = len(items)

    progress(0, max(total_skus, 1), "Buscando carpeta de Dropbox...")
    dbx = dropbox_mod.find_dropbox_root()

    try:
        dest = _make_dest_folder(
            client_name=client,
            dest_root=dest_root,
            explicit_name=dest_folder_name,
        )
    except PermissionError:
        raise PermissionError(
            "Sin permiso para crear carpetas en:\n  "
            f"{dest_root or Path.home() / 'Desktop' / 'Fotos de Proformas'}\n\n"
            "Elegí otra carpeta destino con el botón 'Cambiar...' en la pantalla anterior."
        )
    except OSError as e:
        raise OSError(
            f"No pude crear la carpeta destino: {e}\n\n"
            "Verificá que la ubicación exista y sea escribible:\n  "
            f"{dest_root or Path.home() / 'Desktop' / 'Fotos de Proformas'}"
        )

    # GUARD: el destino NUNCA debe caer dentro de Dropbox.
    if _is_under_dropbox(dest):
        raise RuntimeError(
            f"REGLA DE ORO ROTA: la carpeta destino {dest} cae dentro de Dropbox. "
            f"Verifica que ~/Desktop no este sincronizado a Dropbox."
        )

    # Copiar el PDF de la proforma a destino con prefijo "01_" para que aparezca
    # primero en Finder, antes que el reporte ("02_") y las fotos.
    pdf_src = Path(pdf_path)
    proforma_target = dest / f"01_{pdf_src.name}"
    try:
        shutil.copy2(pdf_src, proforma_target)
    except OSError:
        # Si falla la copia de la proforma seguimos igual; no es bloqueante.
        pass

    # Validar items: separar reconocidos de marcas desconocidas
    valid_items = []   # lista de (parsed, item)
    unrecognized = []
    for item in items:
        parsed = parse_sku(item["sku"])
        if parsed is None:
            unrecognized.append(item)
        else:
            valid_items.append((parsed, item))

    missing = [
        {"sku": u["sku"], "qty": u["qty"], "reason": "Marca no reconocida",
         "brand": "(desconocida)"}
        for u in unrecognized
    ]

    # Despachar al runner especifico segun modo. La opcion
    # `use_grupal_when_no_individuals` la elige el usuario en la pantalla 2.
    if modo == MODE_COMPLETE:
        copied_count, copied_per_brand = _run_complete(
            valid_items, year, dbx, dest, progress, missing,
            use_grupal_when_no_individuals=use_grupal_when_no_individuals,
            cancel_event=cancel_event,
        )
    else:
        copied_count, copied_per_brand = _run_simple(
            valid_items, modo, year, dbx, dest, progress, missing,
            cancel_event=cancel_event,
        )

    cancelled = bool(cancel_event is not None and cancel_event.is_set())

    # SKUs por marca (de la proforma, no de copiadas) - para el reporte.
    from collections import Counter
    skus_per_brand = Counter()
    for parsed, _item in valid_items:
        skus_per_brand[parsed["brand"]] += 1
    for u in unrecognized:
        skus_per_brand["(desconocida)"] += 1

    # Escribir reporte
    report_path = _write_report(
        dest=dest,
        pdf_path=Path(pdf_path),
        modo=modo,
        fmt=fmt,
        client=client,
        total_skus=total_skus,
        copied=copied_count,
        missing=missing,
        skus_per_brand=skus_per_brand,
        copied_per_brand=copied_per_brand,
    )

    progress(1, 1, "Cancelado" if cancelled else "Listo")

    return {
        "dest": dest,
        "format": fmt,
        "client": client,
        "total_skus": total_skus,
        "copied": copied_count,
        "missing": missing,
        "report_path": report_path,
        "skus_per_brand": dict(skus_per_brand),
        "copied_per_brand": dict(copied_per_brand),
        "cancelled": cancelled,
    }


# =============================================================================
# Pre-scan: detectar refs ambiguas (marca sin individuales pero con grupal)
# =============================================================================

def _find_refs_without_individuals(valid_items, year, dbx):
    """Devuelve lista de dicts {"ref","brand"} donde:
    - La marca NO tiene archivos individuales en disco
    - PERO la grupal de la referencia SI existe

    Estos casos son ambiguos en MODE_COMPLETE: no podemos comparar "completo"
    sin saber cuantos colores existen. La UI le pregunta al usuario si usar
    la grupal en estos casos.

    No incluimos refs cuyos items son TODOS surtido (ya van auto-grupal).
    """
    seen = set()
    out = []
    for parsed, item in valid_items:
        key = (parsed["prefix"], parsed["number"])
        if key in seen:
            continue
        seen.add(key)
        # Skip si todos los items de esta ref son surtido (ya van a grupal)
        # NOTA: aqui solo vemos UN item; aun asi, si es surtido, lo skipeamos
        # de la pregunta porque la regla de surtido ya cubre el caso.
        if is_surtido(parsed):
            continue
        # ¿Hay individuales en disco?
        existing = finder.list_existing_colors_for_ref(parsed, year, dbx)
        if existing:
            continue
        # ¿Hay grupal?
        grupal = finder.find_grupal(parsed, year, dbx)
        if grupal is None:
            continue
        out.append({
            "ref": f"{parsed['prefix']}{parsed['number']}",
            "brand": parsed["brand"],
            "grupal_name": grupal.name,
        })
    return out


# =============================================================================
# Runners por modo
# =============================================================================

def _run_simple(valid_items, modo, year, dbx, dest, progress, missing,
                cancel_event=None):
    """MODE_GRUPAL y MODE_INDIVIDUAL.

    - GRUPAL: dedup por (prefix, number), buscar grupal de cada referencia.
    - INDIVIDUAL: dedup por (prefix, number, variant, color), buscar individual.

    Si cancel_event esta seteado entre SKUs, corta y retorna lo copiado.
    """
    seen = set()
    plan = []
    for parsed, item in valid_items:
        key = _dedup_key(parsed, modo)
        if key in seen:
            continue
        seen.add(key)
        plan.append((parsed, item))

    from collections import Counter
    copied = 0
    copied_per_brand = Counter()
    total = len(plan)
    for idx, (parsed, item) in enumerate(plan, 1):
        # Check cancel ANTES de cada SKU para corte rapido.
        if cancel_event is not None and cancel_event.is_set():
            return copied, copied_per_brand
        brand = parsed["brand"]
        progress(idx, total, f"{brand} · {item['sku']}")
        if modo == MODE_GRUPAL:
            src = finder.find_grupal(parsed, year, dbx)
        elif is_surtido(parsed):
            # Surtido = bulto multicolor. Va directo a la grupal sin importar
            # que el modo sea "individual".
            src = finder.find_grupal(parsed, year, dbx)
        else:
            # MODE_INDIVIDUAL para SKU normal
            src = finder.find_individual(
                parsed, year, dbx,
                raw=item.get("raw"), suspect=item.get("suspect", False),
            )
        if src is None:
            reason = "Grupal no encontrada (surtido)" if is_surtido(parsed) \
                else "Foto no encontrada"
            missing.append({
                "sku": item["sku"], "qty": item["qty"],
                "reason": reason,
                "brand": brand,
            })
            continue
        if _copy_to_dest(src, dest, missing, item, brand_subfolder=brand):
            copied += 1
            copied_per_brand[brand] += 1
    return copied, copied_per_brand


def _color_covers(existing_color, ordered_color):
    """True si el color de la proforma 'cubre' el color en disco.

    Cobertura es:
      - igual exacto, o
      - el color en disco EMPIEZA con el de la proforma y el de la proforma
        tiene >= 2 letras. Ej: proforma 'KHA', disco 'KHAKI' -> cubre.
        Pero 'B' no cubre 'BLK' (muy corto).
    """
    if existing_color == ordered_color:
        return True
    if len(ordered_color) >= 2 and existing_color.startswith(ordered_color):
        return True
    return False


def _all_existing_covered(existing, ordered):
    """True si cada (variante, color) en `existing` (lo que esta en disco)
    es cubierto por algun (variante, color) en `ordered` (lo pedido en la
    proforma). Misma variante exacta + color via _color_covers().
    """
    if not existing:
        return False
    for ev, ec in existing:
        matched = any(
            ov == ev and _color_covers(ec, oc)
            for ov, oc in ordered
        )
        if not matched:
            return False
    return True


def _run_complete(valid_items, year, dbx, dest, progress, missing,
                  use_grupal_when_no_individuals=False,
                  cancel_event=None):
    """MODE_COMPLETE: por cada referencia base, decidir grupal vs individuales.

    Regla: si la proforma pidio TODOS los colores que existen en disco para
    esa referencia, mandamos la grupal. Si no, mandamos las individuales
    de los colores pedidos.

    Tolerancia: la proforma puede usar codigos de color abreviados (ej "KHA")
    mientras que el archivo en disco tiene el nombre largo (ej "KHAKI").
    _color_covers() considera que KHA cubre KHAKI.

    Si la marca no tiene individuales en disco (ej VOX), no podemos saber
    "completa" objetivamente. La decision la tomo el usuario via dialogo
    al inicio: `use_grupal_when_no_individuals` indica que respondio.
    """
    # Agrupar items por referencia base
    by_ref = {}  # (prefix, number) -> {"parsed": parsed, "items": [(parsed, item), ...]}
    order = []   # mantener orden de aparicion
    for parsed, item in valid_items:
        key = (parsed["prefix"], parsed["number"])
        if key not in by_ref:
            by_ref[key] = {"parsed": parsed, "items": []}
            order.append(key)
        by_ref[key]["items"].append((parsed, item))

    from collections import Counter
    copied = 0
    copied_per_brand = Counter()
    total = len(order)

    for idx, key in enumerate(order, 1):
        # Check cancel ANTES de cada referencia.
        if cancel_event is not None and cancel_event.is_set():
            return copied, copied_per_brand
        group = by_ref[key]
        ref_parsed = group["parsed"]
        items_for_ref = group["items"]
        ref_label = f"{ref_parsed['prefix']}{ref_parsed['number']}"
        brand = ref_parsed["brand"]
        progress(idx, total, f"{brand} · {ref_label}")

        # ¿Algun SKU es surtido? Eso por si solo marca la ref como completa.
        has_surtido = any(is_surtido(p) for p, _ in items_for_ref)

        # Set de (variante, color) pedidos. Excluimos surtido (no es "un color").
        ordered = set(
            (p["variant"], p["color"])
            for p, _ in items_for_ref
            if p.get("variant") and p.get("color") and not is_surtido(p)
        )
        # Set de (variante, color) que existen como individuales en disco
        existing = finder.list_existing_colors_for_ref(ref_parsed, year, dbx)
        # Buscar la grupal
        grupal_src = finder.find_grupal(ref_parsed, year, dbx)

        # ¿Usamos la grupal? Casos:
        # 1. Vino con surtido -> auto, la grupal cubre todos los colores.
        # 2. La marca no tiene individuales en disco (ej VOX) y SI tiene grupal:
        #    aqui depende de la respuesta del usuario al dialogo.
        # 3. Hay individuales y los colores pedidos cubren todos los existentes
        #    (con tolerancia: KHA cubre KHAKI).
        if has_surtido:
            is_complete = True
        elif not existing and grupal_src is not None:
            is_complete = use_grupal_when_no_individuals
        else:
            is_complete = _all_existing_covered(existing, ordered)

        if is_complete and grupal_src:
            # Pediste todos los colores que tenemos: mandamos solo la grupal.
            if _copy_to_dest(grupal_src, dest, missing, None, brand_subfolder=brand):
                copied += 1
                copied_per_brand[brand] += 1
        else:
            # No esta completa o no hay grupal: mandamos las individuales pedidas.
            mandated_individuals = False  # ¿realmente mandamos algun individual?
            for parsed, item in items_for_ref:
                if is_surtido(parsed):
                    # Surtido en este branch -> grupal no encontrada y no tiene
                    # sentido buscar individual (no existe ASURT.jpg).
                    missing.append({
                        "sku": item["sku"], "qty": item["qty"],
                        "reason": "Surtido sin foto grupal disponible",
                        "brand": brand,
                    })
                    continue
                src = finder.find_individual(
                    parsed, year, dbx,
                    raw=item.get("raw"), suspect=item.get("suspect", False),
                )
                if src is None:
                    missing.append({
                        "sku": item["sku"], "qty": item["qty"],
                        "reason": "Foto individual no encontrada",
                        "brand": brand,
                    })
                    continue
                if _copy_to_dest(src, dest, missing, item, brand_subfolder=brand):
                    copied += 1
                    copied_per_brand[brand] += 1
                    mandated_individuals = True
            # Caso especial: estaba completa, no hay grupal, Y mandamos
            # individuales reales. Reportamos como info que la grupal falto.
            if is_complete and not grupal_src and mandated_individuals:
                total_pares = sum(
                    it["qty"] for p, it in items_for_ref if not is_surtido(p)
                )
                missing.append({
                    "sku": ref_label,
                    "qty": total_pares,
                    "reason": "Grupal no encontrada (se mandan individuales)",
                    "brand": brand,
                })

    return copied, copied_per_brand


# =============================================================================
# Reporte
# =============================================================================

_FMT_LABELS = {
    "pepperi":        "Pepperi (Off-line Preview)",
    "sap_factura":    "SAP Business One (Factura de Cliente)",
    "sap_pedido":     "SAP Business One (Pedido)",
    "sap_proforma":   "SAP Business One (Proforma de Cliente)",
    "sap_cotizacion": "SAP Business One (Cotización de Cliente)",
}


def _write_report(dest, pdf_path, modo, fmt, client, total_skus, copied, missing,
                   skus_per_brand=None, copied_per_brand=None):
    """Genera reporte.txt en dest. Devuelve Path."""
    now = datetime.datetime.now()
    skus_per_brand = skus_per_brand or {}
    copied_per_brand = copied_per_brand or {}

    lines = []
    lines.append("REPORTE DE PROCESAMIENTO DE PROFORMA")
    lines.append("=" * 60)
    lines.append(f"Fecha y hora      : {now.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Cliente           : {client or '(no detectado)'}")
    lines.append(f"Archivo PDF       : {pdf_path.name}")
    lines.append(f"Formato detectado : {_FMT_LABELS.get(fmt, fmt)}")
    lines.append(f"Modo de fotos     : {_format_modo_label(modo)}")
    lines.append("")
    lines.append(f"Total SKUs en la proforma : {total_skus}")
    lines.append(f"Total fotos copiadas      : {copied}")
    lines.append(f"Codigos no encontrados    : {len(missing)}")
    lines.append("")

    # Desglose por marca: SKUs ordenados vs fotos copiadas.
    if skus_per_brand:
        lines.append("DESGLOSE POR MARCA")
        lines.append("-" * 60)
        lines.append(f"{'Marca':<22}  {'SKUs':>6}  {'Copiadas':>9}")
        lines.append(f"{'-'*22}  {'-'*6}  {'-'*9}")
        # Ordenar por cantidad de SKUs descendente
        for brand in sorted(skus_per_brand.keys(),
                            key=lambda b: -skus_per_brand[b]):
            n_skus = skus_per_brand[brand]
            n_copied = copied_per_brand.get(brand, 0)
            lines.append(f"{brand:<22}  {n_skus:>6}  {n_copied:>9}")
        lines.append("")

    if missing:
        # Ordenar por cantidad descendente para que los faltantes mas importantes
        # aparezcan primero
        missing_sorted = sorted(missing, key=lambda m: -m["qty"])
        lines.append("CODIGOS NO ENCONTRADOS")
        lines.append("-" * 60)
        lines.append(f"{'Pares':>6}  {'Marca':<20}  {'Codigo':<28}  Razon")
        lines.append(f"{'-'*6}  {'-'*20}  {'-'*28}  {'-'*20}")
        for m in missing_sorted:
            brand = m.get("brand", "")
            lines.append(
                f"{m['qty']:>6}  {brand[:20]:<20}  {m['sku'][:28]:<28}  {m['reason']}"
            )
        lines.append("")
        total_pares_missing = sum(m["qty"] for m in missing)
        lines.append(f"Total pares en codigos faltantes: {total_pares_missing}")

    # "02_reporte.txt" - prefijo "02_" para sortear despues de la proforma
    # (que va con "01_") pero antes de las fotos (que empiezan con letras).
    report_path = dest / "02_reporte.txt"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


# -----------------------------------------------------------------------------
# Smoke test contra los 3 PDFs reales y Dropbox real
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Smoke test: pasá paths de PDFs como args. Si no, intenta encontrar
    # PDFs en ~/Downloads.
    paths = sys.argv[1:]
    if not paths:
        downloads = Path.home() / "Downloads"
        if downloads.is_dir():
            for p in downloads.iterdir():
                if p.suffix.lower() == ".pdf":
                    paths.append(str(p))
                    if len(paths) >= 3:
                        break

    modos_a_probar = [MODE_GRUPAL, MODE_INDIVIDUAL, MODE_COMPLETE]

    for path in paths:
        for modo in modos_a_probar:
            print(f"\n>>> {Path(path).name} | modo={modo}")
            try:
                def cb(i, n, msg):
                    if i == 0 or i == n or msg.startswith("Buscando") and i % 5 == 0:
                        print(f"   [{i}/{n}] {msg}")
                res = process(path, modo, on_progress=cb)
            except Exception as e:
                print(f"   ERROR: {type(e).__name__}: {e}")
                continue
            print(f"   destino: {res['dest']}")
            print(f"   total_skus={res['total_skus']}  copied={res['copied']}  missing={len(res['missing'])}")
            for m in res["missing"][:5]:
                print(f"     missing: {m['qty']:>4} pares  [{m['reason']}]  {m['sku']}")
            if len(res["missing"]) > 5:
                print(f"     ... ({len(res['missing'])-5} mas)")
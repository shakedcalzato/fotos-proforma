# -*- coding: utf-8 -*-
"""Auto-detecta la raiz de Dropbox sincronizado (macOS, Windows o Linux).

Probamos los paths estandar en orden y devolvemos el primero que existe.
Si nada matchea, levantamos DropboxNotFoundError con un mensaje util.

Esta carpeta es de SOLO LECTURA para esta app: nunca escribimos / movemos /
borramos nada dentro. Las copias de fotos van a ~/Desktop, no a Dropbox.
"""

from pathlib import Path

import platform_utils


# Lista de candidatos segun el SO, viene de platform_utils.
CANDIDATE_PATHS = platform_utils.dropbox_candidate_paths()


class DropboxNotFoundError(Exception):
    """No se encontro la carpeta de Dropbox sincronizada."""


def find_dropbox_root():
    """Devuelve Path a la raiz de Dropbox sincronizado.

    Tambien verifica que la raiz contenga las carpetas GRUPALES e INDIVIDUALES;
    si la carpeta existe pero esta vacia, probablemente Dropbox no terminó
    de sincronizar - le avisamos al usuario con un mensaje accionable.

    Raises:
        DropboxNotFoundError: si nada matchea o si la carpeta esta vacia.
    """
    # Paths conocidos primero
    found = None
    for p in CANDIDATE_PATHS:
        if p.is_dir():
            found = p
            break

    # Fallback macOS: cualquier subcarpeta de ~/Library/CloudStorage cuyo
    # nombre empiece con "Dropbox" (cubre Dropbox-Business y variantes).
    if found is None and platform_utils.IS_MAC:
        cs = Path.home() / "Library" / "CloudStorage"
        if cs.is_dir():
            for sub in sorted(cs.iterdir()):
                if sub.is_dir() and sub.name.startswith("Dropbox"):
                    found = sub
                    break

    if found is None:
        raise DropboxNotFoundError(
            "No encontré la carpeta de Dropbox.\n\n"
            "Pasos:\n"
            "1. Abrí la app Dropbox del menú superior (ícono nube).\n"
            "2. Esperá a que diga 'Al día' / 'Up to date'.\n"
            "3. Volvé a procesar.\n\n"
            "Buscado en:\n"
            + "\n".join(f"  • {p}" for p in CANDIDATE_PATHS)
        )

    # Verificar que tenga GRUPALES y INDIVIDUALES
    grupales = found / "GRUPALES"
    individuales = found / "INDIVIDUALES"
    if not grupales.is_dir() and not individuales.is_dir():
        raise DropboxNotFoundError(
            f"Encontré la carpeta de Dropbox en:\n  {found}\n\n"
            "Pero NO contiene las carpetas GRUPALES e INDIVIDUALES con fotos.\n\n"
            "Posibles causas:\n"
            "• Dropbox no terminó de sincronizar (esperá un rato).\n"
            "• Las carpetas se renombraron o movieron.\n"
            "• Estás conectado a otra cuenta de Dropbox."
        )

    return found


def get_status():
    """Chequeo rapido (no bloqueante) de Dropbox para el indicador del UI.

    No lanza excepcion: siempre devuelve dict con keys:
        connected:    bool — True si encontramos Dropbox + GRUPALES/INDIVIDUALES
        root:         Path|None — ruta a la raiz si connected
        brand_count:  int — cantidad de carpetas de marca encontradas (best-effort)
        error:        str — mensaje human-friendly si no connected

    Usado por el footer de pantalla 1 ("Dropbox conectado · 14 marcas")
    y por el pre-flight check al abrir la app.
    """
    try:
        root = find_dropbox_root()
    except DropboxNotFoundError as e:
        return {"connected": False, "root": None, "brand_count": 0,
                "error": str(e)}
    except Exception as e:
        return {"connected": False, "root": None, "brand_count": 0,
                "error": f"{type(e).__name__}: {e}"}

    # Contar marcas top-level en GRUPALES e INDIVIDUALES. Es rapido (un
    # par de listdir). Usamos un set para no duplicar marcas que aparecen
    # en ambas raices.
    brands = set()
    for sub in ("GRUPALES", "INDIVIDUALES"):
        d = root / sub
        if not d.is_dir():
            continue
        try:
            for year_dir in d.iterdir():
                if not year_dir.is_dir():
                    continue
                try:
                    for brand_dir in year_dir.iterdir():
                        if brand_dir.is_dir():
                            brands.add(brand_dir.name.upper())
                except OSError:
                    continue
        except OSError:
            continue

    return {
        "connected": True,
        "root": root,
        "brand_count": len(brands),
        "brands": brands,
        "error": "",
    }


# Smoke test
if __name__ == "__main__":
    try:
        root = find_dropbox_root()
        print(f"Dropbox encontrado: {root}")
        # listar carpetas top-level
        for sub in sorted(root.iterdir())[:20]:
            kind = "dir " if sub.is_dir() else "file"
            print(f"  {kind} {sub.name}")
    except DropboxNotFoundError as e:
        print(f"ERROR: {e}")
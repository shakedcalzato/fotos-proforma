# -*- coding: utf-8 -*-
"""Auto-detecta la raiz de Dropbox sincronizado en macOS.

Probamos los paths estandar en orden y devolvemos el primero que existe.
Si nada matchea, levantamos DropboxNotFoundError con un mensaje util.

Esta carpeta es de SOLO LECTURA para esta app: nunca escribimos / movemos /
borramos nada dentro. Las copias de fotos van a ~/Desktop, no a Dropbox.
"""

from pathlib import Path


CANDIDATE_PATHS = [
    Path.home() / "Dropbox",
    Path.home() / "Library/CloudStorage/Dropbox",
    Path.home() / "Library/CloudStorage/Dropbox-Personal",
]


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

    # Fallback: cualquier subcarpeta de ~/Library/CloudStorage que empiece con "Dropbox"
    if found is None:
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
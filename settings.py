# -*- coding: utf-8 -*-
"""Persistencia de preferencias del usuario entre sesiones.

Guarda en el directorio de settings de la app (segun el SO, ver
platform_utils.app_settings_dir()) las elecciones de la ultima corrida
(modo, opción de "si la referencia no tiene foto individual", carpeta
destino) para que la proxima vez que abra la app levante con los mismos
valores.

NUNCA falla: si el archivo no existe o esta corrupto, devuelve los
defaults. Si falla guardar, lo ignora silenciosamente (no es critico).
"""

import json
from pathlib import Path

import platform_utils


SETTINGS_DIR = platform_utils.app_settings_dir()
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

DEFAULTS = {
    "modo": "complete",
    "no_ind": "grupal",
    "dest_root": str(Path.home() / "Desktop" / "Fotos de Proformas"),
    # Geometria de la ventana del ultimo cierre, formato "WxH+X+Y". Vacio
    # significa "primer arranque, usar tamaño/posicion default y centrar".
    "window_geometry": "",
    # Si el usuario apreto "Omitir esta version" en el banner de update,
    # guardamos el tag aca para no volver a mostrar el banner hasta que
    # salga una version MAS nueva. Vacio = nunca omitio nada.
    "skipped_version": "",
}


def load():
    """Carga settings desde disco. Devuelve dict con todas las keys de DEFAULTS
    (las que falten se completan con default). Nunca lanza excepción."""
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {**DEFAULTS, **data}
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return dict(DEFAULTS)


def save(settings):
    """Guarda dict de settings a disco. Si falla, no hace nada (best-effort)."""
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        # Solo guardamos las keys conocidas
        clean = {k: settings.get(k, v) for k, v in DEFAULTS.items()}
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


if __name__ == "__main__":
    print("path:", SETTINGS_FILE)
    print("loaded:", load())

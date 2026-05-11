# -*- coding: utf-8 -*-
"""Helpers cross-platform (macOS / Windows / Linux).

Centraliza todo lo que requiere lógica distinta por SO para que el resto del
código pueda ignorar la diferencia. Si en el futuro hay que agregar soporte
para un SO nuevo o ajustar una ruta, todo se modifica acá.

API pública:
    IS_MAC, IS_WINDOWS, IS_LINUX
    open_in_explorer(path)         - abre carpeta/archivo en Finder/Explorer
    app_log_path()                  - path al archivo de log de la app
    app_settings_dir()              - directorio para settings persistentes
    show_notification(title, body)  - notificación nativa al usuario
    dropbox_candidate_paths()       - paths donde puede estar Dropbox
"""

import os
import sys
import subprocess
from pathlib import Path


IS_MAC     = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
IS_LINUX   = sys.platform.startswith("linux")


# -----------------------------------------------------------------------------
# Abrir carpeta / archivo en el explorador del SO
# -----------------------------------------------------------------------------

def open_in_explorer(path):
    """Abre una carpeta o archivo en el explorador nativo del SO.
    - macOS: Finder (comando `open`)
    - Windows: Explorer (`os.startfile`)
    - Linux: lo que tenga por default (`xdg-open`)

    Si falla, retorna False sin lanzar. Si tuvo éxito (al menos lanzó el
    comando), retorna True.
    """
    path_str = str(path)
    try:
        if IS_MAC:
            subprocess.run(["open", path_str], check=False)
            return True
        if IS_WINDOWS:
            # os.startfile es la forma idiomática en Windows - no requiere shell
            os.startfile(path_str)  # type: ignore[attr-defined]
            return True
        # Linux y otros Unix
        subprocess.run(["xdg-open", path_str], check=False)
        return True
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Paths estándar por SO
# -----------------------------------------------------------------------------

_APP_NAME = "FotosProforma"


def app_log_path():
    """Path al archivo de log de la app, según convención del SO.
    Asegura que el directorio padre exista.

    - macOS:   ~/Library/Logs/FotosProforma.log
    - Windows: %LOCALAPPDATA%/FotosProforma/Logs/app.log
    - Linux:   ~/.local/state/FotosProforma/log/app.log (XDG state dir)
    """
    if IS_MAC:
        log_dir = Path.home() / "Library" / "Logs"
        log_file = _APP_NAME + ".log"
    elif IS_WINDOWS:
        local_appdata = os.environ.get("LOCALAPPDATA")
        base = Path(local_appdata) if local_appdata else (Path.home() / "AppData" / "Local")
        log_dir = base / _APP_NAME / "Logs"
        log_file = "app.log"
    else:
        xdg_state = os.environ.get("XDG_STATE_HOME")
        base = Path(xdg_state) if xdg_state else (Path.home() / ".local" / "state")
        log_dir = base / _APP_NAME / "log"
        log_file = "app.log"

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return log_dir / log_file


def app_settings_dir():
    """Directorio para guardar settings persistentes de la app.
    Asegura que exista.

    - macOS:   ~/Library/Application Support/FotosProforma/
    - Windows: %APPDATA%/FotosProforma/
    - Linux:   ~/.config/FotosProforma/ (XDG config dir)
    """
    if IS_MAC:
        d = Path.home() / "Library" / "Application Support" / _APP_NAME
    elif IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
        d = base / _APP_NAME
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else (Path.home() / ".config")
        d = base / _APP_NAME

    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


# -----------------------------------------------------------------------------
# Notificacion nativa
# -----------------------------------------------------------------------------

def show_notification(title, body):
    """Muestra una notificación nativa del SO. Falla silenciosamente si no
    está soportada en el SO actual.

    - macOS: osascript (`display notification`)
    - Windows: PowerShell + Windows.UI.Notifications (Win10+)
    - Linux: notify-send
    """
    try:
        if IS_MAC:
            def _esc(s):
                return str(s).replace("\\", "\\\\").replace('"', '\\"')
            script = (
                f'display notification "{_esc(body)}" '
                f'with title "{_esc(title)}" '
                f'sound name "Glass"'
            )
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        if IS_WINDOWS:
            # Escapamos comillas simples para no romper el PowerShell.
            t = str(title).replace("'", "''")
            b = str(body).replace("'", "''")
            # Toast via Windows.UI.Notifications (built-in en Win10+).
            # Si falla la creación del toast (Windows muy viejo), no pasa nada.
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager,"
                "Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null;"
                "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument;"
                f"$xml.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{t}</text><text>{b}</text></binding></visual></toast>');"
                "$t = New-Object Windows.UI.Notifications.ToastNotification($xml);"
                "[Windows.UI.Notifications.ToastNotificationManager]"
                "::CreateToastNotifier('Fotos Proforma').Show($t);"
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        # Linux
        subprocess.Popen(
            ["notify-send", str(title), str(body)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        # Notificacion es nice-to-have - nunca debe romper el flujo.
        pass


# -----------------------------------------------------------------------------
# Candidatos para detectar Dropbox sincronizado
# -----------------------------------------------------------------------------

def dropbox_candidate_paths():
    """Devuelve lista de Paths donde buscar la raíz del Dropbox sincronizado,
    en orden de preferencia. Diferentes SOs y modos de instalación de Dropbox
    usan ubicaciones distintas."""
    paths = []
    if IS_MAC:
        paths += [
            Path.home() / "Dropbox",
            Path.home() / "Library" / "CloudStorage" / "Dropbox",
            Path.home() / "Library" / "CloudStorage" / "Dropbox-Personal",
        ]
    elif IS_WINDOWS:
        paths += [
            Path.home() / "Dropbox",
            Path.home() / "Dropbox (Personal)",
            Path.home() / "Dropbox (Business)",
        ]
        # Algunas instalaciones de Dropbox quedan bajo OneDrive sincronizado
        # o en otras ubicaciones - el usuario ajusta si hace falta.
    else:
        paths += [Path.home() / "Dropbox"]
    return paths


# -----------------------------------------------------------------------------
# Smoke test
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"sys.platform = {sys.platform}")
    print(f"IS_MAC={IS_MAC}  IS_WINDOWS={IS_WINDOWS}  IS_LINUX={IS_LINUX}")
    print(f"app_log_path     = {app_log_path()}")
    print(f"app_settings_dir = {app_settings_dir()}")
    print(f"dropbox candidates:")
    for p in dropbox_candidate_paths():
        marker = "✓" if p.is_dir() else " "
        print(f"  {marker} {p}")

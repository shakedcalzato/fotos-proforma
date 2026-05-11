# -*- coding: utf-8 -*-
"""Entry point - lanza la app de escritorio.

Instala un crash handler global ANTES de levantar la UI para que si algo
inesperado pasa, quede logueado a ~/Library/Logs/FotosProforma.log
en lugar de cerrarse silenciosamente.
"""

import sys
import threading
import datetime
import traceback
from pathlib import Path


LOG_PATH = Path.home() / "Library" / "Logs" / "FotosProforma.log"


def _log_exception(prefix, exc_type, exc_value, exc_tb):
    """Loguea una excepcion al archivo de log con timestamp."""
    tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.datetime.now()} {prefix} ===\n")
            f.write(tb_str)
    except OSError:
        pass


def _install_crash_handler():
    """Atrapar excepciones no manejadas (main thread + worker threads).
    El usuario ve un dialog claro y queda registro en el log."""

    def _show_user_dialog(exc_value):
        # Importacion tardia para no requerir tk si no esta disponible.
        try:
            import tkinter.messagebox as messagebox
            messagebox.showerror(
                "Error inesperado",
                f"Algo salió mal:\n\n{type(exc_value).__name__}: {exc_value}\n\n"
                f"El detalle quedó en:\n{LOG_PATH}\n\n"
                f"Si esto se repite, mostrame el archivo y lo arreglo.",
            )
        except Exception:
            pass

    def _hook(exc_type, exc_value, exc_tb):
        _log_exception("UNCAUGHT", exc_type, exc_value, exc_tb)
        _show_user_dialog(exc_value)

    sys.excepthook = _hook

    if hasattr(threading, "excepthook"):
        def _thread_hook(args):
            _log_exception("THREAD", args.exc_type, args.exc_value, args.exc_traceback)
            # No mostramos dialog desde thread - ya se manejan errores en
            # processor via callbacks. Solo logueamos por las dudas.
        threading.excepthook = _thread_hook


def main():
    _install_crash_handler()
    from app import App
    app = App()
    app.run()


if __name__ == "__main__":
    main()
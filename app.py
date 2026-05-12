# -*- coding: utf-8 -*-
"""UI de escritorio (tkinter clásico) - 3 pantallas, look minimalista.

Paleta inspirada en Apple/Linear: superficie blanca sobre gris muy claro,
texto en grises bien contrastados, acento azul macOS, mucho whitespace.

Notas técnicas:
- Solo tk.* (NO ttk).
- Los botones usan Canvas (no tk.Button) porque en macOS Aqua el theme
  nativo ignora `bg` y se ven grises. Con Canvas dibujamos rectángulo
  redondeado de cualquier color que queramos.
- Los radio buttons son OptionCards (Frames clickables con indicador
  circular dibujado en Canvas).
- Para el selector de año usamos un SegmentedControl horizontal.
- No se customiza el cursor (queda el del sistema).
"""

import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox
from pathlib import Path

import pdf_parser
import pdf_dispatch
from brands import parse_sku
import processor
import dropbox as dropbox_mod
import settings as user_settings
import platform_utils
import updater

# Drag-and-drop dentro de la ventana (no al icono de la app, eso es otro
# mecanismo de macOS y ya esta soportado). Si tkinterdnd2 no esta instalado
# o no carga (raro pero puede pasar en algun build viejo) seguimos andando
# con el boton clasico: la drop zone queda visible pero solo clickeable.
_DND_IMPORT_ERROR = None
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
except Exception as _e:
    TkinterDnD = None
    DND_FILES = None
    DND_AVAILABLE = False
    _DND_IMPORT_ERROR = repr(_e)

# PIL para renderizar thumbnails de fotos en pantalla 2. Si no carga
# (raro), solo perdemos la vista previa — el resto de la app sigue OK.
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except Exception:
    Image = None
    ImageTk = None
    PIL_AVAILABLE = False


def _log_event(msg):
    """Escribe una linea al log de la app — diagnostico para drag-and-drop
    y otros eventos no-criticos. Falla en silencio si no se puede escribir
    (no queremos que el logueo crashee la app)."""
    try:
        import datetime
        log_path = platform_utils.app_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except Exception:
        pass


def _friendly_error(exc_or_str):
    """Convierte una excepcion/string en (title, body) human-friendly para
    mostrar en messagebox.showerror. Mapea errores tecnicos a explicaciones
    accionables. Si no reconocemos el tipo, igual mostramos algo legible.

    Returns:
        (title:str, body:str)
    """
    # Si nos pasaron un string crudo (ej. parsed_data_list[i]["error"]),
    # lo usamos como body — los strings de pdf_parser.ParseError ya estan
    # redactados humanos.
    if isinstance(exc_or_str, str):
        return ("No pude leer los PDFs", exc_or_str)

    name = type(exc_or_str).__name__
    msg = str(exc_or_str) or "(sin detalle)"

    # Errores que ya tienen mensaje humano: los pasamos tal cual.
    if name in ("ParseError", "DropboxNotFoundError"):
        return ("No pude continuar", msg)

    # Filesystem / permisos.
    if isinstance(exc_or_str, PermissionError):
        return (
            "Sin permiso para escribir",
            "macOS no me deja escribir en esa carpeta. Probá:\n\n"
            "• Cambiar la carpeta destino (boton 'Cambiar...' en la pantalla anterior).\n"
            "• Verificar permisos del Escritorio en Ajustes del Sistema → "
            "Privacidad y Seguridad → Archivos y Carpetas.\n\n"
            f"Detalle: {msg}",
        )
    if isinstance(exc_or_str, FileNotFoundError):
        return (
            "Archivo no encontrado",
            f"No encontre uno de los archivos necesarios. {msg}\n\n"
            "Verifica que el PDF y las fotos esten en sus lugares.",
        )
    if isinstance(exc_or_str, OSError):
        return (
            "Error de disco",
            f"Algo salio mal escribiendo al disco:\n\n{msg}\n\n"
            "Probá liberar espacio o elegir otra carpeta destino.",
        )

    # Default: genérico pero legible.
    return (
        "Error inesperado",
        f"{name}: {msg}\n\n"
        "Si vuelve a pasar, mostrame el log de la app:\n"
        f"  {platform_utils.app_log_path()}",
    )


def _split_status_detail(msg):
    """Separa un mensaje de progreso en (status, detail) para mostrar en
    dos labels en pantalla 3 procesando.

    El processor emite cosas como:
    - "Leyendo PDF..."                      -> status="Leyendo PDF...", detail=""
    - "[1/3] file.pdf · GOSSIP · GP0012/A/BLK"
        -> status="[1/3] file.pdf", detail="GOSSIP · GP0012/A/BLK"
    - "Cancelado", "Listo"                  -> status=msg, detail=""
    """
    if not msg or "·" not in msg:
        return msg, ""
    parts = [p.strip() for p in msg.split("·")]
    # Si tenemos 2+ partes, las ultimas 2 son brand · sku (el detalle).
    if len(parts) >= 2:
        detail = " · ".join(parts[-2:])
        status = " · ".join(parts[:-2]) if len(parts) > 2 else ""
        return status, detail
    return msg, ""


# =============================================================================
# Paleta (light + dark)
# =============================================================================
#
# Las constantes BG, SURFACE, TEXT, etc. se asignan dinamicamente al cargar
# el modulo segun el modo del sistema (light/dark). Para detectar el modo
# se consulta el SO una vez al inicio — los widgets se crean despues, asi
# que ven los valores correctos. Si el usuario cambia el modo del sistema
# mientras la app esta abierta, no se refleja hasta reiniciar.

_PALETTE_LIGHT = {
    "BG":              "#F5F5F7",
    "SURFACE":         "#FFFFFF",
    "TEXT":            "#1D1D1F",
    "TEXT_MUTED":      "#6E6E73",
    "TEXT_LIGHT":      "#86868B",
    "ACCENT":          "#0066CC",
    "ACCENT_HOVER":    "#0058B5",
    "ACCENT_TINT":     "#E8F1FC",
    "BORDER":          "#D2D2D7",
    "BORDER_SUBTLE":   "#E5E5EA",
    "BORDER_STRONG":   "#A8A8AC",
    "SHADOW":          "#E0E0E5",
    "DISABLED_BG":     "#E5E5EA",
    "DISABLED_FG":     "#A8A8AC",
    "SUCCESS":         "#30A46C",
    "ERROR":           "#E5484D",
    "TOAST_BG":        "#1D1D1F",
    "TOAST_FG":        "#FFFFFF",
    "HOVER_BG":        "#EDEDED",   # hover sutil para botones secondary
}

_PALETTE_DARK = {
    "BG":              "#1D1D1F",   # fondo de la ventana (mas oscuro que SURFACE)
    "SURFACE":         "#2C2C2E",   # cards / superficies
    "TEXT":            "#F2F2F7",   # texto principal
    "TEXT_MUTED":      "#AEAEB2",   # texto secundario
    "TEXT_LIGHT":      "#8E8E93",   # texto terciario / labels de seccion
    "ACCENT":          "#0A84FF",   # azul mas vibrante para dark
    "ACCENT_HOVER":    "#409CFF",
    "ACCENT_TINT":     "#1C3A5E",   # tint azul oscuro
    "BORDER":          "#48484A",
    "BORDER_SUBTLE":   "#38383A",
    "BORDER_STRONG":   "#5C5C5E",
    "SHADOW":          "#000000",   # sombra negra contrasta sobre BG oscuro
    "DISABLED_BG":     "#3A3A3C",
    "DISABLED_FG":     "#6D6D70",
    "SUCCESS":         "#30D158",
    "ERROR":           "#FF453A",
    "TOAST_BG":        "#F2F2F7",   # invertido: toast claro en dark mode
    "TOAST_FG":        "#1D1D1F",
    "HOVER_BG":        "#3A3A3C",
}


def _detect_dark_mode():
    """True si el SO esta configurado en modo oscuro.

    - macOS: lee `defaults read -g AppleInterfaceStyle` (devuelve "Dark"
      cuando esta en dark mode; en light no existe la key).
    - Windows: lee la registry key AppsUseLightTheme (0 = dark).
    - Linux y otros: por ahora siempre light (es lo mas comun cuando no
      hay forma estandar de detectarlo).

    Falla silenciosa: en caso de error o ambigüedad, devuelve False
    (light) — es el default mas seguro visualmente.
    """
    import subprocess
    try:
        if platform_utils.IS_MAC:
            r = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=2,
            )
            return r.returncode == 0 and r.stdout.strip().lower() == "dark"
        if platform_utils.IS_WINDOWS:
            r = subprocess.run(
                ["reg", "query",
                 r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                 "/v", "AppsUseLightTheme"],
                capture_output=True, text=True, timeout=2,
            )
            # La key vale 0x0 cuando esta en dark, 0x1 en light.
            return r.returncode == 0 and "0x0" in r.stdout
    except Exception:
        pass
    return False


def _apply_palette_globals(dark):
    """Re-asigna todas las constantes globales de paleta segun modo. Llamado
    al cargar el modulo (con el modo detectado) y cuando el SO cambia el
    modo en runtime (App._check_dark_mode_change)."""
    global _PALETTE, DARK_MODE
    global BG, SURFACE, TEXT, TEXT_MUTED, TEXT_LIGHT
    global ACCENT, ACCENT_HOVER, ACCENT_TINT
    global BORDER, BORDER_SUBTLE, BORDER_STRONG, SHADOW
    global DISABLED_BG, DISABLED_FG, SUCCESS, ERROR
    global TOAST_BG, TOAST_FG, HOVER_BG
    _PALETTE = _PALETTE_DARK if dark else _PALETTE_LIGHT
    DARK_MODE = dark
    BG              = _PALETTE["BG"]
    SURFACE         = _PALETTE["SURFACE"]
    TEXT            = _PALETTE["TEXT"]
    TEXT_MUTED      = _PALETTE["TEXT_MUTED"]
    TEXT_LIGHT      = _PALETTE["TEXT_LIGHT"]
    ACCENT          = _PALETTE["ACCENT"]
    ACCENT_HOVER    = _PALETTE["ACCENT_HOVER"]
    ACCENT_TINT     = _PALETTE["ACCENT_TINT"]
    BORDER          = _PALETTE["BORDER"]
    BORDER_SUBTLE   = _PALETTE["BORDER_SUBTLE"]
    BORDER_STRONG   = _PALETTE["BORDER_STRONG"]
    SHADOW          = _PALETTE["SHADOW"]
    DISABLED_BG     = _PALETTE["DISABLED_BG"]
    DISABLED_FG     = _PALETTE["DISABLED_FG"]
    SUCCESS         = _PALETTE["SUCCESS"]
    ERROR           = _PALETTE["ERROR"]
    TOAST_BG        = _PALETTE["TOAST_BG"]
    TOAST_FG        = _PALETTE["TOAST_FG"]
    HOVER_BG        = _PALETTE["HOVER_BG"]


# Forward declarations: las constantes se asignan abajo en
# _apply_palette_globals(). Las dejamos aca con valor del modo detectado
# para que cualquier import temprano vea valores validos.
_PALETTE = _PALETTE_LIGHT
DARK_MODE = False
BG = SURFACE = TEXT = TEXT_MUTED = TEXT_LIGHT = ""
ACCENT = ACCENT_HOVER = ACCENT_TINT = ""
BORDER = BORDER_SUBTLE = BORDER_STRONG = SHADOW = ""
DISABLED_BG = DISABLED_FG = SUCCESS = ERROR = ""
TOAST_BG = TOAST_FG = HOVER_BG = ""
_apply_palette_globals(_detect_dark_mode())


# =============================================================================
# Tipografia (SF Pro Display, fallback Helvetica Neue / Arial)
# =============================================================================

FONT_FAMILY = "SF Pro Display"

def F(size, weight="normal"):
    return (FONT_FAMILY, size, weight) if weight != "normal" else (FONT_FAMILY, size)

FONT_DISPLAY        = F(28, "bold")     # H1
FONT_TITLE          = F(20, "bold")
FONT_SUBTITLE       = F(14)             # subtítulo gris
FONT_BODY           = F(14)
FONT_BODY_BOLD      = F(14, "bold")
FONT_OPTION_TITLE   = F(15, "bold")     # titulo de option card
FONT_OPTION_SUB     = F(13)             # subtitulo de option card
FONT_SECTION_LABEL  = F(11, "bold")     # MODO DE FOTOS, AÑO
FONT_BUTTON         = F(14, "bold")
FONT_CAPTION        = F(12)
FONT_MONO           = ("Menlo", 12)


# =============================================================================
# Geometria
# =============================================================================

# Tamaño inicial al abrir la app. El usuario puede agarrar las esquinas y
# cambiar tamaño libremente (la ventana es redimensionable). Estos valores
# son SOLO el default que se aplica al primer arranque.
WINDOW_W = 800
WINDOW_H = 650

# Tamaño minimo para que el UI no se rompa. Por debajo de esto los textos
# largos se solapan, los botones quedan apretados, etc.
WINDOW_W_MIN = 600
WINDOW_H_MIN = 500

APP_VERSION = "1.0"

SCREEN_PADX = 40
SECTION_GAP = 18   # antes 28 - ganamos 30-40px verticales
ELEMENT_GAP = 12   # antes 16


# =============================================================================
# Datos: opciones de modo y año
# =============================================================================

MODOS = [
    ("grupal", "Solo grupales",
     "Una grupal por referencia. No importa los colores pedidos.",
     processor.MODE_GRUPAL),
    ("individual", "Solo individuales",
     "Una individual por cada color pedido. Una foto por SKU.",
     processor.MODE_INDIVIDUAL),
    ("complete", "Grupal si está completa",
     "Grupal si los colores de la referencia están completos. Individuales si faltan algunos.",
     processor.MODE_COMPLETE),
]

# Que hacer cuando una marca NO tiene fotos individuales en disco
# (ej VOX, donde solo hay grupales). Solo aplica al modo "Grupal si está completa".
NO_IND_OPCIONES = [
    ("missing", "Marcar faltante"),
    ("grupal",  "Usar la grupal"),
]

# Carpeta destino default (la carpeta padre donde se crean las subcarpetas
# por proforma). El usuario puede cambiarla en pantalla 2.
DEFAULT_DEST_ROOT = Path.home() / "Desktop" / "Fotos de Proformas"


# =============================================================================
# CanvasButton - botón con esquinas redondeadas dibujado a mano
# =============================================================================

def _measure_text_width(text, font):
    """Mide el ancho en pixeles de un texto en una fuente dada.
    Necesita que exista una root window (tkinter.font requiere root)."""
    family = font[0]
    size = font[1]
    weight = font[2] if len(font) >= 3 else "normal"
    f = tkfont.Font(family=family, size=size, weight=weight)
    return f.measure(text)


def _round_rect_pts(x1, y1, x2, y2, r):
    """Coordenadas para un poligono que aproxima rectangulo redondeado.
    Para usarlo con create_polygon(..., smooth=True)."""
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def _bind_dynamic_wraplength(label, margin=0, min_wrap=200):
    """Hace que el wraplength del label siga su ancho real cuando la ventana
    se redimensiona. Es la pieza que permite que textos largos se reflowen
    cuando el usuario agranda la ventana.

    El label tiene que estar empaquetado con fill='x' (sino su ancho es el
    del texto, no del parent, y el wrap no tiene sentido).

    margin: pixeles a restar del ancho — util si el label tiene padding interno.
    min_wrap: minimo wraplength para evitar valores absurdos durante layout.
    """
    def _on_configure(event):
        new_wrap = max(min_wrap, event.width - margin)
        # Evitar loops: solo aplicar si cambia significativamente.
        try:
            current = int(label.cget("wraplength"))
        except (ValueError, TypeError):
            current = 0
        if abs(new_wrap - current) > 4:
            label.configure(wraplength=new_wrap)
    label.bind("<Configure>", _on_configure)


class CanvasButton(tk.Canvas):
    """Botón custom dibujado en Canvas. Soporta hover, disabled y bordes
    redondeados. Tres variantes: primary (azul), secondary (gris claro),
    text (sin fondo, texto azul)."""

    @staticmethod
    def kinds():
        """Devuelve los kinds del boton evaluados con la paleta ACTUAL.
        No usamos class attribute porque la paleta cambia en runtime
        cuando el SO pasa de light a dark — un class attr capturaria
        los valores viejos."""
        return {
            "primary":   {"bg": ACCENT,        "fg": "#FFFFFF", "hover_bg": ACCENT_HOVER},
            "secondary": {"bg": SURFACE,       "fg": TEXT,      "hover_bg": HOVER_BG,
                          "border": BORDER},
            "text":      {"bg": None,          "fg": ACCENT,    "hover_bg": ACCENT_TINT},
        }

    def __init__(self, parent, text, command, kind="primary",
                 height=44, padx=24, font=FONT_BUTTON, parent_bg=None):
        # Medir ancho del texto via Font.measure() (no crea widget temporal,
        # que causaba TclError 'invalid command name' cuando lo destruiamos
        # antes de inicializar el Canvas).
        text_w = _measure_text_width(text, font)
        width = text_w + 2 * padx

        if parent_bg is None:
            try:
                parent_bg = parent.cget("bg")
            except tk.TclError:
                parent_bg = BG

        super().__init__(
            parent, width=width, height=height,
            bg=parent_bg, highlightthickness=0, bd=0,
        )
        self.cfg = self.kinds()[kind].copy()
        self.kind = kind
        self.command = command
        self._text = text
        self._enabled = True
        self._hover = False
        # Ojo: _w es atributo INTERNO de tkinter (path tcl). Usamos _width/_height
        # para nuestros propios valores.
        self._width = width
        self._height = height
        self._radius = 10
        self._font = font
        self._parent_bg = parent_bg

        self._draw()
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))

    def _set_hover(self, hovering):
        if self._enabled and self._hover != hovering:
            self._hover = hovering
            self._draw()

    def _on_click(self, event):
        if self._enabled and self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        cfg = self.cfg
        if not self._enabled:
            bg_color = DISABLED_BG
            fg_color = DISABLED_FG
            border = None
        else:
            bg_color = cfg["hover_bg"] if self._hover else cfg["bg"]
            fg_color = cfg["fg"]
            border = cfg.get("border")

        # Si bg es None es boton tipo "text" sin fondo
        if bg_color is not None:
            pts = _round_rect_pts(1, 1, self._width - 1, self._height - 1, self._radius)
            kwargs = {"smooth": True, "fill": bg_color}
            if border:
                kwargs["outline"] = border
                kwargs["width"] = 1
            else:
                kwargs["outline"] = ""
            self.create_polygon(pts, **kwargs)

        self.create_text(
            self._width / 2, self._height / 2,
            text=self._text, fill=fg_color, font=self._font,
        )

    def set_enabled(self, enabled):
        if self._enabled != enabled:
            self._enabled = enabled
            self._draw()


# =============================================================================
# DropZone - bandeja con borde punteado para arrastrar archivos
# =============================================================================

class DropZone(tk.Canvas):
    """Bandeja visual con borde punteado para indicar donde arrastrar PDFs.

    IMPORTANTE: la registracion del drop target NO se hace aca — se hace
    en el ROOT window de App (ver App._wire_root_dnd). En macOS los Canvas
    widgets a veces no reciben <<Drop>> events de forma confiable; el root
    es mucho mas robusto. Esta clase solo se encarga del dibujo y de
    forwardear clicks al filedialog. App le avisa cuando hay drag-over
    para que cambie de color con set_dragover().

    Estados visuales:
    - Idle:        borde gris, texto neutro.
    - Drag-over:   borde y texto azul ACCENT, fondo tintado.
    """

    def __init__(self, parent, on_click, width=None, height=130,
                 parent_bg=BG):
        # Si no nos pasan width, ocupamos lo que el parent nos de.
        kwargs = {"height": height, "bg": parent_bg,
                  "highlightthickness": 0, "bd": 0}
        if width is not None:
            kwargs["width"] = width
        super().__init__(parent, **kwargs)

        self.on_click = on_click
        self._height = height
        self._hover = False
        self._parent_bg = parent_bg

        # Redibujamos cuando cambia el tamaño (importante: el width real
        # del Canvas lo conocemos recien despues del primer layout).
        self.bind("<Configure>", lambda e: self._draw())
        # Click en cualquier parte de la zona = abrir filedialog.
        self.bind("<Button-1>", self._on_click)
        # NO customizamos el cursor: regla del proyecto (ver docstring del
        # modulo arriba — el cursor queda el del sistema en todos lados).

    def set_dragover(self, value):
        """Llamado desde App cuando un archivo entra/sale de la ventana.
        Cambia el color de la bandeja para dar feedback visual."""
        if self._hover != bool(value):
            self._hover = bool(value)
            self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        if w <= 1:  # aun no layouteado
            return
        h = self._height

        if self._hover:
            border_color = ACCENT
            fill_color = ACCENT_TINT
            text_color = ACCENT
            title = "Soltá el PDF para cargarlo"
            sub = ""
        else:
            border_color = BORDER_STRONG
            fill_color = SURFACE
            text_color = TEXT_MUTED
            title = "Arrastrá tu proforma PDF aquí"
            sub = ("o hacé click para elegirla"
                   if DND_AVAILABLE
                   else "Hacé click para elegir un archivo")

        # Rectangulo con borde punteado. tk Canvas no soporta dashed en
        # create_rectangle con fill simultaneamente en todos los backends,
        # asi que dibujamos:
        # 1) un rectangulo plano para el fondo (sin outline)
        # 2) 4 lineas dashed encima para el "borde"
        pad = 6
        self.create_rectangle(
            pad, pad, w - pad, h - pad,
            outline="", fill=fill_color,
        )
        dash = (5, 4)
        line_kwargs = {"fill": border_color, "width": 2, "dash": dash}
        # Top
        self.create_line(pad, pad, w - pad, pad, **line_kwargs)
        # Bottom
        self.create_line(pad, h - pad, w - pad, h - pad, **line_kwargs)
        # Left
        self.create_line(pad, pad, pad, h - pad, **line_kwargs)
        # Right
        self.create_line(w - pad, pad, w - pad, h - pad, **line_kwargs)

        # Texto centrado
        cx = w / 2
        if sub:
            self.create_text(
                cx, h / 2 - 10, text=title,
                fill=text_color, font=FONT_BODY_BOLD,
            )
            self.create_text(
                cx, h / 2 + 14, text=sub,
                fill=TEXT_LIGHT, font=FONT_CAPTION,
            )
        else:
            self.create_text(
                cx, h / 2, text=title,
                fill=text_color, font=FONT_BODY_BOLD,
            )

    def _on_click(self, _event=None):
        if self.on_click:
            self.on_click()

    @staticmethod
    def _parse_dnd_paths(data):
        """tkinterdnd2 entrega los paths en un solo string tipo TCL list:
        - Sin espacios:        'C:/x/y.pdf C:/a/b.pdf'
        - Con espacios:        '{C:/My Stuff/x.pdf} {/Users/x/y z.pdf}'
        - Mezcla:              'simple.pdf {/path con espacios.pdf}'
        - Linux KDE/GNOME:     'file:///path/to/x.pdf'
        Parseamos los formatos y filtramos solo .pdf.
        """
        if not data:
            return []
        import re
        from urllib.parse import unquote
        paths = []
        # Captura {grupos entre llaves} o tokens sin espacios.
        for m in re.finditer(r"\{([^}]*)\}|(\S+)", data):
            p = m.group(1) if m.group(1) is not None else m.group(2)
            if not p:
                continue
            # Algunos entornos mandan file:// URIs en lugar de paths planos.
            if p.startswith("file://"):
                p = unquote(p[len("file://"):])
            paths.append(p)
        return [p for p in paths if p.lower().endswith(".pdf")]


# =============================================================================
# Card - frame blanco con borde sutil, sombra liviana abajo y hover
# =============================================================================

class Card(tk.Frame):
    """Frame blanco contenedor con tres mejoras visuales sobre tk.Frame:

    1. Borde sutil (BORDER_SUBTLE) — mas suave que el BORDER fuerte; en hover
       se intensifica a BORDER, dando sensación de "se puede interactuar".
    2. Sombra de 2px abajo (Frame extra en gris SHADOW) — en tk classic no
       hay drop-shadow nativo, esto la emula barato pero efectivo.
    3. Hover que oscurece el borde — micro-interaccion que da feedback de
       "el cursor esta encima".

    Tecnica: la clase publica ES la superficie blanca (los hijos se ponen
    adentro como antes: tk.Frame(card, bg=SURFACE)). Internamente se crea
    un wrapper Frame que contiene la superficie arriba y la sombra abajo.
    Las llamadas a pack/grid/place/pack_forget se redirigen al wrapper
    para que la sombra se mueva con la card.
    """

    def __init__(self, parent, **kwargs):
        # bg del wrapper = bg del parent para que no se vea el wrapper.
        try:
            parent_bg = parent.cget("bg")
        except tk.TclError:
            parent_bg = BG
        self._wrapper = tk.Frame(parent, bg=parent_bg)

        super().__init__(
            self._wrapper, bg=SURFACE,
            highlightbackground=BORDER_SUBTLE, highlightcolor=BORDER_SUBTLE,
            highlightthickness=1, **kwargs,
        )
        # Sombra: 2px abajo, mismo ancho que la card.
        self._shadow = tk.Frame(self._wrapper, bg=SHADOW, height=2)

        # Card arriba con expand=True (asi acepta fill="both" del wrapper
        # y crece tambien verticalmente cuando hace falta).
        tk.Frame.pack(self, in_=self._wrapper, fill="both", expand=True, side="top")
        self._shadow.pack(in_=self._wrapper, fill="x", side="bottom")

        # Hover effect: el borde se intensifica cuando el cursor entra.
        # add='+' para no pisar binds que el usuario agregue despues.
        self.bind("<Enter>", self._on_card_hover_in, add="+")
        self.bind("<Leave>", self._on_card_hover_out, add="+")

    def _on_card_hover_in(self, _event=None):
        try:
            self.configure(
                highlightbackground=BORDER,
                highlightcolor=BORDER,
            )
        except tk.TclError:
            pass

    def _on_card_hover_out(self, _event=None):
        try:
            self.configure(
                highlightbackground=BORDER_SUBTLE,
                highlightcolor=BORDER_SUBTLE,
            )
        except tk.TclError:
            pass

    # --- redirigir geometry managers al wrapper ----------------------------
    # Asi cuando codigo llama card.pack(fill='x'), packea el wrapper completo
    # (card + sombra) en vez de solo la card sin sombra.

    def pack(self, **kwargs):
        return self._wrapper.pack(**kwargs)

    def pack_forget(self):
        return self._wrapper.pack_forget()

    def grid(self, **kwargs):
        return self._wrapper.grid(**kwargs)

    def grid_forget(self):
        return self._wrapper.grid_forget()

    def place(self, **kwargs):
        return self._wrapper.place(**kwargs)

    def place_forget(self):
        return self._wrapper.place_forget()


# =============================================================================
# OptionCard - card seleccionable (radio button disfrazado de tarjeta)
# =============================================================================

class OptionCard(tk.Frame):
    """Card clickable que representa una opción de un grupo de radios.
    Click en cualquier parte de la card la selecciona.
    """

    def __init__(self, parent, var, value, title, subtitle):
        super().__init__(
            parent, bg=SURFACE,
            highlightbackground=BORDER, highlightcolor=BORDER,
            highlightthickness=1,
        )
        self.var = var
        self.value = value

        inner = tk.Frame(self, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=16, pady=10)

        self.indicator = tk.Canvas(
            inner, width=20, height=20,
            bg=SURFACE, highlightthickness=0,
        )
        self.indicator.pack(side="left", padx=(0, 14))

        text_frame = tk.Frame(inner, bg=SURFACE)
        text_frame.pack(side="left", fill="both", expand=True)

        self.title_label = tk.Label(
            text_frame, text=title, font=FONT_OPTION_TITLE,
            bg=SURFACE, fg=TEXT, anchor="w",
        )
        self.title_label.pack(anchor="w")

        self.subtitle_label = tk.Label(
            text_frame, text=subtitle, font=FONT_OPTION_SUB,
            bg=SURFACE, fg=TEXT_MUTED, anchor="w",
            justify="left",
        )
        self.subtitle_label.pack(anchor="w", pady=(2, 0))

        # Bind click en TODOS los descendientes para que el toque funcione
        for w in (self, inner, text_frame, self.indicator,
                  self.title_label, self.subtitle_label):
            w.bind("<Button-1>", lambda e: self._select())

        var.trace_add("write", lambda *a: self._render())
        self._render()

    def _select(self):
        self.var.set(self.value)

    def _render(self):
        selected = (self.var.get() == self.value)
        bg = ACCENT_TINT if selected else SURFACE
        border = ACCENT if selected else BORDER

        self.configure(highlightbackground=border, bg=bg)
        self._set_bg_recursive(self, bg, skip=self.indicator)

        # indicador
        self.indicator.delete("all")
        self.indicator.configure(bg=bg)
        cx, cy, r = 10, 10, 8
        self.indicator.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            outline=ACCENT if selected else BORDER_STRONG,
            width=1.6, fill=bg,
        )
        if selected:
            r2 = 4
            self.indicator.create_oval(
                cx - r2, cy - r2, cx + r2, cy + r2,
                outline="", fill=ACCENT,
            )

    def _set_bg_recursive(self, widget, bg, skip=None):
        if widget is skip:
            return
        try:
            widget.configure(bg=bg)
        except tk.TclError:
            return
        for child in widget.winfo_children():
            self._set_bg_recursive(child, bg, skip=skip)


# =============================================================================
# SegmentedControl - tres botones lado a lado con uno seleccionado
# =============================================================================

class SegmentedControl(tk.Frame):
    """Selector horizontal con varias opciones. La seleccionada queda azul,
    las demás blancas con borde gris."""

    def __init__(self, parent, var, options, height=44):
        super().__init__(parent, bg=BG)
        self.var = var
        self.options = options  # list of (value, label)
        self.height = height
        self._segments = []

        for i, (value, label) in enumerate(options):
            seg = tk.Canvas(
                self, height=height,
                bg=BG, highlightthickness=0, bd=0,
            )
            w_text = _measure_text_width(label, FONT_BUTTON)
            seg_w = max(120, w_text + 32)
            seg.configure(width=seg_w)
            seg.pack(side="left", padx=(0 if i == 0 else 6, 0))
            seg._value = value
            seg._label = label
            seg._width = seg_w   # NO usar _w (atributo interno tkinter)
            seg._hover = False
            seg.bind("<Button-1>", lambda e, v=value: self.var.set(v))
            seg.bind("<Enter>", lambda e, s=seg: self._on_hover(s, True))
            seg.bind("<Leave>", lambda e, s=seg: self._on_hover(s, False))
            self._segments.append(seg)

        var.trace_add("write", lambda *a: self._render())
        self._render()

    def _on_hover(self, seg, hovering):
        seg._hover = hovering
        self._draw_seg(seg)

    def _render(self):
        for seg in self._segments:
            self._draw_seg(seg)

    def _draw_seg(self, seg):
        seg.delete("all")
        selected = (self.var.get() == seg._value)
        if selected:
            bg = ACCENT
            fg = "#FFFFFF"
            border = ACCENT
        elif seg._hover:
            bg = HOVER_BG
            fg = TEXT
            border = BORDER_STRONG
        else:
            bg = SURFACE
            fg = TEXT
            border = BORDER
        pts = _round_rect_pts(1, 1, seg._width - 1, self.height - 1, 10)
        seg.create_polygon(
            pts, smooth=True, fill=bg,
            outline=border, width=1,
        )
        seg.create_text(
            seg._width / 2, self.height / 2,
            text=seg._label, fill=fg, font=FONT_BUTTON,
        )


# =============================================================================
# Toast - notificacion no-intrusiva abajo centrada
# =============================================================================

class Toast:
    """Aviso temporal que aparece abajo centrado y se va solo. Reemplaza a
    messagebox.showinfo para casos donde el usuario no necesita confirmar
    nada — los toasts no interrumpen el flujo. Para errores criticos donde
    si necesitamos atencion (PDF corrupto, Dropbox no encontrado, etc)
    seguimos usando messagebox.

    Implementacion: Toplevel sin borde, con alpha 0.95 si el SO lo soporta,
    posicionado relative al root window.
    """

    _current = None  # ultimo toast vivo (para reemplazarlo si llaman otro)

    @classmethod
    def show(cls, root, text, duration_ms=2500):
        # Si ya hay uno visible, lo descartamos antes de mostrar el nuevo.
        cls._destroy_current()

        top = tk.Toplevel(root)
        top.overrideredirect(True)         # sin barra de titulo / borde
        top.attributes("-topmost", True)
        try:
            top.attributes("-alpha", 0.95)
        except tk.TclError:
            pass
        top.configure(bg=TOAST_BG)

        tk.Label(
            top, text=text, font=FONT_BODY,
            bg=TOAST_BG, fg=TOAST_FG, padx=20, pady=12,
        ).pack()

        # Posicionar abajo-centro del root window.
        root.update_idletasks()
        top.update_idletasks()
        rx = root.winfo_rootx()
        ry = root.winfo_rooty()
        rw = root.winfo_width()
        rh = root.winfo_height()
        tw = top.winfo_width()
        th = top.winfo_height()
        x = rx + (rw - tw) // 2
        y = ry + rh - th - 32
        top.geometry(f"+{x}+{y}")

        cls._current = top
        root.after(duration_ms, lambda t=top: cls._destroy(t))

    @classmethod
    def _destroy(cls, top):
        if cls._current is top:
            cls._current = None
        try:
            top.destroy()
        except Exception:
            pass

    @classmethod
    def _destroy_current(cls):
        if cls._current is not None:
            cls._destroy(cls._current)


# =============================================================================
# Spinner - circulo animado para loading states
# =============================================================================

class Spinner(tk.Canvas):
    """Spinner circular animado de 8 puntos. Uso:
        s = Spinner(parent, size=24)
        s.pack()
        s.start()
        ...
        s.stop()
        s.destroy()
    """

    def __init__(self, parent, size=24, color=ACCENT, bg=None):
        bg = bg or parent.cget("bg")
        super().__init__(
            parent, width=size, height=size,
            bg=bg, highlightthickness=0, bd=0,
        )
        self._size = size
        self._color = color
        self._bg = bg
        self._step = 0
        self._after_id = None

    def start(self, interval_ms=90):
        self._tick()
        self._after_id = self.after(interval_ms, self._loop, interval_ms)

    def _loop(self, interval_ms):
        self._step = (self._step + 1) % 8
        self._tick()
        if self._after_id is not None:
            self._after_id = self.after(interval_ms, self._loop, interval_ms)

    def _tick(self):
        import math
        self.delete("all")
        n = 8
        radius_outer = self._size / 2 - 3
        radius_dot = max(1.5, self._size / 10)
        cx = cy = self._size / 2
        # Alphas decrecientes desde el "punto activo": el actual opaco,
        # los demas cada vez mas claros (mezcla con bg).
        alphas = [1.0, 0.78, 0.58, 0.42, 0.30, 0.22, 0.16, 0.12]
        for i in range(n):
            angle = 2 * math.pi * i / n - math.pi / 2
            x = cx + radius_outer * math.cos(angle)
            y = cy + radius_outer * math.sin(angle)
            offset = (self._step - i) % n
            alpha = alphas[offset]
            color = self._mix(self._color, self._bg, alpha)
            self.create_oval(
                x - radius_dot, y - radius_dot,
                x + radius_dot, y + radius_dot,
                fill=color, outline="",
            )

    def stop(self):
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self.delete("all")

    @staticmethod
    def _mix(fg, bg, alpha):
        """Mezcla colores hex: alpha=1 → fg puro, alpha=0 → bg puro."""
        fr, fgv, fb = int(fg[1:3], 16), int(fg[3:5], 16), int(fg[5:7], 16)
        br, bgv, bb = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
        r = int(fr * alpha + br * (1 - alpha))
        g = int(fgv * alpha + bgv * (1 - alpha))
        b = int(fb * alpha + bb * (1 - alpha))
        return f"#{r:02x}{g:02x}{b:02x}"


# =============================================================================
# App
# =============================================================================

class App:
    def __init__(self):
        # Usamos TkinterDnD.Tk en vez de tk.Tk para habilitar drag-and-drop
        # de archivos a la ventana. Es una subclase 100% compatible: todo el
        # resto del codigo lo usa como un tk.Tk normal.
        # Si TkinterDnD.Tk() crashea (libs nativas tkdnd no cargan en este
        # binario empaquetado), caemos a tk.Tk() y dejamos el motivo en el
        # log para diagnostico.
        self.dnd_active = False
        if DND_AVAILABLE:
            try:
                self.root = TkinterDnD.Tk()
                self.dnd_active = True
                _log_event("DnD: TkinterDnD.Tk() OK")
            except Exception as e:
                _log_event(f"DnD: TkinterDnD.Tk() FAIL — {type(e).__name__}: {e}")
                self.root = tk.Tk()
        else:
            _log_event(
                f"DnD: tkinterdnd2 no se pudo importar — {_DND_IMPORT_ERROR}"
            )
            self.root = tk.Tk()
        # Atrapar excepciones que ocurren dentro de callbacks de tk
        # (clicks, eventos UI, etc) para que queden logueadas y el usuario
        # vea algo útil en lugar de un cuelgue silencioso.
        self.root.report_callback_exception = self._on_tk_callback_exception
        # Truco macOS Sequoia: tk arranca la ventana en 200x200 antes de
        # aplicar geometry. Si el usuario ve la ventana en ese momento, queda
        # chica hasta que haga "zoom" manualmente. Solución: withdraw() oculta
        # la ventana durante todo el setup; deiconify() al final la muestra
        # con el tamaño correcto ya aplicado.
        self.root.withdraw()
        self.root.title("Fotos Proforma")
        # Ventana redimensionable como cualquier app moderna. El usuario puede
        # agarrar las esquinas y agrandarla; el contenido se adapta gracias a
        # pack(fill=...) y a los wraplengths dinamicos (_bind_dynamic_wraplength).
        self.root.resizable(True, True)
        self.root.configure(bg=BG)
        self.root.minsize(WINDOW_W_MIN, WINDOW_H_MIN)
        # Sin maxsize: la app se puede maximizar libremente.
        # NOTA: el geometry inicial se aplica mas tarde en run() leyendo de
        # settings (recuerda lo que el usuario eligio la sesion anterior). Si
        # no hay nada guardado, run() centra una ventana del tamaño default.

        # Track de pantalla actual (1, 2, "processing", "result") para que
        # los atajos de teclado sepan que hacer en cada contexto.
        self._current_screen = None

        # Interceptar el cierre de la ventana para guardar geometry primero.
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Cargar preferencias guardadas (modo, no_ind, dest_root, geometry)
        prefs = user_settings.load()
        self._saved_geometry = prefs.get("window_geometry", "") or ""

        # Estado
        # pdf_paths es la lista canonica (puede ser 1 o varios para batch).
        # pdf_path / parsed_data / result se mantienen como "el primero" para
        # codigo legacy, pero el flujo real usa las listas plurales.
        self.pdf_paths = []           # list[str]
        self.parsed_data_list = []    # list[dict] {path, parsed, error?}
        self.results = []             # list[dict] - uno por PDF procesado
        self.modo_var = tk.StringVar(value=prefs.get("modo", "complete"))
        self.no_ind_var = tk.StringVar(value=prefs.get("no_ind", "grupal"))
        self.dest_root_var = tk.StringVar(value=prefs.get("dest_root", str(DEFAULT_DEST_ROOT)))
        # Nombres editables de carpeta: dict path -> StringVar.
        # Cada PDF cargado tiene su propio StringVar con el cliente detectado
        # como default. El usuario puede sobreescribir cada uno en pantalla 2.
        self.client_override_vars = {}

        # Container
        self.container = tk.Frame(self.root, bg=BG)
        self.container.pack(fill="both", expand=True)

        # Hook macOS para arrastrar PDFs sobre el ícono / Dock / "Open with".
        # Cuando macOS quiere abrir un PDF con esta app, mandara un Apple Event
        # que Tk traduce a este comando.
        try:
            self.root.createcommand(
                "::tk::mac::OpenDocument", self._on_macos_open_document,
            )
        except tk.TclError:
            pass  # no estamos en macOS Tk - ignoramos

        # IMPORTANTE: dropbox_status / _update_info / _update_banner_dismissed
        # tienen que existir ANTES de show_screen1 porque la pantalla 1 los lee
        # para pintar el chip del footer y para mostrar el banner de update.
        self.dropbox_status = None
        self._update_info = None
        self._update_banner_dismissed = False
        self.s1_update_banner = None

        self.show_screen1()

        # Registrar drop target en TODA la ventana (no en la bandeja). En
        # macOS los Canvas widgets reciben drops de forma poco confiable;
        # el root window siempre los recibe. Bonus: el usuario puede arrastrar
        # a cualquier lado de la ventana, no solo a la bandeja.
        self._wire_root_dnd()

        # Atajos de teclado: Enter avanza, Esc vuelve, Cmd+W cierra.
        self._wire_shortcuts()

        # Pre-flight check de Dropbox en background. El chip del footer ya
        # lo dispara en su propia inicializacion (show_screen1), pero lo
        # forzamos aca por si la pantalla 1 todavia no se renderizo.
        self._refresh_dropbox_status(force_async=True)

        # Chequeo de updates contra GitHub Releases en background. Si hay
        # una version mas nueva, mostramos un banner no-intrusivo arriba
        # de pantalla 1. El thread es daemon y completamente safe-to-fail:
        # sin internet o sin releases publicados, el banner no aparece.
        # (Las flags _update_* ya estan inicializadas arriba, antes de
        # show_screen1, para que esa pantalla pueda leerlas sin crashear.)
        threading.Thread(target=self._check_for_updates, daemon=True).start()

        # Nota: el modo light/dark se aplica al arrancar (ver
        # _apply_palette_globals al cargar el modulo). Si el usuario cambia
        # el modo del SO con la app abierta, el cambio se ve la proxima vez
        # que cierre y reabra la app — no hacemos refresh en runtime porque
        # el flicker durante el re-render se siente peor que el delay de
        # tener que reiniciar.

        # Si la app fue invocada con PDF(s) como argumento (drag al .app desde
        # Finder antes de que la app este abierta) los cargamos.
        argv_pdfs = [
            a for a in sys.argv[1:]
            if a.lower().endswith(".pdf") and Path(a).exists()
        ]
        if argv_pdfs:
            self.root.after(100, lambda paths=argv_pdfs: self._load_pdf_paths(paths))

    # =========================================================================
    # Shortcuts, cierre de ventana, transiciones, toasts, loading
    # =========================================================================

    def _wire_shortcuts(self):
        """Atajos de teclado globales (bindeados al root):
        - Enter: avanzar al siguiente paso (segun pantalla).
        - Escape: volver atras.
        - Cmd+W (macOS) / Ctrl+W (Windows/Linux): cerrar la app.
        - Cmd+Q (macOS): cerrar la app — extra, costumbre mac.
        """
        self.root.bind("<Return>", self._on_enter_key)
        self.root.bind("<KP_Enter>", self._on_enter_key)
        self.root.bind("<Escape>", self._on_escape_key)
        self.root.bind("<Command-w>", lambda e: self._on_close())
        self.root.bind("<Command-q>", lambda e: self._on_close())
        self.root.bind("<Control-w>", lambda e: self._on_close())

    def _on_enter_key(self, event):
        """Enter avanza segun el contexto. Si el foco esta en un Entry o
        Text editable, no interferimos (dejamos que Enter haga lo suyo)."""
        try:
            cls = event.widget.winfo_class()
        except Exception:
            cls = ""
        if cls in ("Entry", "TEntry", "Text", "Spinbox"):
            return
        screen = self._current_screen
        if screen == 1:
            # Solo avanzar si hay al menos un PDF parseado OK.
            if any("parsed" in e for e in self.parsed_data_list):
                self._goto(self.show_screen2)
        elif screen == 2:
            self._start_processing()
        # En "processing" no hacemos nada. En "result" tampoco — el usuario
        # tiene que elegir explicitamente "Abrir carpeta" o "Procesar otra".

    def _on_escape_key(self, _event):
        screen = self._current_screen
        if screen == 2:
            self._goto(self.show_screen1)
        elif screen == "result":
            self._back_to_filters()

    def _on_close(self):
        """Interceptor del cierre de ventana: guardamos geometry antes de
        salir asi la proxima sesion arranca con el mismo tamaño/posicion."""
        self._save_geometry()
        try:
            self.root.destroy()
        except Exception:
            pass

    def _save_geometry(self):
        """Guarda el tamaño + posicion actual en settings.json."""
        try:
            geo = self.root.geometry()  # formato "WxH+X+Y"
            prefs = user_settings.load()
            prefs["window_geometry"] = geo
            user_settings.save(prefs)
        except Exception:
            pass

    def _restore_geometry(self):
        """Aplica el geometry guardado si es valido. Si no hay nada guardado
        o esta fuera de la pantalla actual (ej. usuario desconecto un monitor),
        centra la ventana con tamaño default."""
        geo = self._saved_geometry
        applied = False
        if geo:
            try:
                # Validar que el geometry no este completamente fuera de la
                # pantalla actual. Formato: "WxH+X+Y" (o sin +X+Y).
                import re
                m = re.match(r"(\d+)x(\d+)(?:([+-]\d+)([+-]\d+))?$", geo)
                if m:
                    w = int(m.group(1))
                    h = int(m.group(2))
                    x = int(m.group(3)) if m.group(3) else None
                    y = int(m.group(4)) if m.group(4) else None
                    sw = self.root.winfo_screenwidth()
                    sh = self.root.winfo_screenheight()
                    # Sanity: tamaño minimo respetado, posicion dentro de
                    # alguna pantalla razonable.
                    w = max(WINDOW_W_MIN, w)
                    h = max(WINDOW_H_MIN, h)
                    if x is not None and y is not None:
                        # Si la esquina superior esta totalmente fuera, lo
                        # corregimos a algo razonable.
                        if x < -50 or x > sw - 100 or y < 0 or y > sh - 100:
                            x = max(0, (sw - w) // 2)
                            y = max(28, (sh - h) // 2)
                        self.root.geometry(f"{w}x{h}+{x}+{y}")
                    else:
                        self.root.geometry(f"{w}x{h}")
                    applied = True
            except Exception:
                applied = False
        if not applied:
            # Default: centrar en area usable (descontando menubar y dock).
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            MENU_BAR = 28
            DOCK = 90
            usable_h = sh - MENU_BAR - DOCK
            x = max(0, (sw - WINDOW_W) // 2)
            if WINDOW_H >= usable_h:
                y = MENU_BAR + 5
            else:
                y = MENU_BAR + (usable_h - WINDOW_H) // 2
            self.root.geometry(f"{WINDOW_W}x{WINDOW_H}+{x}+{y}")

    def toast(self, text, duration_ms=2500):
        """Muestra un toast (notificacion no intrusiva). Wrapper de Toast.show
        para que el codigo de la app llame self.toast(...) directo."""
        try:
            Toast.show(self.root, text, duration_ms=duration_ms)
        except Exception:
            # Si el toast falla, no rompemos el flujo — es nice-to-have.
            pass

    # ---- Vista previa de fotos por marca (pantalla 2) ----------------------

    def _build_brand_previews(self, parent):
        """Sección de pantalla 2 que muestra un thumbnail por marca detectada
        en las proformas. La carga es async en thread daemon — la UI no se
        bloquea esperando que se lean las fotos."""
        if not PIL_AVAILABLE:
            return
        if not (self.dropbox_status and self.dropbox_status.get("connected")):
            return

        # Recolectar primera ref con foto (parsed, item) por marca de TODAS
        # las proformas cargadas. Filtramos SKUs surtido (no hay individual,
        # pero la grupal igual existe asi que las dejamos).
        by_brand = {}  # brand -> (parsed, item)
        for entry in self.parsed_data_list:
            if "parsed" not in entry:
                continue
            for item in entry["parsed"]["items"]:
                parsed = parse_sku(item["sku"])
                if parsed and parsed.get("brand") and parsed["brand"] not in by_brand:
                    by_brand[parsed["brand"]] = (parsed, item)
        if not by_brand:
            return

        # Section label + frame horizontal de thumbnails con scroll horizontal
        # — si hay muchas marcas no entran en el ancho de la ventana, asi el
        # usuario puede arrastrar para ver el resto.
        self._section_label(parent, "Vista previa") \
            .pack(anchor="w", pady=(0, 10))

        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", pady=(0, SECTION_GAP))

        thumbs_canvas = tk.Canvas(
            wrap, bg=BG, highlightthickness=0, bd=0,
            height=108,  # alto fijo: 72 thumbnail + 4 pad + ~20 label + scrollbar
        )
        hbar = tk.Scrollbar(wrap, orient="horizontal", command=thumbs_canvas.xview)
        thumbs_canvas.configure(xscrollcommand=hbar.set)
        thumbs_canvas.pack(side="top", fill="x")
        hbar.pack(side="bottom", fill="x")

        thumbs_frame = tk.Frame(thumbs_canvas, bg=BG)
        thumbs_canvas.create_window((0, 0), window=thumbs_frame, anchor="nw")

        def _on_thumbs_configure(_e):
            thumbs_canvas.configure(scrollregion=thumbs_canvas.bbox("all"))
        thumbs_frame.bind("<Configure>", _on_thumbs_configure)

        # Por cada marca: columna con placeholder Canvas + nombre debajo.
        # Los placeholders se reemplazan por la imagen real cuando llega.
        self._brand_preview_widgets = {}
        for brand in by_brand:
            col = tk.Frame(thumbs_frame, bg=BG)
            col.pack(side="left", padx=(0, 10))
            canvas = tk.Canvas(
                col, width=72, height=72, bg=SURFACE,
                highlightthickness=1, highlightbackground=BORDER_SUBTLE,
                bd=0,
            )
            canvas.pack()
            # Placeholder: iniciales de la marca centradas en gris muy claro.
            initials = "".join(w[0] for w in brand.split()[:2]).upper()[:3] or brand[:3].upper()
            canvas.create_text(
                36, 36, text=initials, font=FONT_BODY_BOLD, fill=TEXT_LIGHT,
            )
            # Nombre de la marca debajo (truncado si es muy largo)
            display_name = brand if len(brand) <= 12 else brand[:11] + "…"
            tk.Label(
                col, text=display_name, font=FONT_CAPTION,
                bg=BG, fg=TEXT_MUTED,
            ).pack(pady=(4, 0))
            self._brand_preview_widgets[brand] = canvas

        self._load_brand_previews_async(by_brand)

    def _load_brand_previews_async(self, by_brand):
        """Carga las imágenes en thread daemon. Cuando llega cada thumbnail
        lo aplica via root.after a la canvas correspondiente."""
        if not hasattr(self, "_brand_thumbnail_cache"):
            self._brand_thumbnail_cache = {}
        cache = self._brand_thumbnail_cache
        dbx = self.dropbox_status["root"]

        def _do():
            import finder as finder_mod
            for brand, (parsed, _item) in by_brand.items():
                # Cache hit: aplicar directo en main thread.
                if brand in cache:
                    tk_img = cache[brand]
                    self.root.after(
                        0, lambda b=brand, img=tk_img:
                            self._apply_brand_thumbnail(b, img),
                    )
                    continue
                try:
                    src = finder_mod.find_grupal(parsed, None, dbx)
                    if src is None:
                        continue
                    img = Image.open(src).convert("RGB")
                    img.thumbnail((68, 68), Image.LANCZOS)
                    # PhotoImage SOLO en main thread (Tk no es thread-safe).
                    self.root.after(
                        0, lambda b=brand, im=img: self._cache_and_apply_thumb(b, im),
                    )
                except Exception:
                    continue

        threading.Thread(target=_do, daemon=True).start()

    def _cache_and_apply_thumb(self, brand, pil_img):
        """En main thread: crea PhotoImage, lo cachea y aplica al canvas."""
        try:
            tk_img = ImageTk.PhotoImage(pil_img)
        except Exception:
            return
        self._brand_thumbnail_cache[brand] = tk_img
        self._apply_brand_thumbnail(brand, tk_img)

    def _apply_brand_thumbnail(self, brand, tk_img):
        """Reemplaza el placeholder de la marca por el thumbnail cargado."""
        widgets = getattr(self, "_brand_preview_widgets", {})
        canvas = widgets.get(brand)
        if canvas is None or not canvas.winfo_exists():
            return
        canvas.delete("all")
        canvas.create_image(36, 36, image=tk_img)
        # Guardar ref como atributo del canvas para que el GC no destruya
        # la PhotoImage antes que el canvas.
        canvas.image = tk_img

    def _preflight_brands_check(self, ok_entries):
        """Verifica que las marcas detectadas en las proformas existan como
        carpetas en Dropbox. Si alguna marca falta, advierte al usuario y
        deja decidir si procesa igual (los SKUs de esa marca quedan como
        faltantes en el reporte).

        Returns:
            True si se puede continuar (todas OK o usuario eligio continuar).
            False si el usuario cancelo.
        """
        if not self.dropbox_status:
            return True
        dbx_brands = self.dropbox_status.get("brands") or set()
        if not dbx_brands:
            return True  # no pudimos enumerar marcas, no advertimos

        # Recolectar marcas de todos los PDFs OK.
        proforma_brands = set()
        for entry in ok_entries:
            for item in entry["parsed"]["items"]:
                parsed = parse_sku(item["sku"])
                if parsed and parsed.get("brand"):
                    proforma_brands.add(parsed["brand"].upper())

        # Cruzar contra Dropbox. Usamos BRAND_FOLDERS para mapear variantes
        # de nombre (ej. "VOX" tambien aparece como "VOX 2025"). Hacemos un
        # match laxo: la marca esta OK si alguna carpeta de dropbox contiene
        # su nombre o variante.
        try:
            from brands import BRAND_FOLDERS
        except Exception:
            BRAND_FOLDERS = {}

        missing_brands = []
        for brand in sorted(proforma_brands):
            candidates = {brand.upper()}
            for v in (BRAND_FOLDERS.get(brand, []) or []):
                candidates.add(v.upper())
            found = any(
                any(c == d or c in d or d.startswith(c)
                    for d in dbx_brands)
                for c in candidates
            )
            if not found:
                missing_brands.append(brand)

        if not missing_brands:
            return True

        names = ", ".join(missing_brands)
        n = len(missing_brands)
        plural = "estas marcas" if n > 1 else "esta marca"
        title = "Marca sin carpeta en Dropbox" if n == 1 else f"{n} marcas sin carpeta"
        body = (
            f"No encontré carpeta en Dropbox para {plural}:\n\n"
            f"  {names}\n\n"
            "Los SKUs de esa(s) marca(s) van a quedar como faltantes en el "
            "reporte. ¿Procesar igual?"
        )
        return messagebox.askyesno(title, body, default="yes")

    # ---- Auto-update (chequeo de releases en GitHub) ------------------------

    def _check_for_updates(self):
        """Corre en thread daemon. Consulta la API de GitHub por el ultimo
        release; si es mas nuevo que APP_VERSION y el usuario no lo omitio,
        agenda mostrar el banner en main thread."""
        info = updater.check_latest_release()
        if not info:
            return  # sin internet, sin releases, o fallo silencioso
        remote_tag = info.get("tag_name") or ""
        if not updater.is_newer(remote_tag, APP_VERSION):
            return  # ya estamos al dia (o version local mas nueva)
        # Respeta "omitir esta version".
        prefs = user_settings.load()
        if prefs.get("skipped_version") == remote_tag:
            return
        # Aplicar en main thread.
        try:
            self.root.after(0, lambda: self._on_update_available(info))
        except Exception:
            pass

    def _on_update_available(self, info):
        """Guarda el info y muestra el banner si estamos en pantalla 1."""
        self._update_info = info
        if self._current_screen == 1:
            self._show_update_banner()

    def _show_update_banner(self):
        """Crea un banner azul claro arriba del header con info de la
        nueva version y 3 botones: Ver release / Mas tarde / Omitir.
        Solo se muestra en pantalla 1 — en otras pantallas la guardamos
        para mostrar despues."""
        if not self._update_info or self._update_banner_dismissed:
            return
        if hasattr(self, "s1_update_banner") and self.s1_update_banner is not None \
                and self.s1_update_banner.winfo_exists():
            return  # ya esta visible

        info = self._update_info
        version = info.get("tag_name", "?")

        banner = tk.Frame(self.container, bg=ACCENT_TINT)
        banner.pack(side="top", fill="x")
        # Padding interno con sub-frame para que el bg azul llegue al borde
        # pero el contenido tenga padding.
        inner = tk.Frame(banner, bg=ACCENT_TINT)
        inner.pack(fill="x", padx=SCREEN_PADX, pady=10)

        # Texto a la izquierda.
        text = tk.Frame(inner, bg=ACCENT_TINT)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(
            text,
            text=f"Hay una versión nueva disponible: {version}",
            font=FONT_BODY_BOLD, bg=ACCENT_TINT, fg=ACCENT, anchor="w",
        ).pack(anchor="w")
        tk.Label(
            text,
            text="Bajala desde la página del release y reemplazá la versión actual.",
            font=FONT_CAPTION, bg=ACCENT_TINT, fg=TEXT_MUTED, anchor="w",
        ).pack(anchor="w")

        # Botones a la derecha.
        btns = tk.Frame(inner, bg=ACCENT_TINT)
        btns.pack(side="right")
        CanvasButton(
            btns, text="Ver release",
            command=self._on_update_view, kind="primary",
            height=32, padx=14, font=FONT_CAPTION,
            parent_bg=ACCENT_TINT,
        ).pack(side="left")
        CanvasButton(
            btns, text="Más tarde",
            command=self._on_update_remind_later, kind="text",
            height=32, padx=10, font=FONT_CAPTION,
            parent_bg=ACCENT_TINT,
        ).pack(side="left", padx=(6, 0))
        CanvasButton(
            btns, text="Omitir",
            command=self._on_update_skip, kind="text",
            height=32, padx=10, font=FONT_CAPTION,
            parent_bg=ACCENT_TINT,
        ).pack(side="left", padx=(6, 0))

        self.s1_update_banner = banner

    def _hide_update_banner(self):
        b = getattr(self, "s1_update_banner", None)
        if b is not None and b.winfo_exists():
            b.destroy()
        self.s1_update_banner = None

    def _on_update_view(self):
        """Click en 'Ver release': abrir URL en el browser default."""
        info = self._update_info or {}
        url = info.get("html_url") or ""
        if url:
            platform_utils.open_in_browser(url)
        # No cerramos el banner — el usuario puede querer verlo de nuevo.

    def _on_update_remind_later(self):
        """Click en 'Mas tarde': cerrar el banner solo para esta sesion.
        En el proximo arranque vuelve a aparecer."""
        self._update_banner_dismissed = True
        self._hide_update_banner()

    def _on_update_skip(self):
        """Click en 'Omitir': guardar el tag en settings asi este banner
        no vuelve a aparecer hasta que salga una version MAS nueva."""
        info = self._update_info or {}
        tag = info.get("tag_name") or ""
        if tag:
            prefs = user_settings.load()
            prefs["skipped_version"] = tag
            user_settings.save(prefs)
        self._update_banner_dismissed = True
        self._hide_update_banner()

    # ---- Dropbox status indicator (pre-flight + chip footer) ---------------

    def _refresh_dropbox_status(self, force_async=False):
        """Re-checkea Dropbox y actualiza self.dropbox_status. Si force_async,
        corre en thread daemon y actualiza el UI cuando termina. Si no,
        bloquea (pero es rapido en la mayoria de los casos)."""
        def _do():
            try:
                status = dropbox_mod.get_status()
            except Exception as e:
                status = {"connected": False, "root": None, "brand_count": 0,
                          "error": f"{type(e).__name__}: {e}"}
            # Si seguimos vivos, aplicar en main thread.
            try:
                self.root.after(0, lambda: self._apply_dbx_status(status))
            except Exception:
                pass

        if force_async:
            threading.Thread(target=_do, daemon=True).start()
        else:
            _do()

    def _apply_dbx_status(self, status):
        """Guarda el status nuevo y actualiza el chip si existe."""
        self.dropbox_status = status
        self._update_dbx_chip()

    def _update_dbx_chip(self):
        """Refresca el chip del footer (pantalla 1) segun self.dropbox_status."""
        if not (hasattr(self, "s1_dbx_label") and self.s1_dbx_label is not None
                and self.s1_dbx_label.winfo_exists()):
            return  # no estamos en pantalla 1
        status = self.dropbox_status
        if status is None:
            self.s1_dbx_dot.configure(fg=TEXT_LIGHT)
            self.s1_dbx_label.configure(
                text="Dropbox: chequeando…", fg=TEXT_LIGHT,
            )
            return
        if status["connected"]:
            self.s1_dbx_dot.configure(fg=SUCCESS)
            n = status["brand_count"]
            self.s1_dbx_label.configure(
                text=f"Dropbox conectado · {n} marca{'s' if n != 1 else ''}",
                fg=TEXT_MUTED,
            )
        else:
            self.s1_dbx_dot.configure(fg=ERROR)
            self.s1_dbx_label.configure(
                text="Dropbox no encontrado · click para reintentar",
                fg=ERROR,
            )

    # ---- Loading overlay (#4) ----------------------------------------------

    def _show_loading(self, text="Cargando…"):
        """Muestra un overlay semi-transparente con spinner + texto.
        Usado durante operaciones bloqueantes (ej. parseo de PDFs grandes)
        para que la UI no se sienta congelada."""
        self._hide_loading()  # idempotente: si hay uno previo lo descartamos

        overlay = tk.Frame(self.root, bg=SURFACE)
        # Cubre toda la ventana.
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        try:
            # Alpha en el Frame mismo no se puede, pero podemos hacer la
            # ventana entera un toque mas opaca: vamos con SURFACE solido.
            overlay.lift()
        except Exception:
            pass

        inner = tk.Frame(overlay, bg=SURFACE)
        inner.place(relx=0.5, rely=0.5, anchor="center")

        spinner = Spinner(inner, size=36, color=ACCENT, bg=SURFACE)
        spinner.pack()
        spinner.start()

        tk.Label(
            inner, text=text, font=FONT_BODY,
            bg=SURFACE, fg=TEXT_MUTED,
        ).pack(pady=(14, 0))

        self._loading_overlay = overlay
        self._loading_spinner = spinner

    def _hide_loading(self):
        sp = getattr(self, "_loading_spinner", None)
        ov = getattr(self, "_loading_overlay", None)
        if sp is not None:
            try:
                sp.stop()
            except Exception:
                pass
            self._loading_spinner = None
        if ov is not None:
            try:
                ov.destroy()
            except Exception:
                pass
            self._loading_overlay = None

    # ---- Fade transition entre pantallas (#7) -------------------------------

    def _goto(self, screen_fn):
        """Cambia de pantalla con un fade-in sutil. Es la forma estandar
        de navegar entre pantallas desde botones o atajos de teclado.

        Patron: setea alpha=0.55, cambia contenido instantaneo, hace
        fade-in animado a 1.0 (~75ms total). No hace fade-out previo —
        eso requeriria render asincrono que con tk classic es trabajoso
        y para pantallas que tardan en renderizar se ve peor (ventana
        invisible mientras se monta el contenido).

        Si el SO no soporta -alpha (raro), cambia sin animacion.
        """
        try:
            self.root.attributes("-alpha", 0.55)
        except tk.TclError:
            screen_fn()
            return
        screen_fn()
        # update_idletasks fuerza el render del contenido nuevo antes de
        # que empiece el fade — evita ver "vacio" mientras se monta.
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        self._fade_step_to(1.0)

    def _fade_step_to(self, target, current=None, step=0.15, interval_ms=15):
        """Avanza la opacidad del root hacia target en pasos chiquitos."""
        if current is None:
            try:
                raw = self.root.attributes("-alpha")
                current = float(raw) if raw not in (None, "") else 1.0
            except (tk.TclError, ValueError, TypeError):
                return
        if current >= target:
            try:
                self.root.attributes("-alpha", target)
            except tk.TclError:
                pass
            return
        next_val = min(target, current + step)
        try:
            self.root.attributes("-alpha", next_val)
        except tk.TclError:
            pass
        self.root.after(
            interval_ms,
            lambda: self._fade_step_to(target, next_val, step, interval_ms),
        )

    # =========================================================================
    # Drag-and-drop a nivel root
    # =========================================================================

    def _wire_root_dnd(self):
        """Registra el root window como drop target para archivos. Los drops
        se aceptan en cualquier parte de la ventana y se delegan al handler
        de pantalla 1. En pantallas 2/3 los drops se ignoran (la bandeja ya
        no existe)."""
        if not self.dnd_active:
            return
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<DropEnter>>", self._on_root_dnd_enter)
            self.root.dnd_bind("<<DropLeave>>", self._on_root_dnd_leave)
            self.root.dnd_bind("<<Drop>>", self._on_root_drop)
            _log_event("DnD: root drop target registrado OK")
        except Exception as e:
            _log_event(
                f"DnD: root drop_target_register FAIL — {type(e).__name__}: {e}"
            )

    def _dropzone_alive(self):
        """True si la bandeja de pantalla 1 existe y esta empaquetada (o sea,
        estamos en pantalla 1). Usado por los handlers de drop para decidir
        si reaccionar al evento."""
        return (
            hasattr(self, "s1_drop_zone")
            and self.s1_drop_zone is not None
            and self.s1_drop_zone.winfo_exists()
        )

    def _on_root_dnd_enter(self, _event):
        if self._dropzone_alive():
            self.s1_drop_zone.set_dragover(True)

    def _on_root_dnd_leave(self, _event):
        if self._dropzone_alive():
            self.s1_drop_zone.set_dragover(False)

    def _on_root_drop(self, event):
        if self._dropzone_alive():
            self.s1_drop_zone.set_dragover(False)
        else:
            # No estamos en pantalla 1: ignoramos el drop silenciosamente.
            return
        paths = DropZone._parse_dnd_paths(event.data)
        _log_event(f"DnD: drop recibido — {len(paths)} PDF(s)")
        # Flag temporal: en macOS soltar un drop sobre el Canvas a veces
        # dispara un click sintetico justo despues que abriria el filedialog.
        # Silenciamos cualquier click en los proximos 400ms.
        self._drop_in_progress = True
        self.root.after(400, lambda: setattr(self, "_drop_in_progress", False))
        if paths:
            self._on_dropzone_drop(paths)

    # --- Backwards-compat properties (codigo viejo asume singular) -----------
    @property
    def pdf_path(self):
        return self.pdf_paths[0] if self.pdf_paths else None

    @pdf_path.setter
    def pdf_path(self, value):
        if value is None:
            self.pdf_paths = []
        else:
            self.pdf_paths = [value]

    @property
    def parsed_data(self):
        if self.parsed_data_list:
            return self.parsed_data_list[0].get("parsed")
        return None

    @parsed_data.setter
    def parsed_data(self, value):
        if value is None:
            self.parsed_data_list = []
        else:
            path = self.pdf_paths[0] if self.pdf_paths else None
            self.parsed_data_list = [{"path": path, "parsed": value}]

    @property
    def result(self):
        return self.results[0] if self.results else None

    @result.setter
    def result(self, value):
        if value is None:
            self.results = []
        else:
            self.results = [value]

    # ------------------------- helpers ---------------------------------------

    def _clear(self):
        for w in self.container.winfo_children():
            w.destroy()

    def _header(self, title, subtitle=None):
        wrap = tk.Frame(self.container, bg=BG)
        wrap.pack(fill="x", pady=(22, 14), padx=SCREEN_PADX)
        tk.Label(
            wrap, text=title, font=FONT_DISPLAY, bg=BG, fg=TEXT, anchor="w",
        ).pack(anchor="w", fill="x")
        if subtitle:
            sub = tk.Label(
                wrap, text=subtitle, font=FONT_SUBTITLE,
                bg=BG, fg=TEXT_MUTED, anchor="w", justify="left",
                wraplength=WINDOW_W - 2 * SCREEN_PADX,
            )
            sub.pack(anchor="w", fill="x", pady=(4, 0))
            _bind_dynamic_wraplength(sub)

    def _section_label(self, parent, text):
        return tk.Label(
            parent, text=text.upper(), font=FONT_SECTION_LABEL,
            bg=BG, fg=TEXT_LIGHT, anchor="w",
        )

    def _make_scrollable_body(self, parent, padx=0):
        """Crea un area scrolleable dentro de `parent` y devuelve un frame
        interno donde packear el contenido. El frame interno tiene padding
        horizontal `padx` aplicado para alinear con el resto del UI.

        Guarda referencia al canvas/handler para que después podamos bindear
        el mousewheel a los descendientes con _bind_scroll_wheel_to_descendants.
        """
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="both", expand=True)

        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        inner = tk.Frame(canvas, bg=BG)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_inner_configure)

        def _on_canvas_configure(event):
            canvas.itemconfig(win_id, width=event.width)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * event.delta), "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)

        # Guardar referencias para bindear hijos despues
        self._scroll_canvas = canvas
        self._scroll_handler = _on_mousewheel

        # Frame interno con padding horizontal
        padded = tk.Frame(inner, bg=BG)
        padded.pack(fill="both", expand=True, padx=padx)
        return padded

    def _bind_scroll_wheel_to_descendants(self, parent):
        """Llamar despues de packear el contenido. Bindea <MouseWheel> en
        toda la jerarquia para que el scroll funcione hovereando cualquier
        widget, no solo el canvas vacio."""
        canvas = getattr(self, "_scroll_canvas", None)
        handler = getattr(self, "_scroll_handler", None)
        if canvas is None or handler is None:
            return
        def _bind(widget):
            widget.bind("<MouseWheel>", handler)
            for child in widget.winfo_children():
                _bind(child)
        _bind(parent)

    def _format_dest_path(self, path):
        """Formatea un path para mostrar: '~' si esta bajo home, truncado al
        final si es muy largo."""
        p = Path(path)
        try:
            rel = p.relative_to(Path.home())
            shown = f"~/{rel}"
        except ValueError:
            shown = str(p)
        if len(shown) > 60:
            shown = "…" + shown[-59:]
        return shown

    def _on_pick_dest_root(self):
        """Abre selector de carpeta. Guarda el path elegido para esta sesión."""
        chosen = filedialog.askdirectory(
            title="Elegí la carpeta destino",
            initialdir=str(Path(self.dest_root_var.get())),
            mustexist=True,
        )
        if not chosen:
            return
        self.dest_root_var.set(chosen)
        if hasattr(self, "s2_dest_label") and self.s2_dest_label.winfo_exists():
            self.s2_dest_label.configure(text=self._format_dest_path(chosen))

    # =========================================================================
    # PANTALLA 1 - Selección de PDF
    # =========================================================================

    def show_screen1(self):
        self._clear()
        self._current_screen = 1
        # El banner de update vive como child del container; _clear() lo
        # destruyo, asi que lo nullificamos. Despues del header lo re-creamos
        # si todavia hay update pendiente y el usuario no lo descarto.
        self.s1_update_banner = None
        self._show_update_banner()
        self._header(
            "Fotos Proforma",
            "Cargá una proforma en PDF y armo la carpeta de fotos para WhatsApp.",
        )

        # Footer primero (asi reserva su espacio antes que el body)
        footer = tk.Frame(self.container, bg=BG)
        footer.pack(side="bottom", fill="x", padx=SCREEN_PADX, pady=24)

        self.s1_next_btn = CanvasButton(
            footer, text="Continuar  →",
            command=lambda: self._goto(self.show_screen2), kind="primary",
        )
        self._set_next_enabled(False)
        self.s1_next_btn.pack(side="right")

        # Versión chiquita + indicador de Dropbox a la izquierda.
        footer_left = tk.Frame(footer, bg=BG)
        footer_left.pack(side="left")
        tk.Label(
            footer_left, text=f"v{APP_VERSION}",
            font=FONT_CAPTION, bg=BG, fg=TEXT_LIGHT,
        ).pack(side="left")

        # Chip de Dropbox: dot de color + texto del estado. Click re-chequea.
        self.s1_dbx_chip = tk.Frame(footer_left, bg=BG)
        self.s1_dbx_chip.pack(side="left", padx=(14, 0))
        self.s1_dbx_dot = tk.Label(
            self.s1_dbx_chip, text="●", font=FONT_BODY, bg=BG, fg=TEXT_LIGHT,
        )
        self.s1_dbx_dot.pack(side="left")
        self.s1_dbx_label = tk.Label(
            self.s1_dbx_chip, text="Dropbox: chequeando…",
            font=FONT_CAPTION, bg=BG, fg=TEXT_LIGHT,
        )
        self.s1_dbx_label.pack(side="left", padx=(4, 0))
        # Click en cualquier parte del chip re-chequea (util si Dropbox
        # terminaba de sincronizar mientras la app ya estaba abierta).
        for w in (self.s1_dbx_chip, self.s1_dbx_dot, self.s1_dbx_label):
            w.bind("<Button-1>", lambda _e: self._refresh_dropbox_status())
        # Actualizar inmediatamente con el ultimo estado conocido (si ya
        # corrimos el check al init) y disparar nuevo check en background.
        self._update_dbx_chip()
        self._refresh_dropbox_status(force_async=True)

        # Body scrolleable (igual que pantalla 2): si la card "Resumen" tiene
        # muchas marcas detectadas, la lista se desborda y el usuario necesita
        # scroll para ver todo.
        body = self._make_scrollable_body(self.container, padx=SCREEN_PADX)

        # Card 1 - selección de archivo
        self.s1_card = Card(body)
        self.s1_card.pack(fill="x", pady=(0, ELEMENT_GAP))

        inner = tk.Frame(self.s1_card, bg=SURFACE)
        inner.pack(padx=24, pady=24, fill="x")

        tk.Label(
            inner, text="Proforma PDF", font=FONT_BODY_BOLD,
            bg=SURFACE, fg=TEXT, anchor="w",
        ).pack(anchor="w")

        self.s1_path_label = tk.Label(
            inner, text="Empezá soltando una proforma o eligiendo un archivo.",
            font=FONT_OPTION_SUB,
            bg=SURFACE, fg=TEXT_LIGHT, anchor="w",
            wraplength=WINDOW_W - 2 * SCREEN_PADX - 60, justify="left",
        )
        self.s1_path_label.pack(anchor="w", fill="x", pady=(4, 6))
        _bind_dynamic_wraplength(self.s1_path_label, margin=8)

        # Linea informativa de formatos soportados (empty state). Sirve de
        # "ayuda invisible": el usuario sabe sin probar que la app entiende
        # Pepperi y los varios formatos de SAP.
        tk.Label(
            inner,
            text="Compatibles: Pepperi · SAP Factura · SAP Pedido · SAP Proforma · SAP Cotización",
            font=FONT_CAPTION, bg=SURFACE, fg=TEXT_LIGHT, anchor="w",
        ).pack(anchor="w", fill="x", pady=(0, 14))

        # Drop zone — bandeja con borde punteado donde se arrastran PDFs.
        # Click en cualquier parte = mismo flujo que el boton (filedialog).
        # Los DROPS los recibe el root window (no la bandeja en si — en macOS
        # los Canvas widgets reciben eventos DnD de forma poco confiable),
        # ver App._wire_root_dnd. Esta bandeja solo dibuja y se pinta azul
        # cuando hay un drag-over en cualquier parte de la ventana.
        # Cuando no hay archivos cargados la hacemos un poco mas alta (mas
        # protagonismo en el empty state). Cuando ya hay archivos se mantiene
        # mas compacta porque es solo un "agregar mas".
        drop_h = 150 if not self.pdf_paths else 110
        self.s1_drop_zone = DropZone(
            inner,
            on_click=self._on_pick_pdf,
            parent_bg=SURFACE,
            height=drop_h,
        )
        self.s1_drop_zone.pack(fill="x", pady=(0, 14))

        # Fila de botones (Seleccionar / Agregar + Limpiar). La etiqueta del
        # primer boton y la visibilidad del Limpiar dependen de si ya hay
        # archivos cargados — se re-renderiza en _render_s1_info().
        self.s1_pick_btn_row = tk.Frame(inner, bg=SURFACE)
        self.s1_pick_btn_row.pack(anchor="w")
        self._render_s1_pick_buttons()

        # Card 2 - info parseada (oculto hasta tener PDF)
        self.s1_info_card = Card(body)
        # se empaqueta dinámicamente

        if self.parsed_data:
            self._render_s1_info()

        # Bindeamos el scroll wheel a todos los descendientes del body
        # scrolleable (igual que pantalla 2). Sin esto, la rueda del mouse
        # solo scrollea cuando el cursor esta sobre el Canvas, no sobre
        # las cards adentro.
        self._bind_scroll_wheel_to_descendants(body)

    def _set_next_enabled(self, enabled):
        self.s1_next_btn.set_enabled(enabled)

    def _get_or_create_override_var(self, entry):
        """Devuelve el StringVar del nombre de carpeta para un PDF dado.
        Lo crea con el cliente detectado como default si no existe."""
        path = entry["path"]
        if path not in self.client_override_vars:
            detected = entry["parsed"].get("client") or ""
            self.client_override_vars[path] = tk.StringVar(value=detected)
        return self.client_override_vars[path]

    def _make_name_entry(self, parent, var):
        """Crea un Entry estilizado para editar nombre de carpeta."""
        wrap = tk.Frame(
            parent, bg=SURFACE,
            highlightbackground=BORDER, highlightcolor=ACCENT,
            highlightthickness=1, bd=0,
        )
        wrap.pack(fill="x")
        entry = tk.Entry(
            wrap, textvariable=var,
            font=FONT_BODY, bg=SURFACE, fg=TEXT,
            relief="flat", bd=0, highlightthickness=0,
            insertbackground=TEXT,
        )
        entry.pack(fill="x", padx=12, pady=10)
        return wrap

    def _render_s1_pick_buttons(self):
        """Re-renderiza la fila de botones de la card de seleccion de PDF.
        - Si no hay archivos: 'Seleccionar archivo(s)...' azul.
        - Si hay archivos: 'Agregar más...' azul + 'Limpiar' gris.
        """
        if not hasattr(self, "s1_pick_btn_row"):
            return
        if not self.s1_pick_btn_row.winfo_exists():
            return
        for w in self.s1_pick_btn_row.winfo_children():
            w.destroy()

        has_files = bool(self.pdf_paths)
        primary_text = "Agregar más..." if has_files else "Seleccionar archivo(s)..."
        CanvasButton(
            self.s1_pick_btn_row, text=primary_text,
            command=self._on_pick_pdf, kind="primary",
            parent_bg=SURFACE,
        ).pack(side="left")

        if has_files:
            CanvasButton(
                self.s1_pick_btn_row, text="Limpiar",
                command=self._clear_pdf_paths, kind="secondary",
                parent_bg=SURFACE,
            ).pack(side="left", padx=(8, 0))

    def _on_pick_pdf(self):
        """Abre el dialog de archivos y AGREGA los seleccionados a la lista
        actual (no reemplaza). Si querés empezar de cero, hay un boton
        'Limpiar' al lado."""
        # Si recien soltamos un drop, macOS puede haber disparado un click
        # sintetico sobre la bandeja — ignoramos para no abrir el filedialog
        # encima del drop que ya esta procesando.
        if getattr(self, "_drop_in_progress", False):
            return
        paths = filedialog.askopenfilenames(
            title="Elegí la(s) proforma(s) en PDF",
            filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")],
            initialdir=str(Path.home() / "Downloads"),
        )
        if not paths:
            return
        self._add_pdf_paths(list(paths))

    def _on_dropzone_drop(self, paths):
        """Callback de DropZone cuando el usuario suelta archivos arrastrados.
        Filtra solo PDFs que existen en disco y los suma al batch actual."""
        valid = [p for p in paths if p.lower().endswith(".pdf") and Path(p).is_file()]
        if not valid:
            # Toast no intrusivo en vez de messagebox modal — el usuario
            # solo necesita un aviso, no tiene que hacer click en OK.
            self.toast("Solo acepto archivos .pdf")
            return
        self._add_pdf_paths(valid)

    def _add_pdf_paths(self, paths):
        """Agrega PDFs a la lista actual (sin reemplazar). El parseo corre en
        un thread para no congelar la UI (PDFs grandes pueden tardar varios
        segundos). Mientras tanto se muestra un loading overlay con spinner.
        Filtra duplicados con lo ya cargado."""
        if not hasattr(self, "s1_path_label") or not self.s1_path_label.winfo_exists():
            self.show_screen1()

        existing = set(self.pdf_paths)
        new = [p for p in paths if p not in existing]
        if not new:
            return  # ya estaban todos cargados

        # Loading overlay con spinner. Texto adaptado segun cantidad.
        loading_text = ("Leyendo proforma…" if len(new) == 1
                        else f"Leyendo {len(new)} proformas…")
        self._show_loading(loading_text)

        # Parseo en thread daemon. Cuando termina, callbackea al main thread.
        def _parse_in_bg(paths_to_parse=new):
            results = []
            for p in paths_to_parse:
                entry = {"path": p}
                try:
                    entry["parsed"] = pdf_dispatch.parse(p)
                except pdf_parser.ParseError as e:
                    entry["error"] = str(e)
                except Exception as e:
                    entry["error"] = f"{type(e).__name__}: {e}"
                results.append(entry)
            # Volver al main thread para tocar el UI.
            self.root.after(0, lambda: self._on_parse_done(paths_to_parse, results))

        threading.Thread(target=_parse_in_bg, daemon=True).start()

    def _on_parse_done(self, new_paths, new_entries):
        """Callback en main thread cuando termina el parseo en background.
        Aplica los resultados al estado, oculta loading, muestra toast de
        confirmacion o error si correspondia."""
        self._hide_loading()

        # Si la pantalla cambio mientras parseabamos (raro pero posible),
        # solo aplicamos el estado pero no re-renderizamos.
        self.pdf_paths.extend(new_paths)
        for entry in new_entries:
            self.parsed_data_list.append(entry)
            p = entry["path"]
            if "parsed" in entry and p not in self.client_override_vars:
                detected = entry["parsed"].get("client") or ""
                self.client_override_vars[p] = tk.StringVar(value=detected)

        # Si TODOS los archivos cargados fallaron, error modal (es bloqueante,
        # el usuario tiene que reaccionar).
        errored = [e for e in self.parsed_data_list if "error" in e]
        if errored and len(errored) == len(self.parsed_data_list):
            title, body = _friendly_error(errored[0]["error"])
            messagebox.showerror(title, body)
        else:
            # Toast de confirmacion no intrusivo.
            ok_count = sum(1 for e in new_entries if "parsed" in e)
            if ok_count == 1:
                self.toast("Proforma cargada")
            elif ok_count > 1:
                self.toast(f"{ok_count} proformas cargadas")

        if hasattr(self, "s1_path_label") and self.s1_path_label.winfo_exists():
            self._render_s1_info()

    def _clear_pdf_paths(self):
        """Vacía la lista de PDFs cargados."""
        self.pdf_paths = []
        self.parsed_data_list = []
        self.client_override_vars = {}
        self._render_s1_info()

    # Compat: reemplazar la lista completa (lo usa el handler de macOS).
    def _load_pdf_paths(self, paths):
        self.pdf_paths = []
        self.parsed_data_list = []
        self.client_override_vars = {}
        self._add_pdf_paths(paths)

    def _load_pdf_path(self, path):
        self._load_pdf_paths([path])

    def _on_macos_open_document(self, *paths):
        """macOS llama esto cuando arrastras PDF(s) al icono de la app o usas
        'Open With > Fotos Proforma' desde Finder. Reemplaza la lista actual
        con los PDFs arrastrados (es el comportamiento natural cuando abris
        archivos desde fuera)."""
        pdfs = [str(p) for p in paths if str(p).lower().endswith(".pdf")]
        if pdfs:
            self._load_pdf_paths(pdfs)

    def _render_s1_info(self):
        for w in self.s1_info_card.winfo_children():
            w.destroy()

        # Re-render botones primarios (etiqueta cambia segun estado).
        self._render_s1_pick_buttons()

        if not self.parsed_data_list:
            self.s1_path_label.configure(
                text="Empezá soltando una proforma o eligiendo un archivo.",
                fg=TEXT_LIGHT,
            )
            self.s1_info_card.pack_forget()
            self._set_next_enabled(False)
            return

        # Actualizar el path label segun cuantos PDFs hay
        n = len(self.parsed_data_list)
        if n == 1:
            self.s1_path_label.configure(text=Path(self.pdf_paths[0]).name, fg=TEXT)
        else:
            self.s1_path_label.configure(text=f"{n} proformas seleccionadas", fg=TEXT)

        # Si hay >1 PDFs, mostrar resumen de la lista; si hay 1, mostrar detalle
        # (mismo formato que antes).
        if n == 1:
            self._render_s1_single_info()
        else:
            self._render_s1_batch_info()

        # Boton siguiente: habilitado si hay al menos 1 PDF parseado OK
        ok = any("parsed" in e for e in self.parsed_data_list)
        self._set_next_enabled(ok)

        # Re-bindear scroll wheel a los hijos nuevos de s1_info_card —
        # el body scrolleable necesita que cada descendiente tenga el
        # handler para que el scroll wheel funcione hovereando cualquier
        # parte. _bind_scroll_wheel_to_descendants ya hace recursion.
        self._bind_scroll_wheel_to_descendants(self.s1_info_card)

    def _render_s1_single_info(self):
        """Renderiza el resumen para 1 PDF (formato detallado de siempre)."""
        entry = self.parsed_data_list[0]
        if "error" in entry:
            self.s1_info_card.pack(fill="x")
            inner = tk.Frame(self.s1_info_card, bg=SURFACE)
            inner.pack(padx=24, pady=20, fill="x")
            tk.Label(
                inner, text="Error", font=FONT_BODY_BOLD,
                bg=SURFACE, fg=ERROR, anchor="w",
            ).pack(anchor="w")
            err_lbl = tk.Label(
                inner, text=entry["error"], font=FONT_BODY,
                bg=SURFACE, fg=TEXT_MUTED, anchor="w", justify="left",
                wraplength=WINDOW_W - 2 * SCREEN_PADX - 60,
            )
            err_lbl.pack(anchor="w", fill="x", pady=(4, 0))
            _bind_dynamic_wraplength(err_lbl, margin=8)
            return

        parsed = entry["parsed"]
        items = parsed["items"]
        fmt = parsed["format"]
        total = len(items)

        # Contar SKUs por marca + refs únicas + no reconocidos + sospechosos
        from collections import Counter
        brand_counts = Counter()
        refs = set()
        unrec, suspect = 0, 0
        for it in items:
            p = parse_sku(it["sku"])
            if p is None:
                unrec += 1
                continue
            refs.add(p["prefix"] + p["number"])
            brand_counts[p["brand"]] += 1
            if it.get("suspect"):
                suspect += 1

        fmt_label = {
            "pepperi":         "Pepperi · Off-line Preview",
            "sap_factura":     "SAP Business One · Factura de Cliente",
            "sap_pedido":      "SAP Business One · Pedido",
            "sap_proforma":    "SAP Business One · Proforma",
            "sap_cotizacion":  "SAP Business One · Cotización",
        }.get(fmt, fmt)

        self.s1_info_card.pack(fill="x")
        inner = tk.Frame(self.s1_info_card, bg=SURFACE)
        inner.pack(padx=24, pady=22, fill="x")

        tk.Label(
            inner, text="Resumen", font=FONT_BODY_BOLD,
            bg=SURFACE, fg=TEXT, anchor="w",
        ).pack(anchor="w", pady=(0, 12))

        client = parsed.get("client")
        rows = [
            ("Cliente", client or "(no detectado)"),
            ("Formato", fmt_label),
            ("SKUs totales", str(total)),
            ("Referencias únicas", str(len(refs))),
        ]
        if unrec:
            rows.append(("Marcas no reconocidas", f"{unrec} código(s)"))
        if suspect:
            rows.append(("SKUs ambiguos", f"{suspect} (resuelvo con fuzzy match)"))

        for label, value in rows:
            row = tk.Frame(inner, bg=SURFACE)
            row.pack(fill="x", pady=3)
            tk.Label(
                row, text=label, font=FONT_BODY,
                bg=SURFACE, fg=TEXT_MUTED, width=22, anchor="w",
            ).pack(side="left")
            tk.Label(
                row, text=value, font=FONT_BODY,
                bg=SURFACE, fg=TEXT, anchor="w",
            ).pack(side="left", fill="x", expand=True)

        # Desglose por marca (siempre, aunque sea una sola)
        if brand_counts:
            tk.Label(
                inner, text="MARCAS DETECTADAS",
                font=FONT_SECTION_LABEL, bg=SURFACE, fg=TEXT_LIGHT, anchor="w",
            ).pack(anchor="w", pady=(14, 6))
            # Ordenamos por cantidad de SKUs, más a menos
            for brand in sorted(brand_counts.keys(), key=lambda b: -brand_counts[b]):
                row = tk.Frame(inner, bg=SURFACE)
                row.pack(fill="x", pady=2)
                tk.Label(
                    row, text=brand, font=FONT_BODY,
                    bg=SURFACE, fg=TEXT, width=22, anchor="w",
                ).pack(side="left")
                count = brand_counts[brand]
                tk.Label(
                    row, text=f"{count} SKU{'s' if count != 1 else ''}",
                    font=FONT_BODY, bg=SURFACE, fg=TEXT_MUTED, anchor="w",
                ).pack(side="left")

    def _render_s1_batch_info(self):
        """Renderiza la lista de PDFs cuando son varios (modo batch)."""
        self.s1_info_card.pack(fill="x")
        inner = tk.Frame(self.s1_info_card, bg=SURFACE)
        inner.pack(padx=24, pady=20, fill="x")

        n_total = len(self.parsed_data_list)
        n_ok = sum(1 for e in self.parsed_data_list if "parsed" in e)
        n_skus = sum(
            len(e["parsed"]["items"]) for e in self.parsed_data_list if "parsed" in e
        )

        tk.Label(
            inner, text=f"Procesar {n_total} proformas",
            font=FONT_BODY_BOLD, bg=SURFACE, fg=TEXT, anchor="w",
        ).pack(anchor="w")
        tk.Label(
            inner,
            text=f"{n_ok} OK, {n_total - n_ok} con error · {n_skus} SKUs en total",
            font=FONT_CAPTION, bg=SURFACE, fg=TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(2, 12))

        # Lista por PDF
        for entry in self.parsed_data_list:
            row = tk.Frame(inner, bg=SURFACE)
            row.pack(fill="x", pady=3)
            name = Path(entry["path"]).name
            if "error" in entry:
                # icono de error como texto
                tk.Label(
                    row, text="✗", font=FONT_BODY_BOLD,
                    bg=SURFACE, fg=ERROR, width=2, anchor="w",
                ).pack(side="left")
                tk.Label(
                    row, text=name, font=FONT_BODY,
                    bg=SURFACE, fg=TEXT, anchor="w",
                ).pack(side="left")
                tk.Label(
                    row, text=entry["error"], font=FONT_CAPTION,
                    bg=SURFACE, fg=ERROR, anchor="e",
                ).pack(side="right", padx=(8, 0))
            else:
                p = entry["parsed"]
                client = p.get("client") or "(sin cliente)"
                n_items = len(p["items"])
                tk.Label(
                    row, text="✓", font=FONT_BODY_BOLD,
                    bg=SURFACE, fg=SUCCESS, width=2, anchor="w",
                ).pack(side="left")
                tk.Label(
                    row, text=name, font=FONT_BODY,
                    bg=SURFACE, fg=TEXT, anchor="w",
                ).pack(side="left")
                tk.Label(
                    row, text=f"{client} · {n_items} SKUs",
                    font=FONT_CAPTION, bg=SURFACE, fg=TEXT_MUTED, anchor="e",
                ).pack(side="right", padx=(8, 0))

    # =========================================================================
    # PANTALLA 2 - Modo y año
    # =========================================================================

    def show_screen2(self):
        self._clear()
        self._current_screen = 2
        n = len(self.pdf_paths)
        if n == 1:
            subtitle = f"PDF: {Path(self.pdf_paths[0]).name}"
        elif n > 1:
            subtitle = f"{n} proformas · misma configuración para todas"
        else:
            subtitle = ""
        self._header("Configurá la búsqueda", subtitle)

        # Footer primero
        footer = tk.Frame(self.container, bg=BG)
        footer.pack(side="bottom", fill="x", padx=SCREEN_PADX, pady=24)

        CanvasButton(
            footer, text="←  Volver",
            command=lambda: self._goto(self.show_screen1), kind="secondary",
        ).pack(side="left")

        CanvasButton(
            footer, text="Procesar  →",
            command=self._start_processing, kind="primary",
        ).pack(side="right")

        # Body scrolleable: si el contenido excede el alto disponible, el
        # usuario puede scrollear con el trackpad/mouse wheel.
        body = self._make_scrollable_body(self.container, padx=SCREEN_PADX)

        # ---- VISTA PREVIA POR MARCA ----
        # Muestra un thumbnail por cada marca detectada (de la grupal de la
        # primera ref). Da confianza visual de que la app encontro las
        # fotos correctas antes de procesar.
        if PIL_AVAILABLE:
            self._build_brand_previews(body)

        # ---- MODO DE FOTOS ----
        self._section_label(body, "Modo de fotos") \
            .pack(anchor="w", pady=(0, 10))
        for rid, title, sub, _ in MODOS:
            OptionCard(body, self.modo_var, rid, title, sub) \
                .pack(fill="x", pady=4)

        # ---- MARCAS SIN INDIVIDUALES (solo aplica al modo "Grupal si está completa") ----
        self._section_label(body, "Si la referencia no tiene foto individual") \
            .pack(anchor="w", pady=(SECTION_GAP, 10))
        SegmentedControl(body, self.no_ind_var, NO_IND_OPCIONES) \
            .pack(anchor="w")

        # ---- NOMBRE DE LA CARPETA ----
        # Single: un editor.
        # Batch: un editor por PDF (con el nombre del archivo arriba).
        ok_entries = [e for e in self.parsed_data_list if "parsed" in e]
        if ok_entries:
            self._section_label(body, "Nombre de la carpeta") \
                .pack(anchor="w", pady=(SECTION_GAP, 10))

            if len(ok_entries) == 1:
                var = self._get_or_create_override_var(ok_entries[0])
                self._make_name_entry(body, var)
                tk.Label(
                    body,
                    text="Detectado del PDF. Editalo si querés un nombre distinto.",
                    font=FONT_CAPTION, bg=BG, fg=TEXT_LIGHT, anchor="w",
                ).pack(anchor="w", pady=(6, 0))
            else:
                # Una sección por PDF
                for entry in ok_entries:
                    row = tk.Frame(body, bg=BG)
                    row.pack(fill="x", pady=(0, 10))
                    tk.Label(
                        row, text=Path(entry["path"]).name,
                        font=FONT_CAPTION, bg=BG, fg=TEXT_LIGHT, anchor="w",
                    ).pack(anchor="w", pady=(0, 4))
                    var = self._get_or_create_override_var(entry)
                    self._make_name_entry(row, var)

        # ---- CARPETA DESTINO ----
        self._section_label(body, "Carpeta destino") \
            .pack(anchor="w", pady=(SECTION_GAP, 10))
        dest_row = tk.Frame(body, bg=BG)
        dest_row.pack(fill="x")
        self.s2_dest_label = tk.Label(
            dest_row,
            text=self._format_dest_path(self.dest_root_var.get()),
            font=FONT_BODY, bg=BG, fg=TEXT_MUTED, anchor="w",
        )
        self.s2_dest_label.pack(side="left", fill="x", expand=True, padx=(0, 12))
        CanvasButton(
            dest_row, text="Cambiar...",
            command=self._on_pick_dest_root,
            kind="secondary", padx=18,
        ).pack(side="right")

        # Activar mousewheel scroll en todos los descendientes del body
        self._bind_scroll_wheel_to_descendants(body)

    # =========================================================================
    # PANTALLA 3 - Procesando + resultado
    # =========================================================================

    def _start_processing(self):
        modo_id = self.modo_var.get()
        modo = next(m[3] for m in MODOS if m[0] == modo_id)
        use_grupal_no_ind = (self.no_ind_var.get() == "grupal")
        dest_root = self.dest_root_var.get()

        # Persistir preferencias
        user_settings.save({
            "modo": modo_id,
            "no_ind": self.no_ind_var.get(),
            "dest_root": dest_root,
        })

        # Lista de PDFs que parsearon OK (los que tuvieron error en pantalla 1
        # los salteamos)
        ok_entries = [e for e in self.parsed_data_list if "parsed" in e]
        if not ok_entries:
            messagebox.showerror("Sin proformas", "Ningún PDF se pudo leer.")
            return

        # Pre-flight: Dropbox conectado?
        if self.dropbox_status is None or not self.dropbox_status.get("connected"):
            # Re-chequeo sincronico antes de bloquear (puede haberse conectado).
            self._refresh_dropbox_status(force_async=False)
        if not (self.dropbox_status and self.dropbox_status.get("connected")):
            err = (self.dropbox_status or {}).get("error") or \
                  "No encontré la carpeta de Dropbox sincronizada."
            messagebox.showerror("Dropbox no encontrado", err)
            return

        # Validacion previa: ¿hay marcas en las proformas que no existen en
        # Dropbox? Si hay, advertimos al usuario antes de procesar — esos
        # SKUs van a quedar como faltantes.
        if not self._preflight_brands_check(ok_entries):
            return  # usuario cancelo en el dialog

        from collections import Counter
        parent_path = Path(dest_root) if dest_root else (Path.home() / "Desktop" / "Fotos de Proformas")

        # Resolver el nombre deseado para cada PDF: el override del usuario
        # si lo edito, o el cliente detectado como default.
        desired_names = []
        for entry in ok_entries:
            var = self.client_override_vars.get(entry["path"])
            user_name = var.get().strip() if var else ""
            desired_names.append(
                user_name or entry["parsed"].get("client") or "Proforma"
            )

        # Detectar duplicados en el batch (mismo nombre deseado en >1 PDF).
        # Esos van con -1, -2, -3 desde el primero.
        name_counts = Counter(desired_names)

        def _next_free_n(base, taken):
            """Menor N >= 1 tal que <parent>/<base>-N no existe ni esta en `taken`."""
            n = 1
            while True:
                candidate = parent_path / f"{base}-{n}"
                if not candidate.exists() and str(candidate) not in taken:
                    return n
                n += 1

        explicit_names = []
        taken_paths = set()
        for desired in desired_names:
            if name_counts[desired] > 1:
                # Duplicado en el batch → asignar -N libre
                n = _next_free_n(desired, taken_paths)
                taken_paths.add(str(parent_path / f"{desired}-{n}"))
                explicit_names.append(f"{desired}-{n}")
            else:
                # Unico: pasar el nombre como explicit, processor maneja la
                # colision con filesystem (carpeta existente -> -2, -3).
                explicit_names.append(desired)

        self._batch_total = len(ok_entries)
        self._batch_results = []
        self._batch_errors = []
        self.results = []
        # Cancel event compartido con el processor. Se setea desde el
        # boton "Cancelar" en pantalla 3 procesando — el processor lo
        # chequea entre SKUs y corta limpio.
        self.cancel_event = threading.Event()
        # Fade-in al entrar a pantalla 3 — accion de usuario.
        self._goto(self.show_screen3_processing)

        def run():
            for idx, (entry, folder_name) in enumerate(zip(ok_entries, explicit_names), 1):
                # Si cancelaron a mitad del batch, no procesamos las restantes.
                if self.cancel_event.is_set():
                    break
                pdf_path = entry["path"]
                def wrap_progress(cur, total, msg, _idx=idx, _name=Path(pdf_path).name):
                    prefix = f"[{_idx}/{self._batch_total}] {_name} · "
                    self._safe_progress_cb(cur, total, prefix + msg)
                try:
                    res = processor.process(
                        pdf_path, modo,
                        on_progress=wrap_progress,
                        use_grupal_when_no_individuals=use_grupal_no_ind,
                        dest_root=dest_root,
                        dest_folder_name=folder_name,
                        cancel_event=self.cancel_event,
                    )
                    self._batch_results.append(res)
                except Exception as e:
                    self._batch_errors.append({"path": pdf_path, "error": e})

            self.results = list(self._batch_results)
            self.root.after(0, lambda: self._on_batch_done())

        threading.Thread(target=run, daemon=True).start()

    def _on_batch_done(self):
        # Si todos los resultados estan marcados como cancelled, el usuario
        # corto el proceso. Volvemos a pantalla 2 con un toast informativo,
        # no a pantalla 3 result (no hay nada que mostrar).
        cancelled_all = (
            self._batch_results and
            all(r.get("cancelled") for r in self._batch_results) and
            not self._batch_errors
        )
        # Si NO hubo resultados (porque cancel antes del primer SKU) y
        # tampoco errores, tambien fue cancelacion temprana.
        early_cancel = (
            not self._batch_results and not self._batch_errors
            and getattr(self, "cancel_event", None) is not None
            and self.cancel_event.is_set()
        )
        if cancelled_all or early_cancel:
            self.toast("Proceso cancelado")
            self.show_screen2()
            return

        if not self._batch_results and self._batch_errors:
            # Todo fallo
            first = self._batch_errors[0]
            err = first["error"]
            title, body = _friendly_error(err)
            messagebox.showerror(title, body)
            self.show_screen2()
            return
        # Notificación nativa de macOS (útil cuando estás en otra ventana)
        self._show_done_notification()
        self.show_screen3_result()

    def _show_done_notification(self):
        """Notificación nativa de macOS al terminar de procesar."""
        n = len(self._batch_results)
        if n == 0:
            return
        copied = sum(r["copied"] for r in self._batch_results)
        missing = sum(len(r["missing"]) for r in self._batch_results)

        if n == 1:
            r = self._batch_results[0]
            client = Path(str(r["dest"])).name
            title = f"Listo · {client}"
        else:
            title = f"Listo · {n} proformas"

        if missing:
            body = f"{copied} fotos copiadas, {missing} faltantes."
        else:
            body = f"{copied} fotos copiadas. Todas encontradas."

        # Notificación nativa segun el SO (macOS/Windows/Linux). Si el SO
        # no soporta o falla, no rompe el flujo - es nice-to-have.
        platform_utils.show_notification(title, body)

    def _safe_progress_cb(self, current, total, msg):
        self.root.after(0, lambda: self._update_progress(current, total, msg))

    def _update_progress(self, current, total, msg):
        if self.s3_pct_label and self.s3_pct_label.winfo_exists():
            pct = (current / total * 100) if total else 0
            self.s3_pct_label.configure(text=f"{int(pct)}%")
        if self.s3_count_label and self.s3_count_label.winfo_exists():
            self.s3_count_label.configure(text=f"{current} de {total}")

        # Status detallado: el processor emite mensajes como
        # "[1/3] file.pdf · BRAND · SKU". Separamos la parte BRAND · SKU
        # (detalle visual del SKU actual) del prefijo del batch para que el
        # usuario vea exactamente que se esta procesando en cada momento.
        status_text, detail_text = _split_status_detail(msg)
        if self.s3_msg_label and self.s3_msg_label.winfo_exists():
            self.s3_msg_label.configure(text=status_text)
        if hasattr(self, "s3_detail_label") and self.s3_detail_label and self.s3_detail_label.winfo_exists():
            self.s3_detail_label.configure(text=detail_text)
        # Guardamos el ratio para poder redibujar la barra cuando la ventana
        # se redimensiona (ver _redraw_progress_bar).
        ratio = (current / total) if total else 0
        self._progress_ratio = max(0.0, min(1.0, ratio))
        self._redraw_progress_bar()

    def _redraw_progress_bar(self):
        """Redibuja la barra de progreso usando el ratio guardado. Se llama
        desde _update_progress (cuando avanza el proceso) y desde el handler
        <Configure> del canvas (cuando el usuario redimensiona la ventana)."""
        if not (self.s3_canvas and self.s3_canvas.winfo_exists()):
            return
        ratio = getattr(self, "_progress_ratio", 0.0)
        w = int(self.s3_canvas.winfo_width() * ratio)
        self.s3_canvas.coords(self.s3_bar, 0, 0, w, 6)

    def show_screen3_processing(self):
        self._clear()
        self._current_screen = "processing"
        self._header(
            "Procesando",
            "Buscando fotos en Dropbox y copiando al escritorio.",
        )

        body = tk.Frame(self.container, bg=BG)
        body.pack(fill="both", expand=True, padx=SCREEN_PADX, pady=20)

        card = Card(body)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(padx=28, pady=32, fill="x")

        top_row = tk.Frame(inner, bg=SURFACE)
        top_row.pack(fill="x")
        self.s3_pct_label = tk.Label(
            top_row, text="0%", font=FONT_DISPLAY, bg=SURFACE, fg=TEXT,
        )
        self.s3_pct_label.pack(side="left")
        self.s3_count_label = tk.Label(
            top_row, text="0 de 0", font=FONT_BODY,
            bg=SURFACE, fg=TEXT_MUTED,
        )
        self.s3_count_label.pack(side="right")

        self.s3_canvas = tk.Canvas(
            inner, height=6, bg=BG, highlightthickness=0,
        )
        self.s3_canvas.pack(fill="x", pady=(14, 12))
        self.s3_bar = self.s3_canvas.create_rectangle(
            0, 0, 0, 6, fill=ACCENT, width=0,
        )
        # Redibujar la barra cuando el canvas cambia de ancho (resize de ventana)
        # — mantiene el porcentaje proporcional al nuevo ancho.
        self.s3_canvas.bind("<Configure>", lambda e: self._redraw_progress_bar())

        self.s3_msg_label = tk.Label(
            inner, text="Iniciando…", font=FONT_CAPTION,
            bg=SURFACE, fg=TEXT_MUTED, anchor="w",
            wraplength=WINDOW_W - 2 * SCREEN_PADX - 60, justify="left",
        )
        self.s3_msg_label.pack(anchor="w", fill="x", pady=(0, 4))
        _bind_dynamic_wraplength(self.s3_msg_label, margin=8)

        # Mensaje secundario: detalle del SKU actual. El processor emite
        # "{brand} · {sku}" en cada paso — lo separamos del estado general
        # para que el usuario vea exactamente que se esta buscando ahora.
        self.s3_detail_label = tk.Label(
            inner, text="", font=FONT_BODY_BOLD,
            bg=SURFACE, fg=TEXT, anchor="w",
        )
        self.s3_detail_label.pack(anchor="w", fill="x")
        _bind_dynamic_wraplength(self.s3_detail_label, margin=8)

        # Botón Cancelar abajo de la card (alineado a la derecha del body).
        # Setea el cancel_event que el processor revisa entre SKUs.
        cancel_row = tk.Frame(body, bg=BG)
        cancel_row.pack(fill="x", pady=(SECTION_GAP, 0))
        self.s3_cancel_btn = CanvasButton(
            cancel_row, text="Cancelar",
            command=self._on_cancel_processing, kind="secondary",
        )
        self.s3_cancel_btn.pack(side="right")

    def _on_cancel_processing(self):
        """Setea el cancel_event para que el processor pare entre SKUs.
        No mata el thread bruto — espera el corte limpio. UI muestra
        feedback inmediato cambiando el texto del label."""
        if getattr(self, "cancel_event", None) is not None:
            self.cancel_event.set()
        # Actualizar UI: deshabilitar el boton y avisar al usuario.
        if hasattr(self, "s3_cancel_btn") and self.s3_cancel_btn.winfo_exists():
            self.s3_cancel_btn.set_enabled(False)
        if hasattr(self, "s3_msg_label") and self.s3_msg_label.winfo_exists():
            self.s3_msg_label.configure(text="Cancelando…")

    def _on_processing_done(self, res, exc):
        if exc:
            title, body = _friendly_error(exc)
            messagebox.showerror(title, body)
            self.show_screen2()
            return
        self.result = res
        self.show_screen3_result()

    def show_screen3_result(self):
        self._clear()
        self._current_screen = "result"
        self.s3_canvas = None
        self.s3_pct_label = None
        self.s3_count_label = None
        self.s3_msg_label = None
        self.s3_detail_label = None
        self.s3_cancel_btn = None

        # Si hay >1 resultado, render batch. Si hay 1, render single (igual que antes).
        if len(self.results) > 1:
            self._render_screen3_batch()
            return

        copied = self.result["copied"]
        missing = self.result["missing"]
        total = self.result["total_skus"]
        dest = self.result["dest"]

        if missing:
            title = "Listo, con algunas faltantes"
            subtitle = f"{copied} fotos copiadas, {len(missing)} no encontradas."
        else:
            title = "Listo!"
            subtitle = f"{copied} fotos copiadas. Todas encontradas."
        self._header(title, subtitle)

        # Footer primero
        footer = tk.Frame(self.container, bg=BG)
        footer.pack(side="bottom", fill="x", padx=SCREEN_PADX, pady=24)

        CanvasButton(
            footer, text="←  Volver",
            command=lambda: self._goto(self._back_to_filters), kind="secondary",
        ).pack(side="left")

        CanvasButton(
            footer, text="Abrir carpeta  →",
            command=self._open_dest, kind="primary",
        ).pack(side="right")

        CanvasButton(
            footer, text="Procesar otra",
            command=lambda: self._goto(self._reset_to_start), kind="secondary",
        ).pack(side="right", padx=(0, 8))

        body = tk.Frame(self.container, bg=BG)
        body.pack(fill="both", expand=True, padx=SCREEN_PADX)

        # Stats card
        stats = Card(body)
        stats.pack(fill="x", pady=(0, ELEMENT_GAP))
        si = tk.Frame(stats, bg=SURFACE)
        si.pack(padx=24, pady=20, fill="x")

        for lbl, val, color in [
            ("SKUs en proforma", str(total), TEXT),
            ("Copiadas", str(copied), SUCCESS if copied else ERROR),
            ("Faltantes", str(len(missing)), ERROR if missing else SUCCESS),
        ]:
            col = tk.Frame(si, bg=SURFACE)
            col.pack(side="left", expand=True, fill="x")
            tk.Label(col, text=val, font=F(26, "bold"), bg=SURFACE, fg=color) \
                .pack(anchor="w")
            tk.Label(col, text=lbl, font=FONT_CAPTION, bg=SURFACE, fg=TEXT_MUTED) \
                .pack(anchor="w", pady=(2, 0))

        # Card destino
        dest_card = Card(body)
        dest_card.pack(fill="x", pady=(0, ELEMENT_GAP))
        di = tk.Frame(dest_card, bg=SURFACE)
        di.pack(padx=24, pady=18, fill="x")
        tk.Label(di, text="CARPETA DESTINO", font=FONT_SECTION_LABEL,
                 bg=SURFACE, fg=TEXT_LIGHT, anchor="w").pack(anchor="w", fill="x")
        dest_lbl = tk.Label(
            di, text=str(dest), font=FONT_BODY,
            bg=SURFACE, fg=TEXT, anchor="w",
            wraplength=WINDOW_W - 2 * SCREEN_PADX - 60, justify="left",
        )
        dest_lbl.pack(anchor="w", fill="x", pady=(4, 0))
        _bind_dynamic_wraplength(dest_lbl, margin=8)

        # Lista de faltantes scrolleable + boton "Copiar todos"
        if missing:
            mc = Card(body)
            mc.pack(fill="both", expand=True)
            mi = tk.Frame(mc, bg=SURFACE)
            mi.pack(padx=24, pady=18, fill="both", expand=True)

            sorted_missing = sorted(missing, key=lambda m: -m["qty"])

            # Header con label + boton "Copiar todos" a la derecha
            header_row = tk.Frame(mi, bg=SURFACE)
            header_row.pack(fill="x", pady=(0, 8))
            tk.Label(
                header_row, text=f"FALTANTES ({len(sorted_missing)})",
                font=FONT_SECTION_LABEL, bg=SURFACE, fg=TEXT_LIGHT, anchor="w",
            ).pack(side="left")
            self._copy_all_btn = CanvasButton(
                header_row, text="Copiar todos",
                command=lambda: self._copy_all_missing(sorted_missing),
                kind="secondary", padx=14, height=28, font=FONT_CAPTION,
                parent_bg=SURFACE,
            )
            self._copy_all_btn.pack(side="right")

            # Hint
            tk.Label(
                mi,
                text="Tocá un faltante para copiar el código al portapapeles.",
                font=FONT_CAPTION, bg=SURFACE, fg=TEXT_LIGHT, anchor="w",
            ).pack(anchor="w", pady=(0, 8))

            self._build_scrollable_missing_list(mi, sorted_missing)

    def _render_screen3_batch(self):
        """Pantalla 3 cuando se procesaron varias proformas (batch)."""
        n = len(self.results)
        total_copied = sum(r["copied"] for r in self.results)
        total_missing = sum(len(r["missing"]) for r in self.results)
        total_skus = sum(r["total_skus"] for r in self.results)

        if total_missing:
            title = f"Listo · {n} proformas"
            subtitle = f"{total_copied} fotos copiadas, {total_missing} no encontradas."
        else:
            title = f"Listo! · {n} proformas"
            subtitle = f"{total_copied} fotos copiadas. Todas encontradas."
        self._header(title, subtitle)

        # Footer
        footer = tk.Frame(self.container, bg=BG)
        footer.pack(side="bottom", fill="x", padx=SCREEN_PADX, pady=24)
        CanvasButton(
            footer, text="←  Volver",
            command=lambda: self._goto(self._back_to_filters), kind="secondary",
        ).pack(side="left")
        CanvasButton(
            footer, text="Abrir todas  →",
            command=self._open_all_dests, kind="primary",
        ).pack(side="right")
        CanvasButton(
            footer, text="Procesar otra",
            command=lambda: self._goto(self._reset_to_start), kind="secondary",
        ).pack(side="right", padx=(0, 8))

        body = tk.Frame(self.container, bg=BG)
        body.pack(fill="both", expand=True, padx=SCREEN_PADX)

        # Stats agregadas
        stats = Card(body)
        stats.pack(fill="x", pady=(0, ELEMENT_GAP))
        si = tk.Frame(stats, bg=SURFACE)
        si.pack(padx=24, pady=20, fill="x")
        for lbl, val, color in [
            ("Proformas", str(n), TEXT),
            ("SKUs en total", str(total_skus), TEXT),
            ("Copiadas", str(total_copied), SUCCESS if total_copied else ERROR),
            ("Faltantes", str(total_missing), ERROR if total_missing else SUCCESS),
        ]:
            col = tk.Frame(si, bg=SURFACE)
            col.pack(side="left", expand=True, fill="x")
            tk.Label(col, text=val, font=F(22, "bold"), bg=SURFACE, fg=color) \
                .pack(anchor="w")
            tk.Label(col, text=lbl, font=FONT_CAPTION, bg=SURFACE, fg=TEXT_MUTED) \
                .pack(anchor="w", pady=(2, 0))

        # Lista scrolleable de carpetas creadas
        mc = Card(body)
        mc.pack(fill="x", pady=(0, ELEMENT_GAP))
        mi = tk.Frame(mc, bg=SURFACE)
        mi.pack(padx=24, pady=18, fill="x")
        tk.Label(
            mi, text=f"CARPETAS CREADAS ({n})",
            font=FONT_SECTION_LABEL, bg=SURFACE, fg=TEXT_LIGHT, anchor="w",
        ).pack(anchor="w", pady=(0, 8))

        self._build_scrollable_results_list(mi, self.results)

        # Sección de faltantes agregada (todas las proformas en una lista)
        # con la proforma de origen mostrada debajo de cada SKU.
        all_missing = []
        for r in self.results:
            proforma_name = Path(str(r["dest"])).name
            for m in r["missing"]:
                all_missing.append({**m, "proforma": proforma_name})

        if all_missing:
            sorted_all = sorted(all_missing, key=lambda m: -m["qty"])
            mc2 = Card(body)
            mc2.pack(fill="both", expand=True)
            mi2 = tk.Frame(mc2, bg=SURFACE)
            mi2.pack(padx=24, pady=18, fill="both", expand=True)

            # Header con label + boton Copiar todos
            header_row = tk.Frame(mi2, bg=SURFACE)
            header_row.pack(fill="x", pady=(0, 8))
            tk.Label(
                header_row, text=f"FALTANTES ({len(sorted_all)})",
                font=FONT_SECTION_LABEL, bg=SURFACE, fg=TEXT_LIGHT, anchor="w",
            ).pack(side="left")
            self._copy_all_btn = CanvasButton(
                header_row, text="Copiar todos",
                command=lambda: self._copy_all_missing(sorted_all),
                kind="secondary", padx=14, height=28, font=FONT_CAPTION,
                parent_bg=SURFACE,
            )
            self._copy_all_btn.pack(side="right")

            tk.Label(
                mi2,
                text="Tocá un faltante para copiar el código al portapapeles.",
                font=FONT_CAPTION, bg=SURFACE, fg=TEXT_LIGHT, anchor="w",
            ).pack(anchor="w", pady=(0, 8))

            self._build_scrollable_missing_list(
                mi2, sorted_all,
                show_proforma=False,    # ya está agrupado por proforma
                group_by_proforma=True,
            )

    def _open_all_dests(self):
        """Abre todas las carpetas destino en el explorador del SO
        (Finder en macOS, Explorer en Windows, xdg-open en Linux)."""
        for r in self.results:
            platform_utils.open_in_explorer(r["dest"])

    def _make_open_dest_handler(self, dest_path):
        """Devuelve un handler que abre SOLO esta carpeta (no otras).
        Usar este factory evita closures raros con lambdas en loops."""
        dest_str = str(dest_path)
        def handler():
            ok = platform_utils.open_in_explorer(dest_str)
            if not ok:
                messagebox.showerror(
                    "No pude abrir la carpeta",
                    f"No se pudo abrir:\n{dest_str}",
                )
        return handler

    def _build_scrollable_results_list(self, parent, results):
        """Lista scrolleable de resultados batch (vertical Y horizontal).
        Click en una fila abre la carpeta. Si un nombre de carpeta es muy
        largo, la fila puede scrollearse horizontalmente para verlo entero."""
        wrap = tk.Frame(parent, bg=SURFACE)
        wrap.pack(fill="both", expand=True)

        # Grid 2x2: canvas + vbar a la derecha + hbar abajo.
        canvas = tk.Canvas(
            wrap, bg=SURFACE, highlightthickness=0, bd=0, height=200,
        )
        vbar = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        hbar = tk.Scrollbar(wrap, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        inner = tk.Frame(canvas, bg=SURFACE)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_inner_configure)

        def _on_canvas_configure(event):
            # El inner debe ser al menos tan ancho como el canvas (para que
            # fill='x' en las filas funcione cuando hay espacio sobrante).
            # Si el contenido natural es mayor, dejamos que se ensanche para
            # habilitar scroll horizontal.
            req = inner.winfo_reqwidth()
            canvas.itemconfig(win_id, width=max(event.width, req))
        canvas.bind("<Configure>", _on_canvas_configure)

        for r in results:
            row = tk.Frame(inner, bg=SURFACE)
            row.pack(fill="x", pady=4)

            dest_name = Path(str(r["dest"])).name
            n_copied = r["copied"]
            n_missing = len(r["missing"])

            tk.Label(
                row, text=dest_name, font=FONT_BODY,
                bg=SURFACE, fg=TEXT, anchor="w",
            ).pack(side="left", fill="x", expand=True)

            stat_color = SUCCESS if n_missing == 0 else ERROR
            tk.Label(
                row, text=f"{n_copied} copiadas · {n_missing} faltantes",
                font=FONT_CAPTION, bg=SURFACE, fg=stat_color, anchor="e",
            ).pack(side="right", padx=(8, 12))

            # Mini boton "Abrir" para cada fila. Usamos factory para evitar
            # cualquier closure raro con lambdas en loops.
            btn = CanvasButton(
                row, text="Abrir",
                command=self._make_open_dest_handler(r["dest"]),
                kind="secondary", padx=14, height=30, font=FONT_CAPTION,
                parent_bg=SURFACE,
            )
            btn.pack(side="right")

        # Mouse wheel vertical.
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * event.delta), "units")
        def _bind_wheel(widget):
            widget.bind("<MouseWheel>", _on_mousewheel)
            for child in widget.winfo_children():
                _bind_wheel(child)
        canvas.bind("<MouseWheel>", _on_mousewheel)
        _bind_wheel(inner)

    def _build_scrollable_missing_list(self, parent, sorted_missing,
                                        show_proforma=False,
                                        group_by_proforma=False):
        """Crea una lista scrolleable con todos los faltantes.
        - show_proforma: cada fila muestra el nombre de la proforma debajo.
        - group_by_proforma: agrupa las filas por proforma con un header
          por grupo (usado en pantalla 3 batch).
        """
        # Container con canvas y scrollbar al lado
        wrap = tk.Frame(parent, bg=SURFACE)
        wrap.pack(fill="both", expand=True)

        canvas = tk.Canvas(
            wrap, bg=SURFACE, highlightthickness=0, bd=0,
            height=180,  # alto fijo, el scroll baja por adentro
        )
        scrollbar = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Frame interno donde van las filas
        inner = tk.Frame(canvas, bg=SURFACE)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        # Cuando el contenido cambia, actualizar el scrollregion
        def _on_inner_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_inner_configure)

        # Cuando el canvas se redimensiona, ajustar el ancho del frame interno
        def _on_canvas_configure(event):
            canvas.itemconfig(win_id, width=event.width)
        canvas.bind("<Configure>", _on_canvas_configure)

        HOVER_BG = "#F2F2F4"   # gris muy sutil para hover

        def render_row(parent_frame, m):
            """Renderiza una fila clickeable de faltante."""
            row = tk.Frame(parent_frame, bg=SURFACE)
            row.pack(fill="x", pady=2)

            main = tk.Frame(row, bg=SURFACE)
            main.pack(fill="x")
            qty_lbl = tk.Label(
                main, text=f"{m['qty']:>4}", font=FONT_MONO,
                bg=SURFACE, fg=TEXT, width=5, anchor="e",
            )
            qty_lbl.pack(side="left")
            pares_lbl = tk.Label(
                main, text="pares", font=FONT_CAPTION,
                bg=SURFACE, fg=TEXT_LIGHT, anchor="w",
            )
            pares_lbl.pack(side="left", padx=(2, 12))
            sku_lbl = tk.Label(
                main, text=m["sku"], font=FONT_MONO,
                bg=SURFACE, fg=TEXT, anchor="w",
            )
            sku_lbl.pack(side="left", fill="x", expand=True)
            reason_lbl = tk.Label(
                main, text=m["reason"], font=FONT_CAPTION,
                bg=SURFACE, fg=TEXT_LIGHT, anchor="e",
            )
            reason_lbl.pack(side="right", padx=(8, 0))

            children = [row, main, qty_lbl, pares_lbl, sku_lbl, reason_lbl]

            # Segunda linea con la proforma de origen (solo batch sin grouping)
            if show_proforma and m.get("proforma"):
                sub = tk.Frame(row, bg=SURFACE)
                sub.pack(fill="x")
                tk.Frame(sub, bg=SURFACE, width=72).pack(side="left")
                proforma_lbl = tk.Label(
                    sub, text=f"↳  {m['proforma']}", font=FONT_CAPTION,
                    bg=SURFACE, fg=TEXT_LIGHT, anchor="w",
                )
                proforma_lbl.pack(side="left", fill="x", expand=True)
                children.extend([sub, proforma_lbl])

            def _make_handlers(sku=m["sku"], children=tuple(children), sku_lbl=sku_lbl):
                def on_enter(e):
                    for w in children:
                        try: w.configure(bg=HOVER_BG)
                        except tk.TclError: pass
                def on_leave(e):
                    for w in children:
                        try: w.configure(bg=SURFACE)
                        except tk.TclError: pass
                def on_click(e):
                    self._copy_sku_to_clipboard(sku, sku_lbl)
                return on_enter, on_leave, on_click

            on_enter, on_leave, on_click = _make_handlers()
            for w in children:
                w.bind("<Enter>", on_enter)
                w.bind("<Leave>", on_leave)
                w.bind("<Button-1>", on_click)

        if group_by_proforma:
            # Agrupar por proforma manteniendo orden por mayor cantidad total
            from collections import OrderedDict
            groups = OrderedDict()
            for m in sorted_missing:
                key = m.get("proforma") or "(sin proforma)"
                groups.setdefault(key, []).append(m)

            # Render cada grupo con header
            first_group = True
            for proforma_name, items in groups.items():
                # Sumar pares del grupo para el header
                total_pares = sum(it["qty"] for it in items)
                header = tk.Frame(inner, bg=SURFACE)
                header.pack(fill="x", pady=((4 if first_group else 14), 6))
                first_group = False
                tk.Label(
                    header, text=proforma_name, font=FONT_BODY_BOLD,
                    bg=SURFACE, fg=TEXT, anchor="w",
                ).pack(side="left")
                tk.Label(
                    header,
                    text=f"  ·  {len(items)} faltante{'s' if len(items)!=1 else ''}  ·  {total_pares} pares",
                    font=FONT_CAPTION, bg=SURFACE, fg=TEXT_LIGHT, anchor="w",
                ).pack(side="left")
                for m in items:
                    render_row(inner, m)
        else:
            for m in sorted_missing:
                render_row(inner, m)

        # Scroll con mouse wheel / trackpad. Tk usa <MouseWheel> en macOS;
        # event.delta positivo = arriba.
        def _on_mousewheel(event):
            # En macOS event.delta es chico (1, 2, 3...). Multiplicar para que
            # se sienta natural.
            canvas.yview_scroll(int(-1 * event.delta), "units")

        # Bindear el wheel sobre canvas Y todos los hijos para que ande dentro
        # del listado tambien.
        def _bind_wheel(widget):
            widget.bind("<MouseWheel>", _on_mousewheel)
            for child in widget.winfo_children():
                _bind_wheel(child)
        canvas.bind("<MouseWheel>", _on_mousewheel)
        _bind_wheel(inner)

    def _open_dest(self):
        if not self.result:
            return
        ok = platform_utils.open_in_explorer(self.result["dest"])
        if not ok:
            messagebox.showerror(
                "No pude abrir la carpeta",
                f"No se pudo abrir:\n{self.result['dest']}",
            )

    # ---------- Copiar al portapapeles ---------------------------------------

    def _copy_to_clipboard(self, text):
        """Copia text al portapapeles del sistema."""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()  # asegura que persista al cerrar
        except tk.TclError:
            pass

    def _copy_sku_to_clipboard(self, sku, label_widget):
        """Copia un SKU y muestra feedback breve en el label correspondiente."""
        self._copy_to_clipboard(sku)
        try:
            label_widget.configure(text="✓  Copiado", fg=SUCCESS)
            self.root.after(
                1000,
                lambda: label_widget.winfo_exists()
                and label_widget.configure(text=sku, fg=TEXT),
            )
        except tk.TclError:
            pass

    def _copy_all_missing(self, sorted_missing):
        """Copia todos los SKUs faltantes (uno por linea) al portapapeles.
        Cambia brevemente el texto del boton para confirmar."""
        text = "\n".join(m["sku"] for m in sorted_missing)
        self._copy_to_clipboard(text)
        btn = getattr(self, "_copy_all_btn", None)
        if btn is not None:
            try:
                original = btn._text
                btn._text = f"✓  Copiados ({len(sorted_missing)})"
                btn._draw()
                def restore(_btn=btn, _orig=original):
                    if _btn.winfo_exists():
                        _btn._text = _orig
                        _btn._draw()
                self.root.after(1200, restore)
            except tk.TclError:
                pass

    def _back_to_filters(self):
        """Vuelve a pantalla 2 (filtros) manteniendo PDF y filtros cargados.
        Se usa cuando el usuario ve el resultado y quiere reintentar con
        otros filtros sobre la misma proforma sin tener que volver a elegir."""
        # No tocamos pdf_path, parsed_data, modo_var, no_ind_var, dest_root_var.
        # Mantenemos result para que sea reutilizable, lo limpio si quiere
        # procesar (al apretar Procesar de nuevo se sobreescribe).
        self.show_screen2()

    def _reset_to_start(self):
        self.pdf_path = None
        self.parsed_data = None
        self.result = None
        self.show_screen1()

    def _on_tk_callback_exception(self, exc_type, exc_value, exc_tb):
        """Atrapa excepciones en callbacks de tk (clicks, eventos, etc)."""
        import traceback, datetime
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log_path = platform_utils.app_log_path()
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n=== {datetime.datetime.now()} TK_CALLBACK ===\n")
                f.write(tb_str)
        except OSError:
            pass
        try:
            messagebox.showerror(
                "Error inesperado",
                f"Algo salió mal:\n\n{exc_type.__name__}: {exc_value}\n\n"
                f"Detalle en:\n{log_path}",
            )
        except Exception:
            pass

    def run(self):
        # Setup ya terminó. Aplicamos un update_idletasks() para materializar
        # los widgets y aplicamos el tamaño/posicion guardado de la sesion
        # anterior (o el default centrado si es primer arranque).
        self.root.update_idletasks()
        self._restore_geometry()
        self.root.deiconify()
        self.root.lift()
        self.root.mainloop()

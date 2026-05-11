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


# =============================================================================
# Paleta
# =============================================================================

BG          = "#F5F5F7"   # fondo de la ventana
SURFACE     = "#FFFFFF"   # cards / superficies
TEXT        = "#1D1D1F"   # texto principal
TEXT_MUTED  = "#6E6E73"   # texto secundario
TEXT_LIGHT  = "#86868B"   # texto terciario / labels de seccion

ACCENT       = "#0066CC"
ACCENT_HOVER = "#0058B5"
ACCENT_TINT  = "#E8F1FC"

BORDER          = "#D2D2D7"
BORDER_STRONG   = "#A8A8AC"

DISABLED_BG = "#E5E5EA"
DISABLED_FG = "#A8A8AC"

SUCCESS = "#30A46C"
ERROR   = "#E5484D"


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

WINDOW_W = 760
WINDOW_H = 800   # cabe en pantalla del usuario (982 - menubar 28 - dock ~80 = ~874 usable)

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


class CanvasButton(tk.Canvas):
    """Botón custom dibujado en Canvas. Soporta hover, disabled y bordes
    redondeados. Tres variantes: primary (azul), secondary (gris claro),
    text (sin fondo, texto azul)."""

    KINDS = {
        "primary":   {"bg": ACCENT,        "fg": "#FFFFFF", "hover_bg": ACCENT_HOVER},
        "secondary": {"bg": SURFACE,       "fg": TEXT,      "hover_bg": "#EDEDED",
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
        self.cfg = self.KINDS[kind].copy()
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
# Card - frame blanco con borde sutil
# =============================================================================

class Card(tk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(
            parent, bg=SURFACE,
            highlightbackground=BORDER, highlightcolor=BORDER,
            highlightthickness=1, **kwargs,
        )


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
            bg = "#FAFAFA"
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
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.root.minsize(WINDOW_W, WINDOW_H)
        self.root.maxsize(WINDOW_W, WINDOW_H)
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}")

        # Cargar preferencias guardadas (modo, no_ind, dest_root)
        prefs = user_settings.load()

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

        self.show_screen1()

        # Registrar drop target en TODA la ventana (no en la bandeja). En
        # macOS los Canvas widgets reciben drops de forma poco confiable;
        # el root window siempre los recibe. Bonus: el usuario puede arrastrar
        # a cualquier lado de la ventana, no solo a la bandeja.
        self._wire_root_dnd()

        # Si la app fue invocada con PDF(s) como argumento (drag al .app desde
        # Finder antes de que la app este abierta) los cargamos.
        argv_pdfs = [
            a for a in sys.argv[1:]
            if a.lower().endswith(".pdf") and Path(a).exists()
        ]
        if argv_pdfs:
            self.root.after(100, lambda paths=argv_pdfs: self._load_pdf_paths(paths))

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
        ).pack(anchor="w")
        if subtitle:
            tk.Label(
                wrap, text=subtitle, font=FONT_SUBTITLE,
                bg=BG, fg=TEXT_MUTED, anchor="w", justify="left",
                wraplength=WINDOW_W - 2 * SCREEN_PADX,
            ).pack(anchor="w", pady=(4, 0))

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
        self._header(
            "Fotos Proforma",
            "Cargá una proforma en PDF y armo la carpeta de fotos para WhatsApp.",
        )

        # Footer primero (asi reserva su espacio antes que el body)
        footer = tk.Frame(self.container, bg=BG)
        footer.pack(side="bottom", fill="x", padx=SCREEN_PADX, pady=24)

        self.s1_next_btn = CanvasButton(
            footer, text="Continuar  →",
            command=self.show_screen2, kind="primary",
        )
        self._set_next_enabled(False)
        self.s1_next_btn.pack(side="right")

        # Versión chiquita a la izquierda del footer (útil para soporte)
        tk.Label(
            footer, text=f"v{APP_VERSION}",
            font=FONT_CAPTION, bg=BG, fg=TEXT_LIGHT,
        ).pack(side="left")

        # Body con scrollable area si hace falta (por ahora sin scroll)
        body = tk.Frame(self.container, bg=BG)
        body.pack(fill="both", expand=True, padx=SCREEN_PADX)

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
            inner, text="Sin archivo seleccionado.",
            font=FONT_OPTION_SUB,
            bg=SURFACE, fg=TEXT_LIGHT, anchor="w",
            wraplength=WINDOW_W - 2 * SCREEN_PADX - 60, justify="left",
        )
        self.s1_path_label.pack(anchor="w", pady=(4, 6))

        tk.Label(
            inner,
            text="Podés elegir una o varias proformas, y agregar más después.",
            font=FONT_CAPTION, bg=SURFACE, fg=TEXT_LIGHT, anchor="w",
        ).pack(anchor="w", pady=(0, 14))

        # Drop zone — bandeja con borde punteado donde se arrastran PDFs.
        # Click en cualquier parte = mismo flujo que el boton (filedialog).
        # Los DROPS los recibe el root window (no la bandeja en si — en macOS
        # los Canvas widgets reciben eventos DnD de forma poco confiable),
        # ver App._wire_root_dnd. Esta bandeja solo dibuja y se pinta azul
        # cuando hay un drag-over en cualquier parte de la ventana.
        self.s1_drop_zone = DropZone(
            inner,
            on_click=self._on_pick_pdf,
            parent_bg=SURFACE,
            height=120,
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
        Filtra solo PDFs que existen en disco y los suma al batch actual.
        Si arrastran algo que no es PDF (ej. una imagen), mostramos un aviso
        para que el usuario sepa por que no paso nada."""
        valid = [p for p in paths if p.lower().endswith(".pdf") and Path(p).is_file()]
        if not valid:
            messagebox.showinfo(
                "Sin PDF",
                "Solo acepto archivos .pdf — arrastrá uno o varios PDFs.",
            )
            return
        self._add_pdf_paths(valid)

    def _add_pdf_paths(self, paths):
        """Agrega PDFs a la lista actual (sin reemplazar). Parsea solo los
        nuevos. Filtra duplicados con lo ya cargado."""
        if not hasattr(self, "s1_path_label") or not self.s1_path_label.winfo_exists():
            self.show_screen1()

        existing = set(self.pdf_paths)
        new = [p for p in paths if p not in existing]
        if not new:
            return  # ya estaban todos cargados

        self.pdf_paths.extend(new)
        for p in new:
            entry = {"path": p}
            try:
                entry["parsed"] = pdf_dispatch.parse(p)
            except pdf_parser.ParseError as e:
                entry["error"] = str(e)
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {e}"
            self.parsed_data_list.append(entry)
            # Crear StringVar para este PDF si parseo OK, con el cliente
            # detectado como default. Solo si no existe ya (pegajoso entre
            # adiciones/back-forward).
            if "parsed" in entry and p not in self.client_override_vars:
                detected = entry["parsed"].get("client") or ""
                self.client_override_vars[p] = tk.StringVar(value=detected)

        # Si todos los archivos (viejos + nuevos) fallaron, mostrar error.
        errored = [e for e in self.parsed_data_list if "error" in e]
        if errored and len(errored) == len(self.parsed_data_list):
            messagebox.showerror("No pude leer los PDFs", errored[0]["error"])

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
            self.s1_path_label.configure(text="Sin archivo seleccionado.", fg=TEXT_LIGHT)
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
            tk.Label(
                inner, text=entry["error"], font=FONT_BODY,
                bg=SURFACE, fg=TEXT_MUTED, anchor="w", justify="left",
                wraplength=WINDOW_W - 2 * SCREEN_PADX - 60,
            ).pack(anchor="w", pady=(4, 0))
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
            command=self.show_screen1, kind="secondary",
        ).pack(side="left")

        CanvasButton(
            footer, text="Procesar  →",
            command=self._start_processing, kind="primary",
        ).pack(side="right")

        # Body scrolleable: si el contenido excede el alto disponible, el
        # usuario puede scrollear con el trackpad/mouse wheel.
        body = self._make_scrollable_body(self.container, padx=SCREEN_PADX)

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
        self.show_screen3_processing()

        def run():
            for idx, (entry, folder_name) in enumerate(zip(ok_entries, explicit_names), 1):
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
                    )
                    self._batch_results.append(res)
                except Exception as e:
                    self._batch_errors.append({"path": pdf_path, "error": e})

            self.results = list(self._batch_results)
            self.root.after(0, lambda: self._on_batch_done())

        threading.Thread(target=run, daemon=True).start()

    def _on_batch_done(self):
        if not self._batch_results and self._batch_errors:
            # Todo fallo
            first = self._batch_errors[0]
            err = first["error"]
            messagebox.showerror(
                "Error procesando",
                f"{type(err).__name__}: {err}",
            )
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
        if self.s3_msg_label and self.s3_msg_label.winfo_exists():
            self.s3_msg_label.configure(text=msg)
        if self.s3_canvas and self.s3_canvas.winfo_exists():
            ratio = (current / total) if total else 0
            ratio = max(0.0, min(1.0, ratio))
            w = int(self.s3_canvas.winfo_width() * ratio)
            self.s3_canvas.coords(self.s3_bar, 0, 0, w, 6)

    def show_screen3_processing(self):
        self._clear()
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

        self.s3_msg_label = tk.Label(
            inner, text="Iniciando…", font=FONT_CAPTION,
            bg=SURFACE, fg=TEXT_MUTED, anchor="w",
            wraplength=WINDOW_W - 2 * SCREEN_PADX - 60, justify="left",
        )
        self.s3_msg_label.pack(anchor="w")

    def _on_processing_done(self, res, exc):
        if exc:
            messagebox.showerror(
                "Error procesando",
                f"{type(exc).__name__}: {exc}",
            )
            self.show_screen2()
            return
        self.result = res
        self.show_screen3_result()

    def show_screen3_result(self):
        self._clear()
        self.s3_canvas = None
        self.s3_pct_label = None
        self.s3_count_label = None
        self.s3_msg_label = None

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
            command=self._back_to_filters, kind="secondary",
        ).pack(side="left")

        CanvasButton(
            footer, text="Abrir carpeta  →",
            command=self._open_dest, kind="primary",
        ).pack(side="right")

        CanvasButton(
            footer, text="Procesar otra",
            command=self._reset_to_start, kind="secondary",
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
                 bg=SURFACE, fg=TEXT_LIGHT, anchor="w").pack(anchor="w")
        tk.Label(
            di, text=str(dest), font=FONT_BODY,
            bg=SURFACE, fg=TEXT, anchor="w",
            wraplength=WINDOW_W - 2 * SCREEN_PADX - 60, justify="left",
        ).pack(anchor="w", pady=(4, 0))

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
            command=self._back_to_filters, kind="secondary",
        ).pack(side="left")
        CanvasButton(
            footer, text="Abrir todas  →",
            command=self._open_all_dests, kind="primary",
        ).pack(side="right")
        CanvasButton(
            footer, text="Procesar otra",
            command=self._reset_to_start, kind="secondary",
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
        """Lista scrolleable de resultados batch. Click en una fila abre la carpeta."""
        wrap = tk.Frame(parent, bg=SURFACE)
        wrap.pack(fill="both", expand=True)

        canvas = tk.Canvas(
            wrap, bg=SURFACE, highlightthickness=0, bd=0, height=200,
        )
        scrollbar = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        inner = tk.Frame(canvas, bg=SURFACE)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_inner_configure)

        def _on_canvas_configure(event):
            canvas.itemconfig(win_id, width=event.width)
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

        # Mouse wheel
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
        # los widgets, centramos en pantalla (con bias hacia arriba para
        # evitar que el Dock tape la parte de abajo), y mostramos la ventana
        # (estaba withdrew()-eada desde __init__).
        self.root.update_idletasks()

        # Posicionar la ventana centrada en el AREA USABLE (descontando
        # menu bar y Dock). Si el window es más alto que el área usable,
        # lo pegamos arriba para que al menos el header sea visible.
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        MENU_BAR = 28
        DOCK = 90  # estimación conservadora
        usable_h = screen_h - MENU_BAR - DOCK

        x = max(0, (screen_w - WINDOW_W) // 2)
        if WINDOW_H >= usable_h:
            y = MENU_BAR + 5  # pegada arriba, mejor que pegada abajo
        else:
            y = MENU_BAR + (usable_h - WINDOW_H) // 2
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}+{x}+{y}")
        self.root.deiconify()
        self.root.lift()
        self.root.mainloop()

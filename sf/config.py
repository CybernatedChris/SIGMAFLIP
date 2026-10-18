import os
import json
import atexit
import platform
import subprocess
import tempfile
import tkinter as tk
import tkinter.font as tkfont
import shutil

import customtkinter as ctk
from PIL import Image

_COPIED_FONTS = []

def _purge_copied_fonts():
    for p in _COPIED_FONTS:
        try:
            os.remove(p)
        except OSError:
            pass

atexit.register(_purge_copied_fonts)

SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"
IS_MAC = SYSTEM == "Darwin"
IS_LINUX = SYSTEM == "Linux"

try:
    from fontTools.ttLib import TTFont
    HAS_FONTTOOLS = True
except ImportError:
    HAS_FONTTOOLS = False

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')

MAIN_COLOR = "#E2E8F0"
SUB_COLOR = "#64748B"
GRID_COLORS = {"dark": ("#151515", "#222522"), "light": ("#f3f4f6", "#e5e7eb")}
MAX_FRAMES = 999
SPEED_FPS = {1: 0.5, 2: 1, 3: 2, 4: 4, 5: 6, 6: 12, 7: 20, 8: 30}
VERSION = "v1.3.2"
WARNING_DURATION = 60.0

_FONT_FAMILY_CACHE = os.path.join(tempfile.gettempdir(), "sigmaflip_font_family.json")


def _font_cache_key(font_path):
    try:
        st = os.stat(font_path)
        return [os.path.basename(font_path), st.st_size, int(st.st_mtime)]
    except OSError:
        return None


def extract_font_family(font_path):
    key = _font_cache_key(font_path)
    if key is not None:
        try:
            with open(_FONT_FAMILY_CACHE, "r") as f:
                cached = json.load(f)
            if cached.get("key") == key and cached.get("family"):
                return cached["family"]
        except Exception:
            pass

    family = _parse_font_family(font_path)

    if key is not None:
        try:
            with open(_FONT_FAMILY_CACHE, "w") as f:
                json.dump({"key": key, "family": family}, f)
        except Exception:
            pass
    return family


def _parse_font_family(font_path):
    if HAS_FONTTOOLS:
        try:
            tt = TTFont(font_path)
            for rec in tt['name'].names:
                if rec.nameID == 1 and rec.platformID in (0, 3):
                    name = rec.toUnicode()
                    tt.close()
                    if name:
                        return name
            tt.close()
        except Exception:
            pass
    name = os.path.splitext(os.path.basename(font_path))[0]
    for s in ["Regular", "Bold", "Italic", "Light", "Medium",
               "Thin", "Black", "ExtraBold", "SemiBold"]:
        name = name.replace(s, "").replace("-", " ").replace("_", " ").strip()
    return name or "Arial"

def load_custom_font(root, font_path):
    fallback = "Arial"
    if not os.path.exists(font_path):
        return fallback

    family = extract_font_family(font_path)
    if not family:
        return fallback

    if IS_WINDOWS:
        try:
            import ctypes
            if ctypes.windll.gdi32.AddFontResourceExW(
                    os.path.abspath(font_path), 0x10, 0) == 0:
                return fallback
        except Exception:
            return fallback
    elif IS_MAC:
        try:
            import ctypes
            ct = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreText.framework/CoreText')
            cf = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
            cf.CFStringCreateWithCString.restype = ctypes.c_void_p
            cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32]
            cf.CFURLCreateWithFileSystemPath.restype = ctypes.c_void_p
            cf.CFURLCreateWithFileSystemPath.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32, ctypes.c_bool]
            ct.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
            ct.CTFontManagerRegisterFontsForURL.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p]
            s = cf.CFStringCreateWithCString(None, os.path.abspath(font_path).encode('utf-8'), 0x08000100)
            u = cf.CFURLCreateWithFileSystemPath(None, s, 0, False)
            if not ct.CTFontManagerRegisterFontsForURL(u, 0, None):
                dest = os.path.expanduser(f"~/Library/Fonts/{os.path.basename(font_path)}")
                if not os.path.exists(dest):
                    shutil.copy2(font_path, dest)
                    _COPIED_FONTS.append(dest)
        except Exception:
            return fallback
    elif IS_LINUX:
        try:
            d = os.path.join(os.path.expanduser("~/.local/share/fonts"), "sigmaflip")
            os.makedirs(d, exist_ok=True)
            dest = os.path.join(d, os.path.basename(font_path))
            if not os.path.exists(dest):
                shutil.copy2(font_path, dest)
                _COPIED_FONTS.append(dest)
                subprocess.run(["fc-cache", d], capture_output=True, timeout=20)
        except Exception:
            return fallback

    root.update_idletasks()
    available = tkfont.families()
    if family in available:
        return family
    for f in available:
        if f.lower() == family.lower():
            return f
    word = family.split()[0].lower()
    matches = [f for f in available if word in f.lower()]
    return matches[0] if matches else fallback

GLYPH_FAMILY = "Segoe UI Symbol" if IS_WINDOWS else None
_NAMED_FONTS = {}


def symbol_font(family, size, weight="bold", scaling=1.0):
    """A font spec that can draw glyphs the app font has none of, and nothing else.

    A family list naming a monochrome symbol font first fixes the fallback for that one
    glyph and leaves the rest of the run in the app font. Tk accepts a family list only
    through -family and never in a font description, so the run is named once and handed
    over by name; the cache keeps the name alive, since Tk drops a font nothing holds."""
    px = -round(abs(size) * scaling)
    if GLYPH_FAMILY:
        key = (family, px, weight)
        if key not in _NAMED_FONTS:
            _NAMED_FONTS[key] = tkfont.Font(family=(GLYPH_FAMILY, family), size=px,
                                            weight=weight)
        family = _NAMED_FONTS[key].name
    return (family, px, weight)


def nine_slice(src, size, inset=8):
    """Resizes a bordered panel by stretching only its flat middle and edges, so the
    corners and border keep their thickness instead of being squashed by a plain resize."""
    w, h = size
    sw, sh = src.size
    i = max(1, min(inset, sw // 2, sh // 2, w // 2, h // 2))
    mw, mh = w - 2 * i, h - 2 * i
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    patches = [
        ((0, 0, i, i), (i, i), (0, 0)),                       
        ((sw - i, 0, sw, i), (i, i), (w - i, 0)),             
        ((0, sh - i, i, sh), (i, i), (0, h - i)),             
        ((sw - i, sh - i, sw, sh), (i, i), (w - i, h - i)),   
        ((i, 0, sw - i, i), (mw, i), (i, 0)),                 
        ((i, sh - i, sw - i, sh), (mw, i), (i, h - i)),       
        ((0, i, i, sh - i), (i, mh), (0, i)),                 
        ((sw - i, i, sw, sh - i), (i, mh), (w - i, i)),       
        ((i, i, sw - i, sh - i), (mw, mh), (i, i)),           
    ]
    for box, dst, pos in patches:
        out.paste(src.crop(box).resize(dst, Image.Resampling.NEAREST), pos)
    return out


def draw_grid_lines(canvas, width, height, line_color, tag="grid", x_off=0, y_off=0,
                    x_limit=None):
    """Stamps the grid pattern onto any canvas, touching only `tag`-tagged items.
    x_off/y_off are the canvas' origin inside the window, so stamped patterns line up
    with the window backdrop instead of restarting at every widget edge. x_limit stops the
    lines short of a control that shares the canvas.

    The lines are parked just above the widget's own fill, not at the top of the canvas:
    Tk has no alpha, so a transparent CTk widget still paints an opaque fill over its
    canvas and the grid has to clear it, but the top of the stack belongs to whatever the
    control draws itself (a dropdown arrow, a checkmark). stack_grid does the parking."""
    canvas.delete(tag)
    grid_spacing = 10
    start_x = -(int(x_off) % grid_spacing)
    start_y = -(int(y_off) % grid_spacing)
    across = width + grid_spacing if x_limit is None else x_limit
    for x in range(start_x, across, grid_spacing):
        canvas.create_line(x, 0, x, height, fill=line_color, width=1, tags=tag)
    for y in range(start_y, height + grid_spacing, grid_spacing):
        canvas.create_line(0, y, width if x_limit is None else x_limit, y,
                           fill=line_color, width=1, tags=tag)
    stack_grid(canvas, tag)


def stack_grid(canvas, tag="grid"):
    """Sits the grid just above the widget's own fill and under everything else on the canvas.

    Every CTk _draw ends by lowering inner_parts to the bottom of the canvas, so a correct
    stamp gets buried by the next repaint and the widget reads as a blank plate. Nothing
    redraws a frame's canvas again on a pure repaint, so this runs even when the geometry
    key is unchanged: restacking is cheap, rebuilding every line is not.

    The grid is parked above inner_parts rather than at the top, because the top holds
    whatever the control draws itself: a CTkOptionMenu's dropdown_arrow, a CTkSwitch's
    checkmark, the text item GridLabel draws. Lifting the grid over those is what put the
    arrows under the grid. Note Tk's argument order is `tag raise <to raise> <aboveThis>`."""
    if not isinstance(canvas, tk.Canvas) or not canvas.find_withtag(tag):
        return
    canvas.tag_lower(tag)
    if canvas.find_withtag("inner_parts"):
        canvas.tag_raise(tag, "inner_parts")
    for fg_tag in ("grid_text", "face", "image"):
        if canvas.find_withtag(fg_tag):
            canvas.tag_raise(fg_tag)


def draw_grid_on_canvas(canvas, width, height, mode):
    """Draw a 10px grid pattern on a tk.Canvas. mode is 'dark' or 'light'."""
    bg_color, line_color = GRID_COLORS["light" if mode == "light" else "dark"]
    canvas.configure(bg=bg_color)
    canvas.delete("all")
    draw_grid_lines(canvas, width, height, line_color)
    canvas.tk.call('lower', canvas._w)


class GridLabel(ctk.CTkLabel):
    """CTkLabel renders its text into a child tk/CTk window, and Tk has no alpha, so that
    window paints an opaque rectangle that cuts a hole in the grid backdrop and reads as a
    little plate behind the label. Draw the text on the widget's own canvas instead, and
    keep the widget sized to what it drew so it still shrink-wraps."""

    _grid_fixed_width = None

    def __init__(self, *args, **kwargs):
        width = kwargs.get("width")
        if width is None and len(args) > 1:
            width = args[1]
        self._grid_fixed_width = width or None
        self._grid_var = kwargs.get("textvariable")
        super().__init__(*args, **kwargs)
        if self._grid_var is not None:
            self._grid_var.trace_add("write", self._on_var_write)
            self._pull_var()

    def _pull_var(self):
        """Text is drawn on the canvas, so a textvariable has to be read in by hand."""
        value = self._grid_var.get()
        if value != self._text:
            self._text = value
            self._draw()

    def _on_var_write(self, *_):
        self._pull_var()

    def _hide_inner_labels(self):
        for attr in ("_label", "_image_label"):
            child = getattr(self, attr, None)
            if child is not None:
                child.grid_forget()
                child.place_forget()
                child.pack_forget()

    def _resize_to(self, w, h):
        pad = self._apply_widget_scaling(4)
        dw, dh = round(w + 2 * pad), round(h + 2 * pad)
        if self._grid_fixed_width is not None:
            dw = max(dw, round(self._apply_widget_scaling(self._grid_fixed_width)))
        if abs(dw - self._desired_width) < 1 and abs(dh - self._desired_height) < 1:
            return
        self._desired_width, self._desired_height = dw, dh
        super().configure(require_redraw=False, width=dw, height=dh)

    def _create_grid(self):
        super()._create_grid()
        self._hide_inner_labels()

    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        self._hide_inner_labels()
        pad = self._apply_widget_scaling(4)
        anchor = "nw" if self._anchor == "center" else self._anchor
        centred = anchor in ("n", "center")
        x = self.winfo_width() / 2 if centred else pad
        key = (self._text, str(self._font), self._wraplength,
               self._apply_appearance_mode(self._text_color),
               anchor, x,
               ctk.get_appearance_mode().lower(),
               self._get_widget_scaling())
        if getattr(self, "_grid_draw_key", None) == key \
                and getattr(self, "_grid_image", None) is self._image:
            return
        self._grid_draw_key = key
        self._grid_image = self._image
        self._canvas.delete("grid_text")
        item = None
        if self._text:
            item = self._canvas.create_text(
                x, pad, text=self._text, anchor=anchor,
                font=self._apply_font_scaling(self._font),
                fill=self._apply_appearance_mode(self._text_color),
                width=self._apply_widget_scaling(self._wraplength),
                tags="grid_text")
        elif self._image is not None:
            if isinstance(self._image, ctk.CTkImage):
                self._grid_photo = self._image.create_scaled_photo_image(
                    self._get_widget_scaling(), self._get_appearance_mode())
                picture = self._grid_photo
            else:
                picture = self._image
            item = self._canvas.create_image(x, pad, image=picture, anchor=anchor,
                                             tags="grid_text")

        if item is None:
            self._resize_to(0, 0)
            return
        x0, y0, x1, y1 = self._canvas.bbox(item)
        if x1 is not None:
            self._resize_to(x1 - x0, y1 - y0)

    def configure(self, require_redraw=False, **kwargs):
        if "width" in kwargs:
            self._grid_fixed_width = kwargs.pop("width")
        if "textvariable" in kwargs:
            var = kwargs.pop("textvariable")
            if var is not self._grid_var:
                if self._grid_var is not None:
                    self._grid_var.trace_remove("write", self._on_var_write)
                self._grid_var = var
                if var is not None:
                    var.trace_add("write", self._on_var_write)
                    self._pull_var()
        if any(k in kwargs for k in ("text", "font", "wraplength", "text_color", "image", "anchor")):
            require_redraw = True
        super().configure(require_redraw=require_redraw, **kwargs)


def stamp_grid_backdrops(root, mode=None):
    """Stamps the grid onto every widget that hides the window backdrop, so it reads as
    continuous instead of being covered by the window colour. A widget whose fill is the
    window colour hides the backdrop just as much as fg_color='transparent' does, and CTk
    paints that fill opaquely, so both are stamped. Returns the widgets that were stamped
    so callers can bind their resize."""
    mode = mode or ctk.get_appearance_mode().lower()
    line_color = GRID_COLORS["light" if mode == "light" else "dark"][1]
    bg_canvas = getattr(root, "_sf_bg_canvas", None)
    if bg_canvas is not None and bg_canvas.winfo_exists():
        root_x, root_y = bg_canvas.winfo_rootx(), bg_canvas.winfo_rooty()
    else:
        root_x, root_y = root.winfo_rootx(), root.winfo_rooty()

    bg_fill = GRID_COLORS["light" if mode == "light" else "dark"][0]

    def hides_backdrop(widget):
        """True when the widget paints opaquely over the window grid. CTk resolves
        fg_color='transparent' to the window colour, and a widget handed that colour
        outright (the option menus) hides the backdrop exactly the same way."""
        fill = widget.cget("fg_color")
        if isinstance(fill, (tuple, list)):
            fill = fill[0] if mode == "light" else fill[-1]
        return fill in ("transparent", bg_fill)

    targets, stack = [], [root]
    while stack:
        for child in stack.pop().winfo_children():
            if getattr(child, "_sf_flat", False):
                continue
            stack.append(child)
            if not isinstance(child, ctk.CTkBaseClass):
                continue
            if isinstance(child, ctk.CTkScrollbar) or getattr(child, "_sf_face", False):
                continue
            bg_canvas = getattr(child, "_bg_canvas", None)
            if bg_canvas is not None:
                targets.append((child, bg_canvas))
            if hides_backdrop(child) or bg_canvas is not None:
                canvas = getattr(child, "_canvas", None)
                if canvas is not None:
                    targets.append((child, canvas))

    stamped = []
    for widget, canvas in targets:
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w <= 1 or h <= 1:
            continue
        limit = None
        if canvas.find_withtag("inner_parts_left"):
            left, right = canvas.bbox("inner_parts_left"), canvas.bbox("inner_parts_right")
            if left and right:
                limit = min(int(left[2]), int(right[0]))
        x_off = canvas.winfo_rootx() - root_x
        y_off = canvas.winfo_rooty() - root_y
        key = (w, h, limit, x_off, y_off, mode)
        if getattr(canvas, "_grid_stamp_key", None) == key:
            stack_grid(canvas)
            stamped.append(widget)
            continue
        canvas._grid_stamp_key = key
        draw_grid_lines(canvas, w, h, line_color, x_off=x_off, y_off=y_off, x_limit=limit)
        stamped.append(widget)
    return stamped


def attach_grid_background(window):
    """Attaches a themed 10px grid canvas that redraws on window resize. Returns the
    redraw callable so a caller can force a repaint after a theme change, which fires
    neither <Configure> nor <Map>."""
    canvas = tk.Canvas(window, bg=GRID_COLORS["dark"][0], highlightthickness=0, bd=0)
    canvas.place(x=0, y=0, relwidth=1, relheight=1)
    window._sf_bg_canvas = canvas
    last_grid = {"w": 0, "h": 0, "mode": None}
    resize_id = [None]

    def draw_bg(event=None):
        try:
            if not window.winfo_exists():
                return
            w = window.winfo_width()
            h = window.winfo_height()
            mode = ctk.get_appearance_mode().lower()
            if (w, h, mode) != (last_grid["w"], last_grid["h"], last_grid["mode"]):
                last_grid.update(w=w, h=h, mode=mode)
                draw_grid_on_canvas(canvas, w, h, mode)
            for widget in stamp_grid_backdrops(window, mode):
                if not getattr(widget, "_sf_resize_bound", False):
                    widget._sf_resize_bound = True
                    widget.bind("<Configure>", restack_and_redraw, add="+")
        except Exception:
            pass

    def restack_and_redraw(event=None):
        """Runs after CTk's own <Configure> handler, which has just buried the grid under
        the widget's fill. Restack it now instead of waiting out the redraw debounce, or the
        widget shows as a blank plate for those 50ms."""
        stack_grid(getattr(event.widget, "_canvas", event.widget))
        queue_redraw()

    def queue_redraw(event=None):
        if resize_id[0] is not None:
            try:
                window.after_cancel(resize_id[0])
            except Exception:
                pass
        try:
            resize_id[0] = window.after(50, draw_bg)
        except Exception:
            pass

    window.bind("<Configure>", draw_bg)
    window.bind("<Map>", draw_bg)
    window.after(120, draw_bg)
    return draw_bg

# sf/config.py
import os
import sys
import platform
import subprocess
import tkinter.font as tkfont
import shutil

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

# Sleek Stealth Slate Color Palette
MAIN_COLOR = "#E2E8F0"  # Bright Titanium Platinum (dark mode)
SUB_COLOR = "#64748B"   # Tactical Cool Slate Gray
MAX_FRAMES = 999
SPEED_FPS = {1: 0.5, 2: 1, 3: 2, 4: 4, 5: 6, 6: 12, 7: 20, 8: 30}
VERSION = "v1"
WARNING_DURATION = 60.0

def get_resource_path(relative_path):
    # Resolves directly to the package environment level
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)

def get_icon_path(img_dir, name):
    order = [f"{name}.ico", f"{name}.png"] if IS_WINDOWS else [f"{name}.png", f"{name}.ico"]
    for f in order:
        p = os.path.join(img_dir, f)
        if os.path.exists(p):
            return p
    return None

def safe_relpath(target, start):
    try:
        return os.path.relpath(target, start)
    except ValueError:
        return os.path.abspath(target)

def extract_font_family(font_path):
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
        except Exception:
            return fallback
    elif IS_LINUX:
        try:
            d = os.path.expanduser("~/.local/share/fonts")
            os.makedirs(d, exist_ok=True)
            dest = os.path.join(d, os.path.basename(font_path))
            if not os.path.exists(dest):
                    shutil.copy2(font_path, dest)
                    subprocess.run(["fc-cache", "-f"], capture_output=True)
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


def draw_grid_on_canvas(canvas, width, height, mode):
    """Draw a 10px grid pattern on a tk.Canvas. mode is 'dark' or 'light'."""
    if mode == "dark":
        bg_color = "#151515"
        line_color = "#222522"
    else:
        bg_color = "#f3f4f6"
        line_color = "#e5e7eb"
    canvas.configure(bg=bg_color)
    canvas.delete("all")
    grid_spacing = 10
    for x in range(0, width, grid_spacing):
        canvas.create_line(x, 0, x, height, fill=line_color, width=1)
    for y in range(0, height, grid_spacing):
        canvas.create_line(0, y, width, y, fill=line_color, width=1)
    canvas.tk.call('lower', canvas._w)

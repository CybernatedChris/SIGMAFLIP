# sf/app.py
import os
import sys
import time
import io
import shutil
import json
import threading
import subprocess
import tempfile
import math
import struct
import cv2
import pygame
import tkinter as tk
from tkinter import messagebox, filedialog
import customtkinter as ctk
from PIL import Image, ImageTk, ImageEnhance

from sf.config import (
    IS_WINDOWS, IS_MAC, IS_LINUX, MAIN_COLOR, SUB_COLOR,
    MAX_FRAMES, SPEED_FPS, VERSION, get_resource_path, get_icon_path,
    load_custom_font, WARNING_DURATION, draw_grid_on_canvas
)
from sf.about import show_about_dialog
from sf.dither import apply_ordered_dither, apply_error_diffusion

if IS_WINDOWS:
    import ctypes

try:
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

class SIGMAFLIP:
    def __init__(self, root):
        self.root = root
        self.root.title("SIGMAFLIP")
        self.root.geometry("500x580")  # Re-centered default layout height
        self.root.resizable(False, False)
        
        self._theme_bg = ("#f3f4f6", "#151515")
        self.root.configure(fg_color=self._theme_bg)

        if not HAS_CRYPTO:
            self.root.withdraw()
            messagebox.showerror(
                "Dependency Missing",
                "SIGMAFLIP requires the 'pycryptodome' library to sign exported JPEGs.\n\n"
                "Please run this command in your terminal or command prompt:\n"
                "pip install pycryptodome\n\n"
                "Then restart the application."
            )
            self.root.destroy()
            sys.exit(1)

        h = SUB_COLOR.lstrip('#')
        rgb = [int(h[i:i+2], 16) for i in (0, 2, 4)]
        self.highlight_color = "#{:02X}{:02X}{:02X}".format(
            *[int(c + (255 - c) * 0.65) for c in rgb])

        # Resolve asset directories from nested package folder levels
        self.base_path = os.path.dirname(os.path.abspath(__file__))
        self.assets_path = os.path.join(self.base_path, 'assets')
        self.img_path = os.path.join(self.assets_path, 'img')
        self.sounds_path = os.path.join(self.assets_path, 'sounds')

        self.validate_assets()

        self.icon_path = os.path.join(self.img_path, 'sigma.ico')
        self._set_window_icon(self.root, delay=True)

        font_file = os.path.join(self.assets_path, "font.otf")
        if not os.path.exists(font_file):
            font_file = os.path.join(self.assets_path, "font.ttf")
        self.font_family = load_custom_font(self.root, font_file) if font_file else "Arial"

        self.font_title = ctk.CTkFont(family=self.font_family, size=20, weight="bold")
        self.font_large = ctk.CTkFont(family=self.font_family, size=18)
        self.font_medium = ctk.CTkFont(family=self.font_family, size=16)
        self.font_medium_bold = ctk.CTkFont(family=self.font_family, size=16, weight="bold")
        self.font_small = ctk.CTkFont(family=self.font_family, size=14)
        self.font_tiny = ctk.CTkFont(family=self.font_family, size=12)

        self.main_color_adaptive = ("#1e293b", "#E2E8F0")
        self.sub_color_adaptive = ("#475569", "#94a3b8")
        self.highlight_color_adaptive = ("#3b82f6", self.highlight_color)

        self.video_path = None
        self.cap = None
        self.total_video_frames = 0
        self.video_fps = 24.0
        self.video_duration = 0.0
        self.current_frame_idx = 0.0
        
        self.image_paths = []  
        self.still_index = 0

        self.bg_image_path = None

        self.temp_audio_path = None
        self.has_audio = False
        self._music_paused = False
        self._music_pos_anchor = 0
        self._playback_start_time = 0.0
        self._playback_start_frame = 0.0

        self.speed = 6
        self.playing = False
        self.scale_mode = "Fit"
        self.after_play_id = None
        self.speed_tk_imgs = {}

        self.last_grid_w = 0
        self.last_grid_h = 0
        self.last_grid_mode = None

        self._exporting = False
        self._last_render_time = 0.0
        self._rapid_rendering = False

        self.current_singular_view = "preview"  # "preview" or "grid"
        self._thumbnail_tk_images = []
        self._tooltip_win = None

        config_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.config_filepath = os.path.join(config_dir, ".sigmaflip_config.json")

        self.bg_type_var = tk.StringVar(value="black")
        self.export_structure_var = tk.StringVar(value="dcim")
        self.export_mode_var = ctk.StringVar(value="Video Frames")

        self.audio_enabled = True
        self.bg_type = "black"
        self.export_structure = "dcim"
        self.console_type = "dsi"

        self.advanced_settings = {
            "pixel_precision": False,
            "black_and_white": False,
            "contrast": 1.0,
            "dither_mode": "None",
            "album_capacity": 100,
            "pit_dir": ""
        }

        self.sfx_cache = {}
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
        except Exception:
            if IS_LINUX:
                try:
                    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
                except Exception:
                    pass

        self.preload_sounds()
        self.preload_tool_assets()
        self.create_menu_bar()
        self.build_ui()
        self.load_user_settings() # Restores settings on boot

        # Start a stable, version-independent theme polling loop
        self._last_mode = ctk.get_appearance_mode().lower()
        self.poll_appearance_mode()

    def _cleanup_audio(self) -> None:
        """Stop pygame music, delete temp audio file, and reset audio state flags."""
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass
        if self.temp_audio_path and os.path.exists(self.temp_audio_path):
            try:
                os.unlink(self.temp_audio_path)
            except Exception:
                pass
        self.temp_audio_path = None
        self.has_audio = False
        self._music_paused = False
        self._music_pos_anchor = 0

    def validate_assets(self):
        required_images = [
            'play.png', 'pause.png',
            '1.png', '2.png', '3.png', '4.png', '5.png', '6.png', '7.png', '8.png',
            '1_disabled.png', '2_disabled.png', '3_disabled.png', '4_disabled.png',
            '5_disabled.png', '6_disabled.png', '7_disabled.png', '8_disabled.png',
            'prevframe.png', 'prevframe_disabled.png', 'nextframe.png', 'nextframe_disabled.png'
        ]
        missing = []
        if not os.path.exists(self.img_path):
            missing.append(f"Image folder not found at: {self.img_path}")
        else:
            for fname in required_images:
                p = os.path.join(self.img_path, fname)
                if not os.path.exists(p):
                    missing.append(fname)
        if missing:
            self.root.withdraw()
            messagebox.showerror(
                "Missing Assets",
                "SIGMAFLIP cannot start because image assets are missing.\n\n"
                f"Expected directory: {self.img_path}\n"
                f"Missing items:\n" + "\n".join([f" - {item}" for item in missing])
            )
            self.root.destroy()
            sys.exit(1)

    def preload_sounds(self):
        if not os.path.exists(self.sounds_path):
            return
        for f in os.listdir(self.sounds_path):
            if f.lower().endswith(('.mp3', '.wav', '.ogg')):
                try:
                    s = pygame.mixer.Sound(os.path.join(self.sounds_path, f))
                    s.set_volume(0.5)
                    self.sfx_cache[f] = s
                except Exception:
                    pass

    def play_sound(self, filename):
        if filename in self.sfx_cache:
            try:
                ch = pygame.mixer.find_channel()
                if ch:
                    ch.play(self.sfx_cache[filename])
            except Exception:
                pass

    def preload_tool_assets(self):
        icon_scale = 0.55
        self.icons = {}
        for k, fname in [
            ('play', 'play.png'), ('play_down', 'play_down.png'),
            ('pause', 'pause.png'), ('pause_down', 'pause_down.png'),
            ('upload', 'upload.png'), ('upload_down', 'upload_down.png'),
            ('prev', 'prevframe.png'), ('prev_down', 'prevframe_down.png'), ('prev_disabled', 'prevframe_disabled.png'),
            ('next', 'nextframe.png'), ('next_down', 'nextframe_down.png'), ('next_disabled', 'nextframe_disabled.png'),
            ('beg', 'beg.png'), ('beg_down', 'beg_down.png'), ('beg_disabled', 'beg_disabled.png'),
            ('end', 'end.png'), ('end_down', 'end_down.png'), ('end_disabled', 'end_disabled.png'),
            ('lock', 'lock.png'), ('lock_down', 'lock_down.png'), ('unlock', 'unlock.png'), ('unlock_down', 'unlock_down.png')
        ]:
            p = os.path.join(self.img_path, fname)
            if os.path.exists(p):
                img = Image.open(p)
                self.icons[k] = ctk.CTkImage(
                    light_image=img, dark_image=img,
                    size=(int(img.width * icon_scale), int(img.height * icon_scale))
                )
            else:
                self.icons[k] = None

    def add_press_feedback(self, btn):
        """Flash matching _down variant on press, restore after ~300ms so quick clicks are visible."""
        def find_down():
            cur = btn.cget("image")
            if cur is None:
                return None, None
            for base, img in self.icons.items():
                if not img or img is not cur:
                    continue
                if base.endswith("_down"):
                    # Re-click mid-flash: icon is already the down variant. Keep it down,
                    # restore target is the matching normal icon.
                    orig_base = base[: -len("_down")]
                    return base, self.icons.get(orig_base)
                if not base.endswith("_disabled"):
                    down = f"{base}_down"
                    if down in self.icons and self.icons[down]:
                        return down, cur
            return None, None

        def restore():
            btn._sf_after = None
            if btn.cget("image") is btn._sf_down_img:
                btn.configure(image=btn._sf_restore_target)

        def on_press(e):
            if btn.cget("state") != "normal":
                if getattr(btn, "_sf_after", None):
                    btn.after_cancel(btn._sf_after)
                    btn._sf_after = None
                btn._sf_down_img = None
                btn._sf_restore_target = None
                return
            down, orig = find_down()
            if not down:
                if getattr(btn, "_sf_after", None):
                    btn.after_cancel(btn._sf_after)
                    btn._sf_after = None
                btn._sf_down_img = None
                btn._sf_restore_target = None
                return
            btn._sf_down_img = self.icons[down]
            btn._sf_restore_target = orig
            btn._sf_press_time = time.time()
            if getattr(btn, "_sf_after", None):
                btn.after_cancel(btn._sf_after)
            btn.configure(image=btn._sf_down_img)
            btn._sf_after = btn.after(150, restore)

        def on_release(e=None):
            if not getattr(btn, "_sf_down_img", None):
                return
            if time.time() - getattr(btn, "_sf_press_time", time.time()) > 0.25:
                btn.configure(image=getattr(btn, "_sf_restore_target", None))
                btn._sf_down_img = None
                btn._sf_restore_target = None
                return
            if btn.cget("state") != "normal":
                btn.configure(image=getattr(btn, "_sf_restore_target", None))
                btn._sf_down_img = None
                btn._sf_restore_target = None
                return
            if getattr(btn, "_sf_after", None):
                btn.after_cancel(btn._sf_after)
            # Command may have swapped the icon (nav state, play/pause toggle).
            # Keep the flash going: restore to whatever the icon is now, 150ms after release.
            if btn.cget("image") is not btn._sf_down_img:
                btn._sf_restore_target = btn.cget("image")
            btn.configure(image=btn._sf_down_img)
            btn._sf_after = btn.after(150, restore)

        btn.bind("<Button-1>", on_press, add="+")
        btn.bind("<ButtonRelease-1>", on_release, add="+")
        btn.focus_set = lambda: None

    def _set_window_icon(self, window, delay=True):
        """Resolves taskbar icon mapping prioritizing the high-fidelity .ico file across execution paths."""
        ico_path = os.path.join(self.img_path, 'sigma.ico')
        png_path = os.path.join(self.img_path, 'sigma.png')
        
        def do_set():
            try:
                # Set Windows titlebar icon
                if IS_WINDOWS and os.path.exists(ico_path):
                    try:
                        window.iconbitmap(ico_path)
                    except Exception as e:
                        print(f"Windows iconbitmap configuration error: {e}")
                
                # Apply icon photo representation for standard system window managers
                best_icon_path = ico_path if os.path.exists(ico_path) else (png_path if os.path.exists(png_path) else None)
                if best_icon_path:
                    img = Image.open(best_icon_path)
                    photo = ImageTk.PhotoImage(img)
                    window.iconphoto(True, photo)
                    window._icon_img = photo  # keep reference
            except Exception as e:
                print(f"Icon configuration error: {e}")
                
        if delay:
            window.after(250, do_set)
        else:
            do_set()

    def create_menu_bar(self):
        """Builds a native theme-integrated window menu bar with high-contrast Unicode checkmarks."""
        curr_mode = ctk.get_appearance_mode().lower()
        is_dark = curr_mode == "dark"
        menu_opts = {} if IS_MAC else dict(
            bg="#1a1a1a" if is_dark else "#ffffff",
            fg=MAIN_COLOR if is_dark else "#111827",
            activebackground=SUB_COLOR,
            activeforeground="#111827",
            borderwidth=0, font=(self.font_family, 10))
        
        self.menubar = tk.Menu(self.root, **menu_opts)
        self.root.configure(menu=self.menubar)
        
        # Options Dropdown
        self.options_menu = tk.Menu(self.menubar, tearoff=0, **menu_opts)
        self.menubar.add_cascade(label="Options", menu=self.options_menu)
        
        # Audio Preview toggle (High contrast text ticks)
        self.options_menu.add_command(
            label="✓ Enable Audio Preview" if self.audio_enabled else "   Enable Audio Preview",
            command=self.toggle_audio_menu_item
        )

        self.options_menu.add_separator()

        # Cascade Menu for custom Resizing Background
        self.bg_menu = tk.Menu(self.options_menu, tearoff=0, **menu_opts)
        self.options_menu.add_cascade(label="Background Setting", menu=self.bg_menu)

        self.bg_menu.add_command(label="✓ Solid Black (Default)", command=lambda: self.set_bg_menu_type("black"))
        self.bg_menu.add_command(label="   Solid White", command=lambda: self.set_bg_menu_type("white"))
        self.bg_menu.add_command(label="   Custom Background...", command=lambda: self.set_bg_menu_type("custom"))

        self.options_menu.add_separator()

        # Cascade Menu for Folder Export Structures
        self.struct_menu = tk.Menu(self.options_menu, tearoff=0, **menu_opts)
        self.options_menu.add_cascade(label="Export Folder Structure", menu=self.struct_menu)

        self.struct_menu.add_command(label="✓ Native DCIM (10XNIN01/NIN02)", command=lambda: self.set_struct_menu_type("dcim"))
        self.struct_menu.add_command(label="   Sequential Parts (Part_X)", command=lambda: self.set_struct_menu_type("parts"))

        self.options_menu.add_separator()

        # Cascade Menu for Target Console
        self.console_menu = tk.Menu(self.options_menu, tearoff=0, **menu_opts)
        self.options_menu.add_cascade(label="Target Console", menu=self.console_menu)

        self.console_menu.add_command(label="✓ Nintendo DSi", command=lambda: self.set_console_type("dsi"))
        self.console_menu.add_command(label="   Nintendo 3DS", command=lambda: self.set_console_type("3ds"))

        self.options_menu.add_separator()

        # Option for Advanced Filter Settings
        self.options_menu.add_command(
            label="Advanced Settings...",
            command=self.show_advanced_settings
        )
        
        # Directly add About to top level menubar
        self.menubar.add_command(label="About", command=self.show_about_dialog)

    def toggle_audio_menu_item(self):
        """Toggles audio state, updating high-contrast menu labels manually."""
        self.audio_enabled = not self.audio_enabled
        self.options_menu.entryconfigure(
            0,
            label="✓ Enable Audio Preview" if self.audio_enabled else "   Enable Audio Preview"
        )
        self.on_audio_toggle()

    def set_bg_menu_type(self, bg_type, silent=False):
        """Sets fit padding types, updating high-contrast checkmarks."""
        if bg_type == "custom":
            self.select_custom_bg_image()
            if self.bg_type_var.get() != "custom":
                return
        else:
            self.bg_type = bg_type
            self.bg_type_var.set(bg_type)
            if not silent:
                self.on_bg_type_change()

        self.bg_menu.entryconfigure(0, label="✓ Solid Black (Default)" if self.bg_type_var.get() == "black" else "   Solid Black (Default)")
        self.bg_menu.entryconfigure(1, label="✓ Solid White" if self.bg_type_var.get() == "white" else "   Solid White")
        self.bg_menu.entryconfigure(2, label="✓ Custom Background..." if self.bg_type_var.get() == "custom" else "   Custom Background...")

    def set_struct_menu_type(self, struct_type, silent=False):
        """Sets export folder directory structure options, updating high-contrast ticks."""
        self.export_structure = struct_type
        self.export_structure_var.set(struct_type)
        if not silent:
            self.on_struct_type_change()

        self.struct_menu.entryconfigure(0, label="✓ Native DCIM (10XNIN01/NIN02)" if self.export_structure_var.get() == "dcim" else "   Native DCIM (10XNIN01/NIN02)")
        self.struct_menu.entryconfigure(1, label="✓ Sequential Parts (Part_X)" if self.export_structure_var.get() == "parts" else "   Sequential Parts (Part_X)")

    def set_console_type(self, console_type):
        """Sets the target console, updating high-contrast checkmarks."""
        self.console_type = console_type
        self.console_menu.entryconfigure(0, label="✓ Nintendo DSi" if self.console_type == "dsi" else "   Nintendo DSi")
        self.console_menu.entryconfigure(1, label="✓ Nintendo 3DS" if self.console_type == "3ds" else "   Nintendo 3DS")

    def _prompt_pit_deletion(self, pit):
        """Ask on the main thread whether to delete the stale album cache.
        Blocks the worker thread until the user answers."""
        result = [False]
        done = threading.Event()
        def ask():
            try:
                result[0] = messagebox.askyesno(
                    "Delete album cache?",
                    f"A stale photo album cache was found at:\n{pit}\n\n"
                    "Delete it now so the console re-scans the new photos?")
            finally:
                done.set()
        self.root.after(0, ask)
        done.wait()
        return result[0]

    def _cleanup_dsi_album_cache(self, export_dir):
        """Delete the stale camera album cache so the console re-scans the new photos.
        The DSi photo format is region-free (GBATEK) and the camera app folder code
        is 'HNIJ' (484E494A) on every region, so this path is universal.
        Only runs when exporting to the SD card root in DSi mode.
        Returns a short note about what was cleaned, or '' if nothing to do."""
        pit = os.path.join(export_dir, "private", "ds", "app", "484E494A", "pit.bin")
        if not os.path.isfile(pit):
            return ""
        if not self._prompt_pit_deletion(pit):
            return ""
        try:
            os.unlink(pit)
        except Exception:
            return ""
        return f"\nDeleted stale album cache: {pit}"

    def select_custom_bg_image(self):
        """Launches file selector for custom image backgrounds on resized crops."""
        file_path = filedialog.askopenfilename(
            title="Select Custom Background Frame Image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.webp")]
        )
        if file_path:
            self.bg_image_path = file_path
            self.bg_type = "custom"
            self.bg_type_var.set("custom")
            self.play_sound('apply.mp3')
            self.update_frame_display()
        else:
            self.play_sound('back.mp3')
            # Safely fallback to black standard padding if no configuration exists yet
            if not self.bg_image_path:
                self.bg_type = "black"
                self.bg_type_var.set("black")
            else:
                self.bg_type = "custom"
                self.bg_type_var.set("custom")

    def on_bg_type_change(self):
        """Forces frame updates and playback redraws upon background parameter changes."""
        self.play_sound('apply.mp3')
        self.update_frame_display()

    def on_struct_type_change(self):
        """Updates configurations and playbacks silently when toggling export modes."""
        self.play_sound('apply.mp3')
        if self.cap or self.image_paths:
            self.check_timing_warnings(show_popup=False)

    def show_about_dialog(self):
        """Launches the external about window."""
        self.play_sound('apply.mp3')
        fonts = {
            'title': self.font_title,
            'large': self.font_large,
            'medium': self.font_medium,
            'small': self.font_small,
            'tiny': self.font_tiny
        }
        show_about_dialog(
            parent=self.root,
            fonts=fonts,
            icon_path=self.icon_path,
            main_color=self.main_color_adaptive, # Pass adaptive colors
            sub_color=self.sub_color_adaptive,
            highlight_color=self.highlight_color_adaptive,
            set_icon_fn=self._set_window_icon
        )

    def show_advanced_settings(self):
        """Launches the advanced configuration interface window."""
        self.play_sound('apply.mp3')
        fonts = {
            'title': self.font_title,
            'large': self.font_large,
            'medium': self.font_medium,
            'medium_bold': self.font_medium_bold,
            'small': self.font_small,
            'tiny': self.font_tiny
        }
        from sf.advanced import show_advanced_dialog
        show_advanced_dialog(
            parent=self.root,
            fonts=fonts,
            main_color=self.main_color_adaptive,
            sub_color=self.sub_color_adaptive,
            highlight_color=self.highlight_color_adaptive,
            settings=self.advanced_settings,
            on_change_callback=self.update_frame_display,
            get_export_structure=lambda: self.export_structure,
            get_console_type=lambda: self.console_type,
            set_icon_fn=self._set_window_icon,
            theme_bg=self._theme_bg
        )

    def on_audio_toggle(self):
        """Dynamically handles toggle checking and cleans/starts ffmpeg extractions actively."""
        self.play_sound('apply.mp3')
        if self.audio_enabled:
            if self.video_path and not self.has_audio and "Video" in self.export_mode_var.get():
                self.temp_audio_path = os.path.join(tempfile.gettempdir(), f"sigmaflip_preview_{int(time.time())}.wav")
                threading.Thread(target=self.extract_audio_thread, daemon=True).start()
        else:
            self._cleanup_audio()

    def poll_appearance_mode(self):
        """Safely polls CustomTkinter's appearance mode state to trigger native redrawing on theme changes."""
        try:
            curr_mode = ctk.get_appearance_mode().lower()
            if self._last_mode != curr_mode:
                self._last_mode = curr_mode
                self.on_appearance_mode_changed(curr_mode)
        except Exception:
            pass
        self.root.after(2000, self.poll_appearance_mode)

    def on_appearance_mode_changed(self, new_mode):
        """Forces full interface redraws of standard Tkinter elements when switching themes without breaking CustomTkinter tuples."""
        try:
            # Resolve exact hex values for raw Tkinter elements
            menu_bg = "#2b2b2b" if new_mode == "dark" else "#ffffff"
            menu_fg = "#E2E8F0" if new_mode == "dark" else "#111827"
            
            # Update Tkinter dropdown menu backgrounds
            for menu in (self.options_menu, self.bg_menu, self.struct_menu, self.console_menu):
                try:
                    menu.configure(bg=menu_bg, fg=menu_fg, activebackground=SUB_COLOR, activeforeground="#111827")
                except Exception:
                    pass
                    
            # Redraw Canvas lines and standard Tkinter grids
            self.draw_window_grid(forced_mode=new_mode)
            if self.current_singular_view == "grid" and self.image_paths:
                self.root.after(100, self.populate_thumbnail_grid)
        except Exception:
            pass

    def build_ui(self):
        self.bg_canvas = tk.Canvas(self.root, bg="#1a1a1a", highlightthickness=0, bd=0)
        self.bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.root.bind("<Configure>", self.draw_window_grid)

        top_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        top_frame.pack(fill="x", pady=5)

        self.title_label = ctk.CTkLabel(
            top_frame, text="SIGMAFLIP", font=self.font_title, text_color=self.main_color_adaptive, fg_color="transparent"
        )
        self.title_label.pack()

        self.file_name_label = ctk.CTkLabel(
            top_frame, text="No Video Loaded", font=self.font_tiny, text_color=self.sub_color_adaptive, fg_color="transparent"
        )
        self.file_name_label.pack()

        # Canvas Preview Box Wrapper (solid black shows flipnote with fake bg)
        self.preview_frame = ctk.CTkFrame(self.preview_frame if hasattr(self, 'preview_frame') else self.root, width=320, height=240, fg_color="black", border_width=2, border_color=MAIN_COLOR)
        self.preview_frame.pack(pady=5)
        self.preview_frame.pack_propagate(False)

        # Standard Video Preview Canvas (solid black for flipnote preview background)
        self.video_canvas = tk.Canvas(self.preview_frame, bg="black", highlightthickness=0)
        self.video_canvas.pack(fill="both", expand=True)

        # Scrollable Thumbnail Grid (solid black for thumbnail preview)
        self.grid_scroll_frame = ctk.CTkScrollableFrame(
            self.preview_frame, width=320, height=240, fg_color="black", corner_radius=0
        )

        self.toggle_view_btn = ctk.CTkButton(
            self.root, text="Switch to Grid View", command=self.toggle_singular_view_mode,
            width=150, height=30, font=self.font_tiny, fg_color="transparent", text_color=self.main_color_adaptive, hover_color=self.highlight_color_adaptive
        )

        # Frame Reordering / Deletion organization panel
        self.grid_controls_row = ctk.CTkFrame(self.root, fg_color="transparent")
        
        self.delete_frame_btn = ctk.CTkButton(
            self.grid_controls_row, text="Delete Selected", command=self.delete_selected_frame,
            width=110, height=28, font=self.font_tiny, fg_color="transparent", text_color="#ef4444", hover_color="#dc2626"
        )
        self.delete_frame_btn.pack(side=tk.LEFT, padx=5)
        
        self.move_left_btn = ctk.CTkButton(
            self.grid_controls_row, text="Move Left", command=self.move_frame_left,
            width=80, height=28, font=self.font_tiny, fg_color="transparent", text_color=self.main_color_adaptive, hover_color=self.highlight_color_adaptive
        )
        self.move_left_btn.pack(side=tk.LEFT, padx=5)
        
        self.move_right_btn = ctk.CTkButton(
            self.grid_controls_row, text="Move Right", command=self.move_frame_right,
            width=80, height=28, font=self.font_tiny, fg_color="transparent", text_color=self.main_color_adaptive, hover_color=self.highlight_color_adaptive
        )
        self.move_right_btn.pack(side=tk.LEFT, padx=5)

        # Slider with transparent background parent blending
        self.timeline_slider = ctk.CTkSlider(
            self.root, from_=0, to=100, number_of_steps=100,
            button_color=self.main_color_adaptive, button_hover_color=SUB_COLOR,
            progress_color=self.main_color_adaptive, command=self.on_slider_scrub, bg_color="transparent"
        )
        self.timeline_slider.pack(fill="x", padx=40, pady=5)
        self.timeline_slider.set(0)

        # Transparent Still Image Navigation Row
        self.nav_row = ctk.CTkFrame(self.root, fg_color="transparent")
        btn_defaults = dict(hover_color=self.highlight_color_adaptive, corner_radius=8, fg_color="transparent")

        self.beg_btn = ctk.CTkButton(
            self.nav_row, text="", width=40, height=36,
            command=self.jump_to_beginning, **btn_defaults
        )
        self.beg_btn.pack(side=tk.LEFT, padx=5)
        self.add_press_feedback(self.beg_btn)

        self.prev_btn = ctk.CTkButton(
            self.nav_row, text="", width=40, height=36,
            command=self.show_prev_image, **btn_defaults
        )
        self.prev_btn.pack(side=tk.LEFT, padx=5)
        self.add_press_feedback(self.prev_btn)

        self.nav_label = ctk.CTkLabel(
            self.nav_row, text="0 of 0", font=self.font_small, text_color=self.sub_color_adaptive, fg_color="transparent"
        )
        self.nav_label.pack(side=tk.LEFT, padx=5)

        self.next_btn = ctk.CTkButton(
            self.nav_row, text="", width=40, height=36,
            command=self.show_next_image, **btn_defaults
        )
        self.next_btn.pack(side=tk.LEFT, padx=5)
        self.add_press_feedback(self.next_btn)

        self.end_btn = ctk.CTkButton(
            self.nav_row, text="", width=40, height=36,
            command=self.jump_to_end, **btn_defaults
        )
        self.end_btn.pack(side=tk.LEFT, padx=5)
        self.add_press_feedback(self.end_btn)

        # Custom [] x [] coordinates input panels
        self.tile_config_row = ctk.CTkFrame(self.root, fg_color="transparent")
        self.tile_frame_label = ctk.CTkLabel(
            self.tile_config_row, text="Tile Size:", 
            font=self.font_small, text_color=self.sub_color_adaptive, fg_color="transparent"
        )
        self.tile_frame_label.pack(side=tk.LEFT, padx=(0, 5))

        self.tile_cols_entry = ctk.CTkEntry(
            self.tile_config_row, width=45, height=28,
            font=self.font_tiny, justify="center", border_color=self.main_color_adaptive
        )
        self.tile_cols_entry.insert(0, "2")
        self.tile_cols_entry.pack(side=tk.LEFT, padx=5)
        self.tile_cols_entry.bind("<KeyRelease>", self.on_grid_entry_change)

        self.tile_x_label = ctk.CTkLabel(
            self.tile_config_row, text="x",
            font=self.font_small, text_color=self.sub_color_adaptive, fg_color="transparent"
        )
        self.tile_x_label.pack(side=tk.LEFT, padx=2)

        self.tile_rows_entry = ctk.CTkEntry(
            self.tile_config_row, width=45, height=28,
            font=self.font_tiny, justify="center", border_color=self.main_color_adaptive
        )
        self.tile_rows_entry.insert(0, "2")
        self.tile_rows_entry.pack(side=tk.LEFT, padx=5)

        self.tile_link_locked = False
        self.tile_link_btn = ctk.CTkButton(
            self.tile_config_row, text="", width=32, height=28,
            command=self.toggle_tile_link,
            fg_color="transparent", hover_color=self.highlight_color_adaptive,
            image=self.icons.get('unlock'), corner_radius=4
        )
        self.tile_link_btn.pack(side=tk.LEFT, padx=(5, 0))
        self.add_press_feedback(self.tile_link_btn)
        self.tile_rows_entry.bind("<KeyRelease>", self.on_grid_entry_change)

        self.limit_indicator = ctk.CTkLabel(
            self.root, text="Export Frames: 0 / 999", font=self.font_small, text_color=self.sub_color_adaptive, fg_color="transparent"
        )
        self.limit_indicator.pack(pady=3)

        # Speed Widget Frame Container
        self.sf_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        self.sf_frame.pack(pady=4)

        sample_path = os.path.join(self.img_path, "1.png")
        self.speed_w, self.speed_h = (180, 40)
        if os.path.exists(sample_path):
            img_sample = Image.open(sample_path)
            self.speed_w = img_sample.width
            self.speed_h = img_sample.height

        self.speed_widget = tk.Canvas(self.sf_frame, width=self.speed_w, height=self.speed_h,
                                      bg="#2b2b2b", highlightthickness=0, bd=0, cursor="hand2")
        self.speed_widget.pack(pady=2)
        self.speed_widget.bind("<Button-1>", self.on_speed_widget_click)
        self.sync_speed_widget_image()

        # Transparent Config row (Resize and Export Presets)
        self.config_row = ctk.CTkFrame(self.root, fg_color="transparent")
        self.config_row.pack(pady=4, fill="x", padx=45)

        ctk.CTkLabel(self.config_row, text="Resize:", font=self.font_small, text_color=self.sub_color_adaptive, fg_color="transparent").pack(side=tk.LEFT, padx=(0, 5))
        
        # Dropdown options list contains standard Stretch, Crop, Tiles and Tiles Stretched
        self.aspect_menu = ctk.CTkOptionMenu(
            self.config_row, values=["Fit (Letterbox)", "Stretch", "Crop (4:3)", "Tiles", "Tiles Stretched"],
            command=self.set_scale_mode,
            fg_color=self._theme_bg,
            button_color=self._theme_bg,
            button_hover_color=self.highlight_color_adaptive,
            dropdown_fg_color=("#ffffff", "#2b2b2b"),
            dropdown_text_color=("#1e293b", "#E2E8F0"),
            text_color=self.main_color_adaptive,
            font=self.font_tiny, dropdown_font=self.font_tiny,
            width=110
        )
        self.aspect_menu.pack(side=tk.LEFT)

        # Export Mode Selection Panel
        ctk.CTkLabel(self.config_row, text="Mode:", font=self.font_small, text_color=self.sub_color_adaptive, fg_color="transparent").pack(side=tk.LEFT, padx=(15, 5))
        self.export_mode_var = ctk.StringVar(value="Video Frames")
        self.export_mode_menu = ctk.CTkOptionMenu(
            self.config_row, values=["Video Frames", "Singular Image"],
            variable=self.export_mode_var,
            fg_color=self._theme_bg,
            button_color=self._theme_bg,
            button_hover_color=self.highlight_color_adaptive,
            dropdown_fg_color=("#ffffff", "#2b2b2b"),
            dropdown_text_color=("#1e293b", "#E2E8F0"),
            text_color=self.main_color_adaptive,
            font=self.font_tiny, dropdown_font=self.font_tiny,
            width=140, command=self.on_export_mode_change
        )
        self.export_mode_menu.pack(side=tk.LEFT)

        # Transparent Controls Button Bar
        self.ctrl_row = ctk.CTkFrame(self.root, fg_color="transparent")
        self.ctrl_row.pack(pady=5)

        self.load_btn = ctk.CTkButton(
            self.ctrl_row, text="", command=self.load_video_dialog,
            width=100, height=36, image=self.icons.get('upload'),
            fg_color="transparent", text_color=self.main_color_adaptive, hover_color=self.highlight_color_adaptive, corner_radius=8
        )
        self.load_btn.pack(side=tk.LEFT, padx=3)
        self.add_press_feedback(self.load_btn)

        self.play_btn = ctk.CTkButton(
            self.ctrl_row, text="", command=self.toggle_play,
            width=100, height=36, image=self.icons.get('play'), state="disabled",
            fg_color="transparent", text_color=self.main_color_adaptive, hover_color=self.highlight_color_adaptive, corner_radius=8
        )
        self.play_btn.pack(side=tk.LEFT, padx=3)
        self.add_press_feedback(self.play_btn)

        self.export_btn = ctk.CTkButton(
            self.ctrl_row, text="Export Frames", command=self.export_frames,
            width=100, height=36, font=self.font_medium_bold, state="disabled",
            fg_color="transparent",
            hover_color=self.highlight_color_adaptive, 
            text_color=self.main_color_adaptive, 
            corner_radius=8
        )
        self.export_btn.pack(side=tk.LEFT, padx=3)

        self.progress_bar = ctk.CTkProgressBar(self.root, progress_color=self.highlight_color_adaptive, height=8)
        self.progress_bar.pack(fill="x", padx=45, pady=8)
        self.progress_bar.set(0)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def toggle_tile_link(self):
        """Toggle tile link: when locked, hide rows entry and mirror cols value."""
        self.tile_link_locked = not self.tile_link_locked
        if self.tile_link_locked:
            val = self.tile_cols_entry.get()
            self.tile_rows_entry.delete(0, tk.END)
            self.tile_rows_entry.insert(0, val)
            self.tile_x_label.pack_forget()
            self.tile_rows_entry.pack_forget()
            self.tile_link_btn.configure(image=self.icons.get('lock'), fg_color=self.highlight_color_adaptive)
            self.play_sound('lock.mp3')
        else:
            val = self.tile_cols_entry.get()
            self.tile_rows_entry.delete(0, tk.END)
            self.tile_rows_entry.insert(0, val)
            self.tile_x_label.pack(side=tk.LEFT, padx=2, before=self.tile_link_btn)
            self.tile_rows_entry.pack(side=tk.LEFT, padx=5, before=self.tile_link_btn)
            self.tile_link_btn.configure(image=self.icons.get('unlock'), fg_color="transparent")
            self.play_sound('unlock.mp3')
        self.on_grid_entry_change()

    def on_grid_entry_change(self, event=None):
        """Validates grid entries and updates frame display. Syncs rows→cols when tile link locked."""
        if self.tile_link_locked:
            val = self.tile_cols_entry.get()
            self.tile_rows_entry.delete(0, tk.END)
            self.tile_rows_entry.insert(0, val)
        if self.cap or self.image_paths:
            self.check_timing_warnings(show_popup=False)
        self.update_frame_display()

    def get_grid_dimensions(self):
        """Parses and validates Columns and Rows numeric inputs securely."""
        try:
            cols = int(self.tile_cols_entry.get())
            if cols < 1:
                cols = 1
        except ValueError:
            cols = 2  # Safe fallback for incomplete typing entries

        try:
            rows = int(self.tile_rows_entry.get())
            if rows < 1:
                rows = 1
        except ValueError:
            rows = 2  # Safe fallback for incomplete typing entries

        # To prevent 1x1 configuration bypasses
        if cols == 1 and rows == 1:
            cols = 2
            rows = 2
            
        return cols, rows

    def draw_window_grid(self, event=None, forced_mode=None):
        """Generates a mathematically perfect, infinitely scalable 1-pixel grid dynamically."""
        if event and event.widget != self.root:
            return
            
        ww = self.root.winfo_width()
        wh = self.root.winfo_height()
        curr_mode = forced_mode if forced_mode else ctk.get_appearance_mode().lower()
        
        if ww == self.last_grid_w and wh == self.last_grid_h and curr_mode == self.last_grid_mode:
            return
            
        self.last_grid_w = ww
        self.last_grid_h = wh
        self.last_grid_mode = curr_mode

        draw_grid_on_canvas(self.bg_canvas, ww, wh, curr_mode)

    def sync_speed_widget_image(self):
        self.speed_widget.delete("speed_img")
        is_enabled = (self.cap is not None) and (not self.playing)
        p_img = f"{self.speed}.png" if is_enabled else f"{self.speed}_disabled.png"
        p = os.path.join(self.img_path, p_img)
        if os.path.exists(p):
            img = Image.open(p)
            self.speed_tk_imgs[self.speed] = ImageTk.PhotoImage(img)
            self.speed_widget.create_image(0, 0, image=self.speed_tk_imgs[self.speed], anchor="nw", tags="speed_img")
        
        curr_mode = ctk.get_appearance_mode().lower()
        bg_color = "#2b2b2b" if curr_mode == "dark" else "#ebebeb"
        self.speed_widget.configure(bg=bg_color)

    def on_speed_widget_click(self, event):
        if not self.cap or self.playing:
            return
        x = event.x
        adjusted_x = x + 4
        if adjusted_x < 44:
            return
        arrow_area_width = self.speed_w - 44
        fraction = (adjusted_x - 44) / arrow_area_width
        clicked_speed = int(fraction * 8) + 1
        clicked_speed = max(1, min(clicked_speed, 8))
        self.set_flipnote_speed(clicked_speed)

    def set_flipnote_speed(self, speed_idx):
        self.speed = speed_idx
        self.sync_speed_widget_image()
        self.play_sound(f'speed{self.speed}.mp3')
        if self.cap:
            self.check_timing_warnings(show_popup=False)

    def toggle_singular_view_mode(self):
        """Swaps active frame container states between single-image edit mode and grid reorder mode."""
        self.play_sound('apply.mp3')
        if self.current_singular_view == "preview":
            self.switch_to_grid_view()
        else:
            self.switch_to_preview_view()

    def switch_to_grid_view(self):
        self.current_singular_view = "grid"
        self.toggle_view_btn.configure(text="Switch to Preview View")
        
        # Hide standard single canvas preview, reveal grid container
        self.video_canvas.pack_forget()
        self.grid_scroll_frame.pack(fill="both", expand=True)
        
        # Show reordering / deletion control panel
        self.grid_controls_row.pack(pady=5)
        
        self.populate_thumbnail_grid()
        self.repack_singular_image_layout()

    def switch_to_preview_view(self):
        self.current_singular_view = "preview"
        self.toggle_view_btn.configure(text="Switch to Grid View")
        
        # Stop scroll monitor, hide grid container, restore preview canvas
        self._stop_thumb_scroll()
        self.grid_scroll_frame.pack_forget()
        self.grid_controls_row.pack_forget()
        self.video_canvas.pack(fill="both", expand=True)
        
        self.update_frame_display()
        self.repack_singular_image_layout()

    def single_click_grid_image(self, index):
        """Select grid thumbnail, updates selection visuals in-place without full rerender."""
        self.play_sound('apply.mp3')
        old_idx = self.still_index
        self.still_index = index
        self.video_path = self.image_paths[self.still_index]

        if len(self.image_paths) == 1:
            self.file_name_label.configure(text=os.path.basename(self.video_path))
        else:
            self.file_name_label.configure(text=f"{len(self.image_paths)} Images Loaded")

        self._update_thumb_selection(old_idx, index)
        self.update_nav_buttons_state()

    def double_click_grid_image(self, index):
        """Callback to select and automatically switch viewport back to standard single editor canvas."""
        self.play_sound('apply.mp3')
        self.still_index = index
        self.video_path = self.image_paths[self.still_index]
        self.switch_to_preview_view()

    def show_grid_tooltip(self, event, filename):
        """Generates a floating tooltip showing the full un-truncated image filename on hover."""
        self.hide_grid_tooltip()
        
        self._tooltip_win = tk.Toplevel(self.root)
        self._tooltip_win.wm_overrideredirect(True)
        
        # Track cursor coordinate offsets
        x = event.x_root + 15
        y = event.y_root + 10
        self._tooltip_win.wm_geometry(f"+{x}+{y}")
        
        lbl = tk.Label(
            self._tooltip_win,
            text=filename,
            font=(self.font_family, 9),
            bg="#2d2d2d",
            fg=MAIN_COLOR,
            padx=6,
            pady=4,
            relief="solid",
            bd=1,
            highlightthickness=0
        )
        lbl.pack()

    def hide_grid_tooltip(self, event=None):
        """Cleans and destroys floating tooltip instances."""
        if hasattr(self, "_tooltip_win") and self._tooltip_win:
            try:
                self._tooltip_win.destroy()
            except Exception:
                pass
            self._tooltip_win = None

    def _stop_thumb_scroll(self):
        if hasattr(self, '_thumb_batch_id') and self._thumb_batch_id:
            try:
                self.root.after_cancel(self._thumb_batch_id)
            except Exception:
                pass
            self._thumb_batch_id = None

    def populate_thumbnail_grid(self):
        """Progressive-load grid: renders thumbnails in batches so 999+ doesn't freeze. Renders all eventually."""
        for widget in self.grid_scroll_frame.winfo_children():
            try:
                widget.destroy()
            except Exception:
                pass

        if not self.image_paths:
            return

        self._stop_thumb_scroll()

        self._thumbnail_tk_images = []
        self._thumb_rendered = 0
        self._thumb_batch_id = None
        self._thumb_cells = {}  # idx -> tk.Frame, for in-place selection/move updates

        self._thumb_render_next_batch()

    def _thumb_render_next_batch(self):
        """Render the next chunk of thumbnails. Schedule another chunk if more remain."""
        BATCH = 20
        cols = 3
        start = self._thumb_rendered
        end = min(self._thumb_rendered + BATCH, len(self.image_paths))
        curr_mode = ctk.get_appearance_mode().lower()

        for idx in range(start, end):
            cell = self._build_thumb_cell(idx, curr_mode, cols)
            if cell:
                self._thumb_cells[idx] = cell

        self._thumb_rendered = end

        if self._thumb_rendered < len(self.image_paths):
            self._thumb_batch_id = self.root.after(50, self._thumb_render_next_batch)

    def _build_thumb_cell(self, idx, curr_mode, cols):
        """Create and grid a single thumbnail cell frame. Returns the frame or None."""
        path = self.image_paths[idx]
        try:
            raw_img = Image.open(path)
            flat_img = Image.new("RGB", (70, 52), (26, 26, 26))
            temp_img = raw_img.copy()
            temp_img.thumbnail((70, 52), Image.Resampling.NEAREST)
            x_offset = (70 - temp_img.width) // 2
            y_offset = (52 - temp_img.height) // 2
            if raw_img.mode in ("RGBA", "LA") or (raw_img.mode == "P" and "transparency" in raw_img.info):
                try:
                    alpha_mask = temp_img.convert("RGBA").split()[3]
                    flat_img.paste(temp_img, (x_offset, y_offset), mask=alpha_mask)
                except Exception:
                    flat_img.paste(temp_img.convert("RGB"), (x_offset, y_offset))
            else:
                flat_img.paste(temp_img.convert("RGB"), (x_offset, y_offset))

            tk_thumb = ImageTk.PhotoImage(flat_img)
            self._thumbnail_tk_images.append(tk_thumb)

            is_selected = (idx == self.still_index)
            if curr_mode == "light":
                border_clr = self.highlight_color if is_selected else "#e5e7eb"
                bg_clr = "#e5e7eb" if is_selected else "#f3f4f6"
                text_clr = "#111827" if is_selected else "#4b5563"
            else:
                border_clr = self.highlight_color if is_selected else "#1a1a1a"
                bg_clr = "#2d2d2d" if is_selected else "#1a1a1a"
                text_clr = self.main_color_adaptive[1] if is_selected else self.sub_color_adaptive[1]

            filename = os.path.basename(path)
            display_name = filename[:9] + "..." if len(filename) > 12 else filename

            cell = tk.Frame(self.grid_scroll_frame, bg=bg_clr,
                            highlightbackground=border_clr,
                            highlightthickness=2 if is_selected else 1,
                            bd=0, width=92, height=105)
            cell.pack_propagate(False)

            img_lbl = tk.Label(cell, image=tk_thumb, bg=bg_clr, bd=0, cursor="hand2")
            img_lbl.pack(pady=(4, 2), padx=4)
            img_lbl.bind("<Enter>", lambda e, name=filename: self.show_grid_tooltip(e, name))
            img_lbl.bind("<Leave>", self.hide_grid_tooltip)
            img_lbl.bind("<Button-1>", lambda e, i=idx: self.single_click_grid_image(i))

            txt_lbl = tk.Label(cell, text=f"Frame {idx + 1}\n{display_name}",
                               font=(self.font_family, 9), fg=text_clr, bg=bg_clr,
                               justify="center", cursor="hand2")
            txt_lbl.pack(pady=(0, 4), padx=4, fill="x")
            txt_lbl.bind("<Enter>", lambda e, name=filename: self.show_grid_tooltip(e, name))
            txt_lbl.bind("<Leave>", self.hide_grid_tooltip)
            txt_lbl.bind("<Button-1>", lambda e, i=idx: self.single_click_grid_image(i))

            row = idx // cols
            col = idx % cols
            cell.grid(row=row, column=col, padx=8, pady=8)
            return cell
        except Exception as e:
            print(f"Thumb cell {idx} error: {e}")
            return None

    def _update_thumb_selection(self, old_idx, new_idx):
        """In-place selection visual update, no full grid rerender."""
        curr_mode = ctk.get_appearance_mode().lower()
        if curr_mode == "light":
            sel_bg, sel_border = "#e5e7eb", self.highlight_color
            def_bg, def_border = "#f3f4f6", "#e5e7eb"
        else:
            sel_bg, sel_border = "#2d2d2d", self.highlight_color
            def_bg, def_border = "#1a1a1a", "#1a1a1a"

        for idx, bg, border, thick in [
            (old_idx, def_bg, def_border, 1),
            (new_idx, sel_bg, sel_border, 2),
        ]:
            cell = self._thumb_cells.get(idx)
            if not cell:
                continue
            cell.configure(bg=bg, highlightbackground=border, highlightthickness=thick)
            for child in cell.winfo_children():
                try:
                    child.configure(bg=bg)
                except Exception:
                    pass

    def _swap_thumb_cells(self, idx1, idx2):
        """Swap two cells' positions and content in-place without full rerender."""
        if idx1 not in self._thumb_cells or idx2 not in self._thumb_cells:
            return
        cell1 = self._thumb_cells[idx1]
        cell2 = self._thumb_cells[idx2]
        info1, info2 = cell1.grid_info(), cell2.grid_info()
        cell1.grid_remove()
        cell2.grid_remove()
        cell1.grid(row=info2['row'], column=info2['column'], padx=8, pady=8)
        cell2.grid(row=info1['row'], column=info1['column'], padx=8, pady=8)
        self._thumb_cells[idx1], self._thumb_cells[idx2] = cell2, cell1

    def delete_selected_frame(self):
        """Deletes selected frame, automatically adjusting bounds and resetting indices."""
        if not self.image_paths or len(self.image_paths) <= 1:
            return
        self.play_sound('del.mp3')
        del self.image_paths[self.still_index]

        self.still_index = min(self.still_index, len(self.image_paths) - 1)
        self.video_path = self.image_paths[self.still_index]
        if len(self.image_paths) == 1:
            self.file_name_label.configure(text=os.path.basename(self.video_path))
            self.export_btn.configure(text="Export Frame")
        else:
            self.file_name_label.configure(text=f"{len(self.image_paths)} Images Loaded")
            self.export_btn.configure(text="Export Frames")

        if self.current_singular_view == "grid":
            self.populate_thumbnail_grid()
        else:
            self.update_frame_display()
        self.update_nav_buttons_state()
        self.check_timing_warnings(show_popup=False)

    def move_frame_left(self):
        """Shifts selected image index left, swaps cells in-place."""
        if self.still_index > 0:
            self.play_sound('apply.mp3')
            idx = self.still_index
            self.image_paths[idx], self.image_paths[idx - 1] = self.image_paths[idx - 1], self.image_paths[idx]
            self.still_index -= 1
            self.video_path = self.image_paths[self.still_index]
            self._swap_thumb_cells(idx, idx - 1)
            self._update_thumb_selection(idx, self.still_index)
            self.update_nav_buttons_state()

    def move_frame_right(self):
        """Shifts selected image index right, swaps cells in-place."""
        if self.still_index < len(self.image_paths) - 1:
            self.play_sound('apply.mp3')
            idx = self.still_index
            self.image_paths[idx], self.image_paths[idx + 1] = self.image_paths[idx + 1], self.image_paths[idx]
            self.still_index += 1
            self.video_path = self.image_paths[self.still_index]
            self._swap_thumb_cells(idx, idx + 1)
            self._update_thumb_selection(idx, self.still_index)
            self.update_nav_buttons_state()

    def load_user_settings(self):
        """Restores session parameters from .sigmaflip_config.json on startup."""
        if not os.path.exists(self.config_filepath):
            return
        try:
            with open(self.config_filepath, "r") as f:
                config = json.load(f)

            # Load Advanced Settings State Safely
            advanced = config.get("advanced_settings", {})
            for k in self.advanced_settings:
                if k in advanced:
                    self.advanced_settings[k] = advanced[k]
            self.advanced_settings["pixel_precision"] = bool(self.advanced_settings.get("pixel_precision", False))
            self.advanced_settings["black_and_white"] = bool(self.advanced_settings.get("black_and_white", False))
            self.advanced_settings["contrast"] = max(0.1, min(3.0, float(self.advanced_settings.get("contrast", 1.0))))
            self.advanced_settings["album_capacity"] = max(1, int(self.advanced_settings.get("album_capacity", 100)))

            # Load Main UI parameters
            self.audio_enabled = config.get("audio_enabled", True)
            self.bg_type = config.get("bg_type", "black")
            self.bg_image_path = config.get("bg_image_path", None)
            self.export_structure = config.get("export_structure", "dcim")
            self.console_type = config.get("console_type", "dsi")
            self.bg_type_var.set(self.bg_type)
            self.export_structure_var.set(self.export_structure)
            
            self.options_menu.entryconfigure(
                0,
                label="✓ Enable Audio Preview" if self.audio_enabled else "   Enable Audio Preview"
            )
            self.set_bg_menu_type(self.bg_type, silent=True)
            self.set_struct_menu_type(self.export_structure, silent=True)
            self.set_console_type(self.console_type)

            # Map scaling parameters back to OptionMenu representations
            scale_modes_map = {
                "Fit": "Fit (Letterbox)",
                "Stretch": "Stretch",
                "Crop": "Crop (4:3)",
                "Tiles": "Tiles",
                "Tiles Stretched": "Tiles Stretched"
            }
            raw_scale = config.get("scale_mode", "Fit")
            self.scale_mode = raw_scale if raw_scale in scale_modes_map else "Fit"
            self.aspect_menu.set(scale_modes_map.get(self.scale_mode, "Fit (Letterbox)"))

            # Apply layout triggers based on stored export mode preferences
            stored_mode = config.get("export_mode", "Video Frames")
            self.export_mode_var.set(stored_mode)
            self.export_mode_menu.set(stored_mode)
            
            if stored_mode == "Singular Image":
                self.repack_singular_image_layout()
                self.update_nav_buttons_state()
            else:
                self.repack_video_layout()

        except Exception as e:
            print(f"Error restoring user configurations: {e}")

    def save_user_settings(self):
        """Saves current session settings to .sigmaflip_config.json."""
        try:
            config_data = {
                "export_mode": self.export_mode_var.get(),
                "audio_enabled": self.audio_enabled,
                "bg_type": self.bg_type,
                "bg_image_path": self.bg_image_path if self.bg_image_path else "",
                "export_structure": self.export_structure,
                "console_type": self.console_type,
                "scale_mode": self.scale_mode,
                "advanced_settings": self.advanced_settings
            }
            with open(self.config_filepath, "w") as f:
                json.dump(config_data, f, indent=4)
        except Exception as e:
            print(f"Error saving user configurations: {e}")

    def purge_pycache_directories(self):
        """Recursively purges __pycache__ directories within the application directory footprint."""
        try:
            for root_dir, dirs, files in os.walk(self.base_path):
                for d in dirs:
                    if d == "__pycache__":
                        pycache_path = os.path.join(root_dir, d)
                        shutil.rmtree(pycache_path, ignore_errors=True)
        except Exception as e:
            print(f"Error purging __pycache__ on close: {e}")

    def _forget_all_widgets(self):
        for w in (
            self.timeline_slider, self.limit_indicator, self.tile_config_row,
            self.sf_frame, self.config_row, self.ctrl_row, self.progress_bar,
            self.nav_row, self.grid_controls_row, self.toggle_view_btn,
            self.grid_scroll_frame, self.load_btn, self.play_btn, self.export_btn,
        ):
            w.pack_forget()

    def repack_video_layout(self):
        self._forget_all_widgets()

        self.video_canvas.pack(fill="both", expand=True)
        self.current_singular_view = "preview"
        self.toggle_view_btn.configure(text="Switch to Grid View")
        self.export_btn.configure(text="Export Frames")

        # Sequential packing order
        self.timeline_slider.pack(fill="x", padx=40, pady=5)
        self.limit_indicator.pack(pady=3)
        if self.scale_mode in ("Tiles", "Tiles Stretched"):
            self.tile_config_row.pack(pady=4)
        self.sf_frame.pack(pady=4)
        self.config_row.pack(pady=4, fill="x", padx=45)

        self.load_btn.pack(side=tk.LEFT, padx=3)
        self.play_btn.pack(side=tk.LEFT, padx=3)
        self.export_btn.pack(side=tk.LEFT, padx=3)

        self.ctrl_row.pack(pady=5)
        self.progress_bar.pack(fill="x", padx=45, pady=8)

    def repack_singular_image_layout(self):
        self._forget_all_widgets()

        self.toggle_view_btn.pack(pady=4)
        if self.current_singular_view == "grid":
            self.grid_scroll_frame.pack(fill="both", expand=True)
            self.grid_controls_row.pack(pady=5)
            self.nav_row.pack(pady=5)  # Keep the counter and index labels globally mapped in both viewports
        else:
            self.video_canvas.pack(fill="both", expand=True)
            self.nav_row.pack(pady=5)

        self.limit_indicator.pack(pady=3)
        if self.scale_mode in ("Tiles", "Tiles Stretched"):
            self.tile_config_row.pack(pady=4)
        self.config_row.pack(pady=4, fill="x", padx=45)

        # Control Buttons packed without the play trigger button
        self.load_btn.pack(side=tk.LEFT, padx=3)
        self.export_btn.pack(side=tk.LEFT, padx=3)

        self.ctrl_row.pack(pady=5)
        self.progress_bar.pack(fill="x", padx=45, pady=8)

    def on_export_mode_change(self, value):
        """Handles Mode changes, altering frame requirements, speeds and layouts."""
        self.play_sound('apply.mp3')
        
        # Reset current files loaded
        if self.cap:
            self.cap.release()
            self.cap = None
        self.video_path = None
        self.image_paths = []
        self.file_name_label.configure(text="No File Loaded")
        self.play_btn.configure(state="disabled")
        self.export_btn.configure(state="disabled")
        self.current_frame_idx = 0.0
        self.video_canvas.delete("all")

        # Cleanup audio assets
        self._cleanup_audio()

        if value == "Singular Image":
            self.current_singular_view = "preview"
            self.toggle_view_btn.configure(text="Switch to Grid View")
            self.repack_singular_image_layout()
            self.file_name_label.configure(text="No Image Loaded")
            self.export_btn.configure(text="Export Frame")
            self.limit_indicator.configure(text="Export Frames: 0 / 999", text_color=SUB_COLOR)
            self.update_nav_buttons_state()
        else:
            self.repack_video_layout()
            self.play_btn.configure(state="disabled")
            self.file_name_label.configure(text="No Video Loaded")
            self.limit_indicator.configure(
                text="Export Frames: 0 / 999",
                text_color=SUB_COLOR
            )
            self.export_btn.configure(text="Export Frames")

    def load_video_dialog(self):
        self.play_sound('upload.mp3')
        
        # Adjust accepted dialog extensions based on Export mode selections
        if self.export_mode_var.get() == "Singular Image":
            # Multi-image selection support
            file_paths = filedialog.askopenfilenames(
                filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.webp")]
            )
            if not file_paths:
                self.play_sound('back.mp3')
                return

            self.image_paths = list(file_paths)
            self.still_index = 0
            self.video_path = self.image_paths[self.still_index]  # Store first file path
            
            # Setup layout constraints based on load volume
            if len(self.image_paths) == 1:
                self.file_name_label.configure(text=os.path.basename(self.video_path))
                self.export_btn.configure(text="Export Frame")
            else:
                self.file_name_label.configure(text=f"{len(self.image_paths)} Images Loaded")
                self.export_btn.configure(text="Export Frames")
                
            self.export_btn.configure(state="normal")
            self.update_nav_buttons_state()
            
            if self.current_singular_view == "grid":
                self.populate_thumbnail_grid()
            else:
                self.update_frame_display()
                
            self.check_timing_warnings(show_popup=False)
            return

        # Standard Video Processing
        file_path = filedialog.askopenfilename(
            filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv *.gif")]
        )
        if not file_path:
            self.play_sound('back.mp3')
            return

        if self.cap:
            self.cap.release()

        # Stop previous audio tracking and cleanup previous audio file
        self._cleanup_audio()

        self.video_path = file_path
        self.image_paths = []
        self.file_name_label.configure(text=os.path.basename(file_path))
        self.export_btn.configure(state="normal")

        # Standard video processing initialization
        self.cap = cv2.VideoCapture(file_path)
        self.total_video_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.video_fps = self.cap.get(cv2.CAP_PROP_FPS)
        if self.video_fps <= 0:
            self.video_fps = 24.0
        if file_path.lower().endswith('.gif') and self.total_video_frames > 1:
            try:
                gif = Image.open(file_path)
                total_ms = 0
                for i in range(getattr(gif, 'n_frames', self.total_video_frames)):
                    gif.seek(i)
                    total_ms += gif.info.get('duration', 100)
                gif.close()
                if total_ms > 0:
                    self.video_fps = self.total_video_frames / (total_ms / 1000.0)
            except Exception:
                pass
        self.video_duration = self.total_video_frames / self.video_fps

        self.play_btn.configure(state="normal")
        self.current_frame_idx = 0.0
        self.timeline_slider.configure(from_=0, to=self.total_video_frames - 1)
        self.timeline_slider.set(0)

        # Extract audio in background if toggle is checked
        if self.audio_enabled:
            self.temp_audio_path = os.path.join(tempfile.gettempdir(), f"sigmaflip_preview_{int(time.time())}.wav")
            threading.Thread(target=self.extract_audio_thread, daemon=True).start()

        self.sync_speed_widget_image()
        self.check_timing_warnings(show_popup=True)
        self.update_frame_display()

    def show_prev_image(self):
        """Navigates to the previous still image, in-place selection update in grid view."""
        if self.still_index > 0:
            old_idx = self.still_index
            self.still_index -= 1
            self.play_sound('prev.mp3')
            self.video_path = self.image_paths[self.still_index]
            self.update_nav_buttons_state()
            if self.current_singular_view == "grid":
                self._update_thumb_selection(old_idx, self.still_index)
            else:
                self.update_frame_display()

    def show_next_image(self):
        """Navigates to the next still image, in-place selection update in grid view."""
        if self.still_index < len(self.image_paths) - 1:
            old_idx = self.still_index
            self.still_index += 1
            self.play_sound('next.mp3')
            self.video_path = self.image_paths[self.still_index]
            self.update_nav_buttons_state()
            if self.current_singular_view == "grid":
                self._update_thumb_selection(old_idx, self.still_index)
            else:
                self.update_frame_display()

    def jump_to_beginning(self):
        """Skip to the first image."""
        if self.still_index == 0 or not self.image_paths:
            return
        old_idx = self.still_index
        self.still_index = 0
        self.play_sound('beg.mp3')
        self.video_path = self.image_paths[0]
        self.update_nav_buttons_state()
        if self.current_singular_view == "grid":
            self._update_thumb_selection(old_idx, 0)
        else:
            self.update_frame_display()

    def jump_to_end(self):
        """Skip to the last image."""
        if not self.image_paths:
            return
        last = len(self.image_paths) - 1
        if self.still_index == last:
            return
        old_idx = self.still_index
        self.still_index = last
        self.play_sound('end.mp3')
        self.video_path = self.image_paths[last]
        self.update_nav_buttons_state()
        if self.current_singular_view == "grid":
            self._update_thumb_selection(old_idx, last)
        else:
            self.update_frame_display()

    def update_nav_buttons_state(self):
        """Updates states and swaps standard/disabled navigation images on the fly."""
        total = len(self.image_paths)
        
        # Keep delete button interactively locked when only one frame remains in the list
        if hasattr(self, "delete_frame_btn"):
            if total <= 1:
                self.delete_frame_btn.configure(state="disabled")
            else:
                self.delete_frame_btn.configure(state="normal")

        if total <= 1:
            self.beg_btn.configure(state="disabled", image=self.icons.get('beg_disabled'))
            self.prev_btn.configure(state="disabled", image=self.icons.get('prev_disabled'))
            self.next_btn.configure(state="disabled", image=self.icons.get('next_disabled'))
            self.end_btn.configure(state="disabled", image=self.icons.get('end_disabled'))
            self.nav_label.configure(text=f"1 of {total}" if total == 1 else "0 of 0")
            return
            
        self.nav_label.configure(text=f"{self.still_index + 1} of {total}")
        
        # Beginning button
        if self.still_index == 0:
            self.beg_btn.configure(state="disabled", image=self.icons.get('beg_disabled'))
        else:
            self.beg_btn.configure(state="normal", image=self.icons.get('beg'))
            
        # Previous navigation validation
        if self.still_index == 0:
            self.prev_btn.configure(state="disabled", image=self.icons.get('prev_disabled'))
        else:
            self.prev_btn.configure(state="normal", image=self.icons.get('prev'))
            
        # Next navigation validation
        if self.still_index == total - 1:
            self.next_btn.configure(state="disabled", image=self.icons.get('next_disabled'))
        else:
            self.next_btn.configure(state="normal", image=self.icons.get('next'))
            
        # End button
        if self.still_index == total - 1:
            self.end_btn.configure(state="disabled", image=self.icons.get('end_disabled'))
        else:
            self.end_btn.configure(state="normal", image=self.icons.get('end'))

    def extract_audio_thread(self):
        """Asynchronously extracts the audio from the video clip."""
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            return
        cmd = [
            ffmpeg_bin, "-y", "-i", self.video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
            self.temp_audio_path
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            if os.path.exists(self.temp_audio_path) and os.path.getsize(self.temp_audio_path) > 0:
                self.root.after(0, self.load_extracted_audio)
        except Exception:
            pass

    def load_extracted_audio(self):
        try:
            pygame.mixer.music.load(self.temp_audio_path)
            self.has_audio = True
        except Exception:
            self.has_audio = False

    def check_timing_warnings(self, show_popup=False):
        """Processes video frame warnings preventing continuous dialog prompts during slider changes."""
        if self.export_mode_var.get() == "Singular Image":
            self.update_indicator_metrics(0)
            return

        target_fps = SPEED_FPS[self.speed]
        frame_step = max(1, round(self.video_fps / target_fps))
        estimated_frames = (self.total_video_frames - 1) // frame_step + 1

        # Trigger message box dialog block only when requested
        if show_popup and self.video_duration > WARNING_DURATION:
            self.play_sound('warning.mp3')
            messagebox.showwarning(
                "SIGMAFLIP Warning",
                f"Your selected video is {self.video_duration:.1f} seconds long, which exceeds the recommended 60-second limit.\n\n"
                "Flipnote Studio limits files to 999 frames and enforces strict per-frame memory caps "
                "(derived from ink usage, line counts, and color complexity).\n\n"
                f"At the chosen Speed {self.speed} ({target_fps} FPS), this will yield approximately {estimated_frames} frames.\n\n"
                "To avoid app crashes or failed imports, please consider trimming this video "
                "or opting for a lower playback speed setting."
            )
        self.update_indicator_metrics(estimated_frames)

    def update_indicator_metrics(self, estimated_frames):
        if self.export_mode_var.get() == "Singular Image":
            total_images = len(self.image_paths)
            if total_images > 1:
                self.limit_indicator.configure(
                    text=f"Export Frames: {total_images} (Batch Mode)",
                    text_color=self.main_color_adaptive
                )
            else:
                self.limit_indicator.configure(
                    text="Export Frames: 1",
                    text_color=self.main_color_adaptive
                )
        else:
            if estimated_frames > MAX_FRAMES:
                self.limit_indicator.configure(
                    text=f"Export Frames: {estimated_frames} / {MAX_FRAMES}",
                    text_color="#FF4D4D"
                )
            else:
                self.limit_indicator.configure(
                    text=f"Export Frames: {estimated_frames} / {MAX_FRAMES}",
                    text_color=self.main_color_adaptive
                )

    def set_scale_mode(self, value):
        """Configures self.scale_mode strictly avoiding dynamic substring overlaps."""
        self.play_sound('apply.mp3')
        if value == "Fit (Letterbox)":
            self.scale_mode = "Fit"
            self.tile_config_row.pack_forget()
        elif value == "Stretch":
            self.scale_mode = "Stretch"
            self.tile_config_row.pack_forget()
        elif value == "Crop (4:3)":
            self.scale_mode = "Crop"
            self.tile_config_row.pack_forget()
        elif value in ("Tiles", "Tiles Stretched"):
            self.scale_mode = value
            # Unpack and sequence repeat controller entry fields
            if self.export_mode_var.get() == "Singular Image":
                self.repack_singular_image_layout()
            else:
                self.repack_video_layout()
                
        if self.cap or self.image_paths:
            self.check_timing_warnings(show_popup=False)
        self.update_frame_display()

    def update_frame_display(self):
        if not self.video_path:
            return

        is_singular = (self.export_mode_var.get() == "Singular Image")
        
        # Track rapid rendering (slider dragging or playing) to adjust performance
        curr_time = time.time()
        time_delta = curr_time - self._last_render_time
        self._last_render_time = curr_time
        self._rapid_rendering = (time_delta < 0.08) or self.playing

        # Dynamic drawing split support (Pillow for static image formats, CV2 seek loops for videos)
        if is_singular:
            try:
                pil_img = Image.open(self.video_path).convert("RGBA")
                canvas_w = self.video_canvas.winfo_width() or 320
                canvas_h = self.video_canvas.winfo_height() or 240
                pil_img = self.apply_scaling_to_image(pil_img, canvas_w, canvas_h)
                self.tk_image = ImageTk.PhotoImage(pil_img)
                self.video_canvas.delete("all")
                self.video_canvas.create_image(canvas_w // 2, canvas_h // 2, image=self.tk_image, anchor="center")
            except Exception as e:
                print(f"Still image loading preview error: {e}")
            return

        if not self.cap:
            return

        self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(self.current_frame_idx))
        ret, frame = self.cap.read()
        if not ret:
            return

        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(frame)

        canvas_w = self.video_canvas.winfo_width() or 320
        canvas_h = self.video_canvas.winfo_height() or 240

        pil_img = self.apply_scaling_to_image(pil_img, canvas_w, canvas_h)

        self.tk_image = ImageTk.PhotoImage(pil_img)
        self.video_canvas.delete("all")
        self.video_canvas.create_image(canvas_w // 2, canvas_h // 2, image=self.tk_image, anchor="center")

    def apply_advanced_filters(self, img):
        """Applies configuration settings (contrast, binary black & white, dithering and palette restrictions)."""
        settings = self.advanced_settings
        
        # Apply Contrast Enhancement
        contrast_val = settings.get("contrast", 1.0)
        if contrast_val != 1.0:
            enhancer = ImageEnhance.Contrast(img.convert("RGB"))
            img = enhancer.enhance(contrast_val).convert("RGBA")

        # Black and White Processing
        if settings.get("black_and_white", False):
            gray_img = img.convert("L")
            dither = settings.get("dither_mode", "None")

            if dither == "Floyd-Steinberg":
                dither_algo = getattr(Image, "Dither", None)
                if dither_algo and hasattr(dither_algo, "FLOYDSTEINBERG"):
                    algo = dither_algo.FLOYDSTEINBERG
                else:
                    algo = getattr(Image, "FLOYDSTEINBERG", 3)
                bw_img = gray_img.convert("1", dither=algo)
            elif dither in ("Bayer 2x2", "Bayer 3x3", "Bayer 4x4", "Bayer 8x8", "Halftone", "Flipnote Memory Saver (Experimental)"):
                bw_img = self.apply_ordered_dither(gray_img, dither)
            elif dither in ("Atkinson", "Burkes", "Jarvis-Judice-Ninke", "Stucki", 
                            "Sierra 3-Row", "Sierra 2-Row", "Sierra Lite", "Stevenson-Arce"):
                bw_img = self.apply_error_diffusion(gray_img, dither)
            else:
                bw_img = gray_img.point(lambda x: 255 if x > 127 else 0, mode="1")
            
            img = bw_img.convert("RGBA")

        # Advanced Pixel Precision (Quantize levels to prevent digital gradient noise)
        if settings.get("pixel_precision", False):
            img = img.convert("P", palette=Image.Palette.ADAPTIVE, colors=64).convert("RGBA")

        return img

    def apply_ordered_dither(self, gray_img, mode):
        """Standardized routing mapping ordered dither patterns through Modular Dither library."""
        return apply_ordered_dither(gray_img, mode)

    def apply_error_diffusion(self, gray_img, kernel_name):
        """Standardized routing mapping error diffusion patterns through Modular Dither library."""
        return apply_error_diffusion(gray_img, kernel_name, self._exporting, self._rapid_rendering)

    def apply_scaling_to_image(self, img, target_w, target_h):
        orig_w, orig_h = img.size
        bg_type = self.bg_type_var.get()
        
        # Resolve Background Canvas globally based on active configurations
        if bg_type == "white":
            bg = Image.new("RGB", (target_w, target_h), "white")
        elif bg_type == "custom" and self.bg_image_path and os.path.exists(self.bg_image_path):
            try:
                bg = Image.open(self.bg_image_path).convert("RGB")
                bg = bg.resize((target_w, target_h), Image.Resampling.NEAREST)
            except Exception as e:
                print(f"Error loading Custom Background Fill: {e}")
                bg = Image.new("RGB", (target_w, target_h), "black")
        else:
            bg = Image.new("RGB", (target_w, target_h), "black")
            
        # Convert input image to RGBA to preserve transparent layers
        img_rgba = img.convert("RGBA")

        # Draw active layout structures using crisp retro NEAREST pixel resizing
        if self.scale_mode in ("Tiles", "Tiles Stretched"):
            cols, rows = self.get_grid_dimensions()
            
            # Calculation for grid sub-tile dimensions
            tile_w = target_w // cols
            tile_h = target_h // rows
            
            if self.scale_mode == "Tiles Stretched":
                # Stretched variant: Stretch each sub-tile directly to fill custom entry dimensions
                img_copy = img_rgba.resize((tile_w, tile_h), Image.Resampling.NEAREST)
                for r in range(rows):
                    for col in range(cols):
                        x_offset = col * tile_w
                        y_offset = r * tile_h
                        bg.paste(img_copy, (x_offset, y_offset), mask=img_copy)
            else:
                # Standard variant: Preserves original aspect ratios within each sub-tile centered
                img_copy = img_rgba.copy()
                img_copy.thumbnail((tile_w, tile_h), Image.Resampling.NEAREST)
                for r in range(rows):
                    for col in range(cols):
                        x_offset = col * tile_w + (tile_w - img_copy.width) // 2
                        y_offset = r * tile_h + (tile_h - img_copy.height) // 2
                        bg.paste(img_copy, (x_offset, y_offset), mask=img_copy)
            bg_final = bg
            
        elif self.scale_mode == "Fit":
            img_copy = img_rgba.copy()
            img_copy.thumbnail((target_w, target_h), Image.Resampling.NEAREST)
            bg.paste(img_copy, ((target_w - img_copy.width) // 2, (target_h - img_copy.height) // 2), mask=img_copy)
            bg_final = bg
        elif self.scale_mode == "Stretch":
            img_copy = img_rgba.resize((target_w, target_h), Image.Resampling.NEAREST)
            bg.paste(img_copy, (0, 0), mask=img_copy)
            bg_final = bg
        else:
            scale = max(target_w / orig_w, target_h / orig_h)
            new_w = int(orig_w * scale)
            new_h = int(orig_h * scale)
            img_scaled = img_rgba.resize((new_w, new_h), Image.Resampling.NEAREST)
            left = (new_w - target_w) // 2
            top = (new_h - target_h) // 2
            img_cropped = img_scaled.crop((left, top, left + target_w, top + target_h))
            bg.paste(img_cropped, (0, 0), mask=img_cropped)
            bg_final = bg

        return self.apply_advanced_filters(bg_final)

    def on_slider_scrub(self, val):
        frame_idx = float(val)
        target_fps = SPEED_FPS[self.speed]
        frame_step = max(1, round(self.video_fps / target_fps))
        if frame_step > 1:
            frame_idx = int(frame_idx // frame_step) * frame_step
        self.current_frame_idx = frame_idx
        self.timeline_slider.set(int(self.current_frame_idx))
        self.update_frame_display()
        if self._music_paused:
            # Seek invalidates the paused audio position; resume must restart from here
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
            self._music_paused = False

    def toggle_play(self):
        if not self.cap or (self.export_mode_var.get() == "Singular Image"):
            return

        if self.playing:
            self.playing = False
            self.play_btn.configure(text="", image=self.icons.get('play'))
            self.play_sound('stoppause.mp3')
            
            try:
                pygame.mixer.music.pause()
                self._music_paused = True
            except Exception:
                self._music_paused = False
                pass

            if self.after_play_id:
                self.root.after_cancel(self.after_play_id)
                self.after_play_id = None
            self.sync_speed_widget_image()
        else:
            self.playing = True
            self.play_btn.configure(text="", image=self.icons.get('pause'))
            self.play_sound('playresume.mp3')
            self.sync_speed_widget_image()
            
            # Anchor real-time clock properties
            self._playback_start_time = time.time()
            self._playback_start_frame = self.current_frame_idx
            
            self.start_audio_at_current_frame()
            self.playback_tick()

    def start_audio_at_current_frame(self):
        """Resumes paused WAV track, or restarts it at the current timeline position."""
        if not (self.audio_enabled and self.has_audio):
            return
        if self._music_paused:
            try:
                pygame.mixer.music.unpause()
                self._music_paused = False
                self._music_pos_anchor = pygame.mixer.music.get_pos()
                return
            except Exception:
                self._music_paused = False
        start_sec = self.current_frame_idx / self.video_fps
        try:
            pygame.mixer.music.play(start=start_sec)
            self._music_pos_anchor = 0
        except Exception:
            pass

    def playback_tick(self):
        if not self.playing:
            return

        target_fps = SPEED_FPS[self.speed]
        frame_step = max(1, round(self.video_fps / target_fps))
        elapsed = time.time() - self._playback_start_time
        
        # Audio Hybrid Sync calculation keeps frame increments and Pygame timeline locked in 1:1 sync
        if self.audio_enabled and self.has_audio:
            music_pos = pygame.mixer.music.get_pos()
            if music_pos != -1:
                self.current_frame_idx = self._playback_start_frame + ((music_pos - self._music_pos_anchor) / 1000.0) * self.video_fps
            else:
                self.current_frame_idx = self._playback_start_frame + (elapsed * self.video_fps)
        else:
            self.current_frame_idx = self._playback_start_frame + (elapsed * self.video_fps)

        # Snapshot the exported frame indices (select every Nth frame) so the preview
        # always shows exactly the frames the pipeline will convert.
        if frame_step > 1:
            self.current_frame_idx = int(self.current_frame_idx // frame_step) * frame_step

        if self.current_frame_idx >= self.total_video_frames:
            self.current_frame_idx = 0.0
            self._playback_start_time = time.time()
            self._playback_start_frame = 0.0
            self._music_paused = False
            self.start_audio_at_current_frame()

        self.timeline_slider.set(int(self.current_frame_idx))
        self.update_frame_display()

        # Self-correcting timer: fire at the next whole interval boundary so cumulative
        # callback lag never drifts the preview ahead of the converted frames.
        interval = 1.0 / target_fps
        next_at = interval * (math.floor(elapsed / interval) + 1)
        delay_ms = max(0, int((next_at - elapsed) * 1000))
        self.after_play_id = self.root.after(delay_ms, self.playback_tick)

    DSI_SIG_PADDING = 512  # bytes of COM comment padding for signature area
    DSI_JPEG_KEY = bytes.fromhex("70885206DFE5016D45EAC52333D6446F")  # DSi photo AES key (from DSi bootrom)
    DSI_NATIVE_W = 256
    DSI_NATIVE_H = 192
    

    def _gf_mul2(self, block: bytes) -> bytes:
        """GF(2^128) multiply by 2; matches dsi_jpeg_signature_tool's weird_func exactly."""
        x = int.from_bytes(block, 'little')
        y = (x << 1) & ((1 << 128) - 1)
        if x >> 127:
            y ^= 0x87
        return y.to_bytes(16, 'little')

    def _dsi_ccm_tag(self, data: bytes, nonce: bytes) -> bytes:
        """AES-128-CCM MAC over the whole JPEG with the 1Ch signature slot zeroed,
        using the byte-reversed variant and CMAC tail-block transform that the
        DSi photo app actually verifies (matches dsi_jpeg_signature_tool main.c)."""
        key = self.DSI_JPEG_KEY
        rev_key = key[::-1]
        ecb = AES.new(rev_key, AES.MODE_ECB)

        size = len(data)
        total_size = (size + 15) & ~15
        buf = bytearray(data)
        buf.extend(b'\x00' * (total_size - size))
        buf[0x18A:0x1A6] = b'\x00' * 0x1C

        block = ecb.encrypt(b'\x00' * 16)[::-1]
        block = self._gf_mul2(block)
        final_bytes = ((size - 1) & 0xF) + 1
        if final_bytes == 0x10:
            # Reference only applies gf_mul2 once before this branch; the last
            # aligned block is xored in directly (main.c final_bytes == 0x10).
            block = bytes(a ^ b for a, b in zip(block, bytes(buf[size - 16:size])))
        else:
            tmp = bytearray(16)
            tmp[16 - final_bytes:] = buf[size - final_bytes:size]
            tmp[15 - final_bytes] = 0x80
            block = bytes(a ^ b for a, b in zip(self._gf_mul2(block), bytes(tmp)))
        buf[size - final_bytes:size - final_bytes + 16] = block

        b0 = bytes([0x7A]) + nonce[::-1] + b'\x00\x00\x00'
        mac_state = ecb.encrypt(b0)
        for off in range(0, total_size, 16):
            blk = bytes(buf[off:off + 16])[::-1]
            mac_state = ecb.encrypt(bytes(a ^ b for a, b in zip(blk, mac_state)))

        ctr = bytes([2]) + nonce[::-1] + b'\x00\x00\x00'
        s0 = ecb.encrypt(ctr)[::-1]
        return bytes(a ^ b for a, b in zip(mac_state[::-1], s0))

    def sign_jpeg_dsi(self, data: bytes) -> bytes:
        """Embed the genuine DSi AES-128-CCM signature (IV at 0x18A, MAC at 0x196)."""
        nonce = get_random_bytes(12)
        tag = self._dsi_ccm_tag(data, nonce)
        out = bytearray(data)
        out[0x18A:0x18A + 12] = nonce
        out[0x196:0x196 + 16] = tag
        return bytes(out)

    def verify_dsi_signature(self, data: bytes) -> bool:
        """Programmatically validates the DSi AES-128-CCM signature of a generated JPEG file."""
        try:
            if len(data) < 0x1A6:
                return False
            nonce = bytes(data[0x18A:0x18A + 12])
            tag_stored = bytes(data[0x196:0x196 + 16])
            return self._dsi_ccm_tag(data, nonce) == tag_stored
        except Exception:
            return False

    def verify_jpeg_structure(self, data: bytes) -> bool:
        """Verifies that the generated image payload is structurally readable by standard decoders."""
        try:
            with Image.open(io.BytesIO(data)) as img:
                img.verify()
            return True
        except Exception:
            return False

    def build_dsi_exif(self, time_str: str, thumb_jpeg: bytes) -> bytes:
        """Builds the DSi APP1 Exif payload (big-endian TIFF) with a MakerNote whose
        0x1000 tag points to the 1Ch signature slot at TIFF offset 0x17E (= file 0x18A)."""
        def be16(v):
            return struct.pack(">H", v)
        def be32(v):
            return struct.pack(">I", v)

        # IFD0 at 0x08 -> 0x7A
        ifd0 = bytearray(2 + 9 * 12 + 4)
        ifd0[0:2] = be16(9)
        entries0 = [
            (0x010F, 2, 9, 0x7A), (0x0110, 2, 11, 0x84), (0x011A, 5, 1, 0x90),
            (0x011B, 5, 1, 0x98), (0x0128, 3, 1, 0x00020000), (0x0131, 2, 5, 0xA0),
            (0x0132, 2, 20, 0xA6), (0x0213, 3, 1, 0x00020000), (0x8769, 4, 1, 0xBA),
        ]
        for i, (t, ty, c, v) in enumerate(entries0):
            struct.pack_into(">HHII", ifd0, 2 + i * 12, t, ty, c, v)
        ifd0[2 + 9 * 12:2 + 9 * 12 + 4] = be32(0x1DE)

        # Sub IFD at 0xBA -> 0x138
        sub = bytearray(2 + 10 * 12 + 4)
        sub[0:2] = be16(10)
        entries_sub = [
            (0x9000, 7, 4, 0x30323230), (0x9003, 2, 20, 0x138), (0x9004, 2, 20, 0x14C),
            (0x9101, 7, 4, 0x01020300), (0x927C, 7, 66, 0x160), (0xA000, 7, 4, 0x30313030),
            (0xA001, 3, 1, 0x00010000), (0xA002, 4, 1, 0x280), (0xA003, 4, 1, 0x1E0),
            (0xA005, 4, 1, 0x1A2),
        ]
        for i, (t, ty, c, v) in enumerate(entries_sub):
            struct.pack_into(">HHII", sub, 2 + i * 12, t, ty, c, v)
        sub[2 + 10 * 12:2 + 10 * 12 + 4] = be32(0)

        # MakerNote at 0x160 -> 0x17E
        mn = bytearray(2 + 2 * 12 + 4)
        mn[0:2] = be16(2)
        entries_mn = [(0x1000, 7, 0x1C, 0x17E), (0x1001, 7, 8, 0x19A)]
        for i, (t, ty, c, v) in enumerate(entries_mn):
            struct.pack_into(">HHII", mn, 2 + i * 12, t, ty, c, v)
        mn[2 + 2 * 12:2 + 2 * 12 + 4] = be32(0)

        # Interop IFD at 0x1A2 -> 0x1CC
        interop = bytearray(2 + 3 * 12 + 4)
        interop[0:2] = be16(3)
        entries_int = [
            (0x0001, 2, 4, 0x52393800), (0x0002, 7, 4, 0x30313030), (0x1000, 2, 18, 0x1CC),
        ]
        for i, (t, ty, c, v) in enumerate(entries_int):
            struct.pack_into(">HHII", interop, 2 + i * 12, t, ty, c, v)
        interop[2 + 3 * 12:2 + 3 * 12 + 4] = be32(0)

        # IFD1 (thumbnail) at 0x1DE -> 0x22C
        ifd1 = bytearray(2 + 6 * 12 + 4)
        ifd1[0:2] = be16(6)
        entries_ifd1 = [
            (0x0103, 3, 1, 0x00060000), (0x011A, 5, 1, 0x22C), (0x011B, 5, 1, 0x234),
            (0x0128, 3, 1, 0x00020000), (0x0201, 4, 1, 0x23C), (0x0202, 4, 1, len(thumb_jpeg)),
        ]
        for i, (t, ty, c, v) in enumerate(entries_ifd1):
            struct.pack_into(">HHII", ifd1, 2 + i * 12, t, ty, c, v)
        ifd1[2 + 6 * 12:2 + 6 * 12 + 4] = be32(0)

        dt = time_str.encode() + b'\x00'
        tiff = (
            b"MM\x00\x2A" + be32(8) +
            bytes(ifd0) +
            b"Nintendo\x00\x00" + b"NintendoDS\x00\x00" +
            be32(72) + be32(1) + be32(72) + be32(1) +
            b"EINH\x00\x00" + dt +
            bytes(sub) + dt + dt +
            bytes(mn) +
            b'\x00' * 0x1C + b'\x00' * 8 +
            bytes(interop) + b"JPEG Exif Ver 2.2\x00" +
            bytes(ifd1) +
            be32(72) + be32(1) + be32(72) + be32(1) +
            thumb_jpeg
        )
        assert len(tiff) == 0x23C + len(thumb_jpeg)
        payload = b"Exif\x00\x00" + tiff
        return be16(len(payload) + 2) + payload

    def encode_and_sign_frame_safe(self, pil_img: Image.Image, time_str: str, target_path: str) -> bool:
        """Encodes, signs, and programmatically verifies a frame.
        If verification fails or the file size is too large for the 3DS memory limits,
        it dynamically adjusts encoding parameters to secure a valid, lightweight file."""
        quality = 95
        MAX_FILE_SIZE = 140000  # 140 KB limit to prevent 3DS decoder out-of-memory errors

        for attempt in range(6):
            img_copy = pil_img.copy()
            img_copy.info.clear()

            # Both DSi and 3DS read the same signed DSi photo format from DCIM,
            # so there is a single encoding path (custom APP1 Exif with MakerNote
            # signature slot + thumbnail), always signed with the DSi key.
            try:
                thumb_buf = io.BytesIO()
                img_copy.resize((160, 120), Image.LANCZOS).convert("RGB").save(
                    thumb_buf, format="JPEG", quality=75, subsampling=2)
                main_buf = io.BytesIO()
                img_copy.convert("RGB").save(
                    main_buf, format="JPEG", quality=quality, subsampling=0)
                mdata = main_buf.getvalue()
                # Keep the whole body after SOI: slicing from the SOF marker would
                # drop the DQT quantization tables (libjpeg decodes anyway using
                # defaults, but the DSi hardware decoder needs them and renders
                # the full-screen view as gray without them).
                body = mdata[2:]
                app1 = b"\xFF\xE1" + self.build_dsi_exif(time_str, thumb_buf.getvalue())
                img_data = b"\xFF\xD8" + app1 + body
            except Exception:
                quality -= 5
                continue

            signed_data = self.sign_jpeg_dsi(img_data)

            size_ok = len(signed_data) <= MAX_FILE_SIZE
            sig_ok = self.verify_dsi_signature(signed_data)
            struct_ok = self.verify_jpeg_structure(signed_data)

            if sig_ok and struct_ok and size_ok:
                with open(target_path, "wb") as f:
                    f.write(signed_data)
                return True

            if not size_ok:
                quality -= 8
            else:
                quality -= 3

        signed_data = self.sign_jpeg_dsi(img_data)
        with open(target_path, "wb") as f:
            f.write(signed_data)
        return False

    def _sign_and_partition(self, output_dir: str, sources: list[str], base_time: float, remove_sources: bool = False) -> tuple[int, int]:
        """Sign and partition exported frames into the selected structure.
        DCIM mode: frames are split into batches of `album_capacity`. Each batch
        goes to its own root folder DCIM/, DCIM_2/, ..., with folder numbering
        restarted (100NINxx) per batch. Within a batch, subfolders hold up to
        100 frames each and HNI_xxxx wraps back to 0001 per subfolder, matching
        the DSi album layout (GBATEK). Parts mode keeps the existing Part_X
        layout.
        Returns (signed_count, folder_set_count)."""
        batch_size = max(1, int(self.advanced_settings.get("album_capacity", 100)))
        dsi_suffix = "NIN02" if self.console_type == "dsi" else "NIN01"
        use_parts = (self.export_structure == "parts")

        total = len(sources)
        signed_count = 0
        folder_sets = 0
        frame_index = 0

        if use_parts:
            batches = [("", sources)]
        else:
            batches = []
            for i in range(0, total, batch_size):
                label = "DCIM" if i == 0 else f"DCIM_{i // batch_size + 1}"
                batches.append((label, sources[i:i + batch_size]))

        for batch_label, batch in batches:
            for part_idx, chunk in enumerate([batch[i:i + 100] for i in range(0, len(batch), 100)]):
                if use_parts:
                    part_dir = os.path.join(output_dir, f"Part_{part_idx + 1}")
                else:
                    part_dir = os.path.join(output_dir, batch_label, f"{100 + part_idx}{dsi_suffix}")
                os.makedirs(part_dir, exist_ok=True)
                folder_sets += 1
                for file_idx, src in enumerate(chunk):
                    frame_time = base_time + frame_index * 2
                    time_str = time.strftime("%Y:%m:%d %H:%M:%S", time.localtime(frame_time))
                    out_filename = f"HNI_{file_idx + 1:04d}.JPG"
                    new_filepath = os.path.join(part_dir, out_filename)
                    try:
                        with Image.open(src) as img:
                            img_processed = self.apply_scaling_to_image(img, 640, 480)
                            img_processed.info.clear()
                            self.encode_and_sign_frame_safe(img_processed, time_str, new_filepath)
                        if remove_sources and os.path.exists(src):
                            os.unlink(src)
                        os.utime(new_filepath, (frame_time, frame_time))
                        signed_count += 1
                    except Exception as e:
                        print(f"Error processing {os.path.basename(src)} inside {os.path.basename(part_dir)}: {e}")
                    self.root.after(0, lambda p=(signed_count / max(1, total)): self.progress_bar.set(p))
                    frame_index += 1
        return signed_count, folder_sets

    def run_batch_image_export_pipeline(self, output_dir: str) -> None:
        """Asynchronously processes, timestamps, and signs multiple still images."""
        try:
            self._exporting = True
            base_time = time.time()
            signed_count, folder_sets = self._sign_and_partition(
                output_dir, self.image_paths, base_time, remove_sources=False)

            self.play_sound('apply.mp3')
            cache_note = self._cleanup_dsi_album_cache(output_dir) if self.console_type == "dsi" else ""
            self.root.after(0, lambda note=cache_note: messagebox.showinfo(
                "Export Complete", 
                f"Successfully formatted, signed, and grouped {signed_count} still images into selected directory layout!"
                + note
            ))
        except Exception as e:
            self.play_sound('warning.mp3')
            self.root.after(0, lambda err=e: messagebox.showerror("Export Failure", f"An error occurred during batch still export:\n{str(err)}"))
        finally:
            self._exporting = False
            self.root.after(0, lambda: self.toggle_widgets_interactive_state(enabled=True))
            self.root.after(0, lambda: self.progress_bar.set(1.0))

    def export_frames(self) -> None:
        if not self.video_path and not self.image_paths:
            return
        if self.playing:
            self.toggle_play()

        # Singular Image Export Mode
        if self.export_mode_var.get() == "Singular Image":
            if not self.image_paths:
                return

            # Batch export mode if multiple images are loaded
            if len(self.image_paths) > 1:
                target_dir = filedialog.askdirectory(title="Select Folder to Save Signed JPEGs")
                if not target_dir:
                    self.play_sound('back.mp3')
                    return
                
                self.progress_bar.set(0)
                self.toggle_widgets_interactive_state(enabled=False)
                threading.Thread(
                    target=self.run_batch_image_export_pipeline, 
                    args=(target_dir,), 
                    daemon=True
                ).start()
                return

            # Single image saving routine
            target_file = filedialog.asksaveasfilename(
                title="Save Signed DSi JPEG",
                initialfile="HNI_0001.JPG",
                defaultextension=".JPG",
                filetypes=[("DSi Signed JPEG", "*.JPG")]
            )
            if not target_file:
                self.play_sound('back.mp3')
                return

            try:
                self._exporting = True
                # Load image using Pillow, resizing to native 640x480 resolution
                pil_img = Image.open(self.video_path).convert("RGBA")
                pil_img = self.apply_scaling_to_image(pil_img, 640, 480)

                # Strip any embedded color profiles or metadata to keep the header size identical
                pil_img.info.clear()

                # Inject Chronological EXIF Headers
                time_str = time.strftime("%Y:%m:%d %H:%M:%S", time.localtime())

                self.encode_and_sign_frame_safe(pil_img, time_str, target_file)

                os.utime(target_file, (time.time(), time.time()))
                self.play_sound('apply.mp3')
                console_name = "Nintendo DSi" if self.console_type == "dsi" else "Nintendo 3DS"
                messagebox.showinfo("Export Complete", f"Successfully exported, timestamped, and signed still image for {console_name}!")
            except Exception as e:
                self.play_sound('warning.mp3')
                messagebox.showerror("Export Failure", f"Failed to export still image:\n{str(e)}")
            finally:
                self._exporting = False
            return

        # Standard Video Frames Batch Export
        target_fps = SPEED_FPS[self.speed]

        target_dir = filedialog.askdirectory(title="Choose Output Export Directory")
        if not target_dir:
            self.play_sound('back.mp3')
            return

        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            self.play_sound('warning.mp3')
            messagebox.showerror(
                "Export Error", "FFmpeg executable was not found on your system PATH.\n\nPlease install FFmpeg to run export pipelines."
            )
            return

        self.progress_bar.set(0)
        self.toggle_widgets_interactive_state(enabled=False)

        export_thread = threading.Thread(
            target=self.run_ffmpeg_export_pipeline,
            args=(target_dir, ffmpeg_bin),
            daemon=True
        )
        export_thread.start()

    def run_ffmpeg_export_pipeline(self, output_dir: str, ffmpeg_path: str) -> None:
        target_fps = SPEED_FPS[self.speed]
        frame_step = max(1, round(self.video_fps / target_fps))
        frame_limit = (self.total_video_frames - 1) // frame_step + 1
        bg_type = self.bg_type_var.get()

        frame_sel = f"select='eq(mod(n,{frame_step}),0)'"
        if self.scale_mode == "Fit":
            if bg_type == "custom" and self.bg_image_path and os.path.exists(self.bg_image_path):
                # Custom PNG/JPG file overlay complex layout map
                filter_complex = (
                    f"[0:v]{frame_sel},scale=640:480:force_original_aspect_ratio=decrease[fg];"
                    f"[1:v]scale=640:480,fps={target_fps}[bg];"
                    f"[bg][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2"
                )
                cmd = [
                    ffmpeg_path, "-y", 
                    "-i", self.video_path,
                    "-i", self.bg_image_path,
                    "-fps_mode", "vfr",
                    "-filter_complex", filter_complex,
                    "-frames:v", str(frame_limit),
                    "-q:v", "2", os.path.join(output_dir, "HNI_%04d.JPG")
                ]
            else:
                color_str = "white" if bg_type == "white" else "black"
                vf_filter = f"{frame_sel},scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2:color={color_str}"
                cmd = [
                    ffmpeg_path, "-y", "-i", self.video_path,
                    "-fps_mode", "vfr",
                    "-vf", vf_filter, 
                    "-frames:v", str(frame_limit),
                    "-q:v", "2", os.path.join(output_dir, "HNI_%04d.JPG")
                ]
        else:
            # Stretch, Crop or Tiles (Tiles utilizes centered aspects with post-process drawing overrides in Python!)
            if self.scale_mode == "Stretch":
                vf_filter = f"{frame_sel},scale=640:480"
            else: 
                # Tiles and Tiles Stretched unpadded boundaries which our Python post-processor tiles beautifully!
                if self.scale_mode in ("Tiles", "Tiles Stretched"):
                    vf_filter = f"{frame_sel},scale=640:480:force_original_aspect_ratio=decrease"
                else: # Crop Mode
                    vf_filter = f"{frame_sel},scale=640:480:force_original_aspect_ratio=increase,crop=640:480"
                
            cmd = [
                ffmpeg_path, "-y", "-i", self.video_path,
                "-fps_mode", "vfr",
                "-vf", vf_filter, 
                "-frames:v", str(frame_limit),
                "-q:v", "2", os.path.join(output_dir, "HNI_%04d.JPG")
            ]

        try:
            self._exporting = True
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            expected_frames = frame_limit
            while True:
                line = process.stderr.readline()
                if not line:
                    break
                if "frame=" in line:
                    try:
                        parts = line.split("frame=")[1].strip().split()
                        curr_exported_frame = int(parts[0])
                        progress_val = min(1.0, curr_exported_frame / max(1, expected_frames))
                        self.root.after(0, lambda p=progress_val: self.progress_bar.set(p))
                    except Exception:
                        pass
            process.wait()
            
            # Post-Processing: Sort files alphabetically, inject current timestamp, partition into folder sets of 100, and sign
            if process.returncode == 0:
                self.root.after(0, lambda: self.file_name_label.configure(text="Timestamping, Signing & Splitting...", text_color=MAIN_COLOR))

                temp_filenames = sorted([
                    f for f in os.listdir(output_dir)
                    if f.lower().endswith((".jpg", ".jpeg"))
                ])

                base_time = time.time()
                sources = [os.path.join(output_dir, f) for f in temp_filenames]
                signed_count, folder_sets = self._sign_and_partition(
                    output_dir, sources, base_time, remove_sources=True)

                self.play_sound('apply.mp3')
                self.root.after(0, lambda: self.file_name_label.configure(text=os.path.basename(self.video_path), text_color=SUB_COLOR))
                cache_note = self._cleanup_dsi_album_cache(output_dir) if self.console_type == "dsi" else ""
                self.root.after(0, lambda c=signed_count, p=folder_sets, note=cache_note: messagebox.showinfo(
                    "Export Complete", f"Successfully exported, timestamped, and signed {c} frames grouped into {p} folder sets!"
                    + note
                ))
            else:
                self.play_sound('warning.mp3')
                self.root.after(0, lambda: messagebox.showerror("Export Failed", "The FFmpeg subprocess returned an error execution code."))
        except Exception as e:
            self.play_sound('warning.mp3')
            self.root.after(0, lambda err=e: messagebox.showerror("Pipeline Failure", f"An execution error occurred:\n{str(err)}"))
        finally:
            self._exporting = False
            self.root.after(0, lambda: self.toggle_widgets_interactive_state(enabled=True))
            self.root.after(0, lambda: self.progress_bar.set(1.0))

    def toggle_widgets_interactive_state(self, enabled: bool) -> None:
        """Disables controls during background conversions preventing parameter disruptions."""
        state = "normal" if enabled else "disabled"
        self.load_btn.configure(state=state)
        if self.export_mode_var.get() == "Singular Image":
            self.play_btn.configure(state="disabled")
        else:
            self.play_btn.configure(state=state)
        self.export_btn.configure(state=state)
        self.aspect_menu.configure(state=state)
        self.export_mode_menu.configure(state=state)
        self.beg_btn.configure(state=state)
        self.prev_btn.configure(state=state)
        self.next_btn.configure(state=state)
        self.end_btn.configure(state=state)
        if hasattr(self, 'tile_link_btn'):
            self.tile_link_btn.configure(state=state)
        if hasattr(self, 'tile_cols_entry'):
            self.tile_cols_entry.configure(state=state)
        if hasattr(self, 'tile_rows_entry'):
            self.tile_rows_entry.configure(state=state)
        if hasattr(self, 'speed_widget'):
            self.speed_widget.configure(state=state)

    def on_close(self) -> None:
        """Humble cleanup routines releasing file handles, saving user settings and clearing python caches."""
        self._cleanup_audio()

        if self.cap:
            self.cap.release()
        if self.after_play_id:
            self.root.after_cancel(self.after_play_id)
            
        self.save_user_settings()      # Saves current parameters automatically
        self.purge_pycache_directories() # Deletes __pycache__ compiles silently
        self.root.destroy()

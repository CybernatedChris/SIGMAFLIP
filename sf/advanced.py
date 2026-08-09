# sf/advanced.py
import customtkinter as ctk
import tkinter as tk

def show_advanced_dialog(parent, fonts, main_color, sub_color, highlight_color, settings, on_change_callback, set_icon_fn):
    """Generates a styled, centered, modeless Advanced Settings window with transparent frames [sf/advanced.py]."""
    adv = ctk.CTkToplevel(parent)
    adv.title("Advanced Rendering Settings")
    adv.geometry("400x420")
    adv.resizable(False, False)
    adv.transient(parent)
    
    # Configure native window background to match the active grid canvas
    theme_bg = ("#f3f4f6", "#151515")
    adv.configure(fg_color=theme_bg)
    
    set_icon_fn(adv, delay=True) # Bind icon files natively

    # Grid Background Canvas
    bg_canvas = tk.Canvas(adv, bg="#1a1a1a", highlightthickness=0, bd=0)
    bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)

    def draw_grid(event=None):
        ww = adv.winfo_width()
        wh = adv.winfo_height()
        bg_canvas.delete("all")
        curr_mode = ctk.get_appearance_mode().lower()
        bg_color = "#151515" if curr_mode == "dark" else "#f3f4f6"
        line_color = "#222522" if curr_mode == "dark" else "#e5e7eb"
        bg_canvas.configure(bg=bg_color)
        grid_spacing = 10
        for x in range(0, ww, grid_spacing):
            bg_canvas.create_line(x, 0, x, wh, fill=line_color, width=1)
        for y in range(0, wh, grid_spacing):
            bg_canvas.create_line(0, y, ww, y, fill=line_color, width=1)
        bg_canvas.tk.call('lower', bg_canvas._w)

    adv.bind("<Configure>", draw_grid)

    # Centering Calculations
    adv.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - adv.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - adv.winfo_height()) // 2
    adv.geometry(f"+{x}+{y}")

    # Main Container (transparent so grid bg shows through)
    frame = ctk.CTkFrame(adv, fg_color="transparent")
    frame.pack(fill=tk.BOTH, expand=True, padx=25, pady=20)

    # Title
    ctk.CTkLabel(
        frame, 
        text="Advanced Filters", 
        font=fonts['large'], 
        text_color=main_color,
        fg_color="transparent"
    ).pack(pady=(0, 15))

    # 1. Pixel Precision Switch
    pixel_precision_var = tk.BooleanVar(value=settings.get("pixel_precision", False))
    def toggle_pixel_precision():
        settings["pixel_precision"] = pixel_precision_var.get()
        on_change_callback()

    pixel_switch = ctk.CTkSwitch(
        frame,
        text="Enable Pixel Precision",
        font=fonts['small'],
        text_color=main_color,
        variable=pixel_precision_var,
        command=toggle_pixel_precision,
        progress_color=highlight_color
    )
    pixel_switch.pack(anchor="w", pady=8)

    # 2. Black and White Switch
    bw_var = tk.BooleanVar(value=settings.get("black_and_white", False))
    def toggle_bw():
        val = bw_var.get()
        settings["black_and_white"] = val
        if val:
            dither_menu.configure(state="normal")
        else:
            dither_menu.configure(state="disabled")
        on_change_callback()

    bw_switch = ctk.CTkSwitch(
        frame,
        text="Pure Black & White (Binary Mode)",
        font=fonts['small'],
        text_color=main_color,
        variable=bw_var,
        command=toggle_bw,
        progress_color=highlight_color
    )
    bw_switch.pack(anchor="w", pady=8)

    # 3. Dithering Mode Dropdown Container
    dither_frame = ctk.CTkFrame(frame, fg_color="transparent")
    dither_frame.pack(fill="x", pady=8)

    ctk.CTkLabel(
        dither_frame,
        text="Dither Mode:",
        font=fonts['small'],
        text_color=sub_color,
        fg_color="transparent"
    ).pack(side=tk.LEFT, padx=(0, 10))

    def change_dither(val):
        settings["dither_mode"] = val
        on_change_callback()

    dither_modes = [
        "None", 
        "Floyd-Steinberg", 
        "Bayer 2x2", 
        "Bayer 3x3", 
        "Bayer 4x4", 
        "Bayer 8x8", 
        "Flipnote Memory Saver",
        "Atkinson", 
        "Burkes", 
        "Jarvis-Judice-Ninke", 
        "Stucki", 
        "Sierra 3-Row", 
        "Sierra 2-Row", 
        "Sierra Lite", 
        "Stevenson-Arce", 
        "Halftone"
    ]

    dither_menu = ctk.CTkOptionMenu(
        dither_frame,
        values=dither_modes,
        command=change_dither,
        fg_color=main_color,
        button_color=main_color,
        button_hover_color=highlight_color,
        dropdown_fg_color=("#ffffff", "#2b2b2b"),
        dropdown_text_color=("#111827", "#E2E8F0"),
        text_color=("#ffffff", "#111827"),
        font=fonts['tiny'],
        dropdown_font=fonts['tiny']
    )
    dither_menu.set(settings.get("dither_mode", "None"))
    dither_menu.pack(side=tk.LEFT, fill="x", expand=True)
    
    if not bw_var.get():
        dither_menu.configure(state="disabled")

    # 4. Contrast Slider Container
    contrast_frame = ctk.CTkFrame(frame, fg_color="transparent")
    contrast_frame.pack(fill="x", pady=15)

    contrast_label_var = tk.StringVar(value=f"Contrast: {settings.get('contrast', 1.0):.2f}x")
    ctk.CTkLabel(
        contrast_frame,
        textvariable=contrast_label_var,
        font=fonts['small'],
        text_color=sub_color,
        fg_color="transparent"
    ).pack(anchor="w")

    def on_contrast_change(val):
        contrast_val = float(val)
        settings["contrast"] = contrast_val
        contrast_label_var.set(f"Contrast: {contrast_val:.2f}x")
        on_change_callback()

    contrast_slider = ctk.CTkSlider(
        contrast_frame,
        from_=0.1,
        to=3.0,
        number_of_steps=29,
        button_color=main_color,
        button_hover_color=highlight_color,
        progress_color=main_color,
        command=on_contrast_change
    )
    contrast_slider.set(settings.get("contrast", 1.0))
    contrast_slider.pack(fill="x", pady=(5, 0))

    # ponytail: close button removed per user request — grid shows through instead
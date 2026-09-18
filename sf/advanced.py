import os
import customtkinter as ctk
import tkinter as tk
from sf.config import attach_grid_background

_INTERACTIVE = (ctk.CTkButton, ctk.CTkEntry, ctk.CTkSwitch, ctk.CTkSlider,
                ctk.CTkOptionMenu, ctk.CTkSegmentedButton, ctk.CTkCheckBox)
_CONTAINER = (ctk.CTkFrame, tk.Frame)


def set_interactive_state(widget, enabled, skip=()):
    for child in widget.winfo_children():
        if any(child is s for s in skip):
            continue
        if isinstance(child, _INTERACTIVE):
            try:
                child.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass
        elif isinstance(child, _CONTAINER):
            set_interactive_state(child, enabled, skip)


def show_advanced_dialog(parent, fonts, main_color, sub_color, highlight_color, settings, on_change_callback, set_icon_fn, get_export_structure=None, get_console_type=None, theme_bg=None, get_exporting=None, get_export_mode=None):
    """Modeless Advanced Settings window."""
    adv = ctk.CTkToplevel(parent)
    adv.title("Advanced Settings")
    adv.geometry("400x650")
    adv.resizable(False, False)
    adv.transient(parent)

    theme_bg = theme_bg or ("#f3f4f6", "#151515")
    adv.configure(fg_color=theme_bg)

    set_icon_fn(adv, delay=True)

    attach_grid_background(adv)

    adv.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - adv.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - adv.winfo_height()) // 2
    adv.geometry(f"+{x}+{y}")

    frame = ctk.CTkScrollableFrame(adv, fg_color="transparent")
    frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

    ctk.CTkLabel(
        frame,
        text="Rendering Options",
        font=fonts['medium_bold'],
        text_color=main_color,
        fg_color="transparent"
    ).pack(anchor="center", pady=(0, 10))

    jpg_quality_frame = ctk.CTkFrame(frame, fg_color="transparent")
    jpg_quality_frame.pack(fill="x", pady=5)

    ctk.CTkLabel(
        jpg_quality_frame,
        text="JPG Quality (1-100):",
        font=fonts['small'],
        text_color=main_color
    ).pack(side="left")

    jpg_quality_var = tk.StringVar(value=str(settings.get("jpg_quality", 95)))
    def on_jpg_quality_change(event=None):
        val = jpg_quality_var.get().strip()
        if val.isdigit() and 1 <= int(val) <= 100:
            settings["jpg_quality"] = int(val)
            on_change_callback()
        if event and event.keysym == "Return":
            frame.focus_set()

    jpg_quality_entry = ctk.CTkEntry(
        jpg_quality_frame,
        textvariable=jpg_quality_var,
        width=50,
        font=fonts['small'],
        fg_color=sub_color,
        text_color=main_color,
        border_color=highlight_color
    )
    jpg_quality_entry.pack(side="right")
    jpg_quality_entry.bind("<Return>", on_jpg_quality_change)
    jpg_quality_entry.bind("<FocusOut>", on_jpg_quality_change)

    bw_var = tk.BooleanVar(value=settings.get("black_and_white", False))
    def toggle_bw():
        val = bw_var.get()
        settings["black_and_white"] = val
        if val:
            dither_menu.configure(state="normal")
            perf_switch.configure(state="normal")
            invert_switch.configure(state="normal")
        else:
            dither_menu.configure(state="disabled")
            perf_switch.configure(state="disabled")
            invert_switch.configure(state="disabled")
            if perf_var.get():
                perf_var.set(False)
                settings["performance_mode"] = False
            if invert_var.get():
                invert_var.set(False)
                settings["invert_bw"] = False
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

    invert_var = tk.BooleanVar(value=settings.get("invert_bw", False))
    def toggle_invert():
        settings["invert_bw"] = invert_var.get()
        on_change_callback()

    invert_switch = ctk.CTkSwitch(
        frame,
        text="Invert Display Colors",
        font=fonts['small'],
        text_color=main_color,
        variable=invert_var,
        command=toggle_invert,
        progress_color=highlight_color
    )
    if not bw_var.get():
        invert_switch.configure(state="disabled")
    invert_switch.pack(anchor="w", pady=8)

    perf_var = tk.BooleanVar(value=settings.get("performance_mode", False))
    def toggle_perf():
        settings["performance_mode"] = perf_var.get()
        on_change_callback()

    perf_switch = ctk.CTkSwitch(
        frame,
        text="Performance Mode",
        font=fonts['small'],
        text_color=main_color,
        variable=perf_var,
        command=toggle_perf,
        progress_color=highlight_color
    )
    if not bw_var.get():
        perf_switch.configure(state="disabled")
    perf_switch.pack(anchor="w", pady=8)

    dither_frame = ctk.CTkFrame(frame, fg_color="transparent")
    dither_frame.pack(fill="x", pady=8)

    ctk.CTkLabel(
        dither_frame,
        text="Dither Mode:",
        font=fonts['small'],
        text_color=sub_color,
        fg_color="transparent"
    ).pack(side=tk.LEFT, padx=(0, 10))

    dither_modes = [
        "None",
        "Floyd-Steinberg",
        "Bayer 2x2",
        "Bayer 3x3",
        "Bayer 4x4",
        "Bayer 8x8",
        "Blue Noise 64x64",
        "Flipnote Memory Saver (Experimental)",
        "Atkinson",
        "Jarvis-Judice-Ninke",
        "Sierra 3-Row",
        "Sierra Lite",
        "Stevenson-Arce",
        "Dot Diffusion",
        "Riemersma",
        "Halftone",
        "Woodcut"
    ]

    dither_current = tk.StringVar(value=settings.get("dither_mode", "None"))

    def show_dither_menu(event):
        curr_mode = ctk.get_appearance_mode().lower()
        is_dark = curr_mode == "dark"
        menu_opts = dict(
            bg="#1a1a1a" if is_dark else "#ffffff",
            fg="#E2E8F0" if is_dark else "#111827",
            activebackground="#475569",
            activeforeground="#111827",
            borderwidth=0,
            font=(fonts['small'].cget("family"), 10)
        )
        m = tk.Menu(adv, tearoff=0, **menu_opts)
        for mode in dither_modes:
            prefix = "\u2713 " if mode == dither_current.get() else "   "
            m.add_command(label=prefix + mode, command=lambda v=mode: pick_dither(v))
        m.tk_popup(event.x_root, event.y_root)

    def pick_dither(val):
        dither_current.set(val)
        settings["dither_mode"] = val
        on_change_callback()

    dither_menu = ctk.CTkButton(
        dither_frame,
        textvariable=dither_current,
        anchor="w",
        fg_color=theme_bg or ("#ffffff", "#2b2b2b"),
        hover_color=highlight_color,
        text_color=("#1e293b", "#E2E8F0"),
        font=fonts['tiny'],
        height=28
    )
    dither_menu.bind("<Button-1>", show_dither_menu)
    dither_menu.pack(side=tk.LEFT, fill="x", expand=True)

    if not bw_var.get():
        dither_menu.configure(state="disabled")

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

    wm_section = ctk.CTkFrame(frame, fg_color="transparent")
    wm_section.pack(fill="x")

    ctk.CTkLabel(
        wm_section,
        text="Watermark",
        font=fonts['medium_bold'],
        text_color=main_color,
        fg_color="transparent"
    ).pack(anchor="center", pady=(15, 6))

    wm_enable_var = tk.BooleanVar(value=settings.get("watermark_enabled", False))
    def toggle_wm():
        settings["watermark_enabled"] = wm_enable_var.get()
        on_change_callback()

    ctk.CTkSwitch(
        wm_section,
        text="Enable Watermark Overlay",
        font=fonts['small'],
        text_color=main_color,
        variable=wm_enable_var,
        command=toggle_wm,
        progress_color=highlight_color
    ).pack(anchor="w", pady=6)

    wm_path_var = tk.StringVar(value=os.path.basename(settings.get("watermark_path", "")) or "Default watermark")
    ctk.CTkLabel(
        wm_section,
        textvariable=wm_path_var,
        font=fonts['tiny'],
        text_color=sub_color,
        fg_color="transparent",
        wraplength=330
    ).pack(anchor="w")

    wm_btn_row = ctk.CTkFrame(wm_section, fg_color="transparent")
    wm_btn_row.pack(fill="x", pady=(4, 6))

    def browse_watermark():
        from tkinter import filedialog
        chosen = filedialog.askopenfilename(
            title="Select Watermark Image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.webp")]
        )
        if chosen:
            settings["watermark_path"] = chosen
            settings["watermark_enabled"] = True
            wm_enable_var.set(True)
            wm_path_var.set(os.path.basename(chosen))
            on_change_callback()

    def reset_watermark_file():
        settings["watermark_path"] = ""
        wm_path_var.set("Default watermark")
        on_change_callback()

    ctk.CTkButton(
        wm_btn_row,
        text="Browse...",
        font=fonts['tiny'],
        fg_color=("#2563eb", "#3b82f6"),
        hover_color=highlight_color,
        text_color="#ffffff",
        command=browse_watermark
    ).pack(side="left", expand=True, fill="x", padx=(0, 4))
    ctk.CTkButton(
        wm_btn_row,
        text="Reset",
        font=fonts['tiny'],
        fg_color=("#6b7280", "#4b5563"),
        hover_color=highlight_color,
        text_color="#ffffff",
        command=reset_watermark_file
    ).pack(side="left", expand=True, fill="x", padx=(4, 0))

    pos_map = {"Top Left": "topleft", "Top Right": "topright",
               "Bottom Left": "bottomleft", "Bottom Right": "bottomright"}
    rev_pos_map = {v: k for k, v in pos_map.items()}

    def on_pos_select(val):
        settings["watermark_position"] = pos_map.get(val, "bottomright")
        on_change_callback()

    pos_seg = ctk.CTkSegmentedButton(
        wm_section,
        values=list(pos_map.keys()),
        command=on_pos_select,
        font=fonts['tiny'],
        selected_color=highlight_color,
        selected_hover_color=highlight_color
    )
    pos_seg.set(rev_pos_map.get(settings.get("watermark_position", "bottomright"), "Bottom Right"))
    pos_seg.pack(fill="x", pady=6)

    scale_label_var = tk.StringVar(value=f"Scale: {settings.get('watermark_scale', 40)}% width")
    ctk.CTkLabel(
        wm_section,
        textvariable=scale_label_var,
        font=fonts['tiny'],
        text_color=sub_color,
        fg_color="transparent"
    ).pack(anchor="w")

    def on_scale_change(val):
        v = int(float(val))
        settings["watermark_scale"] = v
        scale_label_var.set(f"Scale: {v}% width")
        on_change_callback()

    scale_slider = ctk.CTkSlider(
        wm_section,
        from_=5,
        to=60,
        number_of_steps=55,
        button_color=main_color,
        button_hover_color=highlight_color,
        progress_color=main_color,
        command=on_scale_change
    )
    scale_slider.set(settings.get("watermark_scale", 40))
    scale_slider.pack(fill="x", pady=(2, 6))

    ctk.CTkLabel(
        frame,
        text="Export Options",
        font=fonts['medium_bold'],
        text_color=main_color,
        fg_color="transparent"
    ).pack(anchor="center", pady=(0, 10))

    capacity_frame = ctk.CTkFrame(frame, fg_color="transparent")
    capacity_frame.pack(fill="x", pady=15)

    capacity_label = ctk.CTkLabel(
        capacity_frame,
        text="Photos per DCIM folder:",
        font=fonts['small'],
        text_color=sub_color,
        fg_color="transparent"
    )
    capacity_label.pack(anchor="center")

    capacity_var = tk.StringVar(value=str(max(1, settings.get("album_capacity", 100))))
    def on_capacity_change(*_):
        try:
            val = max(1, int(capacity_var.get()))
        except ValueError:
            val = 100
        capacity_var.set(str(val))
        settings["album_capacity"] = val
        on_change_callback()

    capacity_entry = ctk.CTkEntry(
        capacity_frame,
        textvariable=capacity_var,
        width=120,
        justify="center",
        font=fonts['small'],
        fg_color=("#ffffff", "#2b2b2b"),
        text_color=main_color,
        border_color=main_color
    )
    capacity_entry.pack(anchor="center", pady=(5, 0))
    capacity_entry.bind("<FocusOut>", on_capacity_change)
    capacity_entry.bind("<Return>", on_capacity_change)

    if get_export_structure is not None:
        def poll_structure():
            if adv.winfo_exists():
                exporting = bool(get_exporting and get_exporting())
                if exporting or get_export_structure() == "parts":
                    capacity_entry.configure(state="disabled")
                    capacity_label.configure(text_color=("#9ca3af", "#6b7280"))
                else:
                    capacity_entry.configure(state="normal")
                    capacity_label.configure(text_color=sub_color)
                adv.after(500, poll_structure)
        adv.after(500, poll_structure)

    pit_title_label = ctk.CTkLabel(
        frame,
        text="Pit File",
        font=fonts['medium_bold'],
        text_color=main_color,
        fg_color="transparent"
    )
    pit_title_label.pack(anchor="center", pady=(0, 10))

    pit_dir_var = tk.StringVar(value=settings.get("pit_dir", ""))
    pit_status_var = tk.StringVar(value="")

    pit_dir_label = ctk.CTkLabel(
        frame,
        textvariable=pit_dir_var,
        font=fonts['tiny'],
        text_color=sub_color,
        fg_color="transparent",
        wraplength=330
    )
    pit_dir_label.pack(anchor="center", pady=(0, 5))

    def browse_pit_dir():
        from tkinter import filedialog
        chosen = filedialog.askdirectory(title="Select SD Card Root (or folder containing pit.bin)")
        if chosen:
            pit_dir_var.set(chosen)
            settings["pit_dir"] = chosen
            pit_status_var.set("")

    browse_btn = ctk.CTkButton(
        frame,
        text="Browse Root...",
        font=fonts['small'],
        fg_color=("#2563eb", "#3b82f6"),
        hover_color=highlight_color,
        text_color="#ffffff",
        command=browse_pit_dir
    )
    browse_btn.pack(anchor="center", pady=(0, 5))

    def delete_pit():
        from tkinter import messagebox
        base = pit_dir_var.get().strip()
        if not base:
            pit_status_var.set("Choose a directory first.")
            return
        pit = os.path.join(base, "private", "ds", "app", "484E494A", "pit.bin")
        if not os.path.isfile(pit):
            pit_status_var.set(f"No pit.bin found at:\n{pit}")
            return
        if not messagebox.askyesno("Delete pit.bin", f"Delete stale album cache?\n\n{pit}"):
            return
        try:
            os.unlink(pit)
            pit_status_var.set("Deleted stale album cache.")
        except Exception as e:
            pit_status_var.set(f"Could not delete: {e}")

    delete_btn = ctk.CTkButton(
        frame,
        text="Delete Pit File",
        font=fonts['small'],
        fg_color=("#dc2626", "#b91c1c"),
        hover_color=highlight_color,
        text_color="#ffffff",
        command=delete_pit
    )
    delete_btn.pack(anchor="center", pady=(0, 5))

    ctk.CTkLabel(
        frame,
        textvariable=pit_status_var,
        font=fonts['tiny'],
        text_color=sub_color,
        fg_color="transparent",
        wraplength=330
    ).pack(anchor="center")

    if get_console_type is not None:
        def poll_console():
            if adv.winfo_exists():
                exporting = bool(get_exporting and get_exporting())
                is_3ds = get_console_type() == "3ds"
                pit_title_label.configure(text_color=("#dc2626", "#ef4444") if is_3ds else main_color)
                pit_state = "disabled" if (exporting or is_3ds) else "normal"
                browse_btn.configure(state=pit_state)
                delete_btn.configure(state=pit_state)
                adv.after(500, poll_console)
        adv.after(500, poll_console)

    if get_exporting is not None:
        gated = (dither_menu, perf_switch, invert_switch)
        def apply_state(exporting, still_mode):
            gated_state = "disabled" if exporting else ("normal" if bw_var.get() else "disabled")
            for widget in gated:
                widget.configure(state=gated_state)
            set_interactive_state(
                frame, not exporting,
                skip=gated + (capacity_entry, browse_btn, delete_btn, wm_section))
            set_interactive_state(wm_section, not (exporting or still_mode))
        last = {"exporting": None, "still": None}
        def poll_state():
            if not adv.winfo_exists():
                return
            exporting = bool(get_exporting())
            still_mode = bool(get_export_mode and get_export_mode() == "Still Images")
            if last["exporting"] != exporting or last["still"] != still_mode:
                last["exporting"] = exporting
                last["still"] = still_mode
                apply_state(exporting, still_mode)
            adv.after(300, poll_state)
        poll_state()

    return adv

import os
import customtkinter as ctk
import tkinter as tk
from PIL import Image
from sf.config import attach_grid_background, GridLabel, VERSION

def show_about_dialog(parent, fonts, icon_path, main_color, sub_color, set_icon_fn):
    about = ctk.CTkToplevel(parent)
    about.title("About SIGMAFLIP")
    about.geometry("440x580")
    about.resizable(False, False)
    about.transient(parent)
    about.grab_set()

    theme_bg = ("#f3f4f6", "#151515")
    about.configure(fg_color=theme_bg)

    set_icon_fn(about, delay=True)

    attach_grid_background(about)

    frame = ctk.CTkFrame(about, fg_color="transparent")
    frame.pack(fill=tk.BOTH, expand=True, padx=25, pady=20)

    png_path = os.path.join(os.path.dirname(icon_path), "sigmoid.png")
    if os.path.exists(png_path):
        try:
            pil_img = Image.open(png_path)
            logo_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(80, 80))
            logo_label = GridLabel(frame, image=logo_img, text="", fg_color="transparent")
            logo_label.pack(pady=(0, 5))
        except Exception as e:
            print(f"About image rendering error: {e}")

    GridLabel(
        frame,
        text="SIGMAFLIP",
        font=fonts['title'],
        text_color=main_color,
        fg_color="transparent"
    ).pack(pady=(0, 5))

    body_text = (
        "A FOSS Python-based alternative to SignaPic DSi, "
        "used for converting videos and still images into signed JPEG frames "
        "according to Flipnote speed.\n\n"
        "Slopped by CybernatedChris\n"
        "Powered by dsi_jpeg_signature_tool by NrNbaYoh"
    )

    GridLabel(
        frame,
        text=body_text,
        font=fonts['small'],
        text_color=main_color,
        wraplength=380,
        justify="center",
        fg_color="transparent"
    ).pack(pady=5)

    ALERT_RED, ALERT_AMBER = "#f87171", "#fbbf24"
    dark = ctk.get_appearance_mode().lower() == "dark"
    disclaimer_ink = "#f87171" if dark else "#b91c1c"
    warning_ink = "#fbbf24" if dark else "#a16207"

    disclaimer_frame = ctk.CTkFrame(frame, fg_color=("#fef2f2", "#1c1415"),
                                    border_width=1, border_color=ALERT_RED, corner_radius=4)
    disclaimer_frame._sf_flat = True
    disclaimer_frame.pack(fill="x", pady=10)

    disclaimer_text = (
        "DISCLAIMER: CybernatedChris assume no responsibility for how SIGMAFLIP is handled. "
        "You are solely responsible for the content you export. Improper use of files "
        "on Nintendo DSi fan services can result in severe consequences, "
        "including permanent system bans or administrative restrictions. "
        "With great power comes great responsibility. Please proceed entirely at your own risk."
    )

    GridLabel(
        disclaimer_frame,
        text=disclaimer_text,
        font=fonts['tiny'],
        text_color=disclaimer_ink,
        wraplength=360,
        justify="center",
        fg_color="transparent"
    ).pack(padx=10, pady=8)

    secondary_frame = ctk.CTkFrame(frame, fg_color=("#fffbeb", "#1c1912"),
                                   border_width=1, border_color=ALERT_AMBER, corner_radius=4)
    secondary_frame._sf_flat = True
    secondary_frame.pack(fill="x", pady=(0, 10))

    GridLabel(
        secondary_frame,
        text="This tool signs VIDEO FRAMES and PICTURES, NOT Flipnote PPM files!",
        font=fonts['tiny'],
        text_color=warning_ink,
        wraplength=360,
        justify="center",
        fg_color="transparent"
    ).pack(padx=10, pady=8)

    GridLabel(
        frame,
        text=VERSION,
        font=fonts['tiny'],
        text_color=sub_color,
        fg_color="transparent"
    ).pack(side=tk.BOTTOM, pady=(5, 0))

# sf/about.py
import os
import customtkinter as ctk
import tkinter as tk
from PIL import Image
from sf.config import draw_grid_on_canvas

def show_about_dialog(parent, fonts, icon_path, main_color, sub_color, highlight_color, set_icon_fn):
    """Generates a styled, centered About window with transparent grid backgrounds."""
    about = ctk.CTkToplevel(parent)
    about.title("About SIGMAFLIP")
    about.geometry("440x580")
    about.resizable(False, False)
    about.transient(parent)
    about.grab_set()
    
    theme_bg = ("#f3f4f6", "#151515")
    about.configure(fg_color=theme_bg)
    
    set_icon_fn(about, delay=True)

    bg_canvas = tk.Canvas(about, bg="#1a1a1a", highlightthickness=0, bd=0)
    bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)

    about.bind("<Configure>", lambda e: draw_grid_on_canvas(bg_canvas, about.winfo_width(), about.winfo_height(), ctk.get_appearance_mode().lower()))

    frame = ctk.CTkFrame(about, fg_color="transparent")
    frame.pack(fill=tk.BOTH, expand=True, padx=25, pady=20)

    png_path = os.path.join(os.path.dirname(icon_path), "sigma.png")
    if os.path.exists(png_path):
        try:
            pil_img = Image.open(png_path)
            logo_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(80, 80))
            logo_label = ctk.CTkLabel(frame, image=logo_img, text="", fg_color="transparent")
            logo_label.pack(pady=(0, 5))
        except Exception as e:
            print(f"About image rendering error: {e}")

    ctk.CTkLabel(
        frame, 
        text="SIGMAFLIP", 
        font=fonts['title'], 
        text_color=main_color,
        fg_color="transparent"
    ).pack(pady=(0, 5))

    body_text = (
        "A FOSS Python-based alternative to SignaPic DSi, "
        "used for converting videos into JPEG-based frames according to Flipnote speed.\n\n"
        "Slopped by CybernatedChris\n"
        "Powered by dsi_jpeg_signature_tool by NrNbaYoh"
    )
    
    ctk.CTkLabel(
        frame, 
        text=body_text, 
        font=fonts['small'], 
        text_color=main_color, 
        wraplength=380, 
        justify="center",
        fg_color="transparent"
    ).pack(pady=5)

    disclaimer_frame = ctk.CTkFrame(frame, fg_color="transparent", border_color="#f87171", border_width=1)
    disclaimer_frame.pack(fill="x", pady=10)

    disclaimer_text = (
        "DISCLAIMER: CybernatedChris assume no responsibility for how SIGMAFLIP is handled. "
        "You are solely responsible for the content you export. Improper use of files "
        "on Nintendo DSi fan services can result in severe consequences, "
        "including permanent system bans or administrative restrictions. "
        "With great power comes great responsibility. Please proceed entirely at your own risk."
    )

    ctk.CTkLabel(
        disclaimer_frame,
        text=disclaimer_text,
        font=fonts['tiny'],
        text_color="#f87171",
        wraplength=360,
        justify="center",
        fg_color="transparent"
    ).pack(padx=10, pady=8)

    secondary_frame = ctk.CTkFrame(frame, fg_color="transparent", border_color="#fbbf24", border_width=1)
    secondary_frame.pack(fill="x", pady=(0, 10))

    ctk.CTkLabel(
        secondary_frame,
        text="This tool signs VIDEO FRAMES and PICTURES, NOT Flipnote PPM files!",
        font=fonts['tiny'],
        text_color="#fbbf24",
        wraplength=360,
        justify="center",
        fg_color="transparent"
    ).pack(padx=10, pady=8)

    ctk.CTkLabel(
        frame, 
        text="v1", 
        font=fonts['tiny'], 
        text_color=sub_color,
        fg_color="transparent"
    ).pack(side=tk.BOTTOM, pady=(5, 0))

# main.py
import platform
import customtkinter as ctk

# Configure DPI awareness before ctk.CTk() is initialized to guarantee pixel-perfect native monitor scaling
if platform.system() == "Windows":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

from sf.app import SIGMAFLIP

def main():
    root = ctk.CTk()
    app = SIGMAFLIP(root)
    root.mainloop()

if __name__ == '__main__':
    main()
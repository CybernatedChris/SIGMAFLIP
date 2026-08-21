import platform
import customtkinter as ctk

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

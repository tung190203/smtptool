#!/usr/bin/env python
"""
run_gui.py - Entry point for GUI version
Run: python run_gui.py (or ./run_gui from bundled app)
"""

import os
import sys
import traceback


def _runtime_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _show_startup_error(exc):
    root_dir = _runtime_dir()
    log_path = os.path.join(root_dir, "startup_error.log")
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(details)
    except Exception:
        pass

    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "SMTP Unlock Tool - startup error",
            f"App không mở được.\n\n{type(exc).__name__}: {exc}\n\nLog: {log_path}",
        )
        root.destroy()
    except Exception:
        pass


def main():
    try:
        from gui import tk, SMTPUnlockGUI
        root = tk.Tk()
        root.update_idletasks()
        root.deiconify()
        root.lift()
        try:
            root.focus_force()
        except Exception:
            pass
        app = SMTPUnlockGUI(root)
        root.mainloop()
    except Exception as exc:
        _show_startup_error(exc)


if __name__ == "__main__":
    main()

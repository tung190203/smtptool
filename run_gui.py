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


def _ensure_stdio():
    if not getattr(sys, "frozen", False):
        return
    root_dir = _runtime_dir()
    os.makedirs(root_dir, exist_ok=True)
    log_path = os.path.join(root_dir, "gui_console.log")
    if sys.stdout is None:
        sys.stdout = open(log_path, "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout


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
        _ensure_stdio()
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

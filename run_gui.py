#!/usr/bin/env python
"""
run_gui.py - Entry point for GUI version
Run: python run_gui.py (or ./run_gui from bundled app)
"""

if __name__ == "__main__":
    from gui import tk, SMTPUnlockGUI
    root = tk.Tk()
    app = SMTPUnlockGUI(root)
    root.mainloop()

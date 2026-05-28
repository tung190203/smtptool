#!/usr/bin/env python
"""
gui.py - SMTP Unlock Tool with GUI (Tkinter)
Khách hàng có thể paste email|password trực tiếp vào text box
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
import threading
import os
import sys
import json
import subprocess
from datetime import datetime

# Import từ run.py
MODULE_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, MODULE_ROOT)

if getattr(sys, "frozen", False) and os.name == "nt":
    ROOT = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "SMTP Unlock Tool")
elif getattr(sys, "frozen", False):
    ROOT = os.path.dirname(sys.executable)
else:
    ROOT = MODULE_ROOT
INPUT_FILE = os.path.join(ROOT, "input.txt")
PROXY_FILE = os.path.join(ROOT, "proxy.txt")
OUTPUT_DIR = os.path.join(ROOT, "output")
ENABLED_FILE = os.path.join(OUTPUT_DIR, "enabled.txt")
FAILED_FILE = os.path.join(OUTPUT_DIR, "failed.txt")
ERROR_REASON_FILE = os.path.join(OUTPUT_DIR, "error_reason.txt")
LOG_FILE = os.path.join(OUTPUT_DIR, "run.log")

class SMTPUnlockGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SMTP Unlock Tool")
        self.root.geometry("800x700")
        self.root.resizable(True, True)
        self.running = False
        self.main_thread_id = threading.get_ident()
        
        # Main frame
        main_frame = ttk.Frame(root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title
        title = ttk.Label(main_frame, text="SMTP Unlock Tool - Bật SMTP cho Hotmail/Outlook", 
                         font=("Arial", 14, "bold"))
        title.pack(pady=10)
        
        # Input section
        input_label = ttk.Label(main_frame, text="Paste email|password hoặc email|password|refresh_token|client_id:", 
                               font=("Arial", 10))
        input_label.pack(anchor="w", pady=(10, 5))
        
        self.input_text = scrolledtext.ScrolledText(main_frame, height=12, width=80, 
                                                     font=("Courier", 9))
        self.input_text.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Example text
        example_label = ttk.Label(main_frame, 
                                 text="Ví dụ: user1@outlook.com|password123\n       user2@hotmail.com|pass@#|refresh_token|client_id", 
                                 font=("Arial", 8, "italic"), foreground="gray")
        example_label.pack(anchor="w")
        
        # Worker threads section
        worker_frame = ttk.Frame(main_frame)
        worker_frame.pack(fill=tk.X, pady=10)
        
        worker_label = ttk.Label(worker_frame, text="Số luồng (threads):", font=("Arial", 10))
        worker_label.pack(side=tk.LEFT, padx=5)
        
        self.worker_var = tk.StringVar(value="4")
        worker_spin = ttk.Spinbox(worker_frame, from_=1, to=16, textvariable=self.worker_var, 
                                  width=5, font=("Arial", 10))
        worker_spin.pack(side=tk.LEFT, padx=5)
        
        worker_hint = ttk.Label(worker_frame, text="(2-8 khuyên dùng)", 
                               font=("Arial", 8, "italic"), foreground="gray")
        worker_hint.pack(side=tk.LEFT, padx=5)
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        
        self.run_button = ttk.Button(button_frame, text="▶ Chạy", command=self.run_tool)
        self.run_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_button = ttk.Button(button_frame, text="⏹ Dừng", command=self.stop_tool, 
                                      state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        clear_button = ttk.Button(button_frame, text="🗑 Xoá", command=self.clear_input)
        clear_button.pack(side=tk.LEFT, padx=5)

        proxy_button = ttk.Button(button_frame, text="Mở proxy.txt", command=self.open_proxy_file)
        proxy_button.pack(side=tk.LEFT, padx=5)

        output_button = ttk.Button(button_frame, text="Mở output", command=self.open_output_folder)
        output_button.pack(side=tk.LEFT, padx=5)

        failed_button = ttk.Button(button_frame, text="failed.txt", command=lambda: self.open_output_file(FAILED_FILE))
        failed_button.pack(side=tk.LEFT, padx=5)

        log_button = ttk.Button(button_frame, text="run.log", command=lambda: self.open_output_file(LOG_FILE))
        log_button.pack(side=tk.LEFT, padx=5)
        
        # Status / Output section
        status_label = ttk.Label(main_frame, text="Kết quả:", font=("Arial", 10, "bold"))
        status_label.pack(anchor="w", pady=(10, 5))
        
        self.status_text = scrolledtext.ScrolledText(main_frame, height=6, width=80, 
                                                      font=("Courier", 9), state=tk.DISABLED)
        self.status_text.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Status bar
        self.status_var = tk.StringVar(value="Sẵn sàng")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, 
                              font=("Arial", 9), relief=tk.SUNKEN)
        status_bar.pack(fill=tk.X, pady=5)
    
    def log(self, msg):
        """Add message to status text"""
        if threading.get_ident() != self.main_thread_id:
            self.root.after(0, self.log, msg)
            return
        self.status_text.config(state=tk.NORMAL)
        self.status_text.insert(tk.END, f"{msg}\n")
        self.status_text.see(tk.END)
        self.status_text.config(state=tk.DISABLED)
    
    def clear_input(self):
        """Clear input text"""
        self.input_text.delete("1.0", tk.END)

    def open_proxy_file(self):
        """Open proxy.txt with the platform default editor."""
        try:
            os.makedirs(ROOT, exist_ok=True)
            if not os.path.exists(PROXY_FILE):
                with open(PROXY_FILE, "w", encoding="utf-8") as f:
                    f.write("")
            self.open_path(PROXY_FILE)

            self.log(f"Đã mở proxy.txt: {PROXY_FILE}")
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không mở được proxy.txt:\n{e}")

    def open_output_folder(self):
        try:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            self.open_path(OUTPUT_DIR)
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không mở được thư mục output:\n{e}")

    def open_output_file(self, path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")
            self.open_path(path)
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không mở được file:\n{path}\n\n{e}")

    def open_path(self, path):
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    
    def parse_input(self):
        """Parse input text and save to input.txt"""
        content = self.input_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showerror("Lỗi", "Vui lòng paste email|password vào text box.")
            return False
        
        lines = content.split("\n")
        valid_lines = []
        
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2:
                messagebox.showerror("Lỗi format", 
                                   f"Dòng {i} không hợp lệ. Hỗ trợ email|password hoặc email|password|refresh_token|client_id:\n{line}")
                return False
            valid_lines.append(line)
        
        if not valid_lines:
            messagebox.showerror("Lỗi", "Không tìm thấy account hợp lệ.")
            return False
        
        # Save to input.txt
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(INPUT_FILE, "w", encoding="utf-8") as f:
            for line in valid_lines:
                f.write(line + "\n")
        
        self.log(f"✓ Parse xong: {len(valid_lines)} account")
        return True
    
    def run_tool_thread(self, workers):
        """Run tool in background thread"""
        try:
            self.log("🚀 Bắt đầu chạy...")
            self.log(f"⏱  Số luồng: {workers}")
            self.log(f"📄 Input: {INPUT_FILE}")
            self.log(f"🌐 Proxy file: {PROXY_FILE}")
            
            import run as backend
            backend.LOG_SINK = self.log
            load_accounts = backend.load_accounts
            load_proxies = backend.load_proxies
            process = backend.process
            ThreadPoolExecutor = backend.ThreadPoolExecutor
            as_completed = backend.as_completed
            time = backend.time
            
            # Load and show account count
            accounts = load_accounts()
            proxies = load_proxies()
            if not accounts:
                self.log("❌ Không có account hợp lệ.")
                return

            os.makedirs(OUTPUT_DIR, exist_ok=True)
            for path in (ENABLED_FILE, FAILED_FILE, ERROR_REASON_FILE, LOG_FILE):
                if os.path.exists(path):
                    os.remove(path)
            
            self.log(f"📋 Accounts: {len(accounts)}")
            if proxies:
                self.log(f"🌐 Proxies: {len(proxies)}")
                if workers > len(proxies):
                    self.log(f"ℹ Có {len(proxies)} proxy nên giảm từ {workers} xuống {len(proxies)} luồng để tránh share proxy cùng lúc.")
                    workers = len(proxies)
            else:
                self.log(f"🌐 Không có proxy (chạy IP gốc)")
            
            def worker(item):
                idx, total, em, pw, rec, p = item
                try:
                    return process(item)
                except Exception as e:
                    self.log(f"❌ [{idx}/{total}] {em} lỗi kỹ thuật: {type(e).__name__}: {e}")
                    return False
            
            items = [
                (i + 1, len(accounts), em, pw, rec, proxies[i % len(proxies)] if proxies else None)
                for i, (em, pw, rec) in enumerate(accounts)
            ]
            ok_count = 0
            fail_count = 0
            
            self.log("\n▶ Chạy...")
            t0 = time.time()
            
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = [ex.submit(worker, item) for item in items]
                for f in as_completed(futures):
                    if f.result():
                        ok_count += 1
                    else:
                        fail_count += 1
            
            elapsed = time.time() - t0
            
            # Show results
            self.log("\n" + "="*60)
            self.log(f"✅ Thành công: {ok_count}/{len(accounts)}")
            self.log(f"❌ Thất bại:   {fail_count}/{len(accounts)}")
            self.log(f"⏱  Thời gian:   {elapsed:.1f}s")
            self.log(f"📁 Output:   {OUTPUT_DIR}")
            self.log("="*60)
            
            # Show results summary
            if os.path.exists(ENABLED_FILE):
                with open(ENABLED_FILE, "r", encoding="utf-8") as f:
                    enabled_count = len(f.readlines())
                self.log(f"\n📄 Thành công lưu vào: output/enabled.txt ({enabled_count} account)")
            
            if os.path.exists(FAILED_FILE):
                with open(FAILED_FILE, "r", encoding="utf-8") as f:
                    failed_count = len(f.readlines())
                self.log(f"📄 Thất bại lưu vào: output/failed.txt ({failed_count} account)")

            if os.path.exists(ERROR_REASON_FILE):
                self.log("\nLý do thất bại:")
                with open(ERROR_REASON_FILE, "r", encoding="utf-8") as f:
                    for line in f.readlines()[-10:]:
                        self.log("  " + line.strip())
            
            self.root.after(0, lambda: messagebox.showinfo(
                "Hoàn thành",
                f"Hoàn thành!\nThành công: {ok_count}\nThất bại: {fail_count}",
            ))
            
        except Exception as e:
            self.log(f"❌ Lỗi: {e}")
            self.root.after(0, lambda err=e: messagebox.showerror("Lỗi", f"Lỗi khi chạy: {err}"))
        finally:
            try:
                import run as backend
                backend.LOG_SINK = None
            except Exception:
                pass
            self.root.after(0, self.finish_run_ui)

    def finish_run_ui(self):
        self.running = False
        self.run_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.status_var.set("Hoàn thành")
    
    def run_tool(self):
        """Parse input and run tool"""
        if self.running:
            messagebox.showwarning("Cảnh báo", "Tool đang chạy, vui lòng chờ.")
            return
        
        if not self.parse_input():
            return
        
        try:
            workers = int(self.worker_var.get())
            if workers < 1:
                workers = 1
        except ValueError:
            messagebox.showerror("Lỗi", "Số luồng phải là số nguyên.")
            return
        
        self.running = True
        self.run_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.status_text.config(state=tk.NORMAL)
        self.status_text.delete("1.0", tk.END)
        self.status_text.config(state=tk.DISABLED)
        self.status_var.set("Chạy...")
        
        # Run in background thread
        thread = threading.Thread(target=self.run_tool_thread, args=(workers,), daemon=True)
        thread.start()
    
    def stop_tool(self):
        """Stop tool (graceful)"""
        self.running = False
        self.run_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.status_var.set("Đã dừng")
        self.log("\n⏹ Đã dừng (accounts còn lại sẽ hủy)")


if __name__ == "__main__":
    root = tk.Tk()
    app = SMTPUnlockGUI(root)
    root.mainloop()

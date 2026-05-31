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
from concurrent.futures import wait, FIRST_COMPLETED
from datetime import datetime
from collections import Counter

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
UNLOCKED_FILE = os.path.join(OUTPUT_DIR, "unlocked.txt")
LIVE_FILE = os.path.join(OUTPUT_DIR, "live.txt")
DEAD_FILE = os.path.join(OUTPUT_DIR, "dead.txt")
LIVE_REASON_FILE = os.path.join(OUTPUT_DIR, "live_reason.txt")
LOG_FILE = os.path.join(OUTPUT_DIR, "run.log")

class SMTPUnlockGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SMTP Unlock Tool")
        self.root.geometry("800x700")
        self.root.resizable(True, True)
        self.running = False
        self.main_thread_id = threading.get_ident()
        self.active_run_id = 0
        self.stop_event = threading.Event()
        
        # Main frame
        main_frame = ttk.Frame(root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title
        title = ttk.Label(main_frame, text="SMTP Unlock Tool - Bật SMTP cho Hotmail/Outlook", 
                         font=("Arial", 14, "bold"))
        title.pack(pady=10)
        
        # Input section
        input_label = ttk.Label(
            main_frame,
            text="Paste email|password|mkp (mail khôi phục) hoặc email|password|refresh_token|client_id:",
                               font=("Arial", 10))
        input_label.pack(anchor="w", pady=(10, 5))
        
        self.input_text = scrolledtext.ScrolledText(main_frame, height=12, width=80, 
                                                     font=("Courier", 9))
        self.input_text.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Example text
        example_label = ttk.Label(main_frame, 
                                 text="Ví dụ: user1@outlook.com|password123|user1@smvmail.com\n       user2@hotmail.com|pass@#|refresh_token|client_id",
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

        self.ignore_refresh_var = tk.BooleanVar(value=False)
        ignore_refresh_chk = ttk.Checkbutton(
            worker_frame,
            text="Không dùng refresh_token khi Check live",
            variable=self.ignore_refresh_var,
        )
        ignore_refresh_chk.pack(side=tk.RIGHT, padx=5)
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        
        self.run_button = ttk.Button(button_frame, text="▶ Chạy", command=self.run_tool)
        self.run_button.pack(side=tk.LEFT, padx=5)

        self.check_button = ttk.Button(button_frame, text="✓ Check live", command=self.check_live)
        self.check_button.pack(side=tk.LEFT, padx=5)
        
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

        live_button = ttk.Button(button_frame, text="live.txt", command=lambda: self.open_output_file(LIVE_FILE))
        live_button.pack(side=tk.LEFT, padx=5)

        unlocked_button = ttk.Button(button_frame, text="unlocked.txt", command=lambda: self.open_output_file(UNLOCKED_FILE))
        unlocked_button.pack(side=tk.LEFT, padx=5)

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

    def make_log_sink(self, run_id):
        def sink(msg):
            if self.active_run_id == run_id:
                self.log(msg)
        return sink
    
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
            messagebox.showerror("Lỗi", "Vui lòng paste email|password|mkp vào text box.")
            return False
        
        lines = content.split("\n")
        valid_lines = []
        
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) not in (3, 4):
                messagebox.showerror("Lỗi format", 
                                   f"Dòng {i} không hợp lệ. Hỗ trợ email|password|mkp hoặc email|password|refresh_token|client_id:\n{line}")
                return False
            if len(parts) == 3 and "@" not in parts[2]:
                messagebox.showerror("Lỗi format", f"Dòng {i} thiếu mkp dạng full email:\n{line}")
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

    def parse_check_accounts(self):
        """Parse input textbox for live check, preserving optional token columns."""
        content = self.input_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showerror("Lỗi", "Vui lòng paste email|password|mkp vào text box.")
            return None

        accounts = []
        for i, raw_line in enumerate(content.splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) not in (3, 4):
                messagebox.showerror(
                    "Lỗi format",
                    f"Dòng {i} không hợp lệ. Hỗ trợ email|password|mkp hoặc email|password|refresh_token|client_id:\n{line}",
                )
                return None
            if len(parts) == 3 and "@" not in parts[2]:
                messagebox.showerror("Lỗi format", f"Dòng {i} thiếu mkp dạng full email:\n{line}")
                return None

            email = parts[0]
            password = parts[1]
            recovery_email = ""
            refresh_token = ""
            client_id = ""
            if len(parts) >= 4:
                refresh_token = parts[2]
                client_id = parts[3]
            elif len(parts) == 3:
                recovery_email = parts[2]
            accounts.append({
                "line": line,
                "email": email,
                "password": password,
                "recovery_email": recovery_email,
                "refresh_token": refresh_token,
                "client_id": client_id,
            })

        if not accounts:
            messagebox.showerror("Lỗi", "Không tìm thấy account hợp lệ.")
            return None
        return accounts
    
    def run_tool_thread(self, workers, run_id, stop_event):
        """Run tool in background thread"""
        sink = None
        try:
            self.log("🚀 Bắt đầu chạy...")
            self.log(f"⏱  Số luồng: {workers}")
            self.log(f"📄 Input: {INPUT_FILE}")
            self.log(f"🌐 Proxy file: {PROXY_FILE}")
            
            import run as backend
            sink = self.make_log_sink(run_id)
            backend.LOG_SINK = sink
            backend.LOG_RUN_ID = run_id
            backend.STOP_EVENT = stop_event
            self.log(f"🔖 Version: {getattr(backend, 'APP_VERSION', 'unknown')}")
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
            for path in (ENABLED_FILE, FAILED_FILE, ERROR_REASON_FILE, UNLOCKED_FILE, LOG_FILE):
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
                if stop_event.is_set():
                    return False
                idx, total, em, pw = item[0], item[1], item[2], item[3]
                try:
                    return process(item)
                except Exception as e:
                    self.log(f"❌ [{idx}/{total}] {em} lỗi kỹ thuật: {type(e).__name__}: {e}")
                    return False
            
            items = [
                (i + 1, len(accounts), em, pw, rec, refresh, client_id, proxies[i % len(proxies)] if proxies else None, run_id)
                for i, (em, pw, rec, refresh, client_id) in enumerate(accounts)
            ]
            ok_count = 0
            fail_count = 0
            
            self.log("\n▶ Chạy...")
            t0 = time.time()
            
            ex = ThreadPoolExecutor(max_workers=workers)
            futures = [ex.submit(worker, item) for item in items]
            pending = set(futures)
            try:
                while pending:
                    if stop_event.is_set() or self.active_run_id != run_id:
                        for f in pending:
                            f.cancel()
                        ex.shutdown(wait=False, cancel_futures=True)
                        self.log("⏹ Đã yêu cầu dừng run cũ; các phiên đang chạy sẽ thoát sớm.")
                        return
                    done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                    for f in done:
                        if f.cancelled():
                            fail_count += 1
                        elif f.result():
                            ok_count += 1
                        else:
                            fail_count += 1
            finally:
                if not stop_event.is_set():
                    ex.shutdown(wait=True)
            
            elapsed = time.time() - t0
            
            # Show results
            self.log("\n" + "="*60)
            self.log(f"✅ Thành công: {ok_count}/{len(accounts)}")
            self.log(f"❌ Thất bại:   {fail_count}/{len(accounts)}")
            self.log(f"⏱  Thời gian:   {elapsed:.1f}s")
            self.log(f"📁 Output:   {OUTPUT_DIR}")
            self.log("="*60)

            if os.path.exists(UNLOCKED_FILE):
                with open(UNLOCKED_FILE, "r", encoding="utf-8") as f:
                    unlocked_count = len(f.readlines())
                self.log(f"\n📄 Đã bật SMTP lưu vào: output/unlocked.txt ({unlocked_count} account)")
            
            # Show results summary
            if os.path.exists(ENABLED_FILE):
                with open(ENABLED_FILE, "r", encoding="utf-8") as f:
                    enabled_count = len(f.readlines())
                self.log(f"📄 Có refresh token lưu vào: output/enabled.txt ({enabled_count} account)")
            
            if os.path.exists(FAILED_FILE):
                with open(FAILED_FILE, "r", encoding="utf-8") as f:
                    failed_count = len(f.readlines())
                self.log(f"📄 Thất bại lưu vào: output/failed.txt ({failed_count} account)")

            if os.path.exists(ERROR_REASON_FILE):
                reasons = []
                with open(ERROR_REASON_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        reason = line.strip().split("|", 1)[1] if "|" in line else line.strip()
                        if reason:
                            reasons.append(reason)

                self.log("\nTop lý do fail:")
                for reason, count in Counter(reasons).most_common(8):
                    self.log(f"  {count}x {reason[:120]}")

                self.log("\nLý do thất bại gần nhất:")
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
                if getattr(backend, "LOG_SINK", None) is sink:
                    backend.LOG_SINK = None
                    backend.STOP_EVENT = None
            except Exception:
                pass
            if self.active_run_id == run_id:
                self.root.after(0, self.finish_run_ui)

    def check_live_thread(self, workers, accounts, ignore_refresh=False, run_id=0, stop_event=None):
        sink = None
        stop_event = stop_event or threading.Event()
        try:
            self.log("✓ Bắt đầu check live...")
            self.log(f"⏱  Số luồng: {workers}")
            self.log(f"🔧 Không dùng refresh_token: {ignore_refresh}")

            import run as backend
            sink = self.make_log_sink(run_id)
            backend.LOG_SINK = sink
            backend.LOG_RUN_ID = run_id
            backend.STOP_EVENT = stop_event
            load_proxies = backend.load_proxies
            check_live_account = backend.check_live_account
            append_line = backend.append_line
            ThreadPoolExecutor = backend.ThreadPoolExecutor
            as_completed = backend.as_completed
            time = backend.time

            proxies = load_proxies()
            if proxies:
                self.log(f"🌐 Proxies: {len(proxies)}")
                if workers > len(proxies):
                    self.log(f"ℹ Có {len(proxies)} proxy nên giảm từ {workers} xuống {len(proxies)} luồng.")
                    workers = len(proxies)
            else:
                self.log("🌐 Không có proxy (chạy IP gốc)")

            os.makedirs(OUTPUT_DIR, exist_ok=True)
            for path in (LIVE_FILE, DEAD_FILE, LIVE_REASON_FILE, LOG_FILE):
                if os.path.exists(path):
                    os.remove(path)

            def worker(item):
                if stop_event.is_set():
                    return False
                idx, total, acc, proxy = item
                email = acc["email"]
                proxy_label = proxy["server"] if proxy else "no-proxy"
                self.log(f"[{idx}/{total}] {email} ({proxy_label}) check...")
                try:
                    proxy_ok, proxy_reason = backend.check_proxy(proxy)
                    if proxy and not proxy_ok:
                        append_line(DEAD_FILE, f"{email}|{acc['password']}")
                        append_line(LIVE_REASON_FILE, f"{email}|{proxy_reason}")
                        self.log(f"❌ [{idx}/{total}] {email} -> {proxy_reason}")
                        return False

                    ok, reason, refresh_token = check_live_account(
                        email,
                        acc["password"],
                        recovery_email=acc["recovery_email"],
                        refresh_token=acc["refresh_token"],
                        client_id=acc["client_id"],
                        proxy=proxy,
                        use_refresh_token=not ignore_refresh,
                    )
                    if ok:
                        if refresh_token:
                            append_line(LIVE_FILE, f"{email}|{acc['password']}|{refresh_token}|{acc['client_id'] or backend.CLIENT_ID}")
                        else:
                            append_line(LIVE_FILE, acc["line"])
                        self.log(f"✅ [{idx}/{total}] {email} -> live")
                        return True

                    append_line(DEAD_FILE, f"{email}|{acc['password']}")
                    append_line(LIVE_REASON_FILE, f"{email}|{reason}")
                    self.log(f"❌ [{idx}/{total}] {email} -> {reason}")
                    return False
                except Exception as e:
                    reason = f"{type(e).__name__}: {e}"
                    append_line(DEAD_FILE, f"{email}|{acc['password']}")
                    append_line(LIVE_REASON_FILE, f"{email}|{reason}")
                    self.log(f"❌ [{idx}/{total}] {email} lỗi kỹ thuật: {reason}")
                    return False

            items = [
                (i + 1, len(accounts), acc, proxies[i % len(proxies)] if proxies else None)
                for i, acc in enumerate(accounts)
            ]
            live_count = 0
            dead_count = 0
            t0 = time.time()

            ex = ThreadPoolExecutor(max_workers=workers)
            futures = [ex.submit(worker, item) for item in items]
            pending = set(futures)
            try:
                while pending:
                    if stop_event.is_set() or self.active_run_id != run_id:
                        for f in pending:
                            f.cancel()
                        ex.shutdown(wait=False, cancel_futures=True)
                        self.log("⏹ Đã yêu cầu dừng check live cũ; các phiên đang chạy sẽ thoát sớm.")
                        return
                    done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                    for f in done:
                        if f.cancelled():
                            dead_count += 1
                        elif f.result():
                            live_count += 1
                        else:
                            dead_count += 1
            finally:
                if not stop_event.is_set():
                    ex.shutdown(wait=True)

            elapsed = time.time() - t0
            self.log("\n" + "="*60)
            self.log(f"✅ Live: {live_count}/{len(accounts)}")
            self.log(f"❌ Dead/Fail: {dead_count}/{len(accounts)}")
            self.log(f"⏱  Thời gian: {elapsed:.1f}s")
            self.log(f"📄 Live: output/live.txt")
            self.log(f"📄 Dead: output/dead.txt")
            self.log("="*60)

            self.root.after(0, lambda: messagebox.showinfo(
                "Check live xong",
                f"Live: {live_count}\nDead/Fail: {dead_count}",
            ))
        except Exception as e:
            self.log(f"❌ Lỗi check live: {e}")
            self.root.after(0, lambda err=e: messagebox.showerror("Lỗi", f"Lỗi khi check live: {err}"))
        finally:
            try:
                import run as backend
                if getattr(backend, "LOG_SINK", None) is sink:
                    backend.LOG_SINK = None
                    backend.STOP_EVENT = None
            except Exception:
                pass
            if self.active_run_id == run_id:
                self.root.after(0, self.finish_run_ui)

    def finish_run_ui(self):
        self.running = False
        self.run_button.config(state=tk.NORMAL)
        self.check_button.config(state=tk.NORMAL)
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
        self.active_run_id += 1
        run_id = self.active_run_id
        self.stop_event = threading.Event()
        self.run_button.config(state=tk.DISABLED)
        self.check_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.status_text.config(state=tk.NORMAL)
        self.status_text.delete("1.0", tk.END)
        self.status_text.config(state=tk.DISABLED)
        self.status_var.set("Chạy...")
        
        # Run in background thread
        thread = threading.Thread(
            target=self.run_tool_thread,
            args=(workers, run_id, self.stop_event),
            daemon=True,
        )
        thread.start()

    def check_live(self):
        """Check live before running unlock."""
        if self.running:
            messagebox.showwarning("Cảnh báo", "Tool đang chạy, vui lòng chờ.")
            return

        accounts = self.parse_check_accounts()
        if not accounts:
            return

        try:
            workers = int(self.worker_var.get())
            if workers < 1:
                workers = 1
        except ValueError:
            messagebox.showerror("Lỗi", "Số luồng phải là số nguyên.")
            return

        self.running = True
        self.active_run_id += 1
        run_id = self.active_run_id
        self.stop_event = threading.Event()
        self.run_button.config(state=tk.DISABLED)
        self.check_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.status_text.config(state=tk.NORMAL)
        self.status_text.delete("1.0", tk.END)
        self.status_text.config(state=tk.DISABLED)
        self.status_var.set("Check live...")

        ignore_refresh = self.ignore_refresh_var.get()
        thread = threading.Thread(
            target=self.check_live_thread,
            args=(workers, accounts, ignore_refresh, run_id, self.stop_event),
            daemon=True,
        )
        thread.start()
    
    def stop_tool(self):
        """Stop tool (graceful)"""
        self.stop_event.set()
        self.active_run_id += 1
        self.running = False
        self.run_button.config(state=tk.NORMAL)
        self.check_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.status_var.set("Đã dừng")
        self.log("\n⏹ Đã dừng (accounts còn lại sẽ hủy)")


if __name__ == "__main__":
    root = tk.Tk()
    app = SMTPUnlockGUI(root)
    root.mainloop()

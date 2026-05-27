HƯỚNG DẪN SỬ DỤNG - SMTP Unlock Tool

═══════════════════════════════════════════════════════════════════════════════

Công cụ này giúp bạn bật SMTP cho hàng loạt tài khoản Hotmail/Outlook một cách tự động.

CÓ 2 CÁCH DÙNG:
1. GUI (giao diện) — khuyên dùng, không cần sửa file
2. CLI (dòng lệnh) — nâng cao, chạy nhanh hơn

═══════════════════════════════════════════════════════════════════════════════

CÁCH 1: SỬ DỤNG GUI (EASY MODE) — KHUYÊN DÙNG

1) Tải và giải nén
   - Bạn đã có folder "smtp_unlock" chứa chương trình sẵn sàng.

2) Chạy GUI trên macOS
   ```bash
   cd /path/to/smtp_unlock
   chmod +x smtp_unlock_gui
   ./smtp_unlock_gui
   ```

3) Chạy GUI trên Windows
   - Double-click `smtp_unlock_gui.exe` (hoặc click "SMTP Unlock Tool GUI" từ Start Menu)

4) Trong cửa sổ GUI
   - Paste email|password vào text box (mỗi dòng 1 account)
   - Ví dụ:
     ```
     user1@hotmail.com|password123
     user2@outlook.com|mypass@#
     user3@live.com|Pass999
     ```
   - Không cần sửa file, không cần format phức tạp
   - Click "▶ Chạy"
   - Chọn số luồng (2-8 khuyên dùng)
   - Xem kết quả trực tiếp trong cửa sổ

5) Kết quả
   - Thành công: tính số account bật SMTP OK
   - Thất bại: tính số account thất bại
   - File output/enabled.txt + output/failed.txt được tạo

═══════════════════════════════════════════════════════════════════════════════

CÁCH 2: SỬ DỤNG CLI (ADVANCED MODE)

1) Chuẩn bị dữ liệu - File "input.txt"
   - Mở file input.txt bằng Text Editor (TextEdit / Notepad, không cần code editor)
   - Mỗi dòng là 1 tài khoản theo format: email|password
   - Ví dụ:
     ```
     user1@hotmail.com|password123
     user2@outlook.com|mypassword@#
     user3@live.com|Pass999
     ```
   - Nếu có recovery email (để verify nếu Microsoft hỏi):
     ```
     user1@hotmail.com|password123|recovery@gmail.com
     ```
   - Lưu file

2) (Tuỳ chọn) Proxy - File "proxy.txt"
   - Nếu IP bị chặn, thêm proxy
   - Mỗi dòng 1 proxy: host:port hoặc host:port:user:pass
   - Ví dụ:
     ```
     192.168.1.1:8080
     proxy.com:3128:user:pass
     ```

3) Chạy CLI trên macOS
   ```bash
   cd /path/to/smtp_unlock
   chmod +x smtp_unlock
   ./smtp_unlock 4
   ```
   (Số 4 = số luồng chạy song song; tùy máy, 2-8 là tốt)

4) Chạy CLI trên Windows
   ```
   cd C:\path\to\smtp_unlock
   smtp_unlock.exe 4
   ```
   (hoặc từ PowerShell: .\smtp_unlock.exe 4)

5) Xem kết quả
   - Sau khi chạy xong, kiểm tra:
     ```
     cat output/enabled.txt     # tài khoản bật SMTP thành công
     cat output/failed.txt      # tài khoản thất bại
     ```

═══════════════════════════════════════════════════════════════════════════════

CÁCH CHUẨN BỊ DỮ LIỆU MỌI NHƯ

Mẫu file input.txt hợp lệ:
```
user1@outlook.com|password1
user2@hotmail.com|password2
user3@live.com|pass@#$
user4@outlook.com|mypass|recovery@gmail.com
```

Những dòng này sẽ BỊ BỎ QUA:
```
# comment — dòng này bị bỏ qua (# ở đầu dòng)
(dòng trống)
```

═══════════════════════════════════════════════════════════════════════════════

VẤN ĐỀ HAY GẶP

❌ GUI không mở / "Not Found"
   → Chắc chắn bạn đã vào đúng folder chứa smtp_unlock_gui
   → Thử: ls smtp_unlock_gui (macOS) hoặc dir smtp_unlock_gui.exe (Windows)
   → Nếu không thấy: file build có lỗi, liên hệ support

❌ "Permission denied"
   → Chạy: chmod +x smtp_unlock_gui (macOS)
   → Rồi ./smtp_unlock_gui

❌ Toàn bộ tài khoản failed
   → Kiểm tra email/password trong GUI/input.txt có đúng không?
   → Nếu account bị locked → Microsoft yêu cầu xác minh (cần xử lý thủ công)
   → Nếu bị rate limit (429) → giảm số luồng hoặc dùng proxy

❌ "Playwright browser not found"
   → Browser runtime đã được đóng gói sẵn, không cần setup thêm
   → Nếu vẫn lỗi → liên hệ support

═══════════════════════════════════════════════════════════════════════════════

MẸO & KHUYẾN NGHỊ

💡 Tốc độ tốt nhất:
   - Sử dụng proxy pool (nếu có).
   - Chạy 4-8 luồng tùy máy.
   - Không quá 10 luồng (tránh rate limit).

💡 Recovery email:
   - Nếu Microsoft hỏi verify email, recovery email sẽ nhận code.
   - Dùng smvmail.com (mặc định: username@smvmail.com).
   - Hoặc cung cấp email thật nếu có.

💡 Chạy nhiều batch:
   - Bạn có thể chạy lại lần 2 với failed account từ output/failed.txt
   - Copy output/failed.txt → input.txt rồi chạy lại

═══════════════════════════════════════════════════════════════════════════════

CẦN GIÚP?

- Kiểm tra file "README.md" để tìm hiểu chi tiết hơn
- Kiểm tra "output/run.log" để xem log chi tiết của lần chạy trước
- Đảm bảo input.txt format đúng: email|password

═══════════════════════════════════════════════════════════════════════════════

1) Tải và giải nén
   - Bạn đã có folder "smtp_unlock" chứa chương trình sẵn sàng.

2) Chuẩn bị dữ liệu - File "input.txt"
   - Mở file input.txt
   - Mỗi dòng là 1 tài khoản theo format: email|password
   - Ví dụ:
     ```
     user1@hotmail.com|password123
     user2@outlook.com|mypassword@#
     user3@live.com|Pass999
     ```
   - Nếu có recovery email (để verify nếu Microsoft hỏi):
     ```
     user1@hotmail.com|password123|recovery@gmail.com
     ```

3) (Tuỳ chọn) Proxy - File "proxy.txt"
   - Nếu IP bị chặn, thêm proxy
   - Mỗi dòng 1 proxy: host:port hoặc host:port:user:pass
   - Ví dụ:
     ```
     192.168.1.1:8080
     proxy.com:3128:user:pass
     ```

4) Chạy chương trình
   - Mở Terminal
   - Đi vào thư mục chứa smtp_unlock:
     ```
     cd /path/to/smtp_unlock
     ```
   - Chạy:
     ```
     ./smtp_unlock 4
     ```
     (Số 4 = số luồng chạy song song; tùy máy, 2-8 là tốt)

   - Hoặc chạy và nhập số luồng khi được hỏi:
     ```
     ./smtp_unlock
     ```

5) Xem kết quả
   - Sau khi chạy xong, kiểm tra:
     ```
     cat output/enabled.txt     # tài khoản bật SMTP thành công
     cat output/failed.txt      # tài khoản thất bại
     ```

6) Hiểu kết quả
   - File "output/enabled.txt" chứa: email|password|refresh_token|client_id
     → Tài khoản này bật SMTP OK.
   - File "output/failed.txt" chứa: email|password
     → Tài khoản thất bại (sai password, account locked, checkpoint, etc.)

═══════════════════════════════════════════════════════════════════════════════

VẤN ĐỀ HAY GẶP

❌ "Cannot find smtp_unlock"
   → Chắc chắn bạn đã vào đúng folder chứa smtp_unlock
   → Thử: ls smtp_unlock

❌ "Permission denied"
   → Chạy: chmod +x smtp_unlock
   → Rồi ./smtp_unlock 4

❌ Toàn bộ tài khoản failed
   → Kiểm tra email/password trong input.txt có đúng không?
   → Nếu account bị locked → Microsoft yêu cầu xác minh (cần xử lý thủ công)
   → Nếu bị rate limit (429) → giảm số luồng hoặc dùng proxy

❌ "Playwright browser not found"
   → Browser runtime đã được đóng gói sẵn, không cần setup thêm
   → Nếu vẫn lỗi → liên hệ support

═══════════════════════════════════════════════════════════════════════════════

MẸO & KHUYẾN NGHỊ

💡 Tốc độ tốt nhất:
   - Sử dụng proxy pool (nếu có).
   - Chạy 4-8 luồng tùy máy.
   - Không quá 10 luồng (tránh rate limit).

💡 Recovery email:
   - Nếu Microsoft hỏi verify email, recovery email sẽ nhận code.
   - Dùng smvmail.com (mặc định: username@smvmail.com).
   - Hoặc cung cấp email thật nếu có.

💡 Chạy nhiều batch:
   - Bạn có thể chạy lại lần 2 với failed account từ output/failed.txt
   - Copy output/failed.txt → input.txt rồi chạy lại

═══════════════════════════════════════════════════════════════════════════════

CÁCH DÙNG (Windows)

1. Tải file SMTP_Unlock_Installer.exe
2. Chạy installer, click "Next" cho tới "Install"
3. Sau khi cài, sẽ có shortcut "SMTP Unlock Tool" trên Desktop hoặc Start Menu
4. Click để mở chương trình
5. Làm theo các bước 2-6 ở trên (macOS) nhưng thay vì ./smtp_unlock 4 là click "Run"

═══════════════════════════════════════════════════════════════════════════════

CẦN GIÚP?

- Kiểm tra file "README.md" để tìm hiểu chi tiết hơn
- Kiểm tra "output/run.log" để xem log chi tiết của lần chạy trước
- Đảm bảo input.txt format đúng: email|password

═══════════════════════════════════════════════════════════════════════════════

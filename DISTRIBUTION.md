PHÂN PHỐI CHO KHÁCH HÀNG - CHECKLIST

Bạn đã build xong cho macOS. Đây là các bước để phân phối:

═══════════════════════════════════════════════════════════════════════════════

PHÂN PHỐI CHO KHÁCH (macOS)

1) Chuẩn bị folder phân phối
   - Folder: dist/smtp_unlock/
   - Trong đó có: smtp_unlock (executable), _internal/, ms-playwright/
   - Kích thước: ~200-300 MB (do Playwright browser)

2) Tạo ZIP (tuỳ chọn nhưng khuyên dùng)
   ```
   cd dist
   zip -r smtp_unlock_mac.zip smtp_unlock/
   ```
   - Kết quả: smtp_unlock_mac.zip (~200-300 MB)

3) Gửi cho khách
   - Gửi file ZIP hoặc folder smtp_unlock/
   - Kèm theo: USAGE_CUSTOMER.md (hướng dẫn sử dụng)

4) Khách tải về và giải nén
   - Khách sẽ có folder smtp_unlock/
   - Khách đọc USAGE_CUSTOMER.md

5) Khách chuẩn bị dữ liệu
   - input.txt (email|password)
   - proxy.txt (tuỳ chọn)
   - Đặt trong cùng thư mục với smtp_unlock

6) Khách chạy
   ```
   cd /path/to/smtp_unlock
   ./smtp_unlock 4
   ```

7) Khách nhận kết quả
   - output/enabled.txt (thành công)
   - output/failed.txt (thất bại)

═══════════════════════════════════════════════════════════════════════════════

PHÂN PHỐI CHO KHÁCH (Windows)

1) Bạn phải build trên Windows
   - Đem project sang máy Windows
   - Chạy PowerShell:
     ```
     .\build_windows.ps1        # build app
     .\inno_build.ps1           # build installer
     ```
   - Kết quả: installer\SMTP_Unlock_Installer.exe

2) Gửi cho khách
   - File: SMTP_Unlock_Installer.exe (~200-300 MB)
   - Kèm: USAGE_CUSTOMER.md

3) Khách chạy installer
   - Double-click → Next → Finish
   - Có shortcut trên Desktop/Start Menu

4) Khách chuẩn bị input.txt + proxy.txt
   - Đặt trong folder cài đặt (ví dụ C:\Program Files\SMTP Unlock Tool\)
   - Hoặc nơi khách muốn chạy

5) Khách chạy từ shortcut hoặc command line
   - Hoặc double-click shortcut
   - Hoặc cmd: run.exe 4

═══════════════════════════════════════════════════════════════════════════════

LỜI NHẮC

✓ Kiểm tra macOS bundle chạy OK trước khi gửi
  ```
  cd dist/smtp_unlock
  chmod +x smtp_unlock
  ./smtp_unlock 1        # test với 1 luồng, 1 account
  ```

✓ Cho khách biết:
  - Format input.txt: email|password
  - Số luồng: 2-8 là tốt (tùy máy)
  - Nếu thất bại: kiểm tra password, tài khoản có bị lock không

✓ Nếu khách báo lỗi Playwright:
  - Browser runtime đã trong folder
  - Thử khởi động lại máy hoặc cập nhật macOS
  - Nếu vẫn lỗi → liên hệ bạn để debug

✓ Hỗ trợ khách:
  - Khách có thể chạy lại lần 2 với failed.txt
  - Giảm luồng nếu bị rate limit
  - Dùng proxy nếu IP bị block

═══════════════════════════════════════════════════════════════════════════════

TIẾP THEO (Windows Build)

Khi bạn có máy Windows:
1. Đem project sang Windows
2. Chạy: .\build_windows.ps1
3. Chạy: .\inno_build.ps1
4. Lấy installer\SMTP_Unlock_Installer.exe để phân phối

═══════════════════════════════════════════════════════════════════════════════

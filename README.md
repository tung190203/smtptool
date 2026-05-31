# Tool bật SMTP cho Hotmail/Outlook (run.py)

Tài liệu này mô tả chức năng, cách sử dụng, định dạng file và luồng hoạt động của công cụ "bật SMTP" trong repository này. File chính là `run.py`.

**Mục lục**
- Tổng quan
- Tính năng chính
- Yêu cầu môi trường
- Định dạng file đầu vào
- Định dạng proxy
- Output và log
- Luồng xử lý (flow) cho mỗi account
- Tuỳ chọn dòng lệnh và config
- Vấn đề hay gặp & khắc phục
- Ghi chú bảo mật & đạo đức

**Tổng quan**

- Công cụ dùng Playwright để tự động đăng nhập tài khoản Microsoft/Outlook, thay đổi cài đặt mailbox (SetConsumerMailbox) để bật SMTP/IMAP/POP, rồi lấy token OAuth để kiểm tra kết nối SMTP (XOAUTH2).
- Mục tiêu: xử lý hàng loạt tài khoản, bật SMTP cho nhiều Hotmail/Live/Outlook account.

**Tính năng chính**

- Tự động đăng nhập MSA (Microsoft Account) bằng Playwright, xử lý trang "Verify your email" bằng mail khôi phục/mkp nếu cung cấp.
- Sniff header request (service.svc) để lấy MSAuth1.0 token và header `x-anchormailbox`.
- Thực thi fetch() trong context trang để gọi `SetConsumerMailbox` và bật `SmtpClientAuthenticationDisabled = false`.
- Nếu API không trả xác nhận rõ ràng, tiến hành luồng OAuth để lấy access token scope SMTP và kiểm tra kết nối SMTP thực tế (XOAUTH2) lên `smtp-mail.outlook.com` hoặc `smtp.office365.com`.
- Hỗ trợ proxy pool (round-robin) và pre-warm browser instances để tăng throughput.

**Yêu cầu môi trường**

- Python 3.8+
- Thư viện Python: `playwright`, `requests`

Cài đặt đề xuất:

```bash
python -m pip install requests playwright
python -m playwright install chromium
```

**Định dạng file đầu vào (`input.txt`)**

- Mỗi dòng là một account.
- Hỗ trợ các format:
  - `email|password`
  - `email|password|mkp`  (mkp là full mail khôi phục dùng để nhận OTP, ví dụ `user@smvmail.com`)
  - `email|password|refresh_token|client_id`  (2 cột cuối được chấp nhận nhưng không dùng)
- Dấu `|` là phân tách. Dòng rỗng hoặc bắt đầu bằng `#` sẽ bị bỏ qua.

Ví dụ:
```
user1@hotmail.com|password1|user1@smvmail.com
user2@hotmail.com|password2
user3@hotmail.com|password3|refresh_token|client_id
```

**Định dạng proxy (`proxy.txt`)**

- Mỗi proxy trên 1 dòng. Hỗ trợ các dạng:
  - `host:port`
  - `host:port:user:pass`
  - `http://host:port` hoặc `http://user:pass@host:port`
- Tool sẽ parse thành dict dùng cho Playwright; nhiều worker sẽ share proxy theo cơ chế round-robin.

**Output & Log**

- Thư mục `output/` được tạo ra khi chạy.
  - `output/enabled.txt` — chứa các account đã bật SMTP thành công. Format: `email|password|refresh_token|client_id`.
  - `output/unlocked.txt` — account đã bật SMTP thành công, kể cả khi chưa lấy được refresh token mới.
  - `output/failed.txt` — các account thất bại. Format: `email|password`.
  - `output/live.txt` — kết quả từ nút `Check live` trong GUI, chứa account/token SMTP đang live.
  - `output/dead.txt` — account check live thất bại.
  - `output/live_reason.txt` — lý do chi tiết khi check live thất bại.
  - `output/run.log` — log chạy chi tiết (append).
- Thư mục `debug_pw/` (tuỳ chọn) chứa screenshot khi `DEBUG_SHOTS = True`.

**Check live trước khi chạy**

- Trên GUI có nút `Check live` để kiểm tra trước khi bấm `Chạy`.
- Với input `email|password|refresh_token|client_id`, tool đổi refresh token sang SMTP scope rồi test XOAUTH2.
- Với input `email|password` hoặc `email|password|mkp`, tool chạy OAuth để lấy refresh token rồi test SMTP XOAUTH2.
- Check live không gọi API bật SMTP/IMAP/POP, chỉ kiểm tra trạng thái đăng nhập/token và SMTP hiện tại.

**Luồng xử lý (mỗi account)**

1. Lấy proxy (nếu có) từ pool (round-robin). Browser được pre-warm và chia sẻ cho mỗi thread; proxy gán ở context-level.
2. Dùng Playwright mở trang login.force URL (login.live.com → redirect sang outlook).
3. Điền email → password, xử lý các bước bổ sung:
   - "Verify your email" → điền `mkp` (từ input hoặc auto `username@smvmail.com`), poll mail free để lấy code, điền code.
   - KMSI / Consent / Skip page → click tự động nếu có.
4. Khi vào được `outlook.live.com` (hoặc khi script sniff ra request chứa `service.svc` có header `Authorization: MSAuth1.0` và `x-anchormailbox`), inject `fetch()` gọi `SetConsumerMailbox` với body bật SMTP/IMAP/POP.
5. Nếu API trả `WasSuccessful=true` → fast path: ghi là `unlocked` và thử lấy refresh_token để lưu vào `enabled.txt`.
6. Nếu API không trả rõ ràng → tiến hành OAuth (GraphAuth) để lấy refresh token rồi exchange scope `SMTP.Send` để lấy SMTP access token, test XOAUTH2 trên host SMTP.
7. Nếu SMTP AUTH succeeds (235) → ghi `enabled.txt` kèm refresh token; ngược lại ghi `failed.txt`.

**Tham số và tuỳ chọn**

- Truyền số luồng qua dòng lệnh: `python run.py 8` (nếu không truyền, script sẽ hỏi interactive số luồng).
- Mặc định bạn có thể bật `DEBUG_SHOTS = True` trong `run.py` để lưu screenshot debug vào `debug_pw/`.
- Một số constant có thể điều chỉnh trong `run.py` như `CLIENT_ID`, `SCOPE_GRAPH`, `DEFAULT_TIMEOUT`, `CHROME_FLAGS`.

**Các hàm/khối quan trọng (tóm tắt)**

- `load_accounts()` — đọc `input.txt`, trả list `(email, password, mkp)`.
- `load_proxies()` — đọc `proxy.txt`, trả list proxy dict.
- `_get_thread_browser()` — khởi tạo và cache browser instance cho mỗi thread.
- `GraphAuth` — class thực hiện flow OAuth (authorization_code) bằng HTTP requests (dùng để lấy refresh token nếu cần).
- `run_oauth()` — wrapper gọi `GraphAuth.run()` để lấy `access_token`/`refresh_token`.
- `get_smtp_token()` — dùng `run_oauth()` + `refresh_for_scope()` để lấy SMTP access token.
- `test_smtp()` — thử AUTH XOAUTH2 với SMTP host; trả `(code, msg)`.
- `login_outlook()` — flow Playwright để đăng nhập và xử lý verify-email + KMSI.
- `unlock_account()` — orchestrator cho mỗi account (Playwright sniff → inject SetConsumerMailbox → OAuth → test SMTP).
- `process()` — wrapper gọi `unlock_account()` và ghi kết quả vào file output.

**Vấn đề hay gặp & khắc phục**

- Rate Limit / 429: Giảm tốc độ, sử dụng proxy pool, giảm số luồng.
- Không nhận được code từ mail free: kiểm tra `mkp` đúng full email và đúng domain site lấy OTP, hoặc tăng timeout poll.
- Playwright cần cài `chromium` bằng `playwright install chromium`.
- Nếu bị checkpoint / MFA / account locked → tool sẽ báo `login_failed` hoặc reason tương ứng, account cần xử lý thủ công.

**Bảo mật & đạo đức**

- Công cụ can thiệp hành vi tài khoản người khác có thể vi phạm Điều khoản dịch vụ của Microsoft hoặc pháp luật. Hãy đảm bảo bạn có quyền hợp pháp để thao tác các account này.
- Không sử dụng credentials thật của người khác nếu không được phép.

**Muốn mở rộng / tinh chỉnh**

- Lưu thêm `recovery_email` vào `enabled.txt` khi thành công.
- Thêm `--no-headless` option hoặc logging chi tiết hơn.
- Hỗ trợ lấy OTP qua SMS gateway thay vì smvmail.

---

Nếu muốn, tôi có thể:
- thêm `requirements.txt` và script chạy sample, hoặc
- hiện thực hoá một file `USAGE.md` ngắn gọn hơn, hoặc
- cập nhật `enabled.txt` để lưu thêm `recovery_email` và timestamp.

Chọn một tuỳ chọn để tôi tiếp tục sửa hoặc bổ sung tài liệu.

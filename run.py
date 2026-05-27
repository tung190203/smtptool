"""
run.py — Tool SMTP unlock 1-file all-in-one.

Flow per account:
  1. Mở Playwright Chromium (có proxy nếu có)
  2. Login Outlook (handle verify-email page với smvmail OTC nếu gặp)
  3. Vào inbox /mail/0/ → sniff MSAuth1.0 token + x-anchormailbox từ service.svc request
  4. page.evaluate fetch() POST SetConsumerMailbox với:
       SmtpClientAuthenticationDisabled = false
       PopEnabled = true
       ImapEnabled = true
  5. Đóng browser
  6. OAuth HTTP lấy SMTP access token
  7. Test SMTP XOAUTH2:
       235          → unlocked
       API non-2xx  → api_failed_<status>
       SMTP fail    → still_blocked / smtp_token_failed

Cách dùng:
    1. Sửa input.txt format: email|password[|recovery_email] (1 dòng 1 account)
  2. (Tùy chọn) Sửa proxy.txt — 1 proxy/dòng
  3. python run.py
  4. Nhập số luồng
  5. Kết quả: output/enabled.txt + output/failed.txt
"""

import os, re, sys, time, json, base64, smtplib, atexit
from urllib.parse import quote, urlencode, unquote, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, local as thread_local
from queue import Queue

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Paths ─────────────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False) and os.name == "nt":
    ROOT = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "SMTP Unlock Tool")
elif getattr(sys, "frozen", False):
    ROOT = os.path.dirname(sys.executable)
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(ROOT, "input.txt")
PROXY_FILE = os.path.join(ROOT, "proxy.txt")
OUTPUT_DIR = os.path.join(ROOT, "output")
ENABLED_FILE = os.path.join(OUTPUT_DIR, "enabled.txt")
FAILED_FILE = os.path.join(OUTPUT_DIR, "failed.txt")
ERROR_REASON_FILE = os.path.join(OUTPUT_DIR, "error_reason.txt")
LOG_FILE = os.path.join(OUTPUT_DIR, "run.log")
SHOTS_DIR = os.path.join(ROOT, "debug_pw")

BUNDLE_ROOT = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else ROOT
BUNDLED_PLAYWRIGHT = os.path.join(BUNDLE_ROOT, "ms-playwright")
if os.path.isdir(BUNDLED_PLAYWRIGHT):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", BUNDLED_PLAYWRIGHT)

import requests
from requests.exceptions import Timeout, ConnectionError as ReqConnErr, RequestException
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page

# ── OAuth constants ───────────────────────────────────────────────────────────
CLIENT_ID    = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"  # Thunderbird
REDIRECT_URI = "https://localhost"
SCOPE_GRAPH = (
    "openid email offline_access "
    "https://outlook.office.com/IMAP.AccessAsUser.All "
    "https://outlook.office.com/POP.AccessAsUser.All "
    "https://outlook.office.com/SMTP.Send"
)
SCOPE_SMTP_ONLY = "https://outlook.office.com/SMTP.Send offline_access"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TIMEOUT     = (5, 15)
MAX_RETRIES = 2

# ── Playwright + smvmail ──────────────────────────────────────────────────────
SMVMAIL_API = "https://smvmail.com/api/email"
# Idea 6: giảm timeout default từ 45s → 15s (fail-fast trên error path).
DEFAULT_TIMEOUT = 15000
LOGIN_GOTO_TIMEOUT = 30000
LOGIN_GOTO_RETRIES = 2

# Idea 1: tắt screenshot trong production (đổi True để debug).
DEBUG_SHOTS = False

# Idea 3 mức 2: domain telemetry CHỈ chứa các domain SAFE (không ảnh hưởng auth/SSO).
# Đã bỏ: .bing.com, ms-sso.copilot.*, clientstate, appsforoffice — có thể cần cho auth.
TELEMETRY_BLOCKLIST = [
    "onecollector.microsoft.com",
    "nexus.officeapps.live.com",
    "browser.events.data.microsoft.com",
    "events.data.microsoft.com",
    "tile-service.weather.microsoft.com",
    "feedback-rfm.microsoft.com",
    "wcpstatic.microsoft.com",
]

# Idea 5: Chrome launch flags tối ưu tốc độ.
CHROME_FLAGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=Translate,AcceptCHFrame",
]

write_lock = Lock()
LOG_SINK = None


def _emit_log(line):
    sink = LOG_SINK
    if sink is None:
        return
    try:
        sink(str(line))
    except Exception:
        pass


def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except Exception:
        pass
    try:
        line = kwargs.get("sep", " ").join(str(a) for a in args)
        if line:
            _emit_log(line)
    except Exception:
        pass

# Idea 3: thread-local Playwright + browser. Mỗi worker thread giữ 1 browser dùng chung
# cho mọi account → save ~2-3s/account launch overhead.
# Browser KHÔNG bind proxy (proxy set ở context-level), nên share được giữa các account/proxy.
_thread_state = thread_local()
_browsers_to_cleanup = []  # collect tất cả browser instances để cleanup atexit
_cleanup_lock = Lock()


def _get_thread_browser():
    """Lấy hoặc tạo browser instance cho thread hiện tại."""
    if not hasattr(_thread_state, "browser") or _thread_state.browser is None:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True, args=CHROME_FLAGS)
        _thread_state.pw = pw
        _thread_state.browser = browser
        with _cleanup_lock:
            _browsers_to_cleanup.append((pw, browser))
    return _thread_state.browser


def _cleanup_all_browsers():
    """Đóng tất cả browser/playwright instances khi process exit."""
    with _cleanup_lock:
        for pw, browser in _browsers_to_cleanup:
            try: browser.close()
            except: pass
            try: pw.stop()
            except: pass
        _browsers_to_cleanup.clear()


atexit.register(_cleanup_all_browsers)


# ─────────────────────────────────────────────────────────────────────────────
#  PROXY HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _fmt_proxy(p: str) -> str | None:
    """Parse colon-separated proxy: host:port hoặc host:port:user:pass."""
    parts = p.split(":", 3)
    if len(parts) == 2:   return f"http://{parts[0]}:{parts[1]}"
    if len(parts) >= 4:   return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    return None


def _normalize_proxy(p) -> str | None:
    """None / "" / dict Playwright / str URL / str colon-separated → URL string cho requests."""
    if not p:
        return None
    if isinstance(p, dict):
        server = p.get("server", "")
        if not server: return None
        user = p.get("username") or ""
        pwd = p.get("password") or ""
        if user:
            if "://" in server:
                scheme, rest = server.split("://", 1)
                return f"{scheme}://{user}:{pwd}@{rest}"
            return f"http://{user}:{pwd}@{server}"
        return server if "://" in server else f"http://{server}"
    if not isinstance(p, str):
        return None
    if "://" in p:
        return p
    return _fmt_proxy(p)


def parse_proxy(s: str):
    """Parse 1 dòng proxy.txt thành dict Playwright format."""
    s = s.strip()
    if not s: return None
    if "://" in s:
        scheme, rest = s.split("://", 1)
        if "@" in rest:
            cred, hostport = rest.split("@", 1)
            user, pwd = cred.split(":", 1) if ":" in cred else (cred, "")
            return {"server": f"{scheme}://{hostport}", "username": user, "password": pwd}
        return {"server": f"{scheme}://{rest}"}
    parts = s.split(":")
    if len(parts) == 2:
        return {"server": f"http://{parts[0]}:{parts[1]}"}
    if len(parts) == 4:
        host, port, user, pwd = parts
        return {"server": f"http://{host}:{port}", "username": user, "password": pwd}
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  OAUTH (HTTP, dùng để lấy SMTP token verify cuối)
# ─────────────────────────────────────────────────────────────────────────────
def refresh_for_scope(refresh_token: str, scope: str, proxy=None,
                      client_id: str | None = None) -> tuple[bool, str]:
    """Đổi refresh_token sang access_token với scope chỉ định.

    client_id: nếu None → dùng CLIENT_ID mặc định (Thunderbird).
    Truyền tham số thay vì mutate global → an toàn khi nhiều luồng chạy với
    client_id khác nhau (refresh_token bind với client_id đã issue).
    """
    proxies = None
    pu = _normalize_proxy(proxy)
    if pu: proxies = {"http": pu, "https": pu}
    cid = client_id or CLIENT_ID
    try:
        r = requests.post(
            TOKEN_URL,
            data=urlencode({
                "client_id": cid,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": scope,
                "redirect_uri": REDIRECT_URI,
            }),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT,
            proxies=proxies,
        )
        if r.status_code == 200:
            return True, r.json().get("access_token", "")
        try:
            desc = r.json().get("error_description", r.text[:200])
        except Exception:
            desc = r.text[:200]
        return False, f"refresh {r.status_code}: {desc}"
    except Exception as exc:
        return False, f"refresh exc: {exc}"


class GraphAuth:
    """OAuth2 authorization-code flow thuần HTTP với Outlook IMAP/POP/SMTP scopes."""

    def __init__(self, proxy=None):
        s = requests.Session()
        s.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        })
        pu = _normalize_proxy(proxy)
        if pu:
            s.proxies = {"http": pu, "https": pu}
        self.session = s

    def _get(self, url, **kw):
        kw.setdefault("timeout", TIMEOUT)
        for i in range(MAX_RETRIES):
            try:
                return self.session.get(url, **kw)
            except (Timeout, ReqConnErr, RequestException):
                if i == MAX_RETRIES - 1: raise
                time.sleep(0.5 * (i + 1))

    def _post(self, url, **kw):
        kw.setdefault("timeout", TIMEOUT)
        for i in range(MAX_RETRIES):
            try:
                return self.session.post(url, **kw)
            except (Timeout, ReqConnErr, RequestException):
                if i == MAX_RETRIES - 1: raise
                time.sleep(0.5 * (i + 1))

    @staticmethod
    def _ppft(html: str) -> str | None:
        m = re.search(r'name="PPFT"[\s\S]*?value=["\']([^"\']+)', html)
        if m: return m.group(1)
        idx = html.find("PPFT")
        if idx >= 0:
            for p in html[idx:idx + 400].split('"'):
                if len(p) > 50: return p.replace("\\", "")
        return None

    @staticmethod
    def _url_post(html: str) -> str:
        for pat in [
            r'urlPost[\'"]?\s*[:=]\s*[\'"]([^"\']+)',
            r'action="([^"]+ppsecure/post\.srf[^"]*)"',
            r'(https://login\.live\.com/ppsecure/post\.srf[^"\']*)',
        ]:
            m = re.search(pat, html)
            if m:
                u = m.group(1)
                return u if u.startswith("http") else "https://login.live.com" + u
        return "https://login.live.com/ppsecure/post.srf"

    @staticmethod
    def _parse_inputs(html: str) -> dict:
        out = {}
        for m in re.finditer(r'<input\b[^>]*>', html, re.I):
            tag = m.group(0)
            nm = re.search(r'\bname\s*=\s*["\']([^"\']+)["\']', tag, re.I)
            if not nm: continue
            vm = re.search(r'\bvalue\s*=\s*["\']([^"\']*)["\']', tag, re.I)
            out[nm.group(1)] = vm.group(1) if vm else ""
        return out

    @staticmethod
    def _extract_code(url_or_loc: str) -> str | None:
        if not url_or_loc: return None
        if "?code=" in url_or_loc or "&code=" in url_or_loc:
            try:
                return unquote(url_or_loc.split("code=", 1)[1].split("&", 1)[0])
            except Exception:
                return None
        return None

    def _exchange(self, code: str) -> tuple[bool, dict | str]:
        try:
            r = self._post(
                TOKEN_URL,
                data=urlencode({
                    "client_id": CLIENT_ID, "grant_type": "authorization_code",
                    "redirect_uri": REDIRECT_URI, "code": code, "scope": SCOPE_GRAPH,
                }),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code == 200:
                j = r.json()
                return True, {
                    "access_token":  j.get("access_token",  ""),
                    "refresh_token": j.get("refresh_token", ""),
                }
            try:
                desc = r.json().get("error_description", r.text[:200])
            except Exception:
                desc = r.text[:200]
            return False, f"Token exchange {r.status_code}: {desc}"
        except Exception as exc:
            return False, f"Token exchange error: {exc}"

    def _handle_proofs_skip(self, fmhf_body: str, post_url: str) -> str | None:
        try:
            am = (
                re.search(r'action="([^"]+)"[^>]*id="fmHF"', fmhf_body)
                or re.search(r'<form[^>]*id="fmHF"[^>]*action="([^"]+)"', fmhf_body)
            )
            if not am: return None
            proofs_url = am.group(1).replace("&amp;", "&")
            if not proofs_url.startswith("http"):
                proofs_url = urljoin(post_url, proofs_url)
            fmhf_data = self._parse_inputs(fmhf_body)
            proofs_page = self._post(
                proofs_url, data=urlencode(fmhf_data),
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "Referer": post_url},
                allow_redirects=True,
            )
            if not proofs_page or proofs_page.status_code != 200:
                return None
            skip_data = self._parse_inputs(proofs_page.text)
            skip_data["action"] = "Skip"
            skip_data[""] = ""
            skip_resp = self._post(
                proofs_url, data=urlencode(skip_data),
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "Referer": proofs_page.url},
                allow_redirects=False,
            )
            if not skip_resp: return None
            loc = skip_resp.headers.get("Location", "")
            code = self._extract_code(loc)
            if code: return code
            if loc and ("oauth20_authorize" in loc or "login.live.com" in loc
                        or "login.microsoftonline" in loc):
                follow = self._get(
                    loc, allow_redirects=True,
                    headers={"Referer": proofs_page.url, "Host": "login.live.com"},
                )
                if follow:
                    return self._extract_code(follow.url)
        except Exception:
            pass
        return None

    def _form_flow(self, html: str, url: str, depth: int = 0) -> str | None:
        if depth > 4: return None
        code = self._extract_code(url)
        if code: return code
        try:
            am = (
                re.search(r'action="([^"]+)"[^>]*id="fmHF"', html)
                or re.search(r'<form[^>]*id="fmHF"[^>]*action="([^"]+)"', html)
                or re.search(r'<form[^>]*action="([^"]+)"', html)
            )
            action = am.group(1).replace("&amp;", "&") if am else url
            if not action.startswith("http"):
                action = urljoin(url, action)
            if "proofs/Add" in action or "proofs/Verify" in action:
                return None
            payload = self._parse_inputs(html)
            if "KMSI" in html:
                payload.update({"LoginOptions": "3", "KMSI": "1"})
            r = self._post(
                action, data=urlencode(payload),
                headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": url},
                allow_redirects=True,
            )
            if r is None: return None
            code = self._extract_code(r.url)
            if code: return code
            for h in r.history:
                code = self._extract_code(h.headers.get("Location", "")) or self._extract_code(h.url)
                if code: return code
            if "<form" in r.text and len(r.text) != len(html):
                return self._form_flow(r.text, r.url, depth + 1)
        except Exception:
            pass
        return None

    def _consent_flow(self, html: str, url: str, depth: int = 0) -> str | None:
        if depth > 3: return None
        try:
            am = (
                re.search(r'<form[^>]*action\s*=\s*["\']([^"\']*Consent/Update[^"\']*)["\']', html, re.I)
                or re.search(r'<form[^>]*action\s*=\s*["\']([^"\']*[Kk]msi[^"\']*)["\']', html)
                or re.search(r'<form[^>]*action\s*=\s*["\']([^"\']*oauth2[^"\']*)["\']', html, re.I)
                or re.search(r'<form[^>]*action\s*=\s*["\']([^"\']+)["\']', html, re.I)
            )
            if not am: return None
            action = am.group(1).replace("&amp;", "&")
            if not action.startswith("http"):
                action = urljoin(url, action)
            payload = self._parse_inputs(html)
            btn_found = False
            for m in re.finditer(
                r'<(?:input|button)\b[^>]*\btype\s*=\s*["\']submit["\'][^>]*>', html, re.I
            ):
                tag = m.group(0)
                nm = re.search(r'\bname\s*=\s*["\']([^"\']+)["\']', tag, re.I)
                vm = re.search(r'\bvalue\s*=\s*["\']([^"\']*)["\']', tag, re.I)
                if not nm or not vm: continue
                v = vm.group(1).lower()
                if any(k in v for k in ("yes", "accept", "allow", "continue", "đồng", "chấp")):
                    payload[nm.group(1)] = vm.group(1)
                    btn_found = True
                    break
            if not btn_found:
                payload.setdefault("ucaction", "Yes")
                payload.setdefault("decision", "allow")
                payload.setdefault("acceptOnly", "1")
            if "KMSI" in html or "kmsi" in action.lower():
                payload.setdefault("LoginOptions", "3")
                payload.setdefault("KMSI", "1")
            r = self._post(
                action, data=urlencode(payload),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": url,
                    "Origin": "https://login.microsoftonline.com",
                },
                allow_redirects=True,
            )
            if r is None: return None
            code = self._extract_code(r.url)
            if code: return code
            for h in r.history:
                code = self._extract_code(h.url) or self._extract_code(h.headers.get("Location", ""))
                if code: return code
            if "<form" in r.text:
                if "Consent/Update" in r.text or "Let this app access" in r.text:
                    nxt = self._consent_flow(r.text, r.url, depth + 1)
                    if nxt: return nxt
                return self._form_flow(r.text, r.url)
        except Exception:
            pass
        return None

    def run(self, email: str, password: str) -> tuple[bool, dict | str]:
        try:
            oauth_url = (
                f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
                f"?response_type=code&client_id={CLIENT_ID}"
                f"&redirect_uri={REDIRECT_URI}"
                f"&scope={quote(SCOPE_GRAPH)}"
                f"&login_hint={quote(email)}&sso_reload=true"
            )
            try:
                r = self._get(oauth_url, allow_redirects=True)
            except Exception as exc:
                return False, f"Network error: {exc}"
            if r.status_code == 429: return False, "Rate Limit (429)"

            page, page_url = r.text, r.url
            if "Access Denied" in page: return False, "IP Blocked/Access Denied"
            if '"IfExistsResult":1' in page: return False, "Email không tồn tại"

            login_type = "11"
            sw = (
                re.search(r'id="idA_PWD_SwitchToPassword"[^>]*href="([^"]+)"', page)
                or re.search(r'href="([^"]+)"[^>]*>[^<]*(?:Use my password|Sử dụng mật khẩu)', page)
            )
            if sw:
                try:
                    sr = self._get(sw.group(1).replace("&amp;", "&"), allow_redirects=True)
                    if sr: page, page_url, login_type = sr.text, sr.url, "22"
                except Exception: pass

            ppft     = self._ppft(page)
            url_post = self._url_post(page)
            if not ppft: return False, "Lỗi lấy PPFT"

            login_data = (
                f"ps=2&psRNGCDefaultType=&psRNGCEntropy=&psRNGCSLK=&canary=&ctx=&hpgrequestid=&"
                f"PPFT={quote(ppft, safe='')}&PPSX=Passp&NewUser=1&FoundMSAs=&fspost=0&"
                f"i21=0&CookieDisclosure=0&IsFidoSupported=1&isSignupPost=0&isRecoveryAttemptPost=0&i13=1&"
                f"login={quote(email, safe='')}&loginfmt={quote(email, safe='')}"
                f"&type={login_type}&LoginOptions={'3' if login_type=='22' else '1'}"
                f"&lrt=&lrtPartition=&hisRegion=&hisScaleUnit=&passwd={quote(password, safe='')}"
            )
            try:
                r = self._post(
                    url_post, data=login_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                             "Referer": page_url, "Origin": "https://login.live.com"},
                    allow_redirects=False,
                )
            except Exception as exc:
                return False, f"Network error (POST pass): {exc}"
            if r.status_code == 429: return False, "Rate Limit (429)"

            body = r.text
            loc  = r.headers.get("Location", "")
            bl   = body.lower()

            if body:
                if "incorrect password" in bl or "aadsts50126" in bl or "80041012" in body:
                    return False, "Sai mật khẩu"
                if "aadsts50034" in bl or "aadsts50056" in bl:
                    return False, "Tài khoản không tồn tại"
                if "aadsts50053" in bl or "aadsts50057" in bl or "aadsts50055" in bl:
                    return False, "Tài khoản bị khóa"
                if "aadsts50079" in bl or "aadsts50076" in bl or "aadsts50074" in bl:
                    return False, "Cần xác minh (MFA)"
                if "too many requests" in bl:
                    return False, "Rate Limit (429)"
                if "help us secure your account" in bl or "xác minh danh tính" in bl:
                    return False, "Checkpoint/Verify Phone"

            code = self._extract_code(loc)
            if code: return self._exchange(code)

            if body and ("proofs/Add" in body or "proofs/Verify" in body):
                code = self._handle_proofs_skip(body, r.url)
                if code: return self._exchange(code)
                return False, "PROOFS_CLEARED"

            if body and ("Let this app access your info" in body or "Consent/Update" in body):
                code = self._consent_flow(body, r.url)
                if code: return self._exchange(code)
                return False, "CONSENT: không resolve được"

            if body and ('id="fmHF"' in body or "fmHF" in body):
                am = (re.search(r'action="([^"]+)"[^>]*id="fmHF"', body)
                      or re.search(r'<form[^>]*id="fmHF"[^>]*action="([^"]+)"', body))
                if am:
                    act = am.group(1)
                    if "Abuse" in act or "abuse" in act:
                        return False, "Tài khoản bị khóa (Abuse)"
                    if "proofs/Add" in act or "proofs/Verify" in act:
                        code = self._handle_proofs_skip(body, r.url)
                        if code: return self._exchange(code)
                        return False, "PROOFS_CLEARED"

            if body and ("KMSI" in body or 'id="fmHF"' in body or "sso_reload" in body):
                if "sso_reload" in body:
                    try:
                        rr = self._get(oauth_url, allow_redirects=True)
                        if rr:
                            code = self._extract_code(rr.url)
                            if code: return self._exchange(code)
                    except Exception: pass
                code = self._form_flow(body, r.url)
                if code: return self._exchange(code)
                return False, "KMSI/Form: không lấy được code"

            return False, "Login thất bại (unknown response)"

        except Exception as exc:
            return False, f"System error: {exc}"

    def close(self):
        try: self.session.close()
        except Exception: pass


def run_oauth(email: str, password: str, proxy=None) -> dict | None:
    """OAuth via GraphAuth, retry 2 lần với soft fail. Trả dict refresh_token / None."""
    for _ in range(2):
        auth = GraphAuth(proxy=proxy)
        try: ok, r = auth.run(email, password)
        finally: auth.close()
        if ok: return r
        if r in ("PROOFS_CLEARED", "CONSENT: không resolve được"):
            time.sleep(1.5); continue
        return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  SMTP VERIFY
# ─────────────────────────────────────────────────────────────────────────────
def test_smtp(email: str, token: str) -> tuple[int | None, str]:
    """AUTH XOAUTH2. Trả (code, msg). code 235 = OK."""
    auth = base64.b64encode(f"user={email}\x01auth=Bearer {token}\x01\x01".encode()).decode()
    for host in ("smtp-mail.outlook.com", "smtp.office365.com"):
        try:
            c = smtplib.SMTP(host, 587, timeout=15)
            c.ehlo(); c.starttls(); c.ehlo()
            code, msg = c.docmd("AUTH", f"XOAUTH2 {auth}")
            text = msg.decode(errors="replace") if isinstance(msg, bytes) else str(msg)
            try: c.quit()
            except: pass
            return code, text[:120]
        except Exception: continue
    return None, "all hosts failed"


def get_smtp_token(email: str, password: str, proxy=None) -> str | None:
    """OAuth → refresh_token → SMTP access_token."""
    r = run_oauth(email, password, proxy=proxy)
    if not r: return None
    ok, tok = refresh_for_scope(r["refresh_token"], SCOPE_SMTP_ONLY, proxy=proxy)
    if not ok: return None
    return tok


# ─────────────────────────────────────────────────────────────────────────────
#  PLAYWRIGHT UI HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def pw_log(line: str):
    print(line, flush=True)
    _emit_log(line)
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception: pass


def shot(page: Page, name: str):
    # Idea 1: bỏ chụp screenshot nếu DEBUG_SHOTS = False (production mode).
    if not DEBUG_SHOTS:
        return
    try:
        os.makedirs(SHOTS_DIR, exist_ok=True)
        p = os.path.join(SHOTS_DIR, f"{int(time.time()*1000)}_{name}.png")
        page.screenshot(path=p, full_page=True)
        pw_log(f"    [shot] {p}")
    except Exception as e:
        pw_log(f"    [shot fail] {e}")


def try_click(page: Page, selectors: list[str], timeout: int = 4000, label: str = "") -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            el.wait_for(state="visible", timeout=timeout)
            el.click(timeout=3000)
            if label: pw_log(f"    [click] {label}: {sel}")
            return True
        except Exception:
            continue
    return False


def safe_fill(page: Page, selectors: list[str], text: str, timeout: int = 8000) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            el.wait_for(state="visible", timeout=timeout)
            el.fill(text)
            return True
        except Exception:
            continue
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  SMVMAIL OTC
# ─────────────────────────────────────────────────────────────────────────────
def _parse_iso_ts(s: str) -> float:
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


CODE_RE = re.compile(r'(?:security code|verification code|access code)[:\s]*<?\b(\d{6,8})\b', re.I)
CODE_RE_FALLBACK = re.compile(r'\b(\d{6,7})\b')


def extract_code(doc: dict) -> str | None:
    body = doc.get("text") or doc.get("html") or ""
    if not isinstance(body, str): return None
    m = CODE_RE.search(body)
    if m: return m.group(1)
    m = CODE_RE_FALLBACK.search(body)
    if m: return m.group(1)
    return None


def poll_smvmail(email: str, since_ts: float, timeout: int = 180) -> str | None:
    """Poll smvmail.com API tới khi có mail mới sau since_ts."""
    deadline = time.time() + timeout
    accept_ts = since_ts - 30
    pw_log(f"    [smv] poll {email}  since={since_ts:.1f}  timeout={timeout}s")
    while time.time() < deadline:
        try:
            r = requests.get(SMVMAIL_API, params={"email": email, "page": 1},
                              timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            docs = r.json().get("data", {}).get("docs", []) if r.status_code == 200 else []
        except Exception:
            docs = []
        for d in docs:
            created_ts = _parse_iso_ts(d.get("createdAt", ""))
            if created_ts < accept_ts: continue
            code = extract_code(d)
            if code:
                pw_log(f"    [smv] found code {code}  at {d.get('createdAt')}")
                return code
        time.sleep(3)
    return None


def handle_verify_email_page(page: Page, recovery_email: str) -> str:
    """Khi gặp 'Verify your email': nhập recovery email, poll OTC, điền OTC."""
    pw_log(f"  [verify-email] điền full email: {recovery_email}")
    if not safe_fill(page, [
        'input[type="email"]', 'input[name="proofEmail"]',
        'input[autocomplete="off"]:visible', 'input[type="text"]:visible',
    ], recovery_email, timeout=5000):
        return "không thấy input field"
    shot(page, "verify_email_filled")

    send_ts = time.time()
    if not try_click(page, [
        'button:has-text("Send code")', '#idSIButton9', 'input[type="submit"]',
    ], timeout=5000, label="Send code"):
        return "không click được Send code"
    # Idea 8: thay sleep(3) bằng wait error message (mất 0s nếu không có error)
    try:
        err = page.locator('text=/doesn.?t match/i').first
        if err.is_visible(timeout=2000):
            err_text = err.text_content() or ""
            pw_log(f"  [verify-email] MS error: {err_text[:200]}")
            return f"email mismatch: {err_text[:120]}"
    except Exception: pass
    shot(page, "after_send_code")

    code = poll_smvmail(recovery_email, send_ts, timeout=150)
    if not code:
        return "smvmail no code"

    try:
        # Idea 9: giảm timeout 10s → 6s
        page.wait_for_selector('text=/Enter your code/i', timeout=6000)
    except PWTimeout:
        shot(page, "no_otc_page")
        return "không thấy 'Enter your code' page"

    pw_log(f"  [verify-email] điền OTC = {code}")

    single_sel = ('input[name="ProofConfirmation"], input[name="OTC"], '
                   'input[autocomplete="one-time-code"], '
                   'input[type="tel"][maxlength="6"], input[type="tel"][maxlength="7"], '
                   'input[type="text"][maxlength="6"], input[type="text"][maxlength="7"]')
    try:
        single = page.locator(single_sel).first
        single.wait_for(state="visible", timeout=2000)
        single.fill(code)
        pw_log("    [otc] điền vào single input")
    except Exception:
        try:
            boxes = page.locator('input[maxlength="1"]:visible')
            cnt = boxes.count()
            pw_log(f"    [otc] thấy {cnt} ô maxlength=1")
            if cnt >= len(code):
                boxes.nth(0).click()
                page.keyboard.type(code, delay=80)
                time.sleep(1)
                pw_log("    [otc] đã type qua keyboard")
            else:
                shot(page, "otc_box_count_wrong")
                return f"thấy {cnt} ô, cần >= {len(code)}"
        except Exception as e:
            shot(page, "otc_fill_fail")
            return f"không fill được OTC: {e}"

    shot(page, "otc_filled")
    # Idea 8: bỏ sleep(1.5) trước submit — click ngay
    try_click(page, [
        '#idSubmit_SAOTCC_Continue', '#idSIButton9',
        'input[type="submit"]', 'button:has-text("Verify")',
        'button:has-text("Next")', 'button:has-text("Submit")',
        'button[type="submit"]',
    ], timeout=3000, label="Submit OTC")
    # Idea 8: thay sleep(4) bằng wait_for_selector cho password page (giảm 3-4s)
    try:
        page.wait_for_selector(
            'input[name="passwd"], input[type="password"], '
            'text=/Verify your email/i',
            timeout=8000,
        )
    except PWTimeout: pass
    shot(page, "after_otc_submit")
    return "ok"


# ─────────────────────────────────────────────────────────────────────────────
#  PLAYWRIGHT LOGIN
# ─────────────────────────────────────────────────────────────────────────────
def _has_token(captured) -> bool:
    """Idea 4: helper check captured token + anchor — early exit khi sniff sớm."""
    return bool(captured and captured.get("token") and captured.get("anchor"))


def goto_with_retry(page: Page, url: str, *, wait_until: str, timeout: int, retries: int,
                    label: str) -> tuple[bool, str | None]:
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
            return True, None
        except PWTimeout as e:
            last_error = f"timeout after {timeout // 1000}s"
            pw_log(f"  [{label}] timeout attempt {attempt}/{retries}")
        except Exception as e:
            last_error = str(e).splitlines()[0]
            pw_log(f"  [{label}] error attempt {attempt}/{retries}: {last_error}")
            if "ERR_PROXY_CONNECTION_FAILED" in str(e):
                break
        if attempt < retries:
            time.sleep(1)
    return False, last_error


def login_outlook(page: Page, email: str, password: str, recovery_email: str,
                   captured: dict | None = None) -> str:
    """Login MSA. Handle verify-email + password + KMSI.
    Idea 4: nếu captured đã có token+anchor giữa flow → return sớm 'ok'."""
    force_url = (
        "https://login.live.com/login.srf"
        f"?wa=wsignin1.0&rpsnv=13&ct={int(time.time())}"
        "&rver=7.0.6737.0&wp=MBI_SSL"
        f"&wreply={quote('https://outlook.live.com/owa/?nlp=1', safe='')}"
        "&lc=1033&id=292841&aadredir=1"
        f"&username={quote(email)}"
    )
    pw_log(f"  [login] goto force-login URL")
    # Idea 2 (mức 2): wait_until="commit" — return ngay khi nhận response, DOM build background.
    ok, err = goto_with_retry(
        page,
        force_url,
        wait_until="commit",
        timeout=LOGIN_GOTO_TIMEOUT,
        retries=LOGIN_GOTO_RETRIES,
        label="login goto",
    )
    if not ok:
        return f"login_goto_failed: {err}"

    # Idea 2: wait for email input thay vì sleep cứng 1.5s
    # Idea 10: state="attached" thay "visible" (không cần đợi layout hoàn tất)
    try:
        page.wait_for_selector('input[name="loginfmt"], input[type="email"]',
                               state="attached", timeout=6000)
    except PWTimeout: pass

    if safe_fill(page, ['input[name="loginfmt"]', 'input[type="email"]'], email, timeout=8000):
        try_click(page, ['#idSIButton9', 'input[type="submit"]'], label="email Next")
        # Idea 2 + 9: chờ password input HOẶC verify-email page (timeout 8s, attached)
        try:
            page.wait_for_selector(
                'input[name="passwd"], input[type="password"], '
                'text=/Verify your email/i',
                state="attached", timeout=8000,
            )
        except PWTimeout: pass
    shot(page, "after_email_step")

    for _ in range(3):
        # Idea 4: early exit nếu đã có token (Microsoft đã bắn service.svc)
        if _has_token(captured):
            pw_log("  [login] early exit — token captured")
            return "ok"

        try:
            t = page.locator('text=/Verify your email/i').first
            if t.is_visible(timeout=1000):
                pw_log("  [login] gặp 'Verify your email' — handle bằng smvmail")
                r = handle_verify_email_page(page, recovery_email)
                if r != "ok": return f"verify_email: {r}"
                # đợi page chuyển tiếp (verify xong → password page) — Idea 9, 10
                try:
                    page.wait_for_selector(
                        'input[name="passwd"], input[type="password"]',
                        state="attached", timeout=6000)
                except PWTimeout: pass
                continue
        except Exception: pass

        if page.locator('input[name="passwd"], input[type="password"]').first.is_visible():
            pw_log("  [login] gặp password page")
            page.fill('input[name="passwd"], input[type="password"]', password)
            clicked = try_click(page, [
                '#idSIButton9', 'button[type="submit"]',
                'button:has-text("Sign in")', 'button:has-text("Next")',
                'input[type="submit"]',
            ], label="Sign in")
            if not clicked:
                try: page.locator('input[name="passwd"], input[type="password"]').first.press("Enter")
                except Exception: pass
            # Idea 2 + 9 + 10: chờ KMSI page hoặc outlook URL (timeout 8s, attached)
            try:
                page.wait_for_selector(
                    '#idSIButton9, #iShowSkip, button:has-text("Yes"), '
                    'a:has-text("Skip"), a:has-text("Not now")',
                    state="attached", timeout=8000,
                )
            except PWTimeout:
                # Có thể đã redirect outlook luôn rồi
                pass
            shot(page, "after_pwd")
            continue

        if "outlook.live.com" in page.url.lower():
            break
        break

    # KMSI / Skip loop — vẫn poll nhưng check token để early exit
    for i in range(8):
        # Idea 4: early exit
        if _has_token(captured):
            pw_log("  [login] early exit — token captured trong KMSI loop")
            return "ok"

        url = page.url.lower()
        if "outlook.live.com" in url and "login" not in url:
            pw_log(f"  [login] đã vào outlook.live.com")
            break
        if try_click(page, ['#idSIButton9', 'input[value="Yes"]',
                            'button:has-text("Yes")'], timeout=1500,
                     label=f"KMSI #{i}"):
            time.sleep(0.2)  # Idea 6: giảm 0.5 → 0.2s
            continue
        if try_click(page, ['#iShowSkip', 'a:has-text("Skip for now")',
                            'button:has-text("Skip")', 'a:has-text("Not now")'],
                     timeout=1500, label=f"Skip #{i}"):
            time.sleep(0.2)  # Idea 6: giảm 0.5 → 0.2s
            continue
        break

    # Idea 4: nếu đã có token, không cần đợi outlook URL nữa
    if _has_token(captured):
        return "ok"

    try:
        page.wait_for_url("**outlook.live.com**", timeout=12000)
    except PWTimeout:
        # Có thể vẫn OK nếu token đã capture
        if _has_token(captured):
            return "ok"
        return f"không vào outlook.live.com (url={page.url})"
    return "ok"


# ─────────────────────────────────────────────────────────────────────────────
#  ORCHESTRATOR — flow mới: KHÔNG baseline check
# ─────────────────────────────────────────────────────────────────────────────
def _log(m):
    line = f"  [v3] {m}"
    print(line, flush=True)
    _emit_log(line)
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def unlock_account(email: str, password: str, recovery_email: str, proxy=None) -> str:
    """
    Flow:
      1. Mở Playwright + login Outlook
      2. Vào inbox + sniff token
      3. Inject fetch SetConsumerMailbox
      4. Đóng browser
      5. OAuth lấy SMTP token
      6. Test SMTP → unlocked | api_failed_<status> | still_blocked | smtp_token_failed
    """
    safe_print(f"\n{'='*80}")
    safe_print(f"  Unlock SMTP for {email}")
    safe_print('='*80)

    captured = {"token": None, "anchor": None}

    def on_request(req):
        if "service.svc" in req.url and "action=" in req.url:
            h = req.headers
            if "authorization" in h and "MSAuth1.0" in h["authorization"]:
                captured["token"] = h["authorization"]
            if "x-anchormailbox" in h:
                captured["anchor"] = h["x-anchormailbox"]

    api_status = None
    api_was_successful = False  # P1: flag để skip SMTP verify nếu API trả WasSuccessful=true

    # Idea 3: dùng browser dùng chung cho thread (tránh launch Chromium mỗi account).
    # Proxy set ở context-level, không phải browser-level.
    browser = _get_thread_browser()
    ctx_kwargs = {
        "user_agent": UA,
        # Idea 5 (mức 2): viewport nhỏ hơn — Chromium layout nhẹ hơn.
        "viewport": {"width": 800, "height": 600},
        "locale": "en-US",
    }
    if proxy:
        ctx_kwargs["proxy"] = proxy
    ctx = browser.new_context(**ctx_kwargs)

    # P2: bỏ tải hình ảnh + font (KHÔNG block CSS vì có thể chứa preload chain).
    ctx.route("**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,otf,ico,mp4,mp3}",
              lambda r: r.abort())

    # Idea 3 (mức 2): block telemetry/tracking domains không cần cho login.
    def _block_telemetry(route):
        url = route.request.url.lower()
        if any(b in url for b in TELEMETRY_BLOCKLIST):
            return route.abort()
        return route.continue_()
    ctx.route("**/*", _block_telemetry)

    # Idea 1 (mức 2): disable CSS animations/transitions trên tất cả page.
    ctx.add_init_script("""
        (() => {
            const style = document.createElement('style');
            style.textContent = '*, *::before, *::after { ' +
                'animation: none !important; ' +
                'transition: none !important; ' +
                'animation-duration: 0s !important; ' +
                'transition-duration: 0s !important; }';
            (document.head || document.documentElement).appendChild(style);
        })();
    """)

    page = ctx.new_page()
    page.set_default_timeout(DEFAULT_TIMEOUT)  # Idea 6: 15s thay 45s
    page.on("request", on_request)

    try:
        # Idea 4: truyền captured vào để login_outlook có thể early exit
        r = login_outlook(page, email, password, recovery_email, captured=captured)
        _log(f"login: {r}")
        if r != "ok":
            return "login_failed"

        # Idea 4: nếu token đã sniff trong login_outlook → skip goto inbox
        if captured["token"] and captured["anchor"]:
            _log("token đã capture trong login flow — skip goto inbox")
        else:
            _log("goto /mail/0/ (inbox only)")
            try:
                # GIỮ "domcontentloaded" cho outlook.live.com (an toàn — đảm bảo JS init).
                page.goto("https://outlook.live.com/mail/0/",
                          wait_until="domcontentloaded", timeout=20000)
            except Exception: pass
            # P3: poll thay sleep cứng. Tối đa 12s, exit sớm nếu đã có token + anchor.
            deadline = time.time() + 12
            while time.time() < deadline:
                if captured["token"] and captured["anchor"]:
                    break
                time.sleep(0.3)

        if not captured["token"]:
            _log("no token captured")
            return "no_token"

        _log(f"token: {captured['token'][:40]}...")
        _log(f"anchor: {captured['anchor']}")

        body = {
            "__type": "SetConsumerMailboxRequest:#Exchange",
            "Header": {
                "__type": "JsonRequestHeaders:#Exchange",
                "RequestServerVersion": "V2018_01_08",
            },
            "Options": {
                "PopEnabled": True,
                "PopMessageDeleteEnabled": False,
                "ImapEnabled": True,
                "SmtpClientAuthenticationDisabled": False,
            },
        }
        args = {
            "body_str": json.dumps(body),
            "token": captured["token"],
            "anchor": captured["anchor"] or "",
        }
        _log("inject fetch() qua page.evaluate")
        result = page.evaluate("""
            async (args) => {
                try {
                    const url = 'https://outlook.live.com/owa/0/service.svc?action=SetConsumerMailbox&app=Mail&n=99';
                    const resp = await fetch(url, {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'Authorization': args.token,
                            'Content-Type': 'application/json; charset=utf-8',
                            'Action': 'SetConsumerMailbox',
                            'x-anchormailbox': args.anchor,
                            'x-owa-urlpostdata': encodeURIComponent(args.body_str),
                        },
                        body: '',
                    });
                    const text = await resp.text();
                    return { status: resp.status, body: text.slice(0, 800) };
                } catch (e) { return { error: e.toString() }; }
            }
        """, args)
        if "error" in result:
            _log(f"fetch JS error: {result['error']}")
            api_status = "api_error"
        else:
            api_status = result.get("status")
            body_str = (result.get("body") or "")
            _log(f"fetch result: status={api_status} body={body_str[:200]}")
            # P1: nếu API trả 200 + WasSuccessful:true thì tin Microsoft, không verify SMTP.
            if api_status == 200 and '"WasSuccessful":true' in body_str.replace(" ", ""):
                api_was_successful = True
    finally:
        # Idea 3: chỉ close context (browser dùng chung, không close).
        try: ctx.close()
        except: pass

    if api_status == "api_error":
        return "api_error"

    # P1: fast path — Microsoft đã xác nhận success, không cần test SMTP.
    if api_was_successful:
        safe_print(f"  SMTP UNLOCKED (fast path via WasSuccessful=true)")
        return "unlocked"

    # Fallback path: API không trả WasSuccessful → verify SMTP để chắc chắn.
    time.sleep(3)
    tok = get_smtp_token(email, password, proxy=proxy)
    if not tok:
        _log("SMTP token failed")
        return "smtp_token_failed"

    c, m = test_smtp(email, tok)
    _log(f"SMTP: {c}: {m[:80]}")
    if c == 235:
        safe_print(f"  SMTP UNLOCKED!")
        return "unlocked"

    if isinstance(api_status, int) and api_status not in (200, 204):
        return f"api_failed_{api_status}"
    return "still_blocked"


# ─────────────────────────────────────────────────────────────────────────────
#  RUNNER — input/output + thread pool
# ─────────────────────────────────────────────────────────────────────────────
def load_accounts():
    """Load accounts. Support these formats per line:
       email|password
       email|password|recovery_email
       email|password|refresh_token|client_id
    For the 4-column token format, refresh_token/client_id are ignored.
    If `recovery_email` is absent, fall back to auto-generated smvmail address.
    Returns list of tuples: (email, password, recovery_email)
    """
    rows = []
    with open(INPUT_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                email = parts[0]
                password = parts[1]
                if len(parts) == 3 and parts[2]:
                    rec = parts[2]
                else:
                    # default fallback used previously
                    rec = email.split("@")[0] + "@smvmail.com"
                rows.append((email, password, rec))
    return rows


def load_proxies():
    if not os.path.exists(PROXY_FILE):
        safe_print(f"Proxy file: {PROXY_FILE} (not found)")
        return []
    proxies = []
    invalid = 0
    with open(PROXY_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            p = parse_proxy(line)
            if p:
                proxies.append(p)
            else:
                invalid += 1
    safe_print(f"Proxy file: {PROXY_FILE} ({len(proxies)} valid, {invalid} invalid)")
    return proxies


def append_line(path, line):
    with write_lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def process(item):
    # item: (idx, total, email, password, recovery_email, proxy)
    idx, total, email, password, recovery_email, proxy = item
    rec = recovery_email
    proxy_label = proxy["server"] if proxy else "no-proxy"
    prefix = f"[{idx}/{total}] {email}  ({proxy_label})"
    safe_print(f"{prefix} ... starting", flush=True)
    wrote_reason = False
    try:
        result = unlock_account(email, password, rec, proxy=proxy)
    except Exception as e:
        result = f"exception: {type(e).__name__}: {e}"
        if "ERR_PROXY_CONNECTION_FAILED" in str(e):
            result = "proxy_connection_failed: proxy die/sai host-port/user-pass hoặc bị mạng chặn"
        append_line(ERROR_REASON_FILE, f"{email}|{result}")
        wrote_reason = True
        _log(f"{email}: {result}")

    if result == "unlocked":
        # SMTP đã bật xong. Giờ lấy refresh_token để ghi file.
        # Logic: thử lấy refresh tối đa 2 lần. KHÔNG đợi giữa các lần.
        _log("lấy refresh_token (lần 1)...")
        oauth = run_oauth(email, password, proxy=proxy)

        if not (oauth and oauth.get("refresh_token")):
            # Lần 1 fail → retry NGAY (không đợi)
            _log("lần 1 fail, retry lấy refresh_token (lần 2)...")
            oauth = run_oauth(email, password, proxy=proxy)

        if oauth and oauth.get("refresh_token"):
            fresh_refresh = oauth["refresh_token"]
            append_line(ENABLED_FILE, f"{email}|{password}|{fresh_refresh}|{CLIENT_ID}")
            safe_print(f"✅ {prefix} -> {result} (refresh ok)", flush=True)
            return True
        else:
            # Cả 2 lần đều fail → ghi failed.txt
            append_line(FAILED_FILE, f"{email}|{password}")
            safe_print(f"❌ {prefix} -> {result} nhung refresh fail 2 lan", flush=True)
            return False
    else:
        # Failed format: KHÔNG có lý do (theo yêu cầu user)
        append_line(FAILED_FILE, f"{email}|{password}")
        if not wrote_reason:
            append_line(ERROR_REASON_FILE, f"{email}|{result}")
        safe_print(f"❌ {prefix} -> {result}", flush=True)
        return False


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for f in (ENABLED_FILE, FAILED_FILE, ERROR_REASON_FILE, LOG_FILE):
        if os.path.exists(f): os.remove(f)

    accounts = load_accounts()
    if not accounts:
        print("❌ input.txt rỗng hoặc không hợp lệ. Định dạng: email|password hoặc email|password|refresh_token|client_id")
        return

    proxies = load_proxies()
    print(f"\n📋 Đã load {len(accounts)} accounts từ input.txt")
    if proxies:
        print(f"🌐 Đã load {len(proxies)} proxies từ proxy.txt")
    else:
        print(f"🌐 Không có proxy (chạy IP gốc)")
    print()

    # Cho user TỰ QUYẾT ĐỊNH số luồng. Có thể truyền qua argv: `python run.py 8`.
    # Nếu không truyền argv, sẽ hỏi interactive như trước.
    # Mô hình SHARED PROXY: tất cả workers chia sẻ proxy có sẵn (round-robin).
    # Chỉ chạy IP gốc khi KHÔNG có proxy nào.
    workers = None
    # try parse first CLI arg as workers
    if len(sys.argv) >= 2:
        try:
            workers = int(sys.argv[1])
            if workers < 1:
                workers = None
        except Exception:
            workers = None

    while True:
        if workers is None:
            raw = input(f"Nhập số luồng muốn chạy: ").strip()
        else:
            raw = str(workers)
        if not raw:
            print("  Vui lòng nhập số (không có mặc định).")
            continue
        try:
            workers = int(raw)
        except ValueError:
            print("  Phải là số nguyên.")
            if len(sys.argv) >= 2:
                return
            continue
        if workers < 1:
            print("  Số luồng phải >= 1.")
            continue
        # Info messages về tỉ lệ proxy:worker (không block flow, không clamp)
        if proxies:
            if workers > len(proxies):
                ratio = workers / len(proxies)
                print(f"  ℹ {workers} luồng chia sẻ {len(proxies)} proxy "
                      f"(~{ratio:.1f} luồng/proxy, round-robin).")
            elif workers < len(proxies):
                print(f"  ℹ {workers} luồng — {workers} proxy đầu được dùng, "
                      f"{len(proxies) - workers} proxy còn lại bỏ qua.")
            else:
                print(f"  ℹ {workers} luồng = {len(proxies)} proxy (1:1 mapping).")
        else:
            print(f"  ℹ Không có proxy — tất cả {workers} luồng chạy IP gốc.")
        break

    # SHARED PROXY POOL — round-robin distribution.
    # Mỗi worker call get_proxy() → trả 1 proxy từ list theo round-robin.
    # Không "lock" proxy, không remove khỏi pool. Nhiều worker dùng cùng proxy OK.
    proxy_counter = [0]  # mutable container để có thể tăng trong closure
    proxy_pool_lock = Lock()

    def get_proxy():
        """Round-robin trả proxy SHARED. Trả None nếu không có proxy nào."""
        if not proxies:
            return None
        with proxy_pool_lock:
            idx = proxy_counter[0] % len(proxies)
            proxy_counter[0] += 1
        return proxies[idx]

    def return_proxy(p):
        """No-op — proxy không bị 'chiếm' nên không cần trả."""
        pass

    def worker(item_no_proxy):
        # item_no_proxy: (idx, total, email, password, recovery_email)
        idx, total, em, pw, rec = item_no_proxy
        p = get_proxy()
        try:
            return process((idx, total, em, pw, rec, p))
        finally:
            return_proxy(p)

    # Shared proxy info
    if proxies:
        proxy_info = f" qua {len(proxies)} proxy (shared, round-robin)"
    else:
        proxy_info = " (IP gốc — không có proxy)"
    print(f"\n🚀 Bắt đầu unlock {len(accounts)} accounts với {workers} luồng{proxy_info}...\n")
    print("=" * 80)
    t0 = time.time()
    ok_count = 0
    fail_count = 0
    items = [(i + 1, len(accounts), em, pw, rec) for i, (em, pw, rec) in enumerate(accounts)]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        # Idea 7: pre-warm browser cho mỗi worker thread trước khi xử lý account.
        # Browser sẽ launch song song trong khi main thread chuẩn bị items.
        print(f"⏳ Pre-warming {workers} browser instance(s)...")
        prewarm_t0 = time.time()
        prewarm_futures = [ex.submit(_get_thread_browser) for _ in range(workers)]
        for pf in prewarm_futures:
            try: pf.result(timeout=30)
            except Exception: pass
        print(f"   pre-warm xong sau {time.time() - prewarm_t0:.1f}s")

        futures = [ex.submit(worker, item) for item in items]
        for f in as_completed(futures):
            if f.result(): ok_count += 1
            else: fail_count += 1

    elapsed = time.time() - t0
    print()
    print("=" * 80)
    print(f"✅ Unlocked: {ok_count}/{len(accounts)}")
    print(f"❌ Failed:   {fail_count}/{len(accounts)}")
    print(f"⏱  Time:     {elapsed:.1f}s")
    print(f"📁 Output:   {OUTPUT_DIR}")
    print("=" * 80)
    input("\nNhấn Enter để thoát...")


if __name__ == "__main__":
    main()

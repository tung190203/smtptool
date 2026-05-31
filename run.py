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
    1. Sửa input.txt format: email|password|mkp (1 dòng 1 account)
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
UNLOCKED_FILE = os.path.join(OUTPUT_DIR, "unlocked.txt")
LIVE_FILE = os.path.join(OUTPUT_DIR, "live.txt")
DEAD_FILE = os.path.join(OUTPUT_DIR, "dead.txt")
LIVE_REASON_FILE = os.path.join(OUTPUT_DIR, "live_reason.txt")
LOG_FILE = os.path.join(OUTPUT_DIR, "run.log")
SHOTS_DIR = os.path.join(ROOT, "debug_pw")

BUNDLE_ROOT = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else ROOT
BUNDLED_PLAYWRIGHT = os.path.join(BUNDLE_ROOT, "ms-playwright")
if os.path.isdir(BUNDLED_PLAYWRIGHT):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", BUNDLED_PLAYWRIGHT)

import requests
from requests.exceptions import Timeout, ConnectionError as ReqConnErr, ProxyError, RequestException
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
APP_VERSION = "2026-05-31-mkp-domain-log"

# ── Playwright + smvmail ──────────────────────────────────────────────────────
SMVMAIL_API = "https://smvmail.com/api/email"
OTP_MAIL_DOMAINS = {
    "smvmail.com",
    "meomail.site",
    "totoson.shop",
    "fviainboxes.com",
    "moakt.com",
}
# Idea 6: giảm timeout default từ 45s → 15s (fail-fast trên error path).
DEFAULT_TIMEOUT = 15000
LOGIN_GOTO_TIMEOUT = 30000
LOGIN_GOTO_RETRIES = 2
INBOX_GOTO_TIMEOUT = 45000
TOKEN_WAIT_SECONDS = 45
OWA_INVALID_RETRY_WAIT_SECONDS = 60
OWA_INVALID_MAX_RETRIES = 2
PROXY_CHECK_URL = "https://login.live.com/"
PROXY_CHECK_TIMEOUT = (8, 15)
RETRY_NO_PROXY_ON_PROXY_CHECKPOINT = True

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
            user_q = quote(user, safe="")
            pwd_q = quote(pwd, safe="")
            if "://" in server:
                scheme, rest = server.split("://", 1)
                return f"{scheme}://{user_q}:{pwd_q}@{rest}"
            return f"http://{user_q}:{pwd_q}@{server}"
        return server if "://" in server else f"http://{server}"
    if not isinstance(p, str):
        return None
    if "://" in p:
        return p
    return _fmt_proxy(p)


_proxy_check_cache = {}
_proxy_check_lock = Lock()


def _proxy_key(proxy) -> str:
    return _normalize_proxy(proxy) or "no-proxy"


def is_proxy_checkpoint_result(result) -> bool:
    """Detect Microsoft risk/checkpoint paths that are commonly proxy-specific."""
    text = str(result or "").lower()
    return (
        "account.live.com/abuse" in text
        or "không vào outlook.live.com" in text
        or "khong vao outlook.live.com" in text
        or "không bắt được outlook mailbox context" in text
        or "khong bat duoc outlook mailbox context" in text
    )


def check_proxy(proxy) -> tuple[bool, str]:
    """Check proxy reachability before spending a browser session on it."""
    if not proxy:
        return True, "no-proxy"

    key = _proxy_key(proxy)
    with _proxy_check_lock:
        cached = _proxy_check_cache.get(key)
    if cached:
        return cached

    proxies = {"http": key, "https": key}
    try:
        r = requests.get(
            PROXY_CHECK_URL,
            headers={"User-Agent": UA},
            timeout=PROXY_CHECK_TIMEOUT,
            proxies=proxies,
            allow_redirects=False,
        )
        ok = r.status_code < 500
        result = (ok, f"proxy_check_http_{r.status_code}")
    except ProxyError as exc:
        result = (False, f"proxy_error: {str(exc).splitlines()[0][:180]}")
    except Timeout:
        result = (False, f"proxy_timeout: cannot reach {PROXY_CHECK_URL} in {PROXY_CHECK_TIMEOUT[1]}s")
    except ReqConnErr as exc:
        result = (False, f"proxy_connection_error: {str(exc).splitlines()[0][:180]}")
    except RequestException as exc:
        result = (False, f"proxy_request_error: {str(exc).splitlines()[0][:180]}")

    with _proxy_check_lock:
        _proxy_check_cache[key] = result
    return result


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
    parts = s.split(":", 3)
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


def check_live_account(email: str, password: str, recovery_email: str = "",
                       refresh_token: str = "", client_id: str = "",
                       proxy=None, use_refresh_token: bool = True) -> tuple[bool, str, str]:
    """Check account/SMTP readiness without changing mailbox settings.

    Returns: (is_live, reason, refresh_token_to_save)
    - 4-column input uses the supplied refresh_token/client_id first unless use_refresh_token is False.
    - 2/3-column input falls back to OAuth login, then SMTP XOAUTH2 verify.
    """
    if refresh_token and use_refresh_token:
        ok, tok = refresh_for_scope(
            refresh_token,
            SCOPE_SMTP_ONLY,
            proxy=proxy,
            client_id=client_id or CLIENT_ID,
        )
        if not ok:
            return False, tok, ""
        code, msg = test_smtp(email, tok)
        if code == 235:
            return True, "smtp_live", refresh_token
        return False, f"smtp_failed_{code}: {msg[:120]}", ""

    oauth = run_oauth(email, password, proxy=proxy)
    if not oauth or not oauth.get("refresh_token"):
        return False, "oauth_login_failed", ""

    fresh_refresh = oauth["refresh_token"]
    ok, tok = refresh_for_scope(fresh_refresh, SCOPE_SMTP_ONLY, proxy=proxy)
    if not ok:
        return False, tok, ""

    code, msg = test_smtp(email, tok)
    if code == 235:
        return True, "smtp_live", fresh_refresh
    return False, f"smtp_failed_{code}: {msg[:120]}", ""


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


def page_text_snippet(page: Page, limit: int = 220) -> str:
    try:
        text = page.locator("body").inner_text(timeout=2000)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit]
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
#  SMVMAIL OTC
# ─────────────────────────────────────────────────────────────────────────────
def _parse_iso_ts(s: str) -> float:
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


CODE_RE = re.compile(r'(?<!\d)(\d{6})(?!\d)')


def extract_code(doc: dict) -> str | None:
    body = " ".join(
        str(v or "") for v in (
            doc.get("subject"),
            doc.get("text"),
            doc.get("html"),
            doc.get("body"),
            doc.get("content"),
        )
    )
    if not isinstance(body, str): return None
    m = CODE_RE.search(body)
    if m: return m.group(1)
    return None


def otp_api_urls_for_email(email: str) -> list[str]:
    domain = email.rsplit("@", 1)[1].lower() if "@" in email else ""
    if domain in OTP_MAIL_DOMAINS:
        return [f"https://{domain}/api/email"]
    return [SMVMAIL_API]


def poll_smvmail(email: str, since_ts: float, timeout: int = 180) -> str | None:
    """Poll API mail free tới khi có mail mới sau since_ts.

    `email` là full mail khôi phục/mkp, ví dụ user@smvmail.com hoặc domain
    free tương ứng trong site lấy OTP.
    """
    deadline = time.time() + timeout
    accept_ts = since_ts - 30
    api_urls = otp_api_urls_for_email(email)
    pw_log(
        f"    [otp-mail] poll {email}  since={since_ts:.1f}  "
        f"timeout={timeout}s api={api_urls[0]}"
    )
    last_diag = 0
    last_seen = "no inbox docs"
    last_skip = ""
    while time.time() < deadline:
        for api_url in api_urls:
            try:
                r = requests.get(
                    api_url,
                    params={"email": email, "page": 1},
                    timeout=15,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                docs = r.json().get("data", {}).get("docs", []) if r.status_code == 200 else []
                if docs:
                    last_seen = f"{api_url} status={r.status_code} docs={len(docs)} latest={docs[0].get('createdAt', '')}"
                else:
                    last_seen = f"{api_url} status={r.status_code} docs=0"
            except Exception as exc:
                docs = []
                last_seen = f"{api_url} error={type(exc).__name__}: {str(exc)[:80]}"
            for d in docs:
                created_ts = _parse_iso_ts(d.get("createdAt", ""))
                if created_ts < accept_ts:
                    last_skip = f"latest mail old createdAt={d.get('createdAt', '')}"
                    continue
                code = extract_code(d)
                if code:
                    pw_log(f"    [otp-mail] found code {code} via {api_url} at {d.get('createdAt')}")
                    return code
                last_skip = (
                    f"mail found but no code subject={str(d.get('subject', ''))[:80]} "
                    f"createdAt={d.get('createdAt', '')}"
                )
        if time.time() - last_diag >= 30:
            diag = f"{last_seen}; {last_skip}" if last_skip else last_seen
            pw_log(f"    [otp-mail] waiting... {diag}")
            last_diag = time.time()
        time.sleep(3)
    diag = f"{last_seen}; {last_skip}" if last_skip else last_seen
    pw_log(f"    [otp-mail] timeout no code. last={diag}")
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

    try:
        page.wait_for_selector(
            'text=/Enter your code|doesn.?t match|didn.?t match|try again|too many|temporarily|can.?t send|cannot send/i',
            timeout=8000,
        )
    except PWTimeout:
        pass

    page_text = page_text_snippet(page)
    if re.search(r"doesn.?t match|didn.?t match", page_text, re.I):
        pw_log(f"  [verify-email] MS error: {page_text}")
        return f"email mismatch: {page_text[:120]}"
    if re.search(r"too many|temporarily|can.?t send|cannot send|try again", page_text, re.I):
        pw_log(f"  [verify-email] MS send-code error: {page_text}")
        return f"send_code_error: {page_text[:120]}"
    if page_text:
        pw_log(f"  [verify-email] after Send code page: {page_text[:160]}")
    shot(page, "after_send_code")

    code = poll_smvmail(recovery_email, send_ts, timeout=240)
    if not code:
        return "otp mail no code"

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
    try:
        page.wait_for_selector(
            'input[name="passwd"], input[type="password"], '
            'text=/Verify your email|incorrect|wrong|expired|try again|too many|temporarily|outlook.live.com/i',
            timeout=10000,
        )
    except PWTimeout: pass
    page_text = page_text_snippet(page)
    if re.search(r"incorrect|wrong|expired|try again|too many|temporarily", page_text, re.I):
        pw_log(f"  [verify-email] MS OTP error: {page_text}")
        return f"otp_submit_error: {page_text[:120]}"
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
                pw_log("  [login] gặp 'Verify your email' — handle bằng mail khôi phục")
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

    captured = {
        "token": None,
        "anchor": None,
        "canary": None,
        "client_version": None,
        "session_id": None,
        "tenant_id": None,
        "ms_cv": None,
    }

    def on_request(req):
        h = req.headers
        if "x-owa-canary" in h:
            captured["canary"] = h["x-owa-canary"]
        if "x-client-version" in h:
            captured["client_version"] = h["x-client-version"]
        if "x-owa-sessionid" in h:
            captured["session_id"] = h["x-owa-sessionid"]
        if "x-tenantid" in h:
            captured["tenant_id"] = h["x-tenantid"]
        if "ms-cv" in h:
            captured["ms_cv"] = h["ms-cv"]
        if "service.svc" in req.url and "action=" in req.url:
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
        def wait_for_mailbox_context(seconds: int) -> bool:
            deadline = time.time() + seconds
            while time.time() < deadline:
                if captured["anchor"] and (captured["canary"] or captured["token"]):
                    return True
                time.sleep(0.3)
            return bool(captured["anchor"] and (captured["canary"] or captured["token"]))

        def goto_inbox_and_wait_token(seconds: int, *, reload_page: bool = False,
                                      preserve_existing: bool = False) -> bool:
            previous_token = captured["token"]
            previous_anchor = captured["anchor"]
            previous_canary = captured["canary"]
            previous_client_version = captured["client_version"]
            previous_session_id = captured["session_id"]
            previous_tenant_id = captured["tenant_id"]
            previous_ms_cv = captured["ms_cv"]
            captured["token"] = None
            captured["anchor"] = None
            captured["canary"] = None
            captured["client_version"] = None
            captured["session_id"] = None
            captured["tenant_id"] = None
            captured["ms_cv"] = None
            action = "reload /mail/0/" if reload_page else "goto /mail/0/ (inbox only)"
            _log(action)
            nav_urls = ["https://outlook.live.com/mail/0/"]
            if reload_page:
                cache_bust = int(time.time() * 1000)
                nav_urls = [
                    f"https://outlook.live.com/owa/?nlp=1&path=/mail/inbox&cb={cache_bust}",
                    f"https://outlook.live.com/mail/0/?cb={cache_bust}",
                ]

            per_step_wait = max(6, seconds // (len(nav_urls) + (1 if reload_page else 0)))
            for nav_url in nav_urls:
                try:
                    page.goto(nav_url, wait_until="domcontentloaded", timeout=INBOX_GOTO_TIMEOUT)
                except Exception as exc:
                    _log(f"inbox navigation warning: {str(exc).splitlines()[0][:160]}")
                if wait_for_mailbox_context(per_step_wait):
                    return True

            if reload_page:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=INBOX_GOTO_TIMEOUT)
                except Exception as exc:
                    _log(f"inbox reload warning: {str(exc).splitlines()[0][:160]}")
                if wait_for_mailbox_context(per_step_wait):
                    return True

            if wait_for_mailbox_context(max(3, seconds - per_step_wait * len(nav_urls))):
                return True

            if preserve_existing and previous_anchor and (previous_canary or previous_token):
                captured["token"] = previous_token
                captured["anchor"] = previous_anchor
                captured["canary"] = previous_canary
                captured["client_version"] = previous_client_version
                captured["session_id"] = previous_session_id
                captured["tenant_id"] = previous_tenant_id
                captured["ms_cv"] = previous_ms_cv
                _log("Không bắt được OWA context mới; dùng lại context đã bắt được trong lúc login")
                return True
            return False

        def call_set_consumer_mailbox() -> dict:
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
                "token": captured["token"] or "",
                "anchor": captured["anchor"] or "",
                "canary": captured["canary"] or "",
                "client_version": captured["client_version"] or "",
                "session_id": captured["session_id"] or "",
                "tenant_id": captured["tenant_id"] or "",
                "ms_cv": captured["ms_cv"] or "",
            }
            _log("inject fetch() theo pattern OWA thật: x-owa-urlpostdata + body null")
            return page.evaluate("""
                async (args) => {
                    try {
                        const url = 'https://outlook.live.com/owa/service.svc?action=SetConsumerMailbox&app=Mail&n=99';
                        const headers = {
                            'Accept': '*/*',
                            'Content-Type': 'application/json; charset=utf-8',
                            'Action': 'SetConsumerMailbox',
                            'x-anchormailbox': args.anchor,
                            'x-owa-urlpostdata': encodeURIComponent(args.body_str),
                            'x-req-source': 'Mail',
                            'Prefer': 'IdType="ImmutableId"',
                        };
                        if (args.token) {
                            headers['Authorization'] = args.token;
                        }
                        if (args.canary) {
                            headers['X-OWA-Canary'] = args.canary;
                        }
                        if (args.client_version) {
                            headers['x-client-version'] = args.client_version;
                        }
                        if (args.session_id) {
                            headers['x-owa-sessionid'] = args.session_id;
                        }
                        if (args.tenant_id) {
                            headers['x-tenantid'] = args.tenant_id;
                        }
                        if (args.ms_cv) {
                            headers['ms-cv'] = args.ms_cv;
                        }
                        const resp = await fetch(url, {
                            method: 'POST',
                            credentials: 'include',
                            headers,
                            body: null,
                        });
                        const text = await resp.text();
                        return { status: resp.status, body: text.slice(0, 1200) };
                    } catch (e) { return { error: e.toString() }; }
                }
            """, args)

        # Idea 4: truyền captured vào để login_outlook có thể early exit
        r = login_outlook(page, email, password, recovery_email, captured=captured)
        _log(f"login: {r}")
        if r != "ok":
            return r

        # Always capture a fresh mailbox-context token from Outlook inbox.
        # Tokens sniffed during login can be too early and often produce 412/OwaInvalid.
        goto_inbox_and_wait_token(TOKEN_WAIT_SECONDS, preserve_existing=True)

        if not captured["anchor"]:
            _log("Không bắt được x-anchormailbox từ Outlook")
            return "Không bắt được Outlook mailbox context - Outlook chưa load đủ hoặc account bị checkpoint"

        if captured["token"]:
            _log(f"token: {captured['token'][:40]}...")
        _log(f"anchor: {captured['anchor']}")
        if captured["canary"]:
            _log(f"canary: {captured['canary'][:24]}...")
        else:
            _log("canary: không bắt được, vẫn thử bằng cookie/session hiện tại")

        result = call_set_consumer_mailbox()
        if "error" in result:
            _log(f"fetch JS error: {result['error']}")
            api_status = "api_error"
        else:
            api_status = result.get("status")
            body_str = (result.get("body") or "")
            _log(f"fetch result: status={api_status} body={body_str[:200]}")

            retry_no = 0
            while (api_status == 412 or "OwaInvalid" in body_str) and retry_no < OWA_INVALID_MAX_RETRIES:
                retry_no += 1
                _log(f"Microsoft từ chối API OWA 412/OwaInvalid; tải lại Outlook và thử lại {retry_no}/{OWA_INVALID_MAX_RETRIES}")
                if goto_inbox_and_wait_token(OWA_INVALID_RETRY_WAIT_SECONDS, reload_page=True):
                    if captured["token"]:
                        _log(f"fresh token: {captured['token'][:40]}...")
                    _log(f"fresh anchor: {captured['anchor']}")
                    if captured["canary"]:
                        _log(f"fresh canary: {captured['canary'][:24]}...")
                    else:
                        _log("fresh canary: không bắt được")
                    time.sleep(2)
                    result = call_set_consumer_mailbox()
                    if "error" in result:
                        _log(f"fetch retry JS error: {result['error']}")
                        api_status = "api_error"
                        body_str = ""
                        break
                    else:
                        api_status = result.get("status")
                        body_str = (result.get("body") or "")
                        _log(f"fetch retry result: status={api_status} body={body_str[:200]}")
                else:
                    _log("Không bắt được token Outlook mới sau khi tải lại; thử lại vòng tiếp theo nếu còn lượt")
                    continue

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
        if api_status == 412:
            _log("Kết luận: Microsoft vẫn từ chối bật SMTP qua OWA API (412/OwaInvalid). Có thể mailbox/account chưa sẵn sàng, bị checkpoint, hoặc cần login thủ công vào Outlook trước.")
            return "Microsoft từ chối bật SMTP qua OWA API (412/OwaInvalid) - thử login Outlook thủ công rồi chạy lại"
        return f"api_failed_{api_status}"
    return "still_blocked"


# ─────────────────────────────────────────────────────────────────────────────
#  RUNNER — input/output + thread pool
# ─────────────────────────────────────────────────────────────────────────────
def load_accounts():
    """Load accounts. Support these formats per line:
       email|password|mkp
       email|password|refresh_token|client_id
    For the 4-column token format, refresh_token/client_id are preserved.
    `mkp` is the full recovery email used to receive OTP.
    Returns list of tuples: (email, password, recovery_email, refresh_token, client_id)
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
                refresh_token = ""
                client_id = ""
                if len(parts) == 3 and parts[2]:
                    rec = parts[2]
                elif len(parts) >= 4:
                    rec = ""
                    refresh_token = parts[2]
                    client_id = parts[3]
                else:
                    safe_print(f"⚠️ Bỏ qua dòng thiếu mkp: {email}|***")
                    continue
                rows.append((email, password, rec, refresh_token, client_id))
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


def get_refresh_token_with_retry(email: str, password: str, proxy=None):
    attempts = [("proxy", proxy), ("proxy", proxy)]
    if proxy:
        attempts.append(("no-proxy", None))

    last_reason = "unknown"
    for attempt_no, (label, attempt_proxy) in enumerate(attempts, start=1):
        _log(f"lấy refresh_token ({label}, lần {attempt_no}/{len(attempts)})...")
        try:
            oauth = run_oauth(email, password, proxy=attempt_proxy)
        except Exception as exc:
            last_reason = f"{type(exc).__name__}: {str(exc)[:180]}"
            _log(f"refresh_token exception: {last_reason}")
            oauth = None

        if oauth and oauth.get("refresh_token"):
            return oauth["refresh_token"], label, None

        last_reason = "oauth_no_refresh_token"
        time.sleep(2)

    return None, None, last_reason


def process(item):
    # item: (idx, total, email, password, recovery_email, refresh_token, client_id, proxy)
    if len(item) >= 8:
        idx, total, email, password, recovery_email, input_refresh, input_client_id, proxy = item
    else:
        idx, total, email, password, recovery_email, proxy = item
        input_refresh = ""
        input_client_id = ""
    rec = recovery_email
    proxy_label = proxy["server"] if proxy else "no-proxy"
    prefix = f"[{idx}/{total}] {email}  ({proxy_label})"
    safe_print(f"{prefix} ... starting", flush=True)
    if rec:
        safe_print(f"  [input] mkp/recovery_email = {rec}", flush=True)
    elif input_refresh:
        safe_print(f"  [input] dùng refresh_token input, không có mkp", flush=True)
    wrote_reason = False
    try:
        proxy_ok, proxy_reason = check_proxy(proxy)
        if proxy:
            _log(f"{email}: proxy precheck {proxy_reason}")
        if not proxy_ok:
            result = proxy_reason
        else:
            result = unlock_account(email, password, rec, proxy=proxy)
            if proxy and RETRY_NO_PROXY_ON_PROXY_CHECKPOINT and is_proxy_checkpoint_result(result):
                _log(f"{email}: proxy bị Microsoft checkpoint/Abuse ({result}); retry 1 lần bằng IP gốc")
                no_proxy_result = unlock_account(email, password, rec, proxy=None)
                if no_proxy_result == "unlocked":
                    result = no_proxy_result
                else:
                    result = f"{result} | retry_no_proxy={no_proxy_result}"
    except Exception as e:
        result = f"exception: {type(e).__name__}: {e}"
        if "ERR_PROXY_CONNECTION_FAILED" in str(e):
            result = "proxy_connection_failed: proxy die/sai host-port/user-pass hoặc bị mạng chặn"
        elif "proxy_timeout" in str(e) or "proxy_error" in str(e) or "proxy_connection_error" in str(e):
            result = str(e)
        append_line(ERROR_REASON_FILE, f"{email}|{result}")
        wrote_reason = True
        _log(f"{email}: {result}")

    if result == "unlocked":
        append_line(UNLOCKED_FILE, f"{email}|{password}")

        # Nếu input đã có refresh_token thì ưu tiên dùng lại, tránh login OAuth thêm lần nữa.
        if input_refresh:
            ok, tok_or_reason = refresh_for_scope(
                input_refresh,
                SCOPE_SMTP_ONLY,
                proxy=proxy,
                client_id=input_client_id or CLIENT_ID,
            )
            if ok:
                append_line(ENABLED_FILE, f"{email}|{password}|{input_refresh}|{input_client_id or CLIENT_ID}")
                safe_print(f"✅ {prefix} -> {result} (dùng refresh_token có sẵn)", flush=True)
                return True
            _log(f"{email}: refresh_token input không dùng được: {tok_or_reason}")

        # SMTP đã bật xong. Giờ lấy refresh_token để ghi file enabled.txt.
        fresh_refresh, refresh_via, refresh_error = get_refresh_token_with_retry(email, password, proxy=proxy)
        if fresh_refresh:
            append_line(ENABLED_FILE, f"{email}|{password}|{fresh_refresh}|{CLIENT_ID}")
            safe_print(f"✅ {prefix} -> {result} (refresh ok via {refresh_via})", flush=True)
            return True
        else:
            reason = f"unlocked_but_refresh_failed: {refresh_error}"
            append_line(ERROR_REASON_FILE, f"{email}|{reason}")
            safe_print(f"✅ {prefix} -> {result} nhưng chưa lấy được refresh ({refresh_error})", flush=True)
            return True
    else:
        # Failed format: KHÔNG có lý do (theo yêu cầu user)
        append_line(FAILED_FILE, f"{email}|{password}")
        if not wrote_reason:
            append_line(ERROR_REASON_FILE, f"{email}|{result}")
        safe_print(f"❌ {prefix} -> {result}", flush=True)
        return False


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for f in (
        ENABLED_FILE,
        FAILED_FILE,
        ERROR_REASON_FILE,
        UNLOCKED_FILE,
        LIVE_FILE,
        DEAD_FILE,
        LIVE_REASON_FILE,
        LOG_FILE,
    ):
        if os.path.exists(f):
            os.remove(f)

    accounts = load_accounts()
    if not accounts:
        print("❌ input.txt rỗng hoặc không hợp lệ. Định dạng: email|password|mkp hoặc email|password|refresh_token|client_id")
        return

    proxies = load_proxies()
    print(f"🔖 Version: {APP_VERSION}")
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
        # Khi dùng proxy, không chạy nhiều phiên cùng 1 proxy tại cùng thời điểm.
        if proxies:
            if workers > len(proxies):
                print(f"  ℹ Có {len(proxies)} proxy nên giảm từ {workers} xuống {len(proxies)} luồng "
                      f"để tránh share proxy cùng lúc.")
                workers = len(proxies)
            elif workers < len(proxies):
                print(f"  ℹ {workers} luồng — {workers} proxy đầu được dùng, "
                      f"{len(proxies) - workers} proxy còn lại bỏ qua.")
            else:
                print(f"  ℹ {workers} luồng = {len(proxies)} proxy (không share proxy cùng lúc).")
        else:
            print(f"  ℹ Không có proxy — tất cả {workers} luồng chạy IP gốc.")
        break

    def worker(item):
        return process(item)

    # Shared proxy info
    if proxies:
        proxy_info = f" qua {len(proxies)} proxy (1 phiên/proxy, có retry IP gốc nếu bị Abuse)"
    else:
        proxy_info = " (IP gốc — không có proxy)"
    print(f"\n🚀 Bắt đầu unlock {len(accounts)} accounts với {workers} luồng{proxy_info}...\n")
    print("=" * 80)
    t0 = time.time()
    ok_count = 0
    fail_count = 0
    items = [
        (i + 1, len(accounts), em, pw, rec, refresh, client_id, proxies[i % len(proxies)] if proxies else None)
        for i, (em, pw, rec, refresh, client_id) in enumerate(accounts)
    ]

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

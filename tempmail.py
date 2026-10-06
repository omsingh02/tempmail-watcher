#!/usr/bin/env python3
"""
TempMail Watcher - Ultra-efficient on-demand temporary email & OTP/link auto-copy utility.
Zero external dependencies. Instant startup. Multi-provider support (GuerrillaMail + Mail.tm).
Runs on Linux, macOS, Windows, and WSL.

https://github.com/omsingh02/tempmail-watcher
"""

import sys
import os
import re
import json
import time
import html
import base64
import shutil
import secrets
import datetime
import platform
import argparse
import subprocess
import unicodedata
import urllib.request
import urllib.parse
import urllib.error
import http.client
import webbrowser
from typing import Optional, List, Dict, Tuple, Any

VERSION = "1.2.1"


def _default_cache_dir() -> str:
    """Per-user cache directory following each OS's convention."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        xdg = os.environ.get("XDG_CACHE_HOME", "")
        base = xdg if os.path.isabs(xdg) else os.path.expanduser("~/.cache")
    return os.path.join(base, "tempmail")


def _is_wsl() -> bool:
    return sys.platform.startswith("linux") and (
        "WSL_DISTRO_NAME" in os.environ or "microsoft" in platform.uname().release.lower()
    )


CACHE_DIR = _default_cache_dir()
SESSION_FILE = os.path.join(CACHE_DIR, "session.json")
SESSION_MAX_AGE = 3600          # Resume window in seconds, measured from inbox creation
EXIT_TIMEOUT = 124              # Same convention as coreutils `timeout`
MIN_INTERVAL = 1.0
AUTO_OPEN_MIN_SCORE = 70        # --open only fires for links with several verification signals
POLL_ERROR_WARN_AFTER = 3

# --- ANSI Colors & Terminal Formatting ---
class UI:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"

    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BG_CYAN = "\033[46m"
    BG_GREEN = "\033[42m"
    BG_BLUE = "\033[44m"

    @classmethod
    def disable(cls):
        for attr in dir(cls):
            if not attr.startswith("__") and isinstance(getattr(cls, attr), str):
                setattr(cls, attr, "")


def _enable_windows_ansi() -> bool:
    """Turn on ANSI escape processing in the Windows console (already on in Windows Terminal)."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


# https://no-color.org: any non-empty NO_COLOR disables colors
if not sys.stdout.isatty() or os.environ.get("NO_COLOR") or (sys.platform == "win32" and not _enable_windows_ansi()):
    UI.disable()


# --- Text Safety & Layout Helpers ---
# Control chars (ESC, CR/LF, C1 CSI, ...) and bidi overrides that could hijack or spoof terminal output
_UNSAFE_CHARS = re.compile(r'[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]')
_ANSI_SGR = re.compile(r'\x1b\[[0-9;]*m')


def sanitize_text(text: Any) -> str:
    """Make untrusted email text safe to print on a single line."""
    return " ".join(_UNSAFE_CHARS.sub(" ", str(text or "")).split())


def display_width(text: str) -> int:
    """Terminal column width, ignoring color codes and counting wide/emoji characters as 2."""
    plain = _ANSI_SGR.sub("", text)
    width = 0
    for i, ch in enumerate(plain):
        if ch == "\ufe0f":
            # Emoji presentation selector renders the preceding narrow symbol (e.g. ✉️) two columns wide
            if i > 0 and unicodedata.east_asian_width(plain[i - 1]) not in ("W", "F"):
                width += 1
            continue
        if ch in "\u200d\ufe0e" or unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def truncate_width(text: str, max_width: int) -> str:
    """Cut text to fit max_width terminal columns, ending with an ellipsis when shortened."""
    if display_width(text) <= max_width:
        return text
    if max_width <= 0:
        return ""
    out, used = "", 0
    for ch in text:
        w = display_width(ch)
        if used + w > max_width - 1:
            break
        out += ch
        used += w
    return out + "…"


# --- Clipboard & Notification Utilities ---
def _pipe_to(cmd: List[str], data: bytes, env: Optional[Dict[str, str]] = None) -> bool:
    """Feed data to a clipboard command; True only if it exits successfully."""
    try:
        # stdout must not be inherited: wl-copy/xclip fork a background server that would otherwise
        # hold our stdout open and hang callers like `EMAIL=$(tempmail -a)`
        result = subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=env, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def _win32_set_clipboard(text: str) -> bool:
    """Native Windows clipboard via the Win32 API: full Unicode, no helper process."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    data = text.encode("utf-16-le") + b"\x00\x00"

    # Another application may hold the clipboard for a moment
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True  # the clipboard owns the memory now
    finally:
        user32.CloseClipboard()


def copy_to_clipboard(text: str) -> Tuple[bool, str]:
    """Copy text to the system clipboard on Windows, macOS, Linux (Wayland/X11), and WSL."""
    if not text:
        return False, "empty"
    data = text.encode("utf-8")

    # Windows (native)
    if sys.platform == "win32":
        try:
            if _win32_set_clipboard(text):
                return True, "win32"
        except Exception:
            pass

    # macOS: force UTF-8 so non-ASCII text survives a C/POSIX locale
    if sys.platform == "darwin" and shutil.which("pbcopy"):
        if _pipe_to(["pbcopy"], data, env=dict(os.environ, LC_CTYPE="UTF-8")):
            return True, "pbcopy"

    # Wayland (wl-copy)
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        if _pipe_to(["wl-copy"], data):
            # Also copy to primary selection (middle-click paste); best effort
            _pipe_to(["wl-copy", "--primary"], data)
            return True, "wl-copy"

    # X11 (xclip / xsel), also covers XWayland
    if os.environ.get("DISPLAY"):
        if shutil.which("xclip") and _pipe_to(["xclip", "-selection", "clipboard"], data):
            return True, "xclip"
        if shutil.which("xsel") and _pipe_to(["xsel", "--clipboard", "--input"], data):
            return True, "xsel"

    # WSL (and Windows if the native API failed)
    if shutil.which("clip.exe") and _pipe_to(["clip.exe"], text.encode("utf-16le")):
        return True, "clip.exe"

    return False, "none"


# Windows toast via the built-in Windows PowerShell; the text arrives through environment
# variables and is XML-escaped, so untrusted subjects/senders never become code
_WINDOWS_TOAST_SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$title = [Security.SecurityElement]::Escape($env:TEMPMAIL_TITLE)
$body = [Security.SecurityElement]::Escape($env:TEMPMAIL_MESSAGE)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$body</text></binding></visual></toast>")
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def _windows_toast_command(title: str, message: str) -> Optional[Tuple[List[str], Dict[str, str]]]:
    """PowerShell command line and environment that show a Windows toast, or None without PowerShell."""
    exe = shutil.which("powershell.exe")
    if not exe:
        return None
    env = dict(os.environ, TEMPMAIL_TITLE=title, TEMPMAIL_MESSAGE=message)
    if sys.platform != "win32":
        # WSL only forwards variables listed in WSLENV to Windows processes
        env["WSLENV"] = ":".join(filter(None, [os.environ.get("WSLENV"), "TEMPMAIL_TITLE", "TEMPMAIL_MESSAGE"]))
    encoded = base64.b64encode(_WINDOWS_TOAST_SCRIPT.encode("utf-16-le")).decode("ascii")
    return [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], env


def _windows_toast(title: str, message: str) -> bool:
    command = _windows_toast_command(title, message)
    if not command:
        return False
    argv, env = command
    # Fire and forget: PowerShell takes about a second to start
    subprocess.Popen(
        argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    )
    return True


def send_notification(title: str, message: str, urgency: str = "normal") -> bool:
    """Send a desktop notification on Linux, macOS, Windows, or WSL if available."""
    if sys.platform == "win32" or (_is_wsl() and not shutil.which("notify-send")):
        try:
            return _windows_toast(title, message)
        except Exception:
            return False
    if shutil.which("notify-send"):
        try:
            result = subprocess.run(
                ["notify-send", "-a", "TempMail", "-i", "mail-message-new", "-u", urgency, title, message],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10
            )
            return result.returncode == 0
        except Exception:
            pass
    elif sys.platform == "darwin" and shutil.which("osascript"):
        try:
            # Pass text as arguments, never inside the script: senders and subjects are untrusted
            result = subprocess.run(
                ["osascript",
                 "-e", "on run argv",
                 "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
                 "-e", "end run",
                 message, title],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
            )
            return result.returncode == 0
        except Exception:
            pass
    return False


def open_url(url: str) -> bool:
    """Open a URL in the default browser; under WSL fall back to the Windows browser."""
    try:
        if webbrowser.open(url):
            return True
    except Exception:
        pass
    if _is_wsl():
        for cmd in (["wslview", url], ["explorer.exe", url]):
            if shutil.which(cmd[0]):
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except Exception:
                    pass
    return False


# --- Content Extraction & Heuristics ---
class Extractor:
    IGNORE_DOMAINS = [
        "guerrillamail.com", "guerrillamailblock.com", "sharklasers.com",
        "guerrillamail.net", "guerrillamail.org", "pokemail.net", "spam4.me", "grr.la",
        "mail.tm", "facebook.com", "twitter.com", "x.com", "instagram.com",
        "linkedin.com", "youtube.com", "tiktok.com", "t.co", "goo.gl"
    ]

    IGNORE_KEYWORDS = [
        "unsubscribe", "opt-out", "optout", "privacy-policy", "terms-of-service",
        "preferences", "manage-preferences", "view-in-browser", "view-online"
    ]

    STATIC_EXTENSIONS = (
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js", ".woff", ".woff2"
    )

    # "<qualifier> code" phrases right before a number that is not a one-time code
    # (zip code 90210, promo code: SAVE20, reference token 1234, ...)
    NON_OTP_LEAD = re.compile(
        r'\b(?:zip|postal|post|promo|promotional|discount|coupon|voucher|gift|referral|invite|invitation|'
        r'tracking|order|reference|ref|error|status|area|country|dial|tax|product|item|bar|qr)'
        r'[\s-]*(?:code|token|pin)[^\w\n]*(?:is[^\w\n]*)?\Z',
        re.I
    )

    OTP_HINT = re.compile(r'\b(?:codes?|otp|pin|passcode|password|verif\w*|confirm\w*)\b', re.I)

    @classmethod
    def clean_html(cls, raw_html: str) -> str:
        """Strip tags while preserving layout structure."""
        if not raw_html:
            return ""
        clean = re.sub(r'<(script|style|head)[^>]*>[\s\S]*?</\1>', ' ', raw_html, flags=re.I)
        clean = re.sub(r'<(br|/p|/div|/h[1-6]|/tr|/li)>', '\n', clean, flags=re.I)
        clean = re.sub(r'<[^>]+>', ' ', clean)
        return html.unescape(clean)

    @classmethod
    def extract_otp(cls, text: str) -> List[Dict[str, Any]]:
        """Extract OTP / Verification Codes with high precision."""
        candidates = []
        seen = set()

        # High confidence explicit patterns
        patterns = [
            # "code: 123-456" or "OTP: 123 456"
            r'(?i)\b(?:(?:verification|verify|security|confirm(?:ation)?|auth(?:entication)?|one[- ]time|login|access)\s*)?(?:code|otp|pin|passcode|token)\b\s*(?:is\s*:?|:|=|-)*\s*([0-9]{3,4}[- ][0-9]{3,4})\b',
            # "code: 123456", "OTP is: 123456", "pin: 1234"
            r'(?i)\b(?:(?:verification|verify|security|confirm(?:ation)?|auth(?:entication)?|one[- ]time|login|access)\s*)?(?:code|otp|pin|passcode|token)\b\s*(?:is\s*:?|:|=|-)*\s*([0-9]{4,8})\b',
            # "123456 is your code / verification code / login code"
            r'(?i)\b([0-9]{4,8})\s+is\s+your\s+(?:[A-Za-z0-9_-]+\s+)*(?:code|otp|pin|passcode|token|verification|password)\b',
            # "Use code 123456 to verify / login"
            r'(?i)\b(?:use|enter)\s+(?:code\s+)?([0-9]{4,8})\s+to\s+(?:verify|confirm|log\s*in|authenticate)',
            # Alphanumeric codes: e.g. "confirmation code: ABC-123" or "verification code: 7AF29"
            r'(?i)\b(?:(?:verification|security|confirm(?:ation)?|activation)\s*)?code\b\s*(?:is\s*:?|:|=|-)+\s*([A-Za-z0-9]{3,8}(?:-[A-Za-z0-9]{3,8})?)\b'
        ]

        for pat in patterns:
            for m in re.finditer(pat, text):
                if cls.NON_OTP_LEAD.search(text[max(0, m.start(1) - 40):m.start(1)]):
                    continue
                raw = m.group(1).strip()
                code = raw.replace(" ", "").replace("-", "")
                # Filter false positive alphabetic words
                if raw.isalpha():
                    if raw.lower() in {"your", "this", "that", "the", "please", "expires", "valid", "click", "here", "below", "code", "enter", "will", "from"}:
                        continue
                    if not raw.isupper() or len(raw) < 4:
                        continue
                if code not in seen:
                    seen.add(code)
                    candidates.append({
                        "code": code,
                        "raw": raw,
                        "confidence": "high",
                        "context": text[max(0, m.start()-20):min(len(text), m.end()+20)].strip()
                    })

        # Medium confidence fallback: Standalone line or prominent number near verification keywords
        if not candidates:
            this_year = datetime.date.today().year
            likely_years = {str(y) for y in range(this_year - 5, this_year + 6)}
            lines = text.splitlines()
            for i, line in enumerate(lines):
                line_strip = line.strip()
                if re.fullmatch(r'[0-9]{4,8}', line_strip):
                    if line_strip in likely_years:
                        continue
                    context = " ".join(lines[max(0, i-2):min(len(lines), i+3)])
                    if cls.OTP_HINT.search(context):
                        candidates.append({
                            "code": line_strip,
                            "raw": line_strip,
                            "confidence": "medium",
                            "context": line_strip
                        })
                        break

        return candidates

    @classmethod
    def score_url(cls, url: str, anchor: str = "") -> int:
        """Score a URL to determine if it is a confirmation/activation link."""
        url_lower = url.lower()
        anchor_lower = anchor.lower()

        try:
            host = (urllib.parse.urlsplit(url).hostname or "").lower()
        except ValueError:
            return -100
        # Match the hostname only: links often carry our own address (?email=x@sharklasers.com),
        # and substrings like "x.com" / "t.co" occur inside dropbox.com, microsoft.com, ...
        if any(host == d or host.endswith("." + d) for d in cls.IGNORE_DOMAINS):
            return -100

        if any(k in url_lower or k in anchor_lower for k in cls.IGNORE_KEYWORDS):
            return -100

        if url_lower.split("?")[0].endswith(cls.STATIC_EXTENSIONS):
            return -100

        score = 0
        target_words = [
            "verify", "verification", "confirm", "confirmation", "activate", "activation",
            "validate", "validation", "token=", "token/", "auth", "magic", "signup",
            "double-optin", "callback", "approve", "claim"
        ]

        for word in target_words:
            if word in url_lower:
                score += 35
            if word in anchor_lower:
                score += 45

        cta_phrases = [
            "confirm email", "verify email", "verify your account", "activate account",
            "click here to confirm", "click here to verify", "complete registration",
            "confirm your email", "log in", "get started"
        ]
        for cta in cta_phrases:
            if cta in anchor_lower:
                score += 50

        if "?" not in url and not any(k in url_lower for k in ["verify", "confirm", "activate", "token"]):
            score -= 20

        return score

    @classmethod
    def clean_url(cls, raw_url: str) -> Optional[str]:
        """Unescape a URL; None if it contains whitespace or control characters."""
        # Browsers drop tabs/newlines inside URLs (WHATWG URL spec); anything else unsafe is rejected
        url = re.sub(r'[\t\r\n]', '', html.unescape(raw_url)).strip()
        if re.search(r'\s', url) or _UNSAFE_CHARS.search(url):
            return None
        return url

    @classmethod
    def extract_links(cls, text: str, html_body: str = "") -> List[Dict[str, Any]]:
        """Extract and rank confirmation links."""
        candidates = []
        seen = set()

        if html_body:
            a_tags = re.findall(r'<a\s+[^>]*href=[\"\'](https?://[^\"\']+)[\"\'][^>]*>([\s\S]*?)</a>', html_body, re.I)
            for raw_url, anchor_html in a_tags:
                url = cls.clean_url(raw_url)
                if not url:
                    continue
                anchor = sanitize_text(cls.clean_html(anchor_html))
                if url not in seen:
                    seen.add(url)
                    score = cls.score_url(url, anchor)
                    if score > 0:
                        candidates.append({
                            "url": url,
                            "anchor": anchor,
                            "score": score
                        })

        combined = f"{text}\n{cls.clean_html(html_body)}" if html_body else text
        raw_urls = re.findall(r'https?://[^\s<>\"\'`{}|\\^]+', combined)
        for raw_url in raw_urls:
            url = cls.clean_url(raw_url)
            if not url:
                continue
            url = url.rstrip(".,;:)[]{}")
            if url not in seen:
                seen.add(url)
                score = cls.score_url(url, "")
                if score > 0:
                    candidates.append({
                        "url": url,
                        "anchor": "",
                        "score": score
                    })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates


# --- Temp Mail Provider Interfaces ---
class EmailMessage:
    def __init__(self, msg_id: str, sender: str, subject: str, date: str, body_text: str = "", body_html: str = ""):
        self.id = msg_id
        # Header fields are attacker-controlled and get printed verbatim, so strip control characters
        self.sender = sanitize_text(sender)
        self.subject = sanitize_text(subject)
        self.date = sanitize_text(date)
        self.body_text = body_text
        self.body_html = body_html
        self.otps: List[Dict[str, Any]] = []
        self.links: List[Dict[str, Any]] = []

    def analyze(self):
        full_text = f"{self.subject}\n{self.body_text}\n{Extractor.clean_html(self.body_html)}"
        self.otps = Extractor.extract_otp(full_text)
        self.links = Extractor.extract_links(self.body_text, self.body_html)


class BaseProvider:
    name: str = "base"

    def init_inbox(self, custom_user: Optional[str] = None, custom_domain: Optional[str] = None) -> str:
        raise NotImplementedError

    def resume_inbox(self, session_data: Dict[str, Any]) -> str:
        raise NotImplementedError

    def get_session_data(self) -> Dict[str, Any]:
        raise NotImplementedError

    def check_new_messages(self) -> List[EmailMessage]:
        raise NotImplementedError

    def get_domains(self) -> List[str]:
        raise NotImplementedError


def _session_created_at(session_data: Dict[str, Any]) -> float:
    # Older session files only carry "timestamp"
    return float(session_data.get("created_at", session_data.get("timestamp", 0)))


# --- GuerrillaMail Provider ---
class GuerrillaMailProvider(BaseProvider):
    name = "GuerrillaMail"
    DOMAINS = [
        "sharklasers.com",
        "guerrillamail.com",
        "guerrillamailblock.com",
        "pokemail.net",
        "spam4.me",
        "grr.la",
        "guerrillamail.net",
        "guerrillamail.org"
    ]

    def __init__(self):
        self.sid_token: str = ""
        self.email_address: str = ""
        self.alias_address: str = ""
        self.last_seq: int = 0
        self.chosen_domain: str = "sharklasers.com"
        self.created_at: float = 0.0
        self.host = "api.guerrillamail.com"
        self._conn: Optional[http.client.HTTPSConnection] = None

    def _get_connection(self) -> http.client.HTTPSConnection:
        if self._conn is None:
            self._conn = http.client.HTTPSConnection(self.host, timeout=12)
        return self._conn

    def _request(self, func: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Make an efficient HTTPS request, reusing keep-alive connection."""
        params = params or {}
        if self.sid_token and "sid_token" not in params:
            params["sid_token"] = self.sid_token

        query = urllib.parse.urlencode({"f": func, **params})
        url_path = f"/ajax.php?{query}"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            "Accept": "application/json"
        }
        if self.sid_token:
            headers["Cookie"] = f"PHPSESSID={self.sid_token}"

        for attempt in range(2):
            try:
                conn = self._get_connection()
                conn.request("GET", url_path, headers=headers)
                resp = conn.getresponse()
                raw_data = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw_data)
            except Exception:
                if self._conn:
                    try:
                        self._conn.close()
                    except Exception:
                        pass
                self._conn = None
                if attempt == 1:
                    raise

    def get_domains(self) -> List[str]:
        return self.DOMAINS

    def get_session_data(self) -> Dict[str, Any]:
        return {
            "provider": "guerrilla",
            "sid_token": self.sid_token,
            "email_address": self.email_address,
            "alias_address": self.alias_address,
            "last_seq": self.last_seq,
            "chosen_domain": self.chosen_domain,
            "created_at": self.created_at
        }

    def resume_inbox(self, session_data: Dict[str, Any]) -> str:
        self.sid_token = session_data.get("sid_token", "")
        self.email_address = session_data.get("email_address", "")
        self.alias_address = session_data.get("alias_address", "")
        self.last_seq = int(session_data.get("last_seq", 1))
        self.chosen_domain = session_data.get("chosen_domain", "sharklasers.com")
        self.created_at = _session_created_at(session_data)
        return self.email_address

    def init_inbox(self, custom_user: Optional[str] = None, custom_domain: Optional[str] = None) -> str:
        # Only GuerrillaMail's own domains route to this inbox
        if custom_domain:
            custom_domain = custom_domain.lower()
            if custom_domain not in self.DOMAINS:
                raise ValueError(f"'{custom_domain}' is not a GuerrillaMail domain (choose from: {', '.join(self.DOMAINS)})")

        data = self._request("get_email_address")
        self.sid_token = data.get("sid_token", "")
        self.email_address = data.get("email_addr", "")
        self.alias_address = data.get("alias", "")

        if custom_user:
            user_data = self._request("set_email_user", {"email_user": custom_user, "lang": "en"})
            self.email_address = user_data.get("email_addr", self.email_address)

        user_part = self.email_address.split("@")[0]
        if custom_domain:
            self.chosen_domain = custom_domain
        self.email_address = f"{user_part}@{self.chosen_domain}"

        # Initialize sequence to highest currently existing mail_id
        initial_check = self._request("check_email", {"seq": 0})
        msg_list = initial_check.get("list", [])
        if msg_list:
            self.last_seq = max(int(m.get("mail_id", 0)) for m in msg_list)
        else:
            self.last_seq = 1

        self.created_at = time.time()
        return self.email_address

    def check_new_messages(self) -> List[EmailMessage]:
        data = self._request("check_email", {"seq": self.last_seq})
        raw_list = sorted(data.get("list", []), key=lambda m: int(m.get("mail_id", 0)))
        new_messages = []

        for item in raw_list:
            mail_id = int(item.get("mail_id", 0))
            if mail_id <= self.last_seq:
                continue

            # Skip initial welcome email
            if mail_id == 1 and "Welcome to Guerrilla Mail" in item.get("mail_subject", ""):
                self.last_seq = mail_id
                continue

            # Advance last_seq only once a message is fetched, so a failed fetch is retried next poll
            try:
                detail = self._request("fetch_email", {"email_id": mail_id})
            except Exception:
                if new_messages:
                    break
                raise
            body = detail.get("mail_body", "")

            msg = EmailMessage(
                msg_id=str(mail_id),
                sender=detail.get("mail_from", item.get("mail_from", "unknown")),
                subject=detail.get("mail_subject", item.get("mail_subject", "No Subject")),
                date=detail.get("mail_date", item.get("mail_date", "")),
                body_text=Extractor.clean_html(body),
                body_html=body
            )
            msg.analyze()
            new_messages.append(msg)
            self.last_seq = mail_id

        return new_messages


# --- Mail.tm Provider ---
class MailTmProvider(BaseProvider):
    name = "Mail.tm"

    def __init__(self):
        self.base_url = "https://api.mail.tm"
        self.token: str = ""
        self.account_id: str = ""
        self.email_address: str = ""
        self.password: str = ""
        self.seen_ids = set()
        self.created_at: float = 0.0

    def _send(self, method: str, path: str, payload: Optional[Dict] = None, auth: bool = False) -> Any:
        url = f"{self.base_url}{path}"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0)",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        data = json.dumps(payload).encode("utf-8") if payload else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        with urllib.request.urlopen(req, timeout=12) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}

    def _request(self, method: str, path: str, payload: Optional[Dict] = None, auth: bool = False) -> Any:
        try:
            return self._send(method, path, payload, auth)
        except urllib.error.HTTPError as e:
            # JWTs expire (e.g. on a resumed session): log in again with the stored password and retry once
            if e.code == 401 and auth and self.password:
                e.close()
                self._login()
                return self._send(method, path, payload, auth)
            raise

    def _login(self):
        token_resp = self._send("POST", "/token", {"address": self.email_address, "password": self.password})
        self.token = token_resp.get("token", "")

    def get_domains(self) -> List[str]:
        data = self._request("GET", "/domains")
        items = data if isinstance(data, list) else data.get("hydra:member", [])
        return [d["domain"] for d in items if isinstance(d, dict) and d.get("isActive", True)]

    def get_session_data(self) -> Dict[str, Any]:
        return {
            "provider": "mailtm",
            "token": self.token,
            "account_id": self.account_id,
            "email_address": self.email_address,
            "password": self.password,
            "seen_ids": sorted(self.seen_ids),
            "created_at": self.created_at
        }

    def resume_inbox(self, session_data: Dict[str, Any]) -> str:
        self.token = session_data.get("token", "")
        self.account_id = session_data.get("account_id", "")
        self.email_address = session_data.get("email_address", "")
        self.password = session_data.get("password", "")
        self.seen_ids = set(session_data.get("seen_ids", []))
        self.created_at = _session_created_at(session_data)
        return self.email_address

    def init_inbox(self, custom_user: Optional[str] = None, custom_domain: Optional[str] = None) -> str:
        domains = self.get_domains()
        if not domains:
            raise RuntimeError("No active domains found for Mail.tm")

        if custom_domain:
            domain = next((d for d in domains if d.lower() == custom_domain.lower()), None)
            if not domain:
                raise ValueError(f"'{custom_domain}' is not an active Mail.tm domain (choose from: {', '.join(domains)})")
        else:
            domain = domains[0]
        user = custom_user or f"tmp_{secrets.token_hex(4)}"
        self.email_address = f"{user}@{domain}"
        self.password = secrets.token_urlsafe(16)

        # 1. Create account
        acc = self._request("POST", "/accounts", {"address": self.email_address, "password": self.password})
        self.account_id = acc.get("id", "")

        # 2. Get JWT token
        self._login()

        # 3. Mark existing messages as seen
        messages_resp = self._request("GET", "/messages", auth=True)
        messages = messages_resp if isinstance(messages_resp, list) else messages_resp.get("hydra:member", [])
        for m in messages:
            if m.get("id"):
                self.seen_ids.add(m["id"])

        self.created_at = time.time()
        return self.email_address

    def check_new_messages(self) -> List[EmailMessage]:
        messages_resp = self._request("GET", "/messages", auth=True)
        messages = messages_resp if isinstance(messages_resp, list) else messages_resp.get("hydra:member", [])
        new_messages = []

        for m in messages:
            msg_id = m.get("id")
            if not msg_id or msg_id in self.seen_ids:
                continue

            # Mark as seen only once fetched, so a failed fetch is retried next poll
            try:
                detail = self._request("GET", f"/messages/{msg_id}", auth=True)
            except Exception:
                if new_messages:
                    break
                raise
            body_text = detail.get("text", "")
            body_html = ""
            html_list = detail.get("html", [])
            if html_list and isinstance(html_list, list):
                body_html = "".join(html_list)

            sender_obj = detail.get("from", {})
            sender = sender_obj.get("address", sender_obj.get("name", "unknown"))

            msg = EmailMessage(
                msg_id=str(msg_id),
                sender=sender,
                subject=detail.get("subject", "No Subject"),
                date=detail.get("createdAt", ""),
                body_text=body_text,
                body_html=body_html
            )
            msg.analyze()
            new_messages.append(msg)
            self.seen_ids.add(msg_id)

        return new_messages


# --- Session Persistence ---
def save_session(session_data: Dict[str, Any]):
    """Atomically write the session file, readable only by the current user (it holds tokens/passwords)."""
    try:
        os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
        tmp_path = f"{SESSION_FILE}.tmp"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(session_data, f)
        os.replace(tmp_path, SESSION_FILE)
    except Exception:
        pass

def load_session() -> Optional[Dict[str, Any]]:
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Session valid for up to 60 minutes after the inbox was created
        if time.time() - _session_created_at(data) < SESSION_MAX_AGE:
            return data
    except Exception:
        pass
    return None


# --- CLI UI Presenters ---
def _box_row(content: str, width: int, color: str, edge: str = "│") -> str:
    """One boxed line, padded by display width so emoji and color codes don't skew the border."""
    pad = max(0, width - 2 - display_width(content))
    return f"{color}{edge}{UI.RESET}{content}{' ' * pad}{color}{edge}{UI.RESET}"


def print_banner(email_addr: str, provider: str, interval: float, timeout: int, copied: bool, copy_target: str,
                 is_resumed: bool = False, copy_requested: bool = True):
    width = 66
    border_color = UI.CYAN

    print(f"\n{border_color}╭{'─' * (width - 2)}╮{UI.RESET}")
    tag = " (Resumed)" if is_resumed else ""
    print(_box_row(f"  {UI.BOLD}{UI.MAGENTA}⚡ TempMail Watcher{UI.RESET}{UI.DIM} • v{VERSION}{tag}{UI.RESET}", width, border_color))
    print(f"{border_color}├{'─' * (width - 2)}┤{UI.RESET}")
    print(_box_row(f"  {UI.BOLD}📫 Address  :{UI.RESET} {UI.GREEN}{UI.BOLD}{email_addr}{UI.RESET}", width, border_color))

    if copied:
        copy_status = f"{UI.GREEN}✓ Copied to clipboard! Ready to paste.{UI.RESET}"
    elif not copy_requested:
        copy_status = f"{UI.YELLOW}Clipboard copy skipped{UI.RESET}"
    else:
        copy_status = f"{UI.YELLOW}No clipboard available{UI.RESET}"
    print(_box_row(f"  {UI.BOLD}📋 Clipboard:{UI.RESET} {copy_status}", width, border_color))

    prov_str = f"{provider} (poll: {interval}s, timeout: {timeout}s)"
    print(_box_row(f"  {UI.BOLD}🌐 Provider :{UI.RESET} {UI.CYAN}{prov_str}{UI.RESET}", width, border_color))
    print(_box_row(f"  {UI.BOLD}🎯 Auto-Copy:{UI.RESET} {UI.YELLOW}{copy_target.upper()}{UI.RESET}", width, border_color))
    print(f"{border_color}╰{'─' * (width - 2)}╯{UI.RESET}\n")
    print(f"{UI.DIM}⏳ Watching inbox for incoming confirmation mails / OTPs... (Ctrl+C to stop){UI.RESET}")


def print_message_card(msg: EmailMessage, copied_val: Optional[str] = None):
    width = 72
    b = UI.GREEN
    r = UI.RESET
    title = "NEW VERIFICATION EMAIL RECEIVED" if (msg.otps or msg.links) else "NEW EMAIL RECEIVED"

    print(f"\n\a{b}╔{'═' * (width - 2)}╗{r}")
    print(_box_row(f"  {UI.BOLD}{UI.GREEN}✉️  {title}{r}", width, b, "║"))
    print(f"{b}╠{'═' * (width - 2)}╣{r}")

    def row(label, val, color=""):
        prefix = f"  {UI.BOLD}{label:<10}:{r} "
        clean_val = truncate_width(val, width - 2 - display_width(prefix))
        print(_box_row(f"{prefix}{color}{clean_val}{r}", width, b, "║"))

    row("From", msg.sender, UI.CYAN)
    row("Subject", msg.subject, UI.WHITE + UI.BOLD)
    if msg.date:
        row("Date", msg.date, UI.DIM)

    # Show OTPs
    if msg.otps:
        print(f"{b}╟{'─' * (width - 2)}╢{r}")
        for otp in msg.otps:
            code = otp["code"]
            badge = f" {UI.BG_GREEN}{UI.BLACK}{UI.BOLD} COPIED TO CLIPBOARD {r}" if copied_val == code else ""
            print(_box_row(f"  {UI.BOLD}{UI.YELLOW}🔑 OTP CODE{r} : {UI.BOLD}{UI.WHITE}{code}{r}{badge}", width, b, "║"))

    # Show Links
    if msg.links:
        print(f"{b}╟{'─' * (width - 2)}╢{r}")
        for i, link in enumerate(msg.links[:3]):
            url = link["url"]
            anchor = link.get("anchor") or ""
            badge = f" {UI.BG_BLUE}{UI.WHITE}{UI.BOLD} COPIED {r}" if copied_val == url else ""
            if anchor:
                row(f"Link #{i+1}", f"[{anchor}]", UI.DIM)
            prefix = f"  {UI.BOLD}{UI.CYAN}🔗 LINK{r}     : "
            display_url = truncate_width(url, width - 2 - display_width(prefix) - display_width(badge))
            print(_box_row(f"{prefix}{UI.UNDERLINE}{display_url}{r}{badge}", width, b, "║"))

    if not msg.otps and not msg.links:
        print(f"{b}╟{'─' * (width - 2)}╢{r}")
        row("Snippet", sanitize_text(msg.body_text[:300]) or "No text content", UI.DIM)

    print(f"{b}╚{'═' * (width - 2)}╝{r}\n")


# --- Main Loop & Dispatcher ---
def main():
    # Never crash on characters the output encoding can't represent (e.g. emoji piped on Windows)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(
        description="⚡ TempMail Watcher: Ultra-efficient temporary email & OTP/link auto-copy utility.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  tempmail                           # Generate temp-mail, copy to clipboard, watch for OTP/link
  tempmail -a                        # Generate & print email address only, copy to clipboard, exit
  tempmail -o                        # Auto-open confirmation link in browser when received
  tempmail -u testbot -d grr.la      # Use custom user & domain (sharklasers.com, grr.la, etc.)
  tempmail -p mailtm                 # Use Mail.tm provider instead of GuerrillaMail
  tempmail -w                        # Keep watching indefinitely for multiple emails
  tempmail -r                        # Resume watching the previous temp mail inbox
  tempmail --copy otp                # Only copy OTP code (or --copy link)
  tempmail --raw                     # Headless script mode (outputs EMAIL and OTP/LINK)

Exit status: 0 on success, 1 on error, 2 on bad arguments,
             124 if the timeout passes without an OTP or confirmation link.
        """
    )
    parser.add_argument("-p", "--provider", choices=["guerrilla", "mailtm"], default="guerrilla",
                        help="Temporary email provider (default: guerrilla)")
    parser.add_argument("-u", "--user", type=str, default=None,
                        help="Custom username/prefix for inbox")
    parser.add_argument("-d", "--domain", type=str, default=None,
                        help="Custom domain alias (e.g. sharklasers.com, grr.la, pokemail.net)")
    parser.add_argument("-i", "--interval", type=float, default=2.5,
                        help=f"Polling interval in seconds, minimum {MIN_INTERVAL:g} (default: 2.5)")
    parser.add_argument("-t", "--timeout", type=int, default=300,
                        help="Watch timeout in seconds, 0 for infinite (default: 300)")
    parser.add_argument("-c", "--copy", choices=["smart", "otp", "link", "email", "none"], default="smart",
                        help="Target to auto-copy to clipboard on arrival (default: smart: OTP then link)")
    parser.add_argument("-o", "--open", action="store_true",
                        help="Automatically open high-confidence confirmation links in default browser")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-w", "--watch", action="store_true",
                      help="Keep watching indefinitely for multiple incoming emails")
    parser.add_argument("-a", "--address", action="store_true",
                        help="Only generate/print email address, copy to clipboard, and exit")
    parser.add_argument("-r", "--resume", action="store_true",
                        help="Resume watching the previous active mailbox session")
    mode.add_argument("--once", action="store_true",
                      help="Exit after the first email with an OTP or confirmation link (default)")
    parser.add_argument("--raw", action="store_true",
                        help="Script-friendly raw output (prints email address, then OTP/link)")
    parser.add_argument("-j", "--json", action="store_true",
                        help="Output JSON stream for automation pipelines")
    parser.add_argument("--no-notify", action="store_true",
                        help="Disable desktop notifications")
    parser.add_argument("--no-copy-address", action="store_true",
                        help="Do not copy the email address to clipboard initially")
    parser.add_argument("--list-domains", action="store_true",
                        help="List available domains for the provider and exit")
    parser.add_argument("-v", "--version", action="version", version=f"tempmail {VERSION}")

    args = parser.parse_args()

    if args.interval < MIN_INTERVAL:
        parser.error(f"--interval must be at least {MIN_INTERVAL:g}s")
    if args.timeout < 0:
        parser.error("--timeout must be 0 (no limit) or a positive number of seconds")

    quiet = args.raw or args.json
    show_spinner = not quiet and sys.stdout.isatty()

    def fail(message: str):
        if quiet:
            print(json.dumps({"error": message}), file=sys.stderr)
        else:
            print(f"{UI.RED}{message}{UI.RESET}", file=sys.stderr)
        sys.exit(1)

    def clear_spinner():
        if show_spinner:
            # Overwrite with spaces rather than an ANSI erase, which legacy Windows consoles don't support
            sys.stdout.write("\r" + " " * (shutil.get_terminal_size().columns - 1) + "\r")
            sys.stdout.flush()

    # Determine provider
    is_resumed = False
    if args.resume:
        saved_session = load_session()
        if not saved_session:
            fail(f"No session to resume (none saved, or the inbox is older than {SESSION_MAX_AGE // 60} minutes). "
                 "Run without -r to create a new inbox.")
        prov_name = saved_session.get("provider", "guerrilla")
        provider = MailTmProvider() if prov_name == "mailtm" else GuerrillaMailProvider()
        email_address = provider.resume_inbox(saved_session)
        is_resumed = True
    else:
        provider = MailTmProvider() if args.provider == "mailtm" else GuerrillaMailProvider()

    # Domain listing
    if args.list_domains:
        try:
            domains = provider.get_domains()
            if args.json:
                print(json.dumps({"provider": provider.name, "domains": domains}))
            else:
                print(f"{UI.BOLD}Available domains for {provider.name}:{UI.RESET}")
                for d in domains:
                    print(f"  • {UI.CYAN}{d}{UI.RESET}")
        except Exception as e:
            print(f"{UI.RED}Error fetching domains: {e}{UI.RESET}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    # Initialize mailbox if not resumed
    if not is_resumed:
        try:
            email_address = provider.init_inbox(custom_user=args.user, custom_domain=args.domain)
        except Exception as e:
            fail(f"Failed to create inbox on {provider.name}: {e}")
    session_state = provider.get_session_data()
    save_session(session_state)

    # Copy email address to clipboard initially
    address_copied = False
    if not args.no_copy_address:
        address_copied, _ = copy_to_clipboard(email_address)

    # Address-only mode: print and exit
    if args.address:
        if args.raw or not sys.stdout.isatty():
            print(email_address)
        else:
            if address_copied:
                note = f" {UI.DIM}(Copied to clipboard){UI.RESET}"
            elif args.no_copy_address:
                note = ""
            else:
                note = f" {UI.DIM}(No clipboard available){UI.RESET}"
            print(f"{UI.GREEN}{UI.BOLD}{email_address}{UI.RESET}{note}")
        sys.exit(0)

    # Raw / JSON mode initial output
    if args.raw:
        print(f"EMAIL: {email_address}", flush=True)
    elif args.json:
        print(json.dumps({"event": "inbox_created", "email": email_address, "provider": provider.name, "resumed": is_resumed}), flush=True)
    else:
        print_banner(email_address, provider.name, args.interval, args.timeout, address_copied, args.copy, is_resumed,
                     copy_requested=not args.no_copy_address)

    # Watch loop
    start_time = time.time()
    check_count = 0
    poll_errors = 0
    got_verification = False
    spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    spinner_idx = 0

    try:
        while True:
            elapsed = time.time() - start_time
            if args.timeout > 0 and elapsed >= args.timeout:
                clear_spinner()
                if args.json:
                    print(json.dumps({"event": "timeout", "seconds": args.timeout}), flush=True)
                elif args.raw:
                    print(f"TIMEOUT: {args.timeout}s", file=sys.stderr, flush=True)
                elif got_verification:
                    print(f"\n{UI.YELLOW}⏱️  Timeout reached ({args.timeout}s). Stopped watching.{UI.RESET}")
                else:
                    print(f"\n{UI.YELLOW}⏱️  Timeout reached ({args.timeout}s). No new verification emails received.{UI.RESET}")
                # Non-zero when nothing usable arrived, so scripts can tell a timeout from success
                sys.exit(0 if got_verification else EXIT_TIMEOUT)

            check_count += 1

            # Polling animation in TTY mode
            if show_spinner:
                mins, secs = divmod(int(elapsed), 60)
                spin = spinner[spinner_idx % len(spinner)]
                spinner_idx += 1
                sys.stdout.write(f"\r  {UI.CYAN}{spin}{UI.RESET} Checking inbox... {UI.DIM}[{mins:02d}:{secs:02d} elapsed, {check_count} check{'' if check_count == 1 else 's'}]{UI.RESET}   ")
                sys.stdout.flush()

            # Poll for messages
            try:
                messages = provider.check_new_messages()
            except Exception as e:
                poll_errors += 1
                if poll_errors == POLL_ERROR_WARN_AFTER:
                    clear_spinner()
                    if args.json:
                        print(json.dumps({"event": "poll_error", "error": str(e)}), flush=True)
                    else:
                        print(f"{UI.YELLOW}⚠️  Inbox checks keep failing ({e}); still retrying...{UI.RESET}", file=sys.stderr, flush=True)
                # Back off while the provider is unreachable
                time.sleep(max(args.interval, min(args.interval * poll_errors, 30)))
                continue

            if poll_errors >= POLL_ERROR_WARN_AFTER and not args.json:
                clear_spinner()
                print(f"{UI.GREEN}✓ Inbox reachable again.{UI.RESET}", file=sys.stderr, flush=True)
            poll_errors = 0

            # Persist progress (seq / seen ids / refreshed token) only when it changes
            current_state = provider.get_session_data()
            if current_state != session_state:
                save_session(current_state)
                session_state = current_state

            if messages:
                clear_spinner()

                for msg in messages:
                    has_verification = bool(msg.otps or msg.links)
                    copied_val = None
                    copied_type = None

                    # Determine what to copy
                    if args.copy in ("smart", "otp") and msg.otps:
                        copied_val = msg.otps[0]["code"]
                        copied_type = "OTP"
                    elif args.copy in ("smart", "link") and msg.links:
                        copied_val = msg.links[0]["url"]
                        copied_type = "Link"
                    elif args.copy == "email":
                        copied_val = email_address
                        copied_type = "Email"

                    # Execute clipboard copy
                    copy_ok = bool(copied_val) and copy_to_clipboard(copied_val)[0]

                    # Auto-open only links that look like real verification links: anyone can mail this inbox
                    opened_url = None
                    if args.open and msg.links and msg.links[0]["score"] >= AUTO_OPEN_MIN_SCORE:
                        if open_url(msg.links[0]["url"]):
                            opened_url = msg.links[0]["url"]

                    # Desktop notification
                    if not args.no_notify:
                        copied_note = f"{copied_type} copied to clipboard!\n" if copy_ok else ""
                        if msg.otps:
                            otp_code = msg.otps[0]["code"]
                            send_notification(f"OTP: {otp_code}", f"{copied_note}From: {msg.sender}", urgency="critical")
                        elif msg.links:
                            send_notification("Confirmation Link Received", f"{copied_note}From: {msg.sender}")
                        else:
                            send_notification("New Email Received", f"From: {msg.sender}\nSubject: {msg.subject}")

                    # Output presentation
                    if args.raw:
                        if msg.otps:
                            print(f"OTP: {msg.otps[0]['code']}", flush=True)
                        if msg.links:
                            print(f"LINK: {msg.links[0]['url']}", flush=True)
                        print(f"FROM: {msg.sender}", flush=True)
                        print(f"SUBJECT: {msg.subject}", flush=True)
                    elif args.json:
                        event_data = {
                            "event": "message_received",
                            "from": msg.sender,
                            "subject": msg.subject,
                            "date": msg.date,
                            "otps": [o["code"] for o in msg.otps],
                            "links": [link["url"] for link in msg.links],
                            "copied": copied_val if copy_ok else None,
                            "copied_type": copied_type if copy_ok else None,
                            "opened": opened_url
                        }
                        print(json.dumps(event_data), flush=True)
                    else:
                        print_message_card(msg, copied_val if copy_ok else None)
                        if opened_url:
                            print(f"{UI.CYAN}🌐 Opened the confirmation link in your browser.{UI.RESET}")
                        elif args.open and msg.links and msg.links[0]["score"] < AUTO_OPEN_MIN_SCORE:
                            print(f"{UI.DIM}Link not auto-opened: it doesn't look like a verification link.{UI.RESET}")
                        elif args.open and msg.links:
                            print(f"{UI.YELLOW}Couldn't open a browser; copy the link above instead.{UI.RESET}")
                        if not has_verification and not args.watch:
                            print(f"{UI.DIM}No OTP or confirmation link in this email; still watching...{UI.RESET}")

                    # Exit after the first OTP/link unless in continuous watch mode
                    if has_verification:
                        got_verification = True
                        if not args.watch:
                            if not quiet:
                                if copy_ok:
                                    print(f"{UI.GREEN}{UI.BOLD}✨ Done! {copied_type} ready in clipboard. Exiting.{UI.RESET}\n")
                                else:
                                    print(f"{UI.GREEN}{UI.BOLD}✨ Done! Exiting.{UI.RESET}\n")
                            sys.exit(0)

            time.sleep(args.interval)

    except KeyboardInterrupt:
        if not args.raw and not args.json:
            print(f"\n\n{UI.YELLOW}👋 Stopped TempMail watcher.{UI.RESET}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()

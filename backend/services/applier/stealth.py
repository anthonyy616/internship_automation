"""
Stealth & session helpers (T0 of the smart-agent spec).

The stock Playwright headless launch leaks tell-tale signals
(navigator.webdriver=true, no window.chrome, empty plugins, mismatched
UA vs sec-ch-ua) that bot managers fingerprint before the form even
loads. This module:

    - injects a page init script that removes the most common tells
      (webdriver flag, chrome stub, plugins/languages, webgl strings)
    - rotates a small pool of realistic desktop UAs with matching
      sec-ch-ua headers, viewport variance and locale
    - dismisses GDPR/cookie consent banners that would intercept clicks
    - persists per-host cookies (storage_state) so logins and solved
      challenges survive between sessions

Nothing here guarantees bypassing anti-bot systems; it maximises the
success rate on sites that are automatable and keeps failures honest.
"""

import os
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.config import settings

# (user_agent, platform, chrome_version) — kept in sync with the
# sec-ch-ua headers we send.
UA_POOL: Tuple[Tuple[str, str, str], ...] = (
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
     "Windows", '"Chromium";v="126", "Google Chrome";v="126", "Not.A/Brand";v="8"'),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
     "Windows", '"Chromium";v="125", "Google Chrome";v="125", "Not.A/Brand";v="8"'),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
     "macOS", '"Chromium";v="126", "Google Chrome";v="126", "Not.A/Brand";v="8"'),
)

STEALTH_JS = """() => {
    // 1) The loudest headless tell
    Object.defineProperty(navigator, 'webdriver', { get: () => false });

    // 2) window.chrome object real Chrome exposes
    if (!window.chrome) {
        window.chrome = {
            runtime: {}, loadTimes: function () {}, csi: function () {},
            app: { isInstalled: false }, webstore: { onInstallStageChanged: {}, onDownloadProgress: {} }
        };
    }

    // 3) plugins + mimeTypes
    try {
        const makePlugin = (name, description, filename, suffixes) => {
            const p = { name: name, description: description, filename: filename, length: 1,
                        item: (i) => p[0], namedItem: (n) => n === suffixes[0] ? p[0] : null,
                        0: { type: 'application/x-' + suffixes[0], suffixes: suffixes.join(','), description: description } };
            return p;
        };
        const plugins = [
            makePlugin('PDF Viewer', 'Portable Document Format', 'internal-pdf-viewer', ['pdf']),
            makePlugin('Chrome PDF Viewer', 'Portable Document Format', 'chrome-pdf-viewer', ['pdf']),
            makePlugin('Chromium PDF Viewer', 'Portable Document Format', 'chrome-pdf-viewer', ['pdf']),
            makePlugin('Microsoft Edge PDF Viewer', 'Portable Document Format', 'chrome-pdf-viewer', ['pdf']),
            makePlugin('WebKit built-in PDF', 'Portable Document Format', 'webkit-pdf-viewer', ['pdf'])
        ];
        Object.defineProperty(navigator, 'plugins', { get: () => plugins });
        Object.defineProperty(navigator, 'mimeTypes', {
            get: () => [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }]
        });
    } catch (e) {}

    // 4) languages + permissions (headless often reports no permission state)
    try {
        Object.defineProperty(navigator, 'languages', { get: () => ['en-GB', 'en-US', 'en'] });
        const qp = navigator.permissions.query;
        navigator.permissions.query = (p) => p && p.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission, onchange: null })
            : qp(p);
    } catch (e) {}

    // 5) WebGL renderer strings that match a real GPU
    try {
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function (parameter) {
            if (parameter === 37445) return 'Google Inc. (NVIDIA)';
            if (parameter === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
            return getParameter.call(this, parameter);
        };
    } catch (e) {}

    // 6) Hardware consistency (avoid the classic "8 cores on a phone-sized screen")
    try {
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    } catch (e) {}
}"""


def pick_browser_profile() -> Dict:
    """A consistent (UA, sec-ch-ua, platform, viewport, locale) bundle."""
    ua, platform, brand = random.choice(UA_POOL)
    width = random.randint(1366, 1390)
    height = random.randint(760, 800)
    return {
        "user_agent": ua,
        "platform": platform,
        "brand": brand,
        "viewport": {"width": width, "height": height},
        "locale": "en-GB",
    }


def build_context_options(host: str, proxy: Optional[Dict] = None) -> Dict:
    """Options for browser.new_context(): profile + session reuse + proxy.

    ``proxy`` is a Playwright proxy dict from ProxyRotator.next() — pass it
    in so the caller knows which proxy this session is using (for failure
    reporting). Localhost is never proxied.
    """
    profile = pick_browser_profile()
    opts = {
        "user_agent": profile["user_agent"],
        "viewport": profile["viewport"],
        "locale": profile["locale"],
        "extra_http_headers": {
            "sec-ch-ua": profile["brand"],
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{profile["platform"]}"',
            "Accept-Language": "en-GB,en;q=0.9",
        },
    }
    if settings.apply_reuse_session:
        state = load_session(host)
        if state:
            opts["storage_state"] = state
    if proxy and not is_localhost(host):
        opts["proxy"] = proxy
    return opts


# ----------------------------------------------------------------------
# Proxy rotation (T4)
# ----------------------------------------------------------------------
# PROXY_URLS accepts any provider's proxies:
#     http://user:pass@host:port   https://user:pass@host:port
#     socks5://host:port           socks4://host:port
# Credentials are split out of the URL because Playwright wants them as
# separate fields. Proxies rotate round-robin per session and a proxy that
# repeatedly fails (HTTP block, connection errors) goes on cooldown so the
# next session skips it. When every proxy is on cooldown the caller falls
# back to a direct connection rather than stalling the queue.


def is_localhost(host: str) -> bool:
    host = (host or "").lower().split(":")[0]
    return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]")


class ProxyRotator:
    """Round-robin proxy pool with per-proxy failure cooldown."""

    def __init__(
        self,
        urls: List[str],
        fail_threshold: int = 3,
        cooldown_seconds: int = 600,
    ):
        self._entries = [e for u in urls if (e := self._parse(u))]
        self._index = 0
        self._failures: Dict[str, int] = {}
        self._cooldown_until: Dict[str, float] = {}
        self.fail_threshold = max(1, fail_threshold)
        self.cooldown_seconds = max(0, cooldown_seconds)
        self.source = tuple(urls)  # fingerprint so config changes rebuild the pool
        self._lock = None

    @staticmethod
    def _parse(url: str) -> Optional[Dict]:
        """Parse a proxy URL into a Playwright proxy dict.

        Returns {server, username?, password?} or None when unparsable.
        """
        try:
            from urllib.parse import unquote, urlparse
            u = urlparse(url)
            if not u.hostname:
                return None
            scheme = (u.scheme or "http").lower()
            if scheme not in ("http", "https", "socks4", "socks5"):
                return None
            default_port = 1080 if scheme.startswith("socks") else 80
            proxy = {"server": f"{scheme}://{u.hostname}:{u.port or default_port}"}
            if u.username:
                proxy["username"] = unquote(u.username)
            if u.password:
                proxy["password"] = unquote(u.password)
            return proxy
        except Exception:
            return None

    async def next(self) -> Optional[Dict]:
        """Next healthy proxy round-robin, or None when all are on cooldown
        (callers then fall back to a direct connection)."""
        if not self._entries:
            return None
        await self._ensure_lock()
        async with self._lock:
            now = time.monotonic()
            for _ in range(len(self._entries)):
                entry = self._entries[self._index % len(self._entries)]
                self._index += 1
                if now < self._cooldown_until.get(entry["server"], 0):
                    continue
                return entry
            return None

    async def report_failure(self, server: str):
        """Count a failed session against this proxy; at the threshold the
        proxy is cooled down and skipped by future sessions."""
        if not server or not self._entries:
            return
        await self._ensure_lock()
        async with self._lock:
            fails = self._failures.get(server, 0) + 1
            self._failures[server] = fails
            if fails >= self.fail_threshold:
                self._cooldown_until[server] = time.monotonic() + self.cooldown_seconds
                self._failures[server] = 0
                print(f"[proxy] {server} marked unhealthy — cooldown {self.cooldown_seconds}s")

    async def report_success(self, server: str):
        """A successful session resets the failure counter for this proxy."""
        if not server or not self._entries:
            return
        await self._ensure_lock()
        async with self._lock:
            self._failures.pop(server, None)

    async def _ensure_lock(self):
        if self._lock is None:
            import asyncio
            self._lock = asyncio.Lock()


_proxy_rotator: Optional[ProxyRotator] = None


def get_proxy_rotator() -> ProxyRotator:
    """Process-wide rotator, rebuilt automatically when PROXY_URLS changes."""
    global _proxy_rotator
    urls = tuple(settings.apply_proxy_urls)
    if _proxy_rotator is None or _proxy_rotator.source != urls:
        _proxy_rotator = ProxyRotator(
            list(urls),
            fail_threshold=settings.apply_proxy_fail_threshold,
            cooldown_seconds=settings.apply_proxy_cooldown_seconds,
        )
    return _proxy_rotator


# Failures that plausibly implicate the proxy (vs the site / the form).
_PROXY_FAILURE_MARKERS = (
    "blocked by site", "http 429", "http 403", "http 451",
    "navigation failed", "net::", "connection reset", "tunnel", "proxy",
)


def is_proxy_relevant_failure(error: str, http_blocked=None) -> bool:
    """True when this outcome should count against the proxy used."""
    if http_blocked:
        return True
    low = (error or "").lower()
    return any(m in low for m in _PROXY_FAILURE_MARKERS)


# ----------------------------------------------------------------------
# Session (cookie) persistence
# ----------------------------------------------------------------------

def session_path(host: str) -> Path:
    return Path(settings.apply_session_dir) / f"{host}.json"


def load_session(host: str) -> Optional[Dict]:
    if not settings.apply_reuse_session:
        return None
    try:
        path = session_path(host)
        if path.exists():
            import json
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def save_session(host: str, storage_state: Dict) -> bool:
    if not settings.apply_reuse_session or not storage_state:
        return False
    try:
        path = session_path(host)
        path.parent.mkdir(parents=True, exist_ok=True)
        import json
        path.write_text(json.dumps(storage_state), encoding="utf-8")
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------
# Cookie / GDPR consent banners
# ----------------------------------------------------------------------

BANNER_BUTTON_TEXTS = (
    "Accept all", "Accept", "Agree", "Agree and continue", "Allow all",
    "Allow", "Got it", "OK", "I agree", "I accept", "Continue", "Consent",
)


# ----------------------------------------------------------------------
# Managed anti-bot sessions (Hyperbrowser) — T4
# ----------------------------------------------------------------------
# Hyperbrowser runs cloud Chromium sessions with stealth + rotating proxies
# baked in; you connect over CDP exactly like the SDK example
# (create session -> wsEndpoint -> connectOverCDP). We call the REST API
# directly with httpx, so no Hyperbrowser SDK install is needed.

HYPERBROWSER_API_BASE = "https://api.hyperbrowser.ai/api"
HYPERBROWSER_TIMEOUT_MINUTES = int(os.getenv("HYPERBROWSER_TIMEOUT_MINUTES", "30"))
# Hyperbrowser routes sessions through their rotating proxy pool when this is
# on. NOTE: their Free plan rejects useProxy with HTTP 402 — leave off unless
# your plan supports proxies (see HYPERBROWSER_USE_PROXY in .env).
HYPERBROWSER_USE_PROXY = os.getenv("HYPERBROWSER_USE_PROXY", "false").strip().lower() not in ("", "0", "false", "no")


async def create_hyperbrowser_session() -> Optional[Dict]:
    """Create a managed browser session via the Hyperbrowser REST API.

    Returns {"id", "ws_endpoint", "live_url"} or None when unavailable
    (no key, network error, non-200), so callers can fall back to local
    Chromium.
    """
    key = settings.hyperbrowser_api_key
    if not key:
        return None
    import httpx
    try:
        payload = {
            "useStealth": True,
            "acceptCookies": True,
            "screen": {"width": 1440, "height": 900},
            "timeoutMinutes": HYPERBROWSER_TIMEOUT_MINUTES,
        }
        if HYPERBROWSER_USE_PROXY:
            payload["useProxy"] = True
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{HYPERBROWSER_API_BASE}/session",
                headers={"x-api-key": key, "Content-Type": "application/json"},
                json=payload,
            )
            # Free plan rejects useProxy with 402 — retry without it rather
            # than silently falling back to local Chromium.
            if resp.status_code == 402 and payload.get("useProxy"):
                print("[-] Hyperbrowser: plan does not allow proxies — retrying without useProxy.")
                payload.pop("useProxy", None)
                resp = await client.post(
                    f"{HYPERBROWSER_API_BASE}/session",
                    headers={"x-api-key": key, "Content-Type": "application/json"},
                    json=payload,
                )
            if resp.status_code != 200:
                print(f"[-] Hyperbrowser session creation failed: HTTP {resp.status_code} {resp.text[:200]}")
                return None
            data = resp.json()
            return {
                "id": data.get("id"),
                "ws_endpoint": data.get("wsEndpoint"),
                "live_url": data.get("liveUrl"),
            }
    except Exception as e:
        print(f"[-] Hyperbrowser unavailable ({e}) — falling back to local Chromium.")
        return None


async def stop_hyperbrowser_session(session_id: Optional[str]) -> bool:
    """Stop a managed session (idempotent per Hyperbrowser docs)."""
    if not session_id:
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                f"{HYPERBROWSER_API_BASE}/session/{session_id}/stop",
                headers={"x-api-key": settings.hyperbrowser_api_key},
            )
            return resp.status_code in (200, 202, 204)
    except Exception:
        return False


async def dismiss_cookie_banners(page) -> bool:
    """Click the most common cookie-consent buttons across all frames.

    Returns True when at least one banner button was clicked. Called after
    navigation and before interactions; it is idempotent and cheap.
    """
    if getattr(page, "_banners_dismissed", False):
        return False
    clicked = False
    for frame in page.frames:
        for text in BANNER_BUTTON_TEXTS:
            try:
                btn = frame.get_by_role("button", name=text, exact=False).first
                if await btn.count():
                    await btn.click(timeout=2500)
                    clicked = True
                    break
            except Exception:
                continue
    if clicked:
        try:
            await page.wait_for_timeout(600)
        except Exception:
            pass
    # Remember for this page object so we don't re-click on retries
    try:
        page._banners_dismissed = True
    except Exception:
        pass
    return clicked
"""Helpers shared by scripts/cowen/cowen_watch.py and scripts/podcast/podcast_watch.py.

Only code that was identical in both watchers lives here: the YouTube GET helper (same headers,
timeout and retry/backoff), the JSON tree walker, the duration-badge regex, the UTC timestamp,
secret masking, and the webhook POST transport. Payload shapes, state files and log lines stay in
each watcher.
"""
import json, os, re, time, urllib.request, urllib.error
from datetime import datetime, timezone

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
DUR_RE = re.compile(r"^\d{1,2}(:\d{2}){1,2}$")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def http_get(url, tries=3, no_redirect=False, ua=UA):
    """GET with browser-like headers; retries with 1s, 2s, 4s... backoff.
    no_redirect=True returns (3xx code, "") or (404, "") instead of following/retrying (Shorts check)."""
    opener = urllib.request.build_opener(NoRedirect) if no_redirect else urllib.request.build_opener()
    last = None
    for i in range(tries):
        if _forced_fail(url):
            last = RuntimeError("HTTP Error 404 (forced by YT_FORCE_FAIL)")
            time.sleep(0 if i + 1 == tries else 1)
            continue
        req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9",
                                                   "Cookie": "CONSENT=YES+1; SOCS=CAI"})
        try:
            with opener.open(req, timeout=20) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if no_redirect and e.code in (301, 302, 303, 307, 308):
                return e.code, ""
            last = e
            if e.code == 404 and no_redirect:
                return 404, ""
        except Exception as e:  # noqa
            last = e
        if i + 1 < tries:
            time.sleep(2 ** i)
    raise RuntimeError(f"GET failed {url}: {last}")


INITIAL_DATA_RE = re.compile(r"var ytInitialData = (\{.*?\});</script>", re.S)
UA_ALT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
HEALTH_FAIL_LIMIT = 6


def _forced_fail(url):
    """Test-only hook, default off: YT_FORCE_FAIL=comma-separated substrings; matching URLs fail as if
    YouTube returned a page without ytInitialData / an HTTP error. Never set in the workflows."""
    pats = [p for p in os.environ.get("YT_FORCE_FAIL", "").split(",") if p]
    return any(p in url for p in pats)


def fetch_initial_data(url, attempts=3, delay=4):
    """GET a YouTube page and return (html, parsed ytInitialData). On a missing ytInitialData or an HTTP
    error, retries up to attempts-1 more times after `delay` seconds, varying hl=en and the User-Agent."""
    last = None
    for i in range(attempts):
        u = url if i == 0 else url + ("&" if "?" in url else "?") + "hl=en&gl=US"
        try:
            if _forced_fail(url):
                raise RuntimeError("ytInitialData not found (forced by YT_FORCE_FAIL)")
            _, page = http_get(u, tries=1 if i else 2, ua=UA_ALT if i == 2 else UA)
            m = INITIAL_DATA_RE.search(page)
            if not m:
                raise RuntimeError(f"ytInitialData not found on page (len={len(page)})")
            return page, json.loads(m.group(1))
        except Exception as e:  # noqa
            last = e
            if i + 1 < attempts:
                print(f"  fetch attempt {i + 1}/{attempts} failed ({e}); retrying in {delay}s")
                time.sleep(delay)
    raise RuntimeError(f"{last} (after {attempts} attempts)")


class Health:
    """Consecutive-failure counters per source, kept in a small JSON file next to the seen state.
    The file is only rewritten when a counter actually changes (no commit on every healthy run)."""

    def __init__(self, path):
        self.path = path
        try:
            with open(path) as f:
                self.data = json.load(f)
        except Exception:
            self.data = {}
        self.data.setdefault("sources", {})
        self.changed = False

    def ok(self, src):
        cur = self.data["sources"].get(src)
        if cur and cur.get("consecutive_failures", 0):
            self.data["sources"][src] = {"consecutive_failures": 0, "last_ok": now_utc()}
            self.changed = True

    def fail(self, src, err):
        cur = self.data["sources"].setdefault(src, {"consecutive_failures": 0})
        cur["consecutive_failures"] = cur.get("consecutive_failures", 0) + 1
        cur["last_failure"] = now_utc()
        cur["last_error"] = str(err)[:200]
        self.changed = True
        n = cur["consecutive_failures"]
        print(f"::warning::{src}: fetch failed ({err}); consecutive failures {n}/{HEALTH_FAIL_LIMIT}")
        return n

    def broken(self):
        return [k for k, v in self.data["sources"].items() if v.get("consecutive_failures", 0) >= HEALTH_FAIL_LIMIT]

    def save(self):
        if not self.changed:
            return
        self.data["updated"] = now_utc()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
            f.write("\n")


def walk(o, key, out):
    if isinstance(o, dict):
        if key in o:
            out.append(o[key])
        for v in o.values():
            walk(v, key, out)
    elif isinstance(o, list):
        for x in o:
            walk(x, key, out)


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mask_secrets():
    # Belt and braces: GitHub already masks secrets, but register them explicitly too.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for name in ("COWEN_WEBHOOK_URL", "COWEN_WEBHOOK_KEY"):
            val = os.environ.get(name, "")
            if val:
                print(f"::add-mask::{val}")


def post_webhook(payload, user_agent):
    """POST payload as JSON to COWEN_WEBHOOK_URL with the bearer key. Returns the HTTP status (0 on network error)."""
    url = os.environ["COWEN_WEBHOOK_URL"]
    key = os.environ["COWEN_WEBHOOK_KEY"]
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:  # noqa
        print(f"  POST error: {type(e).__name__}")
        code = 0
    return code

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


def http_get(url, tries=3, no_redirect=False):
    """GET with browser-like headers; retries with 1s, 2s, 4s... backoff.
    no_redirect=True returns (3xx code, "") or (404, "") instead of following/retrying (Shorts check)."""
    opener = urllib.request.build_opener(NoRedirect) if no_redirect else urllib.request.build_opener()
    last = None
    for i in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
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
        time.sleep(2 ** i)
    raise RuntimeError(f"GET failed {url}: {last}")


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

#!/usr/bin/env python3
"""Ben Cowen new long-video watcher -> webhook notification.

Source 1 (primary): channel /videos tab (long uploads only; Shorts and streams live in other tabs).
  Only items with a real duration badge (e.g. 12:34) are kept -> skips LIVE / UPCOMING / PREMIERE.
Source 2 (fallback when the page fails): YouTube RSS (intermittently 404/500 since Dec 2025),
  Shorts filtered via /shorts/<id> (200 = Short, redirect = normal video).
Each new candidate is double-checked on its watch page (skip upcoming / live-now).

Env: COWEN_WEBHOOK_URL, COWEN_WEBHOOK_KEY (never printed), TEST_MODE (true/false), VIDEO_ID (optional).
State: state/cowen-seen.json (only modified in normal mode; a video is marked seen only after a 2xx POST).
"""
import json, os, re, sys, time, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

CHANNEL_ID = "UCRvqjQPSeaWn-uEx-w0XOIg"   # @benjaminjcowen
VIDEOS_URL = "https://www.youtube.com/@benjaminjcowen/videos"
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
STATE = os.path.join(os.path.dirname(__file__), "..", "..", "state", "cowen-seen.json")
SEED_EXTRA = ["H_JhpNaRPl8", "qWXGEl-ojx4", "-eKtrrN5P48", "se6UlLbDTEo",
              "aEd5onhPxUs", "FVcHFIPVI40", "s4U8s754p9w"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
MAX_POST_PER_RUN = 3
DUR_RE = re.compile(r"^\d{1,2}(:\d{2}){1,2}$")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def http_get(url, tries=3, no_redirect=False):
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


def from_videos_page():
    st, html = http_get(VIDEOS_URL)
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
    if not m:
        raise RuntimeError("ytInitialData not found on /videos page")
    data = json.loads(m.group(1))
    items = []
    walk(data, "lockupViewModel", items)
    vids = []
    for v in items:
        vid = v.get("contentId")
        if not vid or v.get("contentType", "LOCKUP_CONTENT_TYPE_VIDEO") != "LOCKUP_CONTENT_TYPE_VIDEO":
            continue
        try:
            title = v["metadata"]["lockupMetadataViewModel"]["title"]["content"]
        except Exception:
            title = ""
        badges = []
        walk(v, "thumbnailBadgeViewModel", badges)
        texts = [b.get("text", "") for b in badges if isinstance(b, dict)]
        is_long_vod = any(DUR_RE.match(t or "") for t in texts)
        vids.append({"video_id": vid, "title": title, "eligible": is_long_vod, "badges": texts})
    if not vids:
        raise RuntimeError("no videos parsed from /videos page")
    return vids


def from_rss():
    st, xml = http_get(RSS_URL, tries=4)
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    root = ET.fromstring(xml)
    vids = []
    for e in root.findall("a:entry", ns):
        vid = e.findtext("yt:videoId", namespaces=ns)
        title = e.findtext("a:title", namespaces=ns) or ""
        pub = e.findtext("a:published", namespaces=ns) or ""
        code, _ = http_get(f"https://www.youtube.com/shorts/{vid}", tries=2, no_redirect=True)
        vids.append({"video_id": vid, "title": title, "published": pub, "eligible": code != 200,
                     "badges": [f"shorts-check:{code}"]})
    return vids


def watch_info(vid):
    """Returns dict(published, upcoming, live_now, title) from the watch page (best effort)."""
    info = {"published": "", "upcoming": False, "live_now": False, "title": ""}
    try:
        _, html = http_get(f"https://www.youtube.com/watch?v={vid}", tries=2)
    except Exception as e:
        print(f"  watch page failed for {vid}: {e}")
        return info
    m = re.search(r'"(?:publishDate|uploadDate)":"([^"]+)"', html)
    if m:
        info["published"] = m.group(1)
    info["upcoming"] = '"isUpcoming":true' in html
    info["live_now"] = bool(re.search(r'"isLiveNow":true', html))
    m = re.search(r'<meta name="title" content="([^"]*)"', html)
    if m:
        import html as h
        info["title"] = h.unescape(m.group(1))
    return info


def rss_published(vid):
    """Fallback for 'published' when the watch page is bot-walled (RSS itself is flaky)."""
    try:
        _, xml = http_get(RSS_URL, tries=2)
        m = re.search(r"<yt:videoId>" + re.escape(vid) + r"</yt:videoId>.*?<published>([^<]+)</published>", xml, re.S)
        return m.group(1) if m else ""
    except Exception:
        return ""


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def oembed_title(vid):
    try:
        _, txt = http_get(f"https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v={vid}", tries=2)
        return json.loads(txt).get("title", "")
    except Exception:
        return ""


def post(payload):
    url = os.environ["COWEN_WEBHOOK_URL"]
    key = os.environ["COWEN_WEBHOOK_KEY"]
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": "cowen-watch-gha"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:  # noqa
        print(f"  POST error: {type(e).__name__}")
        code = 0
    print(f"  webhook POST video_id={payload['video_id']} test={payload['test']} -> HTTP {code}")
    return code


def load_state():
    if not os.path.exists(STATE):
        return None
    with open(STATE) as f:
        return json.load(f)


def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    s["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s["seen"] = sorted(set(s["seen"]))
    with open(STATE, "w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
        f.write("\n")


def get_candidates():
    try:
        vids = from_videos_page()
        print(f"source=/videos page, {len(vids)} items")
        return vids
    except Exception as e:
        print(f"/videos page failed ({e}); falling back to RSS")
    vids = from_rss()
    print(f"source=RSS, {len(vids)} items")
    return vids


def mask_secrets():
    # Belt and braces: GitHub already masks secrets, but register them explicitly too.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for name in ("COWEN_WEBHOOK_URL", "COWEN_WEBHOOK_KEY"):
            val = os.environ.get(name, "")
            if val:
                print(f"::add-mask::{val}")


def main():
    mask_secrets()
    test = os.environ.get("TEST_MODE", "false").lower() == "true"
    want = (os.environ.get("VIDEO_ID") or "").strip()

    if test:
        vid = want
        title = ""
        if not vid:
            vids = [v for v in get_candidates() if v["eligible"]]
            vid, title = vids[0]["video_id"], vids[0]["title"]
        info = watch_info(vid)
        payload = {"video_id": vid, "title": title or info["title"] or oembed_title(vid),
                   "url": f"https://www.youtube.com/watch?v={vid}",
                   "published": info["published"] or rss_published(vid), "detected_at": now_utc(), "test": True}
        print(f"TEST MODE: {payload['video_id']} | {payload['title']} (state untouched)")
        code = post(payload)
        return 0 if 200 <= code < 300 else 1

    vids = get_candidates()
    for v in vids[:10]:
        print(f"  {v['video_id']} eligible={v['eligible']} badges={v['badges']} | {v['title'][:70]}")
    state = load_state()
    if state is None:
        seen = set(SEED_EXTRA) | {v["video_id"] for v in vids}
        save_state({"seen": list(seen)})
        print(f"FIRST RUN: seeded state with {len(seen)} ids, nothing posted")
        return 0

    seen = set(state.get("seen", []))
    new = [v for v in vids if v["eligible"] and v["video_id"] not in seen]
    # Non-eligible items (Shorts/live/upcoming) are not marked seen: an upcoming premiere that
    # later becomes a normal VOD will still trigger once.
    if not new:
        print("no new long videos")
        return 0
    new.reverse()  # oldest first
    changed = False
    rc = 0
    for v in new[:MAX_POST_PER_RUN]:
        info = watch_info(v["video_id"])
        if info["upcoming"] or info["live_now"]:
            print(f"  skip {v['video_id']} (upcoming/live now), will retry later")
            continue
        payload = {"video_id": v["video_id"], "title": v["title"] or info["title"] or oembed_title(v["video_id"]),
                   "url": f"https://www.youtube.com/watch?v={v['video_id']}",
                   "published": v.get("published") or info["published"] or rss_published(v["video_id"]),
                   "detected_at": now_utc(), "test": False}
        print(f"NEW: {payload['video_id']} | {payload['title']}")
        code = post(payload)
        if 200 <= code < 300:
            seen.add(v["video_id"])
            changed = True
        else:
            rc = 1
    if changed:
        state["seen"] = list(seen)
        save_state(state)
    return rc


if __name__ == "__main__":
    sys.exit(main())

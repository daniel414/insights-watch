#!/usr/bin/env python3
"""Podcast new long-episode watcher -> one batched webhook notification.

Shows: All-In (@allin), a16z main channel (@a16z), Lex Fridman (@lexfridman; the clips channel is ignored).
Source: each channel's /videos tab (long uploads only; Shorts and streams live in other tabs).
Only items with a real duration badge are kept (skips LIVE / UPCOMING / PREMIERE), and the duration
must be at least the show's minimum (filters clips / trailers). New candidates are double-checked on
their watch page (skip upcoming / live-now).

Env: COWEN_WEBHOOK_URL, COWEN_WEBHOOK_KEY (never printed), TEST_MODE (true/false).
State: state/podcast-seen.json, per show. A show missing from state is seeded with everything currently
listed and nothing is posted for it. Episodes are marked seen only after a 2xx POST.
Test mode: POSTs the latest eligible episode of each show with test=true; state untouched.
"""
import html as htmlmod, json, os, re, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

SHOWS = [
    # key, display name, handle, channel id, minimum duration (minutes)
    {"key": "allin", "show": "All-In", "handle": "@allin", "channel_id": "UCESLZhusAkFfsNsApnjF_Cg", "min_min": 20},
    {"key": "a16z", "show": "a16z", "handle": "@a16z", "channel_id": "UC9cn0TuPq4dnbTY-CBsm8XA", "min_min": 30},
    {"key": "lexfridman", "show": "Lex Fridman", "handle": "@lexfridman", "channel_id": "UCSHZKyawb77ixDdsGog4iWA", "min_min": 20},
]
STATE = os.path.join(os.path.dirname(__file__), "..", "..", "state", "podcast-seen.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
MAX_PER_POST = 5
DUR_RE = re.compile(r"^\d{1,2}(:\d{2}){1,2}$")
CLIP_RE = re.compile(r"(#shorts\b|\bclip\b|\btrailer\b|\bteaser\b)", re.I)


def http_get(url, tries=3):
    last = None
    for i in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                                                   "Cookie": "CONSENT=YES+1; SOCS=CAI"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read().decode("utf-8", "replace")
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


def dur_seconds(t):
    s = 0
    for p in t.split(":"):
        s = s * 60 + int(p)
    return s


def list_videos(show):
    """Newest first. Each: video_id, title, seconds (0 if no duration badge), eligible."""
    _, page = http_get(f"https://www.youtube.com/{show['handle']}/videos")
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", page, re.S)
    if not m:
        raise RuntimeError("ytInitialData not found on /videos page")
    if show["channel_id"] not in page:
        raise RuntimeError("unexpected channel on /videos page")
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
        durs = [b.get("text", "") for b in badges if isinstance(b, dict) and DUR_RE.match(b.get("text", "") or "")]
        secs = dur_seconds(durs[0]) if durs else 0
        eligible = secs >= show["min_min"] * 60 and not CLIP_RE.search(title)
        vids.append({"video_id": vid, "title": title, "seconds": secs, "eligible": eligible})
    if not vids:
        raise RuntimeError("no videos parsed from /videos page")
    return vids


def to_utc(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return s


def watch_info(vid):
    info = {"published": "", "upcoming": False, "live_now": False, "title": ""}
    try:
        _, page = http_get(f"https://www.youtube.com/watch?v={vid}", tries=2)
    except Exception as e:
        print(f"  watch page failed for {vid}: {type(e).__name__}")
        return info
    m = re.search(r'"(?:publishDate|uploadDate)":"([^"]+)"', page)
    if m:
        info["published"] = to_utc(m.group(1))
    info["upcoming"] = '"isUpcoming":true' in page
    info["live_now"] = bool(re.search(r'"isLiveNow":true', page))
    m = re.search(r'<meta name="title" content="([^"]*)"', page)
    if m:
        info["title"] = htmlmod.unescape(m.group(1))
    return info


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def episode(show, v, info):
    return {"show": show["show"], "video_id": v["video_id"], "title": v["title"] or info["title"],
            "url": f"https://www.youtube.com/watch?v={v['video_id']}", "published": info["published"],
            "duration_s": v["seconds"]}


def post(episodes, test):
    url = os.environ["COWEN_WEBHOOK_URL"]
    key = os.environ["COWEN_WEBHOOK_KEY"]
    payload = {"kind": "podcast", "episodes": episodes, "detected_at": now_utc(), "test": test}
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": "podcast-watch-gha"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:  # noqa
        print(f"  POST error: {type(e).__name__}")
        code = 0
    print(f"  webhook POST episodes={len(episodes)} test={test} -> HTTP {code}")
    return code


def load_state():
    if not os.path.exists(STATE):
        return {"shows": {}}
    with open(STATE) as f:
        return json.load(f)


def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    s["updated"] = now_utc()
    for k in s["shows"]:
        s["shows"][k] = sorted(set(s["shows"][k]))
    with open(STATE, "w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
        f.write("\n")


def mask_secrets():
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for name in ("COWEN_WEBHOOK_URL", "COWEN_WEBHOOK_KEY"):
            val = os.environ.get(name, "")
            if val:
                print(f"::add-mask::{val}")


def main():
    mask_secrets()
    test = os.environ.get("TEST_MODE", "false").lower() == "true"
    state = load_state()
    state.setdefault("shows", {})
    changed, rc, pending = False, 0, []

    for show in SHOWS:
        try:
            vids = list_videos(show)
        except Exception as e:
            print(f"[{show['key']}] listing failed: {e}")
            rc = 1
            continue
        elig = [v for v in vids if v["eligible"]]
        print(f"[{show['key']}] {len(vids)} items, {len(elig)} eligible; latest: "
              + (f"{elig[0]['video_id']} {elig[0]['seconds'] // 60}min | {elig[0]['title'][:60]}" if elig else "-"))
        if test:
            if elig:
                pending.append((show, elig[0]))
            continue
        if show["key"] not in state["shows"]:
            state["shows"][show["key"]] = [v["video_id"] for v in vids]
            changed = True
            print(f"[{show['key']}] FIRST RUN: seeded {len(vids)} ids, nothing posted")
            continue
        seen = set(state["shows"][show["key"]])
        # Ineligible items (short clips / live / upcoming) are not marked seen: a premiere that later
        # becomes a normal VOD will still trigger once.
        new = [v for v in elig if v["video_id"] not in seen]
        new.reverse()  # oldest first
        for v in new:
            pending.append((show, v))

    batch = []
    for show, v in pending:
        if len(batch) >= MAX_PER_POST:
            print("  more episodes pending; they will be sent on the next run")
            break
        info = watch_info(v["video_id"])
        if not test and (info["upcoming"] or info["live_now"]):
            print(f"  skip {v['video_id']} (upcoming/live now), will retry later")
            continue
        batch.append((show, v, episode(show, v, info)))
        print(f"{'TEST' if test else 'NEW'}: [{show['key']}] {v['video_id']} | {v['title'][:70]}")

    if batch:
        code = post([b[2] for b in batch], test)
        if 200 <= code < 300:
            if not test:
                for show, v, _ in batch:
                    state["shows"][show["key"]].append(v["video_id"])
                changed = True
        else:
            rc = 1
    elif not test:
        print("no new long episodes")

    if changed and not test:
        save_state(state)
    return rc


if __name__ == "__main__":
    sys.exit(main())

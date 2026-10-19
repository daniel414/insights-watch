#!/usr/bin/env python3
"""Podcast new long-episode watcher -> one batched webhook notification.

Shows: All-In (@allin), a16z main channel (@a16z), Lex Fridman (@lexfridman; the clips channel is ignored).
Source: each channel's /videos tab (long uploads only; Shorts and streams live in other tabs).
Only items with a real duration badge are kept (skips LIVE / UPCOMING / PREMIERE), and the duration
must be at least the show's minimum (filters clips / trailers). New candidates are double-checked on
their watch page (skip upcoming / live-now).

Env: COWEN_WEBHOOK_URL, COWEN_WEBHOOK_KEY (never printed), TEST_MODE, DRY_RUN (true/false).
published: watch page -> public player endpoint -> approximate from the /videos tab's relative age
(published_source = watch | player | approx | none).
State: state/podcast-seen.json, per show. A show missing from state is seeded with everything currently
listed and nothing is posted for it. Episodes are marked seen only after a 2xx POST.
Test mode: POSTs the latest eligible episode of each show with test=true; state untouched.
"""
import html as htmlmod, json, os, re, sys, time, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common.yt import DUR_RE, HEALTH_FAIL_LIMIT, UA, Health, fetch_initial_data, http_get, mask_secrets, now_utc, post_webhook, walk  # noqa: E402

SHOWS = [
    # key, display name, handle, channel id, minimum duration (minutes)
    {"key": "allin", "show": "All-In", "handle": "@allin", "channel_id": "UCESLZhusAkFfsNsApnjF_Cg", "min_min": 20},
    {"key": "a16z", "show": "a16z", "handle": "@a16z", "channel_id": "UC9cn0TuPq4dnbTY-CBsm8XA", "min_min": 30},
    {"key": "lexfridman", "show": "Lex Fridman", "handle": "@lexfridman", "channel_id": "UCSHZKyawb77ixDdsGog4iWA", "min_min": 20},
]
STATE = os.path.join(os.path.dirname(__file__), "..", "..", "state", "podcast-seen.json")
HEALTH = os.path.join(os.path.dirname(__file__), "..", "..", "state", "podcast-health.json")
MAX_PER_POST = 5
CLIP_RE = re.compile(r"(#shorts\b|\bclip\b|\btrailer\b|\bteaser\b)", re.I)


def dur_seconds(t):
    s = 0
    for p in t.split(":"):
        s = s * 60 + int(p)
    return s


def list_videos(show):
    """Newest first. Each: video_id, title, seconds (0 if no duration badge), eligible."""
    page, data = fetch_initial_data(f"https://www.youtube.com/{show['handle']}/videos")
    if show["channel_id"] not in page:
        raise RuntimeError("unexpected channel on /videos page")
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
        parts = []
        walk(v, "metadataParts", parts)
        rel = next((p.get("text", {}).get("content", "") for mp in parts for p in mp
                    if isinstance(p, dict) and p.get("text", {}).get("content", "").endswith(" ago")), "")
        vids.append({"video_id": vid, "title": title, "seconds": secs, "eligible": eligible, "relative": rel})
    if not vids:
        raise RuntimeError("no videos parsed from /videos page")
    return vids


def to_utc(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return s


def innertube_published(vid):
    """Public player endpoint (no key); returns microformat publishDate even when the watch page is walled."""
    body = json.dumps({"context": {"client": {"clientName": "WEB", "clientVersion": "2.20261015.00.00",
                                              "hl": "en", "gl": "US"}}, "videoId": vid}).encode()
    req = urllib.request.Request("https://www.youtube.com/youtubei/v1/player?prettyPrint=false", data=body,
                                 method="POST", headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa
        print(f"  player endpoint failed for {vid}: {type(e).__name__}")
        return ""
    m = re.search(r'"(?:publishDate|uploadDate)":"([^"]+)"', txt)
    return to_utc(m.group(1)) if m else ""


def approx_from_relative(rel):
    """'3d ago' -> approximate UTC timestamp (used only when both exact sources fail)."""
    m = re.match(r"(\d+)\s*(minute|min|hour|hr|day|d|week|wk|w|month|mo)s?\b", rel or "")
    if not m:
        return ""
    n, u = int(m.group(1)), m.group(2)
    sec = {"minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400, "d": 86400,
           "week": 604800, "wk": 604800, "w": 604800, "month": 2592000, "mo": 2592000}[u]
    return datetime.fromtimestamp(time.time() - n * sec, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def watch_info(vid):
    info = {"published": "", "published_source": "", "upcoming": False, "live_now": False, "title": ""}
    page = ""
    try:
        _, page = http_get(f"https://www.youtube.com/watch?v={vid}", tries=2)
    except Exception as e:
        print(f"  watch page failed for {vid}: {type(e).__name__}")
    m = re.search(r'"(?:publishDate|uploadDate)":"([^"]+)"', page) or \
        re.search(r'itemprop="(?:datePublished|uploadDate)" content="([^"]+)"', page)
    if m:
        info["published"], info["published_source"] = to_utc(m.group(1)), "watch"
    else:
        print(f"  watch page had no date for {vid} (len={len(page)}, bot_wall={'not a bot' in page})")
        p = innertube_published(vid)
        if p:
            info["published"], info["published_source"] = p, "player"
    info["upcoming"] = '"isUpcoming":true' in page
    info["live_now"] = bool(re.search(r'"isLiveNow":true', page))
    m = re.search(r'<meta name="title" content="([^"]*)"', page)
    if m:
        info["title"] = htmlmod.unescape(m.group(1))
    return info


def episode(show, v, info):
    pub, src = info["published"], info["published_source"]
    if not pub:
        pub, src = approx_from_relative(v.get("relative", "")), "approx"
    print(f"  published={pub or '-'} source={src or 'none'}")
    return {"show": show["show"], "video_id": v["video_id"], "title": v["title"] or info["title"],
            "url": f"https://www.youtube.com/watch?v={v['video_id']}", "published": pub,
            "published_source": src or "none", "duration_s": v["seconds"]}


def post(episodes, test):
    payload = {"kind": "podcast", "episodes": episodes, "detected_at": now_utc(), "test": test}
    code = post_webhook(payload, "podcast-watch-gha")
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


def main():
    mask_secrets()
    test = os.environ.get("TEST_MODE", "false").lower() == "true"
    dry = os.environ.get("DRY_RUN", "false").lower() == "true"
    if dry:
        test = True
        print("DRY RUN: latest episode per show is resolved and printed; no POST, state untouched")
    state = load_state()
    state.setdefault("shows", {})
    changed, rc, pending = False, 0, []
    health = Health(HEALTH)

    for show in SHOWS:
        try:
            vids = list_videos(show)
        except Exception as e:
            print(f"[{show['key']}] listing failed: {e}")
            n = health.fail(show["key"], e)
            if n >= HEALTH_FAIL_LIMIT:
                print(f"::error::{show['key']}: listing failed {n} runs in a row")
                rc = 1
            continue
        health.ok(show["key"])
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

    if batch and dry:
        return rc
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
    if not test:
        health.save()
    return rc


if __name__ == "__main__":
    sys.exit(main())

"""Forward paper-tracking of a fixed candlestick-pattern rule set (see rules.py). Statistics research only.
Each run: fetch closed BTCUSDT 4h/1d klines (public, keyless), walk every bar closed since the last run in time order,
update the open record, log new triggers, then rewrite trades/summary files under paper/."""
import json, os, sys, time, hashlib, urllib.request, datetime as dt
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rules as R

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OUT = os.path.join(ROOT, "paper")
SYMBOL = "BTCUSDT"
TFS = ["4h", "1d"]
HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
TW = dt.timezone(dt.timedelta(hours=8))
TFMS = {"4h": 4 * 3600 * 1000, "1d": 86400 * 1000}
RULES_SHA = hashlib.sha256(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.py"), "rb").read()).hexdigest()

def tw(ms): return dt.datetime.fromtimestamp(ms / 1000, TW).strftime("%Y-%m-%d %H:%M")
def fr(x): return "-" if x is None else f"{x:+.3f}"

def fetch(tf, limit=1000):
    err = []
    for host in HOSTS:
        url = f"{host}/api/v3/klines?symbol={SYMBOL}&interval={tf}&limit={limit}"
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "paper-track/1.0"})
                rows = json.load(urllib.request.urlopen(req, timeout=20))
                now = int(time.time() * 1000)
                rows = [r for r in rows if int(r[6]) < now]  # closed bars only
                df = pd.DataFrame([[int(r[0]), int(r[6]), *map(float, r[1:5])] for r in rows],
                                  columns=["open_ms", "close_ms", "open", "high", "low", "close"])
                gaps = int((df.open_ms.diff().dropna() != TFMS[tf]).sum())
                print(f"[{tf}] {host}: {len(df)} closed bars, open time {tw(df.open_ms.iloc[0])} ~ {tw(df.open_ms.iloc[-1])} Taipei, gaps={gaps}")
                return R.add_atr(df.reset_index(drop=True)), host
            except Exception as e:
                err.append(f"{host}#{attempt}: {e}"); time.sleep(3 * (attempt + 1))
    raise SystemExit("all kline sources failed: " + " | ".join(err))

def load_jsonl(p):
    return [json.loads(x) for x in open(p) if x.strip()] if os.path.exists(p) else []

def write_jsonl(p, rows):
    with open(p, "w") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")

def step(df, sigs, st, trades, events, tf):
    """Walk bars with open_ms > st['last_open_ms'] in time order. Rule order mirrors rules.simulate():
    stop, then target, then body re-entry, then time exit at bar max_hold; a trigger is ignored while a record is
    open or on the bar the previous one closed; triggers with risk<=0 or risk/entry>25% are ignored."""
    mh = R.MAX_HOLD[tf]; o = df.open_ms.values; cm = df.close_ms.values; h, l, c = df.high.values, df.low.values, df.close.values
    by_bar = {}
    for s in sigs: by_bar.setdefault(s["i"], []).append(s)
    open_tr = next((t for t in trades if t["status"] == "open"), None)
    for p in range(len(df)):
        if o[p] <= st["last_open_ms"]: continue
        ct = tw(int(cm[p]) + 1)
        if open_tr:
            open_tr["bars_held"] += 1; side = open_tr["side"]; ex = None
            if (side == 1 and l[p] <= open_tr["stop"]) or (side == -1 and h[p] >= open_tr["stop"]): ex = (open_tr["stop"], "stop")
            elif (side == 1 and h[p] >= open_tr["target"]) or (side == -1 and l[p] <= open_tr["target"]): ex = (open_tr["target"], "tp")
            elif R.BODY_EXIT and ((side == 1 and c[p] < open_tr["zone_bot"]) or (side == -1 and c[p] > open_tr["zone_top"])): ex = (float(c[p]), "body")
            elif open_tr["bars_held"] >= mh: ex = (float(c[p]), "time")
            open_tr["last_bar_close_tw"] = ct; open_tr["last_close"] = float(c[p])
            if ex:
                e, risk = open_tr["entry"], open_tr["risk"]
                open_tr.update(status="closed", exit=ex[0], why=ex[1], exit_bar_open_ms=int(o[p]), exit_time_tw=ct,
                               R=side * (ex[0] - e) / risk - R.FEE * e / risk, ret=side * (ex[0] - e) / e - R.FEE)
                st["busy_until_ms"] = int(o[p])
                events.append(dict(t=ct, tf=tf, ev="exit", id=open_tr["id"], why=ex[1], R=round(open_tr["R"], 3)))
                open_tr = None
        for s in by_bar.get(p, []):
            e = float(c[p]); stp = float(s["stop"]); risk = abs(e - stp)
            base = dict(t=ct, tf=tf, side=s["side"], big_move=s["move"], close=e, stop=stp)
            if open_tr is not None or o[p] <= st["busy_until_ms"]:
                events.append(dict(base, ev="trigger_ignored", reason="record open")); continue
            if risk <= 0 or risk / e > R.MAX_RISK_FRAC:
                events.append(dict(base, ev="trigger_ignored", reason="risk filter")); continue
            tid = f"{tf}-{int(o[p])}"
            open_tr = dict(id=tid, tf=tf, symbol=SYMBOL, status="open",
                           signal_bar_open_tw=tw(int(o[p])), entry_time_tw=ct, signal_bar_open_ms=int(o[p]),
                           side=s["side"], big_move=s["move"], type="continuation" if s["side"] == s["move"] else "reversal",
                           entry=e, stop=stp, risk=risk, target=e + s["side"] * R.TP_R * risk,
                           zone_top=float(s["top"]), zone_bot=float(s["bot"]), max_hold=mh, bars_held=0, rules_sha256=RULES_SHA)
            trades.append(open_tr)
            events.append(dict(base, ev="trigger", id=tid, type=open_tr["type"], target=open_tr["target"]))
        st["last_open_ms"] = int(o[p])
    return trades

def self_check(df, sigs, tf):
    """Replay the step engine over the whole fetched window and compare with the batch reference simulator."""
    ref = [t for t in R.simulate(df, sigs, tp_r=R.TP_R, max_hold=R.MAX_HOLD[tf], fee=R.FEE) if t["complete"]]
    tr = [t for t in step(df, sigs, dict(last_open_ms=-1, busy_until_ms=-1), [], [], tf) if t["status"] == "closed"]
    a = {(int(df.open_ms[t["i"]]), t["why"], round(t["R"], 9)) for t in ref}
    b = {(t["signal_bar_open_ms"], t["why"], round(t["R"], 9)) for t in tr}
    ok = a == b
    print(f"[{tf}] self-check vs batch simulator on {len(df)} bars: ref={len(a)} step={len(b)} identical={ok}")
    if not ok: print("   only in ref:", sorted(a - b)[:5], " only in step:", sorted(b - a)[:5])
    return ok

def stats(ts):
    if not ts: return dict(n=0, win=None, avgR=None)
    r = np.array([t["R"] for t in ts])
    return dict(n=len(ts), win=round(float((r > 0).mean() * 100), 1), avgR=round(float(r.mean()), 3))

def fmt(s): return f"{s['n']} | {'-' if s['win'] is None else str(s['win']) + '%'} | {fr(s['avgR'])}"
def isowk(t):
    y, w, _ = dt.datetime.strptime(t["exit_time_tw"], "%Y-%m-%d %H:%M").isocalendar(); return f"{y}-W{w:02d}"

def summary(all_trades, st, checks):
    L = ["# paper-track 摘要（技術形態統計研究，非投資建議）", "",
         f"更新時間：{dt.datetime.now(TW).strftime('%Y-%m-%d %H:%M')}（台北時間）　規則檔 sha256：`{RULES_SHA[:16]}`", "",
         "R：以觸發K收盤到失效價的距離為 1 單位，已扣 0.06% 成本。continuation＝突破方向與大K同向，reversal＝反向。", ""]
    for tf in TFS:
        ts = [t for t in all_trades if t["tf"] == tf]; cl = [t for t in ts if t["status"] == "closed"]
        L += [f"## BTCUSDT {tf}", "",
              f"追蹤起點：{tw(st[tf]['start_open_ms'] + TFMS[tf])} 之後收線的K　最後處理：{tw(st[tf]['last_open_ms'] + TFMS[tf])} 收線　自我檢查：{'通過' if checks.get(tf) else '未通過'}", "",
              "| 範圍 | 筆數 | 勝率 | 平均 R |", "|---|---|---|---|",
              f"| 全部已結束 | {fmt(stats(cl))} |",
              f"| continuation | {fmt(stats([t for t in cl if t['type'] == 'continuation']))} |",
              f"| reversal | {fmt(stats([t for t in cl if t['type'] == 'reversal']))} |", ""]
        op = [t for t in ts if t["status"] == "open"]
        L += ["未結束紀錄：" + ("無" if not op else "; ".join(f"{t['id']}（{t['type']}，{t['bars_held']}/{t['max_hold']} 根）" for t in op)), ""]
        wk = {}
        for t in cl: wk.setdefault(isowk(t), []).append(t)
        L += ["### 每週（依結束時間，台北時間週一至週日）", "",
              "| 週 | 筆數 | 勝率 | 平均 R | continuation 筆數 / 平均R | reversal 筆數 / 平均R |", "|---|---|---|---|---|---|"]
        for k in sorted(wk, reverse=True):
            g = wk[k]; sc = stats([t for t in g if t["type"] == "continuation"]); sr = stats([t for t in g if t["type"] == "reversal"])
            L.append(f"| {k} | {fmt(stats(g))} | {sc['n']} / {fr(sc['avgR'])} | {sr['n']} / {fr(sr['avgR'])} |")
        if not wk: L.append("| （尚無已結束紀錄） | | | | | |")
        L.append("")
    L += ["## 回測參考（2017-08 ~ 2026-10，同一規則）", "", "| 範圍 | 筆數 | 勝率 | 平均 R |", "|---|---|---|---|",
          "| 4h 全部 | 330 | 38% | +0.13 |", "| 4h continuation | 151 | 42% | +0.23 |", "| 4h reversal | 179 | 35% | +0.04 |", "| 1d 全部 | 42 | 29% | -0.11 |", ""]
    open(os.path.join(OUT, "summary.md"), "w").write("\n".join(L))
    rows = [dict(week=isowk(t), tf=t["tf"], type=t["type"], R=t["R"]) for t in all_trades if t["status"] == "closed"]
    wdf = pd.DataFrame(rows, columns=["week", "tf", "type", "R"])
    if len(wdf):
        g = wdf.groupby(["week", "tf", "type"]).R.agg(n="count", win=lambda r: round((r > 0).mean() * 100, 1), avgR=lambda r: round(r.mean(), 3)).reset_index()
    else:
        g = pd.DataFrame(columns=["week", "tf", "type", "n", "win", "avgR"])
    g.to_csv(os.path.join(OUT, "weekly.csv"), index=False)

def main():
    os.makedirs(OUT, exist_ok=True)
    sp = os.path.join(OUT, "state.json")
    st = json.load(open(sp)) if os.path.exists(sp) else {}
    all_trades, checks = [], {}
    for tf in TFS:
        df, host = fetch(tf)
        sigs = R.signals(df, big_k=R.BIG_K, small_frac=R.SMALL_FRAC, wait=R.WAIT)
        checks[tf] = self_check(df, sigs, tf)
        tp = os.path.join(OUT, f"trades_{SYMBOL}_{tf}.jsonl"); ep = os.path.join(OUT, f"events_{SYMBOL}_{tf}.jsonl")
        trades = load_jsonl(tp); events = []
        if tf not in st:  # forward-only: start from the bar after the latest closed one
            last = int(df.open_ms.iloc[-1])
            st[tf] = dict(start_open_ms=last, last_open_ms=last, busy_until_ms=-1, rules_sha256=RULES_SHA)
            events.append(dict(t=tw(last + TFMS[tf]), tf=tf, ev="start", rules_sha256=RULES_SHA))
            print(f"[{tf}] tracking starts after the bar closing {tw(last + TFMS[tf])} Taipei")
        if st[tf].get("rules_sha256") != RULES_SHA:
            raise SystemExit(f"rules.py changed ({st[tf].get('rules_sha256')} != {RULES_SHA}); refusing to mix results")
        if int(df.open_ms.iloc[0]) > st[tf]["last_open_ms"] + TFMS[tf]:
            raise SystemExit(f"[{tf}] fetched window does not reach the last processed bar; aborting")
        before = st[tf]["last_open_ms"]
        trades = step(df, sigs, st[tf], trades, events, tf)
        st[tf].update(source=host, last_run_tw=dt.datetime.now(TW).strftime("%Y-%m-%d %H:%M"))
        print(f"[{tf}] processed {int((df.open_ms > before).sum())} new closed bar(s); events: {len(events)}")
        for e in events: print("   ", json.dumps(e, ensure_ascii=False))
        write_jsonl(tp, trades)
        with open(ep, "a") as f:
            for e in events: f.write(json.dumps(e, ensure_ascii=False) + "\n")
        all_trades += trades
    json.dump(st, open(sp, "w"), indent=1)
    summary(all_trades, st, checks)
    if not all(checks.values()): raise SystemExit("self-check failed")

if __name__ == "__main__":
    main()

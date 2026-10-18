# insights-watch

GitHub Actions watchers for new long-form YouTube episodes (Benjamin Cowen, plus the podcast watcher in `podcast-watch.yml`), each sending new items to a webhook, plus a research-only candlestick statistics tracker (`paper-track.yml`). All jobs run on GitHub-hosted runners.


## Triggers

The workflows have no GitHub `schedule:` trigger, because GitHub's scheduled runs were not firing reliably. They are started with `workflow_dispatch` (ref `main`, no inputs, so never in test mode) by the Cloudflare Worker `insights-watch-cron`, which has one cron trigger, `*/5 * * * *` (UTC):

| Workflow | When the Worker dispatches it (UTC) |
|---|---|
| `cowen-watch.yml` | every run (every 5 minutes) |
| `podcast-watch.yml` | minute :15 of every hour |
| `paper-track.yml` | 00:05, 04:05, 08:05, 12:05, 16:05, 20:05 |

- The Worker uses its secret `GH_DISPATCH_TOKEN`: a fine-grained token limited to this repository with Actions read/write. Its value lives only in the Worker secret. Never write it into this repo, a workflow, or a log.
- A failed dispatch is logged by the Worker and not retried; the next cron run tries again. Each workflow has its own `concurrency` group, and the watchers dedupe through `state/`, so an extra or overlapping run is harmless.
- Manual runs still work from the Actions tab (Run workflow) or with `gh workflow run <file>`, including the `test` / `video_id` / `dry_run` inputs.

## cowen-watch

Checks Benjamin Cowen's YouTube channel (`@benjaminjcowen`, channel id `UCRvqjQPSeaWn-uEx-w0XOIg`) every 5 minutes and sends new **long-form** videos (no Shorts, no live/upcoming streams) to a webhook.

- `scripts/cowen/cowen_watch.py`: reads the channel's `/videos` tab and falls back to the RSS feed. When the RSS fallback is used, Shorts are filtered out with a `/shorts/<id>` check.
- `state/cowen-seen.json`: video IDs that were already handled. The workflow commits it back with `[skip ci]`. A video is marked seen only after the webhook returns 2xx.
- `.github/workflows/cowen-watch.yml`: dispatched every 5 minutes by the Cloudflare Worker (see Triggers), or run manually with `test=true` (plus an optional `video_id`) to send a test payload without changing state.

The webhook URL and key are stored only in the repo secrets `COWEN_WEBHOOK_URL` and `COWEN_WEBHOOK_KEY`.

## podcast-watch

Checks three podcast channels every hour (`.github/workflows/podcast-watch.yml`, dispatched at :15 UTC by the Cloudflare Worker) and sends new **long-form** episodes to the same webhook as one batched POST (`kind: "podcast"`, at most 5 episodes per POST).

| Show | Channel | Minimum length |
|---|---|---|
| All-In | `@allin` (`UCESLZhusAkFfsNsApnjF_Cg`) | 20 min |
| a16z (main channel only) | `@a16z` (`UC9cn0TuPq4dnbTY-CBsm8XA`) | 30 min |
| Lex Fridman (clips channel ignored) | `@lexfridman` (`UCSHZKyawb77ixDdsGog4iWA`) | 20 min |

- `scripts/podcast/podcast_watch.py`: reads each channel's `/videos` tab. Shorts, live/upcoming streams, videos below the minimum length and titles marked as clips/trailers are skipped.
- `state/podcast-seen.json`: episode IDs per show. A show that is not in the state yet is seeded with all current episodes and nothing is sent. An episode is marked seen only after the webhook returns 2xx.
- Manual run with `test=true` sends the latest episode of each show with `test: true` and does not change the state. `dry_run=true` only prints what would be sent.
- `published` comes from the watch page, then the public player endpoint, then an estimate from the `/videos` tab's relative age ("3 hours ago"); `published_source` says which (`watch`, `player`, `approx`). YouTube often bot-walls GitHub runners, so the receiver should fill the exact publish time itself.

## paper-track

技術形態統計研究，非投資建議。

- `.github/workflows/paper-track.yml`：每次 4 小時K收線後約 5 分鐘由 Cloudflare Worker 觸發（UTC 00/04/08/12/16/20 點 :05，即台灣時間 08/12/16/20/00/04 點 :05），也可手動執行，抓 Binance 公開K線（`data-api.binance.vision`，免金鑰），只統計收線後的K線，不對外發送任何東西。漏跑時下一次會自動補處理。
- `scripts/paper/rules.py`：固定的形態規則與參數，不再修改；檔案的 sha256 會記在每筆紀錄與 `paper/state.json`，檔案變動時程式會拒絕執行。
- `scripts/paper/paper_track.py`：逐根處理新收線的K，記錄觸發與結果；每次執行都會用整段視窗重播，和批次模擬比對結果是否一致（self-check）。
- `paper/trades_*.jsonl`（每筆紀錄與目前狀態）、`paper/events_*.jsonl`（逐筆事件，只增不改）、`paper/summary.md`（累計與每週統計）、`paper/weekly.csv`。

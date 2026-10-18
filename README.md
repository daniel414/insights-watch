# insights-watch

GitHub Actions watchers for new long-form YouTube episodes (Benjamin Cowen, plus the podcast watcher in `podcast-watch.yml`), each sending new items to a webhook. Scheduled runs use GitHub-hosted runners, and GitHub may start them a few minutes late.

## cowen-watch

Checks Benjamin Cowen's YouTube channel (`@benjaminjcowen`, channel id `UCRvqjQPSeaWn-uEx-w0XOIg`) every 5 minutes with GitHub Actions and sends new **long-form** videos (no Shorts, no live/upcoming streams) to a webhook.

- `scripts/cowen/cowen_watch.py`: reads the channel's `/videos` tab and falls back to the RSS feed. When the RSS fallback is used, Shorts are filtered out with a `/shorts/<id>` check.
- `state/cowen-seen.json`: video IDs that were already handled. The workflow commits it back with `[skip ci]`. A video is marked seen only after the webhook returns 2xx.
- `.github/workflows/cowen-watch.yml`: runs on a schedule, or manually with `test=true` (plus an optional `video_id`) to send a test payload without changing state.

The webhook URL and key are stored only in the repo secrets `COWEN_WEBHOOK_URL` and `COWEN_WEBHOOK_KEY`.

## podcast-watch

Checks three podcast channels every hour (`.github/workflows/podcast-watch.yml`, cron `17 * * * *`) and sends new **long-form** episodes to the same webhook as one batched POST (`kind: "podcast"`, at most 5 episodes per POST).

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

- `.github/workflows/paper-track.yml`：每次 4 小時K收線後約 5 分鐘執行（cron `5 0,4,8,12,16,20 * * *`，UTC），抓 Binance 公開K線（`data-api.binance.vision`，免金鑰），只統計收線後的K線，不對外發送任何東西。漏跑時下一次會自動補處理。
- `scripts/paper/rules.py`：固定的形態規則與參數，不再修改；檔案的 sha256 會記在每筆紀錄與 `paper/state.json`，檔案變動時程式會拒絕執行。
- `scripts/paper/paper_track.py`：逐根處理新收線的K，記錄觸發與結果；每次執行都會用整段視窗重播，和批次模擬比對結果是否一致（self-check）。
- `paper/trades_*.jsonl`（每筆紀錄與目前狀態）、`paper/events_*.jsonl`（逐筆事件，只增不改）、`paper/summary.md`（累計與每週統計）、`paper/weekly.csv`。

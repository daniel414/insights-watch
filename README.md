# cowen-watch

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
- Manual run with `test=true` sends the latest episode of each show with `test: true` and does not change the state.

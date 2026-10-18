# cowen-watch

Checks Benjamin Cowen's YouTube channel (`@benjaminjcowen`, channel id `UCRvqjQPSeaWn-uEx-w0XOIg`) every 15 minutes with GitHub Actions and sends new **long-form** videos (no Shorts, no live/upcoming streams) to a webhook.

- `scripts/cowen/cowen_watch.py`: reads the channel's `/videos` tab and falls back to the RSS feed. When the RSS fallback is used, Shorts are filtered out with a `/shorts/<id>` check.
- `state/cowen-seen.json`: video IDs that were already handled. The workflow commits it back with `[skip ci]`. A video is marked seen only after the webhook returns 2xx.
- `.github/workflows/cowen-watch.yml`: runs on a schedule, or manually with `test=true` (plus an optional `video_id`) to send a test payload without changing state.

The webhook URL and key are stored only in the repo secrets `COWEN_WEBHOOK_URL` and `COWEN_WEBHOOK_KEY`.

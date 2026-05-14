# Telegram Miles Transfer Bot

Small Python watcher for transfer-bonus promos that mention `LATAM`, `Azul`, or `Livelo`, with alerts sent to a Telegram chat.

## What It Does

- Polls RSS and Atom feeds.
- Can also scan narrower HTML section pages when a site does not expose a focused feed.
- Filters for posts that mention your tracked programs plus both transfer language and bonus language.
- Excludes known non-promo status/update wording such as bonus-crediting posts.
- Tries to confirm matched posts against official LATAM / Azul / Livelo links found in the article before alerting.
- Deduplicates alerts so the same article is sent once, even if it appears in overlapping sources.
- Sends a compact Telegram message with the title, source, publish time, summary, article link, and first confirmed official link when available.

## Files

- `miles_transfer_bot.py`: the watcher.
- `config.json`: source list and keyword filters.
- `.miles_transfer_state.json`: persisted seen-item state.
- `.github/workflows/miles-transfer-bot.yml`: scheduled GitHub Actions workflow.
- `tests/test_miles_transfer_bot.py`: regression tests for parsing, filtering, dedup, and confirmation logic.

## Setup

1. Edit `config.json`.
2. Create a Telegram bot with [@BotFather](https://t.me/BotFather) and capture the bot token.
3. Start a chat with your bot or add it to a private group.
4. Get your chat id.
   - Easiest way: send a message to the bot, then open:
     `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   - Read the `chat.id` value from the response.
5. Export the credentials:

```bash
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"
```

6. Run a dry check:

```bash
python3 miles_transfer_bot.py --config config.json --dry-run
```

Optional sanity test:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

7. Run a real check:

```bash
python3 miles_transfer_bot.py --config config.json
```

## Keep It Running Locally

If you want to run it on your own machine instead of GitHub Actions, a simple loop still works:

```bash
python3 miles_transfer_bot.py --config config.json --loop-minutes 30
```

Or run it from `cron`. Example for every day at 9:00 AM and 5:00 PM UTC-3:

```cron
0 9,17 * * * cd /path/to/telegram-miles-bot && /usr/bin/env TELEGRAM_BOT_TOKEN="your_bot_token" TELEGRAM_CHAT_ID="your_chat_id" /usr/bin/python3 miles_transfer_bot.py --config config.json >> bot.log 2>&1
```

## Run It On GitHub Actions

This is the recommended setup if you do not want the bot tied to your laptop.

1. Put this folder in a GitHub repository.
2. Add these GitHub Actions secrets in the repo settings:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. Commit the workflow and the initial `.miles_transfer_state.json` file.
4. Push to GitHub.
5. In the Actions tab, run `Miles Transfer Bot` once manually to verify the Telegram delivery.

The workflow:

- runs every day at 9:00 AM and 5:00 PM UTC-3 (12:00 UTC and 20:00 UTC)
- can also be triggered manually
- executes the bot once
- commits `.miles_transfer_state.json` back to the repo only when it changes

That last step is what prevents duplicate alerts across runs on ephemeral GitHub runners.

Example setup flow:

```bash
cd /path/to/telegram-miles-bot
git init
git add .
git commit -m "Add miles transfer Telegram bot"
git branch -M main
git remote add origin git@github.com:YOUR_USER/YOUR_REPO.git
git push -u origin main
```

## Tuning

- Add or remove feed and HTML sources in `config.json`.
- Add narrower `kind: "html"` sources for stable category pages or sections.
- Tighten keywords if you only want `Livelo -> Azul` or `bank -> LATAM`.
- If a source becomes noisy, remove it and replace it with a narrower feed.
- Add `negative_terms` for phrases that should never generate promo alerts.
- Set `max_item_age_days` to cap how old an unseen promo can be before the bot skips it.

Config notes:

- `kind` defaults to `feed`. Use `kind: "html"` for section/category pages.
- `link_include_patterns` lets HTML sources keep only article URLs that match your regex list.
- `title_cleanup_patterns` can strip author/time suffixes from HTML section-page links.
- `official_link_patterns` controls which official domains are treated as second-stage confirmation candidates.
- `negative_terms` blocks recurring false-positive wording.
- `max_item_age_days` defaults to `7`; set it to `0` to disable age-based skipping.

## Notes

- This version supports both RSS/Atom feeds and HTML section pages.
- Network access is required at runtime both for the feeds and the Telegram Bot API.
- SMS can be added later, but Telegram keeps this version simpler and free.
- GitHub Actions schedules are not exact to the minute. The `12:00 UTC` and `20:00 UTC` cron runs usually start close to those times, but not as a hard real-time SLA.

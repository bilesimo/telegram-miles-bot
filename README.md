# Telegram Miles Transfer Bot

Small Python watcher for transfer-bonus promos that mention `LATAM`, `Azul`, or `Livelo`, with alerts sent to a Telegram chat.

## What It Does

- Polls RSS feeds.
- Can also scan narrower HTML section pages when a site does not expose a focused feed.
- Filters for posts that mention your tracked programs plus both transfer language and bonus language.
- Tries to confirm matched posts against official LATAM / Azul / Livelo links found in the article before alerting.
- Deduplicates alerts so the same post is sent once.
- Sends a compact Telegram message with the title, source, publish time, summary, article link, and first confirmed official link when available.

## Files

- `miles_transfer_bot.py`: the watcher.
- `config.example.json`: example source list and keyword filters.
- `.miles_transfer_state.json`: persisted seen-item state.
- `.github/workflows/miles-transfer-bot.yml`: scheduled GitHub Actions workflow.

## Setup

1. Copy `config.example.json` to `config.json`.
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

## Keep It Running

Run every 30 minutes in a loop:

```bash
python3 miles_transfer_bot.py --config config.json --loop-minutes 30
```

Or run it from `cron` every 15 or 30 minutes. Example:

```cron
*/30 * * * * cd /path/to/telegram-miles-bot && /usr/bin/env TELEGRAM_BOT_TOKEN="your_bot_token" TELEGRAM_CHAT_ID="your_chat_id" /usr/bin/python3 miles_transfer_bot.py --config config.json >> bot.log 2>&1
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

- runs every day at 9:00 AM UTC-3 (12:00 UTC)
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

- Add or remove RSS feeds in `config.json`.
- Add narrower `kind: "html"` sources for stable category pages or sections.
- Tighten keywords if you only want `Livelo -> Azul` or `bank -> LATAM`.
- If a source becomes noisy, remove it and replace it with a narrower feed.

Config notes:

- `kind` defaults to `feed`. Use `kind: "html"` for section/category pages.
- `link_include_patterns` lets HTML sources keep only article URLs that match your regex list.
- `official_link_patterns` controls which official domains are treated as second-stage confirmation candidates.

## Notes

- This version assumes the sources expose RSS or Atom feeds.
- Network access is required at runtime both for the feeds and the Telegram Bot API.
- SMS can be added later, but Telegram keeps this version simpler and free.
- GitHub Actions schedules are not exact to the minute. A daily `12:00 UTC` cron usually runs close to that time, but not as a hard real-time SLA.

# Amsterdam House Bot

Telegram bot for Amsterdam rental hunting. It scans rental sites, sends matching listings, and can auto-reply where configured.

## What It Does

- Scans Kamernet, Funda, Pararius, Roofz, and Huurwoningen.
- Sends Telegram notifications for new listings.
- Supports per-site notification toggles.
- Supports auto-replies for Kamernet, Funda, Pararius, and Roofz.
- Completes Roofz pre-applications and full OSRE applications through API first, with browser fallback.
- Tracks operational events, reply latency, and confirmation latency in SQLite.
- Prunes operational logs older than 3 days.

## Repository Layout

```text
housebot/                 Application code
housebot/scrapers/        Site scrapers and listing parsing
scripts/                  Local testing, mailbox, session, and deploy helpers
tests/                    Unit and regression tests
deploy/                   systemd service file
cloudflare-mailbox-worker/ Cloudflare mailbox API worker
assets/                   Reference/debug assets
main.py                   Entrypoint
```

Runtime files such as `.env`, `listings.db`, saved browser sessions, and reply-message text files are intentionally kept out of git.

## Telegram Commands

- `/start` - initialize your filters
- `/help` - show command list
- `/search` - change rent, bedrooms, size, and Kamernet property type filters
- `/filters` - show active filters
- `/sources status` - show enabled notification sites
- `/sources on SITE`, `/sources off SITE`, `/sources only SITE`, `/sources all` - change notification sites
- `/autoreply status`, `/autoreply on`, `/autoreply off` - control auto-replies
- `/status` - show scan health, source state, and queue state
- `/logs` - show recent operational events
- `/test` - run a scan now
- `/pause` / `/resume` - pause or resume notifications
- `/clear` - clear sent-listing history
- `/cancel` - cancel setup

## Configuration

Configuration is loaded from `.env` locally and from `/etc/amsterdam-house-bot/bot.env` in production.

Keep these categories in `.env`:

- Telegram bot token and allowed chat IDs
- Scan intervals and source toggles
- Site credentials and saved-session paths
- Auto-reply enable/dry-run switches
- Reply messages and applicant profile data
- Mailbox provider settings
- Roofz OSRE application document paths

Do not commit real `.env` files, browser session files, databases, reply text files, or personal documents.

## Local Setup

Install dependencies:

```powershell
uv sync --locked
python -m playwright install chromium
```

Run tests:

```powershell
python -m unittest discover -s tests -v
```

Run the bot locally:

```powershell
python main.py
```

## Useful Scripts

Create or refresh browser sessions:

```powershell
python scripts/kamernet_save_session.py
python scripts/pararius_save_session.py
```

Test one auto-reply flow:

```powershell
python scripts/test_kamernet_reply.py <listing-url>
python scripts/test_funda_reply.py <listing-url>
python scripts/test_pararius_reply.py <listing-url>
python scripts/test_roofz_reply.py <listing-url>
```

Open the housing mailbox viewer:

```powershell
python scripts/mailbox_viewer.py
```

Check Roofz mailbox/pre-application emails:

```powershell
python scripts/check_mailtm_preapplications.py --include-seen
```

## Deployment

Deploy to the existing DigitalOcean droplet:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy.ps1 -DropletHost 134.122.56.43
```

The deploy script:

- Packages the project without local secrets/data.
- Uploads code to `/opt/amsterdam-house-bot`.
- Uploads `.env` as `/etc/amsterdam-house-bot/bot.env`.
- Copies configured Roofz documents into `/var/lib/amsterdam-house-bot/roofz-documents`.
- Keeps the production DB at `/var/lib/amsterdam-house-bot/listings.db`.
- Restarts only the `amsterdam-house-bot` systemd service.

After deploy:

```bash
ssh root@134.122.56.43 "systemctl status amsterdam-house-bot --no-pager"
ssh root@134.122.56.43 "journalctl -u amsterdam-house-bot -f"
```

The trading Docker container on the droplet is separate. Check it with:

```bash
ssh root@134.122.56.43 "docker ps"
```

## Production Files

- App: `/opt/amsterdam-house-bot`
- Env: `/etc/amsterdam-house-bot/bot.env`
- DB: `/var/lib/amsterdam-house-bot/listings.db`
- Roofz documents: `/var/lib/amsterdam-house-bot/roofz-documents`
- Service: `amsterdam-house-bot`

## Operational Notes

- Use `/status` first when checking health.
- Use `/logs` for recent structured events.
- Source-specific failures are logged in `bot_events`.
- Auto-reply state is stored in `auto_replies`.
- Roofz full-application emails must arrive in the configured housing mailbox. The bot does not read Gmail directly.
- Kamernet may require manual verification; those cases are reported in Telegram.
- Pararius may be blocked by Cloudflare in headless production; browser/session quality matters.

## Development Notes

- Keep code under `housebot/`.
- Keep scrapers under `housebot/scrapers/`.
- Add regression tests for every scraper/parser/reply bug.
- Prefer API implementations with browser fallback only where needed.
- Keep heavy scan behavior intact unless explicitly changing scan strategy.

Before deploying, always run:

```powershell
python -m compileall -q housebot scripts tests main.py
python -m unittest discover -s tests -v
```

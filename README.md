# Amsterdam House Bot

Telegram bot that scans Amsterdam rental listings and sends a message when a new listing matches a user's filters.

Supported sources:

- Pararius
- Funda
- Kamernet
- Huurwoningen
- Roofz

The bot stores user filters and already-seen listings in SQLite, so duplicate listings are not sent twice.

## What it does

- Runs a scheduled scan every `POLL_INTERVAL_SECONDS` seconds
- Lets each Telegram user save their own Kamernet property types, rent, bedroom/room, and surface-area filters
- Lets each Telegram user toggle Kamernet/Funda/Roofz auto-replies on or off
- Sends new listings directly in Telegram
- Supports an on-demand scan with `/test`
- Stores operational events in SQLite and keeps the last 3 days for debugging

## Prerequisites

- Python 3.13.7, managed by `uv`
- `uv` 0.8.15 for local development
- A Telegram bot token from BotFather

## Setup From Zero

### 1. Open the project

If you already have the folder locally, just open it in VS Code or your terminal.

### 2. Install dependencies

Use [uv](https://docs.astral.sh/uv/) from the project root. The lockfile is part of the supply-chain protection for this bot, so install with `--locked`:

```bash
uv sync --locked
```

Activate the virtual environment if you want to run commands manually:

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source .venv/bin/activate
```

### 3. Install browser drivers

Roofz requires browser automation. Run this once after installing dependencies:

```bash
# For Roofz (Playwright)
playwright install chromium
```

### 4. Create the environment file

Create a `.env` file in the project root with the following content:

```env
TELEGRAM_TOKEN=123456789:replace-with-your-real-token
POLL_INTERVAL_SECONDS=300
DB_PATH=listings.db
TELEGRAM_ALLOWED_CHAT_IDS=123456789
```

Environment variables:

- `TELEGRAM_TOKEN`: required, Telegram bot token from BotFather
- `POLL_INTERVAL_SECONDS`: optional, scan interval in seconds, defaults to `300`
- `DB_PATH`: optional, SQLite database path, defaults to `listings.db`
- `TELEGRAM_ALLOWED_CHAT_IDS`: optional, comma-separated Telegram chat IDs allowed to use the bot. Leave empty for local unrestricted use.

Kamernet auto-reply variables:

- `KAMERNET_AUTO_REPLY_ENABLED`: optional, set to `1` to reply to new matching Kamernet listings
- `KAMERNET_REPLY_DRY_RUN`: optional, defaults to `1`; keeps the bot from clicking the final send button
- `KAMERNET_EMAIL`: Kamernet login email, required for password login
- `KAMERNET_PASSWORD`: Kamernet password, required for password login; not needed when using a saved Kamernet session
- `KAMERNET_REPLY_MESSAGE`: message to send to landlords, required when auto-reply is enabled
- `KAMERNET_REPLY_MESSAGE_FILE`: optional path to a UTF-8 text file containing the reply message; useful for multiline messages
- `KAMERNET_REPLY_MAX_PER_SCAN`: optional, defaults to `0` for no cap; set a positive number to limit replies per scan
- `KAMERNET_EXPECTED_TENANCY_DURATION`: optional, defaults to `1 year` when Kamernet asks for planned stay
- `KAMERNET_EXPECTED_MOVE_DATE`: optional, defaults to `07/01/2026` for July 1, 2026 when Kamernet asks for move-in date
- `KAMERNET_API_REPLY_ENABLED`: optional, defaults to `1`; captures Kamernet's authenticated submit request in Playwright, aborts the browser send, and replays it with `httpx`; browser submit remains the fallback
- `KAMERNET_STORAGE_STATE_PATH`: optional, defaults beside `DB_PATH`; stores the Kamernet login session

Funda auto-reply variables:

- `FUNDA_AUTO_REPLY_ENABLED`: optional, set to `1` to reply to new matching Funda listings
- `FUNDA_REPLY_DRY_RUN`: optional, defaults to `KAMERNET_REPLY_DRY_RUN`; keeps the bot from submitting the final contact API request
- `FUNDA_EMAIL`: optional, defaults to `KAMERNET_EMAIL`
- `FUNDA_FIRST_NAME`: required when Funda auto-reply is enabled
- `FUNDA_LAST_NAME`: required when Funda auto-reply is enabled
- `FUNDA_PHONE_NUMBER`: required by Funda's contact form
- `FUNDA_REPLY_MESSAGE`: optional message override for Funda
- `FUNDA_REPLY_MESSAGE_FILE`: optional path to a UTF-8 text file containing the Funda reply message; defaults to the Kamernet message when unset
- `FUNDA_REPLY_MAX_PER_SCAN`: optional, defaults to `0` for no cap; set a positive number to limit replies per scan
- `FUNDA_CONTACT_API_BASE`: optional, defaults to `https://contacts-bff.funda.io`
- `FUNDA_CONFIRMATION_ENABLED`: optional, defaults to `1` when a mail.tm inbox is configured; checks for the forwarded Funda confirmation email after a live send
- `FUNDA_MAILTM_ADDRESS`: optional, defaults to `ROOFZ_MAILTM_ADDRESS`; mail.tm inbox where forwarded Funda emails arrive
- `FUNDA_MAILTM_PASSWORD`: optional, defaults to `ROOFZ_MAILTM_PASSWORD`
- `FUNDA_MAILTM_FORWARDER_ADDRESS`: optional, defaults to `FUNDA_EMAIL`; use this when Gmail forwards messages into mail.tm
- `FUNDA_CONFIRMATION_POLL_SECONDS`: optional, defaults to `180`

Roofz auto-reply variables:

- `ROOFZ_AUTO_REPLY_ENABLED`: optional, set to `1` to reply to new matching Roofz listings
- `ROOFZ_REPLY_DRY_RUN`: optional, defaults to `KAMERNET_REPLY_DRY_RUN`; keeps the bot from submitting the final contact API request
- `ROOFZ_EMAIL`: optional, defaults to `KAMERNET_EMAIL`; this is the email sent to Roofz in the contact request
- `ROOFZ_FIRST_NAME`: required when Roofz auto-reply is enabled
- `ROOFZ_LAST_NAME`: required when Roofz auto-reply is enabled
- `ROOFZ_PHONE_NUMBER`: required by Roofz's contact form
- `ROOFZ_REPLY_MESSAGE`: optional message override for Roofz
- `ROOFZ_REPLY_MESSAGE_FILE`: optional path to a UTF-8 text file containing the Roofz reply message; defaults to the Funda/Kamernet message when unset
- `ROOFZ_REPLY_MAX_PER_SCAN`: optional, defaults to `0` for no cap; set a positive number to limit replies per scan
- `ROOFZ_MAILTM_ADDRESS`: mail.tm inbox address where forwarded Roofz pre-application emails arrive
- `ROOFZ_MAILTM_PASSWORD`: mail.tm password for the official mail.tm API
- `ROOFZ_MAILTM_FORWARDER_ADDRESS`: optional, defaults to `ROOFZ_EMAIL`; use this when Gmail forwards messages into mail.tm
- `ROOFZ_PREAPPLICATION_ENABLED`: optional, set to `1` to poll mail.tm for Roofz pre-application links, complete the OSRE form, and check for a confirmation email after the first contact request
- `ROOFZ_PREAPPLICATION_API_ENABLED`: optional, defaults to `1`; resolves OSRE email links and submits the pre-application through the OSRE API before falling back to browser automation
- `ROOFZ_OSRE_PREAPPLICATION_API_URL`: optional, defaults to `https://relet.portal.prd.osre.eu/portal/applications/pre-application`
- `ROOFZ_OSRE_AVAILABILITY_API_BASE`: optional, defaults to `https://financial-check.portal.prd.osre.eu/portal/financial-check/check-availability`
- `ROOFZ_BIRTH_DATE`: required when pre-applications are enabled; use `DD-MM-YYYY`
- `ROOFZ_INITIALS`: optional, defaults to `N.G.`
- `ROOFZ_RENT_TOGETHER`: optional, defaults to `0`
- `ROOFZ_CURRENT_LIVING_SITUATION`: optional, defaults to `Single without children`
- `ROOFZ_WORK_SITUATION`: optional, defaults to `Student`
- `ROOFZ_MONTHLY_INCOME`: required when pre-applications are enabled; gross monthly income for OSRE work/income questions
- `ROOFZ_ANNUAL_INCOME`, `ROOFZ_SAVINGS`: optional values used if the OSRE pre-application asks annual income or savings questions

### 5. Start the bot

```bash
python main.py
```

Expected startup message:

```text
Bot started. Press Ctrl+C to stop.
```

On first boot the bot automatically creates the SQLite database and its tables.

## First Use In Telegram

1. Open your bot in Telegram.
2. Send `/start`.
3. Send `/search` to configure:
   - Kamernet property types. Tap one or more types, then tap `Done`.
   - max monthly rent
   - minimum bedrooms/rooms
   - minimum surface area in square meters
4. Send `/test` to trigger an immediate scan.

After that, the scheduled scanner will keep running in the background while the process stays alive. Send `/autoreply on` if you want the bot to answer new matching Kamernet, Funda, and Roofz listings automatically, and `/autoreply off` to disable replies while keeping Telegram notifications.

## Available Commands

- `/start` - initialize the bot and show help
- `/help` - show available commands
- `/search` - save or update filters
- `/filters` - show current filters
- `/autoreply on|off|status` - control Kamernet/Funda/Roofz auto-replies
- `/logs` - show recent operational events from the SQLite event log
- `/test` - run a scan immediately
- `/pause` - pause notifications
- `/resume` - resume notifications
- `/clear` - clear the seen listings database
- `/cancel` - cancel the filter setup flow

## How the bot works

1. `main.py` starts the Telegram application.
2. `bot.py` registers commands and schedules the recurring scan job.
3. `scanner.py` runs all scrapers for each active user.
4. `db.py` stores filters, deduplicates listings, records operational events, and prunes event rows older than 3 days.

## Auto-Reply

Auto-reply needs both server setup and a Telegram toggle. Each source has its own server-side flag, and the user must send `/autoreply on`. When enabled, it only runs after a new listing has already matched your filters and the Telegram notification has been sent. It records attempts in SQLite so the same listing is not answered twice, even if multiple Telegram users match it.

Operational event logging writes scan starts/finishes, scraper results, new listings, notification sends, auto-reply decisions, auto-reply results, and Telegram warnings into the `bot_events` table. Use `/logs` in Telegram for a quick recent view, or inspect the SQLite table directly on the VPS. Rows older than 3 days are pruned on startup and scheduled scans.

Keep tokens, passwords, personal form data, and reply messages in `.env`. Do not commit real `.env` files, saved browser sessions, local databases, or reply-message text files. For VPS deployment, prefer inline `KAMERNET_REPLY_MESSAGE`, `FUNDA_REPLY_MESSAGE`, and `ROOFZ_REPLY_MESSAGE` values in `.env`; local `*_REPLY_MESSAGE_FILE` paths are not uploaded by the deploy script.

Keep dry-run enabled for the first real test:

```env
KAMERNET_AUTO_REPLY_ENABLED=1
KAMERNET_REPLY_DRY_RUN=1
KAMERNET_EMAIL=you@example.com
KAMERNET_REPLY_MESSAGE_FILE=kamernet_reply_message.txt
KAMERNET_REPLY_MAX_PER_SCAN=0
KAMERNET_EXPECTED_TENANCY_DURATION=1 year
KAMERNET_EXPECTED_MOVE_DATE=07/01/2026
KAMERNET_API_REPLY_ENABLED=1
```

If Kamernet rejects password login in headless browser mode, create a saved session once from a visible browser:

```bash
python scripts/kamernet_save_session.py
```

The script logs in with `KAMERNET_EMAIL` and `KAMERNET_PASSWORD` when they are set. It saves the Kamernet session to `KAMERNET_STORAGE_STATE_PATH`, and the bot reuses that session for dry-run and live replies. If the session expires, run the script again.

For VPS deployments, the saved Kamernet session is intentionally not committed or uploaded by `scripts/deploy.ps1`. Copy it to the production `KAMERNET_STORAGE_STATE_PATH` separately and keep it readable only by the bot service user, for example `/var/lib/amsterdam-house-bot/kamernet_storage_state.json` owned by `amsterdambot`.

Run a one-listing dry-run before enabling live sends:

```bash
python scripts/test_kamernet_reply.py "https://kamernet.nl/en/for-rent/studio-amsterdam/example/studio-1234567"
```

The expected successful dry-run status is `dry_run_ready`. That means the bot logged in, opened the contact form, found the message field, filled the message, and skipped submit. In live mode, the bot prefers the captured Kamernet API request and falls back to a normal browser submit if the API request is unavailable or rejected.

To allow a live send, set `KAMERNET_REPLY_DRY_RUN=0` and pass `--live` to the test script for one listing. The scanner also respects `KAMERNET_REPLY_DRY_RUN=0`, so only switch it after the one-listing live test behaves as expected.

Funda replies use the same contact API used by the website contact form, avoiding browser verification screens. Run a one-listing dry-run before enabling live sends:

```bash
python scripts/test_funda_reply.py --global-id 8013049 --office-id 60557 --url "https://www.funda.nl/detail/huur/amsterdam/appartement-john-blankensteinstraat-127-b/80822048/"
```

To allow a live Funda send, set `FUNDA_REPLY_DRY_RUN=0` and pass `--live` for one listing. When mail.tm is configured, the test waits for the forwarded Funda confirmation email:

```env
FUNDA_EMAIL=you@gmail.com
FUNDA_MAILTM_ADDRESS=you@example.com
FUNDA_MAILTM_PASSWORD=replace-with-mailtm-password
FUNDA_MAILTM_FORWARDER_ADDRESS=you@gmail.com
FUNDA_CONFIRMATION_ENABLED=1
```

You can also verify existing confirmations:

```bash
python scripts/check_funda_confirmations.py --title "John Blankensteinstraat 127-B"
```

The scanner also respects `FUNDA_REPLY_DRY_RUN=0`, so only switch it after the one-listing live test behaves as expected.

Roofz replies use the same contact API used by the listing page. Run a one-listing dry-run before enabling live sends:

```bash
python scripts/test_roofz_reply.py --url "https://www.roofz.eu/huur/woningen/jan-van-galenstraat-502"
```

To allow a live Roofz send, set `ROOFZ_REPLY_DRY_RUN=0` and pass `--live` for one listing. The contact request should use your normal Roofz email in `ROOFZ_EMAIL`. Roofz pre-applications use the official mail.tm API to read forwarded follow-up emails, resolve the OSRE application link, submit the OSRE API payload, and then wait for a confirmation email. If the OSRE API changes or rejects the request, the bot falls back to the browser form filler. Configure the mail.tm inbox and the required OSRE answers:

```env
ROOFZ_EMAIL=you@gmail.com
ROOFZ_MAILTM_ADDRESS=you@example.com
ROOFZ_MAILTM_PASSWORD=replace-with-mailtm-password
ROOFZ_MAILTM_FORWARDER_ADDRESS=you@gmail.com
ROOFZ_PREAPPLICATION_ENABLED=1
ROOFZ_PREAPPLICATION_API_ENABLED=1
ROOFZ_BIRTH_DATE=DD-MM-YYYY
ROOFZ_WORK_SITUATION=Student
ROOFZ_MONTHLY_INCOME=800
```

Then verify the mailbox:

```bash
python scripts/check_mailtm_preapplications.py --title "Jan van Galenstraat 502" --include-seen
```

## Project Structure

```text
.
|-- bot.py
|-- config.py
|-- db.py
|-- main.py
|-- pyproject.toml
|-- scanner.py
|-- scrapers/
|   |-- base.py
|   |-- funda.py
|   |-- huurwoningen.py
|   |-- kamernet.py
|   |-- pararius.py
|   `-- roofz.py
`-- uv.lock
```

## Troubleshooting

### `TELEGRAM_TOKEN not found`

Your `.env` file is missing or the token is empty.

### No listings are being sent

- Make sure you ran `/start` and `/search`
- Run `/test` to check whether listings are available right now
- Verify that your Kamernet property types, rent, bedroom/room, and size filters are not too restrictive

### I want to start fresh

Delete `listings.db`, or use `/clear` to clear previously seen listings.

## Run Without VS Code

The bot does not depend on VS Code. Any terminal is fine as long as the virtual environment is active and `.env` is configured.

## Run On A DigitalOcean VPS

The bot uses Telegram polling, so the VPS does not need a public web port for the bot. Keep SSH open for deployment and make sure the droplet can make outbound HTTPS requests.

This setup assumes:

- Ubuntu droplet
- SSH access as `root`
- Your local `.env` contains a valid `TELEGRAM_TOKEN`
- Your local `.env` contains `TELEGRAM_ALLOWED_CHAT_IDS` if the VPS bot should be private
- The VPS should start with a fresh SQLite database

### Deploy From Windows PowerShell

From the project root:

```powershell
.\scripts\deploy.ps1 -Host YOUR_DROPLET_IP
```

If your SSH key is not the default key:

```powershell
.\scripts\deploy.ps1 -Host YOUR_DROPLET_IP -IdentityFile C:\path\to\key
```

The deploy script uploads the project to `/opt/amsterdam-house-bot`, uploads `.env` to `/etc/amsterdam-house-bot/bot.env`, creates `/var/lib/amsterdam-house-bot/listings.db` on first boot, installs dependencies, and starts a `systemd` service.

The bootstrap pins `uv` to version `0.8.15`, verifies the downloaded binary checksum, installs Python `3.13.7`, and runs `uv sync --locked` so Python dependencies come from `uv.lock`. The local `.env`, `.venv`, `.git`, `__pycache__`, and local database files are excluded from the code archive. The `.env` file is uploaded separately as the service environment file. During VPS setup, any `DB_PATH=` value from `.env` is removed so production always uses `/var/lib/amsterdam-house-bot/listings.db` and deployments do not reset sent-listing history. If an older deploy created `/opt/amsterdam-house-bot/listings.db`, the bootstrap script migrates it before replacing app files.

### Manage The VPS Service

SSH into the droplet:

```bash
ssh root@YOUR_DROPLET_IP
```

Check status:

```bash
systemctl status amsterdam-house-bot
```

Follow logs:

```bash
journalctl -u amsterdam-house-bot -f
```

Restart the bot:

```bash
systemctl restart amsterdam-house-bot
```

Stop the bot:

```bash
systemctl stop amsterdam-house-bot
```

Show recent logs:

```bash
journalctl -u amsterdam-house-bot -n 100 --no-pager
```

### After Deployment

Open Telegram and send:

```text
/start
/search
/test
```

The bot will continue running after you close your terminal because `systemd` owns the process.

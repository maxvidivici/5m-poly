# Ubuntu Deployment

These commands deploy the bot in paper mode with Telegram reports every 15 minutes.
Run live only after paper mode has enough completed trades.

## 1. Server packages

```bash
sudo apt update
sudo apt install -y git python3.11 python3.11-venv python3-pip ca-certificates curl sqlite3
```

## 2. App user and code

Use SSH deploy keys for private GitHub repos. Do not paste GitHub PATs into shell history.

```bash
sudo useradd --system --create-home --home-dir /opt/edge-bot --shell /usr/sbin/nologin edgebot || true
sudo mkdir -p /opt/edge-bot
sudo chown -R "$USER:$USER" /opt/edge-bot

git clone <YOUR_REPO_URL> /opt/edge-bot
cd /opt/edge-bot
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
mkdir -p runtime
cp .env.example .env
```

## 3. Telegram bot

Create a bot with BotFather, send any message to the bot, then get your chat id:

```bash
export TELEGRAM_BOT_TOKEN='<BOT_TOKEN>'
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates"
```

Put the token and chat id into `/opt/edge-bot/.env`:

```bash
nano /opt/edge-bot/.env
```

Required paper/test settings:

```env
EDGE_MODE=paper
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=<BOT_TOKEN>
TELEGRAM_CHAT_ID=<CHAT_ID>
TELEGRAM_REPORT_INTERVAL_SEC=900
TELEGRAM_SEND_ON_START=true
```

## 4. Smoke tests

```bash
cd /opt/edge-bot
. .venv/bin/activate
export PYTHONPATH=src
python -m edge_bot.cli dump-config
python -m edge_bot.cli telegram-test
python -m edge_bot.cli run --mode paper --max-ticks 3 --log-level WARNING
```

## 5. systemd service

```bash
sudo cp /opt/edge-bot/deploy/edge-bot.service /etc/systemd/system/edge-bot.service
sudo chown -R edgebot:edgebot /opt/edge-bot
sudo systemctl daemon-reload
sudo systemctl enable edge-bot
sudo systemctl start edge-bot
sudo systemctl status edge-bot --no-pager
```

Logs and local reports:

```bash
sudo journalctl -u edge-bot -f
sudo -u edgebot bash -lc 'cd /opt/edge-bot && PYTHONPATH=src .venv/bin/python -m edge_bot.cli report --limit 20'
sudo -u edgebot cat /opt/edge-bot/runtime/dashboard.txt
```

## 6. Restart after code/config changes

```bash
cd /opt/edge-bot
git pull
. .venv/bin/activate
python -m pip install -e .
sudo systemctl restart edge-bot
sudo journalctl -u edge-bot -n 100 --no-pager
```

## 7. Later live mode

Live mode requires real credentials and an explicit acknowledgement. Keep the same Telegram reporting.

```env
EDGE_MODE=live
LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY
PM_PRIVATE_KEY=<wallet_private_key>
PM_FUNDER=<funder_or_proxy_wallet_address>
PM_SIGNATURE_TYPE=2
PM_API_KEY=<clob_api_key>
PM_API_SECRET=<clob_api_secret>
PM_API_PASSPHRASE=<clob_api_passphrase>
```

Before live, run paper mode for at least 24-72 hours and verify Telegram reports, fills, settlement, skipped signal reasons and drawdown.

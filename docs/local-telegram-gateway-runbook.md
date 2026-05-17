# Local Telegram Gateway Runbook

This server runs Hermes Agent with Telegram gateway enabled as a systemd
system service.

## Runtime

- Project path: `/root/hermes-agent`
- Hermes home: `/root/.hermes`
- Systemd unit: `/etc/systemd/system/hermes-gateway.service`
- Service user: `root`
- Bot mode: Telegram long polling
- Service command: `/root/hermes-agent/venv/bin/python -m hermes_cli.main gateway run --replace`
- Voice message STT provider: Deepgram (`stt.provider: deepgram`)

## Secrets

Telegram credentials are stored only in `/root/.hermes/.env`.

Do not commit or print:

- `TELEGRAM_BOT_TOKEN`
- `DEEPGRAM_API_KEY`
- `/root/.hermes/.env`
- `/root/.hermes/auth.json`
- OAuth tokens, refresh tokens, API keys, or private keys

## Access Control

Telegram access is restricted with `TELEGRAM_ALLOWED_USERS` in
`/root/.hermes/.env`. Unknown users should not be able to use the bot.

The current home channel is also configured in `/root/.hermes/.env` with
`TELEGRAM_HOME_CHANNEL`.

## Voice Transcription

Telegram voice messages are transcribed through Deepgram's prerecorded audio
API. Hermes is configured with:

```yaml
stt:
  enabled: true
  provider: deepgram
  deepgram:
    model: nova-3
    smart_format: true
    punctuate: true
    detect_language: true
    language: ru
```

The Deepgram API key lives only in `/root/.hermes/.env` as `DEEPGRAM_API_KEY`.
Do not copy it into repository docs, logs, commits, or chat output.

Deepgram models default to English when no language is supplied. The local
server config sets `language: ru` because this Telegram bot is primarily used
for Russian voice messages. If mixed-language use becomes important, clear
`stt.deepgram.language` and keep `detect_language: true`.

Quick non-secret checks:

```bash
grep -q '^DEEPGRAM_API_KEY=' /root/.hermes/.env && echo 'DEEPGRAM_API_KEY is set'
rg -n 'stt:|provider: deepgram|deepgram:' /root/.hermes/config.yaml
```

## Operations

Check service status:

```bash
hermes gateway status --system
systemctl status hermes-gateway --no-pager
```

Start, stop, or restart:

```bash
hermes gateway start --system
hermes gateway stop --system
hermes gateway restart --system
```

View logs:

```bash
journalctl -u hermes-gateway -f
tail -f /root/.hermes/logs/agent.log
tail -f /root/.hermes/logs/errors.log
```

Verify boot behavior:

```bash
systemctl is-enabled hermes-gateway
systemctl is-active hermes-gateway
```

Expected output:

```text
enabled
active
```

## Notes

The first attempted user-level systemd install failed because
`systemctl --user` could not connect to a user bus in this server environment.
The final working setup uses a system-level unit, which starts at boot without
requiring systemd linger.

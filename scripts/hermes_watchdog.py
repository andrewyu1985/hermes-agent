#!/usr/bin/env python3
"""Independent Hermes watchdog run by system cron.

Keeps working even when Hermes cron/LLM is unhealthy.  It must not contain
secrets; Telegram credentials are loaded from ~/.hermes/.env.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
HERMES_DIR = Path("/root/hermes-agent")
ENV_FILE = HERMES_HOME / ".env"
LOG_FILE = HERMES_HOME / "logs" / "watchdog.log"

ERROR_PATTERNS = [
    r"NoneType.*object is not iterable",
    r"Non-retryable client error",
    r"TypeError: 'NoneType' object is not iterable",
]

REQUIRED_FIXES = [
    (HERMES_DIR / "agent/codex_runtime.py", r"NoneType.*iterable", 1, "documented output=null / NoneType iterable regression note"),
    (HERMES_DIR / "agent/codex_runtime.py", r"def _consume_codex_event_stream", 1, "event-driven Codex SSE consumer"),
    (HERMES_DIR / "agent/codex_runtime.py", r"responses\.create", 1, "raw responses.create(stream=True) path"),
    (HERMES_DIR / "agent/codex_runtime.py", r"stream_kwargs\[\"stream\"\] = True", 1, "stream=True low-level kwargs"),
    (HERMES_DIR / "agent/conversation_loop.py", r"except \(TypeError|except TypeError|isinstance\(api_error, TypeError\)", 1, "TypeError-aware response recovery"),
    (HERMES_DIR / "agent/conversation_loop.py", r"output_text", 1, "output_text fallback"),
]


def load_env() -> dict[str, str]:
    data = dict(os.environ)
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return data


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def send_telegram(text: str) -> bool:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_HOME_CHANNEL") or env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("Telegram alert skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_HOME_CHANNEL missing")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=payload), timeout=15) as response:
            response.read()
        return True
    except Exception as exc:
        log(f"Telegram send failed: {type(exc).__name__}: {exc}")
        return False


def run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return proc.returncode, proc.stdout.strip()
    except subprocess.TimeoutExpired as exc:
        out: str = exc.stdout if type(exc.stdout) is str else ""
        return 124, out + "\nTIMEOUT"
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def check_process() -> list[str]:
    code, out = run(["pgrep", "-f", "hermes_cli.main gateway"])
    if code != 0:
        return []
    return [line for line in out.splitlines() if line.strip()]


def check_recent_errors(minutes: int = 60) -> list[str]:
    since = (dt.datetime.now() - dt.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    code, out = run(["journalctl", "-u", "hermes-gateway.service", "--no-pager", f"--since={since}"], timeout=20)
    if code not in (0, 1):
        return [f"journalctl error: {out[:200]}"]
    errors: list[str] = []
    for pattern in ERROR_PATTERNS:
        count = len(re.findall(pattern, out, flags=re.IGNORECASE))
        if count:
            errors.append(f"{pattern}: {count}x")
    return errors


def check_fixes() -> list[str]:
    missing: list[str] = []
    cache: dict[Path, str] = {}
    for path, pattern, minimum, description in REQUIRED_FIXES:
        try:
            text = cache.setdefault(path, path.read_text(encoding="utf-8", errors="replace"))
            count = len(re.findall(pattern, text))
        except Exception as exc:
            missing.append(f"{description}: check error {type(exc).__name__}: {exc}")
            continue
        if count < minimum:
            missing.append(f"{description}: {count}, expected >= {minimum}")
    return missing


def main() -> int:
    log("=== Watchdog check started ===")
    problems: list[str] = []

    pids = check_process()
    if pids:
        log(f"OK: gateway running (PIDs: {', '.join(pids)})")
    else:
        problems.append("❌ Процесс hermes gateway НЕ запущен")
        log("CRITICAL: gateway process not found")

    recent_errors = check_recent_errors(60)
    if recent_errors:
        for item in recent_errors:
            problems.append(f"⚠️ В логах gateway: {item}")
            log(f"ERROR in logs: {item}")
    else:
        log("OK: no critical errors in last 60 min")

    missing_fixes = check_fixes()
    if missing_fixes:
        for item in missing_fixes:
            problems.append(f"🔧 Фикс ОТСУТСТВУЕТ: {item}")
            log(f"FIX MISSING: {item}")
    else:
        log("OK: Codex regression fixes present")

    log("SKIP: token expiry alerts are handled by daily hermes_token_monitor cron")

    if problems:
        message = "🚨 <b>Hermes Watchdog — ПРОБЛЕМЫ</b>\n\n" + "\n".join(problems)
        message += f"\n\n⏰ {dt.datetime.now().strftime('%d.%m.%Y %H:%M')}"
        send_telegram(message)
        log(f"Alert sent: {len(problems)} problems")
        return 1

    log("All checks passed ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

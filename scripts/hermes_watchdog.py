#!/usr/bin/env python3
"""Read-only Hermes watchdog for cron or manual operation.

The watchdog reports Git-HEAD drift and gateway health. Optional Telegram
alerting is used only when credentials are configured. It never repairs files,
refreshes credentials, restarts services, or imports runtime provider code.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

HERMES_DIR = Path(__file__).resolve().parents[1]
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
ENV_FILE = HERMES_HOME / ".env"
LOG_FILE = HERMES_HOME / "logs" / "watchdog.log"


def _load_alert_env() -> dict[str, str]:
    data = dict(os.environ)
    try:
        lines = ENV_FILE.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    except FileNotFoundError:
        return data
    except OSError:
        return data
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return data


def _log(message: str) -> None:
    line = f"[{dt.datetime.now().astimezone().isoformat(timespec='seconds')}] {message}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _run(command: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout.strip()


def check_gateway() -> list[str]:
    code, output = _run(["systemctl", "is-active", "hermes-gateway"])
    if code == 0 and output == "active":
        return []
    return [f"hermes-gateway is not active: {output or f'systemctl exit {code}'}"]


def check_fixes() -> list[str]:
    try:
        from hermes_integrity import check_integrity
    except ModuleNotFoundError:
        try:
            from scripts.hermes_integrity import check_integrity
        except ImportError as exc:
            return [f"integrity checker error: {type(exc).__name__}: {exc}"]
    return check_integrity(HERMES_DIR)


def _send_telegram(text: str) -> bool:
    env = _load_alert_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_HOME_CHANNEL") or env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:3500]}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
    except (OSError, TimeoutError):
        return False
    return True


def main() -> int:
    problems = [*check_gateway(), *check_fixes()]
    if not problems:
        _log("OK: gateway active; vetted Codex sources match Git HEAD")
        return 0
    for problem in problems:
        _log(f"PROBLEM: {problem}")
    _send_telegram("Hermes watchdog requires manual review:\n" + "\n".join(problems))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

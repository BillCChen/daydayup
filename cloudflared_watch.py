#!/usr/bin/env python3
"""Monitor the quick Cloudflare Tunnel and email URL changes."""

from __future__ import annotations

import argparse
import re
import smtplib
import subprocess
import sys
import time
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
LOCAL_DIR = ROOT / "local"
LOG_DIR = ROOT / "logs"
CLOUDFLARED_LOG = LOG_DIR / "cloudflared.log"
CLOUDFLARED_STDERR_LOG = LOG_DIR / "cloudflared_launchd.err.log"
STATE_PATH = LOCAL_DIR / "cloudflared_url.txt"
MAIL_CONFIG_PATH = LOCAL_DIR / "cloudflared_mail.env"
CLOUDFLARED_PLIST = Path("/Users/billchen/Library/LaunchAgents/com.billchen.daydayup.cloudflared.plist")
CLOUDFLARED_LABEL = "com.billchen.daydayup.cloudflared"
PUBLIC_URL_PATTERN = re.compile(r"https://[a-z-]+\.trycloudflare\.com")


def run(command: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def user_domain() -> str:
    return f"gui/{run(['id', '-u'], check=True).stdout.strip()}"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def latest_public_url() -> str:
    text = "\n".join([read_text(CLOUDFLARED_LOG), read_text(CLOUDFLARED_STDERR_LOG)])
    matches = PUBLIC_URL_PATTERN.findall(text)
    return matches[-1] if matches else ""


def saved_public_url() -> str:
    return read_text(STATE_PATH).strip()


def save_public_url(url: str) -> None:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(url + "\n", encoding="utf-8")


def http_ok(url: str, timeout: float = 20.0) -> bool:
    if not url:
        return False
    try:
        request = Request(url, headers={"User-Agent": "daydayup-cloudflared-watch/1.0"})
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 400
    except (OSError, URLError):
        return False


def truncate_tunnel_logs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for path in (CLOUDFLARED_LOG, CLOUDFLARED_STDERR_LOG):
        path.write_text("", encoding="utf-8")


def restart_cloudflared() -> None:
    domain = user_domain()
    run(["launchctl", "bootout", domain, str(CLOUDFLARED_PLIST)])
    truncate_tunnel_logs()
    run(["launchctl", "bootstrap", domain, str(CLOUDFLARED_PLIST)], check=True)
    run(["launchctl", "enable", f"{domain}/{CLOUDFLARED_LABEL}"], check=True)
    run(["launchctl", "kickstart", "-k", f"{domain}/{CLOUDFLARED_LABEL}"], check=True)


def wait_for_public_url(timeout: float = 90.0) -> str:
    deadline = time.monotonic() + timeout
    last_url = ""
    while time.monotonic() < deadline:
        url = latest_public_url()
        if url:
            last_url = url
            if http_ok(url):
                return url
        time.sleep(3)
    return last_url


def send_email(url: str, reason: str) -> None:
    config = load_env(MAIL_CONFIG_PATH)
    host = config.get("SMTP_HOST", "smtp.qq.com")
    port = int(config.get("SMTP_PORT", "465"))
    username = config.get("SMTP_USER", "")
    password = config.get("SMTP_PASSWORD", "")
    recipient = config.get("MAIL_TO", "")
    if not username or not password or not recipient:
        raise RuntimeError(f"mail config is incomplete: {MAIL_CONFIG_PATH}")

    body = "\n".join(
        [
            "Daydayup web console public URL:",
            url,
            "",
            f"Reason: {reason}",
            f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
    )
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = "Daydayup public URL"
    message["From"] = username
    message["To"] = recipient
    with smtplib.SMTP_SSL(host, port, timeout=20) as server:
        server.login(username, password)
        server.sendmail(username, [recipient], message.as_string())


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor and refresh the quick Cloudflare Tunnel")
    parser.add_argument("--force", action="store_true", help="restart the tunnel before checking")
    parser.add_argument("--send-always", action="store_true", help="send the current URL even if unchanged")
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    current_url = saved_public_url() or latest_public_url()
    reason = "scheduled check"

    if args.force or not http_ok(current_url):
        reason = "tunnel refreshed"
        restart_cloudflared()
        current_url = wait_for_public_url()

    if not current_url or not http_ok(current_url):
        print("cloudflared URL is not reachable", file=sys.stderr)
        return 1

    previous_url = saved_public_url()
    save_public_url(current_url)
    if args.send_always or current_url != previous_url or reason == "tunnel refreshed":
        send_email(current_url, reason)

    print(current_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

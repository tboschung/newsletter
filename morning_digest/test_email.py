from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .config import ENV_FILE, read_env
from .emailer import build_message, send_email


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def build_test_message(sender: str, recipient: str):
    subject = "[TEST] Morning Signal email configuration"
    plain = "This is a test email from Morning Signal. No digest or subscriber data was changed."
    html = ("<!doctype html><html><body><h1>Morning Signal test email</h1>"
            "<p>This is a test email. No digest or subscriber data was changed.</p></body></html>")
    return build_message(subject, plain, html, sender, recipient)


def main(argv: list[str] | None = None, *, env_path: Path = ENV_FILE) -> int:
    parser = argparse.ArgumentParser(description="Send a standalone Morning Signal test email")
    parser.add_argument("recipient", help="Destination email address")
    args = parser.parse_args(argv)
    recipient = args.recipient.strip()
    if len(recipient) > 254 or not EMAIL_RE.fullmatch(recipient):
        parser.error("recipient must be a valid email address")
    try:
        config = read_env(env_path)
        username = config.get("SMTP_USERNAME", "").strip()
        password = config.get("SMTP_APP_PASSWORD", "").strip()
        if not username or not password:
            raise ValueError("SMTP_USERNAME and SMTP_APP_PASSWORD are required")
        send_email(build_test_message(username, recipient), username, password)
        print(f"Test email sent to {recipient}")
        return 0
    except Exception as exc:
        print(f"Test email failed: {' '.join(str(exc).split())}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import smtplib
import ssl
from pathlib import Path
from urllib.parse import urlencode
from email.message import EmailMessage

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).with_name("templates")


def build_message(subject: str, plain: str, html: str, sender: str, recipient: str,
                  *, unsubscribe_url: str = "") -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    if unsubscribe_url:
        message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(plain)
    message.add_alternative(html, subtype="html")
    return message


def smtp_check(username: str, password: str) -> None:
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(username, password)


def send_email(message: EmailMessage, username: str, password: str) -> str | None:
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(username, password)
        refused = smtp.send_message(message)
        if refused:
            code, detail = next(iter(refused.values()))
            raise smtplib.SMTPRecipientsRefused({str(message["To"]): (code, detail)})
    return message.get("Message-ID")


def build_confirmation_message(sender: str, recipient: str, public_url: str, token: str) -> EmailMessage:
    link = f"{public_url.rstrip('/')}/confirm?{urlencode({'token': token})}"
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    plain = env.get_template("confirmation.txt.j2").render(confirmation_url=link)
    html = env.get_template("confirmation.html.j2").render(confirmation_url=link)
    return build_message("Confirm your Morning Signal subscription", plain, html, sender, recipient)


def classify_smtp_error(exc: Exception) -> tuple[str, bool, bool]:
    """Return category, permanent-recipient flag, and global-failure flag."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "smtp_auth", False, True
    if isinstance(exc, (smtplib.SMTPConnectError, smtplib.SMTPNotSupportedError)):
        return "smtp_configuration", False, True
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [value[0] for value in exc.recipients.values()]
        if any(500 <= code < 600 for code in codes):
            return "recipient_permanent", True, False
        return "recipient_transient", False, False
    code = getattr(exc, "smtp_code", 0)
    if 400 <= code < 500 or isinstance(exc, (TimeoutError, smtplib.SMTPServerDisconnected)):
        return "recipient_transient", False, False
    return "smtp_error", False, False

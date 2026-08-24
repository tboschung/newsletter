from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage


def build_message(subject: str, plain: str, html: str, sender: str, recipient: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(plain)
    message.add_alternative(html, subtype="html")
    return message


def smtp_check(username: str, password: str) -> None:
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(username, password)


def send_email(message: EmailMessage, username: str, password: str) -> None:
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(username, password)
        smtp.send_message(message)


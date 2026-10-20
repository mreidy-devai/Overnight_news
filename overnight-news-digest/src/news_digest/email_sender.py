"""Send a digest through explicitly configured SMTP with STARTTLS."""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Mapping

REQUIRED_ENVIRONMENT = (
    "SMTP_HOST",
    "SMTP_PORT",
    "DIGEST_EMAIL_FROM",
    "DIGEST_EMAIL_TO",
)
SMTP_TIMEOUT_SECONDS = 20


def build_message(
    *,
    sender: str,
    recipient: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> EmailMessage:
    """Create a multipart email with a plain-text fallback."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


def _smtp_settings(environ: Mapping[str, str]) -> tuple[str, int, str, str, str, str]:
    missing = [key for key in REQUIRED_ENVIRONMENT if not environ.get(key, "").strip()]
    if missing:
        raise ValueError("Missing email environment variables: " + ", ".join(missing))

    try:
        port = int(environ["SMTP_PORT"])
    except (TypeError, ValueError) as exc:
        raise ValueError("SMTP_PORT must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError("SMTP_PORT must be an integer between 1 and 65535")

    username = environ.get("SMTP_USERNAME", "").strip()
    password = environ.get("SMTP_PASSWORD", "")
    if username and not password:
        raise ValueError("SMTP_PASSWORD is required when SMTP_USERNAME is set")

    return (
        environ["SMTP_HOST"].strip(),
        port,
        environ["DIGEST_EMAIL_FROM"].strip(),
        environ["DIGEST_EMAIL_TO"].strip(),
        username,
        password,
    )


def send_email(
    *,
    subject: str,
    html_body: str,
    text_body: str,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Send through SMTP STARTTLS; credentials are read from environment vars."""
    env = os.environ if environ is None else environ
    host, port, sender, recipient, username, password = _smtp_settings(env)
    message = build_message(
        sender=sender,
        recipient=recipient,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        if username:
            server.login(username, password)
        server.send_message(message)

import pytest
from news_digest.email_sender import build_message, send_email


def test_message_is_multipart_alternative():
    msg = build_message(sender="a@example.com", recipient="b@example.com", subject="Test", html_body="<p>Hi</p>", text_body="Hi")
    assert msg["To"] == "b@example.com"
    assert msg.get_content_type() == "multipart/alternative"
    parts = list(msg.iter_parts())
    assert [part.get_content_subtype() for part in parts] == ["plain", "html"]


def test_missing_smtp_config_fails_before_network():
    with pytest.raises(ValueError, match="Missing email environment variables"):
        send_email(subject="s", html_body="<p>x</p>", text_body="x", environ={})


def test_invalid_smtp_port_is_rejected():
    env = {"SMTP_HOST":"localhost", "SMTP_PORT":"bad", "DIGEST_EMAIL_FROM":"a@example.com", "DIGEST_EMAIL_TO":"b@example.com"}
    with pytest.raises(ValueError, match="SMTP_PORT must be an integer"):
        send_email(subject="s", html_body="<p>x</p>", text_body="x", environ=env)

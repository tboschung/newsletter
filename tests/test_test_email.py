from email.message import EmailMessage
from datetime import datetime, timezone

from morning_digest import test_email


def test_build_test_message_is_labelled_multipart():
    message = test_email.build_test_message(
        "sender@example.com", "reader@example.com",
        now=datetime(2026, 10, 5, 6, tzinfo=timezone.utc),
    )
    assert isinstance(message, EmailMessage)
    assert message["Subject"].startswith("[TEST]")
    assert message["To"] == "reader@example.com"
    assert message.is_multipart()
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "TOP AI DEVELOPMENTS" in plain
    assert "EARLY-CAREER AI, ML & DATA JOBS" in plain
    assert "Junior Machine Learning Engineer" in plain
    assert "Your AI briefing" in html
    assert "New multimodal model released" in html


def test_test_email_sends_with_environment_credentials(tmp_path, monkeypatch):
    env = tmp_path / "newsletter.env"
    env.write_text(
        "SMTP_USERNAME=sender@example.com\nSMTP_APP_PASSWORD=secret\n", encoding="utf-8"
    )
    sent = []
    monkeypatch.setattr(test_email, "send_email", lambda *args: sent.append(args))

    assert test_email.main(["reader@example.com"], env_path=env) == 0
    assert sent[0][0]["To"] == "reader@example.com"
    assert sent[0][1:] == ("sender@example.com", "secret")


def test_test_email_reports_configuration_and_smtp_failures(tmp_path, monkeypatch, capsys):
    env = tmp_path / "newsletter.env"
    env.write_text("SMTP_USERNAME=sender@example.com\n", encoding="utf-8")
    assert test_email.main(["reader@example.com"], env_path=env) == 1
    assert "SMTP_USERNAME and SMTP_APP_PASSWORD are required" in capsys.readouterr().err

    env.write_text(
        "SMTP_USERNAME=sender@example.com\nSMTP_APP_PASSWORD=secret\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        test_email, "send_email", lambda *_: (_ for _ in ()).throw(OSError("offline"))
    )
    assert test_email.main(["reader@example.com"], env_path=env) == 1
    assert "Test email failed: offline" in capsys.readouterr().err

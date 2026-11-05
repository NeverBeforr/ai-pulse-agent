import imaplib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_pulse.errors import SourceFailure
from ai_pulse.sources.imap import IMAPSource, parse_message


def test_email_fixture_parses_html_and_date():
    items = parse_message(Path("tests/fixtures/tldr_email.eml").read_bytes())
    assert len(items) == 1
    assert items[0].published_at == "2026-11-01T08:00:00+00:00"
    assert "AI" in items[0].title


def test_multiple_headings_in_one_table_cell_are_independent():
    from email.message import EmailMessage

    message = EmailMessage()
    message["Date"] = "Sun, 01 Nov 2026 08:00:00 +0000"
    message.set_content(
        '<table><td><a href="https://example.com/one"><h3>AI one</h3></a><p>first</p>'
        '<a href="https://example.com/two"><h3>AI two</h3></a><p>second</p></td></table>',
        subtype="html",
    )
    result = parse_message(message.as_bytes())
    assert [item.url for item in result] == ["https://example.com/one", "https://example.com/two"]
    assert "second" not in result[0].summary and "second" in result[1].summary


async def test_imap_is_readonly_and_uses_peek(monkeypatch):
    calls = []

    class Mail:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append("退出")

        def login(self, user, password):
            calls.append("登录")

        def select(self, mailbox, readonly):
            assert readonly is True
            return "OK", []

        def uid(self, operation, *args):
            calls.append((operation, args))
            if operation == "search":
                return "OK", [b"4"]
            assert args[1] == "(BODY.PEEK[])"
            return "OK", [(b"4", Path("tests/fixtures/tldr_email.eml").read_bytes())]

    monkeypatch.setattr(imaplib, "IMAP4_SSL", Mail)
    config = {
        "host": "imap.example.com",
        "port": 993,
        "username": "fake-user",
        "password": SecretStr("fake-password"),
        "mailbox": "INBOX",
        "search": 'FROM "tldr" SINCE {since}',
        "limit": 5,
    }
    items = await IMAPSource(config, None).fetch(datetime(2026, 11, 1, tzinfo=UTC))
    assert len(items) == 1 and calls[-1] == "退出"
    assert "01-Nov-2026" in next(
        call[1][1] for call in calls if isinstance(call, tuple) and call[0] == "search"
    )


async def test_auth_error_never_echoes_password(monkeypatch):
    class Mail:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def login(self, *a):
            raise imaplib.IMAP4.error("fake-secret")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", Mail)
    config = {
        "host": "x",
        "port": 993,
        "username": "x",
        "password": SecretStr("fake-secret"),
        "mailbox": "INBOX",
        "search": "ALL",
        "limit": 1,
    }
    with pytest.raises(SourceFailure) as error:
        await IMAPSource(config, None).fetch(datetime.now(UTC))
    assert error.value.kind == "auth" and "fake-secret" not in str(error.value)

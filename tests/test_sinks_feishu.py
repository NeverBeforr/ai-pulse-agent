import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from ai_pulse.errors import DeliveryError
from ai_pulse.models import SinkConfig
from ai_pulse.offline import fixture_digest
from ai_pulse.sinks.feishu import FeishuSink, render_card
from ai_pulse.sinks.markdown import MarkdownSink


def test_card_matches_golden():
    digest = fixture_digest()
    expected = json.loads(Path("tests/fixtures/feishu_card_expected.json").read_text(encoding="utf-8"))
    assert render_card(digest, digest["items"]) == expected


def test_summary_is_capped_and_card_is_bounded():
    digest = fixture_digest()
    digest["items"][0]["summary"] = "长" * 500
    card = render_card(digest, digest["items"])
    assert "长" * 141 not in json.dumps(card, ensure_ascii=False)
    digest["items"][0]["url"] = "https://example.com/" + ("a" * 30000)
    with pytest.raises(DeliveryError, match="28KB"):
        render_card(digest, digest["items"])


def test_card_time_uses_configured_timezone():
    digest = fixture_digest()
    note = render_card(digest, digest["items"])["card"]["elements"][-1]["elements"][0]["content"]
    assert "生成 08:00" in note
    digest["timezone"] = "UTC"
    note = render_card(digest, digest["items"])["card"]["elements"][-1]["elements"][0]["content"]
    assert "生成 00:00" in note


async def test_http_4xx_is_not_retried(monkeypatch):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json):
            calls.append(url)
            return httpx.Response(403)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    sink = FeishuSink(SinkConfig(type="feishu", webhook=SecretStr("https://example.com/bot/secret")))
    digest = fixture_digest()
    with pytest.raises(DeliveryError, match="403"):
        await sink.send(digest, digest["items"])
    assert len(calls) == 1


async def test_network_retries_are_bounded(monkeypatch):
    calls = []
    waits = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json):
            calls.append(url)
            if len(calls) < 3:
                raise httpx.ConnectError("offline")
            return httpx.Response(200, json={"code": 0})

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr("ai_pulse.sinks.feishu.asyncio.sleep", sleep)
    digest = fixture_digest()
    await FeishuSink(SinkConfig(type="feishu", webhook=SecretStr("https://example.com/bot/secret"))).send(
        digest, digest["items"]
    )
    assert len(calls) == 3 and waits == [2, 6]


async def test_business_failure_is_visible(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json):
            return httpx.Response(200, json={"code": 19001})

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    digest = fixture_digest()
    with pytest.raises(DeliveryError, match="19001"):
        await FeishuSink(SinkConfig(type="feishu", webhook=SecretStr("https://example.com/bot/secret"))).send(
            digest, digest["items"]
        )


async def test_markdown_is_overwritten(tmp_path):
    sink = MarkdownSink(SinkConfig(type="markdown", path=str(tmp_path / "{date}.md")))
    digest = fixture_digest()
    await sink.send(digest, digest["items"])
    await sink.send(digest, digest["items"])
    text = (tmp_path / "2026-11-01.md").read_text(encoding="utf-8")
    assert text.count("# Pulse 每日简报") == 1 and "coverage:" in text

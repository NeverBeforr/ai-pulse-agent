import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ai_pulse.sources.hn import HNSource
from ai_pulse.sources.tldr import TLDRSource


async def test_hn_fixture_and_since_window():
    story = json.loads(Path("tests/fixtures/hn_top.json").read_text())[0]

    class Network:
        async def get(self, url):
            return httpx.Response(200, json=story if "/item/" in url else [1])

    adapter = HNSource({}, Network())
    assert len(await adapter.fetch(datetime(2026, 1, 1, tzinfo=UTC))) == 1
    assert await adapter.fetch(datetime(2027, 1, 1, tzinfo=UTC)) == []


async def test_tldr_archive_uses_public_issue_fixture():
    class Network:
        async def get(self, url):
            text = (
                '<a href="/ai/2026-11-01">一期</a>'
                if url.endswith("archives")
                else Path("tests/fixtures/tldr_issue.html").read_text(encoding="utf-8")
            )
            return httpx.Response(200, text=text)

    adapter = TLDRSource({"url": "https://tldr.tech/ai/archives"}, Network())
    assert len(await adapter.fetch(datetime(2026, 1, 1, tzinfo=UTC))) == 1
    assert await adapter.fetch(datetime(2027, 1, 1, tzinfo=UTC)) == []


async def test_rss_limit_is_applied():
    from ai_pulse.sources.rss import RSSSource

    class Network:
        async def get(self, url):
            return httpx.Response(200, text=Path("tests/fixtures/rss_zh.xml").read_text(encoding="utf-8"))

    assert (
        len(
            await RSSSource({"url": "https://example.com/rss", "limit": 1}, Network()).fetch(
                datetime(2026, 1, 1, tzinfo=UTC)
            )
        )
        == 1
    )

import calendar
import re
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import feedparser
from bs4 import BeautifulSoup

from ..models import CollectedItem
from ..network import canonical_url, public_url
from .base import register
from .common import clean_text, is_ai


def parse_feed(content: str) -> list[CollectedItem]:
    parsed = feedparser.parse(content)
    if not parsed.entries and parsed.bozo:
        raise ValueError("无法解析 RSS/Atom 内容")
    items = []
    for entry in parsed.entries[:40]:
        title, link = entry.get("title", ""), entry.get("link", "")
        if not title or not link:
            continue
        summary = clean_text(entry.get("summary", ""))
        if not is_ai(title + " " + summary):
            continue
        timestamp = entry.get("published_parsed") or entry.get("updated_parsed")
        published = datetime.fromtimestamp(calendar.timegm(timestamp), UTC).isoformat() if timestamp else None
        public_url(link)
        items.append(
            CollectedItem(
                title=title[:500],
                url=canonical_url(link),
                summary=summary,
                published_at=published,
                metrics={"time_label": "来源发布时间", "time_precision": "minute", "time_priority": 3},
            )
        )
    return items


def parse_media(content: str, base_url: str) -> list[CollectedItem]:
    soup = BeautifulSoup(content, "html.parser")
    patterns = {
        "jiqizhixin.com": r"/articles/[^/?#]+",
        "qbitai.com": r"/20\d{2}/\d{2}/\d+\.html",
        "geekpark.net": r"/news/\d+",
    }
    host = urlsplit(base_url).hostname.removeprefix("www.")
    pattern = patterns[host]
    items = []
    seen = set()
    for anchor in soup.select("a[href]"):
        url = urljoin(base_url, anchor["href"])
        if (urlsplit(url).hostname or "").removeprefix("www.") != host or not re.search(pattern, url):
            continue
        title = anchor.get_text(" ", strip=True)
        if len(title) < 8:
            image = anchor.find("img")
            title = image.get("alt", "") if image else ""
        url = canonical_url(url)
        if len(title) < 8 or url in seen or not is_ai(title):
            continue
        seen.add(url)
        items.append(
            CollectedItem(title=title[:500], url=url, summary="官网列表线索，正文尚未读取；发布时间待核验。")
        )
        if len(items) >= 25:
            break
    if not items:
        raise ValueError("页面可访问，但未提取到 AI 相关文章；需核对解析规则")
    return items


@register("rss")
class RSSSource:
    type = "rss"

    def __init__(self, config, network):
        self.config, self.network = config, network

    async def fetch(self, since):
        response = await self.network.get(self.config["url"])
        text = response.text
        if "<html" in text[:500].lower() and (urlsplit(self.config["url"]).hostname or "").removeprefix(
            "www."
        ) in {"jiqizhixin.com", "qbitai.com", "geekpark.net"}:
            items = parse_media(text, self.config["url"])
        else:
            items = parse_feed(text)
        return [
            item
            for item in items
            if item.published_at is None
            or datetime.fromisoformat(item.published_at.replace("Z", "+00:00")) >= since
        ][: self.config.get("limit", 40)]

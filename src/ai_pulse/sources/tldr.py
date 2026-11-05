import asyncio
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import CollectedItem
from ..network import canonical_url, public_url
from .base import register
from .common import is_ai


def parse_tldr(content: str, issue_url: str) -> list[CollectedItem]:
    soup = BeautifulSoup(content, "html.parser")
    date = re.search(r"/(\d{4}-\d{2}-\d{2})", issue_url)
    published = f"{date[1]}T00:00:00+00:00" if date else None
    items = []
    for article in soup.select("article"):
        heading = article.select_one("h3")
        anchor = (heading.find_parent("a") or heading.find("a")) if heading else None
        if not anchor:
            anchor = article.select_one("a[href]")
        if not heading or not anchor:
            continue
        title = heading.get_text(" ", strip=True)
        if re.search(r"\b(sponsor|sponsored|advertisement)\b", title, re.I):
            continue
        url = urljoin(issue_url, anchor["href"])
        text = article.get_text(" ", strip=True)
        if is_ai(title + " " + text):
            public_url(url)
            items.append(
                CollectedItem(
                    title=title[:500],
                    url=canonical_url(url),
                    summary=text[:2200],
                    published_at=published,
                    metrics={
                        "newsletter_issue": issue_url,
                        "time_precision": "date",
                        "time_label": "TLDR期刊日期",
                        "time_priority": 2,
                    },
                )
            )
    if not soup.select("article"):
        raise ValueError("TLDR 页面结构未识别，未取得文章列表")
    return items


@register("tldr")
class TLDRSource:
    type = "tldr"

    def __init__(self, config, network):
        self.config, self.network = config, network

    async def fetch(self, since) -> list[CollectedItem]:
        archive_url = self.config["url"]
        response = await self.network.get(archive_url)
        soup = BeautifulSoup(response.text, "html.parser")
        section = urlsplit(archive_url).path.split("/")[1]
        issue_urls = list(
            dict.fromkeys(
                urljoin(archive_url, anchor["href"])
                for anchor in soup.select("a[href]")
                if re.fullmatch(rf"/{section}/\d{{4}}-\d{{2}}-\d{{2}}", anchor["href"])
            )
        )[:2]
        if not issue_urls:
            raise ValueError("未找到 TLDR 归档期刊链接")
        pages = await asyncio.gather(*(self.network.get(url) for url in issue_urls))
        return [
            item
            for page, url in zip(pages, issue_urls)
            for item in parse_tldr(page.text, url)
            if item.published_at is None or datetime.fromisoformat(item.published_at) >= since
        ][: self.config.get("limit", 40)]

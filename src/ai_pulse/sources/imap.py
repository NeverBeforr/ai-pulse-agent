"""只读获取邮箱中的TLDR邮件，不修改已读状态。"""

import asyncio
import imaplib
from datetime import UTC
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup, Tag

from ..errors import SourceFailure
from .base import register
from .tldr import parse_tldr


def parse_message(raw: bytes):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    html = message.get_body(preferencelist=("html",))
    if html is None:
        raise SourceFailure("parse", "TLDR邮件没有HTML正文")
    try:
        date = parsedate_to_datetime(message["Date"])
    except (ValueError, TypeError) as exc:
        raise SourceFailure("parse", "TLDR邮件缺少有效Date头") from exc
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    content = html.get_content()
    soup = BeautifulSoup(content, "html.parser")
    if not soup.select("article"):
        # 标题与下一标题之间构成一条，避免多个标题共用一个td时重复读取首条。
        articles = []
        for heading in soup.select("h3"):
            anchor = heading.find_parent("a") or heading
            parts = [str(anchor)]
            for sibling in anchor.next_siblings:
                if isinstance(sibling, Tag) and (sibling.name == "h3" or sibling.find("h3")):
                    break
                parts.append(str(sibling))
            articles.append("<article>" + "".join(parts) + "</article>")
        content = "".join(articles)
    issue = "https://tldr.tech/ai/" + date.date().isoformat()
    items = parse_tldr(content, issue)
    for item in items:
        item.published_at = date.astimezone(UTC).isoformat()
        item.metrics.update(time_label="TLDR邮件日期", time_precision="minute")
    return items


@register("imap")
class IMAPSource:
    type = "imap"

    def __init__(self, config, network):
        self.config = config

    async def fetch(self, since):
        return await asyncio.to_thread(self._fetch, since)

    def _fetch(self, since):
        config = self.config
        months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        formatted = f"{since.day:02d}-{months[since.month - 1]}-{since.year}"
        query = config["search"].format(since=formatted)
        try:
            with imaplib.IMAP4_SSL(config["host"], config["port"], timeout=20) as client:
                client.login(config["username"], config["password"].get_secret_value())
                status, _ = client.select(config["mailbox"], readonly=True)
                if status != "OK":
                    raise SourceFailure("auth", "无法只读打开指定邮箱目录")
                status, data = client.uid("search", None, query)
                if status != "OK":
                    raise SourceFailure("parse", "IMAP搜索失败")
                items = []
                for uid in data[0].split()[-config["limit"] :]:
                    status, parts = client.uid("fetch", uid, "(BODY.PEEK[])")
                    if status != "OK":
                        raise SourceFailure("network", "IMAP邮件读取失败")
                    raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                    if raw is None:
                        raise SourceFailure("parse", "IMAP未返回邮件正文")
                    items.extend(
                        i for i in parse_message(raw) if i.published_at >= since.astimezone(UTC).isoformat()
                    )
                return items
        except imaplib.IMAP4.error as exc:
            raise SourceFailure("auth", "IMAP认证或邮箱操作失败，请检查授权码与目录") from exc

"""公开原文读取与正文提取；不将来源摘要充当文章正文。"""

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from trafilatura import extract

from .network import canonical_url


def extract_body(html):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    if any(
        mark in title for mark in ("just a moment", "access denied", "sign in to continue", "robot check")
    ):
        return "", "页面要求验证或登录"
    text = (
        extract(html, include_comments=False, include_tables=True, include_formatting=True, favor_recall=True)
        or ""
    )
    if len(text.strip()) < 200:
        return text, "没有提取到足够的文章正文"
    if re.search(r"subscribe to (?:continue|read (?:the )?full)|登录后阅读全文|付费后阅读", text, re.I):
        return text, "正文包含明确的订阅或登录截断提示"
    return text, ""


async def fetch_content(item, network):
    url = item["url"]
    result = {
        "discovery_url": url,
        "final_url": url,
        "original_url": None,
        "method": "html",
        "status": "unavailable",
        "text": "",
        "error": "",
    }
    host = urlsplit(url).hostname or ""
    parts = urlsplit(url).path.strip("/").split("/")
    if (
        urlsplit(url).path.lower().endswith(".pdf")
        or (host.endswith("arxiv.org") and parts[0] == "abs")
        or host in {"youtube.com", "www.youtube.com", "youtu.be", "x.com", "twitter.com"}
    ):
        return dict(
            result,
            status="unsupported",
            error="本版不将PDF、论文摘要页、视频页面或需脚本的社交页面当作完整正文",
        )
    if item.get("feed_content"):
        feed_soup = BeautifulSoup(item["feed_content"], "html.parser")
        continuation = any(
            re.match(
                r"^(?:read more|continue reading|阅读全文|继续阅读|阅读原文)(?:\s|$|[→»])",
                a.get_text(" ", strip=True),
                re.I,
            )
            for a in feed_soup.select("a[href]")
        )
        text, error = extract_body("<article>" + item["feed_content"] + "</article>")
        if not error and not continuation:
            return dict(
                result,
                status="ready",
                method="hn_post" if host.endswith("ycombinator.com") else "feed_content",
                text=text,
            )
    if (
        host == "github.com"
        and len(parts) >= 5
        and parts[2] == "blob"
        and parts[-1].lower().startswith("readme")
    ):
        response = await network.get("https://raw.githubusercontent.com/" + "/".join(parts[:2] + parts[3:]))
        text = response.text.strip()
        return dict(
            result,
            method="readme",
            status="ready" if len(text) >= 200 else "insufficient",
            text=text,
            error="" if len(text) >= 200 else "README内容不足",
        )
    if host == "github.com" and len(parts) == 2:
        response = await network.get(
            f"https://api.github.com/repos/{parts[0]}/{parts[1]}/readme",
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        text = response.text.strip()
        return dict(
            result,
            method="readme",
            status="ready" if len(text) >= 200 else "insufficient",
            text=text,
            error="" if len(text) >= 200 else "README内容不足",
        )
    for hop in range(2):
        response = await network.get(url)
        result["final_url"] = canonical_url(str(response.url))
        mime = response.headers.get("content-type", "").split(";")[0]
        if mime == "application/pdf" or urlsplit(url).path.lower().endswith(".pdf"):
            return dict(result, status="unsupported", error="本版尚不读取PDF正文")
        if mime and "html" not in mime and not mime.startswith("text/"):
            return dict(result, status="unsupported", error=f"不支持的正文格式：{mime}")
        soup = BeautifulSoup(response.text, "html.parser")
        area = soup.find("article") or soup.find("main") or soup
        upstream = next(
            (
                a
                for a in area.select("a[href]")
                if "source-link" in a.get("class", [])
                or re.fullmatch(
                    r"阅读原文|原文链接|read original(?: article)?", a.get_text(" ", strip=True), re.I
                )
            ),
            None,
        )
        if upstream:
            target = canonical_url(urljoin(str(response.url), upstream["href"]))
            if target != result["final_url"]:
                if hop:
                    return dict(result, status="secondary_only", error="上游页面仍为聚合摘编，未取得原文")
                result.update(original_url=target, method="upstream_html")
                url = target
                continue
        if urlsplit(url).hostname == "news.ycombinator.com":
            post = soup.select_one(".toptext")
            html = "<article>" + str(post) + "</article>" if post else ""
        else:
            html = response.text
        text, error = extract_body(html)
        return dict(result, text=text, status="insufficient" if error else "ready", error=error)
    return dict(result, status="secondary_only", error="未取得上游原文")

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

import httpx

from ..models import CollectedItem
from ..network import Network, canonical_url
from .hn import HNSource
from .rss import parse_feed, parse_media
from .tldr import TLDRSource


class Collectors:
    def __init__(self, network: Network, settings):
        self.network = network
        self.settings = settings

    async def collect(self, source: dict) -> list[CollectedItem]:
        match source["kind"]:
            case "rss":
                return parse_feed((await self.network.get(source["url"])).text)
            case "hn":
                return await self.hacker_news()
            case "tldr":
                return await self.tldr(source["url"])
            case "github":
                return await self.github()
            case "media":
                return parse_media((await self.network.get(source["url"])).text, source["url"])
            case _:
                raise ValueError("该来源尚未适配，支持先添加公开文章链接")

    async def hacker_news(self):
        return await HNSource({}, self.network).fetch(datetime.min.replace(tzinfo=UTC))

    async def tldr(self, archive_url):
        return await TLDRSource({"url": archive_url}, self.network).fetch(datetime.min.replace(tzinfo=UTC))

    async def github(self) -> list[CollectedItem]:
        settings = self.settings()
        headers = {"Accept": "application/vnd.github+json"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        since = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%d")
        queries = [
            f"topic:llm archived:false pushed:>={since}",
            f"topic:ai-agents archived:false created:>={since}",
        ]
        repos = {}
        for query in queries:
            url = "https://api.github.com/search/repositories?" + urlencode(
                {
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": 10,
                }
            )
            payload = (await self.network.get(url, headers)).json()
            for repo in payload["items"]:
                repos[repo["id"]] = repo
        return [self.repository_item(repo) for repo in repos.values()]

    @staticmethod
    def repository_item(repo: dict) -> CollectedItem:
        return CollectedItem(
            title=repo["full_name"],
            url=canonical_url(repo["html_url"]),
            kind="repository",
            summary=(repo.get("description") or "仓库暂无描述")[:3500],
            published_at=repo.get("created_at"),
            metrics={
                "repo_id": repo["id"],
                "stars": repo["stargazers_count"],
                "language": repo.get("language"),
                "topics": repo.get("topics", []),
                "pushed_at": repo.get("pushed_at"),
                "discovery": "GitHub 主题搜索；暂无增长历史",
                "time_label": "仓库创建时间",
                "time_precision": "minute",
                "time_priority": 4,
            },
        )

    async def linked_repositories(self, entries: list[dict]) -> tuple[list[CollectedItem], list[str]]:
        names = list(
            dict.fromkeys(
                "/".join(urlsplit(item["url"]).path.strip("/").split("/")[:2])
                for item in entries
                if urlsplit(item["url"]).hostname == "github.com"
                and len(urlsplit(item["url"]).path.strip("/").split("/")) >= 2
                and urlsplit(item["url"]).path.strip("/").split("/")[0]
                not in {"topics", "orgs", "features", "marketplace", "settings"}
            )
        )[:5]
        headers = {"Accept": "application/vnd.github+json"}
        if self.settings().github_token:
            headers["Authorization"] = f"Bearer {self.settings().github_token}"
        items, notes = [], []
        for name in names:
            try:
                repo = (await self.network.get(f"https://api.github.com/repos/{name}", headers)).json()
            except httpx.HTTPStatusError as exc:
                notes.append(f"{name}: HTTP {exc.response.status_code}")
                if exc.response.status_code in {403, 429}:
                    break
                continue
            if not repo.get("archived"):
                items.append(self.repository_item(repo))
        return items, notes

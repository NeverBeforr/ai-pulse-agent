import asyncio
from datetime import UTC, datetime
from urllib.parse import urlsplit

from ..models import CollectedItem
from ..network import canonical_url, public_url
from .base import register
from .common import clean_text, is_ai


@register("hn")
class HNSource:
    type = "hn"

    def __init__(self, config, network):
        self.config, self.network = config, network

    async def fetch(self, since) -> list[CollectedItem]:
        root = (self.config.get("url") or "https://hacker-news.firebaseio.com/v0/").rstrip("/") + "/"
        batches = await asyncio.gather(
            *[self.network.get(root + kind + "stories.json") for kind in ("top", "new", "show")]
        )
        ids = list(dict.fromkeys(batches[0].json()[:30] + batches[1].json()[:15] + batches[2].json()[:15]))
        ids = ids[: self.config.get("limit", 60)]
        semaphore = asyncio.Semaphore(6)

        async def read_story(story_id):
            async with semaphore:
                return (await self.network.get(root + f"item/{story_id}.json")).json()

        stories = await asyncio.gather(*(read_story(story_id) for story_id in ids))
        items = []
        for story in stories:
            if not story or story.get("deleted") or story.get("dead") or story.get("type") != "story":
                continue
            if not is_ai(story.get("title", "") + " " + story.get("text", "")):
                continue
            link = story.get("url") or f"https://news.ycombinator.com/item?id={story['id']}"
            public_url(link)
            path_parts = urlsplit(link).path.strip("/").split("/")
            is_repo = (
                urlsplit(link).hostname == "github.com"
                and len(path_parts) == 2
                and path_parts[0] not in {"topics", "orgs", "features", "marketplace", "settings"}
            )
            items.append(
                CollectedItem(
                    title=story["title"][:500],
                    url=canonical_url(link),
                    kind="repository" if is_repo else "news",
                    summary=clean_text(story.get("text", "")),
                    published_at=datetime.fromtimestamp(story["time"], UTC).isoformat(),
                    metrics={
                        "hn_score": story.get("score", 0),
                        "hn_comments": story.get("descendants", 0),
                        "hn_url": f"https://news.ycombinator.com/item?id={story['id']}",
                        "time_label": "HN提交时间",
                        "time_precision": "minute",
                        "time_priority": 1,
                        "discovery": "Hacker News 仓库链接" if is_repo else "Hacker News 讨论",
                    },
                )
            )
        return [item for item in items if datetime.fromisoformat(item.published_at) >= since]

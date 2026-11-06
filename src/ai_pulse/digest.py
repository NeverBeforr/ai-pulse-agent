import math
import re
from datetime import UTC, datetime
from hashlib import sha1

from .editorial import SECTION_LIMIT, SECTIONS, section_for_entry


def rank_candidates(
    entries: list[dict], store, covered: set[int], *, now=None, limit=20, allow_uncertain=False
) -> list[dict]:
    now = now or datetime.now(UTC)
    eligible, groups = [], {}
    for original in entries:
        if original["id"] in covered:
            continue
        section = section_for_entry(original)
        if section is None:
            if not allow_uncertain:
                continue
            section = "industry"
        item = dict(original, section=section)
        item["growth"] = store.growth(item["id"]) if item["kind"] == "repository" else None
        raw_time = item["published_at"] or item.get("first_seen")
        published = datetime.fromisoformat(raw_time.replace("Z", "+00:00")) if raw_time else None
        if published is not None and published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        item["_published"] = published
        for source in item["sources"]:
            key = str(source.get("id", source["name"]))
            hn = source.get("kind") == "hn" or source["name"].lower() in {"hn", "hacker news"}
            value = item["metrics"].get("hn_score", 0) if hn else (published.timestamp() if published else 0)
            groups.setdefault(key, []).append((item["id"], value))
        eligible.append(item)
    percentiles = {}
    for key, values in groups.items():
        for identity, value in values:
            percentile = (
                1.0 if len(values) == 1 else (sum(v <= value for _, v in values) - 1) / (len(values) - 1)
            )
            percentiles[(key, identity)] = percentile
    for item in eligible:
        sources = {str(s.get("id", s["name"])) for s in item["sources"]}
        popularity = max((percentiles[(key, item["id"])] for key in sources), default=0)
        multi = 1 - math.exp(-0.5 * (len(sources) - 1)) if sources else 0
        published = item.pop("_published")
        recency = math.exp(-max(0, (now - published).total_seconds() / 3600) / 24) if published else 0
        official = int(any("官方" in s["channel"] for s in item["sources"]))
        item["rank_score"] = 0.45 * popularity + 0.20 * multi + 0.20 * recency + 0.15 * official
    ranked = sorted(
        eligible, key=lambda item: (-item["rank_score"], sha1(item["title"].encode("utf-8")).hexdigest())
    )
    return ranked[:limit] if limit is not None else ranked


def preview_digest(candidates: list[dict]) -> list[dict]:
    items, seen = [], set()
    counts = dict.fromkeys(SECTIONS, 0)
    for candidate in candidates:
        section = candidate["section"]
        if counts[section] == SECTION_LIMIT:
            continue
        fingerprint = re.sub(r"\W+", "", candidate["title"].casefold())
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        counts[section] += 1
        sources = candidate["sources"]
        reason = "来自已关注的信息源，按可用元数据排序，尚未经过模型筛选。"
        if candidate["kind"] == "repository":
            reason = "AI 主题搜索发现；总 Star 仅作背景，暂无充分近期增长证据。"
            if candidate["metrics"].get("hn_score"):
                reason = f"HN 得分 {candidate['metrics']['hn_score']}，作为近期讨论信号；暂无 Star 增长历史。"
            if candidate["growth"]:
                g = candidate["growth"]
                reason = f"实际观测 {g['hours']} 小时，Star 净增 {g['delta']:+d}。"
        elif candidate["metrics"].get("hn_score"):
            reason = f"Hacker News 当前得分 {candidate['metrics']['hn_score']}，可作为讨论热度线索。"
        items.append(
            {
                "entry_id": candidate["id"],
                "title": candidate["title"],
                "url": candidate["url"],
                "kind": candidate["kind"],
                "section": section,
                "summary": (
                    candidate["summary"]
                    or "当前来源仅提供标题，暂无可独立阅读的摘要；等待模型接入后补充查证。"
                )[:700],
                "reason": reason,
                "evidence_status": "规则预览，尚未模型核验",
                "sources": sources,
                "published_at": candidate["published_at"],
                "metrics": candidate["metrics"],
                "growth": candidate["growth"],
                "evidence": [{"title": candidate["title"], "url": candidate["url"]}],
            }
        )
    return items


def needs_daily_digest(now: datetime, schedule_time: str, latest: dict | None) -> bool:
    hour, minute = map(int, schedule_time.split(":"))
    after_schedule = (now.hour, now.minute) >= (hour, minute)
    return after_schedule and (latest is None or latest["local_date"] != now.date().isoformat())


def demo_digest() -> dict:
    # Reading examples about existing capabilities/products, never presented as today's news.
    examples = [
        (
            "industry",
            "news",
            "工具调用：让模型请求外部系统完成具体操作",
            "工具调用允许模型提出结构化的函数请求，由应用执行查询或其他操作后返回结果。开发者可以将模型接入业务系统，同时把实际执行权保留在应用程序中。这里展示的是既有能力，不代表今天发布的新功能。",
            "了解模型能力边界，有助于判断应用可以怎样连接实际业务。",
            "https://developers.openai.com/api/docs/guides/function-calling",
            "官方能力文档",
        ),
        (
            "industry",
            "news",
            "API 计费：看清输入与输出，才能理解使用成本",
            "模型服务的使用成本需要结合输入、输出及具体功能的计费规则理解。比较模型或安排自动化任务时，应同时关注实际用量和服务价格。本条仅演示行业信息的表达方式，没有宣称今天发生价格调整。",
            "成本也是评估模型更新对实际应用影响的重要维度。",
            "https://developers.openai.com/api/docs/pricing",
            "官方计费文档",
        ),
        (
            "products",
            "news",
            "资料型 AI 笔记：围绕自己的材料提问与整理",
            "NotebookLM 这类资料型工具面向需要阅读和整理材料的用户，让用户围绕自己提供的来源提问、归纳要点。它把通用问答放入具体资料情境，降低从长材料中定位信息的负担。这是既有产品案例，具体能力以官网为准。",
            "产品差异体现在资料与阅读流程的结合，而不只是聊天界面。",
            "https://notebooklm.google/",
            "产品官网",
        ),
        (
            "products",
            "repository",
            "Ollama：为开发者提供运行模型的工具入口",
            "Ollama 为希望在自己的设备上尝试模型的开发者提供运行工具和接口。用户可以先运行可用模型，再将其接入自己的应用；实际支持范围取决于模型与硬件。本条展示真实工具的使用场景，不表示今日发布了新版本。",
            "把模型运行流程做成可使用的工具，本身也能形成产品价值。",
            "https://ollama.com/",
            "产品官网",
        ),
    ]
    items = [
        {
            "entry_id": index,
            "section": section,
            "kind": kind,
            "title": title,
            "summary": summary,
            "url": url,
            "reason": reason,
            "sources": [{"name": source, "channel": "阅读演示参考"}],
            "published_at": None,
            "evidence_status": "阅读演示，非今日新闻",
            "metrics": {},
            "growth": None,
            "evidence": [{"title": source, "url": url}],
        }
        for index, (section, kind, title, summary, reason, url, source) in enumerate(examples, 1)
    ]
    return {
        "id": 0,
        "local_date": "演示样例",
        "revision": 1,
        "mode": "demo",
        "created_at": datetime.now(UTC).isoformat(),
        "coverage": {"demo": True},
        "legacy": False,
        "items": items,
        "sections": {section: [item for item in items if item["section"] == section] for section in SECTIONS},
    }

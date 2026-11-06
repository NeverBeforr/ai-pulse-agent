from pathlib import Path

from .base import coverage_line


def render_markdown(digest, items):
    coverage = digest["coverage"]
    lines = [
        "---",
        f"date: '{digest['local_date']}'",
        f"coverage: '{coverage['successful']}/{coverage['total']}'",
        f"items: {len(items)}",
        "---",
        "",
        f"# Pulse 每日简报 · {digest['local_date']}",
        "",
        coverage_line(digest),
        "",
    ]
    for key, title in (("industry", "行业热点"), ("products", "产品案例")):
        lines += [f"## {title}", ""]
        selected = [item for item in items if item["section"] == key]
        if not selected:
            lines += ["本期没有足够的可用内容，不凑数。", ""]
        for item in selected:
            lines += [
                f"### [{item['title']}]({item['url']})",
                "",
                item["summary"],
                "",
                f"理由：{item['reason']}",
                "",
                "来源：" + "、".join(source["name"] for source in item["sources"]),
                "",
            ]
            if item.get("reading", {}).get("status") in {"complete", "chunked_complete"}:
                lines += ["阅读依据：" + item["evidence_status"] + "，已处理完整提取正文。", ""]
    if digest.get("offline"):
        lines += ["离线预览：未检查网络来源，未调用模型，未投递或更新去重状态。", ""]
    return "\n".join(lines)


class MarkdownSink:
    type = "markdown"

    def __init__(self, config):
        self.path = config.path

    async def send(self, digest, items):
        target = Path(self.path.format(date=digest["local_date"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(render_markdown(digest, items), encoding="utf-8")
        temporary.replace(target)

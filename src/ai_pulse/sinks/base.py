"""输出协议。发送失败由编排层记录，适配器不得假报成功。"""

from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo


class Sink(Protocol):
    type: str

    async def send(self, digest: dict, items: list[dict]) -> None: ...


def coverage_line(digest):
    coverage = digest["coverage"]
    if digest.get("offline"):
        return (
            f"历史回放 · {digest['local_date']} · "
            f"样本来源 {coverage['successful']}/{coverage['total']} · 当前来源未检查"
        )
    last = coverage.get("last_success")
    last = (
        datetime.fromisoformat(last).astimezone(ZoneInfo(digest["timezone"])).strftime("%Y-%m-%d %H:%M")
        if last
        else "暂无记录"
    )
    failures = "、".join(f"{r['source']}({r['error']})" for r in coverage.get("results", []) if not r["ok"])
    line = (
        f"覆盖 {coverage['successful']}/{coverage['total']} 个来源 · "
        f"上次成功 {last} · 来源失败：{failures or '无'}"
    )
    if "reading" in coverage:
        reading = coverage["reading"]
        line += f" · 正文阅读{reading['complete']}篇，入选{reading['selected']}条，正文不可用或复核失败{reading['failed']}篇"
        if reading.get("reused"):
            line += f" · 复用已完成结果{reading['reused']}篇"
        if reading["budget_exhausted"]:
            line += " · 本轮预算已用尽"
    return line

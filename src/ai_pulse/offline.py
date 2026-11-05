"""离线演示使用固定样本，不构造网络或模型客户端。"""

from importlib.resources import files
from pathlib import Path

from .digest import preview_digest
from .editorial import section_for_entry
from .sources.rss import parse_feed


def fixture_root():
    checkout = Path(__file__).resolve().parents[2] / "tests/fixtures"
    return checkout if checkout.is_dir() else files("ai_pulse").joinpath("fixtures")


def fixture_digest():
    items = parse_feed(fixture_root().joinpath("rss_zh.xml").read_text(encoding="utf-8"))
    candidates = [
        dict(
            item.model_dump(),
            id=index,
            section=section_for_entry(item.model_dump()),
            growth=None,
            sources=[{"id": 0, "name": "内置离线样本", "channel": "示例"}],
        )
        for index, item in enumerate(items, 1)
    ]
    selected = preview_digest(candidates)
    return {
        "local_date": "2026-11-01",
        "created_at": "2026-11-01T00:00:00+00:00",
        "timezone": "Asia/Hong_Kong",
        "mode": "demo",
        "offline": True,
        "items": selected,
        "coverage": {
            "successful": 1,
            "total": 1,
            "results": [{"source": "离线样本", "ok": True}],
            "last_success": None,
        },
    }

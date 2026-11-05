"""回放历史材料与已录制的中文结果，不构造网络或模型客户端。"""

import json
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

from .digest import rank_candidates
from .models import BriefingDigest
from .sources.hn import parse_stories
from .storage import Store


def fixture_root():
    return files("ai_pulse").joinpath("fixtures")


def fixture_digest():
    root = fixture_root()
    manifest = json.loads(root.joinpath("replay.json").read_text(encoding="utf-8"))
    stories = json.loads(root.joinpath("replay_hn.json").read_text(encoding="utf-8"))
    recorded = BriefingDigest.model_validate_json(
        root.joinpath("model_output.json").read_text(encoding="utf-8")
    )
    source = manifest["source"]
    with TemporaryDirectory(prefix="pulse-replay-") as directory:
        # 真实存储与排序只操作临时库，不读取或修改用户的状态库。
        store = Store(Path(directory))
        store.seed_sources([source])
        for item in parse_stories(stories):
            store.save_item(item.model_dump(), 1)
        candidates = rank_candidates(
            store.entries(), store, set(), now=datetime.fromisoformat(manifest["created_at"])
        )
    allowed = {item["id"]: item for item in candidates}
    selected = []
    for chosen in recorded.items:
        original = allowed[chosen.entry_id]
        evidence = [allowed[identity] for identity in chosen.evidence_ids]
        selected.append(
            dict(
                {key: original[key] for key in ("url", "kind", "published_at", "metrics", "growth", "sources")},
                entry_id=chosen.entry_id,
                section=chosen.section,
                title=chosen.title,
                summary=chosen.summary,
                reason=chosen.reason,
                evidence_status=chosen.evidence_status,
                evidence=[{"title": item["title"], "url": item["url"]} for item in evidence],
            )
        )
    return {
        "local_date": manifest["date"],
        "created_at": manifest["created_at"],
        "timezone": "Asia/Hong_Kong",
        "mode": "demo",
        "offline": True,
        "items": selected,
        "coverage": {
            "successful": 1,
            "total": 1,
            "results": [{"source": source["name"], "ok": True, "count": len(stories)}],
            "last_success": manifest["created_at"],
        },
    }

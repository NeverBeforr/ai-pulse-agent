import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ai_pulse.app import create_app
from ai_pulse.config import Settings
from ai_pulse.digest import preview_digest, rank_candidates
from ai_pulse.editorial import section_for_entry
from ai_pulse.models import ModelDigest
from ai_pulse.storage import Store


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path)
    db.seed_sources(
        [
            {
                "name": "Sample",
                "kind": "rss",
                "url": "https://example.com/rss",
                "channel": "官方 RSS",
                "enabled": True,
                "state": "ok",
                "message": "",
            }
        ]
    )
    return db


def candidate(title, kind="news", summary="AI content", **extra):
    return {"title": title, "kind": kind, "summary": summary, **extra}


@pytest.mark.parametrize(
    "entry, expected",
    [
        (candidate("An AI app turns source material into a notebook"), "products"),
        (candidate("New LLM model weights", "repository"), "industry"),
        (candidate("OpenAI changes API pricing"), "industry"),
        (candidate("开源 AI 编程工具", "repository"), "products"),
        (candidate("AI startup raises $100 million in funding"), None),
        (candidate("AI 公司完成融资，估值再创新高"), None),
        (candidate("AI company appoints a new CEO"), None),
        (candidate("AI 公司人事变动，新任CTO到岗"), None),
        (candidate("Another AI chatbot: ChatGPT clone"), None),
        (candidate("AI 通用聊天套壳"), None),
        (candidate("AI startup raises funding and releases an image model"), "industry"),
        (candidate("AI app uses an existing API for medical notes"), "products"),
        (candidate("AI app helps doctors write medical notes using cloud inference"), "products"),
        (candidate("Football weekend highlights", summary="sports only"), None),
    ],
)
def test_editorial_preclassification(entry, expected):
    assert section_for_entry(entry) == expected


def add(store, title, index, kind="news"):
    return store.save_item(
        {
            "title": title,
            "kind": kind,
            "summary": "AI sample with concrete content",
            "url": f"https://example.com/{index}",
            "published_at": None,
            "metrics": {"stars": 10} if kind == "repository" else {},
        },
        1,
    )


def test_each_section_has_its_own_quota_and_candidate_budget(store):
    for i in range(30):
        add(store, f"AI model release {i}", i)
    add(store, "AI app for research notes", 100)
    candidates = rank_candidates(store.entries(), store, set())
    assert sum(row["section"] == "industry" for row in candidates) == 19
    assert sum(row["section"] == "products" for row in candidates) == 1
    items = preview_digest(candidates)
    assert sum(row["section"] == "industry" for row in items) == 5
    assert sum(row["section"] == "products" for row in items) == 1
    assert len(items) == 6


def test_five_plus_five_and_exclusions_even_with_high_popularity(store):
    for i in range(8):
        add(store, f"AI model release {i}", i)
        add(store, f"AI app for designer workflow {i}", 100 + i)
    add(store, "AI company raises $200 million", 200)
    ranked = rank_candidates(store.entries(), store, set())
    items = preview_digest(ranked)
    assert len(items) == 10
    assert all("raises" not in row["title"] for row in items)
    assert [row["section"] for row in items].count("products") == 5


def output_item(index, section="industry"):
    return {
        "entry_id": index,
        "section": section,
        "title": f"AI 动态{index}",
        "summary": "这是中文事实摘要。",
        "reason": "变化有明确意义。",
        "evidence_ids": [index],
        "evidence_status": "媒体报道",
    }


def test_model_output_validation_is_at_the_result_boundary():
    assert ModelDigest.model_validate({"items": []}).items == []
    balanced = [output_item(i) for i in range(1, 6)] + [output_item(i, "products") for i in range(6, 11)]
    assert len(ModelDigest.model_validate({"items": balanced}).items) == 10
    with pytest.raises(ValidationError, match="各最多5条"):
        ModelDigest.model_validate({"items": [output_item(i) for i in range(1, 7)]})
    with pytest.raises(ValidationError, match="重复"):
        ModelDigest.model_validate({"items": [output_item(1), output_item(1)]})
    with pytest.raises(ValidationError, match="中文"):
        ModelDigest.model_validate({"items": [output_item(1) | {"summary": "English only"}]})


def test_legacy_editions_are_read_without_rewriting_payload(store):
    old = {"entry_id": 1, "title": "Original historical entry", "kind": "repository"}
    store.save_digest("2026-10-27", "preview", datetime.now(UTC).isoformat(), [old], {})
    digest = store.digest()
    assert digest["legacy"] is True
    assert digest["sections"]["products"][0]["title"] == old["title"]
    with store.connection() as db:
        raw = json.loads(db.execute("SELECT items FROM digests").fetchone()[0])
    assert "section" not in raw[0]


def test_history_filters_editorial_section_instead_of_repository_type(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, scheduler_enabled=False))
    with TestClient(app) as client:
        db = app.state.store
        now = datetime.now(UTC).isoformat()
        website_case = {"entry_id": 1, "kind": "news", "section": "products", "title": "网页产品案例"}
        model_repo = {"entry_id": 2, "kind": "repository", "section": "industry", "title": "模型仓库热点"}
        db.save_digest("2026-10-28", "preview", now, [website_case], {"format_version": 2})
        db.save_digest("2026-10-27", "preview", now, [model_repo], {"format_version": 2})
        products = client.get("/history?section=products").text
        industry = client.get("/history?section=industry").text
        assert "网页产品案例" in products and "模型仓库热点" not in products
        assert "模型仓库热点" in industry and "网页产品案例" not in industry
        assert "没有匹配" in client.get("/history?section=industry&q=网页").text


def test_empty_section_and_readable_demo(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, scheduler_enabled=False))
    with TestClient(app) as client:
        demo = client.get("/demo").text
        assert 'data-section="industry"' in demo and 'data-section="products"' in demo
        assert "3–5" in demo and "NotebookLM" in demo and "不是今日新闻" in demo
        items = preview_digest(
            rank_candidates(
                [
                    {
                        "id": 1,
                        "title": "AI model release",
                        "kind": "news",
                        "summary": "A new model",
                        "url": "https://example.com/release",
                        "published_at": None,
                        "metrics": {},
                        "sources": [],
                    }
                ],
                app.state.store,
                set(),
            )
        )
        app.state.store.save_digest(
            "2026-10-28",
            "preview",
            datetime.now(UTC).isoformat(),
            items,
            {"format_version": 2, "successful": 1, "total": 1},
        )
        page = client.get("/").text
        assert "本期暂无产品案例" in page
        assert "另一板块凑数" in page

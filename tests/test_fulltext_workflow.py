import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ai_pulse import pipeline
from ai_pulse.errors import BudgetExceeded
from ai_pulse.generate import EditorialModel, split_body, summarize_article
from ai_pulse.models import ArticleReview, CollectedItem, FinalSelection, ReadingNotes, ScoringResult
from ai_pulse.selection import compose_candidates, weighted_score
from ai_pulse.storage import Store

SUMMARY = "这项人工智能工具面向开发者，提供具体的应用流程和可追溯的信息来源。原文介绍了实际用法、部署要求与功能限制，适合用来判断是否能接入现有工作流。使用前仍应核对条件，不应将作者的宣传直接理解为独立验证的效果。"


class FakeEditor:
    requests = []
    capacity = 100000

    def __init__(self, config, store, run_id):
        self.config, self.store, self.run_id = config, store, run_id

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def fits(self, *args):
        return True

    def text_capacity(self, *args):
        return self.capacity

    def remaining_seconds(self):
        return 3600

    async def request(self, schema, instructions, payload, *, stage, context=None):
        self.requests.append((stage, payload))
        if schema is ScoringResult:
            value = {
                "items": [
                    {
                        "entry_id": i["entry_id"],
                        "section": "industry" if i["entry_id"] <= 12 else "products",
                        "scores": dict(
                            relevance=3,
                            impact=3 if i["entry_id"] == 1 else 2,
                            usefulness=3,
                            novelty=3 if i["entry_id"] == 1 else 2,
                        ),
                        "reason": "内容与开发相关，需要正文复核",
                        "topic": "开发实践",
                        "event_key": f"事件{i['entry_id']}",
                        "exclude": False,
                        "needs_read": True,
                    }
                    for i in payload["candidates"]
                ]
            }
        elif schema is ReadingNotes:
            value = {
                "facts": ["开发工具的实际功能"],
                "limitations": ["需核对使用条件"],
                "quotes": [payload["body"][-60:]],
            }
        elif schema is ArticleReview:
            identity = payload["entry_id"]
            quote = payload["body"][-60:] if "body" in payload else payload["reading_notes"][-1]["quotes"][0]
            value = dict(
                selected=True,
                section="industry" if identity <= 12 else "products",
                scores=dict(relevance=3, impact=2, usefulness=3, novelty=2),
                topic="开发实践",
                event_key=f"独立事件{identity}",
                title="人工智能开发工具的实际变化",
                summary=SUMMARY,
                reason="提供实际应用线索并保留功能限制",
                quotes=[quote],
            )
        else:
            assert schema is FinalSelection
            selected = []
            counts = {"industry": 0, "products": 0}
            for item in payload["articles"]:
                if counts[item["section"]] < payload["section_limit"] and len(selected) < payload["items"]:
                    selected.append(item["entry_id"])
                    counts[item["section"]] += 1
            value = dict(entry_ids=selected, reason="独立事件，按板块配额选稿")
        return schema.model_validate(value, context=context)


def test_split_preserves_every_character_and_tail():
    text = ("文章开头说明。\n\n" * 40) + ("限制：无法离线部署。" * 20)
    spans = split_body(text, 300)
    assert "".join(part for _, _, part in spans) == text
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    assert all(len(part.encode()) <= 300 for _, _, part in spans)


async def test_fulltext_model_receives_late_limitation(pulse_config, tmp_path):
    FakeEditor.requests = []
    model = FakeEditor(pulse_config, Store(tmp_path), 1)
    text = "公开文章介绍人工智能工具的实际用法。" * 80 + "末尾重要限制：只能在云端使用，无法离线部署。"
    content = dict(text=text, final_url="https://example.com/article", content_hash="captured")
    review, coverage = await summarize_article(model, dict(id=1, title="AI工具"), content)
    assert FakeEditor.requests[0][1]["body"] == text
    assert "无法离线部署" in FakeEditor.requests[0][1]["body"]
    assert coverage["status"] == "complete" and review.selected


async def test_long_article_is_read_in_full_before_synthesis(pulse_config, tmp_path, monkeypatch):
    monkeypatch.setattr(FakeEditor, "capacity", 700)
    FakeEditor.requests = []
    model = FakeEditor(pulse_config, Store(tmp_path), 1)
    text = "正文的每一个段落都需要读取。\n\n" * 100 + "最后一段限制：不支持离线。"
    _, coverage = await summarize_article(
        model,
        dict(id=1, title="AI工具"),
        dict(text=text, final_url="https://example.com/article", content_hash="recorded"),
    )
    read = [payload["body"] for stage, payload in FakeEditor.requests if stage == "read_chunk"]
    assert "".join(read) == text
    assert coverage["status"] == "chunked_complete" and coverage["spans"][-1][1] == len(text)
    assert FakeEditor.requests[-1][0] == "fulltext_synthesis"


def test_fabricated_quote_or_number_is_rejected():
    body = "原文说这个人工智能工具只支持云端。" * 30
    data = dict(
        selected=True,
        section="products",
        scores=dict(relevance=3, impact=2, usefulness=3, novelty=2),
        topic="工具",
        event_key="工具发布",
        title="人工智能工具的新能力",
        summary=SUMMARY,
        reason="应用流程具体",
        quotes=["原文没有的断言"],
    )
    with pytest.raises(ValidationError, match="依据片段"):
        ArticleReview.model_validate(data, context={"text": body})
    data["quotes"] = ["只支持云端"]
    data["title"] = "人工智能工具提速999倍"
    with pytest.raises(ValidationError, match="数字"):
        ArticleReview.model_validate(data, context={"text": body})


def test_weights_and_event_dedup_respect_five_plus_five(pulse_config):
    pulse_config.output.items = 10
    pulse_config.output.max_items = 10
    rows = [
        dict(
            entry_id=i,
            section="industry" if i <= 8 else "products",
            selection_score=10,
            event_key=f"事件{i}",
            topic="模型" if i % 2 else "工具",
        )
        for i in range(1, 17)
    ]
    rows[1]["event_key"] = rows[0]["event_key"]
    chosen = compose_candidates(rows, pulse_config)
    assert len(chosen) == 10
    assert sum(i["section"] == "industry" for i in chosen) == 5
    assert len({i["event_key"] for i in chosen}) == 10
    assert weighted_score(dict(relevance=3, impact=2, usefulness=1, novelty=0), pulse_config) == 6
    pulse_config.selection.weights.usefulness = 3
    assert weighted_score(dict(relevance=3, impact=2, usefulness=1, novelty=0), pulse_config) == 8


async def test_pipeline_reads_beyond_twenty_candidates_backfills_and_writes_md(
    pulse_config, monkeypatch, tmp_path
):
    from ai_pulse import selection
    from ai_pulse.models import SinkConfig

    pulse_config.output.items = 10
    pulse_config.output.max_items = 10
    pulse_config.sinks = [SinkConfig(type="markdown", path=str(tmp_path / "digest/{date}.md"))]
    FakeEditor.requests = []
    monkeypatch.setattr(selection, "EditorialModel", FakeEditor)

    class Source:
        async def fetch(self, since):
            return [
                CollectedItem(
                    title=f"AI工具的发布与应用 {i}",
                    url=f"https://example.com/{i}",
                    summary="需要进一步阅读原文",
                    published_at="2026-11-01T00:00:00+00:00",
                )
                for i in range(1, 26)
            ]

    async def content(item, network):
        return dict(
            discovery_url=item["url"],
            final_url=item["url"],
            original_url=None,
            method="html",
            status="unavailable" if item["id"] == 1 else "ready",
            text="" if item["id"] == 1 else SUMMARY * 5 + chr(0x4E10 + item["id"]),
            error="HTTP 403" if item["id"] == 1 else "",
        )

    monkeypatch.setattr(pipeline, "create_source", lambda *a: Source())
    monkeypatch.setattr(selection, "fetch_content", content)
    result = await pipeline.run_once(pulse_config, date="2026-11-01")
    assert result.exit_code == 0 and result.state == "success"
    triage = [p for stage, p in FakeEditor.requests if stage == "triage"]
    assert sum(len(p["candidates"]) for p in triage) == 25
    store = Store(Path(pulse_config.state.path).parent, database_path=Path(pulse_config.state.path))
    digest = store.digest(result.digest_id)
    assert len(digest["items"]) == 10
    assert all(item["reading"]["status"] == "complete" for item in digest["items"])
    assert sum(item["section"] == "industry" for item in digest["items"]) == 5
    assert digest["coverage"]["reading"]["failed"] == 1
    assert all(item["entry_id"] != 1 for item in digest["items"])
    assert "完整提取正文" in (tmp_path / "digest/2026-11-01.md").read_text(encoding="utf-8")


async def test_editorial_request_corrects_once_and_counts_budget(pulse_config, monkeypatch, tmp_path):
    requests = []
    clients = []
    valid = {
        "items": [
            dict(
                entry_id=1,
                section="industry",
                scores=dict(relevance=3, impact=2, usefulness=2, novelty=3),
                reason="相关",
                topic="模型",
                event_key="模型更新",
                exclude=False,
                needs_read=True,
            )
        ]
    }

    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            clients.append(self)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def close(self):
            self.closed = True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await self.close()

        async def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=20, completion_tokens=30),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"items":[]}'
                            if len(requests) == 1
                            else json.dumps(valid, ensure_ascii=False)
                        )
                    )
                ],
            )

    monkeypatch.setattr("ai_pulse.generate.AsyncOpenAI", Client)
    pulse_config.selection.max_model_calls = 2
    store = Store(tmp_path)
    async with EditorialModel(pulse_config, store, 1) as model:
        result = await model.request(
            ScoringResult,
            "按画像评分",
            {"candidates": [{"entry_id": 1}]},
            stage="triage",
            context={"ids": [1]},
        )
        assert len(result.items) == 1 and len(requests) == 2
        with pytest.raises(BudgetExceeded):
            await model.request(
                ScoringResult, "按画像评分", {"candidates": []}, stage="triage", context={"ids": []}
            )
    assert len(store.usage(1)) == 2
    assert all(u["stage"] == "triage" for u in store.usage(1))
    assert len(clients) == 2 and all(client.closed for client in clients)


async def test_final_failure_retry_reuses_validated_body_reviews(pulse_config, monkeypatch):
    from test_status_semantics import fake_sinks

    from ai_pulse import selection
    from ai_pulse.errors import GenerationError

    pulse_config.output.items = 10
    pulse_config.output.max_items = 10
    FakeEditor.requests = []
    final_attempts = []
    fetched = []

    class InterruptedEditor(FakeEditor):
        async def request(self, schema, instructions, payload, **kwargs):
            if schema is FinalSelection:
                final_attempts.append(True)
                if len(final_attempts) == 1:
                    raise GenerationError("模拟最终选稿接口断开")
            return await super().request(schema, instructions, payload, **kwargs)

    class Source:
        async def fetch(self, since):
            return [
                CollectedItem(
                    title=f"AI工具发布 {i}",
                    url=f"https://example.com/{i}",
                    summary="需要读原文",
                    published_at="2026-11-01T00:00:00+00:00",
                )
                for i in range(1, 26)
            ]

    async def content(item, network):
        fetched.append(item["id"])
        return dict(
            discovery_url=item["url"],
            final_url=item["url"],
            original_url=None,
            method="html",
            status="ready",
            text=SUMMARY * 5 + chr(0x4E10 + item["id"]),
            error="",
        )

    monkeypatch.setattr(pipeline, "create_source", lambda *a: Source())
    monkeypatch.setattr(selection, "fetch_content", content)
    monkeypatch.setattr(selection, "EditorialModel", InterruptedEditor)
    fake_sinks(monkeypatch)
    with pytest.raises(GenerationError, match="最终选稿"):
        await pipeline.run_once(pulse_config, date="2026-11-01")
    before = len([p for stage, p in FakeEditor.requests if stage == "fulltext"])
    fetched_before = len(fetched)
    result = await pipeline.run_once(pulse_config, date="2026-11-01")
    assert result.exit_code == 0
    assert result.coverage["reading"]["reused"] >= 10
    assert len([p for stage, p in FakeEditor.requests if stage == "fulltext"]) == before
    assert len(fetched) == fetched_before


@pytest.mark.parametrize("available,expected_code,expected_state", [(0, 4, "failed"), (3, 1, "degraded")])
async def test_unavailable_bodies_and_shortage_are_visible(
    pulse_config, monkeypatch, available, expected_code, expected_state
):
    from test_status_semantics import fake_sinks

    from ai_pulse import selection

    pulse_config.output.items = 10
    pulse_config.output.max_items = 10
    monkeypatch.setattr(selection, "EditorialModel", FakeEditor)
    fake_sinks(monkeypatch)

    class Source:
        async def fetch(self, since):
            return [
                CollectedItem(
                    title=f"AI工具 {i}",
                    url=f"https://example.com/{i}",
                    summary="应用案例",
                    published_at="2026-11-01T00:00:00+00:00",
                )
                for i in range(1, 4)
            ]

    async def content(item, network):
        return dict(
            discovery_url=item["url"],
            final_url=item["url"],
            original_url=None,
            method="html",
            status="ready" if available else "insufficient",
            text=SUMMARY * 5 + chr(0x4E10 + item["id"]) if available else "",
            error="" if available else "只有标题，没有正文",
        )

    monkeypatch.setattr(pipeline, "create_source", lambda *a: Source())
    monkeypatch.setattr(selection, "fetch_content", content)
    result = await pipeline.run_once(pulse_config, date="2026-11-01")
    assert (result.exit_code, result.state) == (expected_code, expected_state)
    assert result.coverage["reading"]["selected"] == available


def test_rss_full_content_is_preserved_separately():
    from ai_pulse.sources.rss import parse_feed

    text = '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>AI</title><item><title>AI工具</title><link>https://example.com/full</link><description>简短摘要</description><content:encoded><![CDATA[<p>原始文章正文与使用限制。</p>]]></content:encoded></item></channel></rss>'
    row = parse_feed(text)[0]
    assert row.summary == "简短摘要"
    assert "原始文章正文与使用限制" in row.feed_content


async def test_runtime_budget_also_stops_body_fetching_without_model_calls(pulse_config, monkeypatch):
    from ai_pulse import selection

    class Exhausted(FakeEditor):
        def remaining_seconds(self):
            return 0

    class Source:
        async def fetch(self, since):
            return [
                CollectedItem(
                    title="AI工具发布",
                    url="https://example.com/budget",
                    summary="应用案例",
                    published_at="2026-11-01T00:00:00+00:00",
                )
            ]

    async def forbidden(*args):
        raise AssertionError("预算耗尽后不能继续读取候补正文")

    monkeypatch.setattr(selection, "EditorialModel", Exhausted)
    monkeypatch.setattr(selection, "fetch_content", forbidden)
    monkeypatch.setattr(pipeline, "create_source", lambda *a: Source())
    result = await pipeline.run_once(pulse_config, date="2026-11-01")
    assert result.exit_code == 4 and result.coverage["reading"]["budget_exhausted"]


async def test_request_timeout_at_runtime_deadline_preserves_budget_semantics(
    pulse_config, monkeypatch, tmp_path
):
    import httpx
    from openai import APITimeoutError

    class TimeoutClient:
        def __init__(self, **kwargs):
            self.http = kwargs["http_client"]
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await self.http.aclose()

        async def create(self, **kwargs):
            raise APITimeoutError(request=httpx.Request("POST", "https://example.com/v1/chat/completions"))

    monkeypatch.setattr("ai_pulse.generate.AsyncOpenAI", TimeoutClient)
    model = EditorialModel(pulse_config, Store(tmp_path), 1)
    clock = iter([1, -1])
    monkeypatch.setattr(model, "remaining_seconds", lambda: next(clock))
    with pytest.raises(BudgetExceeded, match="时间预算"):
        await model.request(ScoringResult, "初筛", {"candidates": []}, stage="triage", context={"ids": []})

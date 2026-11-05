import json
from types import SimpleNamespace

import httpx
import pytest

from ai_pulse.agent import CloudAgent
from ai_pulse.config import Settings
from ai_pulse.digest import rank_candidates
from ai_pulse.storage import Store


async def test_cloud_template_with_fake_client_only(tmp_path, monkeypatch):
    """Local contract check, not an external API integration test."""
    store = Store(tmp_path)
    store.seed_sources(
        [
            {
                "name": "Official",
                "kind": "rss",
                "url": "https://example.com/feed",
                "channel": "官方 RSS",
                "enabled": True,
                "state": "ok",
                "message": "",
            }
        ]
    )
    entry_id = store.save_item(
        {
            "title": "A new AI model",
            "url": "https://example.com/ai",
            "kind": "news",
            "summary": "A release",
            "published_at": None,
            "metrics": {},
        },
        1,
    )
    chosen = {
        "items": [
            {
                "entry_id": entry_id,
                "section": "industry",
                "title": "新模型发布",
                "summary": "自建测试材料的摘要。",
                "reason": "验证引用映射。",
                "evidence_ids": [entry_id],
                "evidence_status": "官方已确认",
            }
        ]
    }

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs):
            assert kwargs["model"] == "test-model"
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
                choices=[
                    SimpleNamespace(message=SimpleNamespace(tool_calls=None, content=json.dumps(chosen)))
                ],
            )

        async def close(self):
            pass

    monkeypatch.setattr("ai_pulse.agent.AsyncOpenAI", FakeClient)
    settings = Settings(data_dir=tmp_path, llm_api_key="fake-not-a-real-key", llm_model="test-model")
    agent = CloudAgent(settings, store, None, 1)
    result = await agent.generate(rank_candidates(store.entries(), store, set()))
    assert result[0]["evidence"][0]["url"] == "https://example.com/ai"
    assert len(store.usage()) == 1
    assert store.usage()[0]["estimated_cost"] is None


def test_cloud_template_requires_explicit_configuration(tmp_path):
    with pytest.raises(ValueError, match="API Key"):
        CloudAgent(Settings(data_dir=tmp_path, llm_api_key="", llm_model=""), None, None, 1)


async def test_unavailable_article_returns_evidence_status_instead_of_aborting(tmp_path):
    class ForbiddenNetwork:
        async def get(self, url):
            request = httpx.Request("GET", url)
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError("Forbidden", request=request, response=response)

    agent = CloudAgent(
        Settings(data_dir=tmp_path, llm_api_key="fake-key", llm_model="test-model"),
        None,
        ForbiddenNetwork(),
        1,
    )
    try:
        result = await agent.tool("read_article", {"url": "https://example.com/restricted"}, [])
        assert result["state"] == "unavailable"
        assert result["http_status"] == 403
        assert "text" not in result
    finally:
        await agent.client.close()


async def test_mocked_tool_round_preserves_merged_evidence_and_final_section(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.seed_sources(
        [
            {
                "name": "Official",
                "kind": "rss",
                "url": "https://example.com/rss",
                "channel": "官方 RSS",
                "enabled": True,
                "state": "ok",
                "message": "",
            },
            {
                "name": "Media",
                "kind": "rss",
                "url": "https://media.example/rss",
                "channel": "媒体",
                "enabled": True,
                "state": "ok",
                "message": "",
            },
        ]
    )
    for index, title in enumerate(("AI app announcement", "AI app explained"), 1):
        store.save_item(
            {
                "title": title,
                "url": f"https://example.com/article-{index}",
                "kind": "news",
                "summary": "An AI app with a public announcement",
                "published_at": None,
                "metrics": {},
            },
            index,
        )
    candidates = rank_candidates(store.entries(), store, set())
    chosen = {
        "items": [
            {
                "entry_id": 1,
                "section": "industry",
                "title": "官方应用的重要更新",
                "summary": "公开资料说明了应用的重要更新，两篇来源报道的是同一事件。",
                "reason": "同一事件合并为一个名额。",
                "evidence_ids": [1, 2],
                "evidence_status": "官方已确认",
            }
        ]
    }
    requests = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                tool = SimpleNamespace(
                    id="call-1", function=SimpleNamespace(name="read_article", arguments='{"entry_id":1}')
                )
                message = SimpleNamespace(
                    tool_calls=[tool],
                    model_dump=lambda **kwargs: {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "read_article", "arguments": '{"entry_id":1}'},
                            }
                        ],
                    },
                )
            else:
                assert any(m["role"] == "tool" and "公开产品资料" in m["content"] for m in kwargs["messages"])
                message = SimpleNamespace(tool_calls=None, content=json.dumps(chosen))
            return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=message)])

        async def close(self):
            pass

    class FakeNetwork:
        async def get(self, url):
            assert url == "https://example.com/article-1"
            return SimpleNamespace(text="<article>公开产品资料</article>")

    monkeypatch.setattr("ai_pulse.agent.AsyncOpenAI", FakeClient)
    agent = CloudAgent(
        Settings(data_dir=tmp_path, llm_api_key="fake-key", llm_model="test-model"), store, FakeNetwork(), 1
    )
    result = await agent.generate(candidates)
    assert len(result) == 1 and result[0]["section"] == "industry"
    assert len(result[0]["evidence"]) == 2
    assert {source["name"] for source in result[0]["sources"]} == {"Official", "Media"}
    assert len(store.usage()) == 2

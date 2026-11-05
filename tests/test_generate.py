import json
from types import SimpleNamespace

import pytest

from ai_pulse.errors import GenerationError
from ai_pulse.generate import generate_single_pass
from ai_pulse.offline import fixture_digest


def candidates():
    return [dict(item, id=item["entry_id"]) for item in fixture_digest()["items"]]


def reply():
    return json.dumps(
        {
            "items": [
                {k: i[k] for k in ("entry_id", "section", "title", "summary", "reason")}
                | {"evidence_ids": [i["entry_id"]], "evidence_status": "材料有限"}
                for i in candidates()
            ]
        },
        ensure_ascii=False,
    )


def mock_client(monkeypatch, outputs):
    requests = []

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def create(self, **kwargs):
            requests.append(kwargs)
            chosen = outputs[min(len(requests) - 1, len(outputs) - 1)]
            if isinstance(chosen, Exception):
                raise chosen
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=200),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=outputs[min(len(requests) - 1, len(outputs) - 1)])
                    )
                ],
            )

    monkeypatch.setattr("ai_pulse.generate.AsyncOpenAI", Client)
    return requests


async def test_normal_generation_calls_model_once(pulse_config, monkeypatch):
    requests = mock_client(monkeypatch, [reply()])
    result = await generate_single_pass(pulse_config, candidates(), [])
    assert len(result.items) == 5 and len(requests) == 1
    assert "tools" not in requests[0]


async def test_invalid_json_has_only_one_correction(pulse_config, monkeypatch):
    requests = mock_client(monkeypatch, ["not-json", reply()])
    assert len((await generate_single_pass(pulse_config, candidates(), [])).items) == 5
    assert len(requests) == 2 and "校验失败" in requests[1]["messages"][-1]["content"]


async def test_repeated_invalid_output_fails_visibly(pulse_config, monkeypatch):
    requests = mock_client(monkeypatch, ["bad"])
    with pytest.raises(GenerationError, match="校验失败"):
        await generate_single_pass(pulse_config, candidates(), [])
    assert len(requests) == 2


async def test_input_is_truncated_before_call(pulse_config, monkeypatch):
    pulse_config.model.max_input_tokens = 4500
    rows = [
        dict(candidates()[0], id=i, entry_id=i, url="https://example.com/" + ("long" * 200))
        for i in range(1, 21)
    ]
    requests = mock_client(monkeypatch, ['{"items":[]}'])
    await generate_single_pass(pulse_config, rows, [])
    payload = json.loads(requests[0]["messages"][1]["content"])
    assert len(payload["candidates"]) < 20
    assert sum(len(m["content"].encode()) for m in requests[0]["messages"]) + 256 < 4500


async def test_reported_over_budget_reduces_candidates_once(pulse_config, monkeypatch):
    sizes = []

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def create(self, **kwargs):
            sizes.append(len(json.loads(kwargs["messages"][1]["content"])["candidates"]))
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=50000 if len(sizes) == 1 else 100, completion_tokens=1),
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"items":[]}'))],
            )

    monkeypatch.setattr("ai_pulse.generate.AsyncOpenAI", Client)
    await generate_single_pass(pulse_config, candidates(), [])
    assert len(sizes) == 2 and sizes[1] < sizes[0]


async def test_agent_mode_keeps_legacy_generator_and_applies_output_quota(pulse_config, monkeypatch):
    from ai_pulse.pipeline import generate_items

    called = []

    class Agent:
        def __init__(self, *args):
            pass

        async def generate(self, rows):
            called.append(True)
            return [
                dict(candidates()[0], entry_id=i, section="industry" if i < 6 else "products")
                for i in range(1, 11)
            ]

    monkeypatch.setattr("ai_pulse.pipeline.CloudAgent", Agent)
    result = await generate_items(pulse_config, [], None, 1, None, agent_mode=True)
    assert called and len(result) == 5
    assert sum(item["section"] == "industry" for item in result) == 3


async def test_model_error_keeps_reason_without_secret_or_automatic_retry(pulse_config, monkeypatch):
    import httpx
    from openai import BadRequestError

    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    error = BadRequestError(
        "不支持参数 fake-model-secret", response=httpx.Response(400, request=request), body={}
    )
    requests = mock_client(monkeypatch, [error])
    with pytest.raises(GenerationError) as exc:
        await generate_single_pass(pulse_config, candidates(), [])
    assert "不支持参数" in str(exc.value) and "fake-model-secret" not in str(exc.value)
    assert len(requests) == 1

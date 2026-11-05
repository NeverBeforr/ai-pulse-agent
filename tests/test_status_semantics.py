from pathlib import Path

import httpx

from ai_pulse import pipeline
from ai_pulse.errors import SourceFailure
from ai_pulse.sources.rss import parse_feed
from ai_pulse.storage import Store


def fake_sources(monkeypatch, failing=()):
    class Source:
        def __init__(self, config):
            self.config = config

        async def fetch(self, since):
            if self.config.id in failing:
                raise SourceFailure("http", "HTTP 403")
            return parse_feed((Path(__file__).parent / "fixtures/rss_zh.xml").read_text(encoding="utf-8"))

    monkeypatch.setattr(pipeline, "create_source", lambda config, network: Source(config))


def fake_generation(monkeypatch):
    async def generate(config, candidates, *args, **kwargs):
        return pipeline.preview_digest(candidates)[: config.output.items]

    monkeypatch.setattr(pipeline, "generate_items", generate)


def fake_sinks(monkeypatch, failing=False):
    class Sink:
        async def send(self, digest, items):
            if failing:
                raise RuntimeError("明确的投递失败")

    monkeypatch.setattr(pipeline, "create_sink", lambda config: Sink())


async def test_all_sources_failed_is_failed_exit4(pulse_config, monkeypatch):
    fake_sources(monkeypatch, {"hn", "official-rss"})
    fake_generation(monkeypatch)
    fake_sinks(monkeypatch)
    result = await pipeline.run_once(pulse_config, date="2026-11-01")
    assert result.exit_code == 4 and result.state == "failed"
    store = Store(
        __import__("pathlib").Path(pulse_config.state.path).parent,
        database_path=__import__("pathlib").Path(pulse_config.state.path),
    )
    assert store.runs()[0]["state"] == "failed" and store.digest() is None


async def test_partial_sources_are_partial_exit1(pulse_config, monkeypatch):
    fake_sources(monkeypatch, {"hn"})
    fake_generation(monkeypatch)
    fake_sinks(monkeypatch)
    result = await pipeline.run_once(pulse_config, date="2026-11-01")
    assert result.exit_code == 1 and result.state == "partial"
    assert result.coverage["successful"] == 1


async def test_sink_all_failed_is_degraded_exit1(pulse_config, monkeypatch):
    fake_sources(monkeypatch)
    fake_generation(monkeypatch)
    fake_sinks(monkeypatch, True)
    result = await pipeline.run_once(pulse_config, date="2026-11-01")
    assert result.exit_code == 1 and result.state == "degraded" and result.digest_id


async def test_pool_timeout_is_client_failure(pulse_config, monkeypatch, caplog):
    class Source:
        async def fetch(self, since):
            raise httpx.PoolTimeout("pool")

    monkeypatch.setattr(pipeline, "create_source", lambda config, network: Source())
    closed = []

    class Network:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(True)

    monkeypatch.setattr(pipeline, "Network", Network)
    result = await pipeline.run_once(pulse_config, date="2026-11-01")
    assert result.exit_code == 4 and result.state == "failed"
    assert len(closed) == 2 and "客户端故障" in caplog.text


async def test_success_and_fresh_network_per_run(pulse_config, monkeypatch):
    fake_sources(monkeypatch)
    fake_generation(monkeypatch)
    fake_sinks(monkeypatch)
    contexts = []

    class Network:
        async def __aenter__(self):
            contexts.append("打开")
            return self

        async def __aexit__(self, *args):
            contexts.append("关闭")

    monkeypatch.setattr(pipeline, "Network", Network)
    result = await pipeline.run_once(pulse_config, date="2026-11-01")
    assert result.state == "success" and result.exit_code == 0
    assert result.coverage["selected_sources"] == {"hn": 5, "official-rss": 5}
    assert result.coverage["generation"]["input_tokens"] is None
    assert contexts == ["打开", "关闭"]


async def test_cancelled_run_is_aborted_and_client_is_closed(pulse_config, monkeypatch):
    import asyncio
    from pathlib import Path

    import pytest

    class Source:
        async def fetch(self, since):
            raise asyncio.CancelledError()

    monkeypatch.setattr(pipeline, "create_source", lambda config, network: Source())
    with pytest.raises(asyncio.CancelledError):
        await pipeline.run_once(pulse_config, date="2026-11-01")
    path = Path(pulse_config.state.path)
    assert Store(path.parent, database_path=path).runs()[0]["state"] == "aborted"

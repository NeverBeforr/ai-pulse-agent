import json
import runpy
import sys
from pathlib import Path

import pytest

from ai_pulse.cli import main
from ai_pulse.offline import fixture_digest, fixture_root


def test_recorded_replay_uses_real_parsing_and_ranking(monkeypatch):
    from ai_pulse import offline

    calls = []
    parser, rank = offline.parse_stories, offline.rank_candidates

    def parse(stories):
        calls.append("parse")
        return parser(stories)

    def ranked(*args, **kwargs):
        calls.append("rank")
        return rank(*args, **kwargs)

    monkeypatch.setattr(offline, "parse_stories", parse)
    monkeypatch.setattr(offline, "rank_candidates", ranked)
    digest = fixture_digest()
    recorded = json.loads(fixture_root().joinpath("model_output.json").read_text(encoding="utf-8"))
    assert calls == ["parse", "rank"]
    assert digest["local_date"] == "2026-10-30"
    assert [i["summary"] for i in digest["items"]] == [i["summary"] for i in recorded["items"]]
    assert len({i["summary"] for i in digest["items"]}) == 5
    assert all("example.com" not in i["url"] and i["evidence"] for i in digest["items"])
    assert any("Simantic" in i["title"] for i in digest["items"])
    assert any("Fabivo" in i["title"] for i in digest["items"])
    assert fixture_digest() == digest


def test_dry_run_separates_recorded_sources_from_unchecked_configuration(capsys, tmp_path):
    config = tmp_path / "pulse.yaml"
    config.write_text(Path("tests/fixtures/pulse.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["run", "--config", str(config), "--dry-run", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["coverage"]["checked"] is False
    assert result["coverage"]["configured_sources"] == 2
    assert result["coverage"]["recorded"]["successful"] == 1
    assert "当前来源未检查" in result["markdown"]
    assert "覆盖 0/" not in result["markdown"]
    assert not (tmp_path / "data").exists()


def test_module_demo_does_not_start_web_or_construct_network(monkeypatch, capsys):
    import httpx
    import uvicorn

    def deny(*args, **kwargs):
        raise AssertionError("模块入口不得启动服务或联网")

    monkeypatch.setattr(uvicorn, "run", deny)
    monkeypatch.setattr(httpx, "AsyncClient", deny)
    monkeypatch.setattr(sys, "argv", ["ai_pulse", "demo"])
    with pytest.raises(SystemExit) as result:
        runpy.run_module("ai_pulse", run_name="__main__")
    assert result.value.code == 0
    assert "Simantic" in capsys.readouterr().out

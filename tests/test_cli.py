import json

import httpx

# 测试替换HTTPX构造器前，先加载会继承其客户端类型的SDK。
import openai  # noqa: F401

from ai_pulse.cli import main


def test_demo_needs_no_client_or_key(monkeypatch, capsys):
    def deny(*args, **kwargs):
        raise AssertionError("demo不能构造客户端")

    monkeypatch.setattr(httpx, "AsyncClient", deny)
    assert main(["demo"]) == 0
    output = capsys.readouterr().out
    assert "行业热点" in output and "产品案例" in output and "离线" in output


def test_dry_run_is_strictly_offline(monkeypatch, capsys):
    def deny(*args, **kwargs):
        raise AssertionError("dry-run不能构造客户端")

    monkeypatch.setattr(httpx, "AsyncClient", deny)
    assert main(["run", "--dry-run", "--config", "tests/fixtures/pulse.yaml"]) == 0
    assert "离线预览" in capsys.readouterr().out


def test_json_stdout_is_one_object(capsys):
    assert main(["run", "--dry-run", "--json", "--config", "tests/fixtures/pulse.yaml"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["exit_code"] == 0 and payload["state"] == "preview"


def test_config_exit_code_and_json(capsys, tmp_path):
    assert main(["run", "--config", str(tmp_path / "missing.yaml"), "--json"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["exit_code"] == 2
    assert "配置错误" in captured.err


def test_runtime_failure_is_visible_and_secrets_are_redacted(pulse_config, monkeypatch, capsys):
    async def fail(*args, **kwargs):
        raise RuntimeError("fake-model-secret https://example.com/bot/fake-webhook-secret")

    monkeypatch.setattr("ai_pulse.pipeline.run_once", fail)
    assert (
        main(
            [
                "run",
                "--config",
                str(__import__("pathlib").Path(pulse_config.state.path).parents[1] / "pulse.yaml"),
                "--json",
                "--verbose",
            ]
        )
        == 3
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out)["exit_code"] == 3
    assert "fake-model-secret" not in captured.out + captured.err
    assert "fake-webhook-secret" not in captured.out + captured.err


def test_bad_date_is_configuration_error(capsys):
    assert (
        main(["run", "--dry-run", "--config", "tests/fixtures/pulse.yaml", "--date", "20261105", "--json"])
        == 2
    )
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2


def test_status_reports_failed_sources(pulse_config, monkeypatch, capsys):
    import asyncio
    from pathlib import Path

    from test_status_semantics import fake_sources

    from ai_pulse import pipeline

    fake_sources(monkeypatch, {"hn", "official-rss"})
    asyncio.run(pipeline.run_once(pulse_config, date="2026-11-01"))
    assert (
        main(["status", "--config", str(Path(pulse_config.state.path).parents[1] / "pulse.yaml"), "--json"])
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["last_run"]["state"] == "failed"
    assert all(source["state"] == "error" for source in data["sources"])


def test_bad_cli_arguments_still_emit_one_json(capsys):
    assert main(["run", "--unknown-option", "--json"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["exit_code"] == 2
    assert "命令参数错误" in captured.err


def test_legacy_serve_uses_configured_state_without_starting_a_server(pulse_config, monkeypatch):
    from pathlib import Path

    called = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: called.append(app.state.store.path))
    config_path = Path(pulse_config.state.path).parents[1] / "pulse.yaml"
    assert main(["serve", "--config", str(config_path)]) == 0
    assert called == [Path(pulse_config.state.path)]


def test_status_names_delivered_channels(pulse_config, monkeypatch, capsys):
    import asyncio
    from pathlib import Path

    from test_status_semantics import fake_generation, fake_sinks, fake_sources

    from ai_pulse import pipeline

    fake_sources(monkeypatch)
    fake_generation(monkeypatch)
    fake_sinks(monkeypatch)
    asyncio.run(pipeline.run_once(pulse_config, date="2026-11-01"))
    assert (
        main(["status", "--config", str(Path(pulse_config.state.path).parents[1] / "pulse.yaml"), "--json"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["last_digest"]["delivered"] == ["feishu", "markdown"]


def test_status_does_not_reuse_success_for_an_old_source_url(pulse_config, capsys):
    from pathlib import Path

    from ai_pulse.storage import Store

    path = Path(pulse_config.state.path)
    store = Store(path.parent, database_path=path)
    store.seed_sources(
        [
            {
                "name": "hn",
                "kind": "hn",
                "url": "https://old.example.com/",
                "channel": "旧地址",
                "enabled": True,
                "state": "ok",
                "message": "旧成功",
            }
        ]
    )
    assert main(["status", "--config", str(path.parents[1] / "pulse.yaml"), "--json"]) == 0
    sources = json.loads(capsys.readouterr().out)["sources"]
    assert len(sources) == 2 and all(source["state"] == "unverified" for source in sources)

from pathlib import Path

import pytest
import yaml

from ai_pulse.config import init_config, load_config
from ai_pulse.errors import ConfigError


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    data = yaml.safe_load(Path("tests/fixtures/pulse.yaml").read_text(encoding="utf-8"))
    path = tmp_path / "pulse.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    for key, value in {
        "LLM_BASE_URL": "https://example.com/v1",
        "LLM_MODEL": "test-model",
        "LLM_API_KEY": "secret-model",
        "FEISHU_WEBHOOK": "https://example.com/bot/secret-webhook",
    }.items():
        monkeypatch.setenv(key, value)
    return path


def test_missing_env_is_named(config_file, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY")
    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        load_config(config_file)


def test_unknown_fields_are_not_ignored(config_file):
    data = yaml.safe_load(config_file.read_text())
    data["surprise"] = True
    config_file.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="可用顶层字段"):
        load_config(config_file)


def test_plain_secrets_are_rejected(config_file):
    data = yaml.safe_load(config_file.read_text())
    data["model"]["api_key"] = "not-allowed-secret"
    config_file.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError) as error:
        load_config(config_file)
    assert "not-allowed-secret" not in str(error.value)


def test_legacy_migration_never_copies_secrets(tmp_path):
    legacy = tmp_path / "settings.json"
    legacy.write_text('{"llm_api_key":"old-secret","timezone":"Asia/Hong_Kong"}')
    target = init_config(tmp_path / "pulse.yaml", legacy)
    text = target.read_text(encoding="utf-8")
    assert "old-secret" not in text and "${LLM_API_KEY}" in text
    with pytest.raises(ConfigError, match="不会覆盖"):
        init_config(target)


def test_offline_configuration_preserves_unbound_refs(config_file, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY")
    config = load_config(config_file, offline=True)
    assert "LLM_API_KEY" in config._unbound_env


def test_chinese_titles_keep_latin_ai_terms():
    from ai_pulse.sources.common import is_ai

    assert is_ai("AI笔记工具更新") and is_ai("模型API工具使用GPT生成摘要")
    assert not is_ai("ordinary email about football")


def test_nested_unknown_field_lists_available_model_fields(config_file):
    data = yaml.safe_load(config_file.read_text())
    data["model"]["surprise"] = 1
    config_file.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError) as exc:
        load_config(config_file)
    assert "model.surprise" in str(exc.value) and "temperature" in str(exc.value)


def test_webhook_configuration_error_never_contains_secret(config_file, monkeypatch):
    monkeypatch.setenv("FEISHU_WEBHOOK", "plain-secret-without-url")
    with pytest.raises(ConfigError) as exc:
        load_config(config_file)
    assert "webhook" in str(exc.value) and "plain-secret-without-url" not in str(exc.value)


def test_unknown_source_lists_registered_types(config_file):
    data = yaml.safe_load(config_file.read_text())
    data["sources"][0]["type"] = "unknown"
    config_file.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError) as exc:
        load_config(config_file)
    assert "已注册来源" in str(exc.value) and "imap" in str(exc.value)


def test_yaml_error_names_line_without_echoing_content(config_file):
    config_file.write_text("model: [\n  secret-value\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(config_file)
    assert "行" in str(exc.value) and "secret-value" not in str(exc.value)


def test_standard_model_environment_overrides_custom_reference(config_file):
    data = yaml.safe_load(config_file.read_text())
    data["model"]["name"] = "${UNBOUND_CUSTOM_MODEL}"
    config_file.write_text(yaml.safe_dump(data))
    assert load_config(config_file).model.name == "test-model"


def test_disabled_source_does_not_require_unused_credentials(config_file):
    data = yaml.safe_load(config_file.read_text())
    data["sources"].append({"id": "mail-off", "type": "imap", "enabled": False})
    config_file.write_text(yaml.safe_dump(data))
    assert load_config(config_file).sources[-1].password is None


def test_whitespace_key_is_a_named_configuration_error(config_file, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "   ")
    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        load_config(config_file)

import json
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml
from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError
from .models import ModelConfig, PulseConfig, SinkConfig, SourceConfig

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    model_mode: Literal["preview", "cloud"] = "preview"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_trust_env: bool = True
    github_token: str = ""
    schedule_time: str = "09:00"
    timezone: str = "Asia/Hong_Kong"
    scheduler_enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    llm_input_price: float | None = None
    llm_output_price: float | None = None
    data_dir: Path = ROOT / "data"
    database_path: Path | None = None

    @field_validator("schedule_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("时间格式应为 HH:MM")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_zone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("llm_input_price", "llm_output_price")
    @classmethod
    def validate_price(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("价格不能为负数")
        return value

    def public(self) -> dict:
        values = self.model_dump(
            mode="json", exclude={"llm_api_key", "github_token", "data_dir", "database_path"}
        )
        return values | {"has_api_key": bool(self.llm_api_key), "has_github_token": bool(self.github_token)}


def load_settings(data_dir: Path | None = None) -> Settings:
    values = {"data_dir": data_dir} if data_dir else {}
    base = Settings(**values)
    path = base.data_dir / "settings.json"
    if path.exists():
        values.update(json.loads(path.read_text(encoding="utf-8")))
    for key in ("llm_api_key", "github_token"):
        values.pop(key, None)
    values["scheduler_enabled"] = False
    return Settings(**values)


def save_settings(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / "settings.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        settings.model_dump_json(indent=2, exclude={"data_dir", "llm_api_key", "github_token"}),
        encoding="utf-8",
    )
    temporary.replace(path)


ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SECRET_FIELDS = {"api_key", "webhook", "password", "signing_secret"}


def redact(value, secrets=()) -> str:
    text = str(value)
    for secret in sorted((v for v in secrets if v), key=len, reverse=True):
        text = text.replace(secret, "[已隐藏]")
    return text


def load_config(path=None, *, offline=False) -> PulseConfig:
    from .sources import SOURCES

    file = Path(path or os.environ.get("PULSE_CONFIG", "pulse.yaml")).resolve()
    if not file.exists():
        legacy = file.parent / "data/settings.json"
        hint = (
            "；检测到旧配置，请执行 pulse init --from-legacy data/settings.json"
            if legacy.exists()
            else "；请执行 pulse init"
        )
        raise ConfigError(f"找不到配置文件{file}{hint}")
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f"第{mark.line + 1}行、第{mark.column + 1}列" if mark else "请检查缩进与标点"
        raise ConfigError(f"YAML语法错误：{location}") from exc
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件：{type(exc).__name__}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(
            "pulse.yaml顶层必须是映射，可用字段：version, timezone, schedule, output, model, sources, sinks, state"
        )
    values, unbound = [], set()

    def expand(node, key="", disabled=False):
        if isinstance(node, dict):
            disabled = disabled or node.get("enabled") is False
            return {k: expand(v, k, disabled) for k, v in node.items()}
        if isinstance(node, list):
            return [expand(v, key, disabled) for v in node]
        if not isinstance(node, str):
            return node
        if key in SECRET_FIELDS and node and not ENV_REF.fullmatch(node):
            raise ConfigError(f"{key}只能使用环境变量引用，例如 ${{ENV_VAR}}，不能写入明文")
        override = {"base_url": "LLM_BASE_URL", "name": "LLM_MODEL", "api_key": "LLM_API_KEY"}.get(key)
        if override and os.environ.get(override, "").strip():
            value = os.environ[override]
            if key in SECRET_FIELDS:
                values.append(value)
            return value

        def substitute(match):
            name = match[1]
            value = os.environ.get(name)
            if not value or not value.strip():
                if offline or disabled:
                    unbound.add(name)
                    return match[0]
                raise ConfigError(f"缺少环境变量：{name}")
            if key in SECRET_FIELDS:
                values.append(value)
            return value

        return ENV_REF.sub(substitute, node)

    expanded = expand(raw)
    try:
        config = PulseConfig.model_validate(expanded)
    except (ValidationError, ValueError, KeyError) as exc:
        if isinstance(exc, ValidationError):
            details = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False)]
            message = "；".join(details)
        else:
            message = "timezone无效或配置结构不正确"
        available = (
            "；可用顶层字段："
            + ", ".join(PulseConfig.model_fields)
            + "；model可用字段："
            + ", ".join(ModelConfig.model_fields)
            + "；source可用字段："
            + ", ".join(SourceConfig.model_fields)
            + "；sink可用字段："
            + ", ".join(SinkConfig.model_fields)
            + "；已注册来源："
            + ", ".join(sorted(SOURCES))
        )
        raise ConfigError(redact(message + available, values)) from exc
    signing_secret = os.environ.get("FEISHU_SIGNING_SECRET")
    if signing_secret:
        if not signing_secret.strip():
            raise ConfigError("环境变量FEISHU_SIGNING_SECRET不能只包含空白")
        for sink in config.sinks:
            if sink.type == "feishu" and sink.signing_secret is None:
                sink.signing_secret = SecretStr(signing_secret)
                values.append(signing_secret)
    for source in config.sources:
        if source.enabled and source.type not in SOURCES:
            raise ConfigError(
                f"来源{source.id}的类型{source.type}未注册；已注册：{', '.join(sorted(SOURCES))}"
            )
    for sink in config.sinks:
        if sink.type == "email":
            raise ConfigError("email输出属于可选P2，当前请使用feishu或markdown")
    for source in config.sources:
        if source.url and not (offline and ENV_REF.search(source.url)):
            from .network import public_url

            try:
                public_url(source.url)
            except ValueError as exc:
                raise ConfigError(f"来源{source.id}：{exc}") from exc
        if source.type == "imap":
            try:
                source.search.format(since="01-Nov-2026")
            except (KeyError, ValueError) as exc:
                raise ConfigError("imap.search只能使用{since}占位符") from exc
    for value, field, schemes in [(config.model.base_url, "model.base_url", {"http", "https"})] + [
        (sink.webhook.get_secret_value(), "sinks.webhook", {"https"})
        for sink in config.sinks
        if sink.type == "feishu"
    ]:
        if offline and ENV_REF.search(value):
            continue
        parts = urlsplit(value)
        if parts.scheme not in schemes or not parts.hostname or parts.username or parts.password:
            raise ConfigError(f"{field}地址格式无效，请检查协议、主机和环境变量")
    config.state.path = str((file.parent / config.state.path).resolve())
    if config.model.prompt:
        config.model.prompt = str((file.parent / config.model.prompt).resolve())
        if not Path(config.model.prompt).is_file():
            raise ConfigError("指定的模型prompt文件不存在")
    for sink in config.sinks:
        if sink.type == "markdown":
            try:
                sink.path.format(date="2026-11-01")
            except (KeyError, ValueError) as exc:
                raise ConfigError("markdown.path只能使用{date}占位符") from exc
            sink.path = str(file.parent / sink.path)
    config._secret_values = values
    config._unbound_env = sorted(unbound)
    return config


def init_config(out="pulse.yaml", legacy=None):
    target = Path(out)
    if target.exists():
        raise ConfigError(f"配置文件已存在：{target}，不会覆盖")
    template = (Path(__file__).parent / "fixtures/default.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(template)
    if legacy:
        file = Path(legacy)
        if file.is_dir():
            file /= "settings.json"
        try:
            old = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError("无法读取指定的旧settings.json") from exc
        data["timezone"] = old.get("timezone", "Asia/Hong_Kong")
        data["schedule"] = old.get("schedule_time", "08:00")
        if old.get("llm_base_url"):
            data["model"]["base_url"] = old["llm_base_url"]
        if old.get("llm_model"):
            data["model"]["name"] = old["llm_model"]
        # 迁移只保留非密钥元数据，真实凭据继续由环境变量提供。
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target

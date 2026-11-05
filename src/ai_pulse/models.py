import re
from collections import Counter
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)


class CollectedItem(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    url: str
    kind: Literal["news", "repository"] = "news"
    summary: str = ""
    published_at: str | None = None
    metrics: dict = Field(default_factory=dict)


class SourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["rss", "web", "github", "hn", "tldr"] = "rss"
    url: str
    channel: str = "用户添加"
    enabled: bool = True


class SourcePatch(BaseModel):
    enabled: bool


class RunInput(BaseModel):
    kind: Literal["collect", "digest", "model_test"]


class ToolInput(BaseModel):
    entry_id: int = Field(gt=0)


class ChosenItem(BaseModel):
    entry_id: int
    section: Literal["industry", "products"]
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1, max_length=900)
    reason: str = Field(min_length=1, max_length=300)
    evidence_ids: list[int] = Field(min_length=1, max_length=6)
    evidence_status: Literal["官方已确认", "媒体报道", "材料有限"]

    @field_validator("title", "summary", "reason")
    @classmethod
    def chinese_editorial_text(cls, value: str) -> str:
        if not re.search(r"[\u4e00-\u9fff]", value):
            raise ValueError("标题、摘要和关注理由应使用中文，专有名称可以保留英文")
        return value


class ModelDigest(BaseModel):
    items: list[ChosenItem] = Field(max_length=10)

    @model_validator(mode="after")
    def edition_rules(self):
        counts = Counter(item.section for item in self.items)
        if any(count > 5 for count in counts.values()):
            raise ValueError("行业热点和产品案例各最多5条，不允许跨板块补位")
        if len({item.entry_id for item in self.items}) != len(self.items):
            raise ValueError("简报包含重复条目")
        return self


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OutputConfig(StrictConfig):
    items: int = Field(default=5, ge=3, le=8)
    max_items: int = Field(default=8, ge=3, le=8)
    language: Literal["zh"] = "zh"
    sections: list[Literal["industry", "products"]] = Field(
        default_factory=lambda: ["industry", "products"], min_length=1
    )
    max_summary_chars: int = Field(default=140, ge=80, le=140)

    @model_validator(mode="after")
    def output_limits(self):
        if self.items > self.max_items or len(set(self.sections)) != len(self.sections):
            raise ValueError("items不能大于max_items，板块不能重复")
        return self


class ModelConfig(StrictConfig):
    base_url: str = Field(min_length=1)
    api_key: SecretStr = Field(min_length=1)
    name: str = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_input_tokens: int = Field(default=20000, ge=512)
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    prompt: str | None = None


class SourceConfig(StrictConfig):
    id: str = Field(min_length=1)
    type: Literal["rss", "hn", "tldr", "imap"]
    enabled: bool = True
    channel: str = ""
    url: str | None = None
    limit: int = Field(default=40, ge=1, le=100)
    host: str | None = None
    port: int = Field(default=993, ge=1, le=65535)
    username: str | None = None
    password: SecretStr | None = None
    mailbox: str = "INBOX"
    search: str = 'FROM "tldr" SINCE {since}'

    @model_validator(mode="after")
    def adapter_fields(self):
        common = {"id", "type", "enabled", "channel", "limit"}
        fields = {
            "rss": {"url"},
            "tldr": {"url"},
            "hn": {"url"},
            "imap": {"host", "port", "username", "password", "mailbox", "search"},
        }[self.type]
        wrong = self.model_fields_set - common - fields
        if wrong:
            raise ValueError(
                f"{self.type}不支持字段{sorted(wrong)}；可用字段：{', '.join(sorted(common | fields))}"
            )
        if not self.enabled:
            return self
        if self.type in {"rss", "tldr"} and not self.url:
            raise ValueError(f"{self.type}来源必须配置url")
        if self.type == "imap" and not all((self.host, self.username, self.password)):
            raise ValueError("imap来源必须配置host、username和password环境变量引用")
        return self


class SinkConfig(StrictConfig):
    type: Literal["feishu", "markdown", "email"]
    webhook: SecretStr | None = None
    feedback_url: str | None = None
    path: str = "./digests/{date}.md"
    host: str | None = None
    port: int = Field(default=587, ge=1, le=65535)
    username: str | None = None
    password: SecretStr | None = None
    from_address: str | None = Field(default=None, alias="from")
    to: list[str] = Field(default_factory=list)
    starttls: bool = True

    @model_validator(mode="after")
    def sink_fields(self):
        fields = {
            "feishu": {"type", "webhook", "feedback_url"},
            "markdown": {"type", "path"},
            "email": {"type", "host", "port", "username", "password", "from_address", "to", "starttls"},
        }[self.type]
        wrong = self.model_fields_set - fields
        if wrong:
            raise ValueError(f"{self.type}不支持字段{sorted(wrong)}；可用字段：{', '.join(sorted(fields))}")
        if self.type == "feishu" and not self.webhook:
            raise ValueError("飞书sink必须配置webhook环境变量引用")
        return self


class StateConfig(StrictConfig):
    path: str = "./data/pulse.db"
    dedup_days: int = Field(default=7, ge=1, le=365)


class PulseConfig(StrictConfig):
    version: Literal[1] = 1
    timezone: str = "Asia/Hong_Kong"
    schedule: str = "08:00"
    output: OutputConfig = Field(default_factory=OutputConfig)
    model: ModelConfig
    sources: list[SourceConfig] = Field(min_length=1)
    sinks: list[SinkConfig] = Field(min_length=1)
    state: StateConfig = Field(default_factory=StateConfig)
    _secret_values: list[str] = PrivateAttr(default_factory=list)
    _unbound_env: list[str] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def config_rules(self):
        from zoneinfo import ZoneInfo

        ZoneInfo(self.timezone)
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", self.schedule):
            raise ValueError("schedule格式应为HH:MM")
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("sources.id不能重复")
        if not any(s.enabled for s in self.sources):
            raise ValueError("至少启用一个信息源")
        return self


class DeliveryRecord(BaseModel):
    digest_id: int
    sink: str
    state: Literal["pending", "sent", "failed", "skipped"]
    attempts: int = 0
    last_error: str = ""
    sent_at: str | None = None


class BriefingDigest(ModelDigest):
    @model_validator(mode="after")
    def edition_rules(self, info: ValidationInfo):
        options = info.context or {}
        maximum = options.get("max_items", 5)
        if len(self.items) > maximum:
            raise ValueError(f"简报最多{maximum}条")
        if len({i.entry_id for i in self.items}) != len(self.items):
            raise ValueError("简报不能包含重复条目")
        if any(n > 3 for n in Counter(i.section for i in self.items).values()):
            raise ValueError("每个板块最多3条")
        for item in self.items:
            if (
                len(item.title) > 60
                or not 80 <= len(item.summary) <= options.get("max_summary_chars", 140)
                or len(item.reason) > 40
            ):
                raise ValueError("标题须不超过60字、摘要80至140字、理由不超过40字")
        return self

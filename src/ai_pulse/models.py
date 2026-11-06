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
    feed_content: str = ""


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
    items: int = Field(default=10, ge=3, le=10)
    max_items: int = Field(default=10, ge=3, le=10)
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

    def section_limit(self):
        return (self.items + len(self.sections) - 1) // len(self.sections)


class SelectionWeights(StrictConfig):
    relevance: float = Field(default=1, ge=0)
    impact: float = Field(default=1, ge=0)
    usefulness: float = Field(default=1, ge=0)
    novelty: float = Field(default=1, ge=0)

    @model_validator(mode="after")
    def nonzero(self):
        if not any(self.model_dump().values()):
            raise ValueError("至少一个评分权重必须大于0")
        return self


class SelectionConfig(StrictConfig):
    profile: str = Field(
        default="关注AI前沿变化、开发工具和真实产品案例，寻找应用灵感。排除纯融资、人事、广告和同质化套壳。",
        min_length=1,
    )
    weights: SelectionWeights = Field(default_factory=SelectionWeights)
    # 这是部署预算，不是选稿质量的最优参数。
    max_model_calls: int = Field(default=60, ge=2, le=500)
    max_runtime_seconds: int = Field(default=600, ge=30, le=3600)


class ModelConfig(StrictConfig):
    base_url: str = Field(min_length=1)
    api_key: SecretStr = Field(min_length=1)
    name: str = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_input_tokens: int = Field(default=20000, ge=512)
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    max_output_tokens: int | None = Field(default=None, ge=256, le=32768)
    trust_env: bool = True
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
    signing_secret: SecretStr | None = Field(default=None, min_length=1)
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
            "feishu": {"type", "webhook", "feedback_url", "signing_secret"},
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
    selection: SelectionConfig = Field(default_factory=SelectionConfig)
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


class RelevanceScores(StrictConfig):
    relevance: int = Field(ge=0, le=3)
    impact: int = Field(ge=0, le=3)
    usefulness: int = Field(ge=0, le=3)
    novelty: int = Field(ge=0, le=3)


class ScoredArticle(StrictConfig):
    entry_id: int
    section: Literal["industry", "products"]
    scores: RelevanceScores
    reason: str
    topic: str
    event_key: str
    exclude: bool
    needs_read: bool


class ScoringResult(StrictConfig):
    items: list[ScoredArticle]

    @model_validator(mode="after")
    def check_ids(self, info: ValidationInfo):
        expected = (info.context or {}).get("ids")
        ids = [item.entry_id for item in self.items]
        if expected is not None and (set(ids) != set(expected) or len(ids) != len(expected)):
            raise ValueError("评分必须覆盖每个候选且不能重复或引用未知ID")
        return self


class ReadingNotes(StrictConfig):
    facts: list[str]
    limitations: list[str]
    quotes: list[str]

    @model_validator(mode="after")
    def check_quotes(self, info: ValidationInfo):
        source = " ".join((info.context or {}).get("text", "").split())
        if any(not quote.strip() or " ".join(quote.split()) not in source for quote in self.quotes):
            raise ValueError("依据片段必须逐字来自本次提供的正文")
        return self


class ArticleReview(StrictConfig):
    selected: bool
    section: Literal["industry", "products"]
    scores: RelevanceScores
    topic: str
    event_key: str
    title: str
    summary: str
    reason: str
    quotes: list[str]

    @model_validator(mode="after")
    def check_review(self, info: ValidationInfo):
        context = info.context or {}
        source = " ".join(context.get("text", "").split())
        if any(not quote.strip() or " ".join(quote.split()) not in source for quote in self.quotes):
            raise ValueError("依据片段必须逐字来自已提供的正文")
        if self.selected:
            if not self.quotes or not self.event_key.strip() or not self.topic.strip():
                raise ValueError("入选内容必须有原文依据、主题和事件标识")
            if self.scores.relevance < 2:
                raise ValueError("入选内容至少应与用户兴趣明确相关")
            if not (
                len(self.title) <= 60
                and 80 <= len(self.summary) <= context.get("max_summary_chars", 140)
                and len(self.reason) <= 40
            ):
                raise ValueError(
                    f"按字符计数（英文、空格和标点也计入）：title当前{len(self.title)}须<=60，summary当前{len(self.summary)}须80至{context.get('max_summary_chars', 140)}，reason当前{len(self.reason)}须<=40。摘要尽量控制在90至110字符"
                )
            if not all(
                re.search(r"[\u4e00-\u9fff]", value) for value in (self.title, self.summary, self.reason)
            ):
                raise ValueError("标题、摘要和关注理由必须为中文")
            original_numbers = set(re.findall(r"\d+(?:\.\d+)?", source.replace(",", "")))
            output_numbers = set(
                re.findall(r"\d+(?:\.\d+)?", (self.title + " " + self.summary).replace(",", ""))
            )
            if not output_numbers <= original_numbers:
                raise ValueError("标题或摘要出现正文没有的数字；保留原文数字形式，不换算单位")
        return self


class FinalSelection(StrictConfig):
    entry_ids: list[int]
    reason: str

    @model_validator(mode="after")
    def check_selection(self, info: ValidationInfo):
        context = info.context or {}
        allowed = context.get("allowed", {})
        if len(set(self.entry_ids)) != len(self.entry_ids) or any(i not in allowed for i in self.entry_ids):
            raise ValueError("最终选稿不能重复或引用未完成全文阅读的ID")
        if len(self.entry_ids) > context.get("max_items", 10):
            raise ValueError("最终选稿超过目标条数")
        if any(
            n > context.get("section_limit", 5)
            for n in Counter(allowed[i]["section"] for i in self.entry_ids).values()
        ):
            raise ValueError("最终选稿超过板块配额")
        return self


class BriefingDigest(ModelDigest):
    @model_validator(mode="after")
    def edition_rules(self, info: ValidationInfo):
        options = info.context or {}
        maximum = options.get("max_items", 10)
        if len(self.items) > maximum:
            raise ValueError(f"简报最多{maximum}条")
        if len({i.entry_id for i in self.items}) != len(self.items):
            raise ValueError("简报不能包含重复条目")
        if any(
            n > options.get("section_limit", (maximum + 1) // 2)
            for n in Counter(i.section for i in self.items).values()
        ):
            raise ValueError("板块条数超过配置配额")
        for item in self.items:
            if (
                len(item.title) > 60
                or not 80 <= len(item.summary) <= options.get("max_summary_chars", 140)
                or len(item.reason) > 40
            ):
                raise ValueError("标题须不超过60字、摘要80至140字、理由不超过40字")
        return self

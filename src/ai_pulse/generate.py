"""默认一次模型调用；仅结构校验失败时允许纠正一次。"""

import json
import logging
from pathlib import Path

from openai import AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from .config import redact
from .errors import GenerationError
from .models import BriefingDigest

log = logging.getLogger(__name__)


async def generate_single_pass(config, candidates, existing, *, store=None, run_id=None):
    if store is None:
        from .storage import Store

        path = Path(config.state.path)
        store = Store(path.parent, database_path=path)
    template = (
        Path(config.model.prompt) if config.model.prompt else Path(__file__).parent / "prompts/digest.md"
    )
    system = (
        template.read_text(encoding="utf-8")
        .replace("{{items}}", str(config.output.items))
        .replace("{{summary_chars}}", str(config.output.max_summary_chars))
        .replace("{{sections}}", ", ".join(config.output.sections))
    )
    shortlist = [
        {
            "id": item["id"],
            "title": item["title"],
            "source": [s["name"] + "（" + s["channel"] + "）" for s in item["sources"]],
            "summary": item["summary"][:300],
            "url": item["url"],
            "published_at": item["published_at"],
        }
        for item in candidates[:20]
    ]
    correction = ""
    async with AsyncOpenAI(
        api_key=config.model.api_key.get_secret_value(),
        base_url=config.model.base_url,
        max_retries=0,
        timeout=config.model.timeout_seconds,
    ) as client:
        for attempt in range(2):
            messages = [{"role": "system", "content": system}, {"role": "user", "content": ""}]
            if correction:
                messages.append({"role": "user", "content": correction})
            while shortlist:
                messages[1]["content"] = json.dumps(
                    {"candidates": shortlist, "existing": existing}, ensure_ascii=False
                )
                # UTF-8字节数作为保守文本Token上界，再留出协议开销，不另发计数请求。
                bound = sum(len(m["content"].encode("utf-8")) for m in messages) + 256
                if bound < config.model.max_input_tokens:
                    break
                shortlist.pop()
            if not shortlist:
                raise GenerationError("提示词与历史超出输入预算，截断后无候选；请缩短模板或提高预算")
            try:
                response = await client.chat.completions.create(
                    model=config.model.name,
                    messages=messages,
                    temperature=config.model.temperature,
                    response_format={"type": "json_object"},
                )
            except OpenAIError as exc:
                raise GenerationError(
                    redact(f"模型不可用：{type(exc).__name__}: {exc}", config._secret_values)[:1000]
                ) from exc
            usage = response.usage
            store.record_usage(
                run_id,
                config.model.name,
                usage.prompt_tokens if usage else None,
                usage.completion_tokens if usage else None,
                None,
            )
            if (
                usage
                and usage.prompt_tokens is not None
                and usage.prompt_tokens >= config.model.max_input_tokens
            ):
                if attempt == 1 or len(shortlist) == 1:
                    raise GenerationError("服务报告的输入Token仍超过预算，未投递；请检查网关计数或缩短模板")
                keep = max(1, int(len(shortlist) * config.model.max_input_tokens / usage.prompt_tokens * 0.8))
                shortlist = shortlist[:keep]
                log.warning("模型服务报告输入超预算，缩减候选后最多再请求一次")
                continue
            allowed = {item["id"]: item for item in candidates if item["id"] in {s["id"] for s in shortlist}}
            try:
                result = BriefingDigest.model_validate_json(
                    response.choices[0].message.content or "",
                    context={
                        "max_items": min(config.output.items, config.output.max_items),
                        "max_summary_chars": config.output.max_summary_chars,
                    },
                )
                for item in result.items:
                    if item.entry_id not in allowed or any(
                        identity not in allowed for identity in item.evidence_ids
                    ):
                        raise ValueError("模型引用了未发送的候选ID")
                    if item.section not in config.output.sections:
                        raise ValueError("模型输出了未配置的板块")
                    if item.evidence_status == "官方已确认" and not any(
                        "官方" in source["channel"]
                        for identity in item.evidence_ids
                        for source in allowed[identity]["sources"]
                    ):
                        raise ValueError("官方确认标签缺少对应官方来源")
                return result
            except (ValidationError, ValueError) as exc:
                detail = (
                    "; ".join(e["msg"] for e in exc.errors(include_input=False))
                    if isinstance(exc, ValidationError)
                    else str(exc)
                )
                if attempt == 1:
                    raise GenerationError(
                        "模型结果校验失败：" + redact(detail, config._secret_values)[:500]
                    ) from exc
                correction = (
                    "上一次结果校验失败："
                    + detail[:1000]
                    + "。请严格按所需JSON结构重新生成，勿输出额外内容。"
                )
                log.warning("模型结果校验失败，执行一次纠正：%s", redact(detail, config._secret_values)[:300])
    raise GenerationError("模型没有返回可用简报")

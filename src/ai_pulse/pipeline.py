"""单次运行：采集、生成、投递与退出状态。"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from openai import OpenAIError
from pydantic import ValidationError

from .agent import CloudAgent
from .config import Settings, redact
from .digest import preview_digest, rank_candidates
from .editorial import FORMAT_VERSION, SECTIONS
from .errors import SourceFailure
from .network import Network, canonical_url
from .sinks import create_sink
from .sinks.markdown import render_markdown
from .sources import create_source
from .storage import Store, utcnow

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    exit_code: int
    state: str
    run_id: int | None = None
    digest_id: int | None = None
    coverage: dict = field(default_factory=dict)
    deliveries: dict = field(default_factory=dict)
    markdown: str | None = None
    message: str = ""


def source_failure(exc):
    if isinstance(exc, SourceFailure):
        return exc
    if isinstance(exc, httpx.PoolTimeout):
        return SourceFailure("network", "客户端故障：连接池PoolTimeout", client_failure=True)
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return SourceFailure("auth" if code in {401, 403} else "http", f"HTTP {code}")
    if isinstance(exc, (httpx.RequestError, OSError, TimeoutError)):
        return SourceFailure("network", type(exc).__name__)
    return SourceFailure("parse", str(exc))


async def collect_sources(config, store, run_id, network, since):
    semaphore = asyncio.Semaphore(3)

    async def fetch(source):
        meta = {
            "name": source.id,
            "kind": source.type,
            "url": source.url or f"{source.type}://{source.host or source.id}",
            "channel": source.channel or source.id,
            "enabled": True,
            "state": "unverified",
            "message": "",
        }
        store.seed_sources([meta])
        saved = next(s for s in store.sources() if s["name"] == source.id and s["url"] == meta["url"])
        async with semaphore:
            store.progress(run_id, f"采集：{source.id}")
            store.update_source(saved["id"], last_attempt=utcnow())
            try:
                items = await create_source(source, network).fetch(since)
                for item in items:
                    payload = item.model_dump()
                    payload["url"] = canonical_url(payload["url"])
                    store.save_item(payload, saved["id"])
                store.update_source(
                    saved["id"], state="ok", message=f"获取{len(items)}条", last_success=utcnow()
                )
                return {"source": source.id, "ok": True, "count": len(items)}
            except (SourceFailure, httpx.HTTPError, OSError, ValueError, KeyError, TimeoutError) as exc:
                failure = source_failure(exc)
                message = redact(failure, config._secret_values)
                store.update_source(saved["id"], state="error", message=f"{failure.kind}: {message}")
                log.error("来源%s失败：%s，%s", source.id, failure.kind, message)
                return {
                    "source": source.id,
                    "ok": False,
                    "error": message,
                    "kind": failure.kind,
                    "client_failure": failure.client_failure,
                }

    results = await asyncio.gather(*(fetch(source) for source in config.sources if source.enabled))
    return {
        "results": results,
        "successful": sum(r["ok"] for r in results),
        "total": len(results),
        "count": sum(r.get("count", 0) for r in results),
        "last_success": max((s["last_success"] for s in store.sources() if s["last_success"]), default=None),
    }


async def generate_items(config, candidates, store, run_id, network, *, agent_mode=False):
    if not agent_mode:
        from .generate import generate_single_pass

        result = await generate_single_pass(
            config, candidates, store.delivered_history(config.state.dedup_days), store=store, run_id=run_id
        )
        allowed = {item["id"]: item for item in candidates}
        items = []
        for chosen in result.items:
            original = allowed[chosen.entry_id]
            evidence = [allowed[identity] for identity in chosen.evidence_ids]
            items.append(
                {
                    **{key: original[key] for key in ("url", "kind", "published_at", "metrics", "growth")},
                    "entry_id": chosen.entry_id,
                    "section": chosen.section,
                    "title": chosen.title,
                    "summary": chosen.summary,
                    "reason": chosen.reason,
                    "evidence_status": chosen.evidence_status,
                    "sources": list({s["id"]: s for item in evidence for s in item["sources"]}.values()),
                    "evidence": [{"title": item["title"], "url": item["url"]} for item in evidence],
                }
            )
        return items
    settings = Settings(
        llm_base_url=config.model.base_url,
        llm_api_key=config.model.api_key.get_secret_value(),
        llm_model=config.model.name,
        llm_trust_env=False,
        scheduler_enabled=False,
    )
    generated = await CloudAgent(settings, store, network, run_id).generate(candidates)
    selected, counts = [], {"industry": 0, "products": 0}
    for item in generated:
        if (
            item["section"] in config.output.sections
            and counts[item["section"]] < 3
            and len(selected) < config.output.items
        ):
            selected.append(item)
            counts[item["section"]] += 1
    return selected


async def deliver(config, store, digest, run_id):
    digest["timezone"] = config.timezone
    results = {}
    for index, sink_config in enumerate(config.sinks):
        identity = f"{sink_config.type}:{index}"
        if not store.record_delivery(digest["id"], identity, "pending"):
            results[identity] = {"state": "sent", "skipped": True}
            continue
        try:
            await create_sink(sink_config).send(digest, digest["items"])
        except Exception as exc:
            # sink边界必须将失败记录下来，其他sink仍可独立投递。
            error = redact(f"{type(exc).__name__}: {exc}", config._secret_values)
            store.record_delivery(digest["id"], identity, "failed", error)
            log.error("投递%s失败：%s", identity, error)
            results[identity] = {"state": "failed", "error": error}
        else:
            store.record_delivery(digest["id"], identity, "sent")
            results[identity] = {"state": "sent"}
    sent = sum(result["state"] == "sent" for result in results.values())
    coverage = digest["coverage"]
    # 以规格的测试计划为准：有内容但全投递失败是degraded/1，绝不假报成功。
    if not sent:
        state, code = "degraded", 1
    elif coverage["successful"] < coverage["total"] or sent < len(results):
        state, code = "partial", 1
    elif len(digest["items"]) < config.output.items:
        state, code = "degraded", 1
    else:
        state, code = "success", 0
    if sent:
        store.mark_delivered([item["entry_id"] for item in digest["items"]], digest["id"])
    store.update_delivery_summary(digest["id"], status="ok" if state == "success" else "degraded")
    detail = dict(coverage, digest_id=digest["id"], deliveries=results, selected=len(digest["items"]))
    store.finish_run(run_id, state, detail)
    return RunResult(code, state, run_id, digest["id"], coverage, results)


async def run_once(config, *, date=None, agent_mode=False):
    path = Path(config.state.path)
    store = Store(path.parent, database_path=path)
    target = date or datetime.now(ZoneInfo(config.timezone)).date().isoformat()
    run_id = store.start_run("digest", "cli")
    try:
        pending = store.retryable_digest(target)
        if pending:
            log.info("重试已保存简报的失败投递，不重复调用模型")
            return await deliver(config, store, pending, run_id)
        end = (
            datetime.now(UTC)
            if date is None
            else datetime.fromisoformat(target)
            .replace(hour=23, minute=59, second=59, tzinfo=ZoneInfo(config.timezone))
            .astimezone(UTC)
        )
        since = end - timedelta(hours=24)
        coverage = {}
        for attempt in range(2):
            async with Network() as network:
                coverage = await collect_sources(config, store, run_id, network, since)
                pool_errors = sum(r.get("client_failure", False) for r in coverage["results"])
                if attempt == 0 and pool_errors and (pool_errors >= 2 or pool_errors == coverage["total"]):
                    log.error("客户端故障：重建Network后整体重试一次")
                    continue
                # 全源失败不能退回旧缓存并伪装为今天的新简报。
                if coverage["successful"] == 0:
                    store.finish_run(run_id, "failed", dict(coverage, error="所有来源采集失败"))
                    return RunResult(4, "failed", run_id, coverage=coverage, message="所有来源采集失败")
                entries = [
                    e
                    for e in store.entries(since.isoformat())
                    if datetime.fromisoformat((e["published_at"] or e["first_seen"]).replace("Z", "+00:00"))
                    <= end
                    and any(
                        s["name"] in {r["source"] for r in coverage["results"] if r["ok"]}
                        for s in e["sources"]
                    )
                ]
                candidates = rank_candidates(
                    entries, store, store.recently_delivered(config.state.dedup_days)
                )[:20]
                if not candidates:
                    store.finish_run(run_id, "failed", dict(coverage, error="去重后没有有效候选"))
                    return RunResult(4, "failed", run_id, coverage=coverage, message="去重后没有有效候选")
                store.progress(run_id, "生成简报")
                generation_started = time.monotonic()
                items = await generate_items(
                    config, candidates, store, run_id, network, agent_mode=agent_mode
                )
                if not items:
                    store.finish_run(run_id, "failed", dict(coverage, error="模型没有选出有效内容"))
                    return RunResult(4, "failed", run_id, coverage=coverage, message="模型没有选出有效内容")
                coverage.update(format_version=FORMAT_VERSION, candidate_count=len(candidates))
                coverage["selected_sources"] = {
                    source: sum(any(s["name"] == source for s in item["sources"]) for item in items)
                    for source in sorted({s["name"] for item in items for s in item["sources"]})
                }
                usages = [usage for usage in store.usage() if usage["run_id"] == run_id]
                coverage["generation"] = {
                    "calls": len(usages),
                    "seconds": round(time.monotonic() - generation_started, 3),
                    "input_tokens": sum(u["input_tokens"] for u in usages)
                    if usages and all(u["input_tokens"] is not None for u in usages)
                    else None,
                    "output_tokens": sum(u["output_tokens"] for u in usages)
                    if usages and all(u["output_tokens"] is not None for u in usages)
                    else None,
                }
                digest_id = store.save_digest(target, "cloud", end.isoformat(), items, coverage)
            return await deliver(config, store, store.digest(digest_id), run_id)
        raise RuntimeError("客户端重建后仍无法完成采集")
    except asyncio.CancelledError:
        store.finish_run(run_id, "aborted", {"error": "运行被中断"})
        raise
    except Exception as exc:
        store.finish_run(
            run_id, "failed", {"error": redact(f"{type(exc).__name__}: {exc}", config._secret_values)}
        )
        raise


def offline_preview(config):
    from .offline import fixture_digest

    # 离线命令只读取本地样本，不构造网络客户端、不投递、不写入去重表。
    digest = fixture_digest()
    digest["offline"] = True
    digest["coverage"] = {
        "successful": 0,
        "total": sum(s.enabled for s in config.sources),
        "results": [],
        "last_success": None,
    }
    digest["items"] = digest["items"][: config.output.items]
    return RunResult(
        0,
        "preview",
        coverage=dict(digest["coverage"], checked=False),
        markdown=render_markdown(digest, digest["items"]),
        message="离线样本预览；未绑定环境变量：" + ", ".join(config._unbound_env),
    )


# 以下为原看板单次执行的兼容实现，保留已有交互契约。


logger = log


async def collect_legacy(self, run_id: int) -> dict:
    sources = [
        source for source in self.store.sources() if source["enabled"] and source["state"] != "removed"
    ]
    semaphore = asyncio.Semaphore(3)

    async def collect_source(source):
        async with semaphore:
            self.store.progress(run_id, f"采集 · {source['name']}")
            self.store.update_source(source["id"], last_attempt=utcnow())
            try:
                items = await self.collectors.collect(source)
                for item in items:
                    self.store.save_item(item.model_dump(), source["id"])
                self.store.update_source(
                    source["id"],
                    state="ok",
                    last_success=utcnow(),
                    message=f"本次获取 {len(items)} 条 AI 候选",
                )
                return {"source": source["name"], "ok": True, "count": len(items)}
            except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
                hint = str(exc)[:200] if isinstance(exc, ValueError) else type(exc).__name__
                self.store.update_source(source["id"], state="error", message=hint)
                return {"source": source["name"], "ok": False, "error": hint}

    results = await asyncio.gather(*(collect_source(source) for source in sources))
    # 保留从HN与TLDR链接发现仓库的旧路径。
    hn = next((source for source in sources if source["kind"] == "hn"), None)
    discovery = {"count": 0, "notes": []}
    if hn:
        try:
            linked, notes = await self.collectors.linked_repositories(self.store.entries())
            for item in linked:
                self.store.save_item(item.model_dump(), hn["id"])
            discovery = {"count": len(linked), "notes": notes}
        except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
            discovery["notes"].append(type(exc).__name__)
    return {
        "results": results,
        "repository_discovery": discovery,
        "successful": sum(row["ok"] for row in results),
        "total": len(results),
        "count": sum(row.get("count", 0) for row in results),
    }


async def execute_legacy(self, run_id: int, kind: str, trigger: str):
    try:
        if kind == "model_test":
            self.store.progress(run_id, "用户触发的模型兼容性测试")
            detail = await CloudAgent(self.settings, self.store, self.network, run_id).test_connection()
        else:
            detail = await self.collect(run_id)
            if detail["successful"] == 0:
                raise ValueError("所有来源采集失败")
            if kind == "digest":
                if not detail["successful"]:
                    raise ValueError("所有启用来源采集失败，已保留上一份简报")
                self.store.progress(run_id, "去重与筛选候选")
                now = datetime.now(UTC)
                latest = self.store.digest()
                cutoff = now - timedelta(hours=72)
                if latest:
                    cutoff = max(cutoff, datetime.fromisoformat(latest["cutoff"]))
                local_date = now.astimezone(ZoneInfo(self.settings.timezone)).date().isoformat()
                # 同日修订保留该日期原有的候选回看窗口。
                if latest and latest["local_date"] == local_date:
                    cutoff = now - timedelta(hours=72)
                candidates = rank_candidates(
                    self.store.entries(cutoff.isoformat()),
                    self.store,
                    self.store.covered_entries(exclude_date=local_date),
                )
                if self.settings.model_mode == "cloud" and candidates:
                    self.store.progress(run_id, "模型筛选与查证")
                    items = await CloudAgent(self.settings, self.store, self.network, run_id).generate(
                        candidates
                    )
                else:
                    items = preview_digest(candidates)
                detail["offline_gap"] = bool(
                    latest and datetime.fromisoformat(latest["cutoff"]) < now - timedelta(hours=72)
                )
                detail["pending_sources"] = [
                    source["name"] for source in self.store.sources() if source["state"] == "pending"
                ]
                detail["candidate_count"] = len(candidates)
                detail["selected"] = len(items)
                detail["format_version"] = FORMAT_VERSION
                detail["section_counts"] = {
                    section: sum(item["section"] == section for item in items) for section in SECTIONS
                }
                detail["digest_id"] = self.store.save_digest(
                    local_date,
                    self.settings.model_mode,
                    now.isoformat(),
                    items,
                    detail,
                )
        self.store.finish_run(
            run_id, "partial" if detail.get("successful", 1) < detail.get("total", 1) else "success", detail
        )
    except asyncio.CancelledError:
        self.store.finish_run(run_id, "aborted", {"error": "程序关闭，任务中断"})
        raise
    except (httpx.HTTPError, OpenAIError, ValueError, ValidationError, KeyError, OSError) as exc:
        hint = str(exc)[:300] if isinstance(exc, ValueError) else type(exc).__name__
        self.store.finish_run(run_id, "failed", {"error": hint})
        if trigger != "manual":
            self.last_failed_auto_date = datetime.now(ZoneInfo(self.settings.timezone)).date().isoformat()
    except Exception:
        logger.exception("Unexpected task failure in run %s", run_id)
        self.store.finish_run(run_id, "failed", {"error": "内部错误，请检查终端堆栈"})

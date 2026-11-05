import hmac
import mimetypes
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError, field_validator

from .config import Settings, load_settings, save_settings
from .digest import demo_digest
from .editorial import SECTIONS
from .models import RunInput, SourceInput, SourcePatch
from .network import Network, public_url
from .service import Service
from .sources import default_sources
from .storage import Store, utcnow

PACKAGE = Path(__file__).parent
mimetypes.add_type("application/javascript", ".js")


class SettingsInput(BaseModel):
    model_mode: Literal["preview", "cloud"]
    llm_base_url: str
    llm_model: str = Field(max_length=150)
    llm_api_key: str = Field(default="", repr=False)
    github_token: str = Field(default="", repr=False)
    schedule_time: str
    llm_input_price: float | None = Field(default=None, ge=0)
    llm_output_price: float | None = Field(default=None, ge=0)

    @field_validator("llm_base_url")
    @classmethod
    def validate_base_url(cls, value):
        return public_url(value.rstrip("/"))


class ArticleInput(BaseModel):
    title: str = Field(min_length=2, max_length=500)
    url: str
    source_id: int = Field(gt=0)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or load_settings()
    store = Store(config.data_dir, database_path=config.database_path)
    store.seed_sources(default_sources())
    csrf_token = secrets.token_urlsafe(32)
    templates = Jinja2Templates(directory=PACKAGE / "templates")

    def local_time(value, short=False):
        if not value:
            return "时间未知"
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.astimezone(ZoneInfo("Asia/Hong_Kong")).strftime(
            "%m-%d %H:%M" if short else "%Y-%m-%d %H:%M"
        )

    templates.env.filters["local_time"] = local_time

    @asynccontextmanager
    async def lifespan(app):
        store.recover_runs()
        service = Service(store, Network(), config)
        app.state.service = service
        service.start()
        yield
        await service.close()

    app = FastAPI(title="AI Pulse", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")

    @app.middleware("http")
    async def guard_changes(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            token = request.headers.get("X-CSRF-Token", "")
            origin = request.headers.get("Origin")
            if not hmac.compare_digest(token, csrf_token) or (
                origin and urlsplit(origin).netloc != request.headers.get("host")
            ):
                return JSONResponse({"detail": "请求校验失败，请刷新本机页面重试"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {"detail": [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]},
            status_code=422,
        )

    def render(request, page, **context):
        sources = store.sources()
        active = next((run for run in store.runs() if run["state"] == "running"), None)
        return templates.TemplateResponse(
            request=request,
            name=page + ".html",
            context={
                "page": page,
                "csrf_token": csrf_token,
                "sources": sources,
                "source_ok": sum(s["state"] == "ok" and s["enabled"] for s in sources),
                "source_total": sum(s["enabled"] for s in sources),
                "active_run": active,
                "settings": app.state.service.settings.public(),
                "section_labels": SECTIONS,
                **context,
            },
        )

    @app.get("/")
    async def index(request: Request):
        digest = store.digest()
        return render(request, "index", digest=digest or demo_digest(), demo=digest is None)

    @app.get("/demo")
    async def demo(request: Request):
        return render(request, "index", digest=demo_digest(), demo=True)

    @app.get("/history")
    async def history(request: Request, q: str = "", section: Literal["", "industry", "products"] = ""):
        digests = store.digests()
        if q or section:
            digests = [
                d
                for d in digests
                if any(
                    q.casefold() in item["title"].casefold() and (not section or item["section"] == section)
                    for item in d["items"]
                )
            ]
        return render(request, "history", digests=digests, query=q, section=section)

    @app.get("/digests/{digest_id}")
    async def digest_page(request: Request, digest_id: int):
        digest = store.digest(digest_id)
        if not digest:
            raise HTTPException(404, "简报不存在")
        return render(request, "index", digest=digest, demo=False)

    @app.get("/sources")
    async def source_page(request: Request):
        return render(request, "sources")

    @app.get("/settings")
    async def settings_page(request: Request):
        return render(request, "settings")

    @app.get("/runs")
    async def runs_page(request: Request):
        return render(request, "runs", runs=store.runs(), usage=store.usage())

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0", "mode": app.state.service.settings.model_mode}

    @app.get("/api/status")
    async def status():
        return {
            "sources": store.sources(),
            "runs": store.runs(),
            "entries": len(store.entries()),
            "digest_id": (store.digest() or {}).get("id"),
        }

    @app.get("/api/settings")
    async def get_settings():
        return app.state.service.settings.public()

    @app.patch("/api/settings")
    async def patch_settings(body: SettingsInput):
        if body.llm_api_key or body.github_token:
            raise HTTPException(422, "v0.3密钥只能通过环境变量提供，旧Web不再接收密钥")
        service = app.state.service
        values = service.settings.model_dump() | body.model_dump()
        for field in ("llm_api_key", "github_token"):
            if not getattr(body, field):
                values[field] = getattr(service.settings, field)
        try:
            updated = Settings(**values)
        except ValidationError as exc:
            raise HTTPException(422, "配置无效，请检查时间、地址或价格") from exc
        if updated.model_mode == "cloud" and (not updated.llm_api_key or not updated.llm_model):
            raise HTTPException(422, "云端模式需要模型名称与 API Key；可以先保留规则预览")
        save_settings(updated)
        service.settings = updated
        service.reschedule()
        service.last_failed_auto_date = None
        return updated.public()

    @app.post("/api/runs", status_code=202)
    async def start_run(body: RunInput):
        if body.kind == "model_test" and (
            not app.state.service.settings.llm_api_key or not app.state.service.settings.llm_model
        ):
            raise HTTPException(409, "API 尚未配置，本次没有发起模型请求")
        run_id, created = app.state.service.submit(body.kind)
        return {"run_id": run_id, "created": created}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: int):
        run = next((row for row in store.runs() if row["id"] == run_id), None)
        if not run:
            raise HTTPException(404, "任务不存在")
        return run

    @app.post("/api/sources", status_code=201)
    async def add_source(body: SourceInput):
        try:
            public_url(body.url)
            if body.kind in {"hn", "tldr", "github"}:
                raise ValueError("内置渠道请在清单中启停；新增来源支持 RSS 或待适配网页")
            values = body.model_dump()
            if body.kind == "web":
                values["enabled"] = False
            source_id = store.add_source(values)
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(422, "来源地址无效、类型不支持或来源已存在") from exc
        return store.source(source_id)

    @app.patch("/api/sources/{source_id}")
    async def patch_source(source_id: int, body: SourcePatch):
        source = store.source(source_id)
        if not source:
            raise HTTPException(404, "来源不存在")
        if source["kind"] == "web" and body.enabled:
            raise HTTPException(409, "该来源尚未适配自动采集，可先手动添加文章")
        store.update_source(source_id, enabled=body.enabled)
        return store.source(source_id)

    @app.delete("/api/sources/{source_id}")
    async def remove_source(source_id: int):
        if not store.source(source_id):
            raise HTTPException(404, "来源不存在")
        store.remove_source(source_id)
        return {"removed": True}

    @app.post("/api/sources/{source_id}/test")
    async def test_source(source_id: int):
        source = store.source(source_id)
        if not source:
            raise HTTPException(404, "来源不存在")
        if app.state.service.active_task and not app.state.service.active_task.done():
            raise HTTPException(409, "采集任务正在运行，请完成后再测试单个来源")
        store.update_source(source_id, last_attempt=utcnow())
        try:
            items = await app.state.service.collectors.collect(source)
            store.update_source(
                source_id, state="ok", last_success=utcnow(), message=f"测试获取 {len(items)} 条"
            )
            return {"ok": True, "count": len(items)}
        except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
            hint = str(exc)[:200] if isinstance(exc, ValueError) else type(exc).__name__
            store.update_source(source_id, state="error", message=hint)
            return JSONResponse({"ok": False, "detail": hint}, status_code=422)

    @app.post("/api/articles", status_code=201)
    async def add_article(body: ArticleInput):
        if not store.source(body.source_id):
            raise HTTPException(404, "来源不存在")
        try:
            url = public_url(body.url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        from .models import CollectedItem
        from .network import canonical_url

        item = CollectedItem(
            title=body.title, url=canonical_url(url), summary="用户添加的公开文章链接，正文和发布时间待核验。"
        )
        entry_id = store.save_item(item.model_dump(), body.source_id)
        return {"entry_id": entry_id}

    return app


app = create_app()

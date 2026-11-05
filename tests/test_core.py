import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ai_pulse.app import create_app
from ai_pulse.collectors import parse_feed, parse_media, parse_tldr
from ai_pulse.config import Settings
from ai_pulse.digest import needs_daily_digest, preview_digest, rank_candidates
from ai_pulse.models import CollectedItem
from ai_pulse.network import Network, canonical_url, public_url
from ai_pulse.service import Service
from ai_pulse.storage import Store


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path)
    db.seed_sources(
        [
            {
                "name": "Test",
                "kind": "rss",
                "url": "https://example.com/feed",
                "channel": "官方 RSS",
                "enabled": True,
                "state": "unverified",
                "message": "",
            }
        ]
    )
    return db


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path, scheduler_enabled=False, model_mode="preview")
    with TestClient(create_app(settings)) as connection:
        yield connection


def headers(client):
    token = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/").text)[1]
    return {"X-CSRF-Token": token}


def item(kind="news", **changes):
    values = {
        "title": "An AI tool",
        "url": "https://example.com/article",
        "kind": kind,
        "summary": "AI tool description",
        "published_at": datetime.now(UTC).isoformat(),
        "metrics": {"stars": 100} if kind == "repository" else {"hn_score": 42},
    }
    return values | changes


def test_url_normalization_preserves_identity_parameters():
    assert canonical_url("https://Example.com/p?id=12&utm_source=x#top") == "https://example.com/p?id=12"
    assert canonical_url("https://example.com/p?id=13") != canonical_url("https://example.com/p?id=12")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://192.168.1.1/",
        "http://localhost/",
        "file:///C:/secret",
        "https://user:pass@example.com/",
    ],
)
def test_reject_private_source_urls(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_feed_relevance_date_and_broken_markup():
    xml = """<rss version="2.0"><channel><title>Test</title>
      <item><title>New AI model</title><link>https://example.com/model?utm_source=x</link>
      <pubDate>Tue, 27 Oct 2026 10:00:00 GMT</pubDate><description>LLM release</description></item>
      <item><title>Football results</title><link>https://example.com/sports</link></item>
      </channel></rss>"""
    result = parse_feed(xml)
    assert len(result) == 1
    assert result[0].url == "https://example.com/model"
    assert result[0].published_at == "2026-10-27T10:00:00+00:00"
    with pytest.raises(ValueError):
        parse_feed("not a feed")


def test_tldr_sponsors_are_not_news_and_issue_time_is_explicit():
    html = """<article><a href="https://example.com/ad"><h3>AI Tool (Sponsor)</h3></a></article>
              <article><a href="https://example.com/model"><h3>New LLM</h3></a><p>AI release</p></article>"""
    result = parse_tldr(html, "https://tldr.tech/ai/2026-10-27")
    assert len(result) == 1
    assert result[0].metrics["time_precision"] == "date"
    with pytest.raises(ValueError):
        parse_tldr("<html>layout changed</html>", "https://tldr.tech/ai/2026-10-27")


def test_media_ignores_javascript_links_and_unknown_publication_time():
    result = parse_media(
        """<a href="javascript:void(0)">AI</a>
       <a href="/articles/new-model">一个新的 AI 大模型项目发布了</a>""",
        "https://www.jiqizhixin.com/",
    )
    assert len(result) == 1
    assert result[0].published_at is None


def test_repository_enrichment_keeps_multi_source_signals(store):
    entry_id = store.save_item(item(), 1)
    store.save_item(item("repository", title="org/project"), 1)
    store.save_item(item(), 1)
    saved = store.entry(entry_id)
    assert saved["kind"] == "repository"
    assert saved["title"] == "org/project"
    assert saved["metrics"]["hn_score"] == 42
    assert saved["metrics"]["stars"] == 100
    assert len(store.entries()) == 1


def test_title_only_source_does_not_erase_an_existing_summary(store):
    entry_id = store.save_item(item(summary="已有来源提供的完整 AI 产品摘要"), 1)
    store.save_item(item(summary="", metrics={"hn_score": 90}), 1)
    saved = store.entry(entry_id)
    assert saved["summary"] == "已有来源提供的完整 AI 产品摘要"
    assert saved["metrics"]["hn_score"] == 90


def test_growth_cold_start_and_negative_delta(store):
    entry_id = store.save_item(item("repository"), 1)
    assert store.growth(entry_id) is None
    now = datetime.now(UTC)
    with store.connection() as db:
        db.execute("DELETE FROM repo_snapshots")
        db.executemany(
            "INSERT INTO repo_snapshots(entry_id,captured_at,stars) VALUES(?,?,?)",
            [(entry_id, (now - timedelta(hours=26)).isoformat(), 105), (entry_id, now.isoformat(), 100)],
        )
    assert store.growth(entry_id)["delta"] == -5
    assert store.growth(entry_id)["hours"] == 26


def test_repository_without_star_metadata_stays_unknown(store):
    entry_id = store.save_item(item("repository", metrics={"hn_score": 120}), 1)
    assert store.growth(entry_id) is None
    assert "stars" not in store.entry(entry_id)["metrics"]
    ranked = rank_candidates(store.entries(), store, set())
    assert "HN 得分 120" in preview_digest(ranked)[0]["reason"]


def test_preview_limit_and_no_invented_growth(store):
    for i in range(14):
        store.save_item(item("repository", title=f"org/project-{i}", url=f"https://github.com/org/p{i}"), 1)
    candidates = rank_candidates(store.entries(), store, set())
    result = preview_digest(candidates)
    assert len(result) == 5
    assert all(row["growth"] is None for row in result)
    assert all("暂无" in row["reason"] for row in result)


@pytest.mark.parametrize(
    "hour,latest_date,expected",
    [(8, None, False), (9, None, True), (23, "2026-10-28", False), (10, "2026-10-26", True)],
)
def test_daily_generation_window(hour, latest_date, expected):
    now = datetime(2026, 10, 28, hour, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    latest = {"local_date": latest_date} if latest_date else None
    assert needs_daily_digest(now, "09:00", latest) is expected


@pytest.mark.parametrize("path", ["/", "/demo", "/history", "/sources", "/runs", "/settings"])
def test_local_pages_without_api_key(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "AI PULSE" in response.text
    assert "utf-8" in response.headers["content-type"]


def test_demo_is_not_current_news(client):
    text = client.get("/").text
    assert "演示样例" in text
    assert "不是今日新闻" in text


def test_windows_javascript_mime_type_is_executable(client):
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")


def test_api_does_not_return_secrets_and_changes_require_csrf(client):
    config = client.get("/api/settings").json()
    assert "llm_api_key" not in config and "github_token" not in config
    assert client.post("/api/runs", json={"kind": "collect"}).status_code == 403
    assert (
        client.post(
            "/api/runs",
            json={"kind": "collect"},
            headers=headers(client) | {"Origin": "https://another.example"},
        ).status_code
        == 403
    )


def test_model_request_not_attempted_before_configuration(client):
    result = client.post("/api/runs", json={"kind": "model_test"}, headers=headers(client))
    assert result.status_code == 409
    assert client.get("/api/status").json()["runs"] == []


def test_source_operations_and_private_address_rejection(client):
    request_headers = headers(client)
    source = {"name": "My RSS", "kind": "rss", "url": "https://example.com/feed"}
    result = client.post("/api/sources", json=source, headers=request_headers)
    assert result.status_code == 201
    source_id = result.json()["id"]
    assert (
        client.patch(f"/api/sources/{source_id}", json={"enabled": False}, headers=request_headers).json()[
            "enabled"
        ]
        == 0
    )
    assert client.delete(f"/api/sources/{source_id}", headers=request_headers).status_code == 200
    source["url"] = "http://127.0.0.1:8000/feed"
    assert client.post("/api/sources", json=source, headers=request_headers).status_code == 422


def test_setting_persistence_and_redaction(client):
    config = client.get("/api/settings").json()
    config.update(
        {"llm_api_key": "test-only-secret", "github_token": "test-only-token", "schedule_time": "10:15"}
    )
    response = client.patch("/api/settings", json=config, headers=headers(client))
    assert response.status_code == 422
    assert "test-only-secret" not in response.text
    assert "test-only-token" not in client.get("/settings").text
    config.update(llm_api_key="", github_token="")
    response = client.patch("/api/settings", json=config, headers=headers(client))
    assert response.status_code == 200 and response.json()["has_api_key"] is False
    config["schedule_time"] = "26:00"
    assert client.patch("/api/settings", json=config, headers=headers(client)).status_code == 422


async def test_task_coalescing_preview_revision_and_failure_keeps_previous(store, tmp_path, monkeypatch):
    service = Service(store, Network(), Settings(data_dir=tmp_path, scheduler_enabled=False))

    async def collect(source):
        return [CollectedItem(**item())]

    monkeypatch.setattr(service.collectors, "collect", collect)
    first, created = service.submit("digest")
    same, created_again = service.submit("digest")
    assert created and not created_again and first == same
    await service.active_task
    assert store.runs()[0]["state"] == "success"
    assert store.digest()["mode"] == "preview"
    assert len(store.digest()["items"]) == 1
    service.submit("digest")
    await service.active_task
    assert store.digest()["revision"] == 2
    previous = store.digest()["id"]

    async def failing(source):
        raise ValueError("source unavailable")

    monkeypatch.setattr(service.collectors, "collect", failing)
    service.submit("digest")
    await service.active_task
    assert store.runs()[0]["state"] == "failed"
    assert store.digest()["id"] == previous
    await service.close()


def test_interrupted_tasks_recover_and_historical_references_survive(store):
    run_id = store.start_run("collect", "test")
    store.recover_runs()
    assert next(row for row in store.runs() if row["id"] == run_id)["state"] == "aborted"
    entry_id = store.save_item(item(), 1)
    store.remove_source(1)
    assert store.entry(entry_id)["sources"][0]["name"] == "Test"

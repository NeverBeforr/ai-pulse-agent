import httpx
import pytest

from ai_pulse.content import fetch_content
from ai_pulse.storage import Store


def article(text):
    return (
        "<html><title>AI开发实践</title><article><h1>AI开发实践</h1>"
        + "".join(f"<p>{part}</p>" for part in text)
        + "</article></html>"
    )


def item():
    return {"id": 1, "url": "https://example.com/article", "kind": "news", "summary": "只有摘要"}


class Pages:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    async def get(self, url, **kwargs):
        self.calls.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return httpx.Response(
            200, text=value, headers={"content-type": "text/html"}, request=httpx.Request("GET", url)
        )


async def test_body_contains_late_limitations_not_only_feed_summary():
    text = [
        "开发团队使用AI工具处理文档并提供可追溯的检索来源。" * 8,
        "限制：该工具只支持公开文档，不支持离线部署。" * 8,
    ]
    result = await fetch_content(item(), Pages({item()["url"]: article(text)}))
    assert result["status"] == "ready" and "不支持离线部署" in result["text"]
    assert result["text"] != "只有摘要"


async def test_feed_full_content_does_not_fetch_remote():
    network = Pages({})
    result = await fetch_content(
        dict(item(), feed_content=article(["AI工具的具体使用方法和限制。" * 30])), network
    )
    assert result["status"] == "ready" and result["method"] == "feed_content" and not network.calls


async def test_feed_content_with_read_more_is_not_treated_as_full_article():
    preview = article(["AI产品的简短预览。" * 40]) + '<a href="https://example.com/article">Read more</a>'
    network = Pages(
        {item()["url"]: article(["实际正文包含使用条件。" * 30, "最后限制：仅支持云端运行。" * 15])}
    )
    result = await fetch_content(dict(item(), feed_content=preview), network)
    assert result["method"] == "html" and network.calls == [item()["url"]]
    assert "最后限制" in result["text"]


async def test_aggregator_follows_explicit_original_link():
    network = Pages(
        {
            item()[
                "url"
            ]: '<article><a class="source-link" href="https://publisher.example/post">阅读原文</a></article>',
            "https://publisher.example/post": article(["一手文章中的AI开发案例与部署条件。" * 30]),
        }
    )
    result = await fetch_content(item(), network)
    assert result["status"] == "ready" and result["original_url"] == "https://publisher.example/post"
    assert result["method"] == "upstream_html" and len(network.calls) == 2


async def test_empty_or_login_page_is_not_fulltext():
    result = await fetch_content(
        item(), Pages({item()["url"]: "<title>Sign in to continue</title><p>login</p>"})
    )
    assert result["status"] == "insufficient"


async def test_http_failure_is_not_converted_to_summary():
    with pytest.raises(httpx.ConnectError):
        await fetch_content(item(), Pages({item()["url"]: httpx.ConnectError("unavailable")}))


@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/abs/1234.12345",
        "https://example.com/paper.pdf",
        "https://www.youtube.com/watch?v=demo",
    ],
)
async def test_abstract_and_video_pages_are_not_claimed_as_fulltext(url):
    network = Pages({})
    result = await fetch_content(dict(item(), url=url), network)
    assert result["status"] == "unsupported" and not network.calls


async def test_readme_is_full_project_description():
    network = Pages(
        {
            "https://api.github.com/repos/owner/project/readme": "# AI项目\n\n"
            + "公开使用说明和功能限制。" * 30
        }
    )
    result = await fetch_content(
        dict(item(), kind="repository", url="https://github.com/owner/project"), network
    )
    assert result["status"] == "ready" and result["method"] == "readme"


def test_snapshot_excludes_raw_text_and_preserves_original(tmp_path):
    store = Store(tmp_path)
    store.seed_sources(
        [
            dict(
                name="source",
                kind="rss",
                url="https://example.com/rss",
                channel="test",
                enabled=True,
                state="ok",
                message="",
            )
        ]
    )
    identity = store.save_item(
        dict(
            title="AI工具",
            url=item()["url"],
            kind="news",
            summary="PRIVATE_SNIPPET",
            published_at=None,
            metrics={},
            feed_content="PRIVATE_FEED",
        ),
        1,
    )
    content = dict(
        discovery_url=item()["url"],
        final_url=item()["url"],
        original_url=None,
        method="html",
        status="ready",
        text="PRIVATE_BODY" * 50,
        error="",
    )
    store.save_content(identity, content)
    run = store.start_run("digest", "test")
    store.record_evaluation(run, identity, "read", "ready", {"quotes": ["PRIVATE_EVIDENCE"]})
    target = tmp_path / "snapshot/pulse.db"
    store.export_state(target)
    snapshot = Store(target.parent, database_path=target)
    assert snapshot.content(identity) is None and not snapshot.evaluations(run)
    assert snapshot.entries()[0]["summary"] == "" and snapshot.entries()[0]["feed_content"] == ""
    assert store.content(identity)["text"] == content["text"]
    assert b"PRIVATE_" not in target.read_bytes()


def test_other_source_without_body_does_not_erase_explicit_feed_content(tmp_path):
    store = Store(tmp_path)
    store.seed_sources(
        [
            dict(
                name=f"source{identity}",
                kind="rss",
                url=f"https://example.com/feed{identity}",
                channel="test",
                enabled=True,
                state="ok",
                message="",
            )
            for identity in (1, 2)
        ]
    )
    row = dict(title="AI工具", url=item()["url"], kind="news", summary="摘要", published_at=None, metrics={})
    store.save_item(dict(row, feed_content="已由订阅明确提供的文章正文"), 1)
    store.save_item(row, 2)
    saved = store.entries()[0]
    assert saved["feed_content"] == "已由订阅明确提供的文章正文"
    assert len(saved["sources"]) == 2

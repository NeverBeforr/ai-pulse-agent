from datetime import UTC, datetime, timedelta

from test_status_semantics import fake_generation, fake_sinks, fake_sources

from ai_pulse import pipeline
from ai_pulse.offline import fixture_digest
from ai_pulse.storage import Store


def seed(tmp_path):
    store = Store(tmp_path)
    store.seed_sources(
        [
            {
                "name": "sample",
                "kind": "rss",
                "url": "https://example.com/rss",
                "channel": "示例",
                "enabled": True,
                "state": "ok",
                "message": "",
            }
        ]
    )
    row = fixture_digest()["items"][0]
    identity = store.save_item(
        {k: row[k] for k in ("title", "url", "kind", "summary", "published_at", "metrics")}, 1
    )
    digest_id = store.save_digest(
        "2026-11-01", "cloud", datetime.now(UTC).isoformat(), [row], {"format_version": 2}
    )
    return store, identity, digest_id


def test_delivery_window_and_expiry(tmp_path):
    store, identity, digest_id = seed(tmp_path)
    store.mark_delivered([identity], digest_id)
    assert identity in store.recently_delivered(7)
    with store.connection() as db:
        db.execute(
            "UPDATE delivered_entries SET delivered_at=?",
            ((datetime.now(UTC) - timedelta(days=8)).isoformat(),),
        )
    assert identity not in store.recently_delivered(7)


def test_delivery_records_are_idempotent(tmp_path):
    store, identity, digest_id = seed(tmp_path)
    assert store.record_delivery(digest_id, "feishu:0", "pending")
    store.record_delivery(digest_id, "feishu:0", "sent")
    assert not store.record_delivery(digest_id, "feishu:0", "pending")
    assert len(store.deliveries(digest_id)) == 1
    assert store.deliveries(digest_id)[0]["attempts"] == 1


async def test_retry_failed_sink_does_not_regenerate_or_repeat_sent_sink(pulse_config, monkeypatch):
    fake_sources(monkeypatch)
    fake_generation(monkeypatch)
    sent = []

    class Sink:
        def __init__(self, kind):
            self.kind = kind

        async def send(self, digest, items):
            sent.append(self.kind)
            if self.kind == "feishu":
                raise RuntimeError("第一次投递失败")

    monkeypatch.setattr(pipeline, "create_sink", lambda config: Sink(config.type))
    first = await pipeline.run_once(pulse_config, date="2026-11-01")
    assert first.state == "partial"

    async def forbidden(*args, **kwargs):
        raise AssertionError("投递重试不能重新调用模型")

    monkeypatch.setattr(pipeline, "generate_items", forbidden)

    class GoodSink:
        async def send(self, digest, items):
            sent.append("重试飞书")

    monkeypatch.setattr(pipeline, "create_sink", lambda config: GoodSink())
    second = await pipeline.run_once(pulse_config, date="2026-11-01")
    assert second.state == "success" and sent == ["feishu", "markdown", "重试飞书"]


async def test_delivered_entries_are_not_sent_again(pulse_config, monkeypatch):
    fake_sources(monkeypatch)
    fake_generation(monkeypatch)
    fake_sinks(monkeypatch)
    first = await pipeline.run_once(pulse_config, date="2026-11-01")
    second = await pipeline.run_once(pulse_config, date="2026-11-01")
    assert first.exit_code == 0 and second.exit_code == 4


def test_old_database_migration_is_additive(tmp_path):
    import sqlite3

    original = '[{"entry_id":1,"title":"旧简报","kind":"news"}]'
    with sqlite3.connect(tmp_path / "pulse.sqlite3") as old:
        old.execute(
            "CREATE TABLE digests(id INTEGER PRIMARY KEY,local_date TEXT,revision INTEGER,mode TEXT,cutoff TEXT,created_at TEXT,items TEXT,coverage TEXT)"
        )
        old.execute(
            "INSERT INTO digests VALUES(1,?,?,?,?,?,?,?)",
            (
                "2026-10-30",
                1,
                "preview",
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
                original,
                "{}",
            ),
        )
    store = Store(tmp_path)
    Store(tmp_path)
    with store.connection() as db:
        columns = {r["name"] for r in db.execute("PRAGMA table_info(digests)")}
        assert {"status", "delivery", "delivered_at"} <= columns
        assert db.execute("SELECT items FROM digests WHERE id=1").fetchone()[0] == original
    assert store.digest(1)["status"] == "ok" and store.digest(1)["items"][0]["title"] == "旧简报"

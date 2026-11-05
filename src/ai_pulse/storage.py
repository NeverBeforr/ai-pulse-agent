import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


RUN_STATES = {"running", "success", "partial", "degraded", "failed", "aborted"}


class Store:
    def __init__(self, data_dir: Path, *, database_path: Path | None = None):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = database_path or data_dir / "pulse.sqlite3"
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, url TEXT NOT NULL,
                    channel TEXT NOT NULL, enabled INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    message TEXT NOT NULL DEFAULT '', last_attempt TEXT, last_success TEXT,
                    UNIQUE(name, url)
                );
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL, summary TEXT NOT NULL, published_at TEXT, first_seen TEXT NOT NULL,
                    updated_at TEXT NOT NULL, metrics TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS entry_sources (
                    entry_id INTEGER NOT NULL REFERENCES entries(id),
                    source_id INTEGER NOT NULL REFERENCES sources(id),
                    PRIMARY KEY(entry_id, source_id)
                );
                CREATE TABLE IF NOT EXISTS repo_snapshots (
                    id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES entries(id),
                    captured_at TEXT NOT NULL, stars INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS snapshot_entry_time ON repo_snapshots(entry_id, captured_at);
                CREATE TABLE IF NOT EXISTS digests (
                    id INTEGER PRIMARY KEY, local_date TEXT NOT NULL, revision INTEGER NOT NULL,
                    mode TEXT NOT NULL, cutoff TEXT NOT NULL, created_at TEXT NOT NULL,
                    items TEXT NOT NULL, coverage TEXT NOT NULL, UNIQUE(local_date, revision)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, trigger TEXT NOT NULL,
                    state TEXT NOT NULL, stage TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT, detail TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS model_usage (
                    id INTEGER PRIMARY KEY, run_id INTEGER, model TEXT NOT NULL, created_at TEXT NOT NULL,
                    input_tokens INTEGER, output_tokens INTEGER, estimated_cost REAL
                );
            """)
        self._migrate()

    def _migrate(self):
        with self.connection() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(digests)")}
            for name, definition in {
                "status": "TEXT NOT NULL DEFAULT 'ok'",
                "delivered_at": "TEXT",
                "delivery": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE digests ADD COLUMN {name} {definition}")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY, digest_id INTEGER NOT NULL REFERENCES digests(id),
                    sink TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '', sent_at TEXT, UNIQUE(digest_id,sink)
                );
                CREATE TABLE IF NOT EXISTS delivered_entries (
                    entry_id INTEGER PRIMARY KEY REFERENCES entries(id), digest_id INTEGER NOT NULL,
                    delivered_at TEXT NOT NULL
                );
                PRAGMA user_version=3;
            """)
            db.execute("UPDATE runs SET state='aborted' WHERE state='interrupted'")

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db

    def seed_sources(self, sources: list[dict]) -> None:
        with self.connection() as db:
            for source in sources:
                db.execute(
                    "INSERT OR IGNORE INTO sources(name,kind,url,channel,enabled,state,message) "
                    "VALUES(:name,:kind,:url,:channel,:enabled,:state,:message)",
                    source,
                )

    def sources(self) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM sources ORDER BY id")]

    def source(self, source_id: int) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            return dict(row) if row else None

    def update_source(self, source_id: int, **values) -> None:
        columns = {
            key: value
            for key, value in values.items()
            if key in {"enabled", "state", "message", "last_attempt", "last_success"}
        }
        with self.connection() as db:
            db.execute(
                f"UPDATE sources SET {','.join(f'{key}=?' for key in columns)} WHERE id=?",
                (*columns.values(), source_id),
            )

    def add_source(self, source: dict) -> int:
        with self.connection() as db:
            cursor = db.execute(
                "INSERT INTO sources(name,kind,url,channel,enabled,state) VALUES(?,?,?,?,?,?)",
                (
                    source["name"],
                    source["kind"],
                    source["url"],
                    source["channel"],
                    source["enabled"],
                    "pending" if source["kind"] == "web" else "unverified",
                ),
            )
            return cursor.lastrowid

    def remove_source(self, source_id: int) -> None:
        # Preserve source identity in historical citations; removal is a soft delete.
        self.update_source(source_id, enabled=False, state="removed", message="已移除；历史引用保留")

    def save_item(self, item: dict, source_id: int) -> int:
        now = utcnow()
        with self.connection() as db:
            row = db.execute("SELECT * FROM entries WHERE url=?", (item["url"],)).fetchone()
            if row:
                entry_id = row["id"]
                keep_repository = row["kind"] == "repository" and item["kind"] != "repository"
                old_metrics = json.loads(row["metrics"])
                metrics = old_metrics | item["metrics"]
                keep_time = keep_repository or old_metrics.get("time_priority", 0) > item["metrics"].get(
                    "time_priority", 0
                )
                if keep_time:
                    for key in ("time_label", "time_precision", "time_priority"):
                        if key in old_metrics:
                            metrics[key] = old_metrics[key]
                db.execute(
                    "UPDATE entries SET title=?,summary=?,kind=?,published_at=COALESCE(?,published_at),"
                    "updated_at=?,metrics=? WHERE id=?",
                    (
                        row["title"] if keep_repository else item["title"],
                        row["summary"] if keep_repository else (item["summary"] or row["summary"]),
                        "repository" if keep_repository else item["kind"],
                        row["published_at"] if keep_time else item["published_at"],
                        now,
                        json.dumps(metrics, ensure_ascii=False),
                        entry_id,
                    ),
                )
            else:
                cursor = db.execute(
                    "INSERT INTO entries(title,url,kind,summary,published_at,first_seen,updated_at,metrics) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (
                        item["title"],
                        item["url"],
                        item["kind"],
                        item["summary"],
                        item["published_at"],
                        now,
                        now,
                        json.dumps(item["metrics"], ensure_ascii=False),
                    ),
                )
                entry_id = cursor.lastrowid
            db.execute("INSERT OR IGNORE INTO entry_sources VALUES(?,?)", (entry_id, source_id))
            if item["kind"] == "repository" and isinstance(item["metrics"].get("stars"), int):
                db.execute(
                    "INSERT INTO repo_snapshots(entry_id,captured_at,stars) VALUES(?,?,?)",
                    (entry_id, now, item["metrics"]["stars"]),
                )
            return entry_id

    def entries(self, since: str | None = None) -> list[dict]:
        with self.connection() as db:
            query = "SELECT * FROM entries"
            args = ()
            if since:
                query += (
                    " WHERE (kind='news' AND COALESCE(published_at, first_seen)>=?) OR "
                    "(kind='repository' AND updated_at>=?)"
                )
                args = (since, since)
            rows = [dict(row) for row in db.execute(query + " ORDER BY updated_at DESC LIMIT 400", args)]
            source_map = {}
            for row in db.execute(
                "SELECT es.entry_id,s.id,s.name,s.channel,s.kind FROM entry_sources es JOIN sources s "
                "ON s.id=es.source_id"
            ):
                source_map.setdefault(row["entry_id"], []).append(
                    {key: row[key] for key in ("id", "name", "channel", "kind")}
                )
            for item in rows:
                item["metrics"] = json.loads(item["metrics"])
                item["sources"] = source_map.get(item["id"], [])
            return rows

    def entry(self, entry_id: int) -> dict | None:
        return next((item for item in self.entries() if item["id"] == entry_id), None)

    def growth(self, entry_id: int) -> dict | None:
        with self.connection() as db:
            current = db.execute(
                "SELECT * FROM repo_snapshots WHERE entry_id=? ORDER BY captured_at DESC LIMIT 1",
                (entry_id,),
            ).fetchone()
            if not current:
                return None
            target = (datetime.fromisoformat(current["captured_at"]) - timedelta(hours=24)).isoformat()
            previous = db.execute(
                "SELECT * FROM repo_snapshots WHERE entry_id=? AND captured_at<=? "
                "ORDER BY captured_at DESC LIMIT 1",
                (entry_id, target),
            ).fetchone()
            if not previous:
                return None
            hours = (
                datetime.fromisoformat(current["captured_at"])
                - datetime.fromisoformat(previous["captured_at"])
            ).total_seconds() / 3600
            return {
                "delta": current["stars"] - previous["stars"],
                "hours": round(hours, 1),
                "start": previous["captured_at"],
                "end": current["captured_at"],
            }

    def save_digest(self, local_date: str, mode: str, cutoff: str, items: list, coverage: dict) -> int:
        with self.connection() as db:
            revision = db.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM digests WHERE local_date=?", (local_date,)
            ).fetchone()[0]
            cursor = db.execute(
                "INSERT INTO digests(local_date,revision,mode,cutoff,created_at,items,coverage) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    local_date,
                    revision,
                    mode,
                    cutoff,
                    utcnow(),
                    json.dumps(items, ensure_ascii=False),
                    json.dumps(coverage, ensure_ascii=False),
                ),
            )
            return cursor.lastrowid

    def digests(self) -> list[dict]:
        with self.connection() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM digests ORDER BY id DESC LIMIT 200")]
        for row in rows:
            row["items"] = json.loads(row["items"])
            row["coverage"] = json.loads(row["coverage"])
            row["legacy"] = row["coverage"].get("format_version", 1) < 2
            if row["legacy"]:
                # Old editions predate editorial sections. Normalize once at the persisted-data boundary.
                for item in row["items"]:
                    item["section"] = "products" if item["kind"] == "repository" else "industry"
            row["sections"] = {
                section: [item for item in row["items"] if item["section"] == section]
                for section in ("industry", "products")
            }
        return rows

    def digest(self, digest_id: int | None = None) -> dict | None:
        return next((d for d in self.digests() if digest_id is None or d["id"] == digest_id), None)

    def covered_entries(self, days: int = 7, exclude_date: str | None = None) -> set[int]:
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        return {
            item["entry_id"]
            for digest in self.digests()
            if digest["created_at"] >= since and digest["local_date"] != exclude_date
            for item in digest["items"]
        }

    def start_run(self, kind: str, trigger: str) -> int:
        with self.connection() as db:
            cursor = db.execute(
                "INSERT INTO runs(kind,trigger,state,stage,started_at) VALUES(?,?,'running','准备中',?)",
                (kind, trigger, utcnow()),
            )
            return cursor.lastrowid

    def progress(self, run_id: int, stage: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE runs SET stage=? WHERE id=?", (stage, run_id))

    def finish_run(self, run_id: int, state: str, detail: dict) -> None:
        if state not in RUN_STATES:
            raise ValueError(f"非法运行状态：{state}；可用：{', '.join(sorted(RUN_STATES))}")
        with self.connection() as db:
            db.execute(
                "UPDATE runs SET state=?,stage=?,detail=?,finished_at=? WHERE id=?",
                (
                    state,
                    "完成" if state == "success" else "未完成",
                    json.dumps(detail, ensure_ascii=False),
                    utcnow(),
                    run_id,
                ),
            )

    def recover_runs(self) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE runs SET state='aborted',stage='进程已中断',finished_at=? WHERE state='running'",
                (utcnow(),),
            )

    def runs(self) -> list[dict]:
        with self.connection() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 80")]
        for row in rows:
            row["detail"] = json.loads(row["detail"])
        return rows

    def usage(self) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM model_usage ORDER BY id DESC LIMIT 100")]

    def record_usage(
        self, run_id: int, model: str, inputs: int | None, outputs: int | None, cost: float | None
    ) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO model_usage(run_id,model,created_at,input_tokens,output_tokens,"
                "estimated_cost) VALUES(?,?,?,?,?,?)",
                (run_id, model, utcnow(), inputs, outputs, cost),
            )

    def mark_delivered(self, entry_ids: list[int], digest_id: int):
        with self.connection() as db:
            db.executemany(
                "INSERT INTO delivered_entries(entry_id,digest_id,delivered_at) VALUES(?,?,?) "
                "ON CONFLICT(entry_id) DO UPDATE SET digest_id=excluded.digest_id,delivered_at=excluded.delivered_at",
                [(entry_id, digest_id, utcnow()) for entry_id in entry_ids],
            )

    def recently_delivered(self, days: int) -> set[int]:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self.connection() as db:
            return {
                row[0]
                for row in db.execute(
                    "SELECT entry_id FROM delivered_entries WHERE delivered_at>=?", (cutoff,)
                )
            }

    def record_delivery(self, digest_id: int, sink: str, state: str, error: str = "") -> bool:
        if state not in {"pending", "sent", "failed", "skipped"}:
            raise ValueError("投递状态只能是pending、sent、failed、skipped")
        with self.connection() as db:
            previous = db.execute(
                "SELECT state FROM deliveries WHERE digest_id=? AND sink=?", (digest_id, sink)
            ).fetchone()
            if previous and previous["state"] == "sent":
                return False
            db.execute(
                "INSERT INTO deliveries(digest_id,sink,state,attempts,last_error,sent_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(digest_id,sink) DO UPDATE SET state=excluded.state, "
                "attempts=deliveries.attempts+?,last_error=excluded.last_error,sent_at=excluded.sent_at",
                (
                    digest_id,
                    sink,
                    state,
                    int(state == "pending"),
                    error,
                    utcnow() if state == "sent" else None,
                    int(state == "pending"),
                ),
            )
        return True

    def deliveries(self, digest_id: int) -> list[dict]:
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM deliveries WHERE digest_id=? ORDER BY id", (digest_id,))
            ]

    def update_delivery_summary(self, digest_id: int, *, status: str):
        deliveries = self.deliveries(digest_id)
        sent = [row["sent_at"] for row in deliveries if row["state"] == "sent"]
        payload = {
            row["sink"]: {"state": row["state"], "error": row["last_error"], "attempts": row["attempts"]}
            for row in deliveries
        }
        with self.connection() as db:
            db.execute(
                "UPDATE digests SET status=?,delivered_at=?,delivery=? WHERE id=?",
                (status, max(sent) if sent else None, json.dumps(payload, ensure_ascii=False), digest_id),
            )

    def retryable_digest(self, date: str) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT d.id FROM digests d JOIN deliveries l ON l.digest_id=d.id "
                "WHERE d.local_date=? AND l.state IN ('failed','pending') ORDER BY d.id DESC LIMIT 1",
                (date,),
            ).fetchone()
        return self.digest(row[0]) if row else None

    def delivered_history(self, days: int) -> list[dict]:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT e.title,e.url FROM delivered_entries d JOIN entries e ON e.id=d.entry_id "
                    "WHERE d.delivered_at>=? ORDER BY d.delivered_at DESC LIMIT 20",
                    (cutoff,),
                )
            ]

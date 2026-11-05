from datetime import UTC, datetime
from hashlib import sha1

from ai_pulse.digest import rank_candidates


class Store:
    def growth(self, identity):
        return None


def entry(identity, title, sources, score=0):
    return {
        "id": identity,
        "title": title,
        "summary": "AI应用资料",
        "kind": "news",
        "published_at": "2026-11-01T00:00:00+00:00",
        "metrics": {"hn_score": score},
        "sources": sources,
    }


def source(identity, kind="rss"):
    return {"id": identity, "name": str(identity), "kind": kind, "channel": "媒体"}


def test_hn_no_longer_dominates():
    hn = entry(1, "AI HN high score", [source(1, "hn")], 1000000)
    multi = entry(2, "AI cross-source case", [source(2), source(3), source(4)])
    result = rank_candidates([hn, multi], Store(), set(), now=datetime(2026, 11, 1, tzinfo=UTC))
    assert result[0]["id"] == 2 and result[0]["rank_score"] >= result[1]["rank_score"]


def test_single_source_no_penalty():
    row = entry(1, "AI single source", [source(1)])
    assert rank_candidates([row], Store(), set())[0]["id"] == 1


def test_deterministic_order():
    rows = [entry(i, title, [source(1)]) for i, title in enumerate(["AI beta", "AI alpha", "AI gamma"], 1)]
    now = datetime(2026, 11, 1, tzinfo=UTC)
    expected = [r["id"] for r in sorted(rows, key=lambda r: sha1(r["title"].encode()).hexdigest())]
    assert [r["id"] for r in rank_candidates(rows, Store(), set(), now=now)] == expected
    assert [r["id"] for r in rank_candidates(rows[::-1], Store(), set(), now=now)] == expected

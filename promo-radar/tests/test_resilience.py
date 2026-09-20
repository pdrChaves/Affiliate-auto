"""Falhas externas e concorrência: o sistema degrada sem quebrar."""
import threading
import time
from datetime import timedelta

from app.amazon.mock import MockClient
from app.models import PostStatus, utcnow


class DownClient(MockClient):
    def get_items(self, asins, fast=False):
        raise RuntimeError("API fora do ar")

    def search(self, *a, **k):
        raise RuntimeError("API fora do ar")


def _stale_post(svc, hours=2):
    svc.collect("lego")
    p = svc.db.list_posts(["pending"], niche_id="lego")[0]
    svc.db.update_post(p["id"], price_checked_at=utcnow() - timedelta(hours=hours))
    return p


def test_send_with_api_down_warns_but_allows(svc):
    p = _stale_post(svc)
    svc.client = DownClient(svc.s)
    r = svc.prepare_send(p["id"])
    assert r["ok"] and r["warning"] and r["share_url"]
    assert svc.db.get_post(p["id"])["status"] == "pending"      # nada foi alterado


def test_send_with_api_down_blocks_after_24h(svc):
    p = _stale_post(svc, hours=25)
    svc.client = DownClient(svc.s)
    r = svc.prepare_send(p["id"])
    assert not r["ok"] and "24h" in r["reason"]


def test_refresh_with_api_down_raises_controlled(svc):
    import pytest

    from app.service import CatalogUnavailableError
    p = _stale_post(svc)
    svc.client = DownClient(svc.s)
    with pytest.raises(CatalogUnavailableError):
        svc.refresh(p["id"])


def test_collect_with_api_down_logs_error(svc):
    svc.client = DownClient(svc.s)
    r = svc.collect("lego")
    assert "error" in r and svc.db.recent_runs(1)[0]["error"]


def test_monitor_with_api_down_does_not_raise(svc):
    p = _stale_post(svc, hours=0)
    svc.mark_sent(p["id"])
    svc.client = DownClient(svc.s)
    assert svc.monitor_sent() == []


def test_concurrent_collects_do_not_duplicate(svc):
    class Slow(MockClient):
        def search(self, *a, **k):
            time.sleep(0.2)
            return super().search(*a, **k)
    svc.client = Slow(svc.s, jitter=0)
    results = []
    ths = [threading.Thread(target=lambda: results.append(svc.collect("lego"))) for _ in range(5)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    asins = [p["asin"] for p in svc.db.list_posts(["pending"], niche_id="lego")]
    assert len(asins) == len(set(asins)) == 3
    assert sum("skipped" in r for r in results) == 4


def test_unique_index_blocks_duplicates_across_processes(svc):
    import pytest

    from app.db import DuplicateActivePostError
    from app.models import Offer
    o = Offer(asin="B0DUPDUP01", title="t", url="u", price_cents=1)
    svc.db.create_post("lego", o, "H", "t", 1)
    with pytest.raises(DuplicateActivePostError):
        svc.db.create_post("lego", o, "H", "t", 1)
    svc.db.create_post("radar-homem", o, "H", "t", 1)          # outro nicho pode


def test_state_guards(svc):
    import pytest

    from app.service import InvalidStateError, NotFoundError
    p = _stale_post(svc, hours=0)
    svc.reject(p["id"])
    for fn in (svc.approve, svc.mark_sent, svc.reject):
        with pytest.raises(InvalidStateError):
            fn(p["id"])
    with pytest.raises(NotFoundError):
        svc.approve(424242)


def test_housekeeping_deletes_old(svc):
    p = _stale_post(svc, hours=0)
    svc.db.update_post(p["id"], status=PostStatus.EXPIRED, created_at=utcnow() - timedelta(days=100))
    svc.purge_old_content()
    svc.db.log_run("lego", 0, 0, {})
    with svc.db._lock:
        svc.db._conn.execute("UPDATE runs SET started_at=?", ((utcnow() - timedelta(days=40)).isoformat(),))
    res = svc.housekeeping()
    assert res["posts_apagados"] == 1 and res["coletas_apagadas"] >= 1

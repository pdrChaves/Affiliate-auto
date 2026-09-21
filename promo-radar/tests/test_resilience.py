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
    svc.run_search("headset gamer", "Games e Consoles")
    p = svc.db.list_posts(["pending"])[0]
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


def test_search_with_api_down_is_controlled(svc):
    import pytest

    from app.service import CatalogUnavailableError
    svc.client = DownClient(svc.s)
    with pytest.raises(CatalogUnavailableError):
        svc.search("teclado")
    r = svc.run_search("teclado")                      # a busca automática só registra o erro
    assert "error" in r and svc.db.recent_runs(1)[0]["error"]


def test_monitor_with_api_down_does_not_raise(svc):
    svc.s.monitor_sent_enabled = True
    p = _stale_post(svc, hours=0)
    svc.mark_sent(p["id"])
    svc.client = DownClient(svc.s)
    assert svc.monitor_sent() == []


def test_concurrent_searches_do_not_duplicate(svc):
    """Buscas simultâneas (você clicando + a rodada automática) nunca duplicam um produto na fila."""
    class Slow(MockClient):
        def search(self, *a, **k):
            time.sleep(0.2)
            return super().search(*a, **k)
    svc.client = Slow(svc.s, jitter=0)
    ths = [threading.Thread(target=svc.run_search, args=("bluetooth", "Eletrônicos, TV e Áudio")) for _ in range(5)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    asins = [p["asin"] for p in svc.db.list_posts(["pending"])]
    assert len(asins) == len(set(asins)) == 2


def test_saved_searches_do_not_overlap(svc):
    class Slow(MockClient):
        def search(self, *a, **k):
            time.sleep(0.3)
            return super().search(*a, **k)
    svc.client = Slow(svc.s, jitter=0)
    svc.save_search("bluetooth", "Eletrônicos, TV e Áudio")
    saidas = []
    ths = [threading.Thread(target=lambda: saidas.append(svc.run_saved_searches())) for _ in range(3)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert sum(1 for s in saidas if s and "skipped" in s[0]) == 2


def test_unique_index_blocks_duplicates_across_processes(svc):
    import pytest

    from app.db import DuplicateActivePostError
    from app.models import Offer
    o = Offer(asin="B0DUPDUP01", title="t", url="u", price_cents=1)
    svc.db.create_post(o, "H", "t", 1)
    with pytest.raises(DuplicateActivePostError):
        svc.db.create_post(o, "H", "t", 1)           # o índice único vale entre processos


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
    svc.db.log_run("games", 0, 0, {})
    with svc.db._lock:
        svc.db._conn.execute("UPDATE runs SET started_at=?", ((utcnow() - timedelta(days=40)).isoformat(),))
    res = svc.housekeeping()
    assert res["posts_apagados"] == 1 and res["buscas_apagadas"] >= 1

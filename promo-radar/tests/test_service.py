from datetime import timedelta, UTC

from app.models import PostStatus, utcnow


def test_collect_queues_only_valid(svc):
    res = svc.collect("eletronicos")
    assert res["queued"] == 3
    assert res["rejected"] == {"fora_de_estoque": 1, "nao_buybox": 1, "desconto_baixo": 1}
    # segunda coleta não duplica
    assert svc.collect("eletronicos")["queued"] == 0


def test_headlines_not_repeated_in_run(svc):
    svc.collect("eletronicos")
    heads = [p["headline"] for p in svc.db.list_posts(["pending"], niche_id="eletronicos")]
    assert len(set(heads)) == len(heads)


def test_send_always_revalidates_by_default(svc):
    """MAX_PRICE_AGE_MINUTES=0: o preço é conferido no clique, para o post sair com a promoção ativa."""
    svc.collect("games")
    p = svc.db.list_posts(["pending"], niche_id="games")[0]
    assert svc.is_stale(p)                                           # recém-coletado já é revalidado
    svc.client.overrides[p["asin"]] = p["price_cents"] / 100 - 5     # caiu mais 5 reais
    r = svc.prepare_send(p["id"])
    assert r["ok"] and r["share_url"].startswith("https://wa.me/?text=")
    assert r["post"]["price_cents"] == p["price_cents"] - 500         # texto reflete preço novo
    assert svc.price_age(r["post"]) < timedelta(seconds=10)           # preço conferido agora


def test_send_blocked_when_deal_died(svc):
    svc.collect("games")
    p = svc.db.list_posts(["pending"], niche_id="games")[0]
    svc.db.update_post(p["id"], price_checked_at=utcnow() - timedelta(hours=3))
    svc.client.overrides[p["asin"]] = p["basis_cents"] / 100          # voltou ao preço cheio
    r = svc.prepare_send(p["id"])
    assert not r["ok"] and r["post"]["status"] == PostStatus.EXPIRED.value


def test_monitor_is_off_by_default(svc):
    svc.collect("games")
    p = svc.db.list_posts(["pending"], niche_id="games")[0]
    svc.mark_sent(p["id"])
    svc.client.overrides[p["asin"]] = p["price_cents"] / 100 + 20
    assert svc.monitor_sent() == []
    assert svc.db.get_post(p["id"])["status"] == PostStatus.SENT.value


def test_monitor_flags_ended_promo_when_enabled(svc):
    svc.s.monitor_sent_enabled = True
    svc.collect("games")
    p = svc.db.list_posts(["pending"], niche_id="games")[0]
    svc.mark_sent(p["id"])
    assert svc.monitor_sent() == []
    svc.client.overrides[p["asin"]] = p["price_cents"] / 100 + 20
    assert svc.monitor_sent() == [p["id"]]
    assert svc.db.get_post(p["id"])["status"] == PostStatus.ENDED.value
    assert any("encerrada" in m for m in svc.notifier.msgs)


def test_expire_and_purge_24h(svc):
    svc.collect("games")
    p = svc.db.list_posts(["pending"], niche_id="games")[0]
    old = utcnow() - timedelta(hours=25)
    svc.db.update_post(p["id"], created_at=old)
    assert svc.expire_stale_queue() == 1
    assert svc.purge_old_content() >= 1
    q = svc.db.get_post(p["id"])
    assert q["asin"] == p["asin"] and q["offer"].title == "" and q["price_cents"] is None


def test_manual_hides_prices_by_default(svc):
    pid = svc.add_manual("games", "B0MANUAL01", "LEGO X", 199.9, 120.0, "CUPOM10")
    txt = svc.db.get_post(pid)["text"]
    assert "R$" not in txt and "CUPOM10" in txt


def test_manual_prices_when_allowed(svc):
    svc.s.allow_manual_prices = True
    pid = svc.add_manual("games", "B0MANUAL01", "LEGO X", 199.9, 120.0)
    assert "~De R$ 199,90~" in svc.db.get_post(pid)["text"]


def test_window(svc):
    from datetime import datetime
    n = svc.niche("eletronicos")   # 08:00–22:30 BRT
    assert svc.in_window(n, datetime(2026, 9, 19, 15, 0, tzinfo=UTC))      # 12:00 BRT
    assert not svc.in_window(n, datetime(2026, 9, 19, 7, 0, tzinfo=UTC))   # 04:00 BRT

"""Busca → fila → envio (sem nicho: tudo gira em torno do termo pesquisado)."""
from datetime import timedelta, UTC

import pytest

from app.models import PostStatus, utcnow
from app.service import InvalidInputError


def test_search_returns_evaluated_results(svc):
    res = svc.search("bluetooth", "Eletrônicos, TV e Áudio")
    assert res, "a busca deveria achar produtos"
    assert all(r["offer"].category == "Eletrônicos, TV e Áudio" for r in res)
    assert all("#publi" in r["preview"] for r in res)            # já mostra o post que sairia
    assert res[0]["ok"], "os aprovados vêm primeiro"
    assert any(not r["ok"] for r in res)                         # e os reprovados trazem o motivo
    assert {r["reason"] for r in res if r["reason"]} <= {
        "desconto_baixo", "fora_de_estoque", "nao_buybox", "faixa_de_preco", "sem_preco_de"}
    assert svc.db.count_posts() == 0                             # buscar não grava nada


def test_search_validates_term_and_category(svc):
    with pytest.raises(InvalidInputError, match="2 letras"):
        svc.search("a")
    with pytest.raises(InvalidInputError, match="inválida"):
        svc.search("teclado", "Fashion")
    vazio = [r["offer"].asin for r in svc.search("teclado", "")]
    todos = [r["offer"].asin for r in svc.search("teclado", "All")]
    assert vazio == todos                                              # vazio = todos os departamentos


def test_queue_asin_puts_one_product_in_queue(svc):
    pid = svc.queue_asin("B0MOCK0101", query="headset")
    p = svc.db.get_post(pid)
    assert p["status"] == "pending" and p["query"] == "headset"
    assert "B0MOCK0101" in p["text"] and p["text"].startswith("#publi")
    with pytest.raises(InvalidInputError, match="já está na fila"):
        svc.queue_asin("B0MOCK0101")


def test_queue_asin_allows_product_outside_the_rules_with_warning(svc):
    pid = svc.queue_asin("B0MOCK0103")          # desconto baixo
    assert "fora das regras (desconto_baixo)" in svc.db.get_post(pid)["note"]


def test_run_search_queues_only_what_passes(svc):
    res = svc.run_search("bluetooth", "Eletrônicos, TV e Áudio")
    assert res["queued"] == 2
    assert res["rejected"] == {"desconto_baixo": 1, "nao_buybox": 1}
    asins = {p["asin"] for p in svc.db.list_posts(["pending"])}
    assert "B0MOCK0003" not in asins             # desconto baixo
    assert "B0MOCK0006" not in asins             # não é buy box
    assert svc.run_search("bluetooth", "Eletrônicos, TV e Áudio")["queued"] == 0   # não repete


def test_run_search_respects_limit(svc):
    res = svc.run_search("bluetooth", "Eletrônicos, TV e Áudio", limit=1)
    assert res["queued"] == 1 and res["rejected"].get("excedeu_limite_por_busca")


def test_saved_searches_run_in_batch(svc):
    svc.save_search("fone de ouvido", "Eletrônicos, TV e Áudio")
    svc.save_search("headset gamer", "Games e Consoles")
    resultados = svc.run_saved_searches()
    assert sum(r.get("queued", 0) for r in resultados) >= 2
    assert [b["last_run_at"] is not None for b in svc.db.searches()] == [True, True]
    assert any(r.get("query") == "watchlist" for r in resultados)


def test_watchlist_queues_when_price_is_good(svc):
    svc.add_watch("b0mock0102")                 # aceita minúsculo
    assert svc.db.watchlist() == ["B0MOCK0102"]
    res = svc.run_watchlist()
    assert res["queued"] == 1
    assert svc.db.list_posts(["pending"])[0]["query"] == "watchlist"


def test_headlines_not_repeated_in_a_batch(svc):
    svc.run_search("bluetooth", "Eletrônicos, TV e Áudio")
    heads = [p["headline"] for p in svc.db.list_posts(["pending"])]
    assert len(set(heads)) == len(heads)


def test_send_always_revalidates_by_default(svc):
    """MAX_PRICE_AGE_MINUTES=0: o preço é conferido no clique, para o post sair com a promoção ativa."""
    svc.run_search("headset gamer", "Games e Consoles")
    p = svc.db.list_posts(["pending"])[0]
    assert svc.is_stale(p)
    svc.client.overrides[p["asin"]] = p["price_cents"] / 100 - 5
    r = svc.prepare_send(p["id"])
    assert r["ok"] and r["share_url"].startswith("https://wa.me/?text=")
    assert r["post"]["price_cents"] == p["price_cents"] - 500
    assert svc.price_age(r["post"]) < timedelta(seconds=10)


def test_send_blocked_when_deal_died(svc):
    svc.run_search("headset gamer", "Games e Consoles")
    p = svc.db.list_posts(["pending"])[0]
    svc.client.overrides[p["asin"]] = p["basis_cents"] / 100
    r = svc.prepare_send(p["id"])
    assert not r["ok"] and r["post"]["status"] == PostStatus.EXPIRED.value


def test_monitor_is_off_by_default(svc):
    svc.run_search("headset gamer", "Games e Consoles")
    p = svc.db.list_posts(["pending"])[0]
    svc.mark_sent(p["id"])
    svc.client.overrides[p["asin"]] = p["price_cents"] / 100 + 20
    assert svc.monitor_sent() == []
    assert svc.db.get_post(p["id"])["status"] == PostStatus.SENT.value


def test_monitor_flags_ended_promo_when_enabled(svc):
    svc.s.monitor_sent_enabled = True
    svc.run_search("headset gamer", "Games e Consoles")
    p = svc.db.list_posts(["pending"])[0]
    svc.mark_sent(p["id"])
    assert svc.monitor_sent() == []
    svc.client.overrides[p["asin"]] = p["price_cents"] / 100 + 20
    assert svc.monitor_sent() == [p["id"]]
    assert svc.db.get_post(p["id"])["status"] == PostStatus.ENDED.value
    assert any("encerrada" in m for m in svc.notifier.msgs)


def test_expire_and_purge_24h(svc):
    svc.run_search("headset gamer", "Games e Consoles")
    p = svc.db.list_posts(["pending"])[0]
    svc.db.update_post(p["id"], created_at=utcnow() - timedelta(hours=25))
    assert svc.expire_stale_queue() == 1
    assert svc.purge_old_content() >= 1
    q = svc.db.get_post(p["id"])
    assert q["asin"] == p["asin"] and q["offer"].title == "" and q["price_cents"] is None


def test_manual_hides_prices_by_default(svc):
    pid = svc.add_manual("B0MANUAL01", "Teclado X", 199.9, 120.0, "CUPOM10")
    txt = svc.db.get_post(pid)["text"]
    assert "R$" not in txt and "CUPOM10" in txt
    assert svc.db.get_post(pid)["query"] == "manual"


def test_manual_prices_when_allowed(svc):
    svc.s.allow_manual_prices = True
    pid = svc.add_manual("B0MANUAL01", "Teclado X", 199.9, 120.0)
    assert "~De R$ 199,90~" in svc.db.get_post(pid)["text"]


def test_posting_window(svc):
    from datetime import datetime
    assert svc.in_window(datetime(2026, 9, 19, 15, 0, tzinfo=UTC))      # 12:00 BRT
    assert not svc.in_window(datetime(2026, 9, 19, 7, 0, tzinfo=UTC))   # 04:00 BRT

from datetime import timedelta

from app.config import Niche
from app.db import DB
from app.models import Offer, PostStatus, utcnow
from app.pipeline.scorer import evaluate

N = Niche(id="n", name="N", min_discount_pct=20, min_price=10, max_price=1000, cooldown_hours=72,
          repost_if_drop_pct=5)


def mk(**kw):
    base = dict(asin="B000000001", title="T", url="u", price_cents=8000, basis_cents=12000,
                basis_type="WAS_PRICE")
    base.update(kw)
    return Offer(**base)


def test_accepts_good_deal():
    v = evaluate(mk(), N, DB(":memory:"))
    assert v.ok and v.score > 30


def test_rejections():
    db = DB(":memory:")
    assert evaluate(mk(price_cents=None), N, db).reason == "sem_preco"
    assert evaluate(mk(in_stock=False), N, db).reason == "fora_de_estoque"
    assert evaluate(mk(is_buybox=False), N, db).reason == "nao_buybox"
    assert evaluate(mk(condition_new=False), N, db).reason == "nao_novo"
    assert evaluate(mk(basis_cents=None), N, db).reason == "sem_preco_de"
    assert evaluate(mk(price_cents=11000), N, db).reason == "desconto_baixo"
    assert evaluate(mk(price_cents=500, basis_cents=5000), N, db).reason == "faixa_de_preco"


def test_list_price_and_suspicious_warnings():
    db = DB(":memory:")
    v = evaluate(mk(basis_type="LIST_PRICE"), N, db)
    assert v.ok and any("tabela" in w for w in v.warnings)
    strict = N.model_copy(update={"accept_list_price": False})
    assert evaluate(mk(basis_type="LIST_PRICE"), strict, db).reason == "de_eh_preco_de_tabela"
    v = evaluate(mk(price_cents=2000, basis_cents=12000), N, db)
    assert any("SUSPEITO" in w for w in v.warnings)


def test_cooldown_and_repost_on_drop():
    db = DB(":memory:")
    pid = db.create_post("n", mk(), "H", "txt", 10)
    assert evaluate(mk(), N, db).reason == "ja_na_fila"
    db.update_post(pid, status=PostStatus.SENT, sent_at=utcnow())
    assert evaluate(mk(), N, db).reason == "cooldown"
    assert evaluate(mk(price_cents=7000), N, db).ok            # caiu >5% → pode repostar
    db.update_post(pid, sent_at=utcnow() - timedelta(hours=80))
    assert evaluate(mk(), N, db).ok                            # passou o cooldown

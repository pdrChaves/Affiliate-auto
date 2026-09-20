from app.config import Niche
from app.models import Offer
from app.pipeline.render import brl, render_post, truncate_title, whatsapp_share_url


def test_brl():
    assert brl(31600) == "R$ 316,00"
    assert brl(123456789) == "R$ 1.234.567,89"
    assert brl(5) == "R$ 0,05"


def test_truncate_only_cuts_never_rewrites():
    t = "LEGO " + "palavra " * 30
    out = truncate_title(t, 40)
    assert len(out) <= 41 and out.endswith("…")
    assert t.startswith(out[:-1])


def test_post_has_disclosure_prices_timestamp():
    o = Offer(asin="B000000001", title="Produto X", url="https://www.amazon.com.br/dp/B000000001?tag=t-20",
              price_cents=31600, basis_cents=59900, merchant="Amazon.com.br")
    txt = render_post(o, Niche(id="n", name="N"), "CHAMADA", "America/Sao_Paulo")
    assert txt.splitlines()[0].startswith("#publi")           # CONAR: identificação no topo
    assert "~De R$ 599,00~" in txt and "*Por R$ 316,00*" in txt
    assert "Preço verificado em" in txt and "podem mudar" in txt  # carimbo exigido pela Amazon
    assert "amazon.com.br/dp/B000000001?tag=t-20" in txt          # link transparente


def test_hidden_prices_mode():
    o = Offer(asin="B000000001", title="X", url="u", price_cents=100, basis_cents=200, source="manual")
    txt = render_post(o, Niche(id="n", name="N"), "H", "America/Sao_Paulo", show_prices=False)
    assert "R$" not in txt and "Confira o preço" in txt


def test_share_url_encodes():
    assert whatsapp_share_url("a b*\n").startswith("https://wa.me/?text=a%20b%2A%0A")

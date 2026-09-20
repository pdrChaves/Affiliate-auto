import httpx

from app.config import Niche, Settings, Style
from app.models import Offer
from app.pipeline.copywriter import Copywriter, validate_headline

NICHE = Niche(id="n", name="N", style=Style(headline_fallbacks=["A", "B", "C"]))
OFFER = Offer(asin="B000000001", title="T", url="u", price_cents=1)


def test_validate_rules():
    assert validate_headline("pra treinar no conforto") == "PRA TREINAR NO CONFORTO"
    assert validate_headline("50% OFF HOJE") is None
    assert validate_headline("MENOR PREÇO DO ANO") is None
    assert validate_headline("X" * 60) is None


def test_fallback_rotates():
    c = Copywriter(Settings(_env_file=None))
    first = c.headline(OFFER, NICHE)
    second = c.headline(OFFER, NICHE, avoid=[first])
    assert first != second


def _cw(reply):
    def h(req):
        return httpx.Response(200, json={"content": [{"type": "text", "text": reply}]})
    return Copywriter(Settings(_env_file=None, anthropic_api_key="k"), http=httpx.Client(transport=httpx.MockTransport(h)))


def test_llm_used_when_valid():
    assert _cw("Montagem de respeito 🧱").headline(OFFER, NICHE) == "MONTAGEM DE RESPEITO 🧱"


def test_llm_rejected_falls_back():
    assert _cw("SÓ R$ 99 HOJE").headline(OFFER, NICHE) in {"A", "B", "C"}

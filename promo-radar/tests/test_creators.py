import base64
import json

import httpx

from app.amazon.creators import CreatorsClient, parse_item
from app.config import Settings

ITEM = {
    "asin": "B0TESTE001", "detailPageURL": "https://www.amazon.com.br/dp/B0TESTE001?tag=x-20",
    "itemInfo": {"title": {"displayValue": "LEGO Teste"}, "features": {"displayValues": ["a", "b"]}},
    "images": {"primary": {"large": {"url": "https://m.media-amazon.com/images/I/x.jpg"}}},
    "offersV2": {"listings": [
        {"isBuyBoxWinner": False, "price": {"money": {"amount": 90.0}}},
        {"isBuyBoxWinner": True, "availability": {"type": "IN_STOCK"}, "condition": {"value": "New"},
         "merchantInfo": {"name": "Amazon.com.br"}, "dealDetails": {"badge": "Oferta Relâmpago"},
         "price": {"money": {"amount": 116.89, "currency": "BRL"},
                   "savingBasis": {"money": {"amount": 189.99}, "savingBasisType": "WAS_PRICE"},
                   "savings": {"money": {"amount": 73.10}, "percentage": 38}}}]},
}


def test_parse_prefers_buybox_and_reads_prices():
    o = parse_item(ITEM, "www.amazon.com.br", "meutag-20")
    assert o.price_cents == 11689 and o.basis_cents == 18999 and o.savings_pct == 38
    assert o.is_buybox and o.in_stock and o.deal_badge == "Oferta Relâmpago"
    assert o.url == "https://www.amazon.com.br/dp/B0TESTE001?tag=meutag-20"


def test_parse_accepts_pascalcase():
    pascal = {"ASIN": "B0TESTE002", "ItemInfo": {"Title": {"DisplayValue": "X"}},
              "OffersV2": {"Listings": [{"IsBuyBoxWinner": True, "Price": {"Money": {"Amount": 10}}}]}}
    assert parse_item(pascal, "www.amazon.com.br", "t-20").price_cents == 1000


def _client(version, calls):
    def handler(req: httpx.Request):
        calls.append(req)
        if req.url.path.endswith("token"):
            return httpx.Response(200, json={"access_token": "TKN", "expires_in": 3600})
        if len([c for c in calls if c.url.path.endswith("getItems")]) == 1:
            return httpx.Response(429)       # primeira chamada: throttling → deve tentar de novo
        return httpx.Response(200, json={"itemsResult": {"items": [ITEM]}})
    s = Settings(_env_file=None, amazon_credential_id="id", amazon_credential_secret="sec",
                 amazon_credential_version=version, amazon_partner_tag="t-20", amazon_rps=1000)
    return CreatorsClient(s, http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_auth_v3_and_retry(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []
    offers = _client("3.1", calls).get_items(["B0TESTE001"])
    assert offers[0].asin == "B0TESTE001"
    tok = calls[0]
    assert str(tok.url) == "https://api.amazon.com/auth/o2/token"
    assert tok.headers["authorization"] == "Basic " + base64.b64encode(b"id:sec").decode()
    assert b"scope=creatorsapi%3A%3Adefault" in tok.content
    api = calls[-1]
    assert api.headers["authorization"] == "Bearer TKN" and api.headers["x-marketplace"] == "www.amazon.com.br"
    body = json.loads(api.content)
    assert body["itemIds"] == ["B0TESTE001"] and body["partnerTag"] == "t-20"
    assert len([c for c in calls if c.url.path.endswith("token")]) == 1   # token em cache


def test_auth_v2_header(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []
    _client("2.1", calls).get_items(["B0TESTE001"])
    assert "amazoncognito.com" in str(calls[0].url) and b"creatorsapi%2Fdefault" in calls[0].content
    assert calls[-1].headers["authorization"] == "Bearer TKN, Version 2.1"


def test_batches_of_10(monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path.endswith("token"):
            return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
        return httpx.Response(200, json={"itemsResult": {"items": []}})
    s = Settings(_env_file=None, amazon_credential_id="i", amazon_credential_secret="s", amazon_rps=1000)
    CreatorsClient(s, http=httpx.Client(transport=httpx.MockTransport(handler))).get_items(
        [f"B0000000{i:02d}" for i in range(23)])
    sizes = [len(json.loads(c.content)["itemIds"]) for c in calls if c.url.path.endswith("getItems")]
    assert sizes == [10, 10, 3]

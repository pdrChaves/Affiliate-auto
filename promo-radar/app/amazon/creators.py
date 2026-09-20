"""Cliente do Amazon Creators API (sucessor do PA-API 5, desligado em 2026).

Autenticação: OAuth2 client_credentials.
  - credencial 3.x (Login with Amazon): Basic auth + scope "creatorsapi::default"; header "Bearer <token>"
  - credencial 2.x (Cognito): Basic auth + scope "creatorsapi/default"; header "Bearer <token>, Version 2.x"
Brasil (www.amazon.com.br) pertence ao grupo North America → versão 3.1 (ou 2.1 em credenciais antigas).
"""
from __future__ import annotations

import base64
import logging
import threading
import time
from typing import Any

import httpx

from ..config import Settings
from ..models import Offer, utcnow
from .base import affiliate_link
from .ratelimit import RateLimiter

log = logging.getLogger(__name__)

RESOURCES = [
    "itemInfo.title",
    "itemInfo.features",
    "images.primary.large",
    "offersV2.listings.price",
    "offersV2.listings.availability",
    "offersV2.listings.condition",
    "offersV2.listings.merchantInfo",
    "offersV2.listings.isBuyBoxWinner",
    "offersV2.listings.dealDetails",
]


class CreatorsAPIError(RuntimeError):
    pass


def _g(d: Any, *path: str) -> Any:
    """Acessa chaves ignorando maiúsc./minúsc. (a API responde lowerCamelCase; o PA-API usava PascalCase)."""
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        lk = key.lower()
        cur = next((v for k, v in cur.items() if k.lower() == lk), None)
    return cur


def _cents(money: Any) -> int | None:
    amount = _g(money, "amount")
    return int(round(float(amount) * 100)) if amount is not None else None


def parse_item(item: dict, marketplace: str, tag: str) -> Offer | None:
    asin = _g(item, "asin")
    title = _g(item, "itemInfo", "title", "displayValue")
    if not asin or not title:
        return None
    listings = _g(item, "offersV2", "listings") or []
    # Preferimos a oferta vencedora da buy box (é a que o cliente vê ao clicar).
    listing = next((l for l in listings if _g(l, "isBuyBoxWinner")), listings[0] if listings else None)
    features = _g(item, "itemInfo", "features", "displayValues") or []
    offer = Offer(
        asin=asin,
        title=title,
        url=affiliate_link(marketplace, asin, tag),
        price_cents=None,
        image_url=_g(item, "images", "primary", "large", "url"),
        features=list(features)[:5],
        fetched_at=utcnow(),
    )
    if listing:
        price = _g(listing, "price") or {}
        offer.price_cents = _cents(_g(price, "money"))
        offer.basis_cents = _cents(_g(price, "savingBasis", "money"))
        offer.basis_type = _g(price, "savingBasis", "savingBasisType")
        offer.savings_pct = _g(price, "savings", "percentage")
        avail = (_g(listing, "availability", "type") or "").upper()
        offer.in_stock = avail in ("", "IN_STOCK", "NOW")
        cond = (_g(listing, "condition", "value") or "New").lower()
        offer.condition_new = cond == "new"
        offer.is_buybox = bool(_g(listing, "isBuyBoxWinner"))
        offer.merchant = _g(listing, "merchantInfo", "name")
        offer.deal_badge = _g(listing, "dealDetails", "badge")
    else:
        offer.in_stock = False
    return offer


class CreatorsClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.s = settings
        self.http = http or httpx.Client(timeout=20)
        self.limiter = RateLimiter(settings.amazon_rps)
        self._token: str | None = None
        self._token_exp = 0.0
        self._tlock = threading.Lock()

    # ---------- auth ----------
    @property
    def _is_v2(self) -> bool:
        return self.s.amazon_credential_version.startswith("2.")

    def _get_token(self) -> str:
        with self._tlock:
            if self._token and time.time() < self._token_exp - 60:
                return self._token
            basic = base64.b64encode(
                f"{self.s.amazon_credential_id}:{self.s.amazon_credential_secret}".encode()).decode()
            r = self.http.post(
                self.s.token_url,
                data={"grant_type": "client_credentials",
                      "scope": "creatorsapi/default" if self._is_v2 else "creatorsapi::default"},
                headers={"Authorization": f"Basic {basic}",
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code != 200:
                raise CreatorsAPIError(f"Falha ao obter token ({r.status_code}): {r.text[:300]}")
            body = r.json()
            self._token = body["access_token"]
            self._token_exp = time.time() + int(body.get("expires_in", 3600))
            return self._token

    def _headers(self) -> dict:
        auth = f"Bearer {self._get_token()}"
        if self._is_v2:
            auth += f", Version {self.s.amazon_credential_version}"
        return {"Authorization": auth, "Content-Type": "application/json",
                "x-marketplace": self.s.amazon_marketplace}

    def _post(self, op: str, payload: dict) -> dict:
        payload = {"partnerTag": self.s.amazon_partner_tag, "marketplace": self.s.amazon_marketplace,
                   "resources": RESOURCES, **payload}
        for attempt in range(4):
            self.limiter.wait()
            r = self.http.post(f"{self.s.amazon_api_base}/{op}", json=payload, headers=self._headers())
            if r.status_code == 429 or r.status_code >= 500:   # throttling / instabilidade → backoff
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 401:                           # token expirado antes da hora
                self._token = None
                continue
            if r.status_code != 200:
                raise CreatorsAPIError(f"{op} {r.status_code}: {r.text[:500]}")
            body = r.json()
            for err in _g(body, "errors") or []:
                log.warning("Creators API %s: %s - %s", op, _g(err, "code"), _g(err, "message"))
            return body
        raise CreatorsAPIError(f"{op}: esgotou tentativas (throttling)")

    # ---------- operações ----------
    def get_items(self, asins: list[str]) -> list[Offer]:
        out: list[Offer] = []
        for i in range(0, len(asins), 10):   # limite da API: 10 ASINs por chamada
            body = self._post("getItems", {"itemIds": asins[i:i + 10], "itemIdType": "ASIN",
                                           "condition": "New", "languagesOfPreference": ["pt_BR"]})
            for it in _g(body, "itemsResult", "items") or []:
                o = parse_item(it, self.s.amazon_marketplace, self.s.amazon_partner_tag)
                if o:
                    out.append(o)
        return out

    def search(self, keywords=None, search_index="All", browse_node_id=None, min_saving_pct=None,
               min_price_cents=None, max_price_cents=None, pages=1) -> list[Offer]:
        out: list[Offer] = []
        for page in range(1, pages + 1):
            payload: dict[str, Any] = {"searchIndex": search_index, "itemCount": 10, "itemPage": page,
                                       "condition": "New", "languagesOfPreference": ["pt_BR"]}
            if keywords:
                payload["keywords"] = keywords
            if browse_node_id:
                payload["browseNodeId"] = browse_node_id
            if min_saving_pct:
                payload["minSavingPercent"] = int(min_saving_pct)
            if min_price_cents:
                payload["minPrice"] = int(min_price_cents)   # menor unidade monetária (centavos)
            if max_price_cents:
                payload["maxPrice"] = int(max_price_cents)
            body = self._post("searchItems", payload)
            items = _g(body, "searchResult", "items") or []
            out += [o for it in items if (o := parse_item(it, self.s.amazon_marketplace, self.s.amazon_partner_tag))]
            if len(items) < 10:
                break
        return out

"""Fonte simulada: gera respostas NO FORMATO do Creators API e passa pelo mesmo parser.
Serve para rodar o sistema inteiro sem credenciais (dev/demonstração/testes)."""
from __future__ import annotations

import random

from ..config import Settings
from .creators import parse_item

# (asin, título, preço "De", preço atual base, índice, flags)
CATALOG = [
    ("B0MOCK0001", "Fone de Ouvido Bluetooth JBL Tune 520BT", 399.00, 219.00, "Electronics", {}),
    ("B0MOCK0002", "Smartwatch Amazfit Bip 6 GPS", 599.00, 429.00, "Electronics", {}),
    ("B0MOCK0003", "Caixa de Som Bluetooth JBL Go 4", 249.90, 229.90, "Electronics", {}),   # desconto baixo
    ("B0MOCK0004", "Fone de Ouvido Bluetooth Xiaomi Redmi Buds 6", 299.00, 169.90, "Electronics", {}),
    ("B0MOCK0005", "Smartwatch Xiaomi Watch S4", 899.99, 559.99, "Electronics", {"stock": False}),
    ("B0MOCK0006", "Caixa de Som Bluetooth Sony SRS-XB100", 449.00, 249.00, "Electronics", {"buybox": False}),
    ("B0MOCK0101", "Headset Gamer HyperX Cloud Stinger 2", 349.99, 212.36, "VideoGames", {}),
    ("B0MOCK0102", "Controle Sem Fio DualSense PS5", 489.99, 316.89, "VideoGames", {}),
    ("B0MOCK0103", "Headset Gamer Logitech G435", 399.99, 359.99, "VideoGames", {}),        # desconto baixo
    ("B0MOCK0104", "Teclado Mecânico Redragon Kumara K552", 229.99, 144.49, "Computers", {"deal": "Oferta Relâmpago"}),
    ("B0MOCK0201", "Air Fryer Mondial 4L AFN-40", 399.00, 249.00, "HomeAndKitchen", {}),
    ("B0MOCK0202", "Robô Aspirador Xiaomi E10", 1499.00, 899.00, "HomeAndKitchen", {}),
]


def _raw(asin, title, basis, price, flags):
    pct = round(100 * (basis - price) / basis)  # a Amazon arredonda o percentual
    return {
        "asin": asin,
        "detailPageURL": f"https://www.amazon.com.br/dp/{asin}",
        "itemInfo": {"title": {"displayValue": title},
                     "features": {"displayValues": ["Produto de exemplo (modo mock)"]}},
        "images": {"primary": {"large": {"url": None}}},
        "offersV2": {"listings": [{
            "availability": {"type": "IN_STOCK" if flags.get("stock", True) else "OUT_OF_STOCK"},
            "condition": {"value": "New"},
            "isBuyBoxWinner": flags.get("buybox", True),
            "merchantInfo": {"name": "Amazon.com.br"},
            "dealDetails": {"badge": flags["deal"]} if "deal" in flags else None,
            "price": {
                "money": {"amount": price, "currency": "BRL"},
                "savingBasis": {"money": {"amount": basis, "currency": "BRL"}, "savingBasisType": "WAS_PRICE"},
                "savings": {"money": {"amount": round(basis - price, 2)}, "percentage": pct},
            },
        }]},
    }


class MockClient:
    def __init__(self, settings: Settings, jitter: float = 0.0, seed: int | None = None):
        self.s = settings
        self.jitter = jitter              # variação aleatória de preço (simula mercado)
        self.rng = random.Random(seed)  # nosec B311 (só simula variação de preço)
        self.overrides: dict[str, float] = {}   # testes podem forçar preço

    def _offers(self, rows):
        out = []
        for asin, title, basis, price, _idx, flags in rows:
            p = self.overrides.get(asin, price)
            if self.jitter:
                p = round(p * (1 + self.rng.uniform(-self.jitter, self.jitter)), 2)
            out.append(parse_item(_raw(asin, title, basis, p, flags), self.s.amazon_marketplace,
                                  self.s.amazon_partner_tag))
        return out

    def get_items(self, asins, fast=False):
        return self._offers([r for r in CATALOG if r[0] in asins])

    def search(self, keywords=None, search_index="All", browse_node_id=None, min_saving_pct=None,
               min_price_cents=None, max_price_cents=None, pages=1):
        words = [w for w in (keywords or "").lower().split() if len(w) > 3]
        rows = [r for r in CATALOG
                if (search_index in ("All", r[4]))
                and (not words or any(w in r[1].lower() for w in words))]
        return self._offers(rows)

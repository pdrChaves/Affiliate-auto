"""Contrato da fonte de catálogo. Qualquer fonte (Creators API, mock, futura) implementa isto."""
from __future__ import annotations

from typing import Protocol

from ..models import Offer


class CatalogClient(Protocol):
    def get_items(self, asins: list[str]) -> list[Offer]:
        """Preço/estado atual de até N ASINs (a implementação faz o lote de 10)."""

    def search(self, keywords: str | None, search_index: str = "All", browse_node_id: str | None = None,
               min_saving_pct: int | None = None, min_price_cents: int | None = None,
               max_price_cents: int | None = None, pages: int = 1) -> list[Offer]:
        """Busca produtos com oferta."""


def affiliate_link(marketplace: str, asin: str, tag: str) -> str:
    """Link de afiliado canônico e transparente (o domínio da Amazon fica visível → sem 'cloaking')."""
    return f"https://{marketplace}/dp/{asin}?tag={tag}"

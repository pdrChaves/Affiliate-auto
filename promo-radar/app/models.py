"""Modelos de domínio. Valores monetários em centavos (int) para evitar erro de float."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Offer:
    """Snapshot de um produto + melhor oferta, como devolvido pela API."""
    asin: str
    title: str
    url: str                         # link de afiliado (com tag)
    price_cents: int | None          # preço atual
    basis_cents: int | None = None   # preço "De" (savingBasis) informado pela Amazon
    basis_type: str | None = None    # LIST_PRICE / WAS_PRICE ...
    savings_pct: int | None = None
    image_url: str | None = None     # só exibido por link (não armazenamos imagem)
    merchant: str | None = None
    in_stock: bool = True
    is_buybox: bool = True
    condition_new: bool = True
    features: list[str] = field(default_factory=list)
    deal_badge: str | None = None    # ex.: "Oferta relâmpago"
    source: str = "api"              # api | manual
    coupon: str | None = None        # cupom digitado por você (a API não fornece cupons)
    fetched_at: datetime = field(default_factory=utcnow)

    @property
    def discount_pct(self) -> float | None:
        if self.savings_pct is not None:
            return float(self.savings_pct)
        if self.price_cents and self.basis_cents and self.basis_cents > self.price_cents:
            return round(100 * (self.basis_cents - self.price_cents) / self.basis_cents, 1)
        return None


class PostStatus(StrEnum):
    PENDING = "pending"        # aguardando revisão
    APPROVED = "approved"      # aprovado, pronto p/ enviar
    SENT = "sent"              # você confirmou o envio
    REJECTED = "rejected"
    EXPIRED = "expired"        # preço mudou / ficou velho antes do envio
    ENDED = "ended"            # já enviado, mas a promoção acabou (apagar no WhatsApp se der)

"""Validação da oferta: decide se vale postar e dá uma nota para ordenar a fila.

Filosofia: melhor perder uma oferta do que postar "promoção" falsa (CDC art. 37 — publicidade enganosa).

Por que NÃO guardamos histórico de preço próprio: a Licença do Creators API só permite guardar
conteúdo de produto (preço, título…) por até 24h; só o ASIN pode ser guardado indefinidamente.
Então a checagem de "De" inflado usa o que a própria Amazon informa (tipo do preço de referência)
+ regras de sanidade, e casos duvidosos vão para revisão humana com aviso.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from ..config import Niche
from ..db import DB
from ..models import Offer, PostStatus, utcnow

SUSPICIOUS_DISCOUNT = 75      # acima disso, quase sempre é "De" inflado → revisar


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    score: float = 0.0
    warnings: list[str] = field(default_factory=list)


def evaluate(offer: Offer, niche: Niche, db: DB, check_repeat: bool = True) -> Verdict:
    if offer.price_cents is None:
        return Verdict(False, "sem_preco")
    if not offer.in_stock:
        return Verdict(False, "fora_de_estoque")
    if not offer.condition_new:
        return Verdict(False, "nao_novo")
    if niche.require_buybox and not offer.is_buybox:
        return Verdict(False, "nao_buybox")
    price = offer.price_cents / 100
    if not (niche.min_price <= price <= niche.max_price):
        return Verdict(False, "faixa_de_preco")
    disc = offer.discount_pct
    if not offer.basis_cents or disc is None:
        return Verdict(False, "sem_preco_de")          # o post exige "De x Por"
    if disc < niche.min_discount_pct:
        return Verdict(False, "desconto_baixo")
    btype = (offer.basis_type or "").upper()
    if btype == "LIST_PRICE" and not niche.accept_list_price:
        return Verdict(False, "de_eh_preco_de_tabela")

    warnings: list[str] = []
    score = disc
    if btype == "LIST_PRICE":
        warnings.append("'De' é preço de tabela/sugerido (não é o preço anterior praticado) — confira")
        score -= 10
    if disc >= SUSPICIOUS_DISCOUNT:
        warnings.append(f"SUSPEITO: desconto de {disc:.0f}% — confira se o 'De' é real")
        score -= 15
    if offer.deal_badge:
        score += 5

    # repetição: mesmo produto no mesmo nicho (ASIN pode ser guardado sem limite de tempo)
    last = check_repeat and db.last_post_for(
        niche.id, offer.asin, [PostStatus.PENDING.value, PostStatus.APPROVED.value, PostStatus.SENT.value,
                               PostStatus.ENDED.value])
    if last:
        if last["status"] in (PostStatus.PENDING.value, PostStatus.APPROVED.value):
            return Verdict(False, "ja_na_fila")
        ref = last["sent_at"] or last["created_at"]
        recent = utcnow() - ref < timedelta(hours=niche.cooldown_hours)
        # preço do último envio só existe enquanto não foi expurgado (janela de monitoramento)
        dropped = bool(last["price_cents"]) and \
            offer.price_cents <= last["price_cents"] * (1 - niche.repost_if_drop_pct / 100)
        if recent and not dropped:
            return Verdict(False, "cooldown")
        if recent and dropped:
            warnings.append("repost: caiu mais desde o último envio")
    return Verdict(True, "", round(score, 1), warnings)

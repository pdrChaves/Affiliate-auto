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

from ..config import Filters
from ..db import DB
from ..models import Offer, PostStatus, utcnow

SUSPICIOUS_DISCOUNT = 75      # acima disso, quase sempre é "De" inflado → revisar


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    score: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _hard_reject(offer: Offer, f: Filters) -> str | None:
    """Regras eliminatórias. Retorna o código do motivo ou None se passou em todas."""
    if offer.price_cents is None:
        return "sem_preco"
    disc = offer.discount_pct
    btype = (offer.basis_type or "").upper()
    rules: list[tuple[bool, str]] = [
        (not offer.in_stock, "fora_de_estoque"),
        (not offer.condition_new, "nao_novo"),
        (f.require_buybox and not offer.is_buybox, "nao_buybox"),
        (not f.min_price <= offer.price_cents / 100 <= f.max_price, "faixa_de_preco"),
        (not offer.basis_cents or disc is None, "sem_preco_de"),          # o post exige "De x Por"
        (disc is not None and disc < f.min_discount_pct, "desconto_baixo"),
        (btype == "LIST_PRICE" and not f.accept_list_price, "de_eh_preco_de_tabela"),
    ]
    return next((reason for failed, reason in rules if failed), None)


def _score(offer: Offer) -> tuple[float, list[str]]:
    disc = offer.discount_pct or 0.0
    score, warnings = disc, []
    if (offer.basis_type or "").upper() == "LIST_PRICE":
        warnings.append("'De' é preço de tabela/sugerido (não é o preço anterior praticado) — confira")
        score -= 10
    if disc >= SUSPICIOUS_DISCOUNT:
        warnings.append(f"SUSPEITO: desconto de {disc:.0f}% — confira se o 'De' é real")
        score -= 15
    if offer.deal_badge:
        score += 5
    return score, warnings


def _repeat_check(offer: Offer, f: Filters, db: DB) -> tuple[str | None, str | None]:
    """(motivo de rejeição, aviso). ASIN pode ser guardado sem limite de tempo, então o histórico de envios vale."""
    last = db.last_post_for(offer.asin, [PostStatus.PENDING.value, PostStatus.APPROVED.value,
                                         PostStatus.SENT.value, PostStatus.ENDED.value])
    if not last:
        return None, None
    if last["status"] in (PostStatus.PENDING.value, PostStatus.APPROVED.value):
        return "ja_na_fila", None
    ref = last["sent_at"] or last["created_at"]
    if utcnow() - ref >= timedelta(hours=f.cooldown_hours):
        return None, None
    # preço do último envio só existe enquanto não foi expurgado (janela de monitoramento)
    prev = last["price_cents"]
    if prev and offer.price_cents is not None and \
            offer.price_cents <= prev * (1 - f.repost_if_drop_pct / 100):
        return None, "repost: caiu mais desde o último envio"
    return "cooldown", None


def evaluate(offer: Offer, f: Filters, db: DB, check_repeat: bool = True) -> Verdict:
    reason = _hard_reject(offer, f)
    if reason:
        return Verdict(False, reason)
    score, warnings = _score(offer)
    if check_repeat:
        reason, warning = _repeat_check(offer, f, db)
        if reason:
            return Verdict(False, reason)
        if warning:
            warnings.append(warning)
    return Verdict(True, "", round(score, 1), warnings)

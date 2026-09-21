"""Monta a mensagem no formato do WhatsApp (*negrito*, ~riscado~)."""
from __future__ import annotations

from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..config import Style
from ..models import Offer

TITLE_MAX = 90


def brl(cents: int) -> str:
    s = f"{cents / 100:,.2f}"                     # 1,234.56
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def truncate_title(title: str, limit: int = TITLE_MAX) -> str:
    """A política só permite TRUNCAR o texto da Amazon, não reescrever. Então cortamos na palavra."""
    title = " ".join(title.split())
    if len(title) <= limit:
        return title
    cut = title[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;-–") + "…"


def render_post(offer: Offer, style: Style, headline: str, tz: str, *, show_prices: bool = True) -> str:
    coupon = offer.coupon
    local = offer.fetched_at.astimezone(ZoneInfo(tz))
    lines = ["#publi · link de afiliado Amazon", "", f"*{headline}*", "", truncate_title(offer.title), ""]
    if show_prices and offer.price_cents is not None:
        if offer.basis_cents and offer.basis_cents > offer.price_cents:
            lines.append(f"~De {brl(offer.basis_cents)}~")
        pct = offer.discount_pct
        lines.append(f"*Por {brl(offer.price_cents)}* {style.emoji_price}"
                     + (f" (-{pct:.0f}%)" if pct else ""))
    else:
        lines.append("💰 Confira o preço atualizado no link")
    if offer.deal_badge:
        lines.append(f"⚡ {offer.deal_badge}")
    if coupon:
        lines += ["", f"🎟️ Cupom: *{coupon}*"]
    lines += ["", "🛒 Compre aqui:", offer.url, ""]
    if offer.merchant:
        lines.append(f"📦 Vendido por {offer.merchant}")
    if show_prices:
        lines.append(f"🕒 Preço verificado em {local:%d/%m} às {local:%H:%M}. "
                     "Preço e disponibilidade podem mudar.")
    return "\n".join(lines).strip()


def whatsapp_share_url(text: str) -> str:
    """Abre o WhatsApp (app ou Web) com o texto pronto; você escolhe a comunidade e aperta enviar."""
    return "https://wa.me/?text=" + quote(text)

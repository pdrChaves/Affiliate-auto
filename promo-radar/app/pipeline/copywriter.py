"""Gera a CHAMADA (headline) do post. O título do produto nunca é reescrito (regra da Amazon).

Com ANTHROPIC_API_KEY: usa IA com instruções rígidas (sem números, sem promessas).
Sem chave ou se a IA falhar/violar as regras: usa as frases do nicho (config).
"""
from __future__ import annotations

import hashlib
import logging
import re

import httpx

from ..config import Niche, Settings
from ..models import Offer

log = logging.getLogger(__name__)

FORBIDDEN = re.compile(r"\d|R\$|%|grátis|gratis|garantid|melhor preço|menor preço|últimas|ultimas|oficial",
                       re.IGNORECASE)
MAX_LEN = 42

PROMPT = """Você escreve a CHAMADA (1 linha) de um post de promoção para a comunidade de WhatsApp "{niche}".
Tom: {tone}.
Produto: {title}
Características (fonte: Amazon): {features}

Regras obrigatórias:
- Responda SÓ com a chamada, em CAIXA ALTA, no máximo {max_len} caracteres, sem aspas.
- Pode terminar com 1 emoji. Sem hashtags.
- NÃO use números, preços, porcentagens, "grátis", "garantido", "menor preço", "últimas unidades", "oficial".
- NÃO invente características que não estejam acima. Nada de urgência falsa.
Exemplos de estilo: "PRA TREINAR NO CONFORTO", "UMA DAS TRADICIONAIS DA F1 EM LEGO 🏎️"."""


def fallback_headline(offer: Offer, niche: Niche, avoid: list[str] | None = None) -> str:
    """Escolhe a frase do nicho menos usada recentemente (evita chamadas repetidas em sequência)."""
    options = niche.style.headline_fallbacks or ["OFERTA DO DIA"]
    avoid = avoid or []
    start = int(hashlib.md5(offer.asin.encode(), usedforsecurity=False).hexdigest(), 16) % len(options)
    ordered = options[start:] + options[:start]
    return min(ordered, key=lambda h: avoid.count(h))


def validate_headline(h: str) -> str | None:
    h = h.strip().strip('"').strip("'").splitlines()[0].strip() if h.strip() else ""
    h = h.upper()
    if not h or len(h) > MAX_LEN or FORBIDDEN.search(h) or "#" in h:
        return None
    return h


class Copywriter:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.s = settings
        self.http = http or httpx.Client(timeout=20)

    def headline(self, offer: Offer, niche: Niche, avoid: list[str] | None = None) -> str:
        if not self.s.anthropic_api_key:
            return fallback_headline(offer, niche, avoid)
        try:
            r = self.http.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": self.s.anthropic_api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": self.s.anthropic_model, "max_tokens": 60,
                      "messages": [{"role": "user", "content": PROMPT.format(
                          niche=niche.name, tone=niche.style.tone, title=offer.title,
                          features="; ".join(offer.features) or "(não informado)", max_len=MAX_LEN)}]},
            )
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
            h = validate_headline(text)
            if h:
                return h
            log.info("Headline da IA rejeitada pelas regras: %r", text)
        except Exception as e:  # IA é opcional: nunca derruba o pipeline
            log.warning("Copywriter IA falhou (%s); usando fallback", e)
        return fallback_headline(offer, niche, avoid)

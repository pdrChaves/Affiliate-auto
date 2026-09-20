"""Orquestração: coleta → validação → texto → fila → (envio manual) → monitoramento pós-envio."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import Niche, Settings
from .db import DB
from .models import Offer, PostStatus, utcnow
from .pipeline.copywriter import Copywriter
from .pipeline.render import render_post, whatsapp_share_url
from .pipeline.scorer import evaluate

log = logging.getLogger(__name__)

QUEUE_TTL_HOURS = 24          # conteúdo da API não pode ficar guardado > 24h sem atualizar
MONITOR_HOURS = 48            # acompanha posts enviados por ~2 dias (janela de "apagar para todos")


class PromoService:
    def __init__(self, settings: Settings, niches: list[Niche], db: DB, client, copywriter: Copywriter, notifier):
        self.s, self.db, self.client = settings, db, client
        self.niches = {n.id: n for n in niches}
        self.copy, self.notifier = copywriter, notifier

    # ---------- utilidades ----------
    def niche(self, niche_id: str) -> Niche:
        return self.niches[niche_id]

    def in_window(self, niche: Niche, now: datetime | None = None) -> bool:
        now = (now or utcnow()).astimezone(ZoneInfo(self.s.timezone)).time()
        start, end = (time.fromisoformat(t) for t in niche.posting_window)
        return start <= now <= end if start <= end else (now >= start or now <= end)

    def show_prices(self, offer: Offer) -> bool:
        return offer.source == "api" or self.s.allow_manual_prices

    def _render(self, offer: Offer, niche: Niche, headline: str) -> str:
        return render_post(offer, niche, headline, self.s.timezone, show_prices=self.show_prices(offer))

    # ---------- coleta ----------
    def collect(self, niche_id: str) -> dict:
        niche = self.niche(niche_id)
        rejected: Counter = Counter()
        offers: dict[str, Offer] = {}
        try:
            for spec in niche.searches:
                for o in self.client.search(
                        keywords=spec.keywords, search_index=spec.search_index,
                        browse_node_id=spec.browse_node_id, min_saving_pct=int(niche.min_discount_pct),
                        min_price_cents=int(niche.min_price * 100), max_price_cents=int(niche.max_price * 100)):
                    offers[o.asin] = o
            watch = sorted(set(niche.watchlist) | set(self.db.watchlist(niche.id)))
            if watch:
                for o in self.client.get_items(watch):
                    offers[o.asin] = o
        except Exception as e:
            log.exception("Coleta falhou (%s)", niche_id)
            self.db.log_run(niche.id, len(offers), 0, dict(rejected), error=str(e))
            return {"niche": niche_id, "fetched": len(offers), "queued": 0, "error": str(e)}

        candidates = []
        for o in offers.values():
            v = evaluate(o, niche, self.db)
            if v.ok:
                candidates.append((v, o))
            else:
                rejected[v.reason] += 1
        candidates.sort(key=lambda x: x[0].score, reverse=True)
        queued = 0
        used = [p["headline"] for p in self.db.list_posts(niche_id=niche.id, limit=20) if p["headline"]]
        for v, o in candidates[: niche.max_posts_per_run]:
            headline = self.copy.headline(o, niche, avoid=used)
            used.append(headline)
            pid = self.db.create_post(niche.id, o, headline, self._render(o, niche, headline), v.score)
            if v.warnings:
                self.db.update_post(pid, note=" | ".join(v.warnings))
            queued += 1
        rejected["excedeu_limite_por_rodada"] += max(0, len(candidates) - niche.max_posts_per_run)
        rejected = +rejected
        self.db.log_run(niche.id, len(offers), queued, dict(rejected))
        if queued and self.in_window(niche):
            self.notifier.notify(f"🆕 {queued} promo(s) nova(s) na fila de '{niche.name}'. Abra o painel para revisar.")
        return {"niche": niche_id, "fetched": len(offers), "queued": queued, "rejected": dict(rejected)}

    def collect_all(self) -> list[dict]:
        return [self.collect(n.id) for n in self.niches.values() if n.enabled]

    # ---------- entrada manual ----------
    def add_asin(self, niche_id: str, asin: str) -> dict:
        """Adiciona ASIN à watchlist e já tenta enfileirar."""
        self.db.add_watch(niche_id, asin)
        return self.collect(niche_id)

    def add_manual(self, niche_id: str, asin: str, title: str, url: str, basis: float | None, price: float | None,
                   coupon: str | None = None) -> int:
        """Para quando você ainda NÃO tem Creators API (menos de 10 vendas/30 dias)."""
        niche = self.niche(niche_id)
        o = Offer(asin=asin.strip().upper(), title=title.strip(), url=url.strip(),
                  price_cents=int(round(price * 100)) if price else None,
                  basis_cents=int(round(basis * 100)) if basis else None, source="manual",
                  coupon=(coupon or "").strip() or None)
        headline = self.copy.headline(o, niche)
        pid = self.db.create_post(niche.id, o, headline, self._render(o, niche, headline), 0)
        note = "manual" + ("" if self.show_prices(o) else " — preços ocultos (ALLOW_MANUAL_PRICES=false)")
        self.db.update_post(pid, note=note)
        return pid

    # ---------- ciclo do post ----------
    def refresh(self, post_id: int) -> dict:
        """Busca o preço de novo. Se a oferta morreu, expira; se só mudou, reescreve o texto."""
        post = self.db.get_post(post_id)
        if post["offer"].source != "api":
            return post
        niche = self.niche(post["niche_id"])
        fresh = self.client.get_items([post["asin"]])
        if not fresh:
            self.db.update_post(post_id, status=PostStatus.EXPIRED, note="produto indisponível na API")
            return self.db.get_post(post_id)
        o = fresh[0]
        v = evaluate(o, niche, self.db, check_repeat=False)
        if not v.ok:
            self.db.update_post(post_id, status=PostStatus.EXPIRED, offer=o, price_checked_at=o.fetched_at,
                                note=f"expirou na revalidação: {v.reason}")
        else:
            o.coupon = post["offer"].coupon
            self.db.update_post(post_id, offer=o, price_cents=o.price_cents, basis_cents=o.basis_cents,
                                discount_pct=o.discount_pct, price_checked_at=o.fetched_at,
                                text=self._render(o, niche, post["headline"]))
        return self.db.get_post(post_id)

    def edit_headline(self, post_id: int, headline: str) -> None:
        post = self.db.get_post(post_id)
        headline = " ".join(headline.split()).upper()[:60]
        self.db.update_post(post_id, headline=headline,
                            text=self._render(post["offer"], self.niche(post["niche_id"]), headline))

    def set_coupon(self, post_id: int, coupon: str) -> None:
        post = self.db.get_post(post_id)
        o = post["offer"]
        o.coupon = coupon.strip() or None
        self.db.update_post(post_id, offer=o, text=self._render(o, self.niche(post["niche_id"]), post["headline"]))

    def approve(self, post_id: int) -> None:
        self.db.update_post(post_id, status=PostStatus.APPROVED, approved_at=utcnow())

    def reject(self, post_id: int) -> None:
        self.db.update_post(post_id, status=PostStatus.REJECTED)

    def is_stale(self, post: dict) -> bool:
        return utcnow() - post["price_checked_at"] > timedelta(minutes=self.s.max_price_age_minutes)

    def prepare_send(self, post_id: int) -> dict:
        """Chamado no clique 'Enviar': garante preço fresco antes de gerar o link do WhatsApp."""
        post = self.db.get_post(post_id)
        if post["offer"].source == "api" and self.is_stale(post):
            post = self.refresh(post_id)
        ok = post["status"] in (PostStatus.PENDING.value, PostStatus.APPROVED.value)
        return {"post": post, "ok": ok, "share_url": whatsapp_share_url(post["text"]) if ok else None}

    def mark_sent(self, post_id: int) -> None:
        self.db.update_post(post_id, status=PostStatus.SENT, sent_at=utcnow())

    # ---------- rotinas ----------
    def expire_stale_queue(self) -> int:
        n = 0
        limit = utcnow() - timedelta(hours=QUEUE_TTL_HOURS)
        for p in self.db.list_posts([PostStatus.PENDING.value, PostStatus.APPROVED.value], limit=1000):
            if p["created_at"] < limit:
                self.db.update_post(p["id"], status=PostStatus.EXPIRED, note="ficou >24h na fila")
                n += 1
        return n

    def purge_old_content(self) -> int:
        """Licença do Creators API: conteúdo de produto (preço, título, imagem) só pode ficar guardado 24h.
        Posts não enviados: expurgo após 24h. Enviados: após a janela de monitoramento (o preço enviado é o
        registro do que VOCÊ publicou e é necessário para detectar o fim da promoção)."""
        now = utcnow()
        n = self.db.purge_product_content(now - timedelta(hours=self.s.content_retention_hours),
                                          [PostStatus.EXPIRED.value, PostStatus.REJECTED.value])
        n += self.db.purge_product_content(now - timedelta(hours=MONITOR_HOURS + 1),
                                           [PostStatus.SENT.value, PostStatus.ENDED.value])
        return n

    def monitor_sent(self) -> list[int]:
        """Política: remover o link quando a promoção acabar. No WhatsApp só dá para 'apagar para todos'
        por ~2 dias, então vigiamos esse período e avisamos você."""
        since = utcnow() - timedelta(hours=MONITOR_HOURS)
        posts = [p for p in self.db.list_posts([PostStatus.SENT.value], limit=1000)
                 if p["sent_at"] and p["sent_at"] >= since and p["offer"].source == "api"]
        if not posts:
            return []
        fresh = {o.asin: o for o in self.client.get_items(sorted({p["asin"] for p in posts}))}
        ended = []
        for p in posts:
            o = fresh.get(p["asin"])
            reason = None
            if not o or not o.in_stock or o.price_cents is None:
                reason = "indisponível"
            elif o.price_cents > p["price_cents"]:
                reason = f"preço subiu para R$ {o.price_cents / 100:.2f}"
            if o:   # mantém o conteúdo guardado sempre atualizado (exigência da Licença)
                o.coupon = p["offer"].coupon
                self.db.update_post(p["id"], offer=o)
            if reason:
                self.db.update_post(p["id"], status=PostStatus.ENDED, ended_at=utcnow(),
                                    note=f"promo encerrada: {reason}")
                self.notifier.notify(f"⚠️ Promo encerrada ({reason}) — {p['offer'].title[:60]}. "
                                     f"Se ainda der, apague a mensagem na comunidade.")
                ended.append(p["id"])
        return ended

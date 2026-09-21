"""Orquestração: coleta → validação → texto → fila → (envio manual) → monitoramento pós-envio."""
from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .amazon.base import affiliate_link
from .config import Niche, Settings
from .db import DB, DuplicateActivePostError
from .models import Offer, PostStatus, utcnow
from .pipeline.copywriter import Copywriter
from .pipeline.render import render_post, whatsapp_share_url
from .pipeline.scorer import Verdict, evaluate

log = logging.getLogger(__name__)

QUEUE_TTL_HOURS = 24          # conteúdo da API não pode ficar guardado > 24h sem atualizar
MONITOR_HOURS = 48            # acompanha posts enviados por ~2 dias (janela de "apagar para todos")
KEEP_POSTS_DAYS = 90          # posts já expurgados (só ASIN) são apagados depois disso
KEEP_RUNS_DAYS = 30
ASIN_RE = re.compile(r"[A-Z0-9]{10}")
ACTIVE = (PostStatus.PENDING.value, PostStatus.APPROVED.value)


class NotFoundError(Exception):
    pass


class InvalidInputError(ValueError):
    pass


class InvalidStateError(Exception):
    pass


class CatalogUnavailableError(Exception):
    """A API da Amazon não respondeu (fora do ar, throttling, timeout)."""


class PromoService:
    def __init__(self, settings: Settings, niches: list[Niche], db: DB, client: Any, copywriter: Copywriter,
                 notifier: Any):
        self.s, self.db, self.client = settings, db, client
        self.niches = {n.id: n for n in niches}
        self.copy, self.notifier = copywriter, notifier
        self._collect_locks = {n.id: threading.Lock() for n in niches}

    # ---------- utilidades ----------
    def niche(self, niche_id: str) -> Niche:
        try:
            return self.niches[niche_id]
        except KeyError:
            raise InvalidInputError(f"nicho desconhecido: {niche_id!r}") from None

    def post(self, post_id: int) -> dict:
        p = self.db.get_post(post_id)
        if p is None:
            raise NotFoundError(f"post {post_id} não existe")
        return p

    def in_window(self, niche: Niche, now: datetime | None = None) -> bool:
        local = (now or utcnow()).astimezone(ZoneInfo(self.s.timezone)).time()
        start, end = (time.fromisoformat(t) for t in niche.posting_window)
        return start <= local <= end if start <= end else (local >= start or local <= end)

    def show_prices(self, offer: Offer) -> bool:
        return offer.source == "api" or self.s.allow_manual_prices

    def _render(self, offer: Offer, niche: Niche, headline: str) -> str:
        return render_post(offer, niche, headline, self.s.timezone, show_prices=self.show_prices(offer))

    def _fetch(self, asins: list[str], fast: bool = False) -> list[Offer]:
        try:
            return self.client.get_items(asins, fast=fast)
        except Exception as e:
            log.warning("Catálogo indisponível: %s", e)
            raise CatalogUnavailableError(str(e)) from e

    # ---------- coleta ----------
    def collect(self, niche_id: str) -> dict:
        niche = self.niche(niche_id)
        lock = self._collect_locks[niche.id]
        if not lock.acquire(blocking=False):      # evita coletas simultâneas do mesmo nicho
            return {"niche": niche_id, "skipped": "coleta já em andamento"}
        try:
            return self._collect(niche)
        finally:
            lock.release()

    def _gather(self, niche: Niche) -> dict[str, Offer]:
        """Busca por categoria da Amazon. Cada oferta guarda em qual categoria foi encontrada."""
        offers: dict[str, Offer] = {}
        for spec in niche.searches:
            for o in self.client.search(
                    keywords=spec.keywords, search_index=spec.search_index,
                    browse_node_id=spec.browse_node_id, min_saving_pct=int(niche.min_discount_pct),
                    min_price_cents=int(niche.min_price * 100), max_price_cents=int(niche.max_price * 100)):
                if o.asin in offers:
                    continue                       # já achado por uma busca anterior: mantém a categoria dela
                o.category = spec.search_index
                offers[o.asin] = o
        watch = sorted(set(niche.watchlist) | set(self.db.watchlist(niche.id)))
        if watch:
            for o in self.client.get_items(watch):
                o.category = offers[o.asin].category if o.asin in offers else None
                offers[o.asin] = o
        return offers

    def _collect(self, niche: Niche) -> dict:
        try:
            offers = self._gather(niche)
        except Exception as e:
            log.exception("Coleta falhou (%s)", niche.id)
            self.db.log_run(niche.id, 0, 0, {}, error=str(e)[:500])
            return {"niche": niche.id, "fetched": 0, "queued": 0, "error": str(e)}

        rejected: Counter = Counter()
        candidates: list[tuple[Verdict, Offer]] = []
        for o in offers.values():
            v = evaluate(o, niche, self.db)
            if v.ok:
                candidates.append((v, o))
            else:
                rejected[v.reason] += 1
        candidates.sort(key=lambda x: x[0].score, reverse=True)
        queued = self._enqueue(niche, candidates[: niche.max_posts_per_run], rejected)
        rejected["excedeu_limite_por_rodada"] += max(0, len(candidates) - niche.max_posts_per_run)
        rejected = +rejected
        self.db.log_run(niche.id, len(offers), queued, dict(rejected))
        if queued and self.in_window(niche):
            self.notifier.notify(f"🆕 {queued} promo(s) nova(s) na fila de '{niche.name}'. Abra o painel para revisar.")
        return {"niche": niche.id, "fetched": len(offers), "queued": queued, "rejected": dict(rejected)}

    def _enqueue(self, niche: Niche, chosen: list[tuple[Verdict, Offer]], rejected: Counter) -> int:
        queued = 0
        used = [p["headline"] for p in self.db.list_posts(niche_id=niche.id, limit=20) if p["headline"]]
        for v, o in chosen:
            headline = self.copy.headline(o, niche, avoid=used)
            try:
                pid = self.db.create_post(niche.id, o, headline, self._render(o, niche, headline), v.score)
            except DuplicateActivePostError:            # outra coleta (outro processo) chegou antes
                rejected["ja_na_fila"] += 1
                continue
            used.append(headline)
            if v.warnings:
                self.db.update_post(pid, note=" | ".join(v.warnings))
            queued += 1
        return queued

    def expire_orphan_posts(self) -> int:
        """Nichos renomeados ou removidos do niches.yaml deixam posts órfãos na fila: expira e avisa no log."""
        n = self.db.expire_orphan_posts(list(self.niches))
        if n:
            log.info("%s post(s) de nichos que não existem mais foram expirados", n)
        return n

    def collect_all(self) -> list[dict]:
        return [self.collect(n.id) for n in self.niches.values() if n.enabled]

    # ---------- entrada manual ----------
    @staticmethod
    def clean_asin(asin: str) -> str:
        asin = asin.strip().upper()
        if not ASIN_RE.fullmatch(asin):
            raise InvalidInputError("ASIN inválido (10 letras/números, ex.: B0ABCDE123)")
        return asin

    def add_asin(self, niche_id: str, asin: str) -> dict:
        """Adiciona ASIN à watchlist e já tenta enfileirar."""
        niche = self.niche(niche_id)
        self.db.add_watch(niche.id, self.clean_asin(asin))
        return self.collect(niche.id)

    def _validate_manual(self, asin: str, title: str, basis: float | None, price: float | None) -> tuple[str, str]:
        asin = self.clean_asin(asin)
        title = " ".join(title.split())
        if not title:
            raise InvalidInputError("título obrigatório")
        if any(v is not None and not 0 < v < 1_000_000 for v in (basis, price)):
            raise InvalidInputError("preço fora do intervalo")
        return asin, title

    def add_manual(self, niche_id: str, asin: str, title: str, basis: float | None, price: float | None,
                   coupon: str | None = None) -> int:
        """Para quando você ainda NÃO tem Creators API (menos de 10 vendas/30 dias).
        O link é SEMPRE gerado pelo sistema (amazon.com.br/dp/ASIN?tag=SUA_TAG): não há campo de URL livre,
        então não dá para colocar um link de terceiros (phishing) num post."""
        niche = self.niche(niche_id)
        asin, title = self._validate_manual(asin, title, basis, price)
        link = affiliate_link(self.s.amazon_marketplace, asin, self.s.amazon_partner_tag)
        o = Offer(asin=asin, title=title, url=link,
                  price_cents=int(round(price * 100)) if price else None,
                  basis_cents=int(round(basis * 100)) if basis else None, source="manual",
                  coupon=(coupon or "").strip() or None)
        headline = self.copy.headline(o, niche)
        try:
            pid = self.db.create_post(niche.id, o, headline, self._render(o, niche, headline), 0)
        except DuplicateActivePostError:
            raise InvalidInputError("esse produto já está na fila deste nicho") from None
        note = "manual" + ("" if self.show_prices(o) else " — preços ocultos (ALLOW_MANUAL_PRICES=false)")
        self.db.update_post(pid, note=note)
        return pid

    # ---------- ciclo do post ----------
    def _require_active(self, post: dict) -> None:
        if post["status"] not in ACTIVE:
            raise InvalidStateError(f"post #{post['id']} está '{post['status']}' e não pode mais ser alterado")

    def refresh(self, post_id: int, fast: bool = True) -> dict:
        """Busca o preço de novo. Se a oferta morreu, expira; se só mudou, reescreve o texto.
        Levanta CatalogUnavailableError se a API não responder (nada é alterado nesse caso)."""
        post = self.post(post_id)
        self._require_active(post)
        if post["offer"].source != "api":
            return post
        niche = self.niche(post["niche_id"])
        fresh = self._fetch([post["asin"]], fast=fast)
        if not fresh:
            self.db.update_post(post_id, status=PostStatus.EXPIRED, note="produto indisponível na API")
            return self.post(post_id)
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
        return self.post(post_id)

    def edit_headline(self, post_id: int, headline: str) -> None:
        post = self.post(post_id)
        self._require_active(post)
        headline = " ".join(headline.split()).upper()[:60]
        if not headline:
            raise InvalidInputError("chamada vazia")
        self.db.update_post(post_id, headline=headline,
                            text=self._render(post["offer"], self.niche(post["niche_id"]), headline))

    def set_coupon(self, post_id: int, coupon: str) -> None:
        post = self.post(post_id)
        self._require_active(post)
        o = post["offer"]
        o.coupon = " ".join(coupon.split()) or None
        self.db.update_post(post_id, offer=o, text=self._render(o, self.niche(post["niche_id"]), post["headline"]))

    def approve(self, post_id: int) -> None:
        self._require_active(self.post(post_id))
        self.db.update_post(post_id, status=PostStatus.APPROVED, approved_at=utcnow())

    def reject(self, post_id: int) -> None:
        self._require_active(self.post(post_id))
        self.db.update_post(post_id, status=PostStatus.REJECTED)

    def price_age(self, post: dict) -> timedelta:
        return utcnow() - post["price_checked_at"]

    def is_stale(self, post: dict) -> bool:
        """Com MAX_PRICE_AGE_MINUTES=0 (padrão), todo envio revalida o preço na hora."""
        return self.price_age(post) > timedelta(minutes=self.s.max_price_age_minutes)

    def prepare_send(self, post_id: int) -> dict:
        """Chamado no clique 'Enviar': tenta garantir preço fresco antes de gerar o link do WhatsApp.

        Se a API estiver fora, NÃO quebra a tela: mostra aviso e libera o envio com o preço antigo
        (o post já traz o carimbo "Preço verificado em ..."), desde que ele tenha menos de 24h (Licença)."""
        post = self.post(post_id)
        warning = None
        if post["offer"].source == "api" and self.is_stale(post) and post["status"] in ACTIVE:
            try:
                post = self.refresh(post_id, fast=True)
            except CatalogUnavailableError:
                if self.price_age(post) >= timedelta(hours=self.s.content_retention_hours):
                    return {"post": post, "ok": False, "share_url": None,
                            "reason": "API da Amazon indisponível e o preço tem mais de 24h. Tente mais tarde."}
                warning = ("Não foi possível revalidar o preço agora (API da Amazon indisponível). "
                           "O post vai com o preço e o horário da última checagem.")
        ok = post["status"] in ACTIVE
        return {"post": post, "ok": ok, "warning": warning,
                "reason": None if ok else (post.get("note") or post["status"]),
                "share_url": whatsapp_share_url(post["text"]) if ok else None}

    def mark_sent(self, post_id: int) -> None:
        self._require_active(self.post(post_id))
        self.db.update_post(post_id, status=PostStatus.SENT, sent_at=utcnow())

    # ---------- rotinas ----------
    def expire_stale_queue(self) -> int:
        n = 0
        limit = utcnow() - timedelta(hours=QUEUE_TTL_HOURS)
        for p in self.db.list_posts(list(ACTIVE), limit=5000):
            if p["created_at"] < limit:
                self.db.update_post(p["id"], status=PostStatus.EXPIRED, note="ficou >24h na fila")
                n += 1
        return n

    def purge_old_content(self) -> int:
        """Licença do Creators API: conteúdo de produto (preço, título, imagem) só pode ficar guardado 24h.
        Posts não enviados: expurgo após 24h. Enviados: após a janela de monitoramento (o preço enviado é o
        registro do que VOCÊ publicou e é necessário para detectar o fim da promoção)."""
        now = utcnow()
        keep = self.s.content_retention_hours
        n = self.db.purge_product_content(now - timedelta(hours=keep),
                                          [PostStatus.EXPIRED.value, PostStatus.REJECTED.value])
        # com o monitoramento ligado, o preço enviado é necessário durante a janela de acompanhamento
        sent_keep = MONITOR_HOURS + 1 if self.s.monitor_sent_enabled else keep
        n += self.db.purge_product_content(now - timedelta(hours=sent_keep),
                                           [PostStatus.SENT.value, PostStatus.ENDED.value])
        return n

    def housekeeping(self) -> dict:
        """Diário: apaga posts expurgados antigos e o log de coletas velho. Semanal (domingo): VACUUM."""
        now = utcnow()
        posts, runs = self.db.delete_old(now - timedelta(days=KEEP_POSTS_DAYS), now - timedelta(days=KEEP_RUNS_DAYS))
        if now.astimezone(ZoneInfo(self.s.timezone)).weekday() == 6:
            self.db.vacuum()
        return {"posts_apagados": posts, "coletas_apagadas": runs}

    @staticmethod
    def _ended_reason(post: dict, fresh: Offer | None) -> str | None:
        if not fresh or not fresh.in_stock or fresh.price_cents is None:
            return "indisponível"
        if post["price_cents"] is not None and fresh.price_cents > post["price_cents"]:
            return f"preço subiu para R$ {fresh.price_cents / 100:.2f}"
        return None

    def monitor_sent(self) -> list[int]:
        """Opcional (MONITOR_SENT_ENABLED). Acompanha por 48h o que já foi enviado e avisa quando a
        promoção acaba, para você apagar a mensagem enquanto o WhatsApp ainda permite (~2 dias)."""
        if not self.s.monitor_sent_enabled:
            return []
        since = utcnow() - timedelta(hours=MONITOR_HOURS)
        posts = [p for p in self.db.list_posts([PostStatus.SENT.value], limit=1000)
                 if p["sent_at"] and p["sent_at"] >= since and p["offer"].source == "api"]
        if not posts:
            return []
        try:
            fresh = {o.asin: o for o in self._fetch(sorted({p["asin"] for p in posts}))}
        except CatalogUnavailableError:
            return []                               # tenta de novo na próxima hora
        ended = []
        for p in posts:
            o = fresh.get(p["asin"])
            if o:   # mantém o conteúdo guardado sempre atualizado (exigência da Licença)
                o.coupon = p["offer"].coupon
                self.db.update_post(p["id"], offer=o)
            reason = self._ended_reason(p, o)
            if reason:
                self.db.update_post(p["id"], status=PostStatus.ENDED, ended_at=utcnow(),
                                    note=f"promo encerrada: {reason}")
                self.notifier.notify(f"⚠️ Promo encerrada ({reason}) — {p['offer'].title[:60]}. "
                                     f"Se ainda der, apague a mensagem na comunidade.")
                ended.append(p["id"])
        return ended

    def health(self, scheduler_running: bool | None) -> dict:
        """ok=False se o banco não lê/escreve ou se o agendador deveria estar rodando e não está."""
        checks: dict[str, bool] = {}
        try:
            self.db.check()
            checks["db"] = True
        except Exception:
            log.exception("health: banco com problema")
            checks["db"] = False
        if scheduler_running is not None:
            checks["scheduler"] = scheduler_running
        return {"ok": all(checks.values()), "checks": checks}

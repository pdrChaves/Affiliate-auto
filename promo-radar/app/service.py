"""Orquestração: busca → validação → texto → fila → (envio manual).

Não existe "nicho": você pesquisa um termo (ex.: "teclado", "mochila"), opcionalmente dentro de uma
categoria da Amazon, e escolhe o que vai para a fila. Buscas salvas rodam sozinhas de tempos em tempos.
"""
from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .amazon.base import affiliate_link
from .categories import search_index
from .config import AppConfig, Settings, clean_category
from .db import DB, DuplicateActivePostError
from .models import Offer, PostStatus, utcnow
from .pipeline.copywriter import Copywriter
from .pipeline.render import render_post, whatsapp_share_url
from .pipeline.scorer import evaluate

log = logging.getLogger(__name__)

QUEUE_TTL_HOURS = 24          # conteúdo da API não pode ficar guardado > 24h sem atualizar
MONITOR_HOURS = 48            # janela do monitoramento pós-envio (opcional)
KEEP_POSTS_DAYS = 90
KEEP_RUNS_DAYS = 30
ASIN_RE = re.compile(r"[A-Z0-9]{10}")
ACTIVE = (PostStatus.PENDING.value, PostStatus.APPROVED.value)
MAX_TERM = 80


class NotFoundError(Exception):
    pass


class InvalidInputError(ValueError):
    pass


class InvalidStateError(Exception):
    pass


class CatalogUnavailableError(Exception):
    """A API da Amazon não respondeu (fora do ar, throttling, timeout)."""


class PromoService:
    def __init__(self, settings: Settings, config: AppConfig, db: DB, client: Any, copywriter: Copywriter,
                 notifier: Any):
        self.s, self.cfg, self.db, self.client = settings, config, db, client
        self.copy, self.notifier = copywriter, notifier
        self._search_lock = threading.Lock()

    # ---------- utilidades ----------
    @property
    def filters(self):  # noqa: ANN201
        return self.cfg.filters

    @property
    def style(self):  # noqa: ANN201
        return self.cfg.style

    def post(self, post_id: int) -> dict:
        p = self.db.get_post(post_id)
        if p is None:
            raise NotFoundError(f"post {post_id} não existe")
        return p

    def in_window(self, now: datetime | None = None) -> bool:
        local = (now or utcnow()).astimezone(ZoneInfo(self.s.timezone)).time()
        start, end = (time.fromisoformat(t) for t in self.filters.posting_window)
        return start <= local <= end if start <= end else (local >= start or local <= end)

    def show_prices(self, offer: Offer) -> bool:
        return offer.source == "api" or self.s.allow_manual_prices

    def _render(self, offer: Offer, headline: str) -> str:
        return render_post(offer, self.style, headline, self.s.timezone, show_prices=self.show_prices(offer))

    def _fetch(self, asins: list[str], fast: bool = False) -> list[Offer]:
        try:
            return self.client.get_items(asins, fast=fast)
        except Exception as e:
            log.warning("Catálogo indisponível: %s", e)
            raise CatalogUnavailableError(str(e)) from e

    @staticmethod
    def clean_asin(asin: str) -> str:
        asin = asin.strip().upper()
        if not ASIN_RE.fullmatch(asin):
            raise InvalidInputError("ASIN inválido (10 letras/números, ex.: B0ABCDE123)")
        return asin

    def clean_term(self, term: str) -> str:
        term = " ".join(term.split())[:MAX_TERM]
        if len(term) < 2:
            raise InvalidInputError("digite ao menos 2 letras para pesquisar")
        return term

    def clean_category(self, category: str | None) -> str:
        try:
            return clean_category(category, self.s.amazon_marketplace)
        except ValueError as e:
            raise InvalidInputError(str(e)) from None

    # ---------- busca ----------
    def search(self, term: str, category: str | None = None, pages: int = 1) -> list[dict]:
        """Pesquisa na Amazon e devolve os resultados JÁ avaliados, sem gravar nada.

        Cada item vem com o veredito das regras (`ok`, motivo, nota, avisos) e com o texto que o post
        teria, para você decidir o que entra na fila."""
        term = self.clean_term(term)
        dep = self.clean_category(category)
        f = self.filters
        # O departamento escolhido nem sempre existe como searchIndex na API (Pet Shop, Roupas,
        # Brinquedos...). Nesses casos a busca vai em "All" e o resultado é filtrado abaixo pelo
        # departamento que a própria Amazon atribuiu ao produto.
        indice = search_index(dep, self.s.amazon_marketplace) if dep else "All"
        try:
            offers = self.client.search(
                keywords=term, search_index=indice, browse_node_id=None,
                min_saving_pct=None, min_price_cents=int(f.min_price * 100),
                max_price_cents=int(f.max_price * 100), pages=pages)
        except Exception as e:
            log.warning("Busca falhou (%s): %s", term, e)
            raise CatalogUnavailableError(str(e)) from e
        resultados = []
        for o in offers:
            # A categoria é a do PRODUTO (veio de browseNodeInfo), nunca a do filtro escolhido.
            if dep and o.category != dep:
                continue
            v = evaluate(o, f, self.db)
            resultados.append({"offer": o, "ok": v.ok, "reason": v.reason, "score": v.score,
                               "warnings": v.warnings, "preview": self._render(o, self.copy.headline(o, self.style))})
        resultados.sort(key=lambda r: (not r["ok"], -r["score"]))
        return resultados

    def queue_offer(self, offer: Offer, query: str = "", warnings: list[str] | None = None,
                    score: float = 0, avoid: list[str] | None = None) -> int:
        headline = self.copy.headline(offer, self.style, avoid=avoid)
        try:
            pid = self.db.create_post(offer, headline, self._render(offer, headline), score, query)
        except DuplicateActivePostError:
            raise InvalidInputError("esse produto já está na fila") from None
        if warnings:
            self.db.update_post(pid, note=" | ".join(warnings))
        return pid

    def queue_asin(self, asin: str, query: str = "", category: str | None = None) -> int:
        """Coloca na fila um produto específico (resultado da busca, watchlist ou colado por você)."""
        asin = self.clean_asin(asin)
        fresh = self._fetch([asin], fast=True)
        if not fresh:
            raise InvalidInputError("produto não encontrado na Amazon")
        o = fresh[0]
        if category and not o.category:      # só preenche quando a API não disse o departamento
            o.category = self.clean_category(category)
        v = evaluate(o, self.filters, self.db)
        if not v.ok and v.reason == "ja_na_fila":
            raise InvalidInputError("esse produto já está na fila")
        avisos = list(v.warnings)
        if not v.ok:
            avisos.insert(0, f"fora das regras ({v.reason}) — você adicionou mesmo assim")
        return self.queue_offer(o, query=query, warnings=avisos, score=v.score)

    def run_search(self, term: str, category: str | None = None, limit: int | None = None) -> dict:
        """Pesquisa e enfileira automaticamente o que passa nas regras (usado pelas buscas salvas)."""
        term, cat = self.clean_term(term), self.clean_category(category)
        rejected: Counter = Counter()
        try:
            resultados = self.search(term, cat)
        except CatalogUnavailableError as e:
            self.db.log_run(term, 0, 0, {}, error=str(e)[:500])
            return {"query": term, "category": cat, "fetched": 0, "queued": 0, "error": str(e)}
        limite = limit if limit is not None else self.filters.max_posts_per_search
        queued, usados = 0, [p["headline"] for p in self.db.list_posts(limit=20) if p["headline"]]
        for r in resultados:
            if not r["ok"]:
                rejected[r["reason"]] += 1
                continue
            if queued >= limite:
                rejected["excedeu_limite_por_busca"] += 1
                continue
            try:
                self.queue_offer(r["offer"], query=term, warnings=r["warnings"], score=r["score"], avoid=usados)
            except InvalidInputError:
                rejected["ja_na_fila"] += 1
                continue
            usados.append(self.db.list_posts(limit=1)[0]["headline"] or "")
            queued += 1
        self.db.log_run(term, len(resultados), queued, dict(rejected))
        if queued and self.in_window():
            self.notifier.notify(f"🆕 {queued} promo(s) de '{term}' na fila. Abra o painel para revisar.")
        return {"query": term, "category": cat, "fetched": len(resultados), "queued": queued,
                "rejected": dict(rejected)}

    # ---------- buscas salvas e watchlist ----------
    def save_search(self, term: str, category: str | None = None) -> int:
        return self.db.save_search(self.clean_term(term), self.clean_category(category))

    def run_saved_searches(self) -> list[dict]:
        """Rotina automática: roda todas as buscas salvas, uma de cada vez."""
        if not self._search_lock.acquire(blocking=False):
            return [{"skipped": "já existe uma rodada em andamento"}]
        try:
            out = []
            for s in self.db.searches(only_enabled=True):
                res = self.run_search(s["keywords"], s["category"])
                self.db.mark_search_run(s["id"], res.get("queued", 0))
                out.append(res)
            out.append(self.run_watchlist())
            return out
        finally:
            self._search_lock.release()

    def run_watchlist(self) -> dict:
        """Confere os ASINs vigiados e enfileira os que entraram em promoção."""
        asins = self.db.watchlist()
        if not asins:
            return {"query": "watchlist", "fetched": 0, "queued": 0}
        try:
            offers = self._fetch(asins)
        except CatalogUnavailableError as e:
            self.db.log_run("watchlist", 0, 0, {}, error=str(e)[:500])
            return {"query": "watchlist", "fetched": 0, "queued": 0, "error": str(e)}
        rejected: Counter = Counter()
        queued = 0
        for o in offers:
            v = evaluate(o, self.filters, self.db)
            if not v.ok:
                rejected[v.reason] += 1
                continue
            try:
                self.queue_offer(o, query="watchlist", warnings=v.warnings, score=v.score)
                queued += 1
            except InvalidInputError:
                rejected["ja_na_fila"] += 1
        self.db.log_run("watchlist", len(offers), queued, dict(rejected))
        return {"query": "watchlist", "fetched": len(offers), "queued": queued, "rejected": dict(rejected)}

    def add_watch(self, asin: str) -> None:
        self.db.add_watch(self.clean_asin(asin))

    # ---------- entrada manual ----------
    def _validate_manual(self, asin: str, title: str, basis: float | None, price: float | None) -> tuple[str, str]:
        asin = self.clean_asin(asin)
        title = " ".join(title.split())
        if not title:
            raise InvalidInputError("título obrigatório")
        if any(v is not None and not 0 < v < 1_000_000 for v in (basis, price)):
            raise InvalidInputError("preço fora do intervalo")
        return asin, title

    def add_manual(self, asin: str, title: str, basis: float | None, price: float | None,
                   coupon: str | None = None) -> int:
        """Para quando você ainda NÃO tem Creators API (menos de 10 vendas/30 dias).
        O link é SEMPRE gerado pelo sistema (amazon.com.br/dp/ASIN?tag=SUA_TAG)."""
        asin, title = self._validate_manual(asin, title, basis, price)
        link = affiliate_link(self.s.amazon_marketplace, asin, self.s.amazon_partner_tag)
        o = Offer(asin=asin, title=title, url=link,
                  price_cents=int(round(price * 100)) if price else None,
                  basis_cents=int(round(basis * 100)) if basis else None, source="manual",
                  coupon=(coupon or "").strip() or None)
        pid = self.queue_offer(o, query="manual")
        note = "manual" + ("" if self.show_prices(o) else " — preços ocultos (ALLOW_MANUAL_PRICES=false)")
        self.db.update_post(pid, note=note)
        return pid

    # ---------- ciclo do post ----------
    def _require_active(self, post: dict) -> None:
        if post["status"] not in ACTIVE:
            raise InvalidStateError(f"post #{post['id']} está '{post['status']}' e não pode mais ser alterado")

    def refresh(self, post_id: int, fast: bool = True) -> dict:
        """Busca o preço de novo. Se a oferta morreu, expira; se só mudou, reescreve o texto."""
        post = self.post(post_id)
        self._require_active(post)
        if post["offer"].source != "api":
            return post
        fresh = self._fetch([post["asin"]], fast=fast)
        if not fresh:
            self.db.update_post(post_id, status=PostStatus.EXPIRED, note="produto indisponível na API")
            return self.post(post_id)
        o = fresh[0]
        o.category = post["offer"].category
        v = evaluate(o, self.filters, self.db, check_repeat=False)
        if not v.ok:
            self.db.update_post(post_id, status=PostStatus.EXPIRED, offer=o, price_checked_at=o.fetched_at,
                                note=f"expirou na revalidação: {v.reason}")
        else:
            o.coupon = post["offer"].coupon
            self.db.update_post(post_id, offer=o, price_cents=o.price_cents, basis_cents=o.basis_cents,
                                discount_pct=o.discount_pct, price_checked_at=o.fetched_at,
                                text=self._render(o, post["headline"]))
        return self.post(post_id)

    def edit_headline(self, post_id: int, headline: str) -> None:
        post = self.post(post_id)
        self._require_active(post)
        headline = " ".join(headline.split()).upper()[:60]
        if not headline:
            raise InvalidInputError("chamada vazia")
        self.db.update_post(post_id, headline=headline, text=self._render(post["offer"], headline))

    def set_coupon(self, post_id: int, coupon: str) -> None:
        post = self.post(post_id)
        self._require_active(post)
        o = post["offer"]
        o.coupon = " ".join(coupon.split()) or None
        self.db.update_post(post_id, offer=o, text=self._render(o, post["headline"]))

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
        """No clique de Enviar: confere o preço na Amazon para o post sair com a promoção ativa.

        Se a API estiver fora, NÃO quebra a tela: mostra aviso e libera com o preço da última checagem
        (o post traz o carimbo de horário), desde que tenha menos de 24h (limite da Licença)."""
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
        """Licença do Creators API: conteúdo de produto (preço, título, imagem) só pode ficar guardado 24h."""
        now = utcnow()
        keep = self.s.content_retention_hours
        n = self.db.purge_product_content(now - timedelta(hours=keep),
                                          [PostStatus.EXPIRED.value, PostStatus.REJECTED.value])
        sent_keep = MONITOR_HOURS + 1 if self.s.monitor_sent_enabled else keep
        n += self.db.purge_product_content(now - timedelta(hours=sent_keep),
                                           [PostStatus.SENT.value, PostStatus.ENDED.value])
        return n

    def housekeeping(self) -> dict:
        """Diário: apaga posts expurgados antigos e o log de buscas velho. Domingo: VACUUM."""
        now = utcnow()
        posts, runs = self.db.delete_old(now - timedelta(days=KEEP_POSTS_DAYS), now - timedelta(days=KEEP_RUNS_DAYS))
        if now.astimezone(ZoneInfo(self.s.timezone)).weekday() == 6:
            self.db.vacuum()
        return {"posts_apagados": posts, "buscas_apagadas": runs}

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
                o.category = p["offer"].category
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

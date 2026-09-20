"""Painel web (FastAPI). É onde você revisa, ajusta e ENVIA (1 clique) cada promoção."""
from __future__ import annotations

import html
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from ..app_factory import build_service
from ..models import PostStatus
from ..pipeline.render import brl
from ..scheduler import start_scheduler

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
security = HTTPBasic()

TABS = {
    "fila": [PostStatus.PENDING.value, PostStatus.APPROVED.value],
    "enviados": [PostStatus.SENT.value],
    "encerrados": [PostStatus.ENDED.value],
    "descartados": [PostStatus.EXPIRED.value, PostStatus.REJECTED.value],
}


def wa_to_html(text: str) -> str:
    """Pré-visualização aproximada da formatação do WhatsApp."""
    t = html.escape(text)
    t = re.sub(r"\*(.+?)\*", r"<b>\1</b>", t)
    t = re.sub(r"~(.+?)~", r"<s>\1</s>", t)
    t = re.sub(r"(https?://\S+)", r'<a href="\1" target="_blank" rel="noopener">\1</a>', t)
    return t.replace("\n", "<br>")


def create_app(svc=None, with_scheduler: bool | None = None) -> FastAPI:
    svc = svc or build_service()
    run_sched = with_scheduler if with_scheduler is not None else os.getenv("DISABLE_SCHEDULER") != "1"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sch = start_scheduler(svc) if run_sched else None
        yield
        if sch:
            sch.shutdown(wait=False)

    app = FastAPI(title="Promo Radar", lifespan=lifespan)
    app.state.svc = svc
    tz = ZoneInfo(svc.s.timezone)
    TEMPLATES.env.filters["wa"] = wa_to_html
    TEMPLATES.env.filters["brl"] = lambda c: brl(c) if c is not None else "—"
    TEMPLATES.env.filters["local"] = lambda d: d.astimezone(tz).strftime("%d/%m %H:%M") if d else "—"

    def auth(cred: HTTPBasicCredentials = Depends(security)):
        ok = (secrets.compare_digest(cred.username, svc.s.panel_user)
              and secrets.compare_digest(cred.password, svc.s.panel_password))
        if not ok:
            raise HTTPException(401, "Não autorizado", headers={"WWW-Authenticate": "Basic"})

    def back(tab="fila"):
        return RedirectResponse(f"/?tab={tab}", status_code=303)

    @app.get("/health")
    def health():
        return {"ok": True, "mode": svc.s.catalog_mode}

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def index(request: Request, tab: str = "fila", niche: str | None = None):
        posts = svc.db.list_posts(TABS.get(tab, TABS["fila"]), niche_id=niche or None)
        for p in posts:
            p["stale"] = svc.is_stale(p)
            p["niche_name"] = svc.niches[p["niche_id"]].name if p["niche_id"] in svc.niches else p["niche_id"]
        return TEMPLATES.TemplateResponse(request, "index.html", {
            "posts": posts, "tab": tab, "tabs": list(TABS), "niche": niche, "niches": list(svc.niches.values()),
            "runs": svc.db.recent_runs(15), "settings": svc.s,
            "watch": {n: svc.db.watchlist(n) for n in svc.niches},
        })

    @app.post("/collect", dependencies=[Depends(auth)])
    def collect(niche: str = Form("")):
        if niche:
            svc.collect(niche)
        else:
            svc.collect_all()
        return back()

    @app.post("/watch", dependencies=[Depends(auth)])
    def watch(niche: str = Form(...), asin: str = Form(...)):
        asin = asin.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{10}", asin):
            raise HTTPException(400, "ASIN inválido (10 caracteres, ex.: B0ABCDE123)")
        svc.add_asin(niche, asin)
        return back()

    @app.post("/watch/remove", dependencies=[Depends(auth)])
    def unwatch(niche: str = Form(...), asin: str = Form(...)):
        svc.db.remove_watch(niche, asin)
        return back()

    @app.post("/manual", dependencies=[Depends(auth)])
    def manual(niche: str = Form(...), asin: str = Form(...), title: str = Form(...), url: str = Form(...),
               basis: str = Form(""), price: str = Form(""), coupon: str = Form("")):
        if "amazon.com.br" not in url or "tag=" not in url:
            raise HTTPException(400, "Use o link de afiliado da Amazon (amazon.com.br com ?tag=).")
        num = lambda v: float(v.replace(".", "").replace(",", ".")) if v.strip() else None  # noqa: E731
        svc.add_manual(niche, asin, title, url, num(basis), num(price), coupon)
        return back()

    @app.post("/posts/{pid}/approve", dependencies=[Depends(auth)])
    def approve(pid: int):
        svc.approve(pid)
        return back()

    @app.post("/posts/{pid}/reject", dependencies=[Depends(auth)])
    def reject(pid: int, tab: str = Form("fila")):
        svc.reject(pid)
        return back(tab)

    @app.post("/posts/{pid}/refresh", dependencies=[Depends(auth)])
    def refresh(pid: int):
        svc.refresh(pid)
        return back()

    @app.post("/posts/{pid}/headline", dependencies=[Depends(auth)])
    def headline(pid: int, headline: str = Form(...)):
        svc.edit_headline(pid, headline)
        return back()

    @app.post("/posts/{pid}/coupon", dependencies=[Depends(auth)])
    def coupon(pid: int, coupon: str = Form("")):
        svc.set_coupon(pid, coupon)
        return back()

    @app.get("/posts/{pid}/send", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def send(request: Request, pid: int):
        if not svc.db.get_post(pid):
            raise HTTPException(404)
        res = svc.prepare_send(pid)
        p = res["post"]
        p["niche_name"] = svc.niches[p["niche_id"]].name
        p["target"] = svc.niches[p["niche_id"]].whatsapp_target
        return TEMPLATES.TemplateResponse(request, "send.html", {"r": res, "p": p, "settings": svc.s})

    @app.post("/posts/{pid}/sent", dependencies=[Depends(auth)])
    def sent(pid: int):
        svc.mark_sent(pid)
        return back()

    return app


def app_from_env() -> FastAPI:
    return create_app()

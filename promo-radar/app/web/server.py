"""Painel web (FastAPI). É onde você revisa, ajusta e ENVIA (1 clique) cada promoção."""
from __future__ import annotations

import hmac
import html
import math
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..app_factory import build_service
from ..models import PostStatus
from ..pipeline.render import brl
from ..scheduler import start_scheduler
from ..service import CatalogUnavailableError, InvalidInputError, InvalidStateError, NotFoundError, PromoService
from .security import COOKIE, LoginLimiter, SecurityMiddleware, Session, SessionStore, check_credentials

HERE = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))
PAGE_SIZE = 30

TABS = {
    "fila": [PostStatus.PENDING.value, PostStatus.APPROVED.value],
    "enviados": [PostStatus.SENT.value],
    "encerrados": [PostStatus.ENDED.value],
    "descartados": [PostStatus.EXPIRED.value, PostStatus.REJECTED.value],
}
# Mensagens por código fixo (nunca texto vindo da URL → sem injeção de conteúdo)
MESSAGES = {
    "api_off": ("warn", "API da Amazon indisponível agora. Nada foi alterado; tente de novo em alguns minutos."),
    "coleta_ok": ("ok", "Coleta concluída."),
    "coleta_ocupada": ("warn", "Já existe uma coleta em andamento para este nicho."),
    "coleta_erro": ("bad", "A coleta falhou. Veja o motivo em 'Últimas coletas'."),
    "salvo": ("ok", "Alteração salva."),
    "enviado": ("ok", "Marcado como enviado. A oferta será monitorada por 48h."),
}

# Limites de tamanho dos campos (o corpo inteiro também é limitado a 64 KB no middleware)
Asin = Annotated[str, Form(max_length=20)]
NicheId = Annotated[str, Form(max_length=40)]
Csrf = Annotated[str, Form(max_length=100)]


# ---------- utilidades de template ----------
def wa_to_html(text: str) -> str:
    """Pré-visualização aproximada da formatação do WhatsApp (escapa antes de formatar)."""
    t = html.escape(text)
    t = re.sub(r"\*(.+?)\*", r"<b>\1</b>", t)
    t = re.sub(r"~(.+?)~", r"<s>\1</s>", t)
    t = re.sub(r"(https://[^\s<]+)", r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>', t)
    return t.replace("\n", "<br>")


def parse_price(v: str) -> float | None:
    v = v.strip().replace("R$", "").strip()
    if not v:
        return None
    try:
        return float(v.replace(".", "").replace(",", ".")) if "," in v else float(v)
    except ValueError:
        raise InvalidInputError(f"preço inválido: {v[:20]!r}") from None


class LoginRequiredError(Exception):
    pass


class CsrfError(Exception):
    pass


# ---------- dependências ----------
def get_svc(request: Request) -> PromoService:
    return request.app.state.svc


def get_session(request: Request) -> Session:
    s = request.app.state.sessions.get(request.cookies.get(COOKIE))
    if s is None:
        raise LoginRequiredError()
    return s


def check_csrf(csrf: Csrf = "", session: Session = Depends(get_session)) -> Session:
    if not csrf or not hmac.compare_digest(csrf, session.csrf):
        raise CsrfError()
    return session


Svc = Annotated[PromoService, Depends(get_svc)]
Auth = Annotated[Session, Depends(get_session)]
Post = Annotated[Session, Depends(check_csrf)]


def back(tab: str = "fila", msg: str | None = None) -> RedirectResponse:
    tab = tab if tab in TABS else "fila"
    return RedirectResponse(f"/?tab={tab}" + (f"&msg={msg}" if msg else ""), status_code=303)


router = APIRouter()


# ---------- login ----------
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, erro: str = "") -> Response:
    return TEMPLATES.TemplateResponse(request, "login.html", {"erro": erro[:20]})


@router.post("/login")
def login(request: Request, username: Annotated[str, Form(max_length=100)] = "",  # nosec B107
          password: Annotated[str, Form(max_length=200)] = "") -> Response:
    app = request.app
    ip = request.client.host if request.client else "?"
    wait = app.state.limiter.blocked_for(ip)
    if wait:
        return TEMPLATES.TemplateResponse(request, "login.html", {"erro": "bloqueado", "wait": wait},
                                          status_code=429, headers={"Retry-After": str(wait)})
    s = app.state.svc.s
    if not check_credentials(username, password, s.panel_user, s.panel_password):
        app.state.limiter.fail(ip)
        return TEMPLATES.TemplateResponse(request, "login.html", {"erro": "invalido"}, status_code=401)
    app.state.limiter.reset(ip)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, app.state.sessions.create(username), httponly=True, samesite="strict",
                    secure=s.cookie_secure, max_age=s.session_hours * 3600, path="/")
    return resp


@router.post("/logout")
def logout(request: Request, _: Post) -> Response:
    request.app.state.sessions.delete(request.cookies.get(COOKIE))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


# ---------- painel ----------
@router.get("/health")
def health(request: Request) -> JSONResponse:
    sch = request.app.state.scheduler
    res = request.app.state.svc.health(sch.running if sch is not None else None)
    return JSONResponse({"ok": res["ok"]}, status_code=200 if res["ok"] else 503)


@router.get("/", response_class=HTMLResponse)
def index(request: Request, svc: Svc, session: Auth, tab: str = "fila", niche: str = "", page: int = 1,
          msg: str = "") -> Response:
    tab = tab if tab in TABS else "fila"
    niche_id = niche if niche in svc.niches else None
    total = svc.db.count_posts(TABS[tab], niche_id=niche_id)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, page), pages)
    posts = svc.db.list_posts(TABS[tab], niche_id=niche_id, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    for p in posts:
        p["stale"] = svc.is_stale(p)
        p["niche_name"] = svc.niches[p["niche_id"]].name if p["niche_id"] in svc.niches else p["niche_id"]
    tabs = [t for t in TABS if t != "encerrados" or svc.s.monitor_sent_enabled]
    return TEMPLATES.TemplateResponse(request, "index.html", {
        "posts": posts, "tab": tab, "tabs": tabs, "niche": niche_id or "", "niches": list(svc.niches.values()),
        "runs": svc.db.recent_runs(15), "settings": svc.s, "csrf": session.csrf, "page": page, "pages": pages,
        "total": total, "msg": MESSAGES.get(msg), "watch": {n: svc.db.watchlist(n) for n in svc.niches},
    })


@router.post("/collect")
def collect(svc: Svc, _: Post, niche: NicheId = "") -> Response:
    res = [svc.collect(svc.niche(niche).id)] if niche else svc.collect_all()
    if any("error" in r for r in res):
        return back(msg="coleta_erro")
    if any("skipped" in r for r in res):
        return back(msg="coleta_ocupada")
    return back(msg="coleta_ok")


@router.post("/watch")
def watch(svc: Svc, _: Post, niche: NicheId, asin: Asin) -> Response:
    svc.add_asin(niche, asin)
    return back(msg="salvo")


@router.post("/watch/remove")
def unwatch(svc: Svc, _: Post, niche: NicheId, asin: Asin) -> Response:
    svc.db.remove_watch(niche, asin)
    return back(msg="salvo")


@router.post("/manual")
def manual(svc: Svc, _: Post, niche: NicheId, asin: Asin, title: Annotated[str, Form(max_length=300)],
           basis: Annotated[str, Form(max_length=20)] = "", price: Annotated[str, Form(max_length=20)] = "",
           coupon: Annotated[str, Form(max_length=30)] = "") -> Response:
    svc.add_manual(niche, asin, title, parse_price(basis), parse_price(price), coupon)
    return back(msg="salvo")


@router.post("/posts/{pid}/approve")
def approve(pid: int, svc: Svc, _: Post) -> Response:
    svc.approve(pid)
    return back(msg="salvo")


@router.post("/posts/{pid}/reject")
def reject(pid: int, svc: Svc, _: Post, tab: Annotated[str, Form(max_length=20)] = "fila") -> Response:
    svc.reject(pid)
    return back(tab)


@router.post("/posts/{pid}/refresh")
def refresh(pid: int, svc: Svc, _: Post) -> Response:
    svc.refresh(pid)
    return back(msg="salvo")


@router.post("/posts/{pid}/headline")
def headline(pid: int, svc: Svc, _: Post, headline: Annotated[str, Form(max_length=60)]) -> Response:
    svc.edit_headline(pid, headline)
    return back(msg="salvo")


@router.post("/posts/{pid}/coupon")
def coupon(pid: int, svc: Svc, _: Post, coupon: Annotated[str, Form(max_length=30)] = "") -> Response:
    svc.set_coupon(pid, coupon)
    return back(msg="salvo")


@router.get("/posts/{pid}/send", response_class=HTMLResponse)
def send(request: Request, pid: int, svc: Svc, session: Auth) -> Response:
    res = svc.prepare_send(pid)
    p = res["post"]
    niche = svc.niches.get(p["niche_id"])
    p["niche_name"] = niche.name if niche else p["niche_id"]
    p["target"] = niche.whatsapp_target if niche else ""
    return TEMPLATES.TemplateResponse(request, "send.html",
                                      {"r": res, "p": p, "settings": svc.s, "csrf": session.csrf})


@router.post("/posts/{pid}/sent")
def sent(pid: int, svc: Svc, _: Post) -> Response:
    svc.mark_sent(pid)
    return back(msg="enviado")


# ---------- app ----------
def _error(request: Request, status: int, title: str, detail: str) -> Response:
    return TEMPLATES.TemplateResponse(request, "error.html", {"title": title, "detail": detail},
                                      status_code=status)


def _install_handlers(app: FastAPI) -> None:
    @app.exception_handler(LoginRequiredError)
    async def _login(request: Request, exc: LoginRequiredError) -> Response:
        if request.method == "GET":
            return RedirectResponse("/login", status_code=303)
        return _error(request, 401, "Sessão expirada", "Entre de novo no painel.")

    @app.exception_handler(CsrfError)
    async def _csrf(request: Request, exc: CsrfError) -> Response:
        return _error(request, 403, "Requisição recusada", "Token de segurança inválido. Recarregue a página.")

    @app.exception_handler(NotFoundError)
    async def _nf(request: Request, exc: NotFoundError) -> Response:
        return _error(request, 404, "Não encontrado", str(exc))

    @app.exception_handler(InvalidInputError)
    async def _bad(request: Request, exc: InvalidInputError) -> Response:
        return _error(request, 400, "Dados inválidos", str(exc))

    @app.exception_handler(InvalidStateError)
    async def _state(request: Request, exc: InvalidStateError) -> Response:
        return _error(request, 409, "Ação não permitida", str(exc))

    @app.exception_handler(CatalogUnavailableError)
    async def _api(request: Request, exc: CatalogUnavailableError) -> Response:
        return back(msg="api_off")


def create_app(svc: PromoService | None = None, with_scheduler: bool | None = None) -> FastAPI:
    svc = svc or build_service()
    problem = svc.s.password_problem()
    if problem:
        raise SystemExit(f"Painel não iniciado: {problem}. Defina no .env (veja .env.example).")
    run_sched = with_scheduler if with_scheduler is not None else os.getenv("DISABLE_SCHEDULER") != "1"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        app.state.scheduler = start_scheduler(svc) if run_sched else None
        yield
        if app.state.scheduler:
            app.state.scheduler.shutdown(wait=False)

    app = FastAPI(title="Promo Radar", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.svc = svc
    app.state.scheduler = None
    app.state.sessions = SessionStore(svc.s.session_hours * 3600)
    app.state.limiter = LoginLimiter(svc.s.login_max_failures, svc.s.login_window_minutes * 60)
    app.add_middleware(SecurityMiddleware, hsts=svc.s.cookie_secure)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    tz = ZoneInfo(svc.s.timezone)
    env = TEMPLATES.env
    env.filters["wa"] = wa_to_html
    env.filters["brl"] = lambda c: brl(c) if c is not None else "—"
    env.filters["local"] = lambda d: d.astimezone(tz).strftime("%d/%m %H:%M") if d else "—"
    _install_handlers(app)
    app.include_router(router)
    return app


def app_from_env() -> FastAPI:
    return create_app()

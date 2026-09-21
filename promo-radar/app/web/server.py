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
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ..app_factory import build_service
from ..categories import categories, display_name
from ..db import COUNT_CAP
from ..models import PostStatus
from ..pipeline.render import brl
from ..scheduler import start_scheduler
from ..service import CatalogUnavailableError, InvalidInputError, InvalidStateError, NotFoundError, PromoService
from .security import COOKIE, LoginLimiter, SecurityMiddleware, Session, SessionStore, check_credentials

HERE = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))
PAGE_SIZE = 30

STATUS_PT = {"pending": "aguardando revisão", "approved": "aprovado", "sent": "enviado",
             "rejected": "descartado", "expired": "expirado", "ended": "encerrado"}

TABS = {
    "fila": [PostStatus.PENDING.value, PostStatus.APPROVED.value],
    "enviados": [PostStatus.SENT.value],
    "encerrados": [PostStatus.ENDED.value],
    "descartados": [PostStatus.EXPIRED.value, PostStatus.REJECTED.value],
}
# Mensagens por código fixo (nunca texto vindo da URL → sem injeção de conteúdo)
MESSAGES = {
    "api_off": ("warn", "API da Amazon indisponível agora. Nada foi alterado; tente de novo em alguns minutos."),
    "busca_ok": ("ok", "Busca concluída."),
    "busca_erro": ("bad", "A busca falhou. Veja o motivo em 'Últimas buscas'."),
    "salvo": ("ok", "Alteração salva."),
    "enviado": ("ok", "Marcado como enviado."),
    "na_fila": ("ok", "Produto colocado na fila."),
}

# Limites de tamanho dos campos (o corpo inteiro também é limitado a 64 KB no middleware)
Asin = Annotated[str, Form(max_length=20)]
Termo = Annotated[str, Form(max_length=80)]
Categoria = Annotated[str, Form(max_length=40)]
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


class View(BaseModel):
    """Filtros da tela, carregados em todo formulário para não se perderem na ação."""
    tab: str = "fila"
    cat: str = ""
    q: str = ""
    page: int = 1

    def url(self, msg: str | None = None) -> str:
        q = {"tab": self.tab if self.tab in TABS else "fila", "cat": self.cat, "q": self.q,
             "page": str(self.page), "msg": msg or ""}
        return "/?" + urlencode({k: v for k, v in q.items() if v})


def back(view: View | None = None, msg: str | None = None) -> RedirectResponse:
    return RedirectResponse((view or View()).url(msg), status_code=303)


def local_time(dt: Any, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%d/%m %H:%M") if dt else ""


def wants_json(request: Request) -> bool:
    """O painel chama as ações por fetch; sem JS (ou sem o cabeçalho) cai no redirect de sempre."""
    return request.headers.get("x-requested-with") == "fetch"


def card(svc: PromoService, post_id: int) -> dict:  # noqa: D401
    """Estado do card depois de uma ação, para o painel atualizar só ele."""
    p = svc.db.get_post(post_id)
    if p is None:
        return {"id": post_id, "removed": True}
    return {
        "id": p["id"], "status": p["status"], "headline": p["headline"] or "", "note": p["note"] or "",
        "coupon": p["offer"].coupon or "", "text_html": wa_to_html(p["text"]),
        "price_checked": local_time(p["price_checked_at"], svc.s.timezone),
        "stale": svc.is_stale(p), "removed": p["status"] not in TABS["fila"],
        "query": p["query"] or "",
        "score": p["score"], "category": display_name(p["category"] or "", svc.s.amazon_marketplace),
        "status_pt": STATUS_PT.get(p["status"], p["status"]),
    }


def acted(request: Request, svc: PromoService, post_id: int, view: View, msg: str = "salvo") -> Response:
    if not wants_json(request):
        return back(view, msg)
    data = card(svc, post_id)
    # contagens atualizadas dos filtros, para os chips não ficarem desatualizados sem recarregar
    data["counts"] = svc.db.count_by_category(TABS["fila"], termo=view.q or None)
    data["total"] = sum(data["counts"].values())
    return JSONResponse(data)


def view_form(tab: Annotated[str, Form(max_length=20)] = "fila", cat: Categoria = "",
              q: Termo = "", page: Annotated[int, Form()] = 1) -> View:
    """Filtros que o formulário carrega junto (POST), para a ação voltar exatamente para a mesma tela."""
    return View(tab=tab, cat=cat, q=q, page=max(1, page))


def view_query(tab: str = "fila", cat: str = "", q: str = "", page: int = 1) -> View:
    return View(tab=tab, cat=cat, q=q, page=max(1, page))


ViewDep = Annotated[View, Depends(view_form)]
ViewQuery = Annotated[View, Depends(view_query)]

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
def index(request: Request, svc: Svc, session: Auth, tab: str = "fila", cat: str = "", q: str = "",
          page: int = 1, msg: str = "") -> Response:
    tab = tab if tab in TABS else "fila"
    cats = categories(svc.s.amazon_marketplace)          # categorias oficiais do marketplace
    cat_id = cat if cat in cats else None
    termo = " ".join(q.split())[:80] or None             # filtro de texto sobre a fila
    by_cat = svc.db.count_by_category(TABS[tab], termo=termo)
    # o total sai da mesma contagem dos chips: com o filtro de texto, uma varredura no lugar de duas
    total = by_cat.get(cat_id, 0) if cat_id else sum(by_cat.values())
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, page), pages)
    posts = svc.db.list_posts(TABS[tab], limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE,
                              category=cat_id, termo=termo)
    for p in posts:
        p["stale"] = svc.is_stale(p)
        p["category_name"] = display_name(p["category"] or "", svc.s.amazon_marketplace)
    view = View(tab=tab, cat=cat_id or "", q=termo or "", page=page)
    tabs = [t for t in TABS if t != "encerrados" or svc.s.monitor_sent_enabled]
    return TEMPLATES.TemplateResponse(request, "index.html", {
        "posts": posts, "tab": tab, "tabs": tabs, "cats": cats, "cat": cat_id or "", "q": termo or "",
        "by_cat": by_cat, "sem_categoria": by_cat.get("", 0), "runs": svc.db.recent_runs(15),
        "settings": svc.s, "csrf": session.csrf, "page": page, "pages": pages, "total": total,
        "total_txt": f"{COUNT_CAP}+" if total >= COUNT_CAP else str(total),
        "msg": MESSAGES.get(msg), "watch": svc.db.watchlist(), "buscas": svc.db.searches(), "view": view,
    })


# ---------- barra de pesquisa ----------
@router.get("/buscar", response_class=HTMLResponse)
def buscar(request: Request, svc: Svc, session: Auth, q: str = "", cat: str = "") -> Response:
    """Pesquisa na Amazon e mostra os resultados para você escolher o que vai para a fila."""
    termo = " ".join(q.split())[:80]
    cats = categories(svc.s.amazon_marketplace)
    categoria = cat if cat in cats else "All"
    resultados, erro = [], None
    if termo:
        try:
            resultados = svc.search(termo, categoria)
        except CatalogUnavailableError:
            erro = MESSAGES["api_off"][1]
        except InvalidInputError as e:
            erro = str(e)
    return TEMPLATES.TemplateResponse(request, "buscar.html", {
        "q": termo, "cat": categoria, "cats": cats, "resultados": resultados, "erro": erro,
        "settings": svc.s, "csrf": session.csrf, "salvas": svc.db.searches(),
    })


@router.post("/buscar/fila")
def buscar_fila(svc: Svc, _: Post, asin: Asin, q: Termo = "", cat: Categoria = "") -> Response:
    """Coloca na fila um produto que apareceu na busca."""
    svc.queue_asin(asin, query=q, category=cat or None)
    return RedirectResponse(f"/buscar?{urlencode({'q': q, 'cat': cat})}&ok={asin}", status_code=303)


@router.post("/buscar/salvar")
def buscar_salvar(svc: Svc, _: Post, q: Termo, cat: Categoria = "All") -> Response:
    """Salva a busca para rodar sozinha de tempos em tempos."""
    svc.save_search(q, cat)
    return RedirectResponse(f"/buscar?{urlencode({'q': q, 'cat': cat})}&salva=1", status_code=303)


@router.post("/buscas/{search_id}/toggle")
def busca_toggle(svc: Svc, _: Post, search_id: int, view: ViewDep,
                 enabled: Annotated[str, Form(max_length=5)] = "") -> Response:
    svc.db.toggle_search(search_id, enabled == "1")
    return back(view, "salvo")


@router.post("/buscas/{search_id}/remover")
def busca_remover(svc: Svc, _: Post, search_id: int, view: ViewDep) -> Response:
    svc.db.delete_search(search_id)
    return back(view, "salvo")


@router.post("/buscas/rodar")
def buscas_rodar(svc: Svc, _: Post, view: ViewDep) -> Response:
    res = svc.run_saved_searches()
    return back(view, "busca_erro" if any("error" in r for r in res) else "busca_ok")


@router.post("/watch")
def watch(svc: Svc, _: Post, view: ViewDep, asin: Asin) -> Response:
    svc.add_watch(asin)
    return back(view, "salvo")


@router.post("/watch/remove")
def unwatch(svc: Svc, _: Post, view: ViewDep, asin: Asin) -> Response:
    svc.db.remove_watch(asin)
    return back(view, "salvo")


@router.post("/manual")
def manual(svc: Svc, _: Post, view: ViewDep, asin: Asin,
           title: Annotated[str, Form(max_length=300)],
           basis: Annotated[str, Form(max_length=20)] = "",
           price: Annotated[str, Form(max_length=20)] = "",
           coupon: Annotated[str, Form(max_length=30)] = "") -> Response:
    svc.add_manual(asin, title, parse_price(basis), parse_price(price), coupon)
    return back(view, "salvo")


# --- ações de um post: respondem JSON para o painel (fetch) e redirect sem JS ---
@router.post("/posts/{pid}/approve")
def approve(request: Request, pid: int, svc: Svc, _: Post, view: ViewDep) -> Response:
    svc.approve(pid)
    return acted(request, svc, pid, view)


@router.post("/posts/{pid}/reject")
def reject(request: Request, pid: int, svc: Svc, _: Post, view: ViewDep) -> Response:
    svc.reject(pid)
    return acted(request, svc, pid, view)


@router.post("/posts/{pid}/refresh")
def refresh(request: Request, pid: int, svc: Svc, _: Post, view: ViewDep) -> Response:
    svc.refresh(pid)
    return acted(request, svc, pid, view)


@router.post("/posts/{pid}/headline")
def headline(request: Request, pid: int, svc: Svc, _: Post, view: ViewDep,
             headline: Annotated[str, Form(max_length=60)]) -> Response:
    svc.edit_headline(pid, headline)
    return acted(request, svc, pid, view)


@router.post("/posts/{pid}/coupon")
def coupon(request: Request, pid: int, svc: Svc, _: Post, view: ViewDep,
           coupon: Annotated[str, Form(max_length=30)] = "") -> Response:
    svc.set_coupon(pid, coupon)
    return acted(request, svc, pid, view)


@router.get("/posts/{pid}/send", response_class=HTMLResponse)
def send(request: Request, pid: int, svc: Svc, session: Auth, view: ViewQuery) -> Response:
    res = svc.prepare_send(pid)
    p = res["post"]
    p["target"] = svc.style.whatsapp_target
    return TEMPLATES.TemplateResponse(request, "send.html",
                                      {"r": res, "p": p, "settings": svc.s, "csrf": session.csrf, "view": view})


@router.post("/posts/{pid}/sent")
def sent(pid: int, svc: Svc, _: Post, view: ViewDep) -> Response:
    svc.mark_sent(pid)
    return back(view, "enviado")


# ---------- app ----------
def _error(request: Request, status: int, title: str, detail: str) -> Response:
    if wants_json(request):
        return JSONResponse({"erro": f"{title}: {detail}"}, status_code=status)
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
        if wants_json(request):
            return JSONResponse({"erro": MESSAGES["api_off"][1]}, status_code=503)
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

import re

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.web.server import create_app

from .conftest import PASSWORD


def login(c, password=PASSWORD):
    return c.post("/login", data={"username": "admin", "password": password}, follow_redirects=False)


def csrf_of(c):
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/").text).group(1)


@pytest.fixture
def client(svc):
    c = TestClient(create_app(svc, with_scheduler=False), base_url="http://painel.local")
    assert login(c).status_code == 303
    return c


def test_requires_login(svc):
    c = TestClient(create_app(svc, with_scheduler=False))
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.post("/collect", data={"niche": ""}).status_code == 401


def test_refuses_weak_or_missing_password(svc):
    for pw in ["", "troque-esta-senha", "curta", "admin"]:
        svc.s.panel_password = pw
        with pytest.raises(SystemExit):
            create_app(svc, with_scheduler=False)


def test_session_cookie_flags(svc):
    c = TestClient(create_app(svc, with_scheduler=False))
    cookie = login(c).headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_bruteforce_lockout(svc):
    c = TestClient(create_app(svc, with_scheduler=False))
    codes = [login(c, "errada-errada-1").status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert login(c).status_code == 429          # nem a senha certa entra durante o bloqueio


def test_csrf_token_and_origin(client, svc):
    assert client.post("/collect", data={"niche": ""}).status_code == 403                    # sem token
    assert client.post("/collect", data={"niche": "", "csrf": "x"}).status_code == 403       # token errado
    tok = csrf_of(client)
    r = client.post("/collect", data={"niche": "", "csrf": tok}, headers={"Origin": "https://mal.example"})
    assert r.status_code == 403                                                              # outra origem
    r = client.post("/collect", data={"niche": "", "csrf": tok}, follow_redirects=False)
    assert r.status_code == 303


def test_security_headers_and_no_docs(client):
    h = client.get("/").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    for p in ["/docs", "/redoc", "/openapi.json"]:
        assert client.get(p).status_code == 404


def test_body_limit_and_field_limits(client):
    tok = csrf_of(client)
    big = client.post("/manual", data={"csrf": tok, "niche": "lego", "asin": "B0BIGBIG01", "title": "A" * 70000})
    assert big.status_code == 413
    long = client.post("/manual", data={"csrf": tok, "niche": "lego", "asin": "B0BIGBIG01", "title": "A" * 400})
    assert long.status_code == 422


def test_invalid_inputs_are_4xx(client):
    tok = csrf_of(client)
    assert client.post("/posts/999/refresh", data={"csrf": tok}).status_code == 404
    assert client.post("/posts/999/headline", data={"csrf": tok, "headline": "x"}).status_code == 404
    assert client.get("/posts/999/send").status_code == 404
    assert client.post("/collect", data={"csrf": tok, "niche": "nao-existe"}).status_code == 400
    assert client.post("/manual", data={"csrf": tok, "niche": "lego", "asin": "B0XXXXXXX1", "title": "t",
                                         "price": "abc"}).status_code == 400
    assert client.post("/watch", data={"csrf": tok, "niche": "lego", "asin": "abc"}).status_code == 400


def test_manual_link_is_always_generated(client, svc):
    tok = csrf_of(client)
    client.post("/manual", data={"csrf": tok, "niche": "lego", "asin": "b0manual01", "title": "LEGO X",
                                 "url": "https://evil.example/phish"})
    p = svc.db.list_posts(["pending"])[0]
    assert p["offer"].url == "https://www.amazon.com.br/dp/B0MANUAL01?tag=teste-20"
    assert "evil" not in p["text"]


def test_panel_flow(client, svc):
    tok = csrf_of(client)
    assert client.post("/collect", data={"niche": "", "csrf": tok}, follow_redirects=False).status_code == 303
    page = client.get("/")
    assert "Enviar" in page.text and "<s>De R$" in page.text
    pid = svc.db.list_posts(["pending"])[0]["id"]
    client.post(f"/posts/{pid}/headline", data={"headline": "nova chamada", "csrf": tok})
    assert "*NOVA CHAMADA*" in svc.db.get_post(pid)["text"]
    client.post(f"/posts/{pid}/coupon", data={"coupon": "LEGO10", "csrf": tok})
    send = client.get(f"/posts/{pid}/send")
    assert "wa.me/?text=" in send.text and "LEGO10" in send.text and "<script>" not in send.text
    client.post(f"/posts/{pid}/sent", data={"csrf": tok})
    assert svc.db.get_post(pid)["status"] == "sent"
    assert "monitorado" in client.get("/?tab=enviados").text
    # post enviado não pode mais ser alterado
    assert client.post(f"/posts/{pid}/reject", data={"csrf": tok}).status_code == 409


def test_watch_and_manual_happy_path(client, svc):
    tok = csrf_of(client)
    client.post("/watch", data={"csrf": tok, "niche": "lego", "asin": "B0MOCK0104"})
    assert "B0MOCK0104" in svc.db.watchlist("lego")
    client.post("/watch/remove", data={"csrf": tok, "niche": "lego", "asin": "B0MOCK0104"})
    assert svc.db.watchlist("lego") == []
    client.post("/collect", data={"niche": "lego", "csrf": tok})
    pid = svc.db.list_posts(["pending"])[0]["id"]
    client.post(f"/posts/{pid}/approve", data={"csrf": tok})
    assert svc.db.get_post(pid)["status"] == "approved"
    client.post(f"/posts/{pid}/refresh", data={"csrf": tok})
    client.post(f"/posts/{pid}/reject", data={"csrf": tok})
    assert svc.db.get_post(pid)["status"] == "rejected"


def test_pagination(client, svc):
    from app.models import Offer
    for i in range(65):
        svc.db.create_post("lego", Offer(asin=f"B0PAGE{i:04d}", title="t", url="u", price_cents=1), "H", "t", i)
    assert "página 1 de 3" in client.get("/").text
    assert "página 3 de 3" in client.get("/?page=99").text


def test_logout(client):
    tok = csrf_of(client)
    client.post("/logout", data={"csrf": tok})
    assert client.get("/", follow_redirects=False).status_code == 303


def test_health(svc):
    c = TestClient(create_app(svc, with_scheduler=False))
    assert c.get("/health").json() == {"ok": True}

    def broken():
        raise RuntimeError("disco cheio")
    svc.db.check = broken
    r = c.get("/health")
    assert r.status_code == 503 and r.json() == {"ok": False}


def test_settings_password_rules():
    base = dict(_env_file=None, panel_user="admin")
    assert Settings(**base, panel_password="").password_problem()
    assert Settings(**base, panel_password="troque-esta-senha").password_problem()
    assert Settings(**base, panel_password="curtinha").password_problem()
    assert Settings(**base, panel_password="uma-senha-bem-grande").password_problem() is None


def test_health_reports_dead_scheduler(svc):
    app = create_app(svc, with_scheduler=False)
    c = TestClient(app)

    class Dead:
        running = False
    app.state.scheduler = Dead()
    assert c.get("/health").status_code == 503

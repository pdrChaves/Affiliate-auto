"""CLI, agendador, notificador e cliente da API em cenários não cobertos antes."""
import json

import httpx

from app.amazon.creators import CreatorsClient
from app.config import Settings
from app.senders.telegram import TelegramNotifier


def test_cli_commands(monkeypatch, capsys, svc):
    import app.app_factory
    from app import cli
    monkeypatch.setattr(app.app_factory, "build_service", lambda *a, **k: svc)
    cli.main(["collect", "lego"])
    assert json.loads(capsys.readouterr().out)["niche"] == "lego"
    cli.main(["preview"])
    assert "#publi" in capsys.readouterr().out
    cli.main(["monitor"])
    cli.main(["purge"])
    assert "expurgados" in capsys.readouterr().out


def test_scheduler_registers_jobs(svc):
    from app.scheduler import start_scheduler
    sch = start_scheduler(svc)
    try:
        ids = {j.id for j in sch.get_jobs()}
        assert {"collect:lego", "collect:radar-homem", "expire_queue", "purge_content", "housekeeping"} <= ids
        assert "monitor_sent" not in ids          # desligado por padrão
        assert sch.running
    finally:
        sch.shutdown(wait=False)


def test_scheduler_adds_monitor_when_enabled(svc):
    from app.scheduler import start_scheduler
    svc.s.monitor_sent_enabled = True
    sch = start_scheduler(svc)
    try:
        assert "monitor_sent" in {j.id for j in sch.get_jobs()}
    finally:
        sch.shutdown(wait=False)


def test_telegram_sends_and_swallows_errors():
    sent = []

    def ok(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True})
    TelegramNotifier("TOKEN", "42", http=httpx.Client(transport=httpx.MockTransport(ok))).notify("oi")
    assert sent == [{"chat_id": "42", "text": "oi", "disable_web_page_preview": True}]

    def down(req):
        raise httpx.ConnectError("x", request=req)
    TelegramNotifier("T", "1", http=httpx.Client(transport=httpx.MockTransport(down))).notify("x")


def _client(handler):
    s = Settings(_env_file=None, amazon_credential_id="i", amazon_credential_secret="s", amazon_rps=1000)
    return CreatorsClient(s, http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_timeout_is_retried(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def h(req):
        if req.url.path.endswith("token"):
            return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("t", request=req)
        return httpx.Response(200, json={"itemsResult": {"items": []}})
    assert _client(h).get_items(["B000000001"]) == []
    assert calls["n"] == 2


def test_fast_mode_gives_up_quickly(monkeypatch):
    import pytest

    from app.amazon.creators import CreatorsAPIError
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def h(req):
        if req.url.path.endswith("token"):
            return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
        calls["n"] += 1
        return httpx.Response(503)
    with pytest.raises(CreatorsAPIError):
        _client(h).get_items(["B000000001"], fast=True)
    assert calls["n"] == 2


def test_token_server_unreachable():
    import pytest

    from app.amazon.creators import CreatorsAPIError

    def h(req):
        raise httpx.ConnectError("x", request=req)
    with pytest.raises(CreatorsAPIError, match="token"):
        _client(h).get_items(["B000000001"])


def test_search_payload_and_pagination():
    from tests.test_creators import ITEM
    bodies = []

    def h(req):
        if req.url.path.endswith("token"):
            return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
        bodies.append(json.loads(req.content))
        items = [dict(ITEM, asin=f"B0SRCH{i:04d}") for i in range(10 if len(bodies) == 1 else 3)]
        return httpx.Response(200, json={"searchResult": {"items": items}})
    out = _client(h).search("lego", "Toys", browse_node_id="123", min_saving_pct=20, min_price_cents=5000,
                            max_price_cents=90000, pages=3)
    assert len(out) == 13 and len(bodies) == 2           # parou quando a página veio incompleta
    b = bodies[0]
    assert b["keywords"] == "lego" and b["minSavingPercent"] == 20 and b["browseNodeId"] == "123"
    assert b["minPrice"] == 5000 and b["itemPage"] == 1 and bodies[1]["itemPage"] == 2

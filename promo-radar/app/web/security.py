"""Segurança do painel: sessão por cookie, CSRF, limite de tentativas de login e cabeçalhos HTTP.

Por que saiu o HTTP Basic da v1: o navegador reenviava a senha sozinho em qualquer requisição, inclusive
nas disparadas por outros sites (CSRF). Agora:
  - cookie de sessão HttpOnly + SameSite=Strict (o navegador não o envia em requisições vindas de outro site);
  - token CSRF por sessão em todo formulário;
  - checagem de Origin/Referer em todo POST;
  - bloqueio por IP após falhas de login.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp

COOKIE = "pr_session"
MAX_BODY_BYTES = 64 * 1024     # formulários do painel são pequenos; nada legítimo passa disso

CSP = ("default-src 'self'; img-src 'self' data: https://*.media-amazon.com https://*.ssl-images-amazon.com; "
       "style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")


def _digest(s: str) -> bytes:
    return hashlib.sha256(s.encode()).digest()


def check_credentials(user: str, password: str, expected_user: str, expected_password: str) -> bool:
    """Comparação em tempo constante (não vaza, pelo tempo de resposta, quantos caracteres acertou)."""
    ok_user = hmac.compare_digest(_digest(user), _digest(expected_user))
    ok_pass = hmac.compare_digest(_digest(password), _digest(expected_password))
    return ok_user and ok_pass


@dataclass
class Session:
    user: str
    csrf: str
    expires: float


class SessionStore:
    """Sessões em memória: reiniciar o processo desloga (aceitável para 1 operador)."""

    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._items: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, user: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._gc()
            self._items[token] = Session(user, secrets.token_urlsafe(32), time.time() + self.ttl)
        return token

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._lock:
            s = self._items.get(token)
            if not s or s.expires < time.time():
                self._items.pop(token, None)
                return None
            s.expires = time.time() + self.ttl      # expiração deslizante
            return s

    def delete(self, token: str | None) -> None:
        with self._lock:
            self._items.pop(token or "", None)

    def _gc(self) -> None:
        now = time.time()
        for k in [k for k, v in self._items.items() if v.expires < now]:
            del self._items[k]


@dataclass
class LoginLimiter:
    max_failures: int
    window_seconds: int
    _fails: dict[str, deque] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def blocked_for(self, ip: str) -> int:
        """Segundos restantes de bloqueio (0 = liberado)."""
        with self._lock:
            q = self._prune(ip)
            if len(q) >= self.max_failures:
                return int(q[0] + self.window_seconds - time.time()) + 1
            return 0

    def fail(self, ip: str) -> None:
        with self._lock:
            self._prune(ip).append(time.time())

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)

    def _prune(self, ip: str) -> deque:
        q = self._fails.setdefault(ip, deque())
        limit = time.time() - self.window_seconds
        while q and q[0] < limit:
            q.popleft()
        return q


def same_origin(request: Request) -> bool:
    """POST só vale se Origin/Referer (quando presentes) apontarem para este mesmo host."""
    host = request.headers.get("host", "")
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value:
            return urlsplit(value).netloc == host
    return True   # clientes sem navegador (curl) não mandam; o token CSRF continua exigido


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, hsts: bool):
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return PlainTextResponse("Requisição grande demais", status_code=413)
        if request.method == "POST" and not same_origin(request):
            return PlainTextResponse("Origem não permitida", status_code=403)
        resp = await call_next(request)
        h = resp.headers
        h["Content-Security-Policy"] = CSP
        h["X-Frame-Options"] = "DENY"
        h["X-Content-Type-Options"] = "nosniff"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not request.url.path.startswith("/static/"):
            h["Cache-Control"] = "no-store"
        if self.hsts:
            h["Strict-Transport-Security"] = "max-age=31536000"
        return resp

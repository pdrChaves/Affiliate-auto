"""Configuração: variáveis de ambiente (.env) + regras e estilo (config/config.yaml)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .categories import as_table, is_valid

# Endpoints de token do Creators API por versão de credencial.
# Brasil pertence ao grupo "North America" (versões x.1).
TOKEN_URLS = {
    "3.1": "https://api.amazon.com/auth/o2/token",
    "3.2": "https://api.amazon.co.uk/auth/o2/token",
    "3.3": "https://api.amazon.co.jp/auth/o2/token",
    "2.1": "https://creatorsapi.auth.us-east-1.amazoncognito.com/oauth2/token",
    "2.2": "https://creatorsapi.auth.eu-south-2.amazoncognito.com/oauth2/token",
    "2.3": "https://creatorsapi.auth.us-west-2.amazoncognito.com/oauth2/token",
}


MIN_PASSWORD_LEN = 8
MARKETPLACE = "www.amazon.com.br"   # marketplace usado para validar as categorias das buscas

WEAK_PASSWORDS = {"troque-esta-senha", "admin", "password", "12345678", "123456789", "123456789012",
                  "senha123", "senha1234", "admin123", "qwerty123", "promoradar"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    catalog_mode: str = "mock"  # mock | creators

    amazon_partner_tag: str = "seutag-20"
    amazon_marketplace: str = "www.amazon.com.br"
    amazon_credential_id: str = ""
    amazon_credential_secret: str = ""
    amazon_credential_version: str = "3.1"
    amazon_token_url: str = ""
    amazon_api_base: str = "https://creatorsapi.amazon/catalog/v1"
    amazon_rps: float = 1.0

    max_price_age_minutes: int = 0      # 0 = revalida o preço SEMPRE antes de gerar o link do envio
    monitor_sent_enabled: bool = False  # acompanhar a oferta depois do envio e avisar quando acabar
    allow_manual_prices: bool = False
    content_retention_hours: int = 24   # Licença: conteúdo de produto (exceto ASIN) no máx. 24h

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    panel_user: str = "admin"
    panel_password: str = ""            # obrigatório, >= 8 caracteres (o painel não sobe sem isso)
    cookie_secure: bool = False         # true quando o painel estiver atrás de HTTPS (liga Secure + HSTS)
    session_hours: int = 12
    login_max_failures: int = 5         # falhas por IP na janela abaixo → bloqueio
    login_window_minutes: int = 15
    host: str = "127.0.0.1"             # só a própria máquina; no Docker o compose define 0.0.0.0
    port: int = 8000
    database_path: str = "data/promo.db"
    config_file: str = "config/config.yaml"
    timezone: str = "America/Sao_Paulo"

    def password_problem(self) -> str | None:
        """Motivo para recusar a senha do painel, ou None se estiver ok."""
        pw = self.panel_password
        if not pw:
            return "PANEL_PASSWORD não definida"
        if pw in WEAK_PASSWORDS or pw.lower() == self.panel_user.lower():
            return "PANEL_PASSWORD é a senha de exemplo ou é igual ao usuário"
        if len(pw) < MIN_PASSWORD_LEN:
            return f"PANEL_PASSWORD precisa ter pelo menos {MIN_PASSWORD_LEN} caracteres"
        return None

    @property
    def token_url(self) -> str:
        return self.amazon_token_url or TOKEN_URLS.get(self.amazon_credential_version, TOKEN_URLS["3.1"])


class Filters(BaseModel):
    """Regras que decidem se uma oferta encontrada vira post. Valem para todas as buscas."""
    min_discount_pct: float = 20
    min_price: float = 0
    max_price: float = 1_000_000
    require_buybox: bool = True
    accept_list_price: bool = True
    cooldown_hours: int = 72
    repost_if_drop_pct: float = 5
    max_posts_per_search: int = 5
    posting_window: tuple[str, str] = ("08:00", "22:30")
    saved_search_every_minutes: int = 60


class Style(BaseModel):
    emoji_price: str = "🔥"
    whatsapp_target: str = ""
    headline_fallbacks: list[str] = Field(default_factory=lambda: ["ACHADO DO DIA"])
    tone: str = "direto e honesto"


class AppConfig(BaseModel):
    filters: Filters = Field(default_factory=Filters)
    style: Style = Field(default_factory=Style)


def clean_category(search_index: str | None, marketplace: str = MARKETPLACE) -> str:
    """Valida a categoria da Amazon (search_index). Vazio = todos os departamentos."""
    idx = (search_index or "All").strip() or "All"
    if not is_valid(idx, marketplace):
        raise ValueError(f"categoria inválida: {idx!r}\nCategorias válidas em {marketplace}:\n{as_table(marketplace)}")
    return idx


def load_config(path: str | Path, marketplace: str | None = None) -> AppConfig:
    global MARKETPLACE
    if marketplace:
        MARKETPLACE = marketplace
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AppConfig(**data)


@lru_cache
def get_settings() -> Settings:
    return Settings()

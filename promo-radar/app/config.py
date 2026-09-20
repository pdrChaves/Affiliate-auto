"""Configuração: variáveis de ambiente (.env) + nichos (YAML)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    max_price_age_minutes: int = 60
    allow_manual_prices: bool = False
    content_retention_hours: int = 24   # Licença: conteúdo de produto (exceto ASIN) no máx. 24h

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    panel_user: str = "admin"
    panel_password: str = "troque-esta-senha"
    database_path: str = "data/promo.db"
    niches_file: str = "config/niches.yaml"
    timezone: str = "America/Sao_Paulo"

    @property
    def token_url(self) -> str:
        return self.amazon_token_url or TOKEN_URLS.get(self.amazon_credential_version, TOKEN_URLS["3.1"])


class SearchSpec(BaseModel):
    keywords: str | None = None
    search_index: str = "All"
    browse_node_id: str | None = None


class Style(BaseModel):
    emoji_price: str = "🔥"
    headline_fallbacks: list[str] = Field(default_factory=lambda: ["OFERTA DO DIA"])
    tone: str = "direto e honesto"


class Niche(BaseModel):
    id: str
    name: str
    whatsapp_target: str = ""
    enabled: bool = True
    collect_every_minutes: int = 60
    min_discount_pct: float = 20
    min_price: float = 0
    max_price: float = 1_000_000
    require_buybox: bool = True
    accept_list_price: bool = True   # aceitar 'De' = preço de tabela (LIST_PRICE)? vem com aviso
    cooldown_hours: int = 72
    repost_if_drop_pct: float = 5
    max_posts_per_run: int = 5
    posting_window: tuple[str, str] = ("08:00", "22:00")
    searches: list[SearchSpec] = Field(default_factory=list)
    watchlist: list[str] = Field(default_factory=list)
    style: Style = Field(default_factory=Style)


def load_niches(path: str | Path) -> list[Niche]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [Niche(**n) for n in data.get("niches", [])]


@lru_cache
def get_settings() -> Settings:
    return Settings()

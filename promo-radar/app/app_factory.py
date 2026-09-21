"""Monta todas as peças a partir da configuração (injeção de dependência simples)."""
from __future__ import annotations

import logging

from .amazon import build_client
from .config import Settings, get_settings, load_niches
from .db import DB
from .pipeline.copywriter import Copywriter
from .senders import build_notifier
from .service import PromoService


def build_service(settings: Settings | None = None) -> PromoService:
    s = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    niches = load_niches(s.niches_file, s.amazon_marketplace)
    svc = PromoService(s, niches, DB(s.database_path), build_client(s), Copywriter(s), build_notifier(s))
    svc.expire_orphan_posts()       # limpa posts de nichos que saíram da configuração
    return svc

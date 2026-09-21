"""Configuração única e categorias da Amazon (não existe mais 'nicho')."""
import pytest

from app.categories import as_table, categories, display_name, is_valid
from app.config import Filters, Style, clean_category, load_config


def test_brazil_has_only_the_official_indexes():
    br = categories("www.amazon.com.br")
    assert len(br) == 10 and br["VideoGames"] == "Games"
    for ausente in ["Fashion", "Toys", "HealthPersonalCare", "SportsAndOutdoors", "Beauty"]:
        assert ausente not in br            # não existem no amazon.com.br
    assert "Electronics" in as_table() and display_name("HomeAndKitchen") == "Casa e Cozinha"


def test_unknown_marketplace_skips_validation():
    assert is_valid("QualquerCoisa", "www.amazon.xx")   # sem catálogo → não valida


def test_clean_category():
    assert clean_category("") == "All" and clean_category(None) == "All"
    assert clean_category("VideoGames") == "VideoGames"
    with pytest.raises(ValueError, match="inválida"):
        clean_category("Fashion")


def test_config_file_loads_filters_and_style():
    cfg = load_config("config/config.yaml", "www.amazon.com.br")
    assert isinstance(cfg.filters, Filters) and isinstance(cfg.style, Style)
    assert cfg.filters.min_discount_pct >= 1 and cfg.filters.max_price > cfg.filters.min_price
    assert len(cfg.filters.posting_window) == 2
    assert cfg.style.headline_fallbacks and cfg.style.emoji_price


def test_defaults_when_file_is_minimal(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("filters:\n  min_discount_pct: 35\n", encoding="utf-8")
    cfg = load_config(f)
    assert cfg.filters.min_discount_pct == 35 and cfg.filters.require_buybox is True
    assert cfg.style.emoji_price == "🔥"

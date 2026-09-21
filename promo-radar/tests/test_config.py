"""Configuração única e categorias da Amazon (não existe mais 'nicho')."""
import pytest

from app.categories import OUTROS, as_table, categories, from_browse_nodes, is_valid, search_index
from app.config import Filters, Style, clean_category, load_config


def test_departments_follow_the_site_menu():
    """Os filtros seguem o menu "Comprar por categoria" do amazon.com.br, não os searchIndex da API."""
    br = categories("www.amazon.com.br")
    assert len(br) == 19
    for nome in ["Pet Shop", "Roupas, Calçados e Acessórios", "Brinquedos e Jogos", "Bebês",
                 "Beleza e Cuidados Pessoais", "Esportes, Aventura e Lazer", "Livros"]:
        assert nome in br, nome
    # departamentos sem searchIndex na API caem em "All" e são filtrados pelo produto
    assert search_index("Livros") == "Books" and search_index("Games e Consoles") == "VideoGames"
    assert search_index("Pet Shop") == "All" and search_index("Roupas, Calçados e Acessórios") == "All"
    assert "Pet Shop" in as_table()


def test_department_comes_from_the_browse_nodes():
    """O departamento sai dos nomes que a Amazon devolve, do mais específico ao mais genérico."""
    assert from_browse_nodes(["Fones de Ouvido", "Eletrônicos"]) == "Eletrônicos, TV e Áudio"
    assert from_browse_nodes(["Ração para Cães", "Pet Shop"]) == "Pet Shop"
    assert from_browse_nodes(["Camisetas", "Moda Masculina"]) == "Roupas, Calçados e Acessórios"
    assert from_browse_nodes(["Livros"]) == "Livros"
    assert from_browse_nodes(["Categoria Que Não Existe"]) == OUTROS   # nunca some do painel
    assert from_browse_nodes([]) == OUTROS


def test_unknown_marketplace_skips_validation():
    assert is_valid("QualquerCoisa", "www.amazon.xx")   # sem catálogo → não valida


def test_clean_category():
    assert clean_category("") == "" and clean_category(None) == ""      # vazio = todos
    assert clean_category("All") == ""                                  # nome antigo vira vazio
    assert clean_category("Games e Consoles") == "Games e Consoles"
    with pytest.raises(ValueError, match="inválida"):
        clean_category("VideoGames")                                    # searchIndex não é departamento


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

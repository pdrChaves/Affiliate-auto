"""Categorias do niches.yaml: a mesma taxonomia da Amazon."""
import pytest
from pydantic import ValidationError

from app.categories import as_table, categories, display_name, is_valid
from app.config import SearchSpec, load_niches


def test_brazil_has_only_the_official_indexes():
    br = categories("www.amazon.com.br")
    assert len(br) == 10 and br["VideoGames"] == "Games"
    for ausente in ["Fashion", "Toys", "HealthPersonalCare", "SportsAndOutdoors", "Beauty"]:
        assert ausente not in br            # não existem no amazon.com.br
    assert "Electronics" in as_table() and display_name("HomeAndKitchen") == "Casa e Cozinha"


def test_unknown_marketplace_skips_validation():
    assert is_valid("QualquerCoisa", "www.amazon.xx")   # sem catálogo → não valida


def test_search_spec_rules():
    SearchSpec(search_index="Electronics", keywords="fone")
    SearchSpec(search_index="VideoGames", browse_node_id="7791985011")
    with pytest.raises(ValidationError, match="inválida"):
        SearchSpec(search_index="Fashion", keywords="tênis")
    with pytest.raises(ValidationError, match="keywords"):
        SearchSpec(search_index="Electronics")
    with pytest.raises(ValidationError, match="browse_node_id exige"):
        SearchSpec(search_index="All", browse_node_id="123")


def test_niches_file_uses_valid_categories():
    niches = load_niches("config/niches.yaml", "www.amazon.com.br")
    assert [n.id for n in niches] == ["eletronicos", "games", "casa"]
    usados = {s.search_index for n in niches for s in n.searches}
    assert usados <= set(categories("www.amazon.com.br"))


def test_broken_niches_file_fails_loudly(tmp_path):
    bad = tmp_path / "n.yaml"
    bad.write_text("niches:\n  - id: x\n    name: X\n    searches:\n      - {search_index: Toys, keywords: lego}\n",
                   encoding="utf-8")
    with pytest.raises(ValidationError, match="Toys"):
        load_niches(bad, "www.amazon.com.br")

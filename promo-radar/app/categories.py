"""Categorias oficiais da Amazon (searchIndex) — a mesma taxonomia do site.

O Brasil (www.amazon.com.br) aceita SÓ estes 10 valores. Categorias comuns em outros países
(Fashion, Toys, HealthPersonalCare, SportsAndOutdoors…) NÃO existem aqui: para esses assuntos,
use `search_index: All` com palavras-chave, ou um browse node da categoria no site.

Fonte: Creators API → Locale Reference → Brazil.
"""
from __future__ import annotations

SEARCH_INDEXES: dict[str, dict[str, str]] = {
    "www.amazon.com.br": {
        "All": "Todos os departamentos",
        "Books": "Livros",
        "Computers": "Computadores e Informática",
        "Electronics": "Eletrônicos",
        "HomeAndKitchen": "Casa e Cozinha",
        "KindleStore": "Loja Kindle",
        "MobileApps": "Apps e Jogos",
        "OfficeProducts": "Material para Escritório e Papelaria",
        "ToolsAndHomeImprovement": "Ferramentas e Materiais de Construção",
        "VideoGames": "Games",
    },
}


def categories(marketplace: str) -> dict[str, str]:
    """Categorias do marketplace. Marketplace não catalogado aqui → {} (validação desligada)."""
    return SEARCH_INDEXES.get(marketplace, {})


def is_valid(search_index: str, marketplace: str) -> bool:
    cats = categories(marketplace)
    return not cats or search_index in cats


def display_name(search_index: str, marketplace: str = "www.amazon.com.br") -> str:
    return categories(marketplace).get(search_index, search_index)


def as_table(marketplace: str = "www.amazon.com.br") -> str:
    return "\n".join(f"  {k:<26} {v}" for k, v in categories(marketplace).items())

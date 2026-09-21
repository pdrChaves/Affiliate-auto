"""Departamentos do amazon.com.br — os mesmos do menu "Comprar por categoria" do site.

Duas taxonomias convivem na mesma loja, e é importante não confundi-las:

1. DEPARTAMENTO (o que você vê no site e o que filtramos no painel). Vem do próprio produto:
   a API devolve em `browseNodeInfo` a árvore de categorias onde a Amazon o colocou, com o nome
   já em português. É isso que grava `Offer.category`.

2. `searchIndex` (o que a API aceita PARA BUSCAR). O Brasil só aceita 10 valores. Não há
   `searchIndex` para Pet Shop, Roupas, Brinquedos, Beleza, Esportes e outros: nesses casos a
   busca vai em "All" e o resultado é filtrado pelo departamento que a Amazon devolveu.

Fonte dos searchIndex: Creators API → Locale Reference → Brazil.
"""
from __future__ import annotations

import unicodedata

OUTROS = "Outros"

# Departamentos do menu do site, na ordem em que aparecem lá.
# valor = searchIndex equivalente na API, ou "" quando a API não tem um (busca cai em All).
DEPARTAMENTOS: dict[str, dict[str, str]] = {
    "www.amazon.com.br": {
        "Alimentos e Bebidas": "",
        "Automotivo": "",
        "Bebês": "",
        "Beleza e Cuidados Pessoais": "",
        "Bolsas, Malas e Mochilas": "",
        "Brinquedos e Jogos": "",
        "Casa, Jardim e Limpeza": "HomeAndKitchen",
        "Celulares e Comunicação": "Electronics",
        "Computadores e Informática": "Computers",
        "Cozinha": "HomeAndKitchen",
        "Eletrônicos, TV e Áudio": "Electronics",
        "Esportes, Aventura e Lazer": "",
        "Ferramentas e Construção": "ToolsAndHomeImprovement",
        "Filmes, Séries e Música": "",
        "Games e Consoles": "VideoGames",
        "Livros": "Books",
        "Papelaria e Escritório": "OfficeProducts",
        "Pet Shop": "",
        "Roupas, Calçados e Acessórios": "",
    },
}

# Como o nome que a Amazon devolve no browse node vira um dos departamentos acima.
# A chave é o nome normalizado (sem acento, minúsculo); comparamos por "começa com" e por
# "contém", então variações como "Eletrônicos" ou "Eletrônicos e Tecnologia" caem no mesmo lugar.
APELIDOS: dict[str, str] = {
    "alimentos": "Alimentos e Bebidas", "mercearia": "Alimentos e Bebidas",
    "bebidas": "Alimentos e Bebidas", "grocery": "Alimentos e Bebidas",
    "automotivo": "Automotivo", "automotive": "Automotivo", "carro": "Automotivo",
    "bebe": "Bebês", "baby": "Bebês",
    "beleza": "Beleza e Cuidados Pessoais", "cuidados pessoais": "Beleza e Cuidados Pessoais",
    "saude": "Beleza e Cuidados Pessoais", "perfumaria": "Beleza e Cuidados Pessoais",
    "bolsas": "Bolsas, Malas e Mochilas", "malas": "Bolsas, Malas e Mochilas",
    "mochilas": "Bolsas, Malas e Mochilas", "luggage": "Bolsas, Malas e Mochilas",
    "brinquedos": "Brinquedos e Jogos", "toys": "Brinquedos e Jogos", "jogos e brinquedos": "Brinquedos e Jogos",
    "casa": "Casa, Jardim e Limpeza", "jardim": "Casa, Jardim e Limpeza",
    "limpeza": "Casa, Jardim e Limpeza", "moveis": "Casa, Jardim e Limpeza",
    "ferramentas e jardim": "Casa, Jardim e Limpeza",
    "celulares": "Celulares e Comunicação", "smartphone": "Celulares e Comunicação",
    "comunicacao": "Celulares e Comunicação",
    "informatica": "Computadores e Informática", "computadores": "Computadores e Informática",
    "computers": "Computadores e Informática",
    "cozinha": "Cozinha", "kitchen": "Cozinha", "eletroportateis": "Cozinha",
    "eletronicos": "Eletrônicos, TV e Áudio", "electronics": "Eletrônicos, TV e Áudio",
    "tv": "Eletrônicos, TV e Áudio", "audio": "Eletrônicos, TV e Áudio",
    "esportes": "Esportes, Aventura e Lazer", "sports": "Esportes, Aventura e Lazer",
    "aventura": "Esportes, Aventura e Lazer", "lazer": "Esportes, Aventura e Lazer",
    "ferramentas": "Ferramentas e Construção", "construcao": "Ferramentas e Construção",
    "tools": "Ferramentas e Construção", "materiais de construcao": "Ferramentas e Construção",
    "filmes": "Filmes, Séries e Música", "series": "Filmes, Séries e Música",
    "musica": "Filmes, Séries e Música", "dvd": "Filmes, Séries e Música", "cd": "Filmes, Séries e Música",
    "games": "Games e Consoles", "video games": "Games e Consoles", "videogames": "Games e Consoles",
    "consoles": "Games e Consoles",
    "livros": "Livros", "books": "Livros", "kindle": "Livros", "ebooks": "Livros",
    "papelaria": "Papelaria e Escritório", "escritorio": "Papelaria e Escritório",
    "office": "Papelaria e Escritório", "material para escritorio": "Papelaria e Escritório",
    "pet": "Pet Shop", "petshop": "Pet Shop", "produtos para animais": "Pet Shop",
    "animais": "Pet Shop",
    "roupas": "Roupas, Calçados e Acessórios", "moda": "Roupas, Calçados e Acessórios",
    "calcados": "Roupas, Calçados e Acessórios", "acessorios": "Roupas, Calçados e Acessórios",
    "vestuario": "Roupas, Calçados e Acessórios", "fashion": "Roupas, Calçados e Acessórios",
    "apps": "Games e Consoles", "aplicativos": "Games e Consoles",
}


def _normaliza(texto: str) -> str:
    """Minúsculo, sem acento — para comparar "Eletrônicos" com "eletronicos"."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def categories(marketplace: str) -> dict[str, str]:
    """Departamentos do marketplace {nome: searchIndex}. Não catalogado → {} (validação desligada)."""
    return DEPARTAMENTOS.get(marketplace, {})


def names(marketplace: str = "www.amazon.com.br") -> list[str]:
    return list(categories(marketplace))


def is_valid(departamento: str, marketplace: str = "www.amazon.com.br") -> bool:
    cats = categories(marketplace)
    return not cats or departamento in cats or departamento == OUTROS


def search_index(departamento: str, marketplace: str = "www.amazon.com.br") -> str:
    """searchIndex que a API aceita para esse departamento, ou "All" quando não existe um."""
    return categories(marketplace).get(departamento) or "All"


def from_browse_nodes(nomes: list[str], marketplace: str = "www.amazon.com.br") -> str:
    """Departamento a partir dos nomes que a Amazon devolveu em browseNodeInfo.

    Recebe os nomes do mais específico ao mais genérico (a "escada" de ancestrais). Devolve o
    primeiro que reconhecer; nada reconhecido vira "Outros" — o produto continua aparecendo no
    painel, só sem departamento, em vez de sumir do filtro.
    """
    validos = categories(marketplace)
    for nome in nomes:
        if nome in validos:                       # a Amazon devolveu exatamente o nome do menu
            return nome
        alvo = _normaliza(nome)
        for apelido, dep in APELIDOS.items():
            if alvo == apelido or alvo.startswith(apelido + " ") or f" {apelido}" in f" {alvo}":
                return dep
    return OUTROS


def as_table(marketplace: str = "www.amazon.com.br") -> str:
    linhas = []
    for nome, idx in categories(marketplace).items():
        linhas.append(f"  {nome:<32} {idx or 'busca em Todos os departamentos'}")
    return "\n".join(linhas)

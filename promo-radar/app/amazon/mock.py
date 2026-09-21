"""Fonte simulada: gera respostas NO FORMATO do Creators API e passa pelo mesmo parser.
Serve para rodar o sistema inteiro sem credenciais (dev/demonstração/testes)."""
from __future__ import annotations

import random
import unicodedata

from ..config import Settings
from .creators import parse_item

# (asin, título, preço "De", preço atual base, [nó do produto, departamento raiz], flags)
# O departamento raiz imita o que a Amazon devolve em browseNodeInfo: é DELE que sai a categoria.
CATALOG = [
    ("B0MOCK0001", "Fone de Ouvido Bluetooth JBL Tune 520BT", 399.00, 219.00,
     ["Fones de Ouvido", "Eletrônicos"], {}),
    ("B0MOCK0002", "Smartwatch Amazfit Bip 6 GPS", 599.00, 429.00,
     ["Smartwatches", "Eletrônicos"], {}),
    ("B0MOCK0003", "Caixa de Som Bluetooth JBL Go 4", 249.90, 229.90,
     ["Caixas de Som", "Eletrônicos"], {}),                                    # desconto baixo
    ("B0MOCK0004", "Fone de Ouvido Bluetooth Xiaomi Redmi Buds 6", 299.00, 169.90,
     ["Fones de Ouvido", "Eletrônicos"], {}),
    ("B0MOCK0005", "Smartwatch Xiaomi Watch S4", 899.99, 559.99,
     ["Smartwatches", "Eletrônicos"], {"stock": False}),
    ("B0MOCK0006", "Caixa de Som Bluetooth Sony SRS-XB100", 449.00, 249.00,
     ["Caixas de Som", "Eletrônicos"], {"buybox": False}),
    ("B0MOCK0007", "Smart TV LG 50\" 4K UHD", 2799.00, 1899.00,
     ["Televisores", "Eletrônicos"], {}),
    ("B0MOCK0101", "Headset Gamer HyperX Cloud Stinger 2", 349.99, 212.36,
     ["Headsets", "Games"], {}),
    ("B0MOCK0102", "Controle Sem Fio DualSense PS5", 489.99, 316.89,
     ["Controles", "Games"], {}),
    ("B0MOCK0103", "Headset Gamer Logitech G435", 399.99, 359.99,
     ["Headsets", "Games"], {}),                                               # desconto baixo
    ("B0MOCK0104", "Teclado Mecânico Redragon Kumara K552", 229.99, 144.49,
     ["Teclados", "Computadores e Informática"], {"deal": "Oferta Relâmpago"}),
    ("B0MOCK0105", "Mouse Gamer Logitech G203 Lightsync", 179.90, 99.90,
     ["Mouses", "Computadores e Informática"], {}),
    ("B0MOCK0106", "SSD Kingston NV3 1TB NVMe", 599.00, 349.00,
     ["Armazenamento", "Computadores e Informática"], {}),
    ("B0MOCK0201", "Air Fryer Mondial 4L AFN-40", 399.00, 249.00,
     ["Fritadeiras", "Cozinha"], {}),
    ("B0MOCK0202", "Robô Aspirador Xiaomi E10", 1499.00, 899.00,
     ["Aspiradores", "Casa, Jardim e Limpeza"], {}),
    ("B0MOCK0203", "Jogo de Panelas Tramontina 5 Peças", 549.00, 329.00,
     ["Panelas", "Cozinha"], {}),
    ("B0MOCK0301", "O Homem Mais Rico da Babilônia", 44.90, 24.90,
     ["Finanças Pessoais", "Livros"], {}),
    ("B0MOCK0302", "Box Trilogia O Senhor dos Anéis", 189.90, 109.90,
     ["Ficção Científica e Fantasia", "Livros"], {}),
    ("B0MOCK0401", "Ração Golden Special Cães Adultos 15kg", 189.90, 129.90,
     ["Ração para Cães", "Pet Shop"], {}),
    ("B0MOCK0402", "Arranhador para Gatos com Plataforma", 159.90, 89.90,
     ["Arranhadores", "Pet Shop"], {}),
    ("B0MOCK0501", "Tênis Olympikus Corre 3 Masculino", 349.99, 199.99,
     ["Tênis de Corrida", "Roupas, Calçados e Acessórios"], {}),
    ("B0MOCK0502", "Camiseta Básica Algodão Pima", 129.90, 69.90,
     ["Camisetas", "Roupas, Calçados e Acessórios"], {}),
    ("B0MOCK0601", "LEGO Star Wars Millennium Falcon 75375", 499.99, 329.99,
     ["Blocos de Montar", "Brinquedos e Jogos"], {"deal": "Oferta do Dia"}),
    ("B0MOCK0602", "Jogo de Tabuleiro Banco Imobiliário", 149.90, 89.90,
     ["Jogos de Tabuleiro", "Brinquedos e Jogos"], {}),
    ("B0MOCK0701", "Mochila Notebook Samsonite 15.6\"", 399.00, 229.00,
     ["Mochilas", "Bolsas, Malas e Mochilas"], {}),
    ("B0MOCK0702", "Mala de Viagem Média ABS 24 Polegadas", 549.00, 299.00,
     ["Malas de Viagem", "Bolsas, Malas e Mochilas"], {}),
    ("B0MOCK0801", "Whey Protein Concentrado 900g", 189.90, 119.90,
     ["Suplementos", "Esportes, Aventura e Lazer"], {}),
    ("B0MOCK0802", "Kit Halteres Ajustáveis 20kg", 499.00, 319.00,
     ["Musculação", "Esportes, Aventura e Lazer"], {}),
    ("B0MOCK0901", "Perfume Masculino Natura Essencial 100ml", 229.90, 149.90,
     ["Perfumes", "Beleza e Cuidados Pessoais"], {}),
    ("B0MOCK0902", "Barbeador Elétrico Philips Série 3000", 349.00, 199.00,
     ["Barbeadores", "Beleza e Cuidados Pessoais"], {}),
    ("B0MOCK1001", "Cafeteira Nespresso Inissia", 599.00, 349.00,
     ["Cafeteiras", "Cozinha"], {}),
    ("B0MOCK1002", "Café em Grãos Especial 1kg", 89.90, 54.90,
     ["Café", "Alimentos e Bebidas"], {}),
    ("B0MOCK1101", "Kit Limpeza Automotiva Vonixx", 199.90, 119.90,
     ["Cuidados com o Veículo", "Automotivo"], {}),
    ("B0MOCK1102", "Câmera de Ré Veicular HD", 189.00, 99.00,
     ["Eletrônicos Automotivos", "Automotivo"], {}),
    ("B0MOCK1201", "Fralda Pampers Premium Care M 80un", 129.90, 79.90,
     ["Fraldas", "Bebês"], {}),
    ("B0MOCK1301", "Smartphone Motorola Moto G84 256GB", 1799.00, 1099.00,
     ["Smartphones", "Celulares e Comunicação"], {}),
    ("B0MOCK1302", "Capa Antichoque para iPhone 15", 89.90, 39.90,
     ["Capas e Cases", "Celulares e Comunicação"], {}),
    ("B0MOCK1401", "Furadeira de Impacto Bosch GSB 550W", 399.00, 259.00,
     ["Furadeiras", "Ferramentas e Construção"], {}),
    ("B0MOCK1501", "Caderno Inteligente Smart Universitário", 119.90, 69.90,
     ["Cadernos", "Papelaria e Escritório"], {}),
    ("B0MOCK1601", "Vinil O Lado Escuro da Lua - Pink Floyd", 299.00, 189.00,
     ["Discos de Vinil", "Filmes, Séries e Música"], {}),
]


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()


def _browse_node_info(caminho):
    """Monta browseNodeInfo como a API devolve: nó específico + escada de ancestrais até a raiz."""
    no = {"id": "0", "displayName": caminho[-1], "contextFreeName": caminho[-1], "isRoot": True}
    for nome in reversed(caminho[:-1]):
        no = {"id": "0", "displayName": nome, "contextFreeName": nome, "isRoot": False, "ancestor": no}
    return {"browseNodes": [no], "websiteSalesRank": {"displayName": caminho[-1], "salesRank": 1}}


def _raw(asin, title, basis, price, flags, caminho=("Eletrônicos",)):
    pct = round(100 * (basis - price) / basis)  # a Amazon arredonda o percentual
    return {
        "asin": asin,
        "detailPageURL": f"https://www.amazon.com.br/dp/{asin}",
        "itemInfo": {"title": {"displayValue": title},
                     "features": {"displayValues": ["Produto de exemplo (modo mock)"]}},
        "images": {"primary": {"large": {"url": None}}},
        "browseNodeInfo": _browse_node_info(list(caminho)),
        "offersV2": {"listings": [{
            "availability": {"type": "IN_STOCK" if flags.get("stock", True) else "OUT_OF_STOCK"},
            "condition": {"value": "New"},
            "isBuyBoxWinner": flags.get("buybox", True),
            "merchantInfo": {"name": "Amazon.com.br"},
            "dealDetails": {"badge": flags["deal"]} if "deal" in flags else None,
            "price": {
                "money": {"amount": price, "currency": "BRL"},
                "savingBasis": {"money": {"amount": basis, "currency": "BRL"}, "savingBasisType": "WAS_PRICE"},
                "savings": {"money": {"amount": round(basis - price, 2)}, "percentage": pct},
            },
        }]},
    }


class MockClient:
    def __init__(self, settings: Settings, jitter: float = 0.0, seed: int | None = None):
        self.s = settings
        self.jitter = jitter              # variação aleatória de preço (simula mercado)
        self.rng = random.Random(seed)  # nosec B311 (só simula variação de preço)
        self.overrides: dict[str, float] = {}   # testes podem forçar preço

    def _offers(self, rows):
        out = []
        for asin, title, basis, price, caminho, flags in rows:
            p = self.overrides.get(asin, price)
            if self.jitter:
                p = round(p * (1 + self.rng.uniform(-self.jitter, self.jitter)), 2)
            out.append(parse_item(_raw(asin, title, basis, p, flags, caminho), self.s.amazon_marketplace,
                                  self.s.amazon_partner_tag))
        return out

    def get_items(self, asins, fast=False):
        return self._offers([r for r in CATALOG if r[0] in asins])

    def search(self, keywords=None, search_index="All", browse_node_id=None, min_saving_pct=None,
               min_price_cents=None, max_price_cents=None, pages=1):
        words = [_sem_acento(w) for w in (keywords or "").lower().split() if len(w) > 3]
        # O mock não filtra por searchIndex: quem restringe por departamento é o serviço, olhando
        # a categoria que saiu do browseNodeInfo — exatamente como acontece com a API real.
        # O termo casa com o título OU com a categoria do produto, como faz a busca do site
        # (pesquisar "livro" traz livros, mesmo que a palavra não esteja no título).
        rows = [r for r in CATALOG
                if not words or any(w in _sem_acento(r[1] + " " + " ".join(r[4])) for w in words)]
        return self._offers(rows)

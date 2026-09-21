# Contexto Compactado — Promo Radar (automação de promoções Amazon → WhatsApp)

**Data da compactação:** 21/09/2026
**Chat original:** ~120 mensagens (inclui uma compactação anterior do início da conversa)
**Versão atual do sistema:** v1.4

---

## 1. Objetivo

Automação que busca promoções na Amazon, valida o desconto, monta o post no formato das comunidades de WhatsApp (descrição + "De x Por") com link de afiliado, e deixa pronto para envio com 1 clique. Entregáveis exigidos pelo usuário: relatório detalhado do sistema, conformidade com os termos (Amazon, WhatsApp, CONAR/CDC), e capacidade de evoluir após a v1.

**Local do projeto:** `C:\PROGRAMAÇÃO\PESSOAL\Promocao-afiliado\promo-radar\` (repo git, remoto `github.com/pdrChaves/Affiliate-auto.git`, branch `main`)

---

## 2. Decisões Tomadas

### Produto e conformidade

- **Envio no WhatsApp é MANUAL (1 clique via `wa.me/?text=`):** os Termos do WhatsApp proíbem "bulk/auto-messaging" por contas comuns (Baileys, whatsapp-web.js, Evolution API = banimento do número). A Groups API oficial aceita no máximo 8 participantes e não fala com Comunidades. Camada de envio isolada em `app/senders/` para troca futura.
- **Imagem nunca é baixada:** o card de pré-visualização do WhatsApp busca a imagem direto da Amazon. A Licença proíbe armazenar imagens.
- **Cache de conteúdo ≤ 24h** (`CONTENT_RETENTION_HOURS=24`, não aumentar): só o ASIN pode ser guardado indefinidamente. Por isso **não existe histórico próprio de preços**.
- **`#publi` na primeira linha** (CONAR 2026) + carimbo de horário e aviso "preço pode mudar" (CDC art. 37).
- **Preço revalidado no clique de Enviar** (`MAX_PRICE_AGE_MINUTES=0`): o post só sai com a promoção ativa. Se caiu, envio bloqueado e post expira.
- **Monitor pós-envio DESLIGADO por padrão** (`MONITOR_SENT_ENABLED=false`): o usuário disse explicitamente que não quer ser avisado quando a promoção acabar — só quer postar enquanto está ativa.
- **`ALLOW_MANUAL_PRICES=false`:** preço digitado à mão viola a letra da política (não foi "obtido pela API"). Ligar é decisão do usuário, com risco de encerramento da conta de Associado.
- **Senha mínima 8 caracteres** (usuário pediu; era 12).

### API Amazon

- **Creators API** substituiu a PA-API 5 (desligada em 30/04/2026 e aposentada em 15/05/2026).
- OAuth2 `client_credentials`, Basic auth, escopo `creatorsapi::default` (LWA v3.x) / `creatorsapi/default` (Cognito v2.x).
- **Brasil = grupo North America → credencial versão 3.1**, token em `https://api.amazon.com/auth/o2/token`.
- Base: `https://creatorsapi.amazon/catalog/v1`; payloads lowerCamelCase; `getItems` (10 ASINs/chamada), `searchItems`.
- Preços em `offersV2.listings[].price.{money, savingBasis, savings}`.
- **Exige 10 vendas qualificadas nos últimos 30 dias** para liberar o acesso.

### Arquitetura de conteúdo (evoluiu 3 vezes — leia a ordem)

1. **v1.0–v1.2 — "nichos":** lista fixa em `config/niches.yaml`, cada nicho com buscas e filtros próprios. **REMOVIDO.**
2. **v1.3 — barra de pesquisa:** usuário pesquisa um termo ("teclado", "mochila"), vê o resultado já avaliado com veredito por item, e escolhe o que entra na fila. Buscas salvas rodam sozinhas. Config única em `config/config.yaml`.
3. **v1.4 — departamentos do site:** os filtros do painel passam a ser os **19 departamentos do menu "Comprar por categoria" do amazon.com.br**, e **a categoria de cada produto vem do próprio produto** (`browseNodeInfo` da API), nunca da caixa de seleção da busca.

### Duas taxonomias (ponto que causou dois erros — não confundir)

| | O que é | Onde aparece | Quantos no BR |
|---|---|---|---|
| **Departamento** | Onde a Amazon colocou o produto | Menu do site; **filtros do painel** | 19 |
| `searchIndex` | Recorte que a API aceita **para buscar** | Só na chamada `searchItems` | 10 |

Os 10 `searchIndex` válidos no amazon.com.br (confirmado na Locale Reference do Creators API): `All, Books, Computers, Electronics, HomeAndKitchen, KindleStore, MobileApps, OfficeProducts, ToolsAndHomeImprovement, VideoGames`. **`Fashion`, `Toys`, `HealthPersonalCare`, `SportsAndOutdoors`, `Beauty`, `PetSupplies` NÃO existem no BR.**

Na busca: departamento com `searchIndex` equivalente usa ele; os outros 9 (Pet Shop, Roupas, Brinquedos, Beleza, Esportes, Bebês, Automotivo, Alimentos, Filmes) buscam em `All` e o resultado é filtrado pelo departamento do produto.

### Stack

- **Python 3.11+**, **FastAPI + Jinja2** server-rendered (sem build de front), **SQLite** (WAL, índice único parcial, JSON1), **APScheduler** no mesmo processo, **httpx** (MockTransport nos testes), **pydantic-settings**, **Docker** (não-root uid 10001, `read_only`, `no-new-privileges`), `requirements.lock` com 29 pacotes fixados.
- IA (Claude API) **opcional e só para a chamada do post**, com validação; sem chave usa `headline_fallbacks` do `config.yaml`. IA nunca escreve o post inteiro (alteraria dados do produto = proibido).
- Windows: `uvloop` com marcador `; sys_platform != "win32"` (não tem wheel) e `tzdata==2026.2` (Python no Windows não traz tz database).

### Segurança

Login por sessão (cookie `HttpOnly`/`SameSite=Strict`), token CSRF por sessão, checagem de `Origin`/`Referer` **ignorando `"null"`**, bloqueio por IP (5 falhas / 15 min → 429), CSP estrita sem JS/CSS inline, `/docs` desativado, corpo limitado a 64 KB, `HOST=127.0.0.1`.

### Desempenho

- `COUNT_CAP = 5000`: contagens do rodapé e dos chips param em 5.000 linhas varridas e a tela escreve `5000+`. Sem isso o filtro de texto (`LIKE '%termo%'`, que nenhum índice resolve) varria a tabela inteira.
- Total do rodapé **derivado da contagem dos chips** (uma varredura em vez de duas).
- Índices: `ux_posts_active` (único parcial em `posts(asin)` para pending/approved), `ix_posts_rank (status, score, created_at)`, `ix_posts_cat_rank (category, status, score, created_at)`, `ix_posts_created (purged, created_at)`.
- `count_by_category` sem termo usa `INDEXED BY ix_posts_cat_rank` (covering index, sem B-tree temporária).

### Painel

- Ações nos cards (chamada, cupom, aprovar, descartar) via `fetch` → **JSON, sem recarregar a página**, preservando filtros. Fallback por redirect para quem estiver sem JavaScript (`wants_json()` checa `X-Requested-With: fetch`).
- Filtros: chips dos 19 departamentos com contagem + campo de texto sobre a fila.
- Paginação de 30 em 30.

---

## 3. Estado Atual

**Tudo funcionando e gravado na máquina do usuário.** Versão v1.4.

| Métrica | Valor |
|---|---|
| Testes | **92 passando** (~5,5s), 15/15 execuções estáveis |
| Cobertura (linha + branch) | **93%** |
| ruff / mypy strict / bandit / pip-audit | **0 apontamentos** |
| Complexidade média (radon) | A (3,00), 202 blocos |
| Segurança (DAST `qa/dast.py`) | **18/18** |
| Falhas/resiliência (`qa/avail.py`) | **12/12** |
| Soak 2 min | 45.342 req, 0 erros, RSS 65,6 → 69,7 MB (sem vazamento) |

Verificado no navegador: 19 chips com contagens corretas; `ração`→Pet Shop 1, `perfume`→Beleza 1, `livro`→Livros 2, `teclado` em Livros 0 (correto).

**O que NÃO foi testado:** chamadas reais ao Creators API (sem credenciais — usuário ainda não tem as 10 vendas) e o build do Docker (registro de imagens bloqueado no ambiente).

---

## 4. Arquivos e Artefatos Relevantes

| Arquivo | Status | Descrição |
|---------|--------|-----------|
| `app/categories.py` | Reescrito v1.4 | 19 departamentos + `APELIDOS` (browse node → departamento) + `search_index()` + `from_browse_nodes()` + `OUTROS` |
| `app/config.py` | Editado | `Settings`, `Filters`, `Style`, `AppConfig`, `clean_category()` (agora valida departamento; vazio = todos), `MIN_PASSWORD_LEN=8` |
| `app/db.py` | Editado | Tabelas `posts`, `searches`, `watchlist`, `runs`; `COUNT_CAP`; `SEARCH_INDEX_ANTIGO`; `_migrate()` |
| `app/service.py` | Reescrito v1.3 | `search()`, `queue_asin()`, `queue_offer()`, `run_search()`, `save_search()`, `run_saved_searches()`, `prepare_send()`, `monitor_sent()`, `housekeeping()` |
| `app/amazon/creators.py` | Editado | Cliente OAuth2 + `parse_item()` + `browse_names()`; `RESOURCES` inclui `browseNodeInfo.*` |
| `app/amazon/mock.py` | Reescrito | **40 produtos** cobrindo os 19 departamentos, com `browseNodeInfo` no formato real |
| `app/web/server.py` | Editado | Rotas do painel; `View`/`view_form`/`view_query`/`ViewDep`/`ViewQuery` |
| `app/web/templates/buscar.html` | Criado v1.3 | Tela de resultados da pesquisa com veredito + prévia |
| `app/web/templates/index.html` | Editado | Barra de pesquisa, chips, filtro de texto, cards `class="js"` |
| `app/web/static/app.js` | Editado | Submit via fetch, `pinta()`, `contadores()`, copiar para área de transferência |
| `config/config.yaml` | Criado v1.3 | Arquivo único: `filters:` + `style:` (substituiu `config/niches.yaml`, já deletado no git) |
| `tests/` (10 arquivos) | Editados | 92 testes |
| `qa/dast.py`, `qa/avail.py`, `qa/load.py`, `qa/volume.py` | Editados | Baterias de QA; resultados brutos em `qa/results/` |
| `RELATORIO.md` | Atualizado v1.4 | Relatório técnico completo (16 seções) |
| `RELATORIO_QA_v3.md` | Criado | Avaliação v1.3/v1.4 (inclui seção 2.1 sobre o defeito achado em uso) |
| `RELATORIO_QA.md`, `RELATORIO_QA_v2.md` | Históricos | Avaliações v1.0 e v1.1 |
| `FLUXOGRAMA.md`, `docs/fluxograma.html` | Atualizados v1.4 | Mermaid + versão visual (gerador em `/home/claude/flow/gen.py`) |
| `iniciar.ps1`, `testar.ps1` | Criados | Na pasta **pai** (`Promocao-afiliado\`), resolvem o problema recorrente de pasta errada |
| Artifact do fluxograma | v4 publicada | `https://claude.ai/artifact/2FGmGUDQMXtwpQjHUMBhia` |

---

## 5. Código e Configurações Críticas

### Rodar no Windows (o usuário errou isso 3 vezes)

```powershell
# de qualquer lugar, a partir da pasta Promocao-afiliado:
.\iniciar.ps1

# manualmente:
cd "C:\PROGRAMAÇÃO\PESSOAL\Promocao-afiliado\promo-radar"
Set-ExecutionPolicy -Scope Process RemoteSigned   # se bloquear scripts
.\.venv\Scripts\Activate.ps1                       # NÃO é .\.env\activate.ps1
python -m app.cli serve                            # http://127.0.0.1:8000
```

### CLI

```
python -m app.cli serve
python -m app.cli categorias
python -m app.cli buscar "teclado mecânico" "Computadores e Informática"
python -m app.cli salvas | monitor | purge | preview
```

### Migração automática do banco (`app/db.py::_migrate`)

Idempotente. Ordem importa: **derruba os índices de `posts` antes do `DROP COLUMN niche_id`**, senão o SQLite recusa.

```python
if "niche_id" in cols:
    self._conn.execute("UPDATE posts SET query = COALESCE(query, niche_id)")
    for (nome,) in self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='posts' "
            "AND name NOT LIKE 'sqlite_autoindex%'").fetchall():
        self._conn.execute(f'DROP INDEX IF EXISTS "{nome}"')
    self._conn.execute("ALTER TABLE posts DROP COLUMN niche_id")
```

Depois do `executescript(SCHEMA)`, traduz categorias antigas:

```python
SEARCH_INDEX_ANTIGO = {
    "All": "", "Books": "Livros", "Computers": "Computadores e Informática",
    "Electronics": "Eletrônicos, TV e Áudio", "HomeAndKitchen": "Casa, Jardim e Limpeza",
    "KindleStore": "Livros", "MobileApps": "Games e Consoles",
    "OfficeProducts": "Papelaria e Escritório",
    "ToolsAndHomeImprovement": "Ferramentas e Construção", "VideoGames": "Games e Consoles",
}
```

### Categoria vem do produto (`app/amazon/creators.py`)

```python
def browse_names(item: dict) -> list[str]:
    """Nomes de categoria da Amazon, do mais específico ao mais genérico."""
    saida = []
    for node in _g(item, "browseNodeInfo", "browseNodes") or []:
        atual = node
        for _ in range(12):
            if not isinstance(atual, dict):
                break
            for campo in ("displayName", "contextFreeName"):
                nome = _g(atual, campo)
                if isinstance(nome, str) and nome and nome not in saida:
                    saida.append(nome)
            atual = _g(atual, "ancestor")
    topo = _g(item, "browseNodeInfo", "websiteSalesRank", "displayName")
    if isinstance(topo, str) and topo and topo not in saida:
        saida.append(topo)
    return saida

# em parse_item(): category=from_browse_nodes(browse_names(item), marketplace)
```

`RESOURCES` precisa incluir: `browseNodeInfo.browseNodes`, `browseNodeInfo.browseNodes.ancestor`, `browseNodeInfo.websiteSalesRank`.

### Formato do post

```
#publi · link de afiliado Amazon

*CHAMADA EM CAIXA ALTA*

Título do produto

~De R$ 599,00~
*Por R$ 316,00* 🔥 (-47%)
⚡ Oferta Relâmpago                 ← só se a Amazon informar selo
🎟️ Cupom: *MELHORCUPOM*            ← só se preenchido

🛒 Compre aqui:
https://www.amazon.com.br/dp/B0XXXXXXX?tag=seutag-20

📦 Vendido por Amazon.com.br
🕒 Preço verificado em 19/09 às 14:32. Preço e disponibilidade podem mudar.
```

### Rotinas automáticas

| Rotina | Frequência |
|---|---|
| `buscas_salvas` (+ watchlist) | `saved_search_every_minutes` (60) |
| `monitor_sent` | 1h — **desligado por padrão** |
| `expire_stale_queue` | 30 min |
| `purge_content` | 1h |
| `housekeeping` | diário 04:10 (`VACUUM` aos domingos) |

Trava no serviço: rodada concorrente é pulada e registrada como `skipped`.

---

## 6. Erros e Armadilhas Conhecidas

### Bugs corrigidos (não reintroduzir)

- **Categoria vinha do filtro, não do produto.** `Offer.category = search_index escolhido` → pesquisar em "Todos os departamentos" gravava `All` em tudo, deixando todo post sem categoria. Corrigido: vem de `browseNodeInfo`.
- **Teste que fornecia o dado derivado.** O teste antigo enfileirava passando a categoria na mão e conferia que ela voltava — testava o que o código fazia, não o que deveria fazer. **Regra adotada: onde o dado é derivado, o teste não pode fornecê-lo pronto.**
- **`DROP COLUMN niche_id` falhava** com `error in index ix_posts_asin after drop column`: o SQLite recusa enquanto um índice citar a coluna (os da v1 eram `(niche_id, asin)`). O `V1_SCHEMA` do teste não criava índices, por isso passou verde e quebrou no banco real.
- **Categorias inventadas** (`Fashion`, `Toys`, `HealthPersonalCare`) que não existem no amazon.com.br — o usuário pegou.
- **"Origem não permitida" no login:** `Referrer-Policy: no-referrer` fazia o Chrome/Edge mandar `Origin: null`, e a checagem rejeitava. Corrigido para `same-origin` + ignorar `"null"` (CSRF + SameSite=Strict continuam protegendo).
- **422 em todo POST** após refatoração: `view_form`/`view_query`/`ViewDep`/`ViewQuery` foram apagados por uma substituição em bloco; o FastAPI tratou `View` como query model. Devem ficar **antes** de `router = APIRouter()`.
- **`# noqa: S608` injetado dentro de strings SQL triplas** quebrou as queries. Usar `per-file-ignores` no pyproject ou `# nosec` numa linha só.
- **`json_each` no filtro de status matou o uso do índice** (15,8 → 40 ms). Voltou para `IN (?,?)` + consulta em 2 etapas (ids pelo índice, depois as linhas) → 2,4 ms.
- **Regressão de desempenho v1.3:** filtros novos sem índice. Filtro por categoria 33,4 → 1,3 ms; contagem dos chips 19,5 → 6,3 ms; painel com filtro de texto 14,3 → 32,7 req/s.

### Ambiente do usuário (Windows)

- **Ele abre o terminal em `Promocao-afiliado` em vez de `promo-radar`** — aconteceu 3 vezes. Daí os scripts `iniciar.ps1`/`testar.ps1` na pasta pai. Solução de raiz: abrir o VS Code direto em `promo-radar`.
- Digitou `.\.env\activate.ps1` (arquivo de config) em vez de `.\.venv\Scripts\Activate.ps1` (ambiente).
- PowerShell bloqueia scripts: `Set-ExecutionPolicy -Scope Process RemoteSigned`.
- Sair do venv: `deactivate`.
- `mcp__remote-devices__device_commit_files` **não apaga arquivos** na máquina dele — deleções precisam ser pedidas ao usuário.

### Limitações aceitas

- **Sem histórico de preços** (Licença proíbe guardar >24h): confia no `savingBasis` da Amazon e diferencia `WAS_PRICE` de `LIST_PRICE`; casos duvidosos vão para a fila **com aviso**. Revisão humana é a última barreira.
- **Filtro de texto é `LIKE '%termo%'`** — nenhum índice resolve (padrão começa com curinga). Mitigado pelo `COUNT_CAP`.
- **Painel com 100 mil posts:** 81 req/s (era 268 na v1.2). A queda é o custo dos chips de categoria (~5,5 ms/req). Fila real expira em 24h e fica em dezenas de itens. Se crescer, cachear as contagens por alguns segundos — não tirar o filtro.

---

## 7. Próximos Passos

- [ ] Rodar `.\iniciar.ps1` e validar os 19 filtros na tela (última verificação foi minha, no container)
- [ ] `git add` + commit + push dos arquivos da v1.4 (23 arquivos) — `git status` vai mostrá-los modificados
- [ ] Conseguir as **10 vendas qualificadas em 30 dias** para liberar o Creators API
- [ ] Ao ligar a API real: `python -m app.cli buscar "teclado"` e conferir a saída **antes** de confiar no agendador (nomes de campo podem divergir do documentado)
- [ ] Se algum produto real cair em "Outros": acrescentar o nome ao dicionário `APELIDOS` em `app/categories.py` (uma linha)
- [ ] Testar o envio num grupo só dele no WhatsApp antes de usar na comunidade
- [ ] Preencher `AMAZON_PARTNER_TAG`, `AMAZON_CREDENTIAL_ID`, `AMAZON_CREDENTIAL_SECRET` e trocar `CATALOG_MODE=creators` quando tiver acesso

---

## 8. Informações Pendentes

- **Aviso de reset do limite de 5h:** o usuário pediu para ser avisado quando o limite renovar. Não consigo ler o consumo da conta — preciso que ele informe o horário que o app mostra ("seu limite será redefinido às HH:MM") para agendar o lembrete. Ficou em aberto.
- **Build do Docker nunca executado** (registro de imagens bloqueado no ambiente de desenvolvimento). Dockerfile revisado mas não rodado.
- **Comportamento real do `browseNodeInfo` no BR não validado** (sem credenciais): os nomes exatos dos nós raiz são suposição baseada na documentação. O fallback "Outros" evita que um produto suma do painel.

---

> **Instrução para o próximo chat:** Este arquivo contém o contexto compactado de um chat anterior. Use-o como base para continuar o trabalho. Não peça ao usuário para repetir informações que já estão aqui. Comece confirmando brevemente que entendeu o contexto e pergunte por onde o usuário quer continuar.

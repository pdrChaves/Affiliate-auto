# Promo Radar: avaliação da v1.3/v1.4 (busca por termo; filtros nos departamentos do site)

Data: 21/09/2026 · Versão testada: **v1.4** · Comparada com: v1.1/v1.2 (`RELATORIO_QA_v2.md`) e v1.0 (`RELATORIO_QA.md`)
Ambiente: container Linux, 2 vCPU, 8 GB, Python 3.11, 1 processo uvicorn, catálogo `mock`, gerador de carga na mesma máquina.

---

## 1. Resultado

**A bateria inteira passou de novo depois da reescrita.** Nada regrediu em segurança ou disponibilidade, a cobertura se manteve em 93% com 26 testes a mais, e o desempenho dos filtros novos foi corrigido dentro desta avaliação (seção 4).

| Dimensão | v1.1/v1.2 | v1.3 | O que mudou |
|---|---|---|---|
| Qualidade | A− | **A−** | 90 testes (antes 64), cobertura 93%, 0 erros de tipagem, 0 apontamentos de lint, 0 alertas no bandit |
| Segurança | A− | **A−** | 18/18 testes de ataque aprovados nas rotas novas (`/buscar`, `/buscar/fila`, `/buscar/salvar`, `/buscas/*`) |
| Desempenho | A | **A** | Filtro por categoria **25× mais rápido** e contagem dos chips **3× mais rápida** depois das correções desta rodada |
| Disponibilidade | A | **A** | 12/12 cenários de falha aprovados; buscas simultâneas não duplicam; o banco sobrevive a `kill -9` |

**O que continua fora do alcance deste teste:** a API real da Amazon (sem credenciais) e o build do Docker (registro de imagens bloqueado neste ambiente).

---

## 2. O que mudou na v1.3 e o que isso exigiu testar

A mudança foi grande: saiu o conceito de "nicho" (lista fixa no `niches.yaml`, com buscas e filtros próprios) e entrou a **barra de pesquisa**, com buscas salvas que rodam sozinhas. Isso mexeu no banco, no serviço, em todas as rotas do painel e nos filtros da fila.

| Área | Mudança | Como foi verificado |
|---|---|---|
| Banco | `posts.niche_id` sai; entram `query` (o termo que trouxe o produto) e a tabela `searches`; a `watchlist` passa a ser única por ASIN; `runs.niche_id` vira `runs.query` | migração automática testada a partir de um banco v1 real, inclusive com duplicatas (`test_db.py`) |
| Serviço | `collect(nicho)` → `search(termo)` (só lê, não grava), `queue_asin`, `run_search`, `save_search`, `run_saved_searches` | 12 testes novos em `test_service.py` |
| Painel | rotas `/buscar`, `/buscar/fila`, `/buscar/salvar`, `/buscas/{id}/toggle`, `/buscas/{id}/remover`, `/buscas/rodar` | 18/18 no DAST + 9 testes novos em `test_web.py` |
| Filtros | chips com os **19 departamentos do menu do site** + campo de texto sobre a fila | `test_category_filter_uses_the_site_departments`, `test_text_filter_over_the_queue` |
| Categoria | vem do produto (`browseNodeInfo`), não da caixa de seleção da busca | `test_department_comes_from_the_browse_nodes`, `test_migrates_old_api_categories_to_site_departments` |
| Ações no card | trocar chamada, cupom, aprovar, descartar devolvem **JSON** e não recarregam a página | `test_actions_return_json_and_keep_filters` + verificação em navegador real |

---

## 2.1 Defeito encontrado em uso, depois da avaliação

O usuário reportou que os filtros não retornavam nada e que todos os itens apareciam sem categoria.
A bateria automatizada não pegou porque testava o comportamento **como implementado**, não como
esperado: o teste enfileirava passando a categoria na mão e conferia que ela voltava.

| | |
|---|---|
| **Sintoma** | Selecionar "Livros" e pesquisar não retornava nada; todos os posts sem categoria |
| **Causa** | `Offer.category` recebia o `searchIndex` **escolhido no filtro da busca**, não o departamento do produto. Pesquisar em "Todos os departamentos" gravava `All` em tudo |
| **Causa secundária** | O catálogo fictício só tinha 4 departamentos: filtros como Livros e Pet Shop não tinham o que mostrar |
| **Correção** | A categoria passa a sair de `browseNodeInfo` (a própria Amazon diz onde o produto está, em português); o catálogo mock cobre os 19 departamentos |
| **Teste que teria pego** | `test_department_comes_from_the_browse_nodes` + `test_category_filter_uses_the_site_departments`, que agora enfileiram **sem** passar categoria e conferem que cada produto caiu no departamento certo |

Lição aplicada aos testes: onde o dado é derivado, o teste não pode fornecê-lo pronto.

---

## 3. Qualidade

| Métrica | v1.1 | v1.3 |
|---|---|---|
| Testes automatizados | 64 | **92** |
| Estabilidade | 15/15 | **15/15** execuções verdes seguidas |
| Tempo da suíte | ~3,5 s | ~5,5 s |
| Cobertura (linha + branch) | 93% | **93%** |
| Erros de tipagem (mypy, strict) | 0 | **0** (24 arquivos) |
| Apontamentos de lint (ruff) | 0 | **0** |
| Alertas de segurança (bandit) | 0 | **0** |
| Vulnerabilidades em dependências (pip-audit) | 0 | **0** (29 pacotes fixados) |
| Complexidade média (radon) | A (2,99) | **A (3,00)** — 202 blocos, nenhum acima de B |

Cobertura por módulo mais crítico: `config.py` 100%, `scorer.py` 100%, `categories.py` 100%, `models.py` 100%, `db.py` 97%, `render.py` 98%, `web/server.py` 95%, `service.py` 93%, `security.py` 92%.

**O bandit apontou um falso positivo** (`B608`, SQL montado por concatenação em `searches()`): a concatenação é de um filtro fixo, sem nenhum dado de usuário. Está anotado com `# nosec` e o motivo escrito ao lado.

---

## 4. Desempenho

### 4.1 Uma regressão encontrada e corrigida dentro desta avaliação

Os filtros novos (chips de categoria e campo de texto) entraram sem índice próprio. Com o banco de 100 mil posts do teste de volume, o resultado foi:

| Operação | Antes | Depois | Como foi resolvido |
|---|---|---|---|
| Filtrar a fila por categoria | 33,4 ms | **1,3 ms** | índice `ix_posts_cat_rank (category, status, score, created_at)`: o SQLite deixa de montar uma B-tree temporária para ordenar |
| Contar os posts de cada chip | 19,5 ms | **6,3 ms** | mesma consulta agrupando **pelo índice** (`INDEXED BY`), que cobre a consulta inteira |
| Painel com filtro de texto (10 req simultâneas) | 14,3 req/s · p50 560 ms | **32,7 req/s · p50 265 ms** | o teto `COUNT_CAP` nas contagens + o total do rodapé derivado da própria contagem dos chips (uma varredura no lugar de duas) |

O filtro de texto é um `LIKE '%termo%'`: **nenhum índice de banco resolve isso**, porque o padrão começa com curinga. A saída foi limitar o estrago: as contagens param em 5.000 linhas varridas e a tela escreve `5000+`. A listagem em si nunca foi o problema — é paginada de 30 em 30 e sai do índice (2,6 ms com 10 mil posts, 3,5 ms com 100 mil).

### 4.2 Banco realista (poucos posts na fila)

| Cenário | Concorrência | req/s | p50 | p95 | p99 | erros |
|---|---|---|---|---|---|---|
| `GET /health` | 1 | 430,1 | 2,2 ms | 3,1 ms | 4,3 ms | 0 |
| `GET /health` | 50 | 304,6 | 111 ms | 480 ms | 763 ms | 0 |
| `GET /` (fila) | 1 | 245,2 | 3,9 ms | 5,1 ms | 6,6 ms | 0 |
| `GET /` (fila) | 10 | 283,8 | 32,6 ms | 63,6 ms | 79,9 ms | 0 |
| `GET /` (fila) | 50 | 262,9 | 169 ms | 351 ms | 616 ms | 0 |
| `GET /?cat=Electronics` | 10 | 307,9 | 30,6 ms | 57,1 ms | 70,7 ms | 0 |
| `GET /?q=teclado` | 10 | 324,9 | 28,8 ms | 54,5 ms | 69,2 ms | 0 |
| `GET /posts/1/send` | 10 | 525,9 | 17,9 ms | 31,7 ms | 45,5 ms | 0 |

Para dimensionar: o uso real é **uma pessoa** clicando no painel. Mesmo o pior caso acima tem folga de mais de 300×.

### 4.3 Banco grande (100 mil posts, 10 mil na fila — cenário sintético)

| Cenário | req/s | p50 | p95 | erros |
|---|---|---|---|---|
| `GET /` (fila paginada) | 81,1 | 114 ms | 219 ms | 0 |
| `GET /?tab=enviados` | 89,0 | 105 ms | 208 ms | 0 |
| `GET /?cat=VideoGames` | 81,7 | 113 ms | 231 ms | 0 |
| `GET /?q=teclado` | 32,7 | 265 ms | 632 ms | 0 |
| `GET /` **durante o expurgo** de 88.705 linhas | 87,4 | 104 ms | 213 ms | 0 |

O expurgo de 88.705 linhas leva **1,2 s** e o painel continua respondendo enquanto ele roda (WAL + um único `UPDATE`).

**Leitura honesta desses números:** a queda em relação à v1.2 (que media 268 req/s no mesmo banco) é o preço dos chips de categoria — contar quantos posts há em cada uma custa ~5,5 ms por requisição com 100 mil linhas. Como a fila real expira em 24h e fica na casa das dezenas, o custo verdadeiro é de microssegundos. Se um dia a fila crescer de verdade, o caminho é cachear as contagens por alguns segundos, não tirar o filtro.

### 4.4 Volume e banco

| Medida | 10 mil posts | 100 mil posts |
|---|---|---|
| Inserção | 0,45 s | 6,6 s |
| Tamanho do arquivo | 11,8 MB | 123,8 MB |
| Listar a fila (paginada) | 3,3 ms | 3,1 ms |
| Listar enviados | 2,6 ms | 2,9 ms |
| Filtrar por categoria | 3,5 ms | 1,3 ms |
| Filtrar por texto | 2,6 ms | 3,5 ms |
| Contar os chips | 1,2 ms | 6,3 ms |
| Buscar 1 post por id | 0,01 ms | 0,02 ms |
| Expurgo | 0,09 s (7.705 linhas) | 1,3 s (88.705 linhas) |

### 4.5 Estabilidade sob uso contínuo

2 minutos de carga contínua misturando fila, busca ao vivo e tela de envio: **45.342 requisições, 0 erros**. Memória do processo: 65,6 MB no início, 69,7 MB no fim — cresce ~4 MB nos primeiros 10 s (aquecimento) e estabiliza. Sem vazamento.

| Cenário do soak | req/s | p50 | p99 | erros |
|---|---|---|---|---|
| fila (60 s) | 333,6 | 11,3 ms | 25,6 ms | 0 |
| busca ao vivo `/buscar?q=bluetooth` (30 s) | 366,0 | 5,0 ms | 10,9 ms | 0 |
| tela de envio (30 s) | 479,8 | 7,8 ms | 16,9 ms | 0 |

---

## 5. Segurança: 18/18

Testes de ataque contra o servidor rodando (`qa/dast.py`), agora cobrindo as rotas novas.

| # | Teste | Resultado |
|---|---|---|
| SEC-01 | Todas as rotas exigem autenticação (inclusive `/buscar`, `/buscar/fila`, `/buscar/salvar`, `/buscas/rodar`) | ✅ GET → redireciona para o login; POST → 401 |
| SEC-02 | Senha errada é recusada | ✅ 401 |
| SEC-03 | Senha padrão/fraca impede o painel de subir | ✅ processo sai com erro explicativo |
| SEC-04 | Força bruta | ✅ 300 tentativas: 5 avaliadas, 295 bloqueadas (429); a senha certa também é barrada durante o bloqueio |
| SEC-05 | `/docs`, `/redoc`, `/openapi.json` | ✅ 404 |
| SEC-06 | Cabeçalhos de segurança | ✅ CSP, X-Frame-Options, nosniff, Referrer-Policy presentes |
| SEC-07 | CSRF (sem token, token forjado, origem maliciosa) | ✅ 403 nos três; legítimo 303; cookie `HttpOnly; SameSite=Strict` |
| SEC-08 | XSS no painel e na tela de envio | ✅ tudo escapado |
| SEC-09 | XSS pelo campo de chamada | ✅ escapado |
| SEC-10 | Post manual não aceita link de terceiros | ✅ campo de URL não existe; o link é sempre gerado |
| SEC-11 | SQL injection (inclusive no **campo de busca da fila**) | ✅ 200 e banco intacto nos três payloads |
| SEC-12 | Entradas inválidas → 4xx, sem stack trace | ✅ ASIN inválido 400, termo curto 400, preço inválido 400, post inexistente 404 |
| SEC-13 | Limite de tamanho | ✅ corpo de 10 MB → 413; título de 400 caracteres → 422 |
| SEC-14 | Injeção de cabeçalho via redirect | ✅ recusado |
| SEC-15 | Open redirect | ✅ destino sempre interno |
| SEC-16 | `/health` não expõe detalhes internos | ✅ só `{"ok": ...}` |
| SEC-17 | Logout invalida a sessão no servidor | ✅ |
| SEC-18 | Cookie de sessão forjado | ✅ recusado |

O campo de busca da fila (`/?q=…`) e o da pesquisa (`/buscar?q=…`) recebem texto livre do usuário e vão parar em consultas SQL e em chamadas à API. Ambos passam por parâmetros ligados (`?`), nunca por concatenação, e o termo é normalizado e limitado a 80 caracteres antes de chegar ao banco.

---

## 6. Disponibilidade e resiliência: 12/12

| # | Cenário | Resultado |
|---|---|---|
| DISP-01 | Amazon devolvendo 500 | ✅ erro registrado em "Últimas buscas", processo de pé (4 tentativas com backoff, 7 s) |
| DISP-02 | Throttling 429 permanente | ✅ desiste após 7 s e tenta na próxima rodada |
| DISP-03 | Timeout da API | ✅ entra no retry, capturado |
| DISP-04 | Servidor de token (OAuth) fora | ✅ erro controlado |
| DISP-05 | Botão **Enviar** com a API fora | ✅ HTTP 200: mostra aviso e libera o envio com o preço da última checagem (até 24h) |
| DISP-06 | Botão **Revalidar** com a API fora | ✅ volta ao painel com "API indisponível", sem alterar nada |
| DISP-07 | Monitor pós-envio com a API fora | ✅ tratado, tenta de novo na próxima hora |
| DISP-08 | Agendador com job que explode | ✅ continua rodando (3 execuções em 3,5 s) |
| DISP-09 | Telegram fora | ✅ não afeta nada |
| DISP-10 | IA fora | ✅ cai nas chamadas de reserva do `config.yaml` |
| DISP-11 | **5 buscas simultâneas pelo mesmo termo** | ✅ 2 posts, 0 duplicados |
| DISP-12 | Dois processos gravando o mesmo produto | ✅ índice único parcial em `posts(asin)` barra o segundo |

Além disso:

- **`kill -9` no meio de uma escrita:** banco íntegro depois de reiniciar, o post gravado continua lá com a chamada editada, as buscas salvas continuam lá. Reinício em **0,70 s**.
- **`/health` com o banco removido:** responde **503**; volta a **200** sozinho quando o arquivo reaparece.
- **Rodadas automáticas não se sobrepõem:** com 3 disparos simultâneos, 1 roda e 2 são registradas como `skipped` — não empilham chamadas na API.

---

## 7. O que não foi testado

| Item | Por quê | Risco residual |
|---|---|---|
| Chamadas reais ao Creators API | Sem credenciais (exigem 10 vendas em 30 dias) | Nomes de campos podem divergir. O parser aceita camelCase e PascalCase e tolera campos ausentes. **No primeiro uso real, rode `python -m app.cli buscar "teclado"` e confira a saída** |
| Build do Docker | O registro de imagens está bloqueado neste ambiente | Baixo: o Dockerfile foi revisado (usuário não-root uid 10001, `read_only`, `no-new-privileges`, porta só em 127.0.0.1) |
| Envio real no WhatsApp | Depende da sua conta e das comunidades | O link `wa.me/?text=` é gerado e testado; o passo seguinte é manual por decisão de projeto |
| Carga de vários usuários simultâneos | O painel é de uso pessoal, local | Não se aplica |

---

## 8. Como repetir esta avaliação

```bash
pytest -q --cov=app --cov-report=term-missing   # 92 testes, cobertura
ruff check . && mypy app && bandit -r app -q    # lint, tipagem, segurança estática
pip-audit -r requirements.lock                  # vulnerabilidades nas dependências
radon cc app -a -s && radon mi app -s           # complexidade

qa/start.sh 8766 qa/run/dast.db && python3 qa/dast.py      # 18 testes de ataque
python3 qa/avail.py                                        # 12 cenários de falha
python3 qa/volume.py qa/run/vol.db 100000                  # volume + tempos de consulta
qa/start.sh 8767 qa/run/perf.db && python3 qa/load.py http://localhost:8767 \
  qa/results/x.json '[{"path":"/","c":10}]'                # carga
```

Os resultados brutos ficam em `qa/results/` (JSON e texto). Os da v1.0 estão em `qa/results_v1/`.

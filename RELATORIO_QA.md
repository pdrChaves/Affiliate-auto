# Promo Radar: relatório de testes (qualidade, segurança, desempenho, disponibilidade)

Data: 19/09/2026 · Versão testada: v1.0 (a entregue antes, **sem alterações**)
Ambiente: container Linux, 2 vCPU, 8 GB RAM, Python 3.11, 1 processo uvicorn, catálogo em modo `mock`.

---

## 1. Resultado geral

**Veredito: pronto para uso local ou em rede privada, mas não para expor o painel na internet.** O código é saudável, o desempenho sobra para 1 operador e a coleta resiste a falhas da Amazon. Os problemas reais estão em duas frentes: a proteção do painel (CSRF, senha padrão, sem limite de tentativas) e o tratamento de erros no fluxo de envio.

| Dimensão | Nota | Resumo |
|---|---|---|
| Qualidade | **B** | 29/29 testes passando (estáveis em 15 execuções), cobertura de 81%, complexidade média A. Há 23 erros de tipagem, e alguns deles viram erro 500 real |
| Segurança | **C** | Sem XSS, sem SQL injection, 0 CVEs nas dependências. **Falhas:** CSRF, senha padrão aceita, sem limite de força bruta, link manual aceita domínio falso |
| Desempenho | **A** (no uso previsto) | 450–1.100 req/s com p95 abaixo de 31 ms na carga normal. Memória estável (~65 MB). O painel fica lento só com 100 mil posts ou durante um expurgo grande |
| Disponibilidade | **B−** | A coleta tolera API fora, 429, timeout e kill -9 sem perder dados. **Falhas:** "Enviar" com a API fora dá erro 500; coletas simultâneas duplicam posts; o health check não detecta banco quebrado |

**Totais: 60 verificações executadas** (29 unitárias + 16 de segurança dinâmica + 15 de disponibilidade), além de 5 ferramentas de análise estática. **Achados reais: 20.** São 7 de severidade alta ou média-alta, 7 médios e 6 baixos (seção 6).

---

## 2. Qualidade de código

### 2.1 Métricas

| Métrica | Valor | Referência |
|---|---|---|
| Linhas de código (SLOC) | 1.058 (app) | — |
| Testes automatizados | 29, **100% passando** | — |
| Estabilidade (flaky) | **15/15 execuções verdes** | 0 testes instáveis |
| Tempo da suíte | 0,8–1,5 s | — |
| Cobertura linha + branch | **81%** (876 statements, 206 branches) | aceitável: ≥ 80% |
| Complexidade ciclomática média (radon) | **A (3,33)** | A = 1–5 |
| Funções por faixa | A: 87 · B: 10 · C: 5 · **D: 1** | D = 21–30, candidata a refatoração |
| Índice de manutenibilidade | todos os arquivos **A** (pior: `service.py` 36,7) | A ≥ 20 |
| Lint (ruff: E, F, W, B, UP, SIM, S, C90, N) | 14 apontamentos em `app/` | 2 corrigíveis automaticamente |
| Tipagem (mypy) | **23 erros** em 2 arquivos (+1 de stub ausente) | ideal: 0 |

### 2.2 Cobertura por módulo (onde faltam testes)

| Módulo | Cobertura | Observação |
|---|---|---|
| `pipeline/scorer.py`, `models.py`, `amazon/base.py`, `ratelimit.py` | 100% | regras de negócio e contratos totalmente cobertos |
| `pipeline/render.py`, `copywriter.py`, `config.py`, `db.py`, `mock.py` | 94–98% | — |
| `service.py` | 86% | ramos de erro da coleta e o reenvio sem conteúdo |
| `web/server.py` | 78% | rotas `/watch`, `/manual`, `/approve`, `/reject` sem teste de sucesso |
| `amazon/creators.py` | 71% | `search()` sem teste unitário |
| `scheduler.py` | 30% | só verificado manualmente |
| `cli.py`, `senders/telegram.py` | **0%** | sem teste |

### 2.3 Pontos de atenção

- **mypy × bugs reais:** 18 dos 23 erros são "valor pode ser `None`" em `service.py`, porque `get_post()` pode não achar o post. Os testes de segurança confirmaram que isso gera **erro 500** ao chamar um post inexistente (SEC-12). A tipagem apontou o bug antes do teste.
- `in_window()` reaproveita a variável `now` com dois tipos (datetime → time). Funciona, mas confunde.
- Maior complexidade: `scorer.evaluate` (D, 25), `service.monitor_sent` (C, 15), `service.collect` (C, 14). Estão cobertos por testes, mas são o próximo alvo de refatoração.
- Comentários: 7% das linhas, com docstrings em todos os módulos.

---

## 3. Segurança

### 3.1 Análise estática e dependências

| Ferramenta | Resultado | Triagem |
|---|---|---|
| **pip-audit** (CVEs) | **0 vulnerabilidades** | ✅ |
| **bandit** | 1 alto, 4 médios, 1 baixo | **5 são falso positivo**, 1 real (abaixo) |
| ↳ B324 MD5 (alto) | `copywriter.py:40` | falso positivo: MD5 só escolhe a frase de reserva, sem uso criptográfico |
| ↳ B608 SQL string (3× médio) | `db.py` | falso positivo: valores sempre parametrizados. Os nomes de coluna em `update_post` vêm só do código interno. Frágil, mas não explorável hoje |
| ↳ B104 bind 0.0.0.0 (médio) | `cli.py:21` | **real**: o painel escuta em todas as interfaces por padrão |
| ↳ B311 random (baixo) | `mock.py` | falso positivo: só simula preço |
| Dockerfile | roda como **root** (sem `USER`) | real, médio |
| requirements.txt | faixas (`>=`), **sem lockfile** | real, baixo: builds futuros podem puxar versões diferentes |

### 3.2 Testes dinâmicos (ataques contra o servidor rodando)

| ID | Teste | Resultado | Evidência |
|---|---|---|---|
| SEC-01 | Rotas do painel exigem login | ✅ | 12/12 rotas → 401 sem credencial |
| SEC-02 | Senha errada recusada | ✅ | 401 |
| SEC-03 | Senha padrão não é aceita | ❌ | o servidor sobe e aceita `admin / troque-esta-senha` sem aviso |
| SEC-04 | Limite contra força bruta | ❌ | **300 tentativas em 0,3 s (~974/s)**, nenhuma bloqueada |
| SEC-05 | Documentação da API privada | ❌ | `/docs`, `/redoc` e `/openapi.json` públicos (mapa de rotas sem login) |
| SEC-06 | Cabeçalhos de segurança | ❌ | faltam CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy e HSTS |
| SEC-07 | Proteção CSRF | ❌ | POST com `Origin: https://site-malicioso.com` → 303 (aceito) |
| SEC-08 | XSS no painel e na tela de envio | ✅ | `<script>`, `<img onerror>` e `</textarea>` saem escapados |
| SEC-09 | XSS no campo de chamada | ✅ | atributo escapado |
| SEC-10 | Link manual só aceita Amazon | ❌ | aceitou `https://evil.example/phish?x=amazon.com.br&tag=…`, `https://amazon.com.br.evil.example/?tag=x` e `javascript:…amazon.com.br?tag=1` |
| SEC-11 | SQL injection | ✅ | 3 payloads clássicos; banco intacto |
| SEC-12 | Entrada inválida → 4xx | ❌ | **5 casos → 500**: post inexistente (refresh, headline), nicho inexistente (collect, manual), preço "abc" |
| SEC-13 | Limite de tamanho | ❌ | título de **10 MB aceito e gravado** em 0,4 s |
| SEC-14 | Injeção de cabeçalho (CRLF) | ✅ | valor codificado na URL |
| SEC-15 | Open redirect | ✅ | redirect fica sempre no próprio site |
| SEC-16 | /health sem detalhes internos | ❌ (baixo) | expõe `mode: mock/creators` |

**Placar: 7 aprovados · 9 reprovados.**

### 3.3 O cenário de ataque mais sério (cadeia SEC-07 + SEC-10)

O login do painel é HTTP Basic, e o navegador reenvia essa credencial automaticamente. Com o painel aberto, **qualquer site que você visitar** pode enviar um formulário escondido para `/manual` e criar na sua fila um post com **link de phishing** que passa na validação (SEC-10). A aparência seria a de uma promoção normal. Se você enviar esse post sem conferir o link, a comunidade recebe um golpe com o seu nome. O mesmo vetor serve para descartar posts ou disparar coletas.

A exploração depende de você estar logado e de alguém mirar especificamente o seu painel. Por outro lado, a correção é barata e o impacto recai sobre a sua comunidade.

---

## 4. Desempenho

O gerador de carga rodou **na mesma máquina** (2 vCPU divididos com o servidor), então os números são conservadores.

### 4.1 Latência e vazão (banco pequeno, uso real)

| Cenário | Concorrência | req/s | p50 | p95 | p99 | Erros |
|---|---|---|---|---|---|---|
| `/health` | 1 | 856 | 1,1 ms | 1,5 ms | 1,7 ms | 0 |
| `/health` | 50 | 475 | 60 ms | 371 ms | 625 ms | 0 |
| Fila (6 posts) | 1 | 450 | 2,2 ms | 2,6 ms | 3,0 ms | 0 |
| Fila (6 posts) | 10 | 598 | 15 ms | 31 ms | 39 ms | 0 |
| Fila (6 posts) | 50 | 265 | 133 ms | 535 ms | 817 ms | 0 |
| Tela de envio | 10 | 1.062 | 6,7 ms | 25 ms | 52 ms | 0 |

**Leitura:** o uso real é 1 pessoa clicando algumas vezes por minuto, ou seja, menos de 1 req/s. Há folga de mais de 400×. A queda com 50 conexões simultâneas vem do processo único do uvicorn com o SQLite serializado. É irrelevante para este caso e só importaria com muitos usuários.

### 4.2 Volume de dados

No uso previsto (~50 posts/dia), 10 mil posts equivalem a ~6 meses e 100 mil a ~5 anos.

| Métrica | 10 mil posts | 100 mil posts |
|---|---|---|
| Tamanho do banco | 10,7 MB | 107 MB |
| Consulta da fila (200 itens) | 4,2 ms | 15,8 ms |
| Consulta de enviados | 6,3 ms | 30,1 ms |
| Busca de duplicata (índice) | 0,02 ms | 0,02 ms |
| **Página da fila, 10 usuários** | — | **42 req/s, p50 221 ms, p95 410 ms** |
| **Página de enviados, 10 usuários** | — | **27 req/s, p50 343 ms, p95 645 ms** |
| **Primeiro expurgo** (acúmulo) | **4,2 s** (7,7 mil linhas) | **47,8 s** (88,7 mil linhas) |
| Banco após expurgo | não encolhe (sem VACUUM) | não encolhe |

**Gargalos encontrados:**
1. **Sem paginação:** a página renderiza até 200 cards. A consulta leva 16 ms, mas o HTML leva ~200 ms.
2. **Expurgo linha a linha:** um `UPDATE` + `commit` por post. No dia a dia processa poucas linhas por hora e o custo some. Com acúmulo (sistema parado por semanas, ou `CONTENT_RETENTION_HOURS` alterado), fica lento.
3. **Contenção durante o expurgo:** com o expurgo rodando em outro processo (ex.: `python -m app.cli purge` com o painel aberto), a fila caiu de **42 para 2 req/s, com p50 de 3,8 s e máximo de 10,4 s**. Não houve erro, só espera. Dentro do próprio servidor o impacto é menor, porque os comandos se intercalam.
4. Posts e a tabela `runs` **nunca são apagados**, só esvaziados. A tabela `runs` ganha 1 linha por coleta.

### 4.3 Memória (teste de resistência)

3 minutos de carga mista, ~130 mil requisições, 0 erros:

| Instante | 0 s | 60 s | 120 s | 170 s |
|---|---|---|---|---|
| Memória do processo | 61,3 MB | 64,5 MB | 64,6 MB | 65,9 MB |

Crescimento de 4,6 MB, que estabiliza. **Sem vazamento aparente.**

---

## 5. Disponibilidade e resiliência

| ID | Cenário de falha | Resultado | Detalhe |
|---|---|---|---|
| DISP-01 | API da Amazon devolvendo 500 | ✅ | coleta registra o erro e segue; 15 s (4 tentativas com backoff) |
| DISP-02 | Throttling 429 permanente | ✅ | desiste em 15 s e tenta de novo na próxima coleta |
| DISP-03 | Timeout da API | ✅ (parcial) | capturado, mas **timeout não tem retry** (só 429/5xx têm) |
| DISP-04 | Servidor de token (OAuth) fora | ✅ | erro claro registrado na coleta |
| DISP-05 | **"Enviar" com a API fora** | ❌ **alta** | **HTTP 500 depois de 15 s.** Você fica sem a tela e sem o texto, sem mensagem de erro |
| DISP-06 | "Revalidar" com a API fora | ❌ | HTTP 500 |
| DISP-07 | Monitor pós-envio com a API fora | ❌ (baixa) | exceção não tratada; o agendador registra e tenta de novo na próxima hora |
| DISP-08 | Rotina do agendador com erro | ✅ | o agendador continua rodando (3 execuções em 3,5 s mesmo com erro) |
| DISP-09 | Telegram fora do ar | ✅ | ignorado com log |
| DISP-10 | IA fora do ar | ✅ | usa a chamada de reserva do nicho |
| DISP-11 | **Coletas simultâneas** | ❌ **média** | 5 coletas paralelas → **15 posts, 12 duplicados**. Um duplo clique em "Coletar agora" durante uma coleta agendada reproduz o problema |
| DISP-12 | `kill -9` durante escritas | ✅ | `PRAGMA integrity_check = ok`, nenhum dado perdido |
| DISP-13 | Tempo de reinício | ✅ | **0,65 s** até o painel responder |
| DISP-14 | Health check com banco quebrado | ❌ **média** | com o arquivo do banco removido, `/health` → **200**, mas a coleta → 500. O `HEALTHCHECK` do Docker não reinicia nada |
| DISP-15 | Resistência (3 min) | ✅ | 0 erros em ~130 mil requisições |

**Placar: 10 aprovados · 5 reprovados.**

**Ponto único de falha:** é um só processo, com agendador e painel juntos. Se ele cair, as duas coisas param. O `restart: unless-stopped` do Compose volta em menos de 1 s, mas só se o processo morrer; um travamento sem crash não é detectado (DISP-14).

---

## 6. Achados priorizados e correção sugerida

| # | Sev. | Achado | Correção | Esforço |
|---|---|---|---|---|
| 1 | **Alta** | CSRF em todas as ações (SEC-07) | Checar `Origin`/`Referer` nos POST + token CSRF por sessão | P |
| 2 | **Alta** | Link manual aceita domínio falso (SEC-10) | Validar com `urlparse`: esquema `https`, host exatamente `www.amazon.com.br`/`amazon.com.br`, parâmetro `tag` = sua tag | P |
| 3 | **Alta** | Aceita a senha padrão (SEC-03) | Recusar iniciar com a senha de exemplo ou com menos de 12 caracteres | P |
| 4 | **Alta** | Sem limite de força bruta (SEC-04) | Bloqueio por IP após N falhas (ex.: 10/5 min) | P |
| 5 | **Alta** | "Enviar"/"Revalidar" → 500 com a API fora (DISP-05/06) | Capturar o erro e mostrar "API indisponível: preço de HH:MM, envie mesmo assim ou tente de novo" | P |
| 6 | Média-alta | Painel escuta em 0.0.0.0 sem TLS (B104) | Padrão `127.0.0.1`; para acesso externo, proxy com TLS (Caddy) ou Tailscale | P |
| 7 | Média-alta | Cabeçalhos ausentes / clickjacking (SEC-06) | Middleware com CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy` | P |
| 8 | Média | Coletas simultâneas duplicam (DISP-11) | Trava por nicho + índice único parcial `(niche_id, asin)` para status pendente | P |
| 9 | Média | Health check superficial (DISP-14) | `/health` testa leitura/escrita no banco e se o agendador está vivo | P |
| 10 | Média | Erros 500 em entradas inválidas (SEC-12, mypy) | 404/400 explícitos; zerar os erros do mypy | P |
| 11 | Média | Sem limite de tamanho (SEC-13) | Limites nos formulários (título ≤ 300, chamada ≤ 60, cupom ≤ 30) | P |
| 12 | Média | `/docs` e `/openapi.json` públicos (SEC-05) | `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` | P |
| 13 | Média | Container como root | `USER` sem privilégio no Dockerfile | P |
| 14 | Média | Expurgo linha a linha + contenção (perf 4.2) | Um `UPDATE` em lote + modo WAL no SQLite + índice em `created_at` | P |
| 15 | Baixa | Página sem paginação | Paginar em 30–50 itens | P |
| 16 | Baixa | Posts/runs nunca apagados; banco não encolhe | Apagar posts expurgados com mais de 90 dias, reter `runs` por 30 dias, `VACUUM` mensal | P |
| 17 | Baixa | Timeout sem retry (DISP-03) | Incluir `httpx.TimeoutException` no retry | P |
| 18 | Baixa | Monitor lança exceção com a API fora (DISP-07) | try/except + log | P |
| 19 | Baixa | Dependências sem versão fixa | Gerar lockfile (`pip-compile` ou `uv lock`) | P |
| 20 | Baixa | Cobertura 0% em CLI/Telegram; 30% no agendador | Testes para esses módulos | M |

P = pequeno (até ~1h cada) · M = médio. Os 20 itens juntos levam cerca de 1 dia de trabalho. **Os itens 1–7 deveriam sair antes de expor o painel fora da sua máquina.**

---

## 7. Limites deste teste

- **Catálogo simulado:** a API real da Amazon não foi exercitada (sem credenciais). As falhas da API foram simuladas no nível HTTP com as respostas documentadas (500, 429, timeout, token fora).
- **Máquina compartilhada:** servidor e gerador de carga dividiram 2 vCPU, então a vazão real tende a ser maior.
- **Sem TLS/proxy no teste:** a segurança de transporte depende de como você publicar o painel (item 6).
- **Não testado:** ataques de rede (DoS volumétrico), segurança do host/VPS, o WhatsApp em si, e pentest manual exaustivo. Foi uma bateria automatizada focada nos riscos desta aplicação.
- O teste de contenção do expurgo usou **outro processo**. Dentro do servidor o impacto esperado é menor, mas não foi medido separadamente.

---

## 8. Como reproduzir

Os scripts ficam em `qa/` (escritos para o ambiente de teste; ajuste os caminhos `/home/claude/...`):

| Script | O que faz |
|---|---|
| `pytest -q --cov=app --cov-branch` | testes + cobertura |
| `ruff check app`, `mypy app`, `radon cc/mi app`, `bandit -r app`, `pip-audit -r requirements.txt` | análise estática |
| `qa/dast.py` | 16 testes de segurança contra o servidor rodando |
| `qa/load.py` | gerador de carga (req/s, p50/p95/p99) |
| `qa/volume.py` | popula N posts e mede consultas e expurgo |
| `qa/avail.py` | 11 cenários de falha e resiliência |
| `qa/results/*.json` | saídas brutas desta rodada |

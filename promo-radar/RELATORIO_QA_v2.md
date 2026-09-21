# Promo Radar: reavaliação após as correções (v1.1)

Data: 19/09/2026 · Versão testada: **v1.1** (atualizado em 20/09 para a v1.2: monitoramento pós-envio desligado por padrão e preço conferido em todo envio; 66 testes, 18/18 de segurança e 12/12 de falhas repetidos e aprovados) · Comparada com: v1.0 (`RELATORIO_QA.md`)
Ambiente: o mesmo da v1 (container Linux, 2 vCPU, 8 GB, Python 3.11, 1 processo uvicorn, catálogo `mock`, gerador de carga na mesma máquina).

---

## 1. Resultado

**Os 20 achados da v1 foram corrigidos e toda a bateria passou.** Não sobrou nenhuma falha de severidade alta ou média. O painel agora pode ser acessado de outro aparelho, desde que via VPN ou HTTPS (seção 6).

| Dimensão | v1.0 | v1.1 | O que mudou |
|---|---|---|---|
| Qualidade | B | **A−** | 64 testes (antes 29), cobertura de 93% (antes 81%), 0 erros de tipagem (antes 23), 0 apontamentos de lint (antes 14) |
| Segurança | C | **A−** | 18/18 testes de ataque aprovados (antes 7/16); 0 alertas no bandit (antes 6); login por sessão com CSRF e bloqueio de força bruta |
| Desempenho | A | **A** | Com banco grande, o painel ficou **6× mais rápido** e o expurgo **47× mais rápido**. Com banco pequeno perdeu ~20% de vazão pelas camadas de segurança, e ainda sobra folga de mais de 300× |
| Disponibilidade | B− | **A** | 16/16 cenários de falha aprovados (antes 10/15); "Enviar" funciona com a Amazon fora; sem duplicatas; health check real |

**O que continua fora do alcance deste teste:** a API real da Amazon (sem credenciais) e o build do Docker. O daemon subiu, mas o registro de imagens está bloqueado neste ambiente. Os detalhes estão na seção 7.

---

## 2. O que foi corrigido (os 20 achados da v1)

| # | Achado v1 | Correção aplicada | Verificado por |
|---|---|---|---|
| 1 | CSRF em todas as ações | Login por **sessão** (sai o HTTP Basic, que o navegador reenviava sozinho). Cookie `HttpOnly` + `SameSite=Strict`, **token CSRF** em todo formulário e checagem de `Origin`/`Referer` em todo POST | SEC-07, `test_csrf_token_and_origin` |
| 2 | Link manual aceitava domínio falso | **O campo de URL não existe mais.** O link é sempre montado pelo sistema: `amazon.com.br/dp/ASIN?tag=SUA_TAG`, com ASIN validado (10 caracteres) | SEC-10, `test_manual_link_is_always_generated` |
| 3 | Aceitava a senha de exemplo | O painel **não sobe** com senha vazia, de exemplo, igual ao usuário, em uma lista de senhas comuns ou com menos de 8 caracteres (limite definido por você; com o painel local + bloqueio por IP, é suficiente) | SEC-03, `test_refuses_weak_or_missing_password` |
| 4 | Sem limite de força bruta | Bloqueio por IP após 5 falhas em 15 min (HTTP 429). Durante o bloqueio, nem a senha certa entra | SEC-04, `test_bruteforce_lockout` |
| 5 | "Enviar"/"Revalidar" davam 500 com a API fora | "Enviar" **mostra aviso e libera** o envio com o preço e o horário da última checagem, se ela tiver menos de 24h (limite da Licença); depois disso bloqueia. "Revalidar" volta ao painel com a mensagem "API indisponível", sem alterar nada. Na tela, a API tenta só 2 vezes (antes 4) | DISP-05/06, `test_send_with_api_down_*` |
| 6 | Escutava em 0.0.0.0 | Padrão `HOST=127.0.0.1`. No Docker, a porta só é publicada em `127.0.0.1:8000` | bandit B104 zerado |
| 7 | Cabeçalhos ausentes | CSP estrita (sem script nem estilo inline; o JS e o CSS saíram para `/static`), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, `Cache-Control: no-store`; HSTS com `COOKIE_SECURE=true` | SEC-06 |
| 8 | Coletas simultâneas duplicavam | Trava por nicho no processo + **índice único parcial** no banco (vale entre processos). Migração automática: duplicatas antigas viram "expired" | DISP-11/12, `test_concurrent_collects_*`, `test_migrates_v1_database_with_duplicates` |
| 9 | Health check superficial | `/health` testa leitura, **escrita** (lock de escrita), se o arquivo do banco existe e se o agendador está vivo. Responde **503** se algo falhar. O `HEALTHCHECK` do Docker usa isso | DISP-15, `test_health*` |
| 10 | Erros 500 em entrada inválida | 404 (post inexistente), 400 (nicho/ASIN/preço inválidos), 409 (ação em post já enviado/descartado). Página de erro sem stack trace | SEC-12, `test_invalid_inputs_are_4xx`, `test_state_guards` |
| 11 | Sem limite de tamanho | Corpo limitado a 64 KB (413) + limite por campo (título 300, chamada 60, cupom 30 → 422) | SEC-13 |
| 12 | `/docs` e `/openapi.json` públicos | Desativados | SEC-05 |
| 13 | Container como root | Usuário `app` (uid 10001), `read_only`, `no-new-privileges`, config montada só leitura | revisão do Dockerfile (build não executado, seção 7) |
| 14 | Expurgo linha a linha travava o painel | **Um único UPDATE** + SQLite em modo **WAL** + coluna `purged` indexada | perf 4.2 |
| 15 | Sem paginação | 30 por página, com consulta em 2 etapas (ordena só ids pelo índice `(status, score, created_at)`) | `test_pagination`, perf 4.2 |
| 16 | Posts/log nunca apagados | Rotina diária: apaga posts já expurgados com mais de 90 dias e coletas com mais de 30; `VACUUM` aos domingos | `test_housekeeping_deletes_old` |
| 17 | Timeout sem retry | Timeout e erro de conexão entram no retry com backoff; servidor de token inacessível vira erro controlado | DISP-03, `test_timeout_is_retried` |
| 18 | Monitor lançava exceção | Captura e tenta de novo na próxima hora | DISP-07 |
| 19 | Dependências sem versão fixa | `requirements.lock` com 28 pacotes fixados; o Dockerfile instala por ele | pip-audit sobre o lock |
| 20 | CLI/Telegram/agendador sem teste | Testes novos: CLI 74%, Telegram 100%, agendador 95% | cobertura |

Também entrou (não estava na lista da v1): logout que invalida a sessão no servidor, recusa de cookie forjado, bloqueio de ações em posts que já saíram da fila, `update_post` com lista branca de colunas e SQL estático nas consultas com filtro de status.

---

## 3. Qualidade

| Métrica | v1.0 | v1.1 |
|---|---|---|
| Testes automatizados | 29 | **64** |
| Estabilidade | 15/15 | **15/15** execuções verdes |
| Tempo da suíte | ~1 s | ~3,5 s |
| Cobertura (linha + branch) | 81% | **93%** |
| Erros de tipagem (mypy) | 23 | **0** |
| Apontamentos de lint (ruff, mesmas regras) | 14 | **0** |
| Complexidade média (radon) | A (3,33) | **A (2,99)** |
| Funções nota D | 1 (`scorer.evaluate`, 25) | **0** (regras viraram tabela + 3 funções) |
| Funções nota C | 5 | 4 (máx. 12) |
| Pior índice de manutenibilidade | A (36,7) | A (26,6), por causa do `service.py` maior |
| SLOC (`app/`) | 1.058 | 1.492 |

**Transparência sobre o lint:** o `pyproject.toml` desliga 2 regras de propósito. A S311 cobre `random` no catálogo simulado; a B008 cobre `Depends()` em parâmetros, que é o padrão do FastAPI. Nos testes também ficam liberados `assert` e senhas fictícias. Há ainda 7 marcações `# nosec`, todas comentadas no código: 5 no `db.py`, onde só a quantidade de `?` é interpolada ou os nomes de coluna vêm da lista branca; 1 no valor padrão vazio do formulário de login; 1 no `random` do mock.

---

## 4. Desempenho

### 4.1 Uso normal (banco pequeno)

| Cenário | Conc. | v1.0 req/s · p95 | v1.1 req/s · p95 | Variação |
|---|---|---|---|---|
| `/health` | 1 | 856 · 1,5 ms | 646 · 1,9 ms | −25%: agora testa o banco de verdade |
| `/health` | 50 | 475 · 371 ms | 461 · 392 ms | ≈ |
| Fila (6 posts) | 1 | 450 · 2,6 ms | 368 · 3,2 ms | −18% |
| Fila (6 posts) | 10 | 598 · 31 ms | 459 · 39 ms | −23% |
| Fila (6 posts) | 50 | 265 · 535 ms | 245 · 572 ms | −8% |
| Tela de envio | 10 | 1.062 · 25 ms | 835 · 20 ms | −21% |

**Leitura:** a v1.1 ficou ~20% mais lenta no uso normal. É o custo da sessão, do CSRF e do middleware de cabeçalhos em cada requisição. Não há erros em nenhum cenário, e o uso real (menos de 1 req/s) continua com folga de mais de 300×. Se um dia importar, dá para trocar o middleware por um ASGI puro e recuperar boa parte da perda.

### 4.2 Volume e expurgo (onde a v1 tinha gargalos)

| Métrica (100 mil posts) | v1.0 | v1.1 | Ganho |
|---|---|---|---|
| Consulta da fila | 15,8 ms | **2,4 ms** | 6,6× |
| Consulta de enviados | 30,1 ms | **2,1 ms** | 14× |
| Página da fila, 10 usuários | 42 req/s · p50 221 ms | **268 req/s · p50 35 ms** | 6,4× |
| Página de enviados, 10 usuários | 27 req/s · p50 343 ms | **200 req/s · p50 47 ms** | 7,4× |
| Expurgo de 88,7 mil linhas | 47,8 s | **1,0 s** | 47× |
| **Painel durante um expurgo** (outro processo) | **2 req/s · p50 3,8 s · máx. 10,4 s** | **270 req/s · p50 35 ms · máx. 115 ms** | sem travamento |
| Banco que nunca encolhe | sim | apaga antigos + `VACUUM` semanal | — |

Com 10 mil posts, as consultas levam 2,3–2,7 ms e o expurgo 0,07 s.

### 4.3 Resistência (soak) e memória

v1.1: 3 minutos de carga mista (fila, tela de envio e **coleta real** em paralelo), **~90 mil requisições, 0 erros**, 18,4 mil coletas executadas e **0 duplicatas** na fila.

| Instante | 0 s | 60 s | 120 s | 170 s |
|---|---|---|---|---|
| Memória | 63,8 MB | 66,6 MB | 66,9 MB | 69,1 MB (estável nos últimos 30 s) |

Sem vazamento aparente.

---

## 5. Segurança e disponibilidade: resultados completos

### 5.1 Testes de ataque (servidor rodando)

| ID | Teste | v1.0 | v1.1 | Evidência v1.1 |
|---|---|---|---|---|
| SEC-01 | Rotas exigem autenticação | ✅ | ✅ | GET → redireciona para /login; POST → 401 |
| SEC-02 | Senha errada recusada | ✅ | ✅ | 401 |
| SEC-03 | Senha padrão/fraca | ❌ | ✅ | processo encerra: "Painel não iniciado: PANEL_PASSWORD é a senha de exemplo…" |
| SEC-04 | Força bruta | ❌ | ✅ | 300 tentativas: **5 avaliadas, 295 bloqueadas (429)**; senha certa durante o bloqueio → 429 |
| SEC-05 | Documentação da API | ❌ | ✅ | `/docs`, `/redoc`, `/openapi.json` → 404 |
| SEC-06 | Cabeçalhos de segurança | ❌ | ✅ | CSP, X-Frame-Options, nosniff, Referrer-Policy presentes |
| SEC-07 | CSRF | ❌ | ✅ | sem token 403 · token forjado 403 · Origin malicioso 403 · legítimo 303 |
| SEC-08 | XSS painel/envio | ✅ | ✅ | 3 payloads escapados |
| SEC-09 | XSS na chamada | ✅ | ✅ | escapado |
| SEC-10 | Link de terceiros no post manual | ❌ | ✅ | URLs enviadas ignoradas; link sempre gerado |
| SEC-11 | SQL injection | ✅ | ✅ | banco intacto |
| SEC-12 | Entradas inválidas → 4xx | ❌ | ✅ | 404/400; página de erro sem stack trace |
| SEC-13 | Limite de tamanho | ❌ | ✅ | corpo de 10 MB → 413; título de 400 caracteres → 422 |
| SEC-14 | Injeção de cabeçalho | ✅ | ✅ | recusado (422) |
| SEC-15 | Open redirect | ✅ | ✅ | redirect sempre interno |
| SEC-16 | /health sem detalhes | ❌ | ✅ | `{"ok": true}` |
| SEC-17 | Logout invalida a sessão | — | ✅ | *novo* |
| SEC-18 | Cookie forjado recusado | — | ✅ | *novo* |
| | **Placar** | **7/16** | **18/18** | |

**Estática:** bandit 6 → **0**; pip-audit **0 CVEs** nas versões travadas do `requirements.lock`.

### 5.2 Falhas e resiliência

| ID | Cenário | v1.0 | v1.1 | Evidência v1.1 |
|---|---|---|---|---|
| DISP-01 | API da Amazon com erro 500 | ✅ 15 s | ✅ **7 s** | erro registrado, processo segue |
| DISP-02 | Throttling 429 permanente | ✅ 15 s | ✅ **7 s** | tenta de novo na próxima coleta |
| DISP-03 | Timeout da API | parcial | ✅ | agora entra no retry com backoff |
| DISP-04 | Servidor de token fora | ✅ | ✅ | erro claro, também quando o servidor está inacessível |
| DISP-05 | "Enviar" com a API fora | ❌ 500 após 15 s | ✅ **200 em 1 s** | aviso + envio liberado com o preço/horário da última checagem |
| DISP-06 | "Revalidar" com a API fora | ❌ 500 | ✅ | volta ao painel com "API indisponível", nada alterado |
| DISP-07 | Monitor com a API fora | ❌ | ✅ | tratado; tenta de novo em 1h |
| DISP-08 | Rotina do agendador com erro | ✅ | ✅ | agendador segue rodando |
| DISP-09 | Telegram fora | ✅ | ✅ | ignorado com log |
| DISP-10 | IA fora | ✅ | ✅ | chamada de reserva |
| DISP-11 | 5 coletas simultâneas | ❌ 12 duplicados | ✅ **0 duplicados** | 4 coletas recusadas com "já em andamento" |
| DISP-12 | Duas conexões/processos gravando o mesmo produto | — | ✅ | índice único bloqueia (*novo*) |
| DISP-13 | `kill -9` durante escritas | ✅ | ✅ | `integrity_check = ok`, última escrita preservada |
| DISP-14 | Tempo de reinício | ✅ 0,65 s | ✅ 0,69 s | — |
| DISP-15 | Health com o banco removido | ❌ 200 | ✅ **503** | e 503 também com o agendador parado |
| DISP-16 | Soak de 3 min | ✅ | ✅ | 0 erros em ~90 mil requisições |
| | **Placar** | **10/15** | **16/16** | |

---

## 6. Riscos que continuam (sem falha no teste, mas vale saber)

| Risco | Impacto | Recomendação |
|---|---|---|
| **Bloqueio de login é por IP** | Atrás de um proxy, todos os acessos chegam com o mesmo IP, e alguém errando a senha 5 vezes bloqueia você por 15 min | Use VPN (Tailscale) em vez de expor atrás de proxy público. Se usar proxy, o próximo passo seria ler o IP real do cabeçalho do proxy |
| Sessões ficam em memória | Reiniciar o processo desloga | Aceitável para 1 operador |
| Senha em texto no `.env` | Quem ler o arquivo tem acesso ao painel | Proteja o arquivo. Guardar só o hash da senha seria uma melhoria simples |
| Sem 2º fator | A senha é a única barreira | Mantenha o painel só local ou via VPN |
| HSTS só com `COOKIE_SECURE=true` | Em HTTP puro não há HSTS nem cookie Secure | Ligue essa opção quando houver HTTPS |
| ~20% menos vazão | Irrelevante no uso previsto | — |
| 4 funções com complexidade C (11–12) | Manutenção um pouco mais cuidadosa | `monitor_sent`, `parse_item`, `search`, `cli.main` |

---

## 7. Limites desta avaliação

- **Docker não foi construído:** o daemon subiu, mas o download da imagem base (`python:3.12-slim`) foi bloqueado pela rede deste ambiente. Dockerfile e compose foram revisados, não executados. Na sua máquina: `docker compose up -d --build` e depois `docker compose ps` (a coluna de status deve mostrar `healthy`).
- **API real da Amazon:** continua testada só com respostas simuladas no nível HTTP.
- Servidor e gerador de carga dividiram 2 vCPU, então os números absolutos são conservadores e a comparação v1 × v2 é justa (mesmo ambiente).
- A carga de 100 mil posts é sintética. Uma fila real não chega a milhares de itens pendentes, porque eles expiram em 24h.

---

## 8. Como reproduzir

```bash
pytest -q --cov                       # 64 testes + cobertura
ruff check app tests && mypy app && bandit -r app -q && pip-audit -r requirements.lock
qa/start.sh 8766 qa/run/dast.db && python3 qa/dast.py http://localhost:8766      # 18 ataques
python3 qa/avail.py                                                               # 12 cenários de falha
qa/start.sh 8767 qa/run/perf.db && python3 qa/load.py http://localhost:8767 qa/results/x.json '[{"path":"/","c":10}]'
python3 qa/volume.py qa/run/vol.db 100000                                         # volume + expurgo
qa/stop.sh
```

Saídas brutas: `qa/results/` (v1.1) e `qa/results_v1/` (v1.0). Os scripts de QA agora usam caminhos relativos ao projeto.

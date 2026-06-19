# Changelog

Todas as mudanças relevantes deste projeto estão documentadas aqui.  
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).

---

## [Insight Engine + WIP] — 2026-06-18

### Added
- **Página WIP** com Diagrama de Fluxo Cumulativo (CFD) semanal por status, KPIs
  de volume atual vs. limite ideal, seção de diagnóstico & recomendação integrada
  ao InsightEngine e expander técnico "Como esses limites são calculados?" discreto.
- **`reconstruct_wip_history`** em `status_time.py`: reconstrói snapshots semanais de
  WIP por status/time a partir do histórico de transições, usando `_status_at()` para
  determinar o status de cada issue em qualquer ponto no tempo.
- **`compute_wip_limit`** em `core_metrics.py`: aplica a Lei de Little por status
  (`limite = ceil(throughput_diário × tempo_médio_no_status)`), mínimo 1.
- **`build_wip_diagnostics`** em `core_metrics.py`: 3 regras — status acima do limite,
  pipeline near-done vazio enquanto etapas iniciais acumulam, WIP total > 1,5× média
  histórica das últimas 4 semanas.
- **`InsightEngine._analyze_wip`**: analisador WIP integrado ao engine existente.
- **`InsightEngine._dedup_by_status`**: deduplicação automática de insights que apontam
  para o mesmo status em categorias diferentes (ex.: WIP Rule 1 + Throughput Rule 2
  disparando para "Em testes" simultaneamente). Mantém o de maior severidade; em empate,
  prefere `wip` (tem evidência quantitativa de limite vs. heurística de concentração).
- **`test_wip.py`**: 21 testes unitários cobrindo `reconstruct_wip_history`,
  `compute_wip_limit` e `build_wip_diagnostics`.
- **`render_context_bar()`** em `squad_health.py`: barra com time ativo e período
  selecionado, exibida no topo de todas as páginas.

### Changed
- **Home — cap de 4 cards de diagnóstico**: exibe apenas os 4 insights de maior
  severidade; excedente gera link "Ver todos os diagnósticos →" apontando para a página
  da categoria do primeiro evento cortado.
- **Home — dedup anterior removida**: o loop `_seen_bk` que deduplicava apenas por
  `bottleneck_status` foi substituído pela deduplicação do engine (que cobre também
  `evidence["status"]` dos eventos WIP).
- **Linguagem visível sem jargão técnico**: "Lei de Little" removida de todos os textos
  que o usuário vê diretamente nos diagnósticos, KPIs e introdução da página WIP.
  O termo aparece apenas no expander técnico para quem quiser se aprofundar.
- Seletor visual do sidebar reestilizado com destaque indigo para indicar o filtro ativo.

---

## [Subtasks + CFR por status terminal] — 2026-06-18

### Added
- **Suporte a subtasks**: `db.py` ganha coluna `parent_key`; `jira_client.normalize_issue`
  captura `fields.parent.key`; `sync_and_snapshot.py` sincroniza subtasks e faz herança
  de time do pai (subtasks herdam o time da História pai; fallback round-robin para
  subtasks órfãs).
- **3 novas regras de diagnóstico de Aging** voltadas a subtasks:
  - Regra A (high): Histórias com todas as subtasks Concluídas mas a pai ainda aberta.
  - Regra B (high): Subtasks em Code Review há mais de 5 dias.
  - Regra C (critical): Histórias em Revisão de Produto / Pronto pra produção há mais
    de 3 dias sem avançar.
- **`test_subtasks.py`**: 12 testes unitários para `normalize_issue` e herança de time.
- **`generate_demo_data.py`** atualizado com fluxo completo de Histórias (13 status),
  Subtasks (4 status) e 3 cenários diagnósticos plantados por time nos meses recentes.

### Fixed
- **CFR agora usa status terminal de GMUDs, não `data_implantacao`**: o campo
  `data_implantacao` fica vazio no Jira real da empresa. A função `calculate_cfr` em
  `metrics.py` passou a usar `resolutiondate` de GMUDs com status terminal
  ("Implantado com Sucesso" / "Implantado com Falha") agrupados por mês de conclusão.
  Antes, CFR mostrava N/A sempre; agora oscila com valores reais.
- `TERMINAL_STATUSES` em `core_metrics.py` adicionou `"implantado com sucesso"` e
  `"implantado com falha"` para cobrir o workflow real de GMUDs.
- `calculate_mttr` retorna `incidente_count` para ponderação correta no
  `aggregate_metrics_by_month`.

---

## [Dataset de demonstração + deploy Streamlit Cloud] — 2026-06-18

### Added
- **`metrics_demo.db`**: banco SQLite sintético (gerado por `scripts/generate_demo_data.py`)
  commitado no repositório para uso no Streamlit Cloud. Contém 836 issues, 385 subtasks,
  CFR real variando por time e mês, cenários diagnósticos pré-plantados.
- `.gitignore` atualizado com `!metrics_demo.db` para permitir o demo enquanto mantém
  `*.db` bloqueando `metrics.db` (dados reais e credenciais nunca entram no repositório).

### Changed
- `requirements.txt` removeu `pytest` (dependência de dev, não necessária em produção
  no Streamlit Cloud).

---

## [Docker + seletor de time global] — 2026-06-18

### Added
- **`Dockerfile`** e **`docker-compose.yml`**: imagem baseada em `python:3.12-slim`,
  usuário não-root, porta 8501; volume para `metrics.db`; credenciais via `env_file`
  (nunca embutidas na imagem); serviço `sync` com `profile=sync` para execução sob
  demanda.
- **Seletor de time global no sidebar** (`st.session_state.global_team`): todas as
  páginas leem o time selecionado deste estado compartilhado em vez de ter seletores
  locais independentes. Badge do Aging na sidebar filtra pelo time selecionado.
- **`db.py` — variável de ambiente `DASHBOARD_DB_PATH`**: caminho do SQLite configurável
  via env var, necessário porque o banco está em `C:\Projetos` (fora do OneDrive para
  evitar conflitos de lock do SQLite com sync na nuvem) e o Streamlit Cloud precisa
  apontar para um path diferente.
- **`scripts/generate_demo_data.py`**: gerador completo de dados sintéticos para demo.
- **`test_smoke.py`**: smoke tests via `AppTest` — 5 páginas × 4 seleções de time = 20
  cenários, garantindo que nenhuma página explode na inicialização.

### Changed
- Seletores locais de time removidos de todas as páginas; cada uma passa a ler
  `st.session_state.global_team`.
- `.dockerignore` exclui `.env` e `metrics.db` da imagem para garantir que credenciais
  e dados reais não entrem em nenhuma camada da imagem.
- `requirements.txt` limpo: removidos `plotly`, `fastapi`, `uvicorn` (não usados);
  adicionados `numpy` explicitamente e `pytest`.

---

## [Diagnósticos e Recomendações — Throughput, Aging, DORA] — 2026-06-18

### Added
- **`build_throughput_diagnostics()`** em `core_metrics.py`: função pura (sem Streamlit),
  testável isoladamente. 3 regras: Aging × Throughput correlacionado, gargalo por
  concentração de status (`diagnose_status_concentration()`), previsibilidade baixa.
- **`build_aging_diagnostics()`** em `core_metrics.py`: 3 regras: gargalo de status,
  tendência de piora vs. mês anterior (via `metric_snapshots`), itens sem movimentação.
- **`build_dora_diagnostics()`** em `core_metrics.py`: 3 regras: faixa DORA piorou,
  faixa DORA melhorou, cruzamento CFR × frequência de deploy no mesmo período.
- Seção "Diagnóstico & Recomendação" adicionada às páginas Throughput, Aging e
  DORA Executivo, usando as funções acima diretamente.
- **`test_throughput_diagnostics.py`** (19 testes), **`test_aging_diagnostics.py`**
  (26 testes), **`test_dora_diagnostics.py`** (22 testes): testes unitários sem DB
  nem Streamlit.
- Aging persiste `aging_avg_age`, `aging_pct_critical`, `aging_total_open` em
  `metric_snapshots` no sync para viabilizar comparação mês a mês.

### Changed
- **InsightEngine refatorou o retorno dos `build_*_diagnostics`**: antes retornavam
  `(list[str], list[str])` (textos de diagnóstico e recomendação); após introdução do
  InsightEngine passaram a retornar `list[InsightEvent]` com id, severity, category,
  layer, evidence e related_ids. Isso permite deduplicação automática e rastreabilidade
  entre evento, diagnóstico e recomendação na Home.
- `diagnose_status_concentration()` extraída como helper compartilhado entre
  Throughput e Aging em vez de duplicada nas duas páginas.

---

## [Migração completa do CSV para o banco real] — 2026-06-17 a 2026-06-18

### Changed
- **Todas as páginas migradas de CSV para SQLite**: Home, DORA Executivo, Throughput,
  Aging e Squad Health passaram a ler exclusivamente de `issues_raw` via `db.engine` e
  `prepare_df()`, com cache de 300s. Badge do Aging na sidebar migrado também.
- **`squad_health.py` reduzido de ~320 para ~36 linhas**: toda a lógica de scoring
  movida para `core_metrics.squad_health_score()`; o arquivo passou a ser puro
  adaptador/renderizador.
- **CFR sem dados redistribui peso** em vez de assumir 50 neutro: os 15% do CFR são
  redistribuídos proporcionalmente entre as outras 4 métricas quando `cfr_percent` for
  None. A UI exibe chip "Sem dados" em vez de um score que parecia calculado.
- `metric_snapshots` renomeou o `metric_name` de `"throughput"` para
  `"deployment_count"`: o campo armazenava contagem de deploys de GMUD (não o
  throughput de issues concluídas, que é calculado em tempo real de `issues_raw`).
  O nome antigo causava ambiguidade; linhas órfãs foram removidas do banco.
- **Throughput** integra round-robin de times ao fluxo de sync: o campo Team do Jira
  retorna null para todas as issues do projeto. `assign_teams_round_robin()` em
  `sync_and_snapshot.py` atribui times ao final de cada sync (ordenação numérica por
  chave, ciclo fixo), preservando atribuições existentes se algum issue já tiver time
  real do Jira.

### Removed
- **`loader.py` e `data/jira_issues_synthetic.csv`** removidos: com todas as páginas
  lendo do banco, nenhum módulo rastreado importava mais `loader.py`. O CSV sintético
  foi gerado originalmente para desenvolvimento local antes do banco existir; foi
  aposentado sem substituição direta (o banco de demo passou a ser `metrics_demo.db`).
- **`api/main.py` deletado localmente sem ter sido commitado**: existia como experimento
  de API REST (FastAPI/Uvicorn) para expor métricas; descartado porque o dashboard
  Streamlit atende o caso de uso e uma API separada adicionaria complexidade de deploy
  sem benefício imediato.

---

## [Centralização em core_metrics.py + issue_transitions] — 2026-06-17

### Added
- **`core_metrics.py`**: módulo puro e testável extraindo toda a lógica de métricas
  das páginas. Exporta `prepare_df`, `compute_throughput`, `compute_aging`,
  `dora_band`, `squad_health_score`, `TERMINAL_STATUSES` e funções de diagnóstico.
  Páginas e testes importam daqui; o módulo não faz chamadas ao Jira.
- **`TERMINAL_STATUSES`** (frozenset de nomes de status em lowercase): substitui a
  verificação por `statusCategory.key == 'done'` que falhava para workflows migrados
  do Jira. O `statusCategory.key` retorna `'indeterminate'` para status migrados
  (ex.: "Feito (migrated)"), então 431 issues ficavam com `is_resolved=False`,
  distorcendo Lead Time, MTTR e Throughput. A mesma constante substitui o frozenset
  `_TERMINAL` de `pages/fluxo.py`, eliminando duas definições independentes que podiam
  divergir.
- **`db.py` — tabela `issue_transitions`**: armazena `from_status`, `to_status`,
  `changed_at` com índices em `issue_key` e `(team, changed_at)`.
- **`status_time.py`**: `time_in_status()`, `average_time_in_status()` e
  `lead_time_real()` para análise de gargalos por tempo médio em cada etapa.
- **`pages/fluxo.py`**: página de Fluxo de Trabalho com gráfico de tempo médio por
  status e identificação do status dominante.
- **`jira_client.py`** ganhou `fetch_all_issues` com suporte a `nextPageToken` e
  guard contra chaves duplicadas; `extract_status_transitions` e
  `_fetch_changelogs_batched` para coleta de histórico de transições em lotes de 50.
- **`test_core_metrics.py`** (75 testes), **`test_transitions.py`** (19 testes),
  **`test_status_time.py`**: suite de testes unitários cobrindo os módulos centrais.
- `scripts/diagnose_status_categories.py`: script de diagnóstico que identificou a
  causa raiz do bug de `resolutiondate` (status `'indeterminate'` nos workflows
  migrados).
- **`docs/metricas.md`**: documentação técnica completa com fórmulas, faixas DORA,
  Squad Health, pipeline de snapshot, limitações conhecidas e glossário.

### Fixed
- **Bug de `resolutiondate`**: 431 issues (83 Incidentes, 348 Histórias, 64 GMUDs)
  com status terminal mas sem `resolutiondate` preenchido no Jira passaram a ter
  `is_resolved=True` via `TERMINAL_STATUSES`. Lead Time, MTTR e Throughput agora
  calculam sobre a base completa de issues concluídas.

### Changed
- **`metrics.py` — CFR e Deployment Frequency exigem `data_implantacao` explícito**:
  o `fillna(resolutiondate)` foi removido. Antes da correção do bug de
  `resolutiondate`, esses campos eram sempre nulos; após a correção passaram a ser
  preenchidos, causando falsos positivos (64 GMUDs contados como deploys sem nenhum
  campo de data de implantação no Jira). CFR e Deploy Freq voltaram a exibir N/A
  nesse commit (resolvido definitivamente no marco "Subtasks + CFR por status terminal").

---

## [Setup inicial — Jira, sync, banco e páginas base] — 2026-06-16 a 2026-06-17

### Added
- **`db.py`**: modelos SQLAlchemy para `issues_raw` e `metric_snapshots`. Escolha por
  SQLite com SQLAlchemy (em vez de pandas direto em CSV ou uma API externa) pelo
  caminho natural de migração para PostgreSQL sem alterar o código das páginas — basta
  trocar a connection string.
- **`sync_and_snapshot.py`**: pipeline Jira → `issues_raw` (upsert por `key`) e
  snapshot de métricas com imutabilidade `finalized=True` e flag `--force-recalculate-period`.
- **`jira_client.py`**: cliente HTTP com autenticação Basic, paginação, normalização de
  issues e cálculo de `resolutiondate` a partir de `statusCategory` (substituído no
  marco de centralização em core_metrics).
- **`pages/dora_executivo.py`**, **`pages/throughput.py`**, **`pages/aging.py`**:
  primeiras páginas do dashboard multi-página via `st.navigation`.
- **`squad_health.py`**: score ponderado de saúde do time (Lead Time 25%, Throughput
  20%, Aging 25%, MTTR 15%, CFR 15%), tendência, atribuição de impacto em pontos.
- **`pages/home.py`**: página inicial com Squad Health, cards de resumo por seção,
  maior oportunidade de melhoria e alertas dinâmicos.
- **`test_snapshots.py`**: 9 testes cobrindo imutabilidade de snapshot, atualização do
  período atual, finalização e idempotência de `issues_raw`.
- `.streamlit/config.toml`: layout `wide`, tema base `light`.
- Lead Time calculado em dias úteis; CFR em percentual de deploys com falha.
- `scripts/legacy/`: agrupa `main.py`, `debug_metrics.py`, `diagnose_jira_df.py`,
  `jira_diagnostics.py`, `jira_inspect.py`, `jira_test_fields.py` e
  `generate_synthetic_jira.py` (inativos após migração para o banco).

### Changed
- `app.py` migrado de single-page para multi-page com `st.navigation` e agrupamento
  de páginas por seção.
- **Banco SQLite movido para `C:\Projetos\Metricas-dashboard\`** (fora do OneDrive):
  o SQLite usa lock de arquivo exclusivo durante writes; o cliente de sync do OneDrive
  causava conflitos de lock que corrompiam transações. O caminho é configurável via
  `DASHBOARD_DB_PATH` para que o Streamlit Cloud aponte para seu próprio storage.

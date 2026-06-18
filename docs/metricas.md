# Documentação Técnica — Métricas de Engenharia

## Índice

1. [Fontes de dados e pipeline](#1-fontes-de-dados-e-pipeline)
2. [Normalização de dados — `core_metrics.prepare_df`](#2-normalização-de-dados)
3. [Transições de status — `status_time.py`](#3-transições-de-status)
4. [Métricas DORA](#4-métricas-dora)
5. [Métricas de fluxo](#5-métricas-de-fluxo)
6. [Scoring — funções de pontuação](#6-scoring)
7. [Squad Health Score](#7-squad-health-score)
8. [Página Fluxo — diagnóstico de gargalo](#8-página-fluxo)
9. [Diagnósticos automáticos](#9-diagnósticos-automáticos)
10. [Pipeline de snapshot](#10-pipeline-de-snapshot)
11. [Status de migração por página](#11-status-de-migração-por-página)
12. [Limitações conhecidas do ambiente de teste](#12-limitações-conhecidas-do-ambiente-de-teste)
13. [Glossário](#13-glossário)

---

## 1. Fontes de dados e pipeline

### Campos usados do Jira

| Campo interno | Campo Jira | Descrição |
|---|---|---|
| `key` | `key` | Identificador único da issue (ex: `PROJ-123`) |
| `issuetype` | `issuetype.name` | Tipo: `Story`, `Bug`, `Task`, `Incidente`, `GMUD` |
| `team` | Campo customizado `Team` | Time responsável |
| `status` | `status.name` | Status atual da issue |
| `created` | `fields.created` | Data/hora de criação |
| `resolutiondate` | `fields.resolutiondate` | Data/hora de resolução (null se em aberto) |
| `data_implantacao` | Campo customizado `Data de Implantação` | Data de deploy em produção (só GMUDs) |
| `updated` | `fields.updated` | Data/hora da última modificação |

### Tabelas SQLite

**`issues_raw`**: cópia integral das issues do Jira. Truncada e recarregada a cada sync.

**`metric_snapshots`**: snapshots imutáveis de métricas por período/time. Período passado → `finalized=True` → imutável.

**`issue_transitions`**: histórico de mudanças de status, uma linha por transição.

| Coluna | Tipo | Descrição |
|---|---|---|
| `issue_key` | string | Chave da issue (ex: `PROJ-123`) |
| `from_status` | string | Status de origem |
| `to_status` | string | Status de destino |
| `changed_at` | datetime | Quando a transição ocorreu |

### Modo offline (dados sintéticos)

Quando não há `.env` configurado, o app usa dados gerados por `generate_synthetic_jira_node.js`.

---

## 2. Normalização de dados

### `core_metrics.prepare_df(issues_df)`

Adiciona colunas derivadas usadas em todo o módulo. Idempotente — pode ser chamada em um DataFrame já preparado.

| Coluna adicionada | Tipo | Derivação |
|---|---|---|
| `year_month` | `str "YYYY-MM"` | `created.dt.to_period("M")` |
| `is_resolved` | `bool` | `resolutiondate.notna()` |
| `data_implantacao` | `datetime\|NaT` | Garantida; `pd.NaT` onde ausente |

Também converte `created`, `resolutiondate`, `updated`, `data_implantacao` para `datetime` (sem timezone).

---

## 3. Transições de status

### `TERMINAL_STATUSES` (em `core_metrics.py`)

Conjunto canônico de nomes de status considerados "concluído/final":

```python
TERMINAL_STATUSES = frozenset({
    "feito", "concluído", "concluido", "done", "fechado", "closed",
    "resolvido", "resolved", "completo", "completed",
})
```

**Por que não confiamos em `statusCategory` do Jira:**  
A API do Jira retorna uma `statusCategory` para cada status (`todo`, `indeterminate`, `done`). Na prática, encontramos o status `"Feito (migrated)"` classificado como `indeterminate` (em vez de `done`) — provavelmente porque foi criado durante uma migração de projeto e o Jira não atualizou sua categoria. Usar `statusCategory` para determinar se uma issue está concluída produziria falsos negativos nesse caso. A lista `TERMINAL_STATUSES` é baseada no nome do status (lowercased, após remover o sufixo ` (migrated)`), não na categoria do Jira.

A função `jira_client._normalize_migrated` remove o sufixo ` (migrated)` antes da checagem, então `"Feito (migrated)"` → `"feito"` → está em `TERMINAL_STATUSES`.

### `status_time.time_in_status(issue_key, created, transitions, now, initial_status)`

Reconstrói a linha do tempo de uma issue e retorna quanto tempo ela passou em cada status (`dict[str, timedelta]`).

**Algoritmo de reconstrução:**

```
[created → transitions[0].changed_at]      → status = transitions[0].from_status
[transitions[i-1] → transitions[i]]        → status = transitions[i-1].to_status
[transitions[-1] → now]                    → status = transitions[-1].to_status
```

Se uma issue revisita um status (rework), os tempos acumulam. Durações negativas (problemas de qualidade de dados) são zeradas.

### `status_time.average_time_in_status(issues, now, team, issuetype)`

Calcula o tempo médio por status em um conjunto de issues. Issues que nunca passaram por um status são excluídas da média daquele status (não contribuem como zero).

Cada issue deve ter:
- `issue_key`, `created`, `resolutiondate` (ou `None`), `team`, `issuetype`, `transitions`

Quando `resolutiondate` está preenchida, usa-a como fim da janela de observação — issues resolvidas não acumulam tempo no status final indefinidamente.

### `status_time.lead_time_real(issue_key, transitions, start_status, end_status)`

Calcula o lead time entre quando a issue entra em `start_status` pela primeira vez e quando entra em `end_status` pela primeira vez depois disso.

Retorna `None` quando a issue nunca passou pelo par `(start_status, end_status)`. Callers devem excluir `None` das médias — a issue genuinamente não tem lead time mensurável para esse intervalo.

---

## 4. Métricas DORA

### `core_metrics.dora_band(key, value)`

Retorna `Elite / High / Medium / Low / N/A` para um valor de métrica DORA.

| Métrica | Elite | High | Medium | Low |
|---|---|---|---|---|
| `lead_time_days` | < 1d | 1–7d | 7–30d | > 30d |
| `deploy_freq_interval` | ≤ 1d | ≤ 5d | ≤ 20d | > 20d |
| `mttr_hours` | < 1h | < 24h | < 168h | ≥ 168h |
| `cfr_percent` | 0–15% (inclusive) | 16–30% | 31–45% | > 45% |

### `core_metrics.worst_dora_band(dora_values)`

Retorna `(pior_faixa, chave_da_pior_métrica)` ignorando valores `N/A`.

### 4.1 Lead Time for Changes

**O que mede:** tempo entre criação e resolução de uma entrega.

**Tipos considerados:** `Story`, `Bug`, `Task`, `História`, `Tarefa`

```
lead_time_dias = dias_úteis(created, resolutiondate)   # pandas.bdate_range
Lead Time (mês) = média(lead_time_dias) de issues resolvidas no mês
```

### 4.2 Deployment Frequency

**Tipo de issue:** `GMUD` com `data_implantacao` preenchida (fallback: `resolutiondate`).

```
deployment_count(mês) = GMUDs com data de implantação no mês
deploy_freq_interval  = dias_no_mês / deployment_count
```

### 4.3 MTTR — Mean Time to Restore

**Tipo de issue:** `Incidente` com `resolutiondate` preenchida.

```
mttr_horas = (resolutiondate − created).total_seconds() / 3600
MTTR (mês) = média(mttr_horas) ponderada por contagem de incidentes por time
```

### 4.4 Change Failure Rate (CFR)

```
CFR(mês) = incidentes_criados_no_mês / GMUDs_implantadas_no_mês × 100
```

Quando não há GMUD no mês, CFR é `null` (não calculado).

---

## 5. Métricas de fluxo

### `core_metrics.compute_throughput(issues_df, team, start_month, end_month)`

Análise completa de throughput sobre issues resolvidas.

**Base temporal:** `resolutiondate` (não `created`).

**WIP:** o mês mais recente é sempre excluído de médias, tendência e saúde. Aparece no gráfico com legenda "em andamento". Exceção: se só há 1 mês, ele é tratado como fechado.

**Retorna:**

| Chave | Tipo | Descrição |
|---|---|---|
| `monthly` | `list[dict]` | Todos os meses com `{month, label, count, is_wip}` |
| `closed` | `list[dict]` | Idem, sem o mês WIP |
| `avg` | `float` | Média sobre meses fechados |
| `cv` | `float` | Coeficiente de variação `std/avg` |
| `best` / `worst` | `dict` | Melhor/pior mês fechado |
| `trend` | `dict` | Resultado de `compute_trend` |
| `health` | `dict` | Resultado de `compute_throughput_health` |
| `predictability` | `dict` | Resultado de `compute_predictability` |
| `wip` | `dict\|None` | Mês WIP |

### `core_metrics.compute_trend(counts, avg_val)`

| Tendência | Critério |
|---|---|
| Crescimento | Últimos 3 meses todos acima de `avg_val` |
| Queda | Últimos 2 meses ambos abaixo de `avg_val` |
| Estável | Demais casos |

### `core_metrics.compute_throughput_health(trend_label, last_count, avg_val, cv)`

| Status | Critério |
|---|---|
| Crítica | `last < 50% avg` OU (`Queda` E `last < 70% avg`) |
| Boa | (`Crescimento` ou `Estável`) E `cv < 40%` |
| Atenção | Demais casos |

### `core_metrics.compute_predictability(cv)`

| Previsibilidade | CV |
|---|---|
| Alta | < 15% |
| Média | 15%–30% |
| Baixa | > 30% |

### `core_metrics.diagnose_throughput_drop(period_months, tp_by_month, issues_df)`

Decomposição heurística de uma queda de throughput em causas correlacionadas.

**Algoritmo:**

1. Para cada fator `f` ∈ {Aging, Bugs, Incidentes}:
   `desvio_f = max(0, (f_último − f_média) / f_média)`
2. `força_total = Σ desvio_f`
3. `delta_tp = (tp_média − tp_último) / tp_média`
4. `fração_explicada = min(1, força_total / delta_tp)`
5. Cada fator recebe `fração_explicada × (desvio_f / força_total) × 100%`; o restante vai para "Variação normal"

Retorna `[]` quando não há queda (`delta_tp ≤ 0`) ou menos de 3 meses.

### `core_metrics.compute_aging(issues_df, today, team, issuetype)`

Análise de issues em aberto.

**Campo base:** `created` (não mudança de status).

**Faixas de tempo:**

| Faixa | Critério |
|---|---|
| `0–7d` | `dias_parado < 7` |
| `7–14d` | `7 ≤ dias_parado < 14` |
| `14–30d` | `14 ≤ dias_parado ≤ 30` |
| `30–60d` | `30 < dias_parado ≤ 60` |
| `60+d` | `dias_parado > 60` |

**`sem_movimento`:** issues abertas com `updated` há mais de 14 dias. `None` quando a coluna `updated` está ausente ou toda nula.

**Diagnóstico de sobre-representação:** para cada Tipo e Time, compara `% no crítico (>30d)` com `% no total`. Flagra quando `diferença ≥ 15 pp` e `n_red ≥ 1`.

---

## 6. Scoring

Todas as funções de scoring são puras (recebem um número, retornam um número em [0, 100]).

### `core_metrics._score_lower_better(v, elite_hi, high_hi, medium_hi, elite_inclusive)`

Normaliza métricas do tipo "menor é melhor" para [0–100]:

```
Elite  → 90–100  (v=0 → 100; v=elite_hi → 90)
High   → 70–89
Medium → 50–69
Low    → 0–49    (decai linearmente até 0 em 2 × medium_hi; floor 0)
```

### `core_metrics.score_lead_time(days)` / `score_mttr(hours)` / `score_cfr(pct)`

Wrappers de `_score_lower_better` com os limiares DORA:

| Função | Elite | High | Medium |
|---|---|---|---|
| `score_lead_time` | < 1d | 7d | 30d |
| `score_mttr` | < 1h | 24h | 168h |
| `score_cfr` | ≤ 15% | 30% | 45% |

### `core_metrics.score_throughput(all_counts, window_counts)`

```
baseline = média da primeira metade de all_counts
pct_change = (window_avg − baseline) / baseline × 100
score = 70 + pct_change × 0.5   (cap 100, floor 0, assimétrico: +10% → +5pt, −10% → −8pt)
```

Retorna 70.0 quando qualquer lista é vazia.

### `core_metrics.score_aging(open_df)`

```
score = 100 − (pct_red × 80 + pct_yellow × 30),  floor 0
```
`pct_red` = fração com `dias_parado > 30`, `pct_yellow` = fração com `7 ≤ dias_parado ≤ 30`.

### `core_metrics.metric_status(score)` / `health_status_label(score)`

| Score | `metric_status` | `health_status_label` |
|---|---|---|
| ≥ 90 | 🟢 Boa | Excelente |
| ≥ 70 | 🟢 Boa | Boa |
| ≥ 50 | 🟡 Atenção | Atenção |
| < 50 | 🔴 Crítica | Crítica |

---

## 7. Squad Health Score

### `core_metrics.squad_health_score(issues_df)`

Score consolidado [0–100] como média ponderada de 5 métricas.

**Pesos (configuráveis em `SQUAD_HEALTH_WEIGHTS`):**

| Métrica | Peso |
|---|---|
| Lead Time | 25% |
| Throughput | 20% |
| Aging | 25% |
| MTTR | 15% |
| CFR | 15% |

**Janela de cálculo:** últimos 3 meses com dados DORA disponíveis (Throughput usa meses fechados, excluindo WIP).

### Política de dados ausentes

| Métrica | Sem dados na janela |
|---|---|
| Lead Time | Fallback 50 (neutro) |
| MTTR | Fallback 50 (neutro) |
| CFR | **Excluída — peso redistribuído** |

**Decisão sobre CFR=N/A:** atribuir 50 (neutro) ao CFR quando não há GMUDs com `data_implantacao` seria afirmar silenciosamente que o time é "médio" em estabilidade de deploys — uma afirmação que os dados não sustentam. A verdade é que a métrica simplesmente não pode ser medida. Por isso, quando `cfr_val is None`, o CFR é excluído do cálculo e seu peso de 15% é redistribuído proporcionalmente entre as outras 4 métricas. O retorno inclui `cfr_excluded=True` para que a UI exiba o chip do CFR com "Sem dados" em vez de um score colorido.

Esta decisão está também comentada no código em `core_metrics.py` na função `squad_health_score`.

### Tendência

Compara o score da janela atual com os 3 meses anteriores:

| Tendência | Critério |
|---|---|
| ↑ Subindo | Diferença > 5 pontos |
| ↓ Caindo | Diferença < −5 pontos |
| → Estável | Demais casos |
| → Sem histórico | Sem janela anterior |

### Principais impactos

```
delta_ponderado[k] = (score_atual[k] − score_anterior[k]) × peso[k]
```

Exibe os 3 maiores `|delta| ≥ 0.5`, positivos ou negativos.

### Retorno

| Chave | Tipo | Descrição |
|---|---|---|
| `score` | `float` | Score 0–100 |
| `status` | `str` | Excelente / Boa / Atenção / Crítica |
| `trend` | `str` | ↑ Subindo / → Estável / ↓ Caindo / → Sem histórico |
| `metrics` | `dict` | Breakdown por métrica: `{label, score, status, emoji, value, unit}` |
| `impacts` | `list[dict]` | Top-3 drivers: `{key, label, delta_points}` |
| `window` | `list[str]` | Meses DORA usados na janela atual |
| `prev_score` | `float\|None` | Score da janela anterior |
| `cfr_excluded` | `bool` | `True` quando CFR foi excluído do scoring |
| `current_dora_month` | `str` | Último mês com dados de LT ou MTTR |
| `current_month_dora` | `dict` | Valores DORA desse mês |

---

## 8. Página Fluxo — diagnóstico de gargalo

### Fonte de dados

Lê `issues_raw` + `issue_transitions` diretamente do SQLite. Delega todo o cálculo a `status_time.average_time_in_status` e `status_time.time_in_status`.

### Seção 1 — Tempo por Status

Para cada status ativo (não terminal), exibe:
- **Média** de tempo (barra horizontal, cor roxa)
- **Mediana** (traço laranja sobreposto)

Para issues sem histórico de transições, o tempo é calculado desde a criação até agora com o status atual como único estado.

### Seção 2 — Itens em Aberto Agora

Volume de issues em aberto agrupado por status atual. Status terminais aparecem por último com cor cinza.

### Seção 3 — Diagnóstico de Gargalo

**Gargalo** = status ativo (não terminal) com maior tempo médio.

**Limiar de severidade relativo:**

```
all_mean_secs = média dos tempos médios de TODOS os status ativos
ratio = bottleneck_mean / all_mean_secs
```

| Severidade | Critério |
|---|---|
| Crítico | `ratio > 2.0` (mais que o dobro da média geral) |
| Atenção | `ratio > 1.5` (50% acima da média) |
| Normal | `ratio ≤ 1.5` (comparável aos demais) |

Usar a média geral (incluindo o próprio gargalo) como referência evita inflar a severidade quando um status é massivamente dominante.

---

## 9. Diagnósticos automáticos

Todas as páginas de métricas exibem uma seção **"Diagnóstico & Recomendação"** gerada por funções puras em `core_metrics.py` — sem Streamlit, testáveis com dados forjados. O padrão é idêntico em todos os módulos: retornam `(diag_items, rec_items)` — listas paralelas de strings, um item por regra disparada.

### 9.1 `build_throughput_diagnostics(closed_list, df, team, pred)`

Três regras independentes:

| Regra | Condição | Exemplo de diagnóstico |
|---|---|---|
| **1 — Aging × TP** | TP subiu E pct_crit < 20% → positivo; TP caiu E pct_crit > 30% → negativo | "Os itens abertos estão sendo resolvidos mais rápido…" |
| **2 — Gargalo** | Status não-terminal com mais de 2× a média de itens abertos | "Muitos itens estão ficando parados em **Em Revisão**, o que pode estar represando…" |
| **3 — Previsibilidade** | `pred["label"] == "Baixa"` (CV > 30%) | "O volume de entregas tem variado bastante…" |

### 9.2 `build_aging_diagnostics(df, team, issuetype, today, prev_aging)`

Três regras independentes:

| Regra | Condição | Notas |
|---|---|---|
| **1 — Gargalo** | Status não-terminal com > 2× média | Reutiliza `diagnose_status_concentration` |
| **2 — Tendência** | `avg_age` ou `pct_critical` mudaram ≥ limiar vs. `prev_aging` | **Worsened + gargalo ativo:** incorpora nome do status na frase. **Improved + pct_crit > 50%:** qualifica com "ainda é crítica" |
| **3 — Sem movimentação** | > 20% dos itens abertos sem `updated` nos últimos 14 dias | Silencioso quando coluna `updated` ausente |

**`prev_aging`:** lido de `metric_snapshots` (`aging_avg_age`, `aging_pct_critical`, `aging_total_open`) para o período anterior. Gravado no sync via `compute_aging`. Fallback: `compute_aging(df, today=today-30d)` quando não há snapshot.

**Guard:** quando `prev_aging["avg_age"] < 0` (artefato de migração — itens criados após a data de referência), a Regra 2 é silenciada.

Constantes de limiar:

| Constante | Valor | Uso |
|---|---|---|
| `_AGING_TREND_AGE_DELTA` | `1.0d` | Variação mínima de `avg_age` para disparar Regra 2 |
| `_AGING_TREND_CRIT_DELTA` | `0.02` | Variação mínima de `pct_critical` para disparar Regra 2 |
| `_AGING_STILL_CRITICAL_THRESHOLD` | `0.50` | Acima desse valor, a mensagem de melhoria é qualificada |

### 9.3 `build_dora_diagnostics(current, prev)`

Compara dois dicts com as chaves `lead_time_days`, `deploy_freq_interval`, `mttr_hours`, `cfr_percent`. Três regras independentes:

| Regra | Condição | Notas |
|---|---|---|
| **1 — Faixa em deterioração** | Faixa DORA piorou vs. `prev` (por métrica) | Recomendação específica por métrica |
| **2 — Faixa em melhoria** | Faixa DORA melhorou vs. `prev` (por métrica) | Recomendação genérica "manter essa prática" |
| **3 — CFR × Deploy Freq** | `cfr_percent` subiu E `deploy_freq_interval` subiu (menos deploys) em valores brutos | Dispara independente de mudança de faixa; aborda a hipótese de cautela pós-falha |

**Política de N/A:** se `current[key]` ou `prev[key]` for `None`, as Regras 1/2 para aquela métrica são silenciadas. Se `cfr_percent` ou `deploy_freq_interval` for `None`, a Regra 3 é silenciada.

### 9.4 `diagnose_status_concentration(open_df, ratio_threshold=2.0)`

Auxiliar compartilhado por Throughput e Aging. Retorna o nome do status não-terminal que concentra mais de `ratio_threshold × média` dos itens abertos, ou `None`.

---

## 10. Pipeline de snapshot

### `sync_and_snapshot.py`

Execução:
```bash
python sync_and_snapshot.py
```

O que faz:
1. Busca issues na API do Jira e normaliza via `jira_client.py`
2. **Sincroniza transições de status** (`issue_transitions`) para a página Fluxo
3. **Atribui times automaticamente** via round-robin quando o campo `Team` está vazio (workaround para o ambiente de teste onde o campo customizado não é preenchido)
4. Grava/atualiza `issues_raw`
5. Calcula métricas DORA e grava snapshots em `metric_snapshots`

Para forçar recálculo de um período já finalizado:
```bash
python sync_and_snapshot.py --force-recalculate-period=2026-05
```

### Migrando para PostgreSQL

Alterar `DATABASE_URL` em `db.py`. O restante do código não precisa mudar (SQLAlchemy abstrai o dialeto).

---

## 11. Status de migração por página

| Página | Fonte de dados | Status |
|---|---|---|
| **Home** | SQLite (`issues_raw` via `db.engine` + `core_metrics`) | Migrado |
| **Throughput** | SQLite (`issues_raw` via `db.engine`) | Migrado |
| **Aging** | SQLite (`issues_raw` via `db.engine`) | Migrado |
| **Squad Health** (card) | SQLite (via `squad_health.compute_squad_health()` → `core_metrics`) | Migrado |
| **Fluxo** | SQLite (`issues_raw` + `issue_transitions`) | Migrado |
| **DORA Executivo** | SQLite (`metric_snapshots`) | Migrado |

---

## 12. Limitações conhecidas do ambiente de teste

| Área | Limitação | Causa | Workaround |
|---|---|---|---|
| **`created`** | Datas de criação congeladas na data da migração para o novo projeto Jira, em vez das datas originais | Campo `created` não é retroativo em migrações do Jira | Nenhum disponível; Lead Time e Aging ficam distorcidos |
| **`team`** | Campo customizado `Team` vazio para todas as issues | Campo não preenchido no projeto de teste | `sync_and_snapshot.py` atribui times via round-robin automático |
| **`data_implantacao`** | Campo vazio em todos os GMUDs | Customização não configurada no projeto de teste | CFR e Deployment Frequency ficam como `null`; CFR é excluído do Squad Health Score com peso redistribuído |
| **MTTR ≈ 0** | MTTR calculado como quase zero para incidentes | `updated == created` para issues migradas — o campo `resolutiondate` aponta para o momento da importação, não para a resolução real | Nenhum disponível; o MTTR no ambiente de teste não reflete a realidade |
| **Aging via `created`** | Dias em aberto medidos desde a criação, não desde a última mudança de status | Falta de histórico de transições detalhado por issue no CSV; `issue_transitions` resolve isso para a página Fluxo, mas `compute_aging` ainda usa `created` | Para o relatório de Aging, o valor é uma aproximação conservadora (superestima o tempo) |

---

## 13. Glossário

| Termo | Definição |
|---|---|
| **DORA** | DevOps Research and Assessment — framework de 4 métricas para medir performance de times de software |
| **Lead Time** | Tempo desde a criação de uma tarefa até sua resolução (em dias úteis) |
| **MTTR** | Mean Time to Restore — tempo médio para restaurar o serviço após um incidente |
| **CFR** | Change Failure Rate — percentual de deploys que causam incidentes |
| **Throughput** | Quantidade de itens concluídos por mês |
| **Aging** | Tempo que itens em aberto ficam sem avançar |
| **WIP** | Work in Progress — o mês atual, considerado incompleto e excluído de baselines |
| **Squad Health Score** | Score 0–100 que combina 5 métricas com pesos, resumindo a saúde do time |
| **Snapshot** | Registro imutável do valor de uma métrica em um período específico |
| **GMUD** | Gerenciamento de Mudança — tipo de issue que representa um deploy planejado |
| **CV** | Coeficiente de Variação — `std / média`, mede previsibilidade do throughput |
| **Faixa crítica (Aging)** | Issues abertas há mais de 30 dias |
| **Sobre-representação** | Quando um tipo/time tem proporção na faixa crítica ≥ 15 p.p. acima da proporção no total de abertos |
| **Gargalo** | Status ativo (não terminal) onde issues passam mais tempo em média |
| **TERMINAL_STATUSES** | Conjunto canônico de nomes de status considerados "concluído/final" — baseado no nome do status, não em `statusCategory` do Jira |
| **cfr_excluded** | Flag no retorno de `squad_health_score` indicando que CFR foi excluído do cálculo por falta de dados |

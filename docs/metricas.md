# Documentação Técnica — Métricas de Engenharia

## Índice

1. [Fontes de dados](#1-fontes-de-dados)
2. [Métricas DORA](#2-métricas-dora)
3. [Métricas de fluxo](#3-métricas-de-fluxo)
4. [Squad Health Score](#4-squad-health-score)
5. [Pipeline de snapshot](#5-pipeline-de-snapshot)
6. [Limitações e aproximações](#6-limitações-e-aproximações)
7. [Glossário](#7-glossário)

---

## 1. Fontes de dados

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

### Modo offline (dados sintéticos)

Quando não há `.env` configurado, o app usa `data/jira_issues_synthetic.csv`.  
O gerador `generate_synthetic_jira_node.js` produz ~600 issues distribuídas nos últimos 6 meses com proporções realistas de tipos e times.

---

## 2. Métricas DORA

### 2.1 Lead Time for Changes

**O que mede:** tempo entre a criação e a resolução de uma entrega de código.

**Tipos de issue considerados:** `Story`, `Bug`, `Task`, `História`, `Tarefa`

**Fórmula:**
```
lead_time_dias = dias_úteis(created, resolutiondate)
Lead Time (mês) = média(lead_time_dias) de todas as issues resolvidas no mês
```

> Usa `pandas.bdate_range` para contar apenas dias úteis (segunda a sexta).

**Faixas DORA:**

| Faixa | Critério |
|---|---|
| Elite | < 1 dia útil |
| High | 1 a 7 dias úteis |
| Medium | 7 a 30 dias úteis |
| Low | > 30 dias úteis |

---

### 2.2 Deployment Frequency

**O que mede:** frequência com que o time implanta código em produção.

**Tipo de issue:** `GMUD` com `data_implantacao` preenchida (ou `resolutiondate` como fallback).

**Fórmula:**
```
deployment_count(mês) = contagem de GMUDs com data de implantação no mês
deploy_freq_interval  = dias_no_mês / deployment_count
```

**Faixas DORA (por intervalo médio entre deploys):**

| Faixa | Critério |
|---|---|
| Elite | ≤ 1 dia |
| High | ≤ 5 dias |
| Medium | ≤ 20 dias |
| Low | > 20 dias |

---

### 2.3 MTTR — Mean Time to Restore

**O que mede:** tempo médio para resolver um incidente em produção.

**Tipo de issue:** `Incidente` com `resolutiondate` preenchida.

**Fórmula:**
```
mttr_horas = (resolutiondate - created).total_seconds() / 3600
MTTR (mês) = média(mttr_horas) ponderada pela contagem de incidentes por time
```

**Faixas DORA:**

| Faixa | Critério |
|---|---|
| Elite | < 1 hora |
| High | < 24 horas |
| Medium | < 168 horas (1 semana) |
| Low | ≥ 168 horas |

---

### 2.4 Change Failure Rate (CFR)

**O que mede:** proporção de deploys que geram incidentes.

**Fórmula:**
```
CFR(mês) = incidentes_criados_no_mês / GMUDs_implantadas_no_mês × 100
```

> Quando não há GMUD no mês, CFR é `null` (não calculado).

**Faixas DORA:**

| Faixa | Critério |
|---|---|
| Elite | 0% a 15% |
| High | 16% a 30% |
| Medium | 31% a 45% |
| Low | > 45% |

---

## 3. Métricas de fluxo

### 3.1 Throughput

**O que mede:** quantidade de itens de qualquer tipo resolvidos por mês.

**Base temporal:** `resolutiondate` (data de conclusão), não `created`.

**Exclusão de WIP:** o mês mais recente é sempre tratado como "em andamento" (WIP) e excluído de todas as comparações de média, tendência, saúde e diagnóstico. Ele aparece no gráfico com cor cinza e legenda "em andamento".

**Saúde do Throughput:**

| Status | Critério |
|---|---|
| Crítica | Último mês fechado < 50% da média, ou (Queda E último < 70% da média) |
| Boa | Tendência Crescimento ou Estável, e CV < 40% |
| Atenção | Demais casos |

**Tendência:**

| Tendência | Critério |
|---|---|
| Crescimento | Últimos 3 meses fechados todos acima da média |
| Queda | Últimos 2 meses fechados ambos abaixo da média |
| Estável | Demais casos |

**Previsibilidade** (Coeficiente de Variação):
```
CV = desvio_padrão / média  (meses fechados)
```

| Previsibilidade | CV |
|---|---|
| Alta | < 15% |
| Média | 15% a 30% |
| Baixa | > 30% |

**Diagnóstico de queda (heurístico):**

Identifica se a queda de throughput tem correlação com aumento de Aging (itens parados), Bugs ou Incidentes no período. Fórmula:

1. Para cada candidato `f` ∈ {Aging, Bugs, Incidentes}:
   `desvio_f = max(0, (f_atual − f_média) / f_média)`
2. `força_total = Σ desvio_f`
3. `delta_throughput = (tp_média − tp_atual) / tp_média`
4. `fração_explicada = min(1, força_total / delta_throughput)`
5. Cada causa recebe: `fração_explicada × (desvio_f / força_total) × 100%`
   "Variação normal" recebe `(1 − fração_explicada) × 100%`

> É uma correlação heurística, não uma prova causal.

---

### 3.2 Aging

**O que mede:** tempo que itens em aberto ficam sem avançar.

**Campo base:** `created` (data de criação). A contagem de "dias em aberto" começa na criação, não em mudanças de status.

**Faixas de cor:**

| Cor | Critério |
|---|---|
| Verde | Dias em aberto < 7 |
| Amarelo | 7 a 30 dias |
| Vermelho (crítico) | > 30 dias |

**KPI "Sem Movimentação":** itens abertos cujo campo `updated` (última modificação no Jira) tem mais de 14 dias. Requer que a coluna `updated` esteja presente no CSV.

**Diagnóstico de sobre-representação:**
Para cada Tipo e Time, compara o percentual na faixa crítica (> 30 dias) com o percentual no total de abertos. Se a diferença ≥ 15 pontos percentuais, o fator é listado como "de atenção".

```
sobre_representação = (% no vermelho) − (% no total de abertos)
Flagrado se: sobre_representação ≥ 15 pp
```

---

## 4. Squad Health Score

**Score consolidado [0–100]** calculado como média ponderada de 5 métricas:

| Métrica | Peso |
|---|---|
| Lead Time | 25% |
| Throughput | 20% |
| Aging | 25% |
| MTTR | 15% |
| CFR | 15% |

**Janela de cálculo:** últimos 3 meses com dados disponíveis (excluindo o mês WIP para Throughput).

### Normalização das métricas para [0–100]

**Lead Time, MTTR, CFR** (quanto menor, melhor):
```
Elite  → 90–100  (valor 0 → 100 pts; valor no limite Elite → 90 pts)
High   → 70–89
Medium → 50–69
Low    → 0–49    (decai linearmente até 0 ao dobro do limite Medium)
```

**Throughput** (quanto maior, melhor):
```
baseline = média da primeira metade do histórico disponível
window_avg = média dos últimos 3 meses fechados
pct_change = (window_avg − baseline) / baseline × 100
score = 70 + pct_change × 0.5   (cap em 100, floor em 0)
```

**Aging:**
```
score = 100 − (pct_red × 80 + pct_yellow × 30)
floor em 0
```
Onde `pct_red` = itens > 30 dias / total e `pct_yellow` = itens 7–30 dias / total.

### Status do score

| Status | Score |
|---|---|
| Excelente | ≥ 90 |
| Boa | ≥ 70 |
| Atenção | ≥ 50 |
| Crítica | < 50 |

### Tendência

Compara o score da janela atual com a janela dos 3 meses anteriores:

| Tendência | Critério |
|---|---|
| ↑ Subindo | Diferença > 5 pontos |
| ↓ Caindo | Diferença < −5 pontos |
| → Estável | Demais casos |

### Principais impactos

Para cada métrica, calcula o delta ponderado entre a janela atual e a anterior:
```
delta_ponderado = (score_atual[k] − score_anterior[k]) × peso[k]
```
Exibe os 3 maiores impactos absolutos (positivos ou negativos), filtrado por `|delta| ≥ 0.5`.

---

## 5. Pipeline de snapshot

### Tabelas (SQLAlchemy / SQLite)

**`issues_raw`**: cópia integral das issues do Jira (truncada e recarregada a cada sync).

**`metric_snapshots`**: snapshots imutáveis de métricas por período/time.

| Coluna | Tipo | Descrição |
|---|---|---|
| `period` | `YYYY-MM` | Período de referência |
| `team` | string | Time (ou "Todos") |
| `metric_name` | string | Ex: `lead_time_days` |
| `value` | float | Valor calculado |
| `finalized` | bool | `True` = período passado, imutável |
| `updated_at` | datetime | Última atualização |

**Regra de imutabilidade:** se `finalized=True` e o período não for o passado via `--force-recalculate-period`, o registro é ignorado (`"skipped"`).

### Migrando para PostgreSQL

Alterar `DATABASE_URL` em `db.py`:
```python
DATABASE_URL = "postgresql://user:password@host:5432/metricas"
```
O restante do código não precisa mudar (SQLAlchemy abstrai o dialeto).

---

## 6. Limitações e aproximações

| Área | Limitação atual |
|---|---|
| **Aging** | "Dias em aberto" é calculado a partir da criação da issue, não da última mudança de status. O campo `updated` é uma aproximação — o tempo real na faixa crítica exigiria o changelog do Jira |
| **CFR** | Assume que todo Incidente criado no mês é causado por algum deploy do mesmo mês. Na prática, incidentes podem ser de longa duração ou pré-existir aos deploys |
| **Lead Time** | Mede criação → resolução no Jira. Não captura o tempo de code review, CI ou deploy caso não haja data de implantação separada |
| **Throughput** | Conta itens resolvidos de qualquer tipo. Não pondera por tamanho ou complexidade |
| **Diagnóstico de queda** | Correlação heurística. Um Aging alto e uma queda de throughput no mesmo período pode ser coincidência — não é garantia de causalidade |
| **Squad Health** | Os pesos (25/20/25/15/15) são configuráveis em `squad_health.py` e refletem uma escolha de priorização, não uma fórmula universal |

---

## 7. Glossário

| Termo | Definição |
|---|---|
| **DORA** | DevOps Research and Assessment — framework de 4 métricas para medir performance de times de software |
| **Lead Time** | Tempo desde a criação de uma tarefa até sua resolução (em dias úteis) |
| **MTTR** | Mean Time to Restore — tempo médio para restaurar o serviço após um incidente |
| **CFR** | Change Failure Rate — percentual de deploys que causam incidentes |
| **Throughput** | Quantidade de itens concluídos por mês |
| **Aging** | Tempo que itens em aberto ficam sem avançar |
| **WIP** | Work in Progress — o mês atual, considerado incompleto e excluído de baselines |
| **Squad Health Score** | Score 0–100 que combina as 5 métricas com pesos, resumindo a saúde do time |
| **Snapshot** | Registro imutável do valor de uma métrica em um período específico, gravado no banco |
| **GMUD** | Gerenciamento de Mudança — tipo de issue que representa um deploy planejado |
| **CV** | Coeficiente de Variação — desvio padrão dividido pela média, mede previsibilidade do throughput |
| **Faixa crítica (Aging)** | Itens abertos há mais de 30 dias |
| **Sobre-representação** | Quando um tipo/time tem proporção na faixa crítica significativamente maior do que no total de abertos (≥ 15 p.p.) |

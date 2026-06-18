# Dashboard de Métricas de Engenharia

Aplicação Streamlit para acompanhamento contínuo de métricas DORA e de fluxo de trabalho de times de engenharia, com dados provenientes do Jira.

## Visão geral

O dashboard consolida quatro métricas DORA (Lead Time, Deployment Frequency, MTTR, CFR) e métricas de fluxo (Throughput, Aging, tempo por status) em um score único chamado **Squad Health**. Cada página oferece detalhes, tendências e diagnósticos automáticos gerados a partir dos dados reais — sem números fixos no código.

## Páginas

| Página | Descrição |
|---|---|
| **Home** | Visão consolidada: Squad Health, resumo de cada página, maior oportunidade de melhoria e alertas ativos |
| **DORA Executivo** | Tabela detalhada das 4 métricas DORA por mês, faixas Elite/High/Medium/Low e sparklines |
| **Throughput** | Entregas mensais com exclusão do mês em andamento (WIP), tendência, previsibilidade (CV) e diagnóstico de queda |
| **Aging** | Itens em aberto por tempo de criação, histograma por faixa, KPI de itens sem movimentação e diagnóstico de sobre-representação |
| **Fluxo** | Tempo médio/mediano por status do workflow, volume por status e diagnóstico de gargalo com limiar relativo |

## Arquitetura

```
Jira API
   │
   ▼
jira_client.py              ← normaliza issues do Jira
   │
   └──► sync_and_snapshot.py + db.py
           │  ├── issues_raw         ← issues brutas
           │  ├── issue_transitions  ← histórico de mudança de status
           │  └── metric_snapshots   ← snapshots DORA por período/time
           └── metrics.db

app.py  (entry point Streamlit)
   ├── pages/home.py
   ├── pages/dora_executivo.py
   ├── pages/throughput.py
   ├── pages/aging.py
   └── pages/fluxo.py
         │
         ├── core_metrics.py   ← camada central de cálculo (DORA, Throughput,
         │                        Aging, Scoring, Squad Health)
         ├── status_time.py    ← tempo por status a partir de transições
         ├── squad_health.py   ← card visual do Squad Health (delega a core_metrics)
         └── metrics.py        ← cálculos DORA legados (calculate_metrics_summary)
```

## Setup

### 1. Clonar e criar ambiente virtual

```bash
git clone <url-do-repositorio>
cd dashboard
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configurar variáveis de ambiente (conexão Jira)

Crie um arquivo `.env` na raiz do projeto:

```env
JIRA_BASE_URL=https://sua-empresa.atlassian.net
JIRA_EMAIL=seu@email.com
JIRA_API_TOKEN=seu_token_aqui
```

> O token é gerado em **Jira → Configurações de conta → Segurança → Criar e gerenciar tokens de API**.

### 3. Sincronizar dados do Jira

```bash
python sync_and_snapshot.py
```

O que este comando faz:
- Busca issues na API do Jira e normaliza via `jira_client.py`
- Sincroniza o histórico de transições de status (`issue_transitions`) — necessário para a página Fluxo
- Atribui times automaticamente via round-robin quando o campo `Team` está vazio
- Grava snapshots de métricas DORA por período em `metric_snapshots`

Para forçar recálculo de um período já finalizado:

```bash
python sync_and_snapshot.py --force-recalculate-period=2026-05
```

### 4. Rodar o dashboard

```bash
streamlit run app.py
```

Abre em `http://localhost:8501`.

### 5. Dados sintéticos (desenvolvimento offline)

Para regenerar o dataset de desenvolvimento:

```bash
node generate_synthetic_jira_node.js
```

Gera dados sintéticos com proporções realistas de tipos e times.

## Estrutura de arquivos

```
├── app.py                            # Entry point Streamlit (navegação, CSS global)
├── core_metrics.py                   # Camada central: DORA, Throughput, Aging, Scoring, Squad Health
├── status_time.py                    # Tempo por status a partir de histórico de transições
├── squad_health.py                   # Squad Health Score + card visual reutilizável
├── metrics.py                        # Cálculos DORA legados (calculate_metrics_summary)
├── jira_client.py                    # Cliente da API REST do Jira
├── db.py                             # Modelos SQLAlchemy (SQLite → Postgres-ready)
├── sync_and_snapshot.py              # Pipeline de snapshot (CLI)
├── generate_synthetic_jira_node.js   # Gerador de dados sintéticos (Node.js)
├── requirements.txt
├── pages/
│   ├── home.py                       # Visão Geral consolidada
│   ├── dora_executivo.py             # DORA Metrics — visão executiva
│   ├── throughput.py                 # Throughput mensal com WIP exclusion
│   ├── aging.py                      # Aging — itens em aberto por tempo
│   └── fluxo.py                      # Fluxo — tempo por status e diagnóstico de gargalo
├── scripts/
│   └── inspect_db.py                 # Utilitário: inspeciona metrics.db no terminal
├── scripts/legacy/                   # Scripts de fase anterior (não fazem parte do app)
└── docs/
    └── metricas.md                   # Fórmulas, funções, Squad Health, limitações e glossário
```

## Testes

```bash
pytest test_core_metrics.py test_snapshots.py test_status_time.py test_transitions.py -v
```

Cobre: cálculos de core_metrics, pipeline de snapshot (inserção, idempotência, imutabilidade, `--force-recalculate-period`), status_time e transições.

## Documentação técnica

Fórmulas completas, funções de `core_metrics.py` organizadas por grupo, `TERMINAL_STATUSES`, metodologia de gargalo, status de migração e limitações do ambiente de teste em [docs/metricas.md](docs/metricas.md).

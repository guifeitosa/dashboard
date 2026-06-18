# Dashboard de Métricas de Engenharia

Aplicação Streamlit para acompanhamento contínuo de métricas DORA e de fluxo de trabalho de times de engenharia, com dados provenientes do Jira.

## Visão geral

O dashboard consolida quatro métricas DORA (Lead Time, Deployment Frequency, MTTR, CFR) e métricas de fluxo (Throughput, Aging, tempo por status) em um score único chamado **Squad Health**. Cada página oferece detalhes, tendências e **diagnósticos automáticos** gerados a partir dos dados reais — sem números fixos no código. Um seletor de time global na barra lateral filtra todas as páginas simultaneamente.

## Páginas

| Página | Descrição |
|---|---|
| **Home** | Visão consolidada: Squad Health, resumo de cada página, maior oportunidade de melhoria e alertas ativos |
| **DORA Executivo** | Tabela detalhada das 4 métricas DORA por mês, faixas Elite/High/Medium/Low, sparklines e diagnóstico de mudança de faixa |
| **Throughput** | Entregas mensais com exclusão do mês em andamento (WIP), tendência, previsibilidade (CV) e diagnóstico automático |
| **Aging** | Itens em aberto por tempo de criação, histograma por faixa, KPI de itens sem movimentação e diagnóstico automático |
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

### 5. Dados de demonstração (desenvolvimento offline)

Para gerar o banco de dados de demonstração com dataset sintético rico:

```bash
python scripts/generate_demo_data.py
```

Gera `metrics_demo.db` com 3 times, 7 meses de histórico, CFR alternando entre meses bons e ruins, gargalo de aging e snapshots DORA completos.

Para rodar o dashboard apontando para o banco de demonstração:

```bash
$env:DASHBOARD_DB_PATH = "metrics_demo.db"   # PowerShell
# ou
DASHBOARD_DB_PATH=metrics_demo.db            # bash
streamlit run app.py
```

## Estrutura de arquivos

```
├── app.py                            # Entry point Streamlit (navegação, seletor global de time, CSS)
├── core_metrics.py                   # Camada central: DORA, Throughput, Aging, Scoring, Diagnósticos
├── status_time.py                    # Tempo por status a partir de histórico de transições
├── squad_health.py                   # Squad Health Score + card visual reutilizável
├── metrics.py                        # Cálculos DORA legados (calculate_metrics_summary)
├── jira_client.py                    # Cliente da API REST do Jira
├── db.py                             # Modelos SQLAlchemy; DASHBOARD_DB_PATH env var
├── sync_and_snapshot.py              # Pipeline de snapshot (CLI)
├── requirements.txt
├── pages/
│   ├── home.py                       # Visão Geral consolidada (filtrada por time global)
│   ├── dora_executivo.py             # DORA Metrics — visão executiva + diagnóstico
│   ├── throughput.py                 # Throughput mensal com WIP exclusion + diagnóstico
│   ├── aging.py                      # Aging — itens em aberto por tempo + diagnóstico
│   └── fluxo.py                      # Fluxo — tempo por status e diagnóstico de gargalo
├── scripts/
│   ├── generate_demo_data.py         # Gera metrics_demo.db com dataset sintético rico
│   └── inspect_db.py                 # Utilitário: inspeciona metrics.db no terminal
├── test_core_metrics.py              # Testes: cálculos core
├── test_snapshots.py                 # Testes: pipeline de snapshot
├── test_status_time.py               # Testes: tempo por status
├── test_transitions.py               # Testes: parsing de transições
├── test_throughput_diagnostics.py    # Testes: diagnóstico de Throughput
├── test_aging_diagnostics.py         # Testes: diagnóstico de Aging
├── test_dora_diagnostics.py          # Testes: diagnóstico DORA
├── test_smoke.py                     # Smoke tests: todas as páginas via AppTest
└── docs/
    └── metricas.md                   # Fórmulas, diagnósticos, Squad Health, limitações e glossário
```

## Testes

```bash
# Suíte unitária completa
pytest test_core_metrics.py test_snapshots.py test_status_time.py test_transitions.py \
       test_throughput_diagnostics.py test_aging_diagnostics.py test_dora_diagnostics.py -v

# Smoke tests (todas as páginas × todos os times, requer metrics_demo.db)
$env:DASHBOARD_DB_PATH = "metrics_demo.db"
pytest test_smoke.py -v
```

| Arquivo | O que cobre |
|---|---|
| `test_core_metrics.py` | `compute_aging`, `compute_throughput`, `dora_band`, scoring |
| `test_snapshots.py` | Pipeline de snapshot: inserção, idempotência, imutabilidade, `--force-recalculate-period` |
| `test_status_time.py` | Cálculo de tempo por status |
| `test_transitions.py` | Parsing de transições |
| `test_throughput_diagnostics.py` | Regras de diagnóstico de Throughput (3 regras) |
| `test_aging_diagnostics.py` | Regras de diagnóstico de Aging (3 regras + enriquecimentos de Regra 2) |
| `test_dora_diagnostics.py` | Regras de diagnóstico DORA (faixa deteriorada, faixa melhorada, cruzamento CFR × Deploy Freq) |
| `test_smoke.py` | AppTest: 5 páginas × 4 filtros de time = 20 casos sem exceção |

## Documentação técnica

Fórmulas completas, funções de `core_metrics.py` organizadas por grupo, `TERMINAL_STATUSES`, metodologia de gargalo, status de migração e limitações do ambiente de teste em [docs/metricas.md](docs/metricas.md).

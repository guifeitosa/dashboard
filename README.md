# Dashboard de Métricas de Engenharia

Aplicação Streamlit para acompanhamento contínuo de métricas DORA e de fluxo de trabalho de times de engenharia, com dados provenientes do Jira.

## Visão geral

O dashboard consolida quatro métricas DORA (Lead Time, Deployment Frequency, MTTR, CFR) e duas métricas de fluxo (Throughput, Aging) em um score único chamado **Squad Health**. Cada página oferece detalhes, tendências e diagnósticos automáticos gerados a partir dos dados reais — sem números fixos no código.

## Páginas

| Página | Descrição |
|---|---|
| **Home** | Visão consolidada: Squad Health, resumo de cada página, maior oportunidade de melhoria e alertas ativos |
| **DORA Executivo** | Tabela detalhada das 4 métricas DORA por mês, faixas Elite/High/Medium/Low, histórico e sparklines |
| **Throughput** | Entregas mensais com exclusão do mês em andamento (WIP), tendência, previsibilidade (CV) e diagnóstico de queda |
| **Aging** | Itens em aberto por tempo de criação, histograma por faixa, KPI de itens sem movimentação e diagnóstico de sobre-representação |

## Arquitetura

```
Jira API
   │
   ▼
jira_client.py          ← normaliza issues do Jira
   │
   ├──► loader.py       ← carrega CSV local (modo offline / dados sintéticos)
   │
   └──► sync_and_snapshot.py + db.py   ← pipeline de snapshot para SQLite
                                          (tabelas metric_snapshots e issues_raw)
                                          └► metrics.db

app.py  (entry point Streamlit)
   ├── pages/home.py
   ├── pages/dora_executivo.py
   ├── pages/throughput.py
   └── pages/aging.py
         ├── squad_health.py   ← Squad Health Score (cálculo + card visual compartilhado)
         ├── metrics.py        ← funções de cálculo DORA
         └── loader.py
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
>
> Sem `.env`, o app funciona no modo offline com `data/jira_issues_synthetic.csv`.

### 3. Rodar o dashboard

```bash
streamlit run app.py
```

Abre em `http://localhost:8501`. A página inicial (Home) carrega automaticamente.

### 4. Sincronizar dados do Jira (pipeline de snapshot)

```bash
python sync_and_snapshot.py
```

Sincroniza issues do Jira para `metrics.db` e grava snapshots de métricas por período. Períodos passados ficam imutáveis (`finalized=True`); o período corrente é recalculado a cada execução.

Para forçar o recálculo de um período já finalizado:

```bash
python sync_and_snapshot.py --force-recalculate-period=2026-05
```

### 5. Dados sintéticos (desenvolvimento offline)

Para regenerar o dataset de desenvolvimento:

```bash
node generate_synthetic_jira_node.js
```

Gera `data/jira_issues_synthetic.csv` com ~600 issues nos últimos 6 meses.

## Estrutura de arquivos

```
├── app.py                            # Entry point Streamlit (navegação, CSS global)
├── loader.py                         # Carrega e normaliza CSV
├── metrics.py                        # Cálculos DORA: MTTR, CFR, Lead Time, Deploy Freq
├── squad_health.py                   # Squad Health Score + card visual reutilizável
├── jira_client.py                    # Cliente da API REST do Jira
├── db.py                             # Modelos SQLAlchemy (SQLite → Postgres-ready)
├── sync_and_snapshot.py              # Pipeline de snapshot (CLI)
├── test_snapshots.py                 # Testes pytest do pipeline de snapshot
├── generate_synthetic_jira_node.js   # Gerador de dados sintéticos (Node.js)
├── requirements.txt
├── pages/
│   ├── home.py                       # Visão Geral consolidada
│   ├── dora_executivo.py             # DORA Metrics — visão executiva
│   ├── throughput.py                 # Throughput mensal com WIP exclusion
│   └── aging.py                      # Aging — itens em aberto por tempo
├── data/
│   └── jira_issues_synthetic.csv     # Dataset sintético para desenvolvimento
├── scripts/
│   └── inspect_db.py                 # Utilitário: inspeciona metrics.db no terminal
├── scripts/legacy/                   # Scripts de fase anterior (não fazem parte do app)
└── docs/
    └── metricas.md                   # Fórmulas, faixas DORA, Squad Health, glossário
```

## Testes

```bash
pytest test_snapshots.py -v
```

Cobre: inserção, idempotência, imutabilidade de períodos finalizados, flag `--force-recalculate-period` e sincronização de issues brutas.

## Documentação técnica

Fórmulas completas, faixas DORA, cálculo do Squad Health, limitações e glossário em [docs/metricas.md](docs/metricas.md).

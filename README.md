# Dashboard de Métricas de Engenharia

Projeto Python para calcular métricas de engenharia a partir de um dataset sintético no formato Jira.

## Estrutura

- `data/jira_issues_synthetic.csv`: dataset sintético gerado
- `loader.py`: carrega e normaliza o CSV
- `metrics.py`: calcula métricas por time/mês
- `main.py`: lê o dataset, calcula o resumo e imprime no terminal
- `generate_synthetic_jira_node.js`: gera o CSV sintético usando Node.js
- `requirements.txt`: dependências Python

## Métricas calculadas

1. **MTTR**: média de `resolutiondate - created` em horas para `Incidente` resolvidos
2. **CFR**: incidentes criados no mês / GMUDs implantadas no mês * 100
3. **Lead Time for Changes**: média de `resolutiondate - created` em dias para `Story`, `Bug`, `Task`
4. **Deployment Frequency**: contagem de `GMUD` com `data_implantacao` preenchida por mês

## Uso

1. Gere ou atualize o dataset sintético:

```bash
node generate_synthetic_jira_node.js
```

2. Instale dependências Python:

```bash
pip install -r requirements.txt
```

3. Rode o resumo:

```bash
python main.py
```

> Nota: o ambiente atual não tinha um executável Python disponível no terminal, então o dataset CSV foi gerado com Node.js. Se você tiver Python instalado, `main.py` irá carregar o CSV e imprimir o resumo de métricas.

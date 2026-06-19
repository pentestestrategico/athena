# Consulta Athena

Script Python para executar consultas no AWS Athena e gerar um relatorio Excel com indicadores de acesso, volume, endpoints, metodos HTTP, usuarios ativos, possiveis IPs suspeitos e IPs associados a mais de um usuario.

## Requisitos

- Python 3.13 ou compativel
- Credenciais AWS com permissao para executar consultas no Athena
- Bucket S3 configurado para armazenar os resultados das consultas Athena
- Dependencias Python:
  - `boto3`
  - `pandas`
  - `python-dotenv`
  - `openpyxl`

O projeto ja possui um ambiente virtual em `venv/`.

## Configuracao

Crie ou ajuste o arquivo `.env` no mesmo diretorio do script:

```env
AWS_ACCESS_KEY_ID=sua_access_key
AWS_SECRET_ACCESS_KEY=sua_secret_key
AWS_REGION=sa-east-1

ATHENA_DATABASE=nome_do_database
ATHENA_TABLE=nome_da_tabela
ATHENA_S3_OUTPUT=s3://seu-bucket/prefixo/

PERIODO_HORAS=24
DATA_INICIO=
DATA_FIM=
LIMITE_REQUISICOES_IP=500
LIMITE_ENDPOINTS_DISTINTOS=30
ATHENA_TIMEOUT_SEGUNDOS=600
ATHENA_POLL_INTERVAL=2
```

Variaveis obrigatorias:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `ATHENA_DATABASE`
- `ATHENA_TABLE`
- `ATHENA_S3_OUTPUT`

Variaveis opcionais:

- `AWS_REGION`: padrao `sa-east-1`
- `PERIODO_HORAS`: padrao `24`
- `DATA_INICIO`: data inicial no formato `YYYY-MM-DD`; quando usada junto com `DATA_FIM`, substitui `PERIODO_HORAS`
- `DATA_FIM`: data final exclusiva no formato `YYYY-MM-DD`
- `LIMITE_REQUISICOES_IP`: padrao `500`
- `LIMITE_ENDPOINTS_DISTINTOS`: padrao `30`
- `ATHENA_TIMEOUT_SEGUNDOS`: padrao `600`
- `ATHENA_POLL_INTERVAL`: padrao `2`

## Execucao

Usando o ambiente virtual local:

```bash
venv/bin/python consulta_athena.py
```

O script cria um arquivo Excel no formato:

```text
relatorio_athena_YYYY-MM-DD_HH-MM-SS.xlsx
```

## Argumentos

Os valores do `.env` podem ser sobrescritos por linha de comando:

```bash
venv/bin/python consulta_athena.py \
  --periodo-horas 48 \
  --limite-requisicoes-ip 1000 \
  --limite-endpoints-distintos 50 \
  --timeout-segundos 900 \
  --poll-interval 3
```

Para consultar somente o dia 14/06/2026, use a data final como 2026-06-15, pois `--data-fim` e exclusiva:

```bash
venv/bin/python consulta_athena.py \
  --data-inicio 2026-06-14 \
  --data-fim 2026-06-15
```

Argumentos disponiveis:

- `--periodo-horas`: periodo analisado em horas.
- `--data-inicio`: data inicial no formato `YYYY-MM-DD`; quando usada junto com `--data-fim`, substitui `--periodo-horas`.
- `--data-fim`: data final exclusiva no formato `YYYY-MM-DD`.
- `--limite-requisicoes-ip`: quantidade minima de requisicoes para classificar um IP como suspeito.
- `--limite-endpoints-distintos`: quantidade minima de endpoints distintos para classificar um IP como suspeito.
- `--timeout-segundos`: tempo maximo de espera por consulta no Athena.
- `--poll-interval`: intervalo entre verificacoes de status da consulta.

Para ver a ajuda:

```bash
venv/bin/python consulta_athena.py --help
```

## Abas Geradas

- `Resumo`: totais gerais do periodo analisado.
- `Top_IPs`: IPs com maior volume de requisicoes.
- `Top_Endpoints`: endpoints mais acessados.
- `IP_x_Endpoint`: combinacoes de IP, endpoint e metodo HTTP.
- `Metodos_HTTP`: distribuicao por metodo HTTP.
- `Volume_por_Hora`: volume agregado por hora.
- `IPs_Suspeitos`: IPs acima dos limites configurados.
- `IPs_Multi_Usuarios`: IPs conectados a mais de um `id_usuario`, com a contagem de usuarios distintos.
- `Usuarios_Ativos`: usuarios com maior atividade.

## Tratamento de Erros

O script valida as variaveis obrigatorias e os argumentos numericos antes de executar as consultas.

Durante a execucao, cada consulta tem timeout configuravel. Se uma consulta exceder o tempo limite, ela sera cancelada no Athena e o script exibira uma mensagem de erro.

Falhas de conexao com AWS ou erros retornados pelo Athena sao exibidos em formato resumido no terminal.

## Observacoes

- Se as abas vierem vazias, verifique se ha registros no periodo configurado por `PERIODO_HORAS`.
- A aba `Resumo` pode conter uma linha mesmo quando as demais abas estao vazias, pois usa agregacoes como `COUNT`, `MIN` e `MAX`.
- O Athena cobra por dados escaneados. Ajuste o periodo e os filtros conforme necessario para controlar custo e tempo de execucao.

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime

import boto3
import pandas as pd
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError
from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Config:
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    database: str
    table: str
    s3_output: str
    periodo_horas: int
    limite_requisicoes_ip: int
    limite_endpoints_distintos: int
    timeout_segundos: int
    poll_interval: int
    data_inicio: str | None
    data_fim: str | None


def ler_int_env(nome, padrao, minimo=1):
    valor = os.getenv(nome, str(padrao))

    try:
        numero = int(valor)
    except ValueError as exc:
        raise ValueError(f"{nome} deve ser um numero inteiro. Valor atual: {valor}") from exc

    if numero < minimo:
        raise ValueError(f"{nome} deve ser maior ou igual a {minimo}. Valor atual: {valor}")

    return numero


def validar_data(nome, valor):
    if not valor:
        return None

    try:
        datetime.strptime(valor, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{nome} deve estar no formato YYYY-MM-DD. Valor atual: {valor}") from exc

    return valor


def criar_parser():
    parser = argparse.ArgumentParser(
        description="Executa consultas no AWS Athena e gera um relatorio Excel."
    )
    parser.add_argument(
        "--periodo-horas",
        type=int,
        default=ler_int_env("PERIODO_HORAS", 24),
        help="Periodo analisado em horas. Padrao: PERIODO_HORAS do .env ou 24.",
    )
    parser.add_argument(
        "--data-inicio",
        default=os.getenv("DATA_INICIO"),
        help="Data inicial no formato YYYY-MM-DD. Quando usada, substitui periodo-horas.",
    )
    parser.add_argument(
        "--data-fim",
        default=os.getenv("DATA_FIM"),
        help="Data final exclusiva no formato YYYY-MM-DD. Exemplo: 2026-06-15 inclui dados ate 2026-06-14 23:59:59.",
    )
    parser.add_argument(
        "--limite-requisicoes-ip",
        type=int,
        default=ler_int_env("LIMITE_REQUISICOES_IP", 500),
        help="Limite minimo de requisicoes para marcar IP como suspeito.",
    )
    parser.add_argument(
        "--limite-endpoints-distintos",
        type=int,
        default=ler_int_env("LIMITE_ENDPOINTS_DISTINTOS", 30),
        help="Limite minimo de endpoints distintos para marcar IP como suspeito.",
    )
    parser.add_argument(
        "--timeout-segundos",
        type=int,
        default=ler_int_env("ATHENA_TIMEOUT_SEGUNDOS", 600),
        help="Tempo maximo de espera por consulta Athena. Padrao: 600.",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=ler_int_env("ATHENA_POLL_INTERVAL", 2),
        help="Intervalo em segundos entre verificacoes de status. Padrao: 2.",
    )
    return parser


def validar_config(args):
    obrigatorias = {
        "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID"),
        "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY"),
        "ATHENA_DATABASE": os.getenv("ATHENA_DATABASE"),
        "ATHENA_TABLE": os.getenv("ATHENA_TABLE"),
        "ATHENA_S3_OUTPUT": os.getenv("ATHENA_S3_OUTPUT"),
    }

    faltando = [nome for nome, valor in obrigatorias.items() if not valor]

    if faltando:
        raise EnvironmentError(f"Variaveis ausentes no .env: {', '.join(faltando)}")

    valores_numericos = {
        "periodo_horas": args.periodo_horas,
        "limite_requisicoes_ip": args.limite_requisicoes_ip,
        "limite_endpoints_distintos": args.limite_endpoints_distintos,
        "timeout_segundos": args.timeout_segundos,
        "poll_interval": args.poll_interval,
    }

    invalidos = [
        f"{nome}={valor}"
        for nome, valor in valores_numericos.items()
        if valor < 1
    ]

    if invalidos:
        raise ValueError(
            "Os argumentos numericos devem ser maiores ou iguais a 1: "
            + ", ".join(invalidos)
        )

    data_inicio = validar_data("data-inicio", args.data_inicio)
    data_fim = validar_data("data-fim", args.data_fim)

    if bool(data_inicio) != bool(data_fim):
        raise ValueError("Use --data-inicio e --data-fim juntos.")

    if data_inicio and data_inicio >= data_fim:
        raise ValueError("--data-inicio deve ser anterior a --data-fim.")

    return Config(
        aws_access_key_id=obrigatorias["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=obrigatorias["AWS_SECRET_ACCESS_KEY"],
        aws_region=os.getenv("AWS_REGION", "sa-east-1"),
        database=obrigatorias["ATHENA_DATABASE"],
        table=obrigatorias["ATHENA_TABLE"],
        s3_output=obrigatorias["ATHENA_S3_OUTPUT"],
        periodo_horas=args.periodo_horas,
        limite_requisicoes_ip=args.limite_requisicoes_ip,
        limite_endpoints_distintos=args.limite_endpoints_distintos,
        timeout_segundos=args.timeout_segundos,
        poll_interval=args.poll_interval,
        data_inicio=data_inicio,
        data_fim=data_fim,
    )


def criar_cliente_athena(config):
    session = boto3.Session(
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
        region_name=config.aws_region,
    )
    return session.client("athena")


def extrair_linha(row, total_colunas):
    values = [
        col.get("VarCharValue", "")
        for col in row.get("Data", [])
    ]

    while len(values) < total_colunas:
        values.append("")

    return values[:total_colunas]


def buscar_resultados(athena, query_id):
    rows = []
    next_token = None

    while True:
        parametros = {"QueryExecutionId": query_id}
        if next_token:
            parametros["NextToken"] = next_token

        result = athena.get_query_results(**parametros)
        rows.extend(result["ResultSet"]["Rows"])

        next_token = result.get("NextToken")
        if not next_token:
            break

    if not rows:
        return pd.DataFrame()

    headers = [
        col.get("VarCharValue", "")
        for col in rows[0]["Data"]
    ]

    data = [
        extrair_linha(row, len(headers))
        for row in rows[1:]
    ]

    return pd.DataFrame(data, columns=headers)


def execute_query(athena, config, sql):
    response = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={
            "Database": config.database
        },
        ResultConfiguration={
            "OutputLocation": config.s3_output
        },
    )

    query_id = response["QueryExecutionId"]
    inicio = time.monotonic()

    try:
        while True:
            query_status = athena.get_query_execution(
                QueryExecutionId=query_id
            )

            status = query_status["QueryExecution"]["Status"]["State"]

            if status in ["SUCCEEDED", "FAILED", "CANCELLED"]:
                break

            tempo_decorrido = time.monotonic() - inicio
            if tempo_decorrido >= config.timeout_segundos:
                athena.stop_query_execution(QueryExecutionId=query_id)
                raise TimeoutError(
                    "Timeout na consulta Athena "
                    f"{query_id} apos {config.timeout_segundos} segundos."
                )

            time.sleep(config.poll_interval)
    except KeyboardInterrupt:
        athena.stop_query_execution(QueryExecutionId=query_id)
        raise

    if status != "SUCCEEDED":
        reason = query_status["QueryExecution"]["Status"].get(
            "StateChangeReason",
            "Sem detalhe do erro",
        )
        raise RuntimeError(f"Erro na consulta Athena: {status} - {reason}")

    return buscar_resultados(athena, query_id)


def montar_queries(config):
    if config.data_inicio and config.data_fim:
        filtro_periodo = f"""
dt_cadastro >= TIMESTAMP '{config.data_inicio} 00:00:00'
AND dt_cadastro < TIMESTAMP '{config.data_fim} 00:00:00'
"""
    else:
        filtro_periodo = f"""
dt_cadastro >= current_timestamp - interval '{config.periodo_horas}' hour
"""

    return {
        "Resumo": f"""
            SELECT
                COUNT(*) AS total_requisicoes,
                COUNT(DISTINCT ds_host) AS total_ips_distintos,
                COUNT(DISTINCT ds_rota) AS total_endpoints_distintos,
                COUNT(DISTINCT id_usuario) AS total_usuarios_distintos,
                MIN(dt_cadastro) AS primeiro_registro,
                MAX(dt_cadastro) AS ultimo_registro
            FROM {config.table}
            WHERE {filtro_periodo}
        """,

        "Top_IPs": f"""
            SELECT
                ds_host AS ip,
                COUNT(*) AS total_requisicoes,
                COUNT(DISTINCT ds_rota) AS endpoints_distintos,
                COUNT(DISTINCT ds_metodo) AS metodos_distintos,
                COUNT(DISTINCT id_usuario) AS usuarios_distintos,
                CASE
                    WHEN COUNT(DISTINCT id_usuario) > 1 THEN true
                    ELSE false
                END AS possui_multiplos_usuarios,
                MIN(dt_cadastro) AS primeiro_acesso,
                MAX(dt_cadastro) AS ultimo_acesso
            FROM {config.table}
            WHERE {filtro_periodo}
              AND ds_host IS NOT NULL
              AND ds_host <> ''
            GROUP BY ds_host
            ORDER BY total_requisicoes DESC
            LIMIT 100
        """,

        "Top_Endpoints": f"""
            SELECT
                ds_rota AS endpoint,
                COUNT(*) AS total_acessos,
                COUNT(DISTINCT ds_host) AS ips_distintos,
                COUNT(DISTINCT id_usuario) AS usuarios_distintos
            FROM {config.table}
            WHERE {filtro_periodo}
              AND ds_rota IS NOT NULL
              AND ds_rota <> ''
            GROUP BY ds_rota
            ORDER BY total_acessos DESC
            LIMIT 100
        """,

        "IP_x_Endpoint": f"""
            SELECT
                ds_host AS ip,
                ds_rota AS endpoint,
                ds_metodo AS metodo,
                COUNT(DISTINCT id_usuario) AS usuarios_distintos,
                CASE
                    WHEN COUNT(DISTINCT id_usuario) > 1 THEN true
                    ELSE false
                END AS possui_multiplos_usuarios,
                COUNT(*) AS total_acessos
            FROM {config.table}
            WHERE {filtro_periodo}
              AND ds_host IS NOT NULL
              AND ds_host <> ''
              AND ds_rota IS NOT NULL
              AND ds_rota <> ''
            GROUP BY ds_host, ds_rota, ds_metodo
            ORDER BY total_acessos DESC
            LIMIT 200
        """,

        "Metodos_HTTP": f"""
            SELECT
                ds_metodo AS metodo,
                COUNT(*) AS total
            FROM {config.table}
            WHERE {filtro_periodo}
              AND ds_metodo IS NOT NULL
              AND ds_metodo <> ''
            GROUP BY ds_metodo
            ORDER BY total DESC
        """,

        "Volume_por_Hora": f"""
            SELECT
                date_trunc('hour', dt_cadastro) AS hora,
                COUNT(*) AS total_requisicoes,
                COUNT(DISTINCT ds_host) AS ips_distintos,
                COUNT(DISTINCT ds_rota) AS endpoints_distintos
            FROM {config.table}
            WHERE {filtro_periodo}
            GROUP BY 1
            ORDER BY 1 DESC
        """,

        "IPs_Suspeitos": f"""
            SELECT
                ds_host AS ip,
                COUNT(*) AS total_requisicoes,
                COUNT(DISTINCT ds_rota) AS endpoints_distintos,
                COUNT(DISTINCT ds_metodo) AS metodos_distintos,
                COUNT(DISTINCT id_usuario) AS usuarios_distintos,
                CASE
                    WHEN COUNT(DISTINCT id_usuario) > 1 THEN true
                    ELSE false
                END AS possui_multiplos_usuarios,
                MIN(dt_cadastro) AS primeiro_acesso,
                MAX(dt_cadastro) AS ultimo_acesso
            FROM {config.table}
            WHERE {filtro_periodo}
              AND ds_host IS NOT NULL
              AND ds_host <> ''
            GROUP BY ds_host
            HAVING COUNT(*) >= {config.limite_requisicoes_ip}
                OR COUNT(DISTINCT ds_rota) >= {config.limite_endpoints_distintos}
            ORDER BY total_requisicoes DESC
            LIMIT 100
        """,

        "IPs_Multi_Usuarios": f"""
            SELECT
                ds_host AS ip,
                COUNT(DISTINCT id_usuario) AS usuarios_distintos,
                true AS possui_multiplos_usuarios,
                COUNT(*) AS total_requisicoes,
                COUNT(DISTINCT ds_rota) AS endpoints_distintos,
                COUNT(DISTINCT ds_metodo) AS metodos_distintos,
                MIN(dt_cadastro) AS primeiro_acesso,
                MAX(dt_cadastro) AS ultimo_acesso
            FROM {config.table}
            WHERE {filtro_periodo}
              AND ds_host IS NOT NULL
              AND ds_host <> ''
              AND id_usuario IS NOT NULL
            GROUP BY ds_host
            HAVING COUNT(DISTINCT id_usuario) > 1
            ORDER BY usuarios_distintos DESC, total_requisicoes DESC
            LIMIT 100
        """,

        "Usuarios_Ativos": f"""
            SELECT
                id_usuario,
                COUNT(*) AS total_requisicoes,
                COUNT(DISTINCT ds_rota) AS endpoints_distintos,
                COUNT(DISTINCT ds_host) AS ips_distintos,
                MIN(dt_cadastro) AS primeiro_acesso,
                MAX(dt_cadastro) AS ultimo_acesso
            FROM {config.table}
            WHERE {filtro_periodo}
              AND id_usuario IS NOT NULL
            GROUP BY id_usuario
            ORDER BY total_requisicoes DESC
            LIMIT 100
        """,
    }


def ajustar_colunas_excel(writer, sheet_name, df):
    worksheet = writer.sheets[sheet_name]

    for idx, col in enumerate(df.columns, 1):
        max_len = max(
            df[col].astype(str).map(len).max() if not df.empty else 0,
            len(col),
        )
        worksheet.column_dimensions[
            worksheet.cell(row=1, column=idx).column_letter
        ].width = min(max_len + 2, 60)


def gerar_excel(resultados):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    arquivo_excel = f"relatorio_athena_{timestamp}.xlsx"

    with pd.ExcelWriter(arquivo_excel, engine="openpyxl") as writer:
        for nome, df in resultados.items():
            sheet_name = nome[:31]
            df.to_excel(
                writer,
                sheet_name=sheet_name,
                index=False
            )
            ajustar_colunas_excel(writer, sheet_name, df)

    return arquivo_excel


def imprimir_previas(resultados):
    print("\n[+] Prévia Top IPs:")
    print(resultados["Top_IPs"].head(10).to_string(index=False))

    print("\n[+] Prévia IPs Suspeitos:")
    print(resultados["IPs_Suspeitos"].head(10).to_string(index=False))

    print("\n[+] Prévia IPs com múltiplos usuários:")
    print(resultados["IPs_Multi_Usuarios"].head(10).to_string(index=False))


def main():
    args = criar_parser().parse_args()
    config = validar_config(args)
    athena = criar_cliente_athena(config)
    queries = montar_queries(config)
    resultados = {}

    print(f"[+] Database: {config.database}")
    print(f"[+] Tabela: {config.table}")
    print(f"[+] Região: {config.aws_region}")
    if config.data_inicio and config.data_fim:
        print(f"[+] Período analisado: {config.data_inicio} até {config.data_fim} (fim exclusivo)")
    else:
        print(f"[+] Período analisado: últimas {config.periodo_horas} horas")
    print(f"[+] Timeout por consulta: {config.timeout_segundos} segundos")

    for nome, sql in queries.items():
        print(f"[+] Executando: {nome}")
        resultados[nome] = execute_query(athena, config, sql)

    arquivo_excel = gerar_excel(resultados)

    print("\n[+] Relatório gerado com sucesso:")
    print(f"    {arquivo_excel}")

    imprimir_previas(resultados)


if __name__ == "__main__":
    try:
        main()
    except (EnvironmentError, ValueError, TimeoutError, RuntimeError) as exc:
        print(f"[ERRO] {exc}", file=sys.stderr)
        sys.exit(1)
    except EndpointConnectionError as exc:
        print(f"[ERRO] Falha ao conectar no endpoint AWS: {exc}", file=sys.stderr)
        sys.exit(1)
    except (BotoCoreError, ClientError) as exc:
        print(f"[ERRO] Falha na chamada AWS: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[ERRO] Execucao interrompida pelo usuario.", file=sys.stderr)
        sys.exit(130)

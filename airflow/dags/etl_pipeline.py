import pandas as pd
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.hooks.postgres_hook import PostgresHook
from datetime import datetime


def extract_data(**kwargs):
    file_path = '/opt/airflow/dados/dados.csv'
    try:
        df = pd.read_csv(file_path, sep=',', encoding='utf-8')
        df.replace('n/d', pd.NA, inplace=True)
        for column in ['ABERTURA', 'FECHAMENTO', 'VARIACAO', 'MINIMO', 'MAXIMO']:
            df[column] = df[column].str.replace(',', '.').replace(pd.NA, '0').astype(float)
        df['VOLUME'] = df['VOLUME'].str.replace('.', '', regex=False).str.replace(',', '.').astype(float)
        df.dropna(subset=['ABERTURA', 'FECHAMENTO', 'VARIACAO', 'MINIMO', 'MAXIMO', 'VOLUME'], inplace=True)
        kwargs['ti'].xcom_push(key='dataframe', value=df.to_dict(orient='records'))
    except Exception as e:
        print(f'Erro ao ler o arquivo: {e}')


def load_to_postgres(**kwargs):
    ti = kwargs['ti']
    data = ti.xcom_pull(key='dataframe', task_ids='extract_data')
    pg_hook = PostgresHook(postgres_conn_id='postgres_conn')
    conn = pg_hook.get_conn()
    cursor = conn.cursor()

    for row in data:
        cursor.execute("""
            INSERT INTO airflow.tabela_dados_financeiros (data, dia_semana, identificador, empresa, abertura, fechamento, variacao, minimo, maximo, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (row['DATA'], row['DIA_SEMANA'], row['IDENTIFICADOR'], row['EMPRESA'],
                  row['ABERTURA'], row['FECHAMENTO'], row['VARIACAO'],
                  row['MINIMO'], row['MAXIMO'], row['VOLUME']))

    conn.commit()
    cursor.close()
    conn.close()


def transform_and_load_dimensions(**kwargs):
    hook = PostgresHook(postgres_conn_id='postgres_conn')
    conn = hook.get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO airflow.dim_data (data, dia_semana)
        SELECT DISTINCT data, dia_semana FROM airflow.tabela_dados_financeiros
        ON CONFLICT (data) DO NOTHING;
    """)

    cursor.execute("""
        INSERT INTO airflow.dim_empresa (nome_empresa)
        SELECT DISTINCT empresa FROM airflow.tabela_dados_financeiros
        ON CONFLICT (nome_empresa) DO NOTHING;
    """)

    cursor.execute("""
        INSERT INTO airflow.dim_identificador (identificador)
        SELECT DISTINCT identificador FROM airflow.tabela_dados_financeiros
        ON CONFLICT (identificador) DO NOTHING;
    """)

    cursor.execute("""
        INSERT INTO airflow.fatos_dados_financeiros (data_id, empresa_id, identificador_id, abertura, fechamento, variacao, minimo, maximo, volume)
        SELECT 
            d.data_id,
            e.empresa_id,
            i.identificador_id,
            f.abertura,
            f.fechamento,
            f.variacao,
            f.minimo,
            f.maximo,
            f.volume
        FROM airflow.tabela_dados_financeiros f
        JOIN airflow.dim_data d ON f.data = d.data
        JOIN airflow.dim_empresa e ON f.empresa = e.nome_empresa
        JOIN airflow.dim_identificador i ON f.identificador = i.identificador;
    """)

    conn.commit()
    cursor.close()
    conn.close()


with DAG(
        dag_id='etl_pipeline',
        schedule_interval='@daily',
        start_date=datetime(2024, 1, 1),
        catchup=False,
) as dag:
    extract_task = PythonOperator(
        task_id='extract_data',
        python_callable=extract_data,
        provide_context=True,
    )

    load_task = PythonOperator(
        task_id='load_to_postgres',
        python_callable=load_to_postgres,
        provide_context=True,
    )

    transform_task = PythonOperator(
        task_id='transform_and_load_dimensions',
        python_callable=transform_and_load_dimensions,
        provide_context=True,
    )

    extract_task >> load_task >> transform_task
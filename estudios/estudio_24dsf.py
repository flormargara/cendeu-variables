"""Fase 1 — Estudio de datos del 24DSF (Central de Deudores BCRA).

Lee el Parquet largo (una fila por CUIT x entidad x período) y produce tablas agregadas
en reportes/estudio_24dsf/*.csv. No escribe datos personales: sólo conteos y montos agregados.

Uso:
    python estudios/estudio_24dsf.py <glob_parquet> <ipc.csv> <dir_salida> [--memoria 8GB]
"""
import argparse
import pathlib
import time

import duckdb

CONSULTAS = {
    # 1. Volumen y composición por período
    "periodos": """
        SELECT periodo, count(*) AS filas, count(DISTINCT cuit) AS cuits, count(DISTINCT entidad) AS entidades,
               sum(monto)::DOUBLE AS monto_nominal,
               sum(monto * io.ipc / ip.ipc)::DOUBLE AS monto_real,
               avg((situacion >= 2)::INT) AS pct_filas_sit2p,
               sum(monto) FILTER (situacion >= 2) / sum(monto) AS pct_monto_sit2p,
               avg((monto = 0)::INT) AS pct_monto_cero,
               avg((monto < 0)::INT) AS pct_monto_neg
        FROM d JOIN ipc ip USING (periodo) CROSS JOIN (SELECT ipc FROM ipc WHERE periodo = (SELECT max(periodo) FROM ipc)) io
        GROUP BY periodo ORDER BY periodo""",
    # 2. Situación x período
    "situacion_periodo": """
        SELECT periodo, situacion, count(*) AS filas, sum(monto)::DOUBLE AS monto
        FROM d GROUP BY ALL ORDER BY ALL""",
    # 3. Tipo de identificación y tipo de persona (prefijo CUIT)
    "tipo_persona": """
        SELECT tipo_id,
               CASE WHEN cuit // 1000000000 IN (20,23,24,27) THEN 'humana'
                    WHEN cuit // 1000000000 IN (30,33,34) THEN 'juridica' ELSE 'otro' END AS tipo_persona,
               count(*) AS filas, count(DISTINCT cuit) AS cuits
        FROM d WHERE periodo = (SELECT max(periodo) FROM d) GROUP BY ALL ORDER BY filas DESC""",
    # 4. Flag
    "flag": "SELECT flag, situacion, count(*) AS filas FROM d GROUP BY ALL ORDER BY ALL",
    # 5. Duplicados CUIT x entidad x período
    "duplicados": """
        SELECT periodo, count(*) AS grupos_dup, sum(n) AS filas_dup
        FROM (SELECT periodo, cuit, entidad, count(*) n FROM d GROUP BY ALL HAVING count(*) > 1)
        GROUP BY periodo ORDER BY periodo""",
    # 6. Entidades: presencia por período (filas y CUITs)
    "entidades_periodo": """
        SELECT entidad, periodo, count(*) AS filas, sum(monto)::DOUBLE AS monto
        FROM d GROUP BY ALL ORDER BY ALL""",
    # 7. Entidades por CUIT (último período)
    "entidades_por_cuit": """
        SELECT least(n, 10) AS n_entidades, count(*) AS cuits
        FROM (SELECT cuit, count(DISTINCT entidad) n FROM d WHERE periodo = (SELECT max(periodo) FROM d) GROUP BY cuit)
        GROUP BY ALL ORDER BY ALL""",
    # 8. Continuidad de la serie por CUIT: meses presentes vs rango, huecos
    "continuidad": """
        WITH c AS (
          SELECT cuit, count(DISTINCT periodo) AS meses,
                 (max(periodo)//100*12 + max(periodo)%100) - (min(periodo)//100*12 + min(periodo)%100) + 1 AS rango,
                 min(periodo) AS primero, max(periodo) AS ultimo
          FROM d GROUP BY cuit)
        SELECT meses = rango AS sin_huecos, (ultimo = (SELECT max(periodo) FROM d)) AS vigente_ultimo,
               count(*) AS cuits, avg(meses) AS meses_prom
        FROM c GROUP BY ALL ORDER BY ALL""",
    # 9. Transiciones de situación (peor situación del CUIT) entre meses consecutivos; 0 = ausente
    "transiciones": """
        WITH m AS (SELECT cuit, periodo, max(situacion) AS sit FROM d GROUP BY ALL),
        g AS (
          SELECT coalesce(a.cuit, b.cuit) AS cuit, a.periodo AS p0,
                 coalesce(a.sit, 0) AS sit_t, coalesce(b.sit, 0) AS sit_t1
          FROM m a FULL JOIN m b
            ON a.cuit = b.cuit AND (b.periodo//100*12 + b.periodo%100) = (a.periodo//100*12 + a.periodo%100) + 1
          WHERE coalesce(a.periodo, 0) < (SELECT max(periodo) FROM d) AND coalesce(b.periodo, 999999) > (SELECT min(periodo) FROM d))
        SELECT sit_t, sit_t1, count(*) AS n FROM g GROUP BY ALL ORDER BY ALL""",
    # 10. Distribución de montos (miles de $ nominales) por situación, último período
    "montos_cuantiles": """
        SELECT situacion, count(*) AS filas,
               quantile_cont(monto, [0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99, 0.999]) AS cuantiles,
               max(monto)::DOUBLE AS maximo
        FROM d WHERE periodo = (SELECT max(periodo) FROM d) GROUP BY ALL ORDER BY ALL""",
    # 11. Posibles fusiones: entidad que deja de informar en t y cuyos CUITs aparecen en una entidad nueva para ellos en t
    # (se calcula en pasos materializados en fusiones(), ver abajo)
}


def fusiones(c: duckdb.DuckDBPyConnection, destino: pathlib.Path) -> None:
    """Entidad que deja de informar en t+1 y cuyos CUITs aparecen en t+1 en una entidad con la que antes no operaban."""
    c.execute("""
        CREATE OR REPLACE TEMP TABLE pres AS
        SELECT DISTINCT entidad, periodo, periodo//100*12 + periodo%100 AS mi FROM d;
        CREATE OR REPLACE TEMP TABLE bajas AS
        SELECT p.entidad AS ent_baja, p.periodo AS ultimo, p.mi FROM pres p
        LEFT JOIN pres q ON q.entidad = p.entidad AND q.mi = p.mi + 1
        WHERE q.entidad IS NULL AND p.periodo < (SELECT max(periodo) FROM pres);
        CREATE OR REPLACE TEMP TABLE cli AS
        SELECT DISTINCT b.ent_baja, b.ultimo, b.mi, d.cuit
        FROM d JOIN bajas b ON d.entidad = b.ent_baja AND d.periodo = b.ultimo;
        CREATE OR REPLACE TEMP TABLE ant AS            -- relaciones previas de esos CUITs (mes de la baja)
        SELECT DISTINCT d.cuit, d.entidad, d.periodo FROM d SEMI JOIN cli USING (cuit)
        WHERE d.periodo IN (SELECT ultimo FROM bajas);
        CREATE OR REPLACE TEMP TABLE nxt AS            -- relaciones al mes siguiente
        SELECT DISTINCT d.cuit, d.entidad, d.periodo//100*12 + d.periodo%100 AS mi FROM d SEMI JOIN cli USING (cuit)
        WHERE d.periodo//100*12 + d.periodo%100 IN (SELECT mi + 1 FROM bajas);
    """)
    sql = """
        WITH mov AS (
          SELECT c.ent_baja, c.ultimo, n.entidad AS ent_destino, count(*) AS cuits_mov
          FROM cli c JOIN nxt n ON n.cuit = c.cuit AND n.mi = c.mi + 1
          ANTI JOIN ant a ON a.cuit = c.cuit AND a.entidad = n.entidad AND a.periodo = c.ultimo
          GROUP BY ALL),
        tot AS (SELECT ent_baja, ultimo, count(*) AS cuits_baja FROM cli GROUP BY ALL)
        SELECT m.*, t.cuits_baja, m.cuits_mov / t.cuits_baja AS pct_mov
        FROM mov m JOIN tot t USING (ent_baja, ultimo)
        WHERE t.cuits_baja >= 100 AND m.cuits_mov / t.cuits_baja >= 0.3
        ORDER BY ultimo, ent_baja, cuits_mov DESC"""
    c.execute(f"COPY ({sql}) TO '{destino}' (HEADER)")
    c.execute(f"COPY (SELECT ent_baja, ultimo FROM bajas ORDER BY ultimo, ent_baja) TO '{destino.with_name('entidades_bajas.csv')}' (HEADER)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pq")
    ap.add_argument("ipc")
    ap.add_argument("salida")
    ap.add_argument("--memoria", default="8GB")
    ap.add_argument("--solo", nargs="*")
    a = ap.parse_args()

    out = pathlib.Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute(f"SET memory_limit = '{a.memoria}'; SET preserve_insertion_order = false;")
    c.execute(f"CREATE VIEW d AS SELECT * FROM read_parquet('{a.pq}', hive_partitioning = true)")
    c.execute(f"CREATE TABLE ipc AS SELECT periodo::BIGINT periodo, ipc::DOUBLE ipc FROM read_csv('{a.ipc}', header = true)")
    for nombre, sql in CONSULTAS.items():
        if a.solo and nombre not in a.solo:
            continue
        t = time.time()
        c.execute(f"COPY ({sql}) TO '{out / (nombre + '.csv')}' (HEADER)")
        print(f"{nombre}: {time.time() - t:.0f}s", flush=True)
    if not a.solo or "fusiones" in a.solo:
        t = time.time()
        fusiones(c, out / "fusiones.csv")
        print(f"fusiones: {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main()

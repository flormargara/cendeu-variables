"""Etapas batch: IPC, conversión del bianual a Parquet, variables por CUIT e IV/Gini.

Toda la lógica pesada vive en los SQL de ``cendeu/sql`` y corre en DuckDB.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import tempfile
import urllib.request
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

import duckdb

IPC_SERIE = "148.3_INIVELNAL_DICI_M_26"  # IPC nivel general nacional, INDEC (datos.gob.ar)
IPC_URL = "https://apis.datos.gob.ar/series/api/series/?ids={serie}&start_date={desde}&format=csv&limit=1000"


def _sql(nombre: str) -> str:
    return resources.files("cendeu").joinpath("sql", nombre).read_text(encoding="utf-8")


@contextmanager
def conexion(memory_limit: str = "10GB", temp_dir: str | Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    """Conexión DuckDB con disco temporal para poder procesar más datos que la RAM."""
    with tempfile.TemporaryDirectory(prefix="cendeu_", dir=temp_dir) as tmp:
        con = duckdb.connect(str(Path(tmp) / "work.duckdb"))
        con.execute(f"SET temp_directory = '{tmp}/spill'")
        con.execute(f"SET memory_limit = '{memory_limit}'")
        try:
            yield con
        finally:
            con.close()


def _correr(con: duckdb.DuckDBPyConnection, archivo_sql: str, **variables) -> None:
    for k, v in variables.items():
        con.execute(f"SET VARIABLE {k} = ?", [v])
    con.execute(_sql(archivo_sql))


def mi(periodo: int) -> int:
    """AAAAMM -> índice de mes continuo."""
    return (periodo // 100) * 12 + periodo % 100


def periodo_mas(periodo: int, meses: int) -> int:
    m = mi(periodo) + meses - 1
    return (m // 12) * 100 + m % 12 + 1


# --------------------------------------------------------------------------- IPC
def descargar_ipc(dst: str | Path, desde: int, hasta: int) -> Path:
    """Baja el IPC INDEC y lo guarda como CSV ``periodo,ipc,imputado``.

    Los meses entre el último publicado y ``hasta`` se completan con el último valor
    (``imputado=1``): hay que volver a correr cuando salga el dato oficial.
    """
    desde_iso = f"{desde // 100}-{desde % 100:02d}-01"
    req = urllib.request.Request(IPC_URL.format(serie=IPC_SERIE, desde=desde_iso),
                                 headers={"User-Agent": "cendeu/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        texto = r.read().decode("utf-8").replace("\r", "")
    filas = list(csv.reader(io.StringIO(texto)))[1:]
    serie = {int(f[0][:7].replace("-", "")): float(f[1]) for f in filas if f and f[1]}
    if not serie:
        raise RuntimeError("La API de IPC no devolvió datos")
    p, ultimo, salida = desde, None, []
    while p <= hasta:
        if p in serie:
            ultimo = serie[p]
            salida.append((p, ultimo, 0))
        elif ultimo is not None:
            salida.append((p, ultimo, 1))
        p = periodo_mas(p, 1)
    dst = Path(dst)
    with dst.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["periodo", "ipc", "imputado"])
        w.writerows(salida)
    return dst


# ------------------------------------------------------------------- conversión
def convertir(txt_24dsf: str | Path, dst_dir: str | Path, corte: int, **kw) -> Path:
    """Convierte ``24DSF.txt`` (ancho fijo) a Parquet largo particionado por período.

    ``corte``: período AAAAMM del archivo (el bloque 1 del registro).
    """
    corte_fecha = dt.date(corte // 100, corte % 100, 1)
    with conexion(**kw) as con:
        _correr(con, "convertir_24dsf.sql", src=str(txt_24dsf), dst=str(dst_dir), corte=corte_fecha)
    return Path(dst_dir)


def validar_contra_control(parquet_dir: str | Path, control_txt: str | Path, **kw) -> dict:
    """Compara cantidad y monto por entidad×período contra ``24DSF_control.txt``."""
    with conexion(**kw) as con:
        con.execute(f"""
            CREATE TEMP TABLE ctrl AS
            SELECT trim(substr(l,1,5))::INT entidad, substr(l,6,6)::INT periodo,
                   replace(split_part(trim(substr(l,12)),' ',1),',','.')::DECIMAL(18,1) monto_c,
                   regexp_extract(trim(substr(l,12)),'(\\d+)\\s*$',1)::BIGINT n_c
            FROM read_csv('{control_txt}', columns={{'l':'VARCHAR'}}, delim='|', quote='', header=false, auto_detect=false);
            CREATE TEMP TABLE pq AS
            SELECT entidad, periodo, sum(monto) monto_p, count(*) n_p
            FROM read_parquet('{Path(parquet_dir)}/**/*.parquet', hive_partitioning=true) GROUP BY ALL;
        """)
        r = con.execute("""
            SELECT count(*) pares_control,
                   count(p.entidad) pares_con_datos,
                   count(*) FILTER (n_c = n_p AND monto_c = monto_p) exactos,
                   count(*) FILTER (n_c = 2*n_p AND monto_c = 2*monto_p) control_doble,
                   (SELECT count(*) FROM pq LEFT JOIN ctrl c USING (entidad, periodo) WHERE c.entidad IS NULL) pares_sin_control
            FROM ctrl c LEFT JOIN pq p USING (entidad, periodo)
        """).fetchone()
    return dict(zip(["pares_control", "pares_con_datos", "exactos", "control_doble", "pares_sin_control"], r))


# -------------------------------------------------------------------- variables
def construir_variables(parquet_dir: str | Path, obs: int, dst: str | Path, ipc_csv: str | Path,
                        historia: int = 12, desempeno: int = 0, **kw) -> Path:
    """Variables por CUIT a la fecha ``obs``.

    ``historia``: meses hacia atrás (incluye obs). ``desempeno`` > 0 agrega el target
    ``target_sit3p`` = situación ≥3 en los ``desempeno`` meses posteriores.
    """
    with conexion(**kw) as con:
        _correr(con, "variables.sql",
                pq=f"{Path(parquet_dir)}/**/*.parquet", ipc=str(ipc_csv),
                obs=int(obs), h=int(historia), perf=int(desempeno), dst=str(dst))
    return Path(dst)


def iv_gini(vars_parquet: str | Path, dst_resumen: str | Path, dst_bins: str | Path, **kw):
    """IV y Gini univariados (humanas en situación 1). Devuelve el resumen como DataFrame."""
    with conexion(**kw) as con:
        _correr(con, "iv_gini.sql", src=str(vars_parquet),
                dst_resumen=str(dst_resumen), dst_bins=str(dst_bins))
    import pandas as pd
    return pd.read_csv(dst_resumen)

"""Aplicación del scorecard (JSON) en DuckDB: sobre un Parquet de variables o un DataFrame.

Ambos caminos usan el mismo SQL generado, así que dan exactamente el mismo resultado.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd


def cargar_modelo(ruta: str | Path) -> dict:
    return json.loads(Path(ruta).read_text())


def _case(var: dict, campo: str) -> str:
    x = f'CAST("{var["nombre"]}" AS DOUBLE)'
    partes = []
    nulo = next(b for b in var["bins"] if b["nulo"])
    partes.append(f"WHEN {x} IS NULL THEN {nulo[campo]}")
    normales = [b for b in var["bins"] if not b["nulo"]]
    for b in normales[:-1]:
        partes.append(f"WHEN {x} <= {b['hasta']!r} THEN {b[campo]}")
    partes.append(f"ELSE {normales[-1][campo]}")
    return "CASE " + " ".join(partes) + " END"


def sql_score(modelo: dict, tabla: str, columnas_extra: tuple[str, ...] = ("cuit", "periodo_obs")) -> str:
    """SELECT que devuelve score, PD y puntos por variable para ``tabla``."""
    pts = [f'{_case(v, "puntos")} AS "pts_{v["nombre"]}"' for v in modelo["variables"]]
    lin = " + ".join(f'{v["coef"]} * {_case(v, "woe")}' for v in modelo["variables"])
    suma = " + ".join(f'"pts_{v["nombre"]}"' for v in modelo["variables"])
    extra = ", ".join(columnas_extra)
    return f"""
        WITH b AS (
          SELECT {extra}, ({modelo["poblacion"]}) AS en_poblacion_modelo,
                 1 / (1 + exp(-({modelo["intercepto"]} + {lin}))) AS pd,
                 {", ".join(pts)}
          FROM {tabla}
        )
        SELECT {extra}, en_poblacion_modelo, ({suma}) AS score, pd,
               {", ".join(f'"pts_{v["nombre"]}"' for v in modelo["variables"])}
        FROM b
    """


def scorear_parquet(vars_parquet: str | Path, modelo: dict | str | Path, dst: str | Path) -> Path:
    """Scorea todos los CUIT de un Parquet de variables y escribe ``dst`` (Parquet)."""
    if not isinstance(modelo, dict):
        modelo = cargar_modelo(modelo)
    con = duckdb.connect()
    q = sql_score(modelo, f"read_parquet('{vars_parquet}')")
    con.execute(f"COPY ({q}) TO '{dst}' (FORMAT parquet, COMPRESSION zstd)")
    con.close()
    return Path(dst)


def scorear_df(df: pd.DataFrame, modelo: dict | str | Path) -> pd.DataFrame:
    """Scorea un DataFrame con las columnas de variables (p. ej. CUITs de una solicitud)."""
    if not isinstance(modelo, dict):
        modelo = cargar_modelo(modelo)
    extra = tuple(c for c in ("cuit", "periodo_obs") if c in df.columns)
    con = duckdb.connect()
    con.register("entrada", df)
    out = con.execute(sql_score(modelo, "entrada", extra)).df()
    con.close()
    return out


def variables_requeridas(modelo: dict) -> list[str]:
    return [v["nombre"] for v in modelo["variables"]] + ["tipo_persona", "sit_actual"]

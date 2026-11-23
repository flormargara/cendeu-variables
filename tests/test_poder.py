"""Tests del informe de poder con datos sintéticos de comportamiento conocido."""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from cendeu import poder

CATALOGO = """
version: 1
prefijo: CEN
operaciones: {VAL: valor, CAT: categoria}
fuentes: {24DSF: bianual}
bloques: {B01_X: prueba}
defaults: {fuente: 24DSF, tipo_dato: DECIMAL, uso: modelo, nulos: nunca}
targets:
  - {nombre: CEN_TGT_SIT3P_6M, descripcion: t, definicion: t, poblacion: p, observaciones: o}
variables:
  - {nombre: CEN_VAL_FUERTE_1M, bloque: B01_X, descripcion: d, definicion: d, sql: x, sentido: "+"}
  - {nombre: CEN_VAL_COPIA_1M, bloque: B01_X, descripcion: d, definicion: d, sql: x, sentido: "+"}
  - {nombre: CEN_VAL_RUIDO_1M, bloque: B01_X, descripcion: d, definicion: d, sql: x, sentido: "+"}
  - {nombre: CEN_VAL_DERIVA_1M, bloque: B01_X, descripcion: d, definicion: d, sql: x, sentido: "-"}
  - {nombre: CEN_CAT_GRUPO_1M, bloque: B01_X, descripcion: d, definicion: d, sql: x, tipo_dato: CATEGORICA}
  - {nombre: CEN_VAL_PARCIAL_1M, bloque: B01_X, descripcion: d, definicion: d, sql: x, sentido: "+"}
"""


@pytest.fixture(scope="module")
def informe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("poder")
    (tmp / "cat.yaml").write_text(CATALOGO, encoding="utf-8")
    rng = np.random.default_rng(1)
    con = duckdb.connect()
    for k, obs in enumerate([202509, 202510, 202511]):
        n = 20_000
        fuerte = rng.normal(size=n)
        grupo = rng.choice(["A", "B", "C"], size=n)
        logit = -2 + 1.5 * fuerte + np.where(grupo == "C", 1.0, 0.0)
        y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
        df = pd.DataFrame({
            "CUIT": np.arange(n) + 20_000_000_000, "PERIODO_OBS": obs, "EN_POBLACION": 1, "CEN_TGT_SIT3P_6M": y,
            "CEN_VAL_FUERTE_1M": fuerte, "CEN_VAL_COPIA_1M": 2 * fuerte + 1,
            "CEN_VAL_RUIDO_1M": rng.normal(size=n),
            "CEN_VAL_DERIVA_1M": rng.normal(loc=3.0 if k == 2 else 0.0, size=n),
            "CEN_CAT_GRUPO_1M": grupo,
            "CEN_VAL_PARCIAL_1M": np.nan if k == 0 else fuerte,   # no disponible en la primera fecha
        })
        df.loc[df.index[:1000], "CEN_VAL_FUERTE_1M"] = np.nan   # nulos como bin propio
        d = tmp / "vars" / f"periodo_obs={obs}"
        d.mkdir(parents=True)
        con.execute(f"COPY (SELECT * FROM df) TO '{d}/parte_00.parquet'")
    res = poder.informe(tmp / "vars", tmp / "cat.yaml", tmp / "out", fechas_oot=[202511], pct_muestra=100, memory_limit="1GB")
    return res.set_index("variable"), tmp / "out"


def test_poder_y_ruido(informe):
    r, _ = informe
    assert r.loc["CEN_VAL_FUERTE_1M", "iv"] > 0.5
    assert r.loc["CEN_VAL_RUIDO_1M", "iv"] < 0.02
    assert r.loc["CEN_VAL_FUERTE_1M", "gini_oot"] > 0.4
    assert abs(r.loc["CEN_VAL_RUIDO_1M", "gini_oot"]) < 0.05
    assert r.loc["CEN_CAT_GRUPO_1M", "iv"] > 0.05
    assert r.loc["CEN_VAL_FUERTE_1M", "pct_nulos_dev"] == pytest.approx(0.05)


def test_estabilidad(informe):
    r, _ = informe
    assert r.loc["CEN_VAL_DERIVA_1M", "psi_max"] > 1.0
    assert r.loc["CEN_VAL_FUERTE_1M", "psi_max"] < 0.05


def test_fecha_no_disponible_no_cuenta(informe):
    r, _ = informe
    assert r.loc["CEN_VAL_PARCIAL_1M", "fechas_evaluadas"] == 2
    assert r.loc["CEN_VAL_PARCIAL_1M", "psi_max"] < 0.05
    assert r.loc["CEN_VAL_PARCIAL_1M", "iv"] > 0.5


def test_sentido_y_grupos(informe):
    r, _ = informe
    assert r.loc["CEN_VAL_FUERTE_1M", "sentido_observado"] == "+" and r.loc["CEN_VAL_FUERTE_1M", "sentido_ok"]
    assert r.loc["CEN_VAL_COPIA_1M", "grupo"] == r.loc["CEN_VAL_FUERTE_1M", "grupo"]
    assert r.loc["CEN_VAL_RUIDO_1M", "grupo"] == "CEN_VAL_RUIDO_1M"


def test_salidas(informe):
    _, out = informe
    for f in ["resumen.csv", "por_fecha.csv", "bins.csv", "grupos.csv", "info.json"]:
        assert (out / f).exists()
    pf = pd.read_csv(out / "por_fecha.csv")
    assert set(pf.obs) == {202509, 202510, 202511}


def test_asignar_bin_igual_que_sql():
    cortes = [-1.0, 0.0, 2.5]
    x = pd.Series([-3.0, -1.0, -0.5, 0.0, 1.0, 2.5, 9.0, np.nan])
    esperado = ["00", "00", "01", "01", "02", "02", "03", "NULO"]
    assert poder.asignar_bin(x, cortes).tolist() == esperado
    con = duckdb.connect()
    sql = [r[0] for r in con.execute(
        "SELECT CASE WHEN v IS NULL OR isnan(v) THEN 'NULO' ELSE lpad(len(list_filter([-1.0, 0.0, 2.5]::DOUBLE[], lambda c: v > c))::VARCHAR, 2, '0') END "
        "FROM (SELECT unnest([-3.0, -1.0, -0.5, 0.0, 1.0, 2.5, 9.0, 'NaN'::DOUBLE]) v)").fetchall()]
    assert sql == esperado


def test_incremental_contra_v1(informe, tmp_path):
    """El score v1 real se aplica a variables v1 sintéticas; la variable fuerte v2 tiene que sumar Gini."""
    import json
    from pathlib import Path

    r, out = informe
    modelo = Path(__file__).parents[1] / "modelos" / "scorecard_cendeu_202509.json"
    nombres = [v["nombre"] for v in json.loads(modelo.read_text())["variables"]]
    rng = np.random.default_rng(2)
    n = 20_000
    v1 = pd.DataFrame({"cuit": np.arange(n) + 20_000_000_000, "periodo_obs": 202509, "tipo_persona": "humana", "sit_actual": 1,
                       **{c: rng.normal(size=n) for c in nombres}})
    duckdb.connect().execute(f"COPY (SELECT * FROM v1) TO '{tmp_path}/v1.parquet'")
    con = duckdb.connect()
    poder._muestra(con, out.parent / "vars", ["CUIT", "CEN_VAL_FUERTE_1M", "CEN_VAL_RUIDO_1M"], "CEN_TGT_SIT3P_6M", 100)
    bins = pd.read_csv(out / "bins.csv", dtype={"bin": str})
    cortes = {v: poder._tabla_bins(con, v, False, [202509, 202510])[1] for v in ["CEN_VAL_FUERTE_1M", "CEN_VAL_RUIDO_1M"]}
    inc = poder.incremental_v1(con, tmp_path / "v1.parquet", modelo, 202509,
                               ["CEN_VAL_FUERTE_1M", "CEN_VAL_RUIDO_1M"], bins, cortes).set_index("variable")
    assert abs(inc.loc["(solo score v1)", "gini_test"]) < 0.1        # v1 sobre ruido: sin poder
    assert inc.loc["CEN_VAL_FUERTE_1M", "ganancia_gini"] > 0.4
    assert abs(inc.loc["CEN_VAL_RUIDO_1M", "ganancia_gini"]) < 0.05


def test_archivos_pagina(informe, tmp_path):
    import csv

    _, out = informe
    rutas = poder.archivos_pagina(out, out.parent / "cat.yaml", tmp_path)
    assert [p.name for p in rutas] == ["resumen.csv", "por_fecha.csv"]   # sin incremental: no se corrió con v1
    with open(tmp_path / "resumen.csv", encoding="utf-8") as f:
        filas = list(csv.reader(f))
    assert all(len(fila) == len(filas[0]) for fila in filas)          # ninguna coma dentro de un campo
    cab = filas[0]
    assert "descripcion" in cab and "representante" in cab
    fuerte = next(f for f in filas[1:] if f[0] == "CEN_VAL_FUERTE_1M")
    assert fuerte[cab.index("descripcion")] == "d"


def test_seleccion(informe, tmp_path):
    _, out = informe
    s = poder.seleccion(out, tmp_path / "seleccion.csv").set_index("variable")
    assert (tmp_path / "seleccion.csv").exists()
    rep = s.loc["CEN_VAL_FUERTE_1M", "grupo"]   # FUERTE y COPIA quedan juntas: sólo se recomienda la representante
    otra = "CEN_VAL_COPIA_1M" if rep == "CEN_VAL_FUERTE_1M" else "CEN_VAL_FUERTE_1M"
    assert s.loc[rep, "recomendada"]
    assert not s.loc[otra, "recomendada"] and f"redundante con {rep}" in s.loc[otra, "motivo_no"]
    assert not s.loc["CEN_VAL_DERIVA_1M", "recomendada"]
    assert not s.loc["CEN_VAL_RUIDO_1M", "recomendada"] and "IV" in s.loc["CEN_VAL_RUIDO_1M", "motivo_no"]
    assert (s.motivo_no.fillna("") == "").eq(s.recomendada).all()

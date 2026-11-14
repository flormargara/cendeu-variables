"""Tests con datos sintéticos (no hay datos reales en el repo)."""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

import cendeu
from cendeu.pipeline import periodo_mas
from cendeu.scorecard import binning_monotono

CORTE = 202609


def _bloque(sit: int, monto: float | None) -> str:
    if not sit:
        return "0 " + ",0".ljust(12) + "0"
    return f"{sit:<2}" + f"{monto:.1f}".replace(".", ",").ljust(12) + "0"


def _registro(entidad: int, cuit: int, hist: dict[int, tuple[int, float]]) -> str:
    """hist: {periodo: (situacion, monto)}; bloque 1 = CORTE, bloque 24 = CORTE-23."""
    bloques = [_bloque(*hist.get(periodo_mas(CORTE, -k), (0, None))) for k in range(24)]
    linea = f"{entidad:<5}" + "11" + str(cuit) + "".join(bloques)
    assert len(linea) == 378
    return linea


# Historia del caso validado a mano contra los datos reales (CUIT anonimizado).
SIT_E7 = {202609: 1, 202608: 1, 202607: 1, 202606: 1, 202605: 1, 202604: 1, 202603: 1, 202602: 1,
          202601: 2, 202512: 1, 202511: 2, 202510: 1, 202509: 1, 202508: 2, 202507: 1, 202506: 2,
          202505: 1, 202504: 2, 202503: 2, 202502: 1, 202501: 1, 202412: 1, 202411: 1, 202410: 1}
SIT_E70408 = {p: 1 for p in SIT_E7} | {202411: 2, 202410: 3}
CUIT_A = 20000000017
CUIT_B = 27000000029  # nuevo en el sistema, sólo no bancario, entra en 202508


@pytest.fixture(scope="module")
def datos(tmp_path_factory):
    d = tmp_path_factory.mktemp("cendeu")
    lineas = [
        _registro(7, CUIT_A, {p: (s, 1000.0) for p, s in SIT_E7.items()}),
        _registro(70408, CUIT_A, {p: (s, 100.0) for p, s in SIT_E70408.items()}),
        _registro(72634, CUIT_A, {202411: (1, 26.0), 202410: (1, 34.0)}),
        _registro(55182, CUIT_B, {p: (1 if p < 202512 else 4, 50.0) for p in SIT_E7 if p >= 202508}),
    ]
    txt = d / "24DSF.txt"
    txt.write_text("\r\n".join(lineas) + "\r\n")
    ipc = d / "ipc.csv"
    ipc.write_text("periodo,ipc,imputado\n" + "".join(f"{periodo_mas(CORTE, -k)},100,0\n" for k in range(25)))
    pq = d / "largo"
    cendeu.convertir(txt, pq, corte=CORTE, memory_limit="1GB")
    vars_ = d / "vars.parquet"
    cendeu.construir_variables(pq, 202509, vars_, ipc, historia=12, desempeno=12, memory_limit="1GB")
    return {"pq": pq, "vars": vars_}


def test_convertir_formato_largo(datos):
    con = duckdb.connect()
    df = con.execute(f"SELECT * FROM read_parquet('{datos['pq']}/**/*.parquet', hive_partitioning=true)").df()
    assert len(df) == 24 + 24 + 2 + 14
    fila = df[(df.cuit == CUIT_A) & (df.entidad == 70408) & (df.periodo == 202410)].iloc[0]
    assert fila.situacion == 3 and float(fila.monto) == 100.0
    assert set(df.periodo.unique()) == {periodo_mas(CORTE, -k) for k in range(24)}


def test_variables_caso_validado(datos):
    v = duckdb.connect().execute(f"SELECT * FROM '{datos['vars']}' WHERE cuit = {CUIT_A}").df().iloc[0]
    assert v.sit_actual == 1
    assert (v.peor_sit_3, v.peor_sit_6, v.peor_sit_hist) == (2, 2, 3)
    assert v.meses_sit2p_hist == 6
    assert v.meses_desde_sit2p == 1
    assert v.racha_sit1_actual == 1
    assert v.cambios_sit_hist == 8
    assert v.n_ent_actual == 2 and v.n_bancos_actual == 1
    assert v.ent_canceladas_hist == 1 and v.ent_nuevas_6 == 0
    assert v.antiguedad_meses == 12
    assert v.deuda_actual == pytest.approx(1100.0)
    assert v.peor_sit_fut == 2 and not v.target_sit3p


def test_variables_nuevo_no_bancario_con_target(datos):
    v = duckdb.connect().execute(f"SELECT * FROM '{datos['vars']}' WHERE cuit = {CUIT_B}").df().iloc[0]
    assert v.tipo_persona == "humana"
    assert v.antiguedad_meses == 2 and v.ent_nuevas_3 == 1
    assert bool(v.solo_no_bancario)
    assert v.peor_sit_fut == 4 and bool(v.target_sit3p)


def test_binning_monotono():
    rng = np.random.default_rng(0)
    x = rng.normal(size=20000)
    x[:500] = np.nan
    y = (rng.random(20000) < 1 / (1 + np.exp(-(x * 1.5 - 1)))).astype(float)
    y[np.isnan(x)] = (rng.random(500) < 0.5)
    b = binning_monotono("x", x, y)
    tasas = [m / n for m, n in zip(b.malos, b.n)]
    assert all(t2 >= t1 for t1, t2 in zip(tasas, tasas[1:]))
    assert min(b.n) >= 0.05 * (~np.isnan(x)).sum()
    assert b.n_nulo == 500 and b.iv > 0.3


@pytest.fixture(scope="module")
def modelo(tmp_path_factory):
    d = tmp_path_factory.mktemp("modelo")
    rng = np.random.default_rng(1)
    n = 30000
    df = pd.DataFrame({
        "cuit": 20_000_000_000 + np.arange(n) * 7,
        "periodo_obs": 202509, "tipo_persona": "humana", "sit_actual": 1,
        "n_no_bancos_actual": rng.poisson(1.0, n).astype(float),
        "ratio_deuda_vs_max": rng.random(n),
        "ruido": rng.normal(size=n),
    })
    df.loc[rng.random(n) < 0.05, "ratio_deuda_vs_max"] = np.nan
    lin = -2.5 + 0.8 * df.n_no_bancos_actual + 1.5 * df.ratio_deuda_vs_max.fillna(0.5)
    df["target_sit3p"] = rng.random(n) < 1 / (1 + np.exp(-lin))
    src = d / "vars.parquet"
    df.to_parquet(src)
    js = d / "modelo.json"
    m = cendeu.entrenar(src, js, pct_muestra=100, log=lambda *_: None)
    return {"df": df, "src": src, "json": js, "modelo": m, "dir": d}


def test_entrenar_selecciona_y_descarta_ruido(modelo):
    nombres = [v["nombre"] for v in modelo["modelo"]["variables"]]
    assert set(nombres) == {"n_no_bancos_actual", "ratio_deuda_vs_max"}
    assert all(v["coef"] < 0 for v in modelo["modelo"]["variables"])
    assert modelo["modelo"]["metricas"]["gini_test"] > 0.3


def test_scoring_df_y_parquet_coinciden(modelo):
    a = cendeu.scorear_df(modelo["df"], modelo["json"])
    dst = modelo["dir"] / "scores.parquet"
    cendeu.scorear_parquet(modelo["src"], modelo["json"], dst)
    b = pd.read_parquet(dst)
    pd.testing.assert_frame_equal(a.sort_values("cuit").reset_index(drop=True),
                                  b.sort_values("cuit").reset_index(drop=True), check_dtype=False)
    pts = [c for c in a.columns if c.startswith("pts_")]
    assert (a[pts].sum(axis=1) == a.score).all()
    # score más alto = menor PD
    assert a[["score", "pd"]].corr(method="spearman").iloc[0, 1] < -0.95
    assert a.en_poblacion_modelo.all()


def test_catalogo_v2_valido():
    from pathlib import Path

    from cendeu.catalogo import cargar

    cat = cargar(Path(__file__).parents[1] / "catalogo" / "variables.yaml")
    assert cat.errores == []
    nombres = [v["nombre"] for v in cat.variables]
    assert all(n.startswith("CEN_") for n in nombres)
    assert len(nombres) == len(set(nombres))
    assert max(v["ventana_meses"] for v in cat.variables) <= 12

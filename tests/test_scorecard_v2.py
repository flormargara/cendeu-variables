"""Scorecard v2 por segmento con datos sintéticos."""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from cendeu import scorecard_v2
from cendeu.scoring import sql_score


def _datos(n=40_000, seed=3):
    rng = np.random.default_rng(seed)
    filas = []
    for obs in scorecard_v2.DESARROLLO + scorecard_v2.OOT:
        x = rng.normal(size=n)
        ruido = rng.normal(size=n)
        y = (rng.random(n) < 1 / (1 + np.exp(-(-2.5 + 1.2 * x)))).astype(int)
        filas.append(pd.DataFrame({"CUIT": np.arange(n) + 20_000_000_000, "obs": obs, "y": y,
                                   "CEN_VAL_SENAL_1M": x.astype("float32"), "CEN_VAL_RUIDO_1M": ruido.astype("float32")}))
    return pd.concat(filas, ignore_index=True)


def test_entrenar_segmento_y_aplicar():
    df = _datos()
    m = scorecard_v2.entrenar_segmento(df, "sit1", "CEN_MAX_SIT_1M = 1", log=lambda *_: None)
    assert [v["nombre"] for v in m["variables"]] == ["CEN_VAL_SENAL_1M"]
    assert m["metricas"]["oot"]["gini"] > 0.45 and m["metricas"]["test"]["gini"] > 0.45
    assert all(f["psi_score"] < 0.05 for f in m["metricas"]["por_fecha"])
    assert any(t["variable"] == "CEN_VAL_RUIDO_1M" and t["resultado"] == "descartada" for t in m["seleccion"]) \
        or "CEN_VAL_RUIDO_1M" not in [t["variable"] for t in m["seleccion"]]
    # el JSON se aplica con el scoring de la v1 sobre columnas v2
    t = df[df.obs == 202603].head(1000).assign(EN_POBLACION=1, CEN_MAX_SIT_1M=1, PERIODO_OBS=202603)
    con = duckdb.connect()
    con.register("t", t)
    s = con.execute(sql_score(m, "t")).df()
    assert len(s) == 1000 and s.en_poblacion_modelo.all() and s.score.notna().all()
    # mayor señal => más riesgo => menos puntos
    r = s.assign(x=t.CEN_VAL_SENAL_1M.to_numpy())
    assert r[["x", "score"]].corr(method="spearman").iloc[0, 1] < -0.9

"""Fase 5 — Poder predictivo y estabilidad de las variables v2.

Trabaja sobre una muestra por hash de CUIT (la misma en todas las fechas) de la población de modelado
(``EN_POBLACION = 1`` y target no nulo). Las fechas de observación se dividen en desarrollo y
*out-of-time* (OOT). Los cortes de los bins se calculan en desarrollo y se aplican tal cual a OOT y a
cada fecha, como lo haría un modelo en producción.

Salidas (todas agregadas, sin datos personales):
- ``resumen.csv``: una fila por variable (IV, Gini y KS de desarrollo, Gini OOT, PSI máximo, nulos,
  sentido observado vs esperado, grupo de correlación).
- ``por_fecha.csv``: Gini, PSI, tasa de nulos y target por variable y fecha de observación.
- ``bins.csv``: conteos y WoE por bin (desarrollo).
- ``grupos.csv``: grupos de variables con |Spearman| alto.
- ``incremental.csv``: ganancia de Gini sobre el score v1 (opcional).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from . import catalogo as cat_mod

N_BINS = 10
MIN_CAT = 0.01  # categorías con menos del 1% de desarrollo van a OTRAS


# ----------------------------------------------------------------------------- métricas sobre bins
def _woe_iv(n: np.ndarray, malos: np.ndarray) -> tuple[np.ndarray, float]:
    buenos = n - malos
    db = (malos + 0.5) / (malos.sum() + 0.5 * len(n))
    dg = (buenos + 0.5) / (buenos.sum() + 0.5 * len(n))
    woe = np.log(dg / db)
    return woe, float(((dg - db) * woe).sum())


def _auc_por_orden(n: np.ndarray, malos: np.ndarray, orden: np.ndarray) -> float:
    """AUC tomando los bins en ``orden`` (de más a menos riesgoso); empates dentro del bin = 1/2."""
    buenos = n - malos
    B, G = malos.sum(), buenos.sum()
    if B == 0 or G == 0:
        return float("nan")
    b, g = malos[orden], buenos[orden]
    g_despues = G - np.cumsum(g)
    return float((b * (g_despues + 0.5 * g)).sum() / (B * G))


def _ks_por_orden(n: np.ndarray, malos: np.ndarray, orden: np.ndarray) -> float:
    buenos = n - malos
    if malos.sum() == 0 or buenos.sum() == 0:
        return float("nan")
    return float(np.abs(np.cumsum(malos[orden]) / malos.sum() - np.cumsum(buenos[orden]) / buenos.sum()).max())


def _psi(p_ref: np.ndarray, p: np.ndarray) -> float:
    a, b = np.clip(p_ref, 1e-4, None), np.clip(p, 1e-4, None)
    return float(((b - a) * np.log(b / a)).sum())


def metricas_variable(t: pd.DataFrame, fechas_dev: list[int], fechas_oot: list[int]) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """``t``: columnas obs, bin, orden_valor (posición del bin por valor; NaN para nulos/categorías), n, malos.

    Las fechas en que la variable es 100% nula (no disponible, p. ej. por falta de historia) se excluyen.
    """
    por_obs = t.groupby("obs").apply(lambda g: (g.bin != "NULO").any(), include_groups=False)
    disponibles = set(por_obs[por_obs].index)
    t = t[t.obs.isin(disponibles)]
    fechas_dev = [f for f in fechas_dev if f in disponibles]
    fechas_oot = [f for f in fechas_oot if f in disponibles]
    dev = t[t.obs.isin(fechas_dev)].groupby("bin", as_index=False).agg(n=("n", "sum"), malos=("malos", "sum"),
                                                                        orden_valor=("orden_valor", "first"))
    n, m = dev.n.to_numpy(float), dev.malos.to_numpy(float)
    woe, iv = _woe_iv(n, m)
    tasa = m / np.maximum(n, 1)
    orden_riesgo = np.argsort(-tasa, kind="stable")
    rango = {b: i for i, b in enumerate(dev.bin.to_numpy()[orden_riesgo])}  # posición de riesgo según desarrollo
    auc = _auc_por_orden(n, m, orden_riesgo)
    # sentido: AUC ordenando por valor (bins numéricos no nulos), de mayor a menor valor
    num = dev[dev.orden_valor.notna()]
    sentido = ""
    if len(num) >= 2:
        nn, mm = num.n.to_numpy(float), num.malos.to_numpy(float)
        auc_valor = _auc_por_orden(nn, mm, np.argsort(-num.orden_valor.to_numpy()))
        sentido = "+" if auc_valor > 0.5 else "-"

    def gini_en(sub: pd.DataFrame) -> float:
        g = sub.groupby("bin", as_index=False).agg(n=("n", "sum"), malos=("malos", "sum"))
        g["r"] = g.bin.map(rango).fillna(len(rango))
        g = g.sort_values("r")
        return 2 * _auc_por_orden(g.n.to_numpy(float), g.malos.to_numpy(float), np.arange(len(g))) - 1

    p_ref = pd.Series(n / n.sum(), index=dev.bin)
    filas_fecha = []
    for obs, sub in t.groupby("obs"):
        g = sub.groupby("bin").agg(n=("n", "sum"), malos=("malos", "sum"))
        p = (g.n / g.n.sum()).reindex(p_ref.index.union(g.index), fill_value=0)
        filas_fecha.append({"obs": obs, "gini": gini_en(sub), "psi": _psi(p_ref.reindex(p.index, fill_value=0).to_numpy(), p.to_numpy()),
                            "pct_nulos": float(sub.loc[sub.bin == "NULO", "n"].sum() / sub.n.sum()),
                            "tasa_target": float(sub.malos.sum() / sub.n.sum()), "n": int(sub.n.sum())})
    pf = pd.DataFrame(filas_fecha)
    oot = t[t.obs.isin(fechas_oot)]
    res = {
        "iv": iv, "gini_dev": 2 * auc - 1, "ks_dev": _ks_por_orden(n, m, orden_riesgo),
        "gini_oot": gini_en(oot) if len(oot) else float("nan"),
        "psi_max": float(pf.loc[pf.obs.isin(fechas_oot + fechas_dev), "psi"].max()),
        "pct_nulos_dev": float(dev.loc[dev.bin == "NULO", "n"].sum() / n.sum()),
        "pct_bin_mayor": float(n.max() / n.sum()), "n_bins": int(len(dev)), "sentido_observado": sentido,
        "fechas_evaluadas": len(disponibles),
    }
    bins = dev.assign(woe=woe, tasa=tasa)
    return res, pf, bins


# ----------------------------------------------------------------------------- muestra y bins en DuckDB
def _muestra(con, dir_vars: Path, columnas: list[str], target: str, pct: int) -> None:
    from .v2 import verificar_muestra
    verificar_muestra(dir_vars, pct)
    cols = ", ".join(f'"{c}"' for c in columnas)
    con.execute(f"""
        CREATE OR REPLACE TABLE mu AS
        SELECT PERIODO_OBS AS obs, "{target}" AS y, {cols}
        FROM read_parquet('{dir_vars}/periodo_obs=*/*.parquet', union_by_name = true)
        WHERE EN_POBLACION = 1 AND "{target}" IS NOT NULL AND hash(CUIT) % 100 < {pct}
    """)


def _tabla_bins(con, var: str, categorica: bool, fechas_dev: list[int]) -> tuple[pd.DataFrame, list[float] | None]:
    dev = ", ".join(map(str, fechas_dev))
    if categorica:
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE cats AS
            SELECT "{var}"::VARCHAR c FROM mu WHERE obs IN ({dev}) AND "{var}" IS NOT NULL
            GROUP BY 1 HAVING count(*) >= {MIN_CAT} * (SELECT count(*) FROM mu WHERE obs IN ({dev}))""")
        expr_bin = f"""CASE WHEN "{var}" IS NULL THEN 'NULO' WHEN "{var}"::VARCHAR IN (SELECT c FROM cats)
                            THEN "{var}"::VARCHAR ELSE 'OTRAS' END"""
        expr_orden = "NULL::DOUBLE"
        cortes = None
    else:
        cortes = con.execute(f"""SELECT list_distinct(quantile_disc("{var}"::DOUBLE, {[i / N_BINS for i in range(1, N_BINS)]}))
                                 FROM mu WHERE obs IN ({dev})""").fetchone()[0] or []
        cortes = sorted(c for c in cortes if c is not None and not math.isnan(c))
        lista = "[" + ", ".join(repr(float(c)) for c in cortes) + "]::DOUBLE[]" if cortes else "[]::DOUBLE[]"
        idx = f'len(list_filter({lista}, lambda c: "{var}"::DOUBLE > c))'
        expr_bin = f"""CASE WHEN "{var}" IS NULL OR isnan("{var}"::DOUBLE) THEN 'NULO'
                            ELSE lpad(({idx})::VARCHAR, 2, '0') END"""
        expr_orden = f"""CASE WHEN "{var}" IS NULL OR isnan("{var}"::DOUBLE) THEN NULL ELSE ({idx})::DOUBLE END"""
    t = con.execute(f"""
        SELECT obs, {expr_bin} AS bin, any_value({expr_orden}) AS orden_valor, count(*) AS n, sum(y) AS malos
        FROM mu GROUP BY ALL ORDER BY obs, bin""").df()
    return t, cortes


# ----------------------------------------------------------------------------- correlaciones
def grupos_correlacion(con, variables: list[str], iv: dict[str, float], umbral: float = 0.8,
                       filas: int = 200_000) -> pd.DataFrame:
    """Spearman sobre una submuestra de desarrollo; agrupa por umbral, la de mayor IV representa al grupo."""
    cols = ", ".join(f'"{v}"::DOUBLE AS "{v}"' for v in variables)
    df = con.execute(f"SELECT {cols} FROM mu USING SAMPLE {filas} ROWS (reservoir, 7)").df()
    r = df.rank().corr().abs()
    asignado, filas_out = {}, []
    for v in sorted(variables, key=lambda x: -iv.get(x, 0)):
        if v in asignado:
            continue
        asignado[v] = v
        for w in variables:
            if w not in asignado and r.loc[v, w] >= umbral:
                asignado[w] = v
    for v, rep in asignado.items():
        filas_out.append({"variable": v, "representante": rep,
                          "spearman_con_representante": 1.0 if v == rep else float(r.loc[v, rep])})
    return pd.DataFrame(filas_out)


# ----------------------------------------------------------------------------- incremental vs v1
def asignar_bin(x: pd.Series, cortes: list[float]) -> pd.Series:
    """Mismo criterio que el SQL: bin = cantidad de cortes estrictamente menores que x; NULO si falta."""
    idx = np.searchsorted(np.asarray(cortes, float), x.to_numpy(float), side="left")
    return pd.Series([f"{i:02d}" for i in idx], index=x.index).where(x.notna(), "NULO")


def incremental_v1(con, vars_v1: str | Path, modelo_v1: str | Path, obs: int, variables: list[str],
                   bins: pd.DataFrame, cortes: dict[str, list[float]]) -> pd.DataFrame:
    """Gini del score v1 solo y del score v1 + WoE de cada variable v2 (logística de 2 variables, 70/30 por hash).

    Población: la intersección v1 ∩ v2 en ``obs`` (humanas en situación 1). El scorecard v1 se entrenó con esa
    misma fecha (target a 12 meses), así que la comparación favorece a v1: la ganancia es conservadora.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    from .scoring import cargar_modelo, sql_score

    modelo = cargar_modelo(modelo_v1)
    con.execute(f"CREATE OR REPLACE TEMP VIEW v1 AS SELECT * FROM read_parquet('{vars_v1}')")
    con.execute(f"CREATE OR REPLACE TEMP TABLE s1 AS SELECT cuit, pd FROM ({sql_score(modelo, 'v1')}) WHERE en_poblacion_modelo")
    cols = ", ".join(f'm."{v}"' for v in variables)
    df = con.execute(f"""SELECT m.y, s1.pd, hash(m.CUIT) % 10 < 7 AS train, {cols}
                         FROM mu m JOIN s1 ON s1.cuit = m.CUIT WHERE m.obs = {obs}""").df()
    if df.empty:
        return pd.DataFrame()
    pd_v1 = df.pd.clip(1e-6, 1 - 1e-6)
    logit, tr, te = np.log(pd_v1 / (1 - pd_v1)), df.train.to_numpy(), ~df.train.to_numpy()
    gini_base = 2 * roc_auc_score(df.y[te], pd_v1[te]) - 1
    out = [{"variable": "(solo score v1)", "gini_test": gini_base, "ganancia_gini": 0.0, "n_test": int(te.sum())}]
    for v in variables:
        woe = dict(zip(bins.loc[bins.variable == v, "bin"], bins.loc[bins.variable == v, "woe"]))
        w = asignar_bin(df[v].astype(float), cortes[v]).map(woe).fillna(0.0).to_numpy()
        X = np.column_stack([logit, w])
        lr = LogisticRegression(max_iter=200).fit(X[tr], df.y[tr])
        g = 2 * roc_auc_score(df.y[te], lr.predict_proba(X[te])[:, 1]) - 1
        out.append({"variable": v, "gini_test": g, "ganancia_gini": g - gini_base, "n_test": int(te.sum())})
    return pd.DataFrame(out).sort_values("ganancia_gini", ascending=False)


# ----------------------------------------------------------------------------- orquestación
def informe(dir_vars: str | Path, catalogo: str | Path, dst: str | Path, fechas_oot: list[int] | None = None,
            pct_muestra: int = 10, vars_v1: str | Path | None = None, modelo_v1: str | Path | None = None,
            obs_v1: int = 202509, memory_limit: str = "10GB") -> pd.DataFrame:
    cat = cat_mod.cargar(catalogo)
    target = cat.meta["targets"][0]["nombre"]
    evaluables = [v for v in cat.variables if v["uso"] in ("modelo", "monitoreo")]
    meta = {v["nombre"]: v for v in evaluables}
    dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{memory_limit}'; SET preserve_insertion_order = false;")
    _muestra(con, Path(dir_vars), list(meta) + ["CUIT"], target, pct_muestra)
    fechas = [r[0] for r in con.execute("SELECT DISTINCT obs FROM mu ORDER BY 1").fetchall()]
    oot = fechas_oot if fechas_oot is not None else fechas[-2:]
    dev = [f for f in fechas if f not in oot]
    resumen, por_fecha, todos_bins, cortes = [], [], [], {}
    for nombre, v in meta.items():
        t, cortes[nombre] = _tabla_bins(con, nombre, v["tipo_dato"] == "CATEGORICA", dev)
        if t.empty:
            continue
        r, pf, b = metricas_variable(t, dev, oot)
        esperado = str(v.get("sentido", ""))
        r.update(variable=nombre, bloque=v["bloque"], uso=v["uso"], tipo_dato=v["tipo_dato"], sentido_esperado=esperado,
                 sentido_ok=(r["sentido_observado"] == esperado) if esperado in ("+", "-") and r["sentido_observado"] else None)
        resumen.append(r)
        por_fecha.append(pf.assign(variable=nombre))
        todos_bins.append(b.assign(variable=nombre))
    res = pd.DataFrame(resumen)
    pf = pd.concat(por_fecha)
    bins = pd.concat(todos_bins)
    numericas = [v for v in res.variable if meta[v]["tipo_dato"] != "CATEGORICA" and meta[v]["uso"] == "modelo"]
    grupos = grupos_correlacion(con, numericas, dict(zip(res.variable, res.iv)))
    res = res.merge(grupos.rename(columns={"representante": "grupo"})[["variable", "grupo"]], on="variable", how="left")
    res = res.sort_values("iv", ascending=False)
    cols = ["variable", "bloque", "uso", "tipo_dato", "iv", "gini_dev", "ks_dev", "gini_oot", "psi_max", "pct_nulos_dev",
            "pct_bin_mayor", "n_bins", "fechas_evaluadas", "sentido_esperado", "sentido_observado", "sentido_ok", "grupo"]
    res[cols].to_csv(dst / "resumen.csv", index=False, float_format="%.6f")
    pf[["variable", "obs", "n", "tasa_target", "gini", "psi", "pct_nulos"]].to_csv(dst / "por_fecha.csv", index=False, float_format="%.6f")
    bins[["variable", "bin", "n", "malos", "tasa", "woe"]].to_csv(dst / "bins.csv", index=False, float_format="%.6f")
    grupos.to_csv(dst / "grupos.csv", index=False, float_format="%.4f")
    info = {"fechas_desarrollo": dev, "fechas_oot": oot, "pct_muestra": pct_muestra, "target": target,
            "n_por_fecha": pf.groupby("obs").n.first().to_dict(), "tasa_por_fecha": pf.groupby("obs").tasa_target.first().to_dict()}
    if vars_v1 and modelo_v1:
        top = res[(res.uso == "modelo") & (res.grupo == res.variable)].variable.head(30).tolist()
        inc = incremental_v1(con, vars_v1, modelo_v1, obs_v1, top, bins, cortes)
        inc.to_csv(dst / "incremental.csv", index=False, float_format="%.6f")
        info["incremental_obs"] = obs_v1
    (dst / "info.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")
    return res


# ----------------------------------------------------------------------------- archivos de la página
def archivos_pagina(dir_reporte: str | Path, catalogo: str | Path, dst: str | Path) -> list[Path]:
    """Arma los tres CSV que usa la página del informe (``reportes/poder_v2/pagina``).

    Agrega al resumen la descripción de cada variable y la marca de representante de grupo. El lector de
    CSV de la página separa sólo por comas, así que se reemplazan por ``;`` las comas dentro de los textos.
    """
    cat = cat_mod.cargar(catalogo)
    desc = {v["nombre"]: v["descripcion"] for v in cat.variables}
    src, dst = Path(dir_reporte), Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    r = pd.read_csv(src / "resumen.csv")
    r.insert(1, "descripcion", r.variable.map(desc))
    r["representante"] = (r.grupo == r.variable).astype(int)
    r = r.drop(columns=[c for c in ("tipo_dato", "n_bins", "pct_bin_mayor") if c in r])
    salidas = {
        "resumen.csv": (r, "%.4f"),
        "por_fecha.csv": (pd.read_csv(src / "por_fecha.csv"), "%.5f"),
    }
    if (src / "incremental.csv").exists():  # sólo si el informe se corrió con --vars-v1
        salidas["incremental.csv"] = (pd.read_csv(src / "incremental.csv"), "%.5f")
    rutas = []
    for nombre, (df, fmt) in salidas.items():
        for c in df.select_dtypes(include="object"):
            df[c] = df[c].astype("string").str.replace(",", ";", regex=False)
        df.to_csv(dst / nombre, index=False, float_format=fmt)
        rutas.append(dst / nombre)
    return rutas


# ----------------------------------------------------------------------------- selección recomendada
IV_MIN, PSI_MAX = 0.1, 0.1


def seleccion(dir_reporte: str | Path, dst: str | Path, iv_min: float = IV_MIN, psi_max: float = PSI_MAX) -> pd.DataFrame:
    """Marca las variables recomendadas como punto de partida para un modelo y escribe ``catalogo/seleccion.csv``.

    No recorta nada: todas las variables del catálogo se siguen calculando. Recomendada = uso ``modelo``,
    IV >= ``iv_min``, PSI máximo < ``psi_max`` y representante de su grupo de correlación (las categóricas no
    se agrupan y cuentan como su propio grupo). La elección final depende de cada modelo (segmento, objetivo).
    """
    r = pd.read_csv(Path(dir_reporte) / "resumen.csv")
    r["grupo"] = r.grupo.fillna(r.variable)
    r["representante"] = r.grupo == r.variable
    motivos = []
    for x in r.itertuples():
        m = []
        if x.uso != "modelo":
            m.append(f"uso {x.uso}")
        if not x.iv >= iv_min:
            m.append(f"IV < {iv_min:g}")
        if not x.psi_max < psi_max:
            m.append(f"PSI >= {psi_max:g}")
        if not x.representante:
            m.append(f"redundante con {x.grupo}")
        motivos.append("; ".join(m))
    r["motivo_no"] = motivos
    r["recomendada"] = r.motivo_no == ""
    cols = ["variable", "recomendada", "iv", "gini_oot", "psi_max", "grupo", "representante", "motivo_no"]
    out = r[cols].sort_values(["recomendada", "iv"], ascending=[False, False])
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dst, index=False, float_format="%.4f")
    return out

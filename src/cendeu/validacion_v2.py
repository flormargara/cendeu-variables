"""Fase 6 — Validación multivariada de las variables v2.

Este repo construye variables; los modelos de PD se hacen en otro proyecto. Acá se ajusta un scorecard
sólo como prueba de las variables: si juntas suman poder, si ese poder se sostiene fuera de muestra y si
superan a las variables v1. No es un modelo para producción (no se calibra ni se publica).

Método de la v1 (``cendeu.scorecard``): binning monótono, WoE, selección forward por ganancia de AUC con
filtros de correlación y de signo, y regresión logística. Además:

- Un modelo por segmento: situación 1 y situación 2 en el mes de observación (tasas de target de ~6% y
  ~75%: son poblaciones distintas).
- Desarrollo sobre varias fechas de observación y validación *out-of-time* sobre las siguientes. 202509
  queda afuera del desarrollo: ahí faltan las variables de 12 meses que necesitan el mes previo.
- Muestra fija por hash del CUIT (la misma en todas las fechas), configurable por segmento.

El ajuste se guarda en el formato JSON de la v1 (``cendeu.scoring`` lo aplica) para poder compararlo con el
scorecard v1 sobre las mismas personas.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from . import catalogo as cat_mod
from .scorecard import Binning, _ks, binning_monotono

SEGMENTOS = {
    "sit1": {"filtro": "CEN_MAX_SIT_1M = 1", "pct_muestra": 3},
    "sit2": {"filtro": "CEN_MAX_SIT_1M = 2", "pct_muestra": 30},
}
DESARROLLO = [202510, 202511, 202512, 202601]
OOT = [202602, 202603]


def candidatas(catalogo: str | Path) -> list[str]:
    """Variables numéricas con uso ``modelo`` (las categóricas, el sexo y las de uso futuro quedan afuera)."""
    cat = cat_mod.cargar(catalogo)
    return [v["nombre"] for v in cat.variables if v["uso"] == "modelo" and v["tipo_dato"] != "CATEGORICA"]


def cargar_muestra(dir_vars: str | Path, variables: list[str], target: str, filtro: str, pct: int,
                   fechas: list[int], memory_limit: str = "6GB") -> pd.DataFrame:
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{memory_limit}'; SET preserve_insertion_order = false;")
    sel = ", ".join(f'"{v}"::FLOAT AS "{v}"' for v in variables)
    df = con.execute(f"""
        SELECT CUIT, PERIODO_OBS AS obs, "{target}"::TINYINT AS y, {sel}
        FROM read_parquet('{Path(dir_vars)}/periodo_obs=*/parte_*.parquet')
        WHERE EN_POBLACION = 1 AND {filtro} AND "{target}" IS NOT NULL
          AND PERIODO_OBS IN ({", ".join(map(str, fechas))}) AND hash(CUIT) % 100 < {pct}
    """).df()
    con.close()
    return df


def _escala(base: float, odds: float, pdo: float) -> tuple[float, float]:
    factor = pdo / np.log(2)
    return factor, base - factor * np.log(odds)


def entrenar_segmento(df: pd.DataFrame, nombre: str, filtro: str, *, fechas_dev=DESARROLLO, fechas_oot=OOT,
                      pct_test: int = 30, iv_min: float = 0.02, corr_max: float = 0.7, max_vars: int = 12,
                      ganancia_min_auc: float = 0.001, base: float = 600, odds: float = 50, pdo: float = 20,
                      log=print) -> dict:
    variables = [c for c in df.columns if c not in ("CUIT", "obs", "y")]
    dev = df.obs.isin(fechas_dev).to_numpy()
    oot = df.obs.isin(fechas_oot).to_numpy()
    test = dev & ((pd.util.hash_array(df.CUIT.to_numpy()) % 100) < pct_test)
    train = dev & ~test
    y = df.y.to_numpy().astype(int)
    log(f"[{nombre}] train {train.sum():,} | test {test.sum():,} | oot {oot.sum():,} | tasa train {y[train].mean():.4f}")

    bins: dict[str, Binning] = {}
    for c in variables:
        x = df[c].to_numpy(dtype=float)
        b = binning_monotono(c, x[train], y[train])
        if len(b.n) + (b.n_nulo > 0) > 1:
            bins[c] = b
    W = pd.DataFrame({c: b.transformar(df[c].to_numpy(dtype=float)).astype(np.float32) for c, b in bins.items()})
    ranking = sorted(((b.iv, c) for c, b in bins.items() if b.iv >= iv_min), reverse=True)

    def ajustar(cols):
        lr = LogisticRegression(C=1e6, max_iter=500)
        lr.fit(W.loc[train, cols], y[train])
        return lr, roc_auc_score(y[test], lr.predict_proba(W.loc[test, cols])[:, 1])

    elegidas: list[str] = []
    auc_actual, traza = 0.5, []
    for iv, c in ranking:
        if len(elegidas) >= max_vars:
            break
        motivo = None
        if elegidas:
            corr = W.loc[train, elegidas].corrwith(W.loc[train, c]).abs()
            if corr.max() > corr_max:
                motivo = f"correlación {corr.max():.2f} con {corr.idxmax()}"
        if motivo is None:
            lr, auc = ajustar(elegidas + [c])
            if (lr.coef_[0] >= 0).any():
                motivo = "signo invertido"
            elif auc - auc_actual < ganancia_min_auc:
                motivo = f"no suma AUC ({auc - auc_actual:+.4f})"
        if motivo:
            traza.append({"variable": c, "iv": round(iv, 4), "resultado": "descartada", "motivo": motivo})
            continue
        elegidas.append(c)
        auc_actual = auc
        traza.append({"variable": c, "iv": round(iv, 4), "resultado": "elegida", "gini_test": round(2 * auc - 1, 4)})
        log(f"  + {c:<38} IV {iv:.3f}  Gini test {2 * auc - 1:.4f}")

    lr, auc_test = ajustar(elegidas)
    p = lr.predict_proba(W[elegidas])[:, 1]
    b0, coefs = float(lr.intercept_[0]), dict(zip(elegidas, map(float, lr.coef_[0])))
    factor, offset = _escala(base, odds, pdo)
    const = (offset - factor * b0) / len(elegidas)

    def puntos(woe: float, beta: float) -> int:
        return int(round(-factor * beta * woe + const))

    variables_json = []
    for c in elegidas:
        b, beta = bins[c], coefs[c]
        lims = [None] + b.cortes + [None]
        filas = [{"desde": lims[i], "hasta": lims[i + 1], "nulo": False, "n": b.n[i],
                  "tasa_malos": round(b.malos[i] / b.n[i], 5), "woe": round(b.woe[i], 6), "puntos": puntos(b.woe[i], beta)}
                 for i in range(len(b.n))]
        filas.append({"desde": None, "hasta": None, "nulo": True, "n": b.n_nulo,
                      "tasa_malos": round(b.malos_nulo / b.n_nulo, 5) if b.n_nulo else None,
                      "woe": round(b.woe_nulo, 6), "puntos": puntos(b.woe_nulo, beta)})
        variables_json.append({"nombre": c, "coef": round(beta, 6), "iv": round(b.iv, 4), "bins": filas})

    score = offset - factor * np.log(p / (1 - p))  # equivalente a la suma de puntos, sin redondeo

    def met(mask) -> dict:
        if mask.sum() == 0 or len(set(y[mask])) < 2:
            return {}
        return {"n": int(mask.sum()), "tasa": round(float(y[mask].mean()), 5),
                "gini": round(2 * roc_auc_score(y[mask], p[mask]) - 1, 4), "ks": round(_ks(y[mask], p[mask]), 4)}

    ref = np.histogram(score[train], bins=np.quantile(score[train], np.linspace(0, 1, 11)))[0] / train.sum()
    cortes_psi = np.quantile(score[train], np.linspace(0, 1, 11))

    def psi(mask) -> float:
        h = np.histogram(np.clip(score[mask], cortes_psi[0], cortes_psi[-1]), bins=cortes_psi)[0] / mask.sum()
        a, b = np.clip(ref, 1e-4, None), np.clip(h, 1e-4, None)
        return round(float(((b - a) * np.log(b / a)).sum()), 4)

    por_fecha = [{"obs": int(f), "muestra": "desarrollo" if f in fechas_dev else "fuera de muestra",
                  **met(df.obs.to_numpy() == f), "psi_score": psi(df.obs.to_numpy() == f)}
                 for f in sorted(set(fechas_dev) | set(fechas_oot))]

    return {
        "version": "validacion-v2",
        "uso": "validación de variables; no es un modelo para producción",
        "creado": dt.datetime.now().isoformat(timespec="seconds"),
        "segmento": nombre,
        "fuente": {"variables": "variables v2 (catalogo/variables.yaml)", "fechas_desarrollo": list(fechas_dev),
                   "fechas_oot": list(fechas_oot)},
        "poblacion": f"EN_POBLACION = 1 AND {filtro}",
        "target": "CEN_TGT_SIT3P_6M: situación >= 3 en alguna entidad en los 6 meses siguientes",
        "convencion_woe": "ln(%buenos/%malos); bin = desde < x <= hasta (None = infinito)",
        "escala": {"base": base, "odds_buenos_malos": odds, "pdo": pdo, "factor": round(float(factor), 6),
                   "offset": round(float(offset), 6)},
        "intercepto": round(b0, 6),
        "variables": variables_json,
        "metricas": {"train": met(train), "test": met(test), "oot": met(oot), "por_fecha": por_fecha},
        "seleccion": traza,
        "iv_todas": {c: round(b.iv, 4) for c, b in sorted(bins.items(), key=lambda t: -t[1].iv)},
    }


def comparar_v1(dir_vars: str | Path, vars_v1: str | Path, modelo_v1: str | Path, modelo_v2: dict,
                obs: int = 202509, pct: int = 3, pct_test: int = 30) -> dict:
    """Gini de v1 y v2 en ``obs`` sobre la población común (humanas en situación 1). El ajuste v2 nunca usa
    ``obs`` si está fuera de sus fechas de desarrollo; la v1 se entrenó en 202509 con target a 12 meses."""
    from .scoring import cargar_modelo, sql_score

    con = duckdb.connect()
    con.execute("SET memory_limit = '6GB';")
    con.execute(f"""CREATE TEMP TABLE v2 AS SELECT * FROM read_parquet('{Path(dir_vars)}/periodo_obs={obs}/parte_*.parquet')
                    WHERE hash(CUIT) % 100 < {pct} AND CEN_TGT_SIT3P_6M IS NOT NULL""")
    con.execute(f"CREATE TEMP VIEW v1 AS SELECT * FROM read_parquet('{vars_v1}') WHERE hash(cuit) % 100 < {pct}")
    s1 = f"SELECT cuit, pd AS pd_v1 FROM ({sql_score(cargar_modelo(modelo_v1), 'v1')}) WHERE en_poblacion_modelo"
    s2 = f"SELECT cuit, pd AS pd_v2 FROM ({sql_score(modelo_v2, 'v2')}) WHERE en_poblacion_modelo"
    df = con.execute(f"""SELECT v2.CEN_TGT_SIT3P_6M AS y, a.pd_v1, b.pd_v2
                         FROM v2 JOIN ({s1}) a ON a.cuit = v2.CUIT JOIN ({s2}) b ON b.cuit = v2.CUIT
                         WHERE v2.CEN_CAT_TIPO_PERSONA_1M = 'HUMANA'""").df()
    con.close()
    if df.empty or df.y.nunique() < 2:
        return {}
    g = lambda col: round(2 * roc_auc_score(df.y, df[col]) - 1, 4)
    return {"obs": obs, "fuera_de_muestra_para_v1": obs != 202509, "n": len(df), "tasa": round(float(df.y.mean()), 5), "gini_v1": g("pd_v1"), "gini_v2": g("pd_v2"),
            "ks_v1": round(_ks(df.y.to_numpy(), df.pd_v1.to_numpy()), 4), "ks_v2": round(_ks(df.y.to_numpy(), df.pd_v2.to_numpy()), 4)}


def validar(dir_vars: str | Path, catalogo: str | Path, dst_dir: str | Path, *,
            vars_v1: dict[int, str | Path] | None = None, modelo_v1: str | Path | None = None, log=print) -> dict:
    """``vars_v1``: {fecha de observación: Parquet de variables v1 con target a 6 meses} para comparar v1 y v2."""
    cat = cat_mod.cargar(catalogo)
    target = cat.meta["targets"][0]["nombre"]
    cands = candidatas(catalogo)
    dst = Path(dst_dir)
    dst.mkdir(parents=True, exist_ok=True)
    resultados = {}
    for nombre, seg in SEGMENTOS.items():
        df = cargar_muestra(dir_vars, cands, target, seg["filtro"], seg["pct_muestra"], DESARROLLO + OOT)
        modelo = entrenar_segmento(df, nombre, seg["filtro"], log=log)
        modelo["fuente"]["pct_muestra"] = seg["pct_muestra"]
        del df
        if nombre == "sit1" and vars_v1 and modelo_v1:
            modelo["comparacion_v1"] = [comparar_v1(dir_vars, ruta, modelo_v1, modelo, obs=int(obs))
                                        for obs, ruta in sorted(vars_v1.items())]
        (dst / f"ajuste_{nombre}.json").write_text(json.dumps(modelo, ensure_ascii=False, indent=2), encoding="utf-8")
        m = modelo["metricas"]
        log(f"[{nombre}] {len(modelo['variables'])} variables | Gini test {m['test'].get('gini')} | oot {m['oot'].get('gini')} | KS oot {m['oot'].get('ks')}")
        resultados[nombre] = modelo
    return resultados

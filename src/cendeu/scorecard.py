"""Entrenamiento de un scorecard WoE + regresión logística sobre las variables CENDEU.

Convención: WoE = ln(%buenos / %malos)  ->  WoE alto = menor riesgo, y en la logística
para P(malo) todos los coeficientes deben ser negativos.
El resultado es un JSON autocontenido que consume ``cendeu.scoring``.
"""
from __future__ import annotations

import datetime as dt
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

POBLACION_SQL = "tipo_persona = 'humana' AND sit_actual = 1"
NO_PREDICTORAS = {
    "cuit", "periodo_obs", "tipo_persona", "sit_actual", "sit_pond_actual",
    "peor_sit_fut", "target_sit3p", "deuda_mb3", "deuda_mb6", "deuda_mb_ini",
}


# ---------------------------------------------------------------------- binning
@dataclass
class Binning:
    nombre: str
    cortes: list[float]                 # límites superiores; último bin es (cortes[-1], +inf)
    n: list[int]
    malos: list[int]
    n_nulo: int = 0
    malos_nulo: int = 0
    woe: list[float] = field(default_factory=list)
    woe_nulo: float = 0.0
    iv: float = 0.0

    def calcular_woe(self) -> None:
        n = np.array(self.n + [self.n_nulo], float)
        b = np.array(self.malos + [self.malos_nulo], float)
        g = n - b
        B, G = b.sum(), g.sum()
        woe = np.log(((g + 0.5) / (G + 0.5)) / ((b + 0.5) / (B + 0.5)))
        iv_bins = (g / G - b / B) * woe
        self.woe = woe[:-1].tolist()
        self.woe_nulo = float(woe[-1]) if self.n_nulo > 0 else 0.0
        self.iv = float(iv_bins.sum())

    def transformar(self, x: np.ndarray) -> np.ndarray:
        idx = np.searchsorted(np.array(self.cortes, float), x, side="left")
        out = np.asarray(self.woe, float)[np.minimum(idx, len(self.woe) - 1)]
        return np.where(np.isnan(x), self.woe_nulo, out)


def binning_monotono(nombre: str, x: np.ndarray, y: np.ndarray,
                     max_bins: int = 20, min_frac: float = 0.05) -> Binning:
    """Bins por cuantiles, luego fusiona vecinos hasta que la tasa de malos sea monótona
    (en el sentido de la correlación de Spearman) y cada bin tenga >= ``min_frac``."""
    nulo = np.isnan(x)
    xv, yv = x[~nulo], y[~nulo]
    cortes = list(np.unique(np.quantile(xv, np.linspace(0, 1, max_bins + 1)[1:-1]))) if len(xv) else []
    # el último corte no puede ser el máximo (dejaría el bin final vacío)
    if cortes and cortes[-1] >= xv.max():
        cortes = cortes[:-1]
    idx = np.searchsorted(np.array(cortes, float), xv, side="left")
    k = len(cortes) + 1
    n = np.bincount(idx, minlength=k).astype(int).tolist()
    m = np.bincount(idx, weights=yv, minlength=k).astype(int).tolist()
    # quitar bins vacíos
    i = 0
    while i < len(n):
        if n[i] == 0 and len(n) > 1:
            j = i if i < len(cortes) else i - 1
            cortes.pop(j); n.pop(i); m.pop(i)
        else:
            i += 1

    with warnings.catch_warnings():  # x constante -> spearman avisa y devuelve NaN
        warnings.simplefilter("ignore")
        rho = pd.Series(xv).corr(pd.Series(yv), method="spearman") if len(xv) > 1 else np.nan
    sentido = 1.0 if (np.isnan(rho) or rho == 0) else float(np.sign(rho))
    total = len(xv)

    def fusionar(i: int) -> None:  # fusiona bin i con i+1
        n[i] += n.pop(i + 1)
        m[i] += m.pop(i + 1)
        cortes.pop(i)

    while len(n) > 1:
        tasas = [mm / nn for mm, nn in zip(m, n)]
        viol = next((i for i in range(len(n) - 1)
                     if (tasas[i + 1] - tasas[i]) * sentido < 0), None)
        if viol is not None:
            fusionar(viol)
            continue
        chico = int(np.argmin(n))
        if n[chico] < min_frac * total:
            if chico == 0:
                fusionar(0)
            elif chico == len(n) - 1:
                fusionar(chico - 1)
            else:
                izq = abs(tasas[chico] - tasas[chico - 1])
                der = abs(tasas[chico] - tasas[chico + 1])
                fusionar(chico - 1 if izq <= der else chico)
            continue
        break

    b = Binning(nombre, [float(c) for c in cortes], n, m,
                int(nulo.sum()), int(y[nulo].sum()))
    b.calcular_woe()
    return b


# ----------------------------------------------------------------- entrenamiento
def _cargar(vars_parquet: str | Path, pct_muestra: int) -> pd.DataFrame:
    con = duckdb.connect()
    cols = [c for (c,) in con.execute(
        f"SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet('{vars_parquet}'))").fetchall()]
    pred = [c for c in cols if c not in NO_PREDICTORAS]
    sel = ", ".join(f'"{c}"::DOUBLE AS "{c}"' for c in pred)
    df = con.execute(f"""
        SELECT cuit, target_sit3p::INT AS y, {sel}
        FROM read_parquet('{vars_parquet}')
        WHERE {POBLACION_SQL} AND hash(cuit) % 100 < {pct_muestra}
    """).df()
    con.close()
    return df


def _ks(y: np.ndarray, p: np.ndarray) -> float:
    o = np.argsort(p)
    yb = y[o]
    cb = np.cumsum(yb) / yb.sum()
    cg = np.cumsum(1 - yb) / (1 - yb).sum()
    return float(np.max(np.abs(cb - cg)))


def entrenar(vars_parquet: str | Path, dst_json: str | Path, *, pct_muestra: int = 20,
             pct_test: int = 30, iv_min: float = 0.02, corr_max: float = 0.7,
             max_vars: int = 12, ganancia_min_auc: float = 0.001,
             base: float = 600, odds: float = 50, pdo: float = 20,
             log=print) -> dict:
    df = _cargar(vars_parquet, pct_muestra)
    y = df.pop("y").to_numpy()
    cuit = df.pop("cuit").to_numpy()
    test = (pd.util.hash_array(cuit) % 100) < pct_test
    log(f"muestra: {len(df):,} filas | tasa malos {y.mean():.4f} | train {(~test).sum():,} test {test.sum():,}")

    bins: dict[str, Binning] = {}
    for c in df.columns:
        b = binning_monotono(c, df[c].to_numpy()[~test], y[~test])
        if len(b.n) + (b.n_nulo > 0) > 1:
            bins[c] = b
    W = pd.DataFrame({c: b.transformar(df[c].to_numpy()) for c, b in bins.items()})
    ranking = sorted(((b.iv, c) for c, b in bins.items() if b.iv >= iv_min), reverse=True)

    def ajustar(cols):
        lr = LogisticRegression(C=1e6, max_iter=500)
        lr.fit(W.loc[~test, cols], y[~test])
        return lr, roc_auc_score(y[test], lr.predict_proba(W.loc[test, cols])[:, 1])

    elegidas: list[str] = []
    auc_actual = 0.5
    for iv, c in ranking:
        if len(elegidas) >= max_vars:
            break
        if elegidas and W.loc[~test, elegidas].corrwith(W.loc[~test, c]).abs().max() > corr_max:
            log(f"  - {c:<22} IV {iv:.3f}  descartada por correlación")
            continue
        lr, auc = ajustar(elegidas + [c])
        if (lr.coef_[0] >= 0).any():
            log(f"  - {c:<22} IV {iv:.3f}  descartada: signo invertido")
            continue
        if auc - auc_actual < ganancia_min_auc:
            log(f"  - {c:<22} IV {iv:.3f}  descartada: no suma AUC ({auc - auc_actual:+.4f})")
            continue
        elegidas.append(c)
        auc_actual = auc
        log(f"  + {c:<22} IV {iv:.3f}  Gini test {2 * auc - 1:.4f}")

    lr, auc_test = ajustar(elegidas)
    p_tr = lr.predict_proba(W.loc[~test, elegidas])[:, 1]
    p_te = lr.predict_proba(W.loc[test, elegidas])[:, 1]
    b0 = float(lr.intercept_[0])
    coefs = dict(zip(elegidas, map(float, lr.coef_[0])))

    factor = pdo / np.log(2)
    offset = base - factor * np.log(odds)
    k = len(elegidas)
    const = (offset - factor * b0) / k

    variables = []
    for c in elegidas:
        b, beta = bins[c], coefs[c]
        lims = [None] + b.cortes + [None]
        filas = [{
            "desde": lims[i], "hasta": lims[i + 1], "nulo": False,
            "n": b.n[i], "tasa_malos": round(b.malos[i] / b.n[i], 5),
            "woe": round(b.woe[i], 6),
            "puntos": int(round(-factor * beta * b.woe[i] + const)),
        } for i in range(len(b.n))]
        filas.append({
            "desde": None, "hasta": None, "nulo": True, "n": b.n_nulo,
            "tasa_malos": round(b.malos_nulo / b.n_nulo, 5) if b.n_nulo else None,
            "woe": round(b.woe_nulo, 6),
            "puntos": int(round(-factor * beta * b.woe_nulo + const)),
        })
        variables.append({"nombre": c, "coef": round(beta, 6), "iv": round(b.iv, 4), "bins": filas})

    modelo = {
        "version": "0.1.0",
        "creado": dt.datetime.now().isoformat(timespec="seconds"),
        "fuente": {"archivo_variables": Path(vars_parquet).name, "pct_muestra": pct_muestra},
        "poblacion": POBLACION_SQL,
        "target": "target_sit3p: situación >= 3 en cualquier entidad en los meses de desempeño",
        "convencion_woe": "ln(%buenos/%malos); bin = desde < x <= hasta (None = infinito)",
        "escala": {"base": base, "odds_buenos_malos": odds, "pdo": pdo,
                   "factor": round(float(factor), 6), "offset": round(float(offset), 6)},
        "intercepto": round(b0, 6),
        "variables": variables,
        "metricas": {
            "n_train": int((~test).sum()), "n_test": int(test.sum()),
            "tasa_malos_train": round(float(y[~test].mean()), 5),
            "gini_train": round(2 * roc_auc_score(y[~test], p_tr) - 1, 4),
            "gini_test": round(2 * auc_test - 1, 4),
            "ks_test": round(_ks(y[test], p_te), 4),
        },
        "iv_todas": {c: round(b.iv, 4) for c, b in sorted(bins.items(), key=lambda t: -t[1].iv)},
    }
    Path(dst_json).write_text(json.dumps(modelo, ensure_ascii=False, indent=2))
    log(f"Gini train {modelo['metricas']['gini_train']} | test {modelo['metricas']['gini_test']} | KS {modelo['metricas']['ks_test']}")
    return modelo

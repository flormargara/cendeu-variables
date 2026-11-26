"""Tests de cendeu actualizar: detección del corte, controles, PSI, estado y resumen (datos sintéticos)."""
from __future__ import annotations

import subprocess

import duckdb
import numpy as np
import pandas as pd
import pytest

from cendeu import actualizar as act
from cendeu.pipeline import periodo_mas

CORTE = 202610
PERIODOS = [periodo_mas(CORTE, -k) for k in range(24)]


def test_detectar_y_proximo_corte(tmp_path):
    e = tmp_path / "entrada"
    e.mkdir()
    for n in ["24DSF202610.7Z", "202610DEUDORES", "1dsf202610.zip", "202610INFRET", "20261030PADRON.7Z"]:
        (e / n).mkdir() if "." not in n else (e / n).write_bytes(b"x")
    d = act.detectar(e, CORTE)
    assert not d["completo"] and d["faltan"] == [
        "202610INFRETPA", "AAAAMMDDPADRON posterior a 202610",
        "AAAAMMDDCHEQUES desde el fin de 202610 hasta 45 días después"]
    (e / "202610INFRETPA").mkdir()
    (e / "20261201PADRON").mkdir()
    (e / "20261201PADRON.7Z").write_bytes(b"x")
    (e / "20261030CHEQUES.7z").write_bytes(b"x")     # anterior al fin del mes de corte: no sirve
    (e / "20261231CHEQUES.7z").write_bytes(b"x")     # más de 45 días después
    assert not act.detectar(e, CORTE)["completo"]
    (e / "20261203CHEQUES.7z").write_bytes(b"x")
    (e / "20261031CHEQUES.7z").write_bytes(b"x")     # la más cercana al fin de mes
    d = act.detectar(e, CORTE)
    assert d["completo"] and d["padron"] == 20261201 and d["cheques"] == 20261031
    assert d["encontrados"]["padron"].endswith("20261201PADRON")   # la carpeta antes que el comprimido
    assert d["encontrados"]["1dsf"].endswith("1dsf202610.zip")
    datos = tmp_path / "datos"
    for p in (202609, 202608):
        (datos / "deudores_24m" / f"periodo={p}").mkdir(parents=True)
    assert act.proximo_corte(datos) == CORTE


def _deudores(dst, periodos, entidades=(7, 11), cuits=100, factor=1.0, omitir=()):
    con = duckdb.connect()
    for p in periodos:
        if p in omitir:
            continue
        df = pd.DataFrame([(e, 11, 20_000_000_000 + c, 1, 10.0 * factor, "0") for e in entidades for c in range(cuits)],
                          columns=["entidad", "tipo_id", "cuit", "situacion", "monto", "flag"])
        d = dst / f"periodo={p}"
        d.mkdir(parents=True)
        con.execute(f"COPY (SELECT * FROM df) TO '{d}/data_0.parquet'")


@pytest.fixture
def refs(tmp_path):
    r = tmp_path / "ref"
    r.mkdir()
    (r / "entidades.csv").write_text("entidad,tipo_agrupado\n7,BANCO\n11,BANCO\n")
    (r / "entidades_equivalencias.csv").write_text("entidad_origen,entidad_destino\n150,7\n")
    filas = [f"{p},1.0,{1 if p == CORTE else 0}" for p in PERIODOS]
    (r / "ipc.csv").write_text("periodo,ipc,imputado\n" + "\n".join(filas) + "\n")
    return r


def _ctrl(ok=True):
    return {"pares_control": 48, "pares_con_datos": 48, "exactos": 48 if ok else 40, "control_doble": 0, "pares_sin_control": 0}


def test_controles_corte_sano(tmp_path, refs):
    _deudores(tmp_path / "nuevo", PERIODOS)
    _deudores(tmp_path / "anterior", [periodo_mas(p, -1) for p in PERIODOS])
    r = act.controles(CORTE, tmp_path / "nuevo", tmp_path / "anterior", None, _ctrl(), refs, refs / "ipc.csv", "1GB")
    niveles = {f["control"]: f["nivel"] for f in r["filas"]}
    assert r["errores"] == 0
    assert niveles["IPC hasta el corte"] == "alerta"            # el corte tiene IPC imputado
    assert niveles["Meses solapados contra el corte anterior"] == "ok"
    assert r["stats"][CORTE]["filas"] == 200


def test_controles_con_errores(tmp_path, refs):
    _deudores(tmp_path / "nuevo", PERIODOS, entidades=(7, 11, 999), omitir=(periodo_mas(CORTE, -5),))
    _deudores(tmp_path / "anterior", [periodo_mas(p, -1) for p in PERIODOS], factor=2.0)
    r = act.controles(CORTE, tmp_path / "nuevo", tmp_path / "anterior", None, _ctrl(ok=False), refs, refs / "ipc.csv", "1GB")
    errores = {f["control"] for f in r["filas"] if f["nivel"] == "error"}
    assert errores == {"Totales contra 24DSF_control.txt", "24 meses terminando en el corte",
                       "Meses solapados contra el corte anterior", "Entidades con tipo asignado"}
    assert "999" in next(f["detalle"] for f in r["filas"] if f["control"] == "Entidades con tipo asignado")


def test_psi_fechas(tmp_path):
    rng = np.random.default_rng(3)
    con = duckdb.connect()
    for obs, loc in [(202603, 0.0), (202604, 0.0), (202610, 2.0)]:
        df = pd.DataFrame({"CUIT": np.arange(5000) + 20_000_000_000, "PERIODO_OBS": obs, "EN_POBLACION": 1,
                           "CEN_MTO_DEUDA_1M": rng.normal(loc=loc, size=5000)})
        d = tmp_path / "vars" / f"periodo_obs={obs}"
        d.mkdir(parents=True)
        con.execute(f"COPY (SELECT * FROM df) TO '{d}/parte_00.parquet'")
    cat = tmp_path / "cat.yaml"
    from tests.test_poder import CATALOGO   # catálogo mínimo válido
    cat.write_text(CATALOGO.replace("CEN_VAL_FUERTE_1M", "CEN_MTO_DEUDA_1M"), encoding="utf-8")
    filas = {r["obs"]: r["psi"] for r in act.psi_fechas(tmp_path / "vars", 202603, [202604, 202610], cat, pct=100, memoria="1GB")
             if r["variable"] == "CEN_MTO_DEUDA_1M"}
    assert filas[202604] < 0.05 and filas[202610] > 1


def test_estado_y_resumen(tmp_path):
    est = act.Estado(tmp_path)
    for p in act.PASOS[:4]:
        est.guardar(p, segundos=60)
    est.guardar("controles", filas=[{"control": "X", "nivel": "ok", "detalle": "d"}], segundos=60)
    est.guardar("variables", obs=[202604, 202610], nuevas=[202604, 202610], referencia=202603, segundos=120)
    est.guardar("monitoreo", referencia=202603, variables=10, psi_max=0.2,
                alertas=[{"variable": "CEN_A_1M", "obs": 202610, "psi": 0.2}], segundos=10)
    assert act.Estado(tmp_path).hecho("variables")
    est.olvidar_desde("promover")
    assert est.hecho("controles") and not act.Estado(tmp_path).hecho("variables")
    est.guardar("variables", obs=[202604, 202610], nuevas=[202604, 202610], referencia=202603)
    est.guardar("monitoreo", referencia=202603, variables=10, psi_max=0.2,
                alertas=[{"variable": "CEN_A_1M", "obs": 202610, "psi": 0.2}])
    texto = act.escribir_resumen(CORTE, est, tmp_path / "rep").read_text()
    assert "| X | ok | d |" in texto and "`CEN_A_1M`" in texto and "202604" in texto


def test_registrar_simulado(monkeypatch, capsys):
    def falla(*a, **k):
        raise subprocess.CalledProcessError(1, "aws")
    monkeypatch.setattr(act.subprocess, "run", falla)
    act.registrar("x.csv", [{"a": 1}], simular=True)
    assert "1 filas nuevas" in capsys.readouterr().out


def test_actualizar_frena_si_falta_un_paquete(tmp_path):
    (tmp_path / "entrada").mkdir()
    with pytest.raises(SystemExit, match="incompleto"):
        act.actualizar(CORTE, str(tmp_path / "entrada"), tmp_path / "datos")


def test_fechas_a_armar_completa_el_target_de_la_foto():
    # 202609 se armó como foto (sin target); en el corte 202703 tiene que rehacerse con su desempeño completo
    existentes = [202509, 202603, 202609, 202610]
    nuevas, obs = act.fechas_a_armar(202703, existentes)
    assert nuevas == obs == [202609, 202703]
    # un corte intermedio arma la foto y la fecha que recién completa su ventana
    assert act.fechas_a_armar(202610, [202603, 202609]) == ([202604, 202610], [202604, 202610])
    # recalcular rehace además todas las existentes
    nuevas, obs = act.fechas_a_armar(202703, existentes, recalcular=True)
    assert nuevas == [202609, 202703] and obs == [202509, 202603, 202609, 202610, 202703]

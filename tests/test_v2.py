"""Tests de la v2 (base + variables desde el catálogo) con datos sintéticos calculados a mano."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from cendeu import v2
from cendeu.pipeline import periodo_mas

RAIZ = Path(__file__).parents[1]
PERIODOS = [periodo_mas(202410, k) for k in range(24)]  # 202410..202609
OBS = 202603

A = 20000000017   # deterioro 2-3-2 y cura; alta en PNFC en 202601; situación 3 en 202606 (target)
B = 27000000029   # entidad 150 hasta 202505 y 7 desde 202506 (fusión: no debe haber altas ni bajas)
C = 20300000001   # presente, ausente 4 meses, vuelve (huecos, reactivación)
D = 20400000002   # fallecido en 202512
E_VIEJO, E_NUEVO = 20111111112, 20222222223  # CUIT dado de baja y su reemplazo


def _filas():
    f = []
    sit_a = {202510: 2, 202511: 3, 202512: 2, 202606: 3}
    for p in PERIODOS:
        f.append((7, A, sit_a.get(p, 1), 100.0, p))
        if p >= 202601 and p != 202609:      # en 202609 la fila de PNFC sólo está en el mensual
            f.append((55494, A, 1, 50.0, p))
        f.append((150 if p <= 202505 else 7, B, 1, 200.0, p))
        if 202504 <= p <= 202506 or p >= 202511:
            f.append((285, C, 1, 30.0, p))
        f.append((7, D, 1, 10.0, p))
        f.append((11, E_VIEJO if p <= 202512 else E_NUEVO, 1, 70.0, p))
    return f


@pytest.fixture(scope="module")
def resultado(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("v2")
    con = duckdb.connect()
    df = pd.DataFrame(_filas(), columns=["entidad", "cuit", "situacion", "monto", "periodo"])
    df["tipo_id"], df["flag"] = 11, "0"
    for p, g in df.groupby("periodo"):
        d = tmp / "d24" / f"periodo={p}"
        d.mkdir(parents=True)
        con.execute(f"COPY (SELECT entidad::INTEGER entidad, tipo_id::UTINYINT tipo_id, cuit::BIGINT cuit, situacion::UTINYINT situacion, "
                    f"monto::DECIMAL(14,1) monto, flag FROM g) TO '{d}/data_0.parquet'")
    # mensual 202609: las filas del 24DSF + la de PNFC de A, con días de atraso
    m = df[df.periodo == 202609].copy()
    m = pd.concat([m, pd.DataFrame([dict(entidad=55494, cuit=A, situacion=1, monto=50.0, periodo=202609, tipo_id=11, flag="0")])])
    (tmp / "men" / "periodo=202609").mkdir(parents=True)
    con.execute(f"""COPY (SELECT entidad::BIGINT entidad, tipo_id::BIGINT tipo_id, cuit::BIGINT cuit, situacion::BIGINT situacion,
                   monto::DECIMAL(14,1) prestamos, 0::DECIMAL(14,1) garantias_otorgadas, 0::DECIMAL(14,1) otros_conceptos,
                   0::DECIMAL(14,1) gtia_pref_a, 0::DECIMAL(14,1) gtia_pref_b, 0::BIGINT proceso_judicial, 0::BIGINT refinanciacion,
                   0::BIGINT recategorizacion_oblig, 0::BIGINT situacion_juridica, 0::BIGINT irrecuperable_disp_tecnica,
                   CASE WHEN cuit = {A} AND entidad = 7 THEN 30 ELSE 0 END::BIGINT dias_atraso
                   FROM m) TO '{tmp}/men/periodo=202609/data.parquet'""")
    pd.DataFrame({"periodo": PERIODOS, "ipc": 1.0, "imputado": 0}).to_csv(tmp / "ipc.csv", index=False)
    con.execute(f"""COPY (SELECT * FROM (VALUES
        ({A}, 0, NULL, NULL, '1990-05-01', 'F', 0, NULL),
        ({D}, 0, NULL, NULL, '1950-01-01', 'M', 1, '20251201'),
        ({E_VIEJO}, 0, '*', {E_NUEVO}, '1980-01-01', 'M', 1, NULL),
        ({E_NUEVO}, 620100, NULL, NULL, '1980-01-01', 'M', 1, NULL))
        t(cuit, actividad, baja, cuit_reemplazo, fecha_nac_contrato, sexo, provincia, fecha_fallecimiento))
        TO '{tmp}/padron.parquet'""")
    con.execute(f"COPY (SELECT 11 tipo_id, {B}::BIGINT cuit, 201001 desde_sit1) TO '{tmp}/1dsf.parquet'")

    base = v2.construir_base(tmp / "d24", tmp / "base", tmp / "ipc.csv", RAIZ / "referencias",
                             mensual=tmp / "men", padron=tmp / "padron.parquet", uno_dsf=tmp / "1dsf.parquet",
                             n_buckets=2, memory_limit="1GB")
    cat = RAIZ / "catalogo" / "variables.yaml"
    out = v2.construir_variables(base, OBS, tmp / "vars", cat, n_buckets=2, memory_limit="1GB")
    out9 = v2.construir_variables(base, 202609, tmp / "vars", cat, n_buckets=2, memory_limit="1GB")
    out0 = v2.construir_variables(base, 202509, tmp / "vars", cat, n_buckets=2, memory_limit="1GB")
    leer = lambda d: con.execute(f"SELECT * FROM read_parquet('{d}/*.parquet')").df().set_index("CUIT")
    return leer(out), leer(out9), leer(out0)


def test_todas_las_variables_del_catalogo(resultado):
    from cendeu.catalogo import cargar
    r = resultado[0]
    nombres = [v["nombre"] for v in cargar(RAIZ / "catalogo" / "variables.yaml").variables]
    assert set(nombres) <= set(r.columns)
    assert set(r.index) == {A, B, C, D, E_NUEVO}   # E_VIEJO quedó unificado en E_NUEVO


def test_calidad_y_transiciones(resultado):
    a = resultado[0].loc[A]
    assert a.CEN_MAX_SIT_12M == 3 and a.CEN_MAX_SIT_3M == 1
    assert a.CEN_CNT_MESES_SIT2P_12M == 3
    assert a.CEN_RCM_SIT2P_12M == 3 and a.CEN_RCM_SIT3P_12M == 4
    assert a.CEN_RAT_CURA_12M == pytest.approx(1 / 3)
    assert a.CEN_RAT_ROLL_2A3_12M == pytest.approx(0.5)
    assert a.CEN_MAX_RACHA_SIT2P_12M == 3 and a.CEN_CNT_RACHA_SIT2P_1M == 0 and a.CEN_CNT_RACHA_SIT1_1M == 3
    assert pd.isna(a.CEN_CNT_MESES_SIT1_ININT_1M)        # foto del 1DSF (202609): nula antes del corte
    assert a.CEN_FLG_ALERTA_REINCIDENCIA_12M == 0
    assert a.CEN_MOD_SIT_12M == 1


def test_magnitud_velocidad_mix(resultado):
    a = resultado[0].loc[A]
    assert a.CEN_MTO_DEUDA_1M == pytest.approx(150)
    assert a.CEN_CNT_ENT_1M == 2 and a.CEN_CNT_ALTAS_3M == 1 and a.CEN_CNT_ALTAS_12M == 1
    assert a.CEN_PCT_DEUDA_PNFC_1M == pytest.approx(1 / 3)
    assert a.CEN_CNT_TIPOS_ENT_1M == 2
    assert a.CEN_TEN_DEUDA_3M == pytest.approx(1.0)
    assert pd.isna(a.CEN_RSI_DEUDA_3M)          # sin variaciones en la ventana
    assert a.CEN_RSI_DEUDA_6M == pytest.approx(100.0)  # sólo una suba (alta en PNFC)
    assert a.CEN_DIF_SIT_3M == 0


def test_target_y_poblacion(resultado):
    r, r9, _ = resultado
    assert r.loc[A, "CEN_TGT_SIT3P_6M"] == 1 and r.loc[B, "CEN_TGT_SIT3P_6M"] == 0
    assert r.loc[D, "EN_POBLACION"] == 0 and r.loc[D, "CEN_FLG_FALLECIDO_1M"] == 1
    assert r.loc[A, "EN_POBLACION"] == 1
    assert r9["CEN_TGT_SIT3P_6M"].isna().all()   # desempeño incompleto


def test_fusion_sin_altas_ni_bajas(resultado):
    b = resultado[0].loc[B]
    assert b.CEN_CNT_ALTAS_12M == 0 and b.CEN_CNT_BAJAS_12M == 0
    assert b.CEN_CNT_ENT_DISTINTAS_12M == 1 and b.CEN_CNT_CAMBIO_PRINC_12M == 0
    assert pd.isna(b.CEN_CNT_MESES_SIT1_ININT_1M)


def test_huecos_y_reactivacion(resultado):
    c = resultado[0].loc[C]
    assert c.CEN_CNT_MESES_PRESENTE_12M == 8 and c.CEN_CNT_HUECOS_12M == 4
    assert c.CEN_FLG_REACTIVACION_12M == 1
    assert c.CEN_CNT_ALTAS_12M == 2 and c.CEN_CNT_BAJAS_12M == 1
    assert c.CEN_RCM_PRIMERA_APARICION_12M == 11


def test_cuit_reemplazado(resultado):
    e = resultado[0].loc[E_NUEVO]
    assert e.CEN_FLG_CUIT_REEMPLAZADO_1M == 1 and e.CEN_CNT_ALTAS_12M == 0


def test_mensual_completa_ultimo_mes(resultado):
    r, r9, _ = resultado
    assert r9.loc[A, "CEN_CNT_ENT_1M"] == 2                 # la fila de PNFC viene sólo del mensual
    assert r9.loc[A, "CEN_MAX_DIAS_ATRASO_1M"] == 30
    assert r["CEN_MAX_DIAS_ATRASO_1M"].isna().all()         # sin mensual para 202603


def test_1dsf_solo_en_su_corte(resultado):
    r9 = resultado[1]
    assert r9.loc[B, "CEN_CNT_MESES_SIT1_ININT_1M"] == (2026 - 2010) * 12 + 8   # desde 201001, sin tope
    assert r9.loc[A, "CEN_CNT_MESES_SIT1_ININT_1M"] == 3                         # última situación 2+ en 202606


def test_tipos_estables_entre_fechas(resultado):
    import pandas.api.types as pt
    r, r9, r0 = resultado
    assert pt.is_float_dtype(r0["CEN_RAT_CURA_12M"]) and pt.is_float_dtype(r["CEN_RAT_CURA_12M"])   # nula en 202509, igual DOUBLE
    assert pt.is_float_dtype(r["CEN_PCT_DEUDA_GTIA_PREF_1M"])                                        # futuro: nula pero DOUBLE


def test_sin_mes_previo_queda_nula(resultado):
    r0 = resultado[2]                       # 202509: la ventana de 12 meses necesita 202409, que no está
    assert r0["CEN_CNT_ALTAS_12M"].isna().all() and r0["CEN_RAT_CURA_12M"].isna().all()
    assert r0["CEN_CNT_ALTAS_3M"].notna().all() and r0["CEN_CNT_ALTAS_6M"].notna().all()
    assert r0["CEN_MAX_SIT_12M"].notna().all()

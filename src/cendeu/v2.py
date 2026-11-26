"""Variables CENDEU v2: base limpia, series densas por CUIT y variables generadas desde el catálogo.

Flujo:
    1. ``construir_base``: 24DSF + mensual → filas CUIT × entidad × período limpias, en pesos constantes,
       con equivalencias de entidades y CUITs reemplazados aplicados. Particionado por período y bucket
       (cuit % N) para poder procesar las variables por partes. También escribe la tabla de persona
       (padrón + 1DSF) y el resumen del mensual por CUIT.
    2. ``construir_variables``: para una fecha de observación t arma, por bucket, 13 meses de series
       densas (t-12..t; el mes t-12 sólo sirve de mes previo para altas y transiciones) como listas por
       CUIT, y evalúa las expresiones ``sql`` del catálogo sobre esas listas.

Índices de las listas: posición 1 = t-12, posición 13 = t. Ventana de v meses = posiciones 14-v..13.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path

from . import catalogo as cat_mod
from .pipeline import conexion, mi, periodo_mas

N_BUCKETS = 16
LARGO = 13  # meses en las listas (12 de ventana + 1 previo)

MACROS = """
CREATE OR REPLACE MACRO ult(l) AS l[13];
CREATE OR REPLACE MACRO ven(l, v) AS list_slice(l, 14 - v, 13);
CREATE OR REPLACE MACRO ini(l, v) AS l[14 - v];
-- pares (mes anterior, mes) para los meses en las posiciones k0..13
CREATE OR REPLACE MACRO pares(l, k0) AS list_transform(range(k0, 14), lambda k: {'a': l[k - 1], 'b': l[k]});
-- meses desde la última posición verdadera de una lista booleana (0 = el último mes); NULL si no hay
CREATE OR REPLACE MACRO rcm(lb) AS list_position(list_reverse(lb), true) - 1;
CREATE OR REPLACE MACRO bits(l, umbral) AS
    list_aggregate(list_transform(l, lambda z: CASE WHEN z >= umbral THEN '1' ELSE '0' END), 'string_agg', '');
-- situación codificada: 0 ausente, 1 normal, 2 situación 2 o peor
CREATE OR REPLACE MACRO cod_sit(l) AS
    list_aggregate(list_transform(l, lambda z: CASE WHEN z = 0 THEN '0' WHEN z = 1 THEN '1' ELSE '2' END), 'string_agg', '');
CREATE OR REPLACE MACRO max_racha(b) AS coalesce(list_max(list_transform(string_split(b, '0'), lambda q: length(q))), 0);
-- moda de la situación; ante empate, la peor
CREATE OR REPLACE MACRO moda_peor(l) AS
    list_max(list_transform(range(0, 6), lambda k: len(list_filter(l, lambda z: z = k)) * 10 + k)) % 10;
CREATE OR REPLACE MACRO rsi(l, v) AS
    CASE WHEN list_sum(list_transform(pares(l, 15 - v), lambda p: abs(p.b - p.a))) = 0 THEN NULL
         WHEN list_sum(list_transform(pares(l, 15 - v), lambda p: greatest(p.a - p.b, 0))) = 0 THEN 100.0
         ELSE 100 - 100 / (1 + list_sum(list_transform(pares(l, 15 - v), lambda p: greatest(p.b - p.a, 0)))
                             / list_sum(list_transform(pares(l, 15 - v), lambda p: greatest(p.a - p.b, 0)))) END;
-- pendiente de la regresión de la serie sobre el mes, relativa al promedio
CREATE OR REPLACE MACRO pnd(l, v) AS
    (list_sum(list_transform(range(1, v + 1), lambda k: (k - (v + 1) / 2.0) * ven(l, v)[k])) / (v * (v * v - 1) / 12.0))
    / nullif(list_avg(ven(l, v)), 0);
CREATE OR REPLACE MACRO tipo_persona(c) AS
    CASE WHEN c // 1000000000 IN (20, 23, 24, 27) THEN 'HUMANA' WHEN c // 1000000000 IN (30, 33, 34) THEN 'JURIDICA' ELSE 'OTRO' END;
"""

TIPOS = ["BANCO", "CIA_FIN", "TARJETA_NB", "PNFC", "FIDEICOMISO"]
TIPO_SQL = {"DECIMAL": "FLOAT", "INTEGER": "INTEGER", "FLAG": "INTEGER", "CATEGORICA": "VARCHAR"}
ZSTD = "COMPRESSION zstd, COMPRESSION_LEVEL 9"
BANCARIOS = "('BANCO', 'CIA_FIN')"

# ----------------------------------------------------------------------------- modo muestra
ARCHIVO_MUESTRA = "_muestra.json"


def filtro_muestra(pct: int | None, col: str = "cuit") -> str:
    """Mismo criterio que poder, validación y scorecard: ``hash(CUIT) % 100 < pct`` (la muestra de un informe
    con porcentaje menor o igual queda contenida en la de las variables)."""
    return f" AND hash({col}) % 100 < {pct}" if pct is not None else ""


def pct_muestra_de(dir_vars: str | Path) -> int:
    """Porcentaje de CUITs con que se armaron las variables de ``dir_vars`` (100 = población completa).

    Las carpetas escritas antes del modo muestra no tienen ``_muestra.json`` y son población completa.
    """
    pcts = [json.loads(f.read_text())["pct_muestra"] for f in Path(dir_vars).glob(f"periodo_obs=*/{ARCHIVO_MUESTRA}")]
    return min(pcts, default=100)


def verificar_muestra(dir_vars: str | Path, pct: int) -> None:
    """Falla si se pide un porcentaje mayor que el de la muestra con que se armaron las variables."""
    disponible = pct_muestra_de(dir_vars)
    if pct > disponible:
        raise ValueError(f"{dir_vars}: las variables son una muestra del {disponible}% de los CUITs y se pidió el {pct}%. "
                         f"Regenerarlas con `cendeu variables-v2 --pct-muestra {pct}` (o mayor) o bajar el porcentaje.")


def _hash(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def _escribir_meta(dst: Path, meta: dict) -> None:
    (dst / "_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _q(path) -> str:
    return str(path).replace("'", "''")


# --------------------------------------------------------------------------- cheques
TOLERANCIA_CHEQUES = 45   # días máximos entre el fin del mes de observación y la foto de cheques que se usa


def _fin_mes(p: int) -> dt.date:
    siguiente = periodo_mas(p, 1)
    return dt.date(siguiente // 100, siguiente % 100, 1) - dt.timedelta(days=1)


def fotos_cheques(fechas: list[int], periodos: list[int], tolerancia: int = TOLERANCIA_CHEQUES) -> dict[int, int]:
    """``{período: foto AAAAMMDD}``: para cada período, la foto de la Central de Cheques Rechazados más cercana
    tomada desde el último día del mes y hasta ``tolerancia`` días después. Una foto es el estado de la central
    el día en que se bajó; con las fechas de rechazo, levantamiento y pago de multa se reconstruye el estado al
    fin del mes. Lo que no se puede reconstruir son los cheques dados de baja entre el fin de mes y la foto
    (por eso la tolerancia corta). Sin foto en ese rango, las variables de cheques del período quedan nulas."""
    salida = {}
    for p in periodos:
        fin = _fin_mes(p)
        cands = [f for f in fechas
                 if 0 <= (dt.date(f // 10000, f // 100 % 100, f % 100) - fin).days <= tolerancia]
        if cands:
            salida[p] = min(cands)
    return salida


def _cheques_sql(foto: Path, p: int, ipc_base: float, n_buckets: int) -> str:
    """Resumen por CUIT de la foto de cheques al último día del mes ``p``. Montos en miles de pesos constantes
    (IPC del mes del rechazo; los anteriores al IPC versionado usan el primer mes disponible)."""
    fin = _fin_mes(p).isoformat()
    ventana = lambda v: f"mes BETWEEN {periodo_mas(p, -(v - 1))} AND {p}"
    sf = lambda v: f"causal = 2 AND {ventana(v)}"
    impago = f"(fecha_levantamiento IS NULL OR fecha_levantamiento > DATE '{fin}')"
    return f"""
      WITH c AS (
        SELECT DISTINCT coalesce(rp.cuit_reemplazo, f.cuit) AS cuit, f.cuit_relacionado AS rel, f.nro_cheque, f.fecha_rechazo,
               f.monto, f.causal, f.fecha_levantamiento, f.pago_multa,
               year(f.fecha_rechazo) * 100 + month(f.fecha_rechazo) AS mes
        FROM read_parquet('{_q(foto)}') f LEFT JOIN reemp rp ON rp.cuit = f.cuit
        WHERE f.fecha_rechazo <= DATE '{fin}'),
      d AS (
        SELECT c.*, c.monto / 1000 * {ipc_base} / coalesce(i.ipc, (SELECT ipc FROM ipc ORDER BY periodo LIMIT 1)) AS mto
        FROM c LEFT JOIN ipc i ON i.periodo = c.mes)
      SELECT cuit, {p} AS periodo,
        count(*) FILTER (rel IS NULL AND {sf(3)}) AS CHQ_SF_3,
        count(*) FILTER (rel IS NULL AND {sf(6)}) AS CHQ_SF_6,
        count(*) FILTER (rel IS NULL AND {sf(12)}) AS CHQ_SF_12,
        coalesce(sum(mto) FILTER (rel IS NULL AND {sf(3)}), 0) AS CHQ_MTO_SF_3,
        coalesce(sum(mto) FILTER (rel IS NULL AND {sf(6)}), 0) AS CHQ_MTO_SF_6,
        coalesce(sum(mto) FILTER (rel IS NULL AND {sf(12)}), 0) AS CHQ_MTO_SF_12,
        count(*) FILTER (rel IS NULL AND causal = 1 AND {ventana(12)}) AS CHQ_VF_12,
        count(*) FILTER (rel IS NULL AND {sf(12)} AND NOT {impago}) AS CHQ_LEV_12,
        count(*) FILTER (rel IS NULL AND causal = 2 AND {impago}) AS CHQ_IMPAGOS,
        coalesce(sum(mto) FILTER (rel IS NULL AND causal = 2 AND {impago}), 0) AS CHQ_MTO_IMPAGOS,
        count(*) FILTER (rel IS NULL AND causal = 2 AND (pago_multa = 'IMPAGA'
                         OR TRY_STRPTIME(pago_multa, '%d/%m/%Y')::DATE > DATE '{fin}')) AS CHQ_MULTA_IMPAGA,
        {mi(p)} - max(mes // 100 * 12 + mes % 100) FILTER (rel IS NULL AND causal = 2 AND {ventana(12)}) AS CHQ_RCM_SF,
        count(DISTINCT (rel, nro_cheque, fecha_rechazo, monto)) FILTER (rel IS NOT NULL AND {sf(12)}) AS CHQ_REL_SF_12,
        count(DISTINCT rel) FILTER (rel IS NOT NULL AND {sf(12)}) AS CHQ_REL_EMP_12,
        count(DISTINCT (rel, nro_cheque, fecha_rechazo, monto)) FILTER (rel IS NOT NULL AND causal = 2 AND {impago}) AS CHQ_REL_IMPAGOS,
        cuit % {n_buckets} AS bucket
      FROM d GROUP BY cuit"""


# --------------------------------------------------------------------------- base
def construir_base(dir_24dsf: str | Path, dst: str | Path, ipc_csv: str | Path, referencias: str | Path,
                   mensual: str | Path | None = None, padron: str | Path | None = None,
                   uno_dsf: str | Path | None = None, base_ipc: int | None = None,
                   n_buckets: int = N_BUCKETS, hilos: int = 6, desde: int | None = None,
                   solo: list[int] | None = None, extras: bool = True,
                   reemplazo_mensual: list[int] | None = None, cheques: str | Path | None = None, **kw) -> Path:
    """Escribe ``dst/deudores/periodo=P/bucket=B``, ``dst/persona/bucket=B``, ``dst/mensual/periodo=P/bucket=B`` y
    ``dst/cheques/periodo=P/bucket=B``.

    ``mensual``: directorio con ``periodo=P/data.parquet`` del deudores.txt convertido; esos períodos
    reemplazan al 24DSF (el 24DSF omite entidades en su primer mes). ``reemplazo_mensual``: limita el reemplazo
    a esos períodos (con un corte nuevo, sólo el mes de corte: los meses anteriores del 24DSF ya vienen
    completos y rectificados); el resumen de campos del mensual se arma igual para todos los meses que haya.
    Cada período se escribe bucket por bucket: un COPY particionado de un mes completo (~40 M filas)
    no entra en 10 GB.

    ``cheques``: directorio con ``corte=AAAAMMDD/data.parquet`` (fotos de la Central de Cheques Rechazados); cada
    período usa la foto que le asigna ``fotos_cheques``.
    """
    dst, ref = Path(dst), Path(referencias)
    dst.mkdir(parents=True, exist_ok=True)
    estado: dict = {}

    @contextmanager
    def con_base():
        # Conexión nueva por período: DuckDB no libera toda la memoria entre COPY particionados.
        with conexion(**kw) as con:
            con.execute(f"SET preserve_insertion_order = false; SET threads = {hilos};")
            con.execute(f"""
                CREATE TABLE ipc AS SELECT periodo::BIGINT periodo, ipc::DOUBLE ipc FROM read_csv('{_q(ipc_csv)}', header = true);
                CREATE TABLE eq AS SELECT entidad_origen, entidad_destino FROM read_csv('{_q(ref / "entidades_equivalencias.csv")}', header = true);
                CREATE TABLE ent AS SELECT entidad, tipo_agrupado, recupero FROM read_csv('{_q(ref / "entidades.csv")}', header = true);
                CREATE VIEW d24 AS SELECT * FROM read_parquet('{_q(Path(dir_24dsf))}/*/*.parquet', hive_partitioning = true);
            """)
            if mensual:
                con.execute(f"CREATE VIEW men AS SELECT * FROM read_parquet('{_q(Path(mensual))}/*/*.parquet', hive_partitioning = true)")
                periodos_men = [r[0] for r in con.execute("SELECT DISTINCT periodo FROM men").fetchall()]
            else:
                con.execute("CREATE VIEW men AS SELECT NULL::BIGINT periodo WHERE false")
                periodos_men = []
            if padron:
                con.execute(f"""CREATE TABLE reemp AS SELECT cuit, cuit_reemplazo FROM read_parquet('{_q(padron)}')
                                WHERE baja = '*' AND cuit_reemplazo > 0 AND cuit_reemplazo <> cuit""")
            else:
                con.execute("CREATE TABLE reemp (cuit BIGINT, cuit_reemplazo BIGINT)")
            estado["periodos_men"] = periodos_men
            yield con

    with con_base() as con:
        periodos_men = estado["periodos_men"]
        base = base_ipc or con.execute("SELECT max(periodo) FROM ipc").fetchone()[0]
        ipc_base = con.execute("SELECT ipc FROM ipc WHERE periodo = ?", [base]).fetchone()[0]
        periodos = sorted({r[0] for r in con.execute("SELECT DISTINCT periodo FROM d24").fetchall()} | set(periodos_men))
    reemplazan = [q for q in periodos_men if reemplazo_mensual is None or q in reemplazo_mensual]
    for p in periodos:
        if (desde and p < desde) or (solo and p not in solo):  # retomar una corrida cortada
            continue
        t0 = time.time()
        with con_base() as con:
            if p in reemplazan:
                fuente = f"""SELECT entidad, cuit, periodo, CASE WHEN situacion = 11 THEN 1 ELSE situacion END AS situacion,
                                    (coalesce(prestamos, 0) + coalesce(garantias_otorgadas, 0) + coalesce(otros_conceptos, 0))::DOUBLE AS monto,
                                    (proceso_judicial IN (1, 2))::INT AS pj
                             FROM men WHERE periodo = {p} AND tipo_id = 11 AND situacion > 0"""
            else:
                fuente = f"""SELECT entidad, cuit, periodo, CASE WHEN situacion = 11 THEN 1 ELSE situacion END AS situacion,
                                    monto::DOUBLE AS monto, (flag IN ('1', '2'))::INT AS pj
                             FROM d24 WHERE periodo = {p} AND tipo_id = 11 AND situacion > 0"""
            for k in range(n_buckets):
                destino = dst / "deudores" / f"periodo={p}" / f"bucket={k}"
                destino.mkdir(parents=True, exist_ok=True)
                con.execute(f"""
                COPY (
                  WITH r AS ({fuente}),
                  m AS (
                    SELECT coalesce(eq.entidad_destino, r.entidad) AS entidad, coalesce(rp.cuit_reemplazo, r.cuit) AS cuit,
                           (rp.cuit IS NOT NULL)::INT AS reemplazado, r.periodo, r.situacion, r.monto, r.pj
                    FROM r LEFT JOIN eq ON eq.entidad_origen = r.entidad LEFT JOIN reemp rp ON rp.cuit = r.cuit
                    WHERE coalesce(rp.cuit_reemplazo, r.cuit) % {n_buckets} = {k})
                  SELECT m.cuit, m.entidad, max(m.situacion)::INTEGER AS situacion,
                         sum(m.monto) * {ipc_base} / any_value(i.ipc) AS monto, max(m.pj) AS pj, max(m.reemplazado) AS reemplazado,
                         coalesce(any_value(e.tipo_agrupado), 'OTRO') AS tipo, coalesce(any_value(e.recupero), 0) AS recupero
                  FROM m JOIN ipc i USING (periodo) LEFT JOIN ent e ON e.entidad = m.entidad
                  GROUP BY m.cuit, m.entidad, m.periodo
                ) TO '{_q(destino / "data.parquet")}' (FORMAT parquet, COMPRESSION zstd)
                """)
        print(f"base {p}: {time.time() - t0:.0f}s", flush=True)

    if not extras:
        return dst
    fechas_chq = sorted(int(d.name.split("=")[1]) for d in Path(cheques).glob("corte=*")) if cheques else []
    asignadas = fotos_cheques(fechas_chq, periodos)
    with con_base() as con:
        imputados = [r[0] for r in con.execute(
            f"SELECT periodo FROM read_csv('{_q(ipc_csv)}', header = true) WHERE imputado = 1 AND periodo <= {max(periodos)}").fetchall()]
    _escribir_meta(dst, dict(
        fecha=dt.datetime.now().isoformat(timespec="seconds"), periodos=[min(periodos), max(periodos)],
        periodos_mensual=periodos_men, reemplazo_mensual=reemplazan, base_ipc=base, ipc_base=ipc_base, ipc_imputados=imputados,
        archivos={"ipc": _hash(ipc_csv), "entidades": _hash(ref / "entidades.csv"),
                  "equivalencias": _hash(ref / "entidades_equivalencias.csv")},
        padron=str(padron) if padron else None, uno_dsf=str(uno_dsf) if uno_dsf else None, n_buckets=n_buckets,
        cheques={str(p): f for p, f in asignadas.items()}))
    if imputados:
        print(f"ATENCIÓN: IPC imputado en {imputados}; rehacer la base cuando el INDEC lo publique", flush=True)
    with con_base() as con:
        # Persona: padrón + 1DSF para los CUITs de la central
        con.execute(f"""CREATE TABLE cuits AS SELECT DISTINCT cuit FROM read_parquet('{_q(dst / "deudores")}/*/*/*.parquet')""")
        sel_padron = (f"""SELECT cuit, fecha_nac_contrato, sexo, provincia, actividad, fecha_fallecimiento
                          FROM read_parquet('{_q(padron)}')""" if padron else
                      "SELECT NULL::BIGINT cuit, NULL::VARCHAR fecha_nac_contrato, NULL::VARCHAR sexo, NULL::BIGINT provincia, NULL::BIGINT actividad, NULL::VARCHAR fecha_fallecimiento WHERE false")
        sel_1dsf = (f"SELECT cuit, desde_sit1 FROM read_parquet('{_q(uno_dsf)}') WHERE tipo_id = 11" if uno_dsf else
                    "SELECT NULL::BIGINT cuit, NULL::BIGINT desde_sit1 WHERE false")
        con.execute(f"""
            COPY (
              SELECT c.cuit, TRY_CAST(nullif(p.fecha_nac_contrato, '1901-01-01') AS DATE) AS fecha_nac,
                     p.sexo, p.provincia, p.actividad,
                     CASE WHEN p.fecha_fallecimiento = '19010101' THEN DATE '1901-01-01'
                          ELSE TRY_STRPTIME(p.fecha_fallecimiento, '%Y%m%d')::DATE END AS fecha_fallecimiento,
                     (p.cuit IS NOT NULL)::INT AS en_padron, u.desde_sit1, c.cuit % {n_buckets} AS bucket
              FROM cuits c LEFT JOIN ({sel_padron}) p USING (cuit) LEFT JOIN ({sel_1dsf}) u USING (cuit)
            ) TO '{_q(dst / "persona")}' (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (bucket), OVERWRITE_OR_IGNORE true)
        """)
        # Resumen del mensual por CUIT (campos que el 24DSF no trae)
        for p in periodos_men:
            con.execute(f"""
                COPY (
                  SELECT coalesce(rp.cuit_reemplazo, m.cuit) AS cuit, {p} AS periodo,
                         max(nullif(dias_atraso, 0)) AS MAX_DIAS_ATRASO,
                         count(DISTINCT entidad) FILTER (refinanciacion = 1) AS ENT_REFINANCIACION,
                         count(DISTINCT entidad) FILTER (recategorizacion_oblig = 1) AS ENT_RECATEG_OBLIG,
                         count(DISTINCT entidad) FILTER (situacion_juridica = 1) AS ENT_SIT_JURIDICA,
                         count(DISTINCT entidad) FILTER (irrecuperable_disp_tecnica = 1) AS ENT_IRRECUP_DISP_TECNICA,
                         (sum(garantias_otorgadas) > 0)::INT AS GARANTE,
                         sum(coalesce(gtia_pref_a, 0) + coalesce(gtia_pref_b, 0)) / nullif(sum(prestamos), 0) AS PCT_GTIA_PREF,
                         coalesce(rp.cuit_reemplazo, m.cuit) % {n_buckets} AS bucket
                  FROM men m LEFT JOIN reemp rp ON rp.cuit = m.cuit
                  WHERE m.periodo = {p} AND m.tipo_id = 11 AND m.situacion > 0
                  GROUP BY ALL
                ) TO '{_q(dst / "mensual")}' (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (periodo, bucket), OVERWRITE_OR_IGNORE true)
            """)
        # Cheques rechazados: resumen por CUIT al fin de cada mes que tenga foto
        for p, f in asignadas.items():
            con.execute(f"""COPY ({_cheques_sql(Path(cheques) / f"corte={f}" / "data.parquet", p, ipc_base, n_buckets)})
                TO '{_q(dst / "cheques")}' (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (periodo, bucket), OVERWRITE_OR_IGNORE true)""")
            print(f"cheques {p}: foto {f}", flush=True)
    return dst


# --------------------------------------------------------------------- variables
def _series_sql(t: int) -> str:
    """Tablas temporales e (panel entidad), s (listas densas) y x (escalares) para un bucket ya cargado en ``b``."""
    tipos_deuda = ",\n".join(f"coalesce(sum(monto) FILTER (i = 13 AND tipo = '{k}'), 0) AS DEUDA_{k}_T" for k in TIPOS)
    tipos_ent = ",\n".join(f"count(DISTINCT entidad) FILTER (i = 13 AND tipo = '{k}') AS ENT_{k}_T" for k in TIPOS)
    return f"""
    CREATE OR REPLACE TEMP TABLE pob AS SELECT DISTINCT cuit FROM b WHERE i = 13;
    CREATE OR REPLACE TEMP TABLE e AS
      SELECT b.*, lag(i) OVER w AS i_prev, lead(i) OVER w AS i_next
      FROM b SEMI JOIN pob USING (cuit) WHERE i BETWEEN 1 AND 13
      WINDOW w AS (PARTITION BY cuit, entidad ORDER BY i);

    CREATE OR REPLACE TEMP TABLE mt AS   -- deuda por tipo y mes (para entropía)
      SELECT cuit, i, tipo, sum(monto) AS monto FROM e GROUP BY ALL;
    CREATE OR REPLACE TEMP TABLE m AS
      WITH a AS (
        SELECT cuit, i, sum(monto) AS deuda, sum(monto) FILTER (situacion >= 2) AS deuda_irr,
               sum(monto) FILTER (situacion >= 3) AS deuda_mora, max(situacion) AS sit,
               sum(situacion * monto) / nullif(sum(monto), 0) AS sit_pond, count(*) AS ent,
               count(*) FILTER (situacion >= 2) AS ent_irr,
               arg_max(entidad, {{'m': monto, 'e': -entidad}}) AS ent_princ, max(monto) AS deuda_princ,
               sum(monto) FILTER (tipo = 'PNFC') AS deuda_pnfc,
               count(*) FILTER (i >= 2 AND (i_prev IS NULL OR i_prev < i - 1)) AS altas
        FROM e GROUP BY ALL),
      h AS (SELECT e.cuit, e.i, sum((e.monto / a.deuda) ^ 2) AS hhi FROM e JOIN a USING (cuit, i) WHERE a.deuda > 0 GROUP BY ALL),
      en AS (SELECT mt.cuit, mt.i, greatest(-sum((mt.monto / a.deuda) * ln(mt.monto / a.deuda)), 0) AS entropia
             FROM mt JOIN a USING (cuit, i) WHERE a.deuda > 0 AND mt.monto > 0 GROUP BY ALL)
      SELECT a.*, h.hhi, en.entropia
      FROM a LEFT JOIN h USING (cuit, i) LEFT JOIN en USING (cuit, i);

    CREATE OR REPLACE TEMP TABLE s AS
      WITH g AS (
        SELECT pob.cuit, k.i, m.* EXCLUDE (cuit, i), bj.bajas AS bajas_sin_deuda
        FROM pob CROSS JOIN range(1, 14) k(i)
        LEFT JOIN m ON m.cuit = pob.cuit AND m.i = k.i
        LEFT JOIN (SELECT cuit, i + 1 AS i, count(*) AS bajas FROM e WHERE i <= 12 AND (i_next IS NULL OR i_next > i + 1) GROUP BY ALL) bj
               ON bj.cuit = pob.cuit AND bj.i = k.i)
      , r AS (   -- un solo ordenamiento por CUIT: lista de registros mensuales
        SELECT cuit, list({{'deuda': deuda, 'deuda_irr': deuda_irr, 'deuda_mora': deuda_mora, 'sit': sit, 'sit_pond': sit_pond,
                           'ent': ent, 'ent_irr': ent_irr, 'hhi': hhi, 'ent_princ': ent_princ, 'altas': altas,
                           'bajas': bajas_sin_deuda, 'entropia': entropia, 'deuda_pnfc': deuda_pnfc}} ORDER BY i) AS r
        FROM g GROUP BY cuit)
      SELECT cuit,
        list_transform(r, lambda z: coalesce(z.deuda, 0)) AS DEUDA,
        list_transform(r, lambda z: coalesce(z.deuda_irr, 0)) AS DEUDA_IRR,
        list_transform(r, lambda z: coalesce(z.deuda_mora, 0)) AS DEUDA_MORA,
        list_transform(r, lambda z: coalesce(z.sit, 0)::INTEGER) AS SIT,
        list_transform(r, lambda z: z.sit_pond) AS SIT_POND,
        list_transform(r, lambda z: coalesce(z.ent, 0)::INTEGER) AS ENT,
        list_transform(r, lambda z: coalesce(z.ent_irr, 0)::INTEGER) AS ENT_IRR,
        list_transform(r, lambda z: coalesce(z.deuda_irr, 0) / nullif(z.deuda, 0)) AS PCT_IRR,
        list_transform(r, lambda z: z.hhi) AS HHI,
        list_transform(r, lambda z: z.ent_princ) AS ENT_PRINC,
        list_transform(r, lambda z: coalesce(z.altas, 0)::INTEGER) AS ALTAS,
        list_transform(r, lambda z: coalesce(z.bajas, 0)::INTEGER) AS BAJAS,
        list_transform(r, lambda z: z.entropia) AS ENTROPIA,
        list_transform(r, lambda z: coalesce(z.deuda_pnfc, 0)) AS DEUDA_PNFC,
        list_transform(r, lambda z: (z.deuda IS NOT NULL)::INTEGER) AS PRES
      FROM r;

    CREATE OR REPLACE TEMP TABLE x AS
      SELECT cuit,
        count(DISTINCT entidad) FILTER (i >= 2) AS ENT_DIST_12,
        count(DISTINCT entidad) FILTER (i >= 2 AND situacion >= 3) AS ENT_SIT3P_12,
        coalesce(max(situacion) FILTER (i >= 2 AND tipo IN {BANCARIOS}), 0) AS MAXSIT_BANCARIA_12,
        coalesce(max(situacion) FILTER (i >= 2 AND tipo NOT IN {BANCARIOS}), 0) AS MAXSIT_NO_BANCARIA_12,
        coalesce(max(recupero) FILTER (i >= 2), 0) AS RECUPERO_12,
        coalesce(max(pj) FILTER (i >= 2), 0) AS PJ_12,
        coalesce(max(reemplazado) FILTER (i >= 2), 0) AS REEMPLAZADO_12,
        (count(*) FILTER (i >= 2 AND situacion >= 4 AND (i_prev IS NULL OR i_prev < i - 1)) > 0)::INT AS ENTRADA_SIT4_12,
        count(*) FILTER (i >= 8 AND tipo NOT IN {BANCARIOS} AND (i_prev IS NULL OR i_prev < i - 1)) AS ALTAS_NOBANC_6,
        count(*) FILTER (i BETWEEN 7 AND 12 AND tipo IN {BANCARIOS} AND (i_next IS NULL OR i_next > i + 1)) AS BAJAS_BANC_6,
        max(monto) FILTER (i = 13) AS DEUDA_PRINC_T,
        count(DISTINCT tipo) FILTER (i = 13) AS TIPOS_T,
        {tipos_deuda},
        {tipos_ent}
      FROM e GROUP BY cuit;

    CREATE OR REPLACE TEMP TABLE emp AS   -- entidades presentes en t-2 y t con peor situación en t
      SELECT a.cuit, count(*) AS ENT_EMPEORA_3
      FROM e a JOIN e c ON c.cuit = a.cuit AND c.entidad = a.entidad AND c.i = 11
      WHERE a.i = 13 AND a.situacion > c.situacion GROUP BY ALL;
    """


def _variables_sql(cat: cat_mod.Catalogo, t: int, mensual_disponible: bool, primero: int,
                   cheques_disponible: bool = False) -> tuple[str, str]:
    """Expresiones del catálogo. Quedan nulas, en vez de dar un valor sesgado:
    - las variables con ``disponible_desde`` posterior a t (p. ej. fotos de 202609: usarlas en fechas
      anteriores filtra información del desempeño);
    - las que necesitan el mes previo a su ventana (``requiere_mes_previo``) cuando ese mes es anterior
      al inicio de la historia;
    - las del mensual si no hay mensual para t, y las de cheques si no hay foto de cheques para t.
    """
    capa1, capa2 = [], []
    for v in cat.variables:
        expr = v["sql"]
        if v["fuente"] == "MENSUAL" and not mensual_disponible:
            expr = "NULL"
        if "CHEQUES" in v["fuente"] and not cheques_disponible:
            expr = "NULL"
        if t < int(v.get("disponible_desde") or 0):
            expr = "NULL"
        if v.get("requiere_mes_previo") and periodo_mas(t, -v["ventana_meses"]) < primero:
            expr = "NULL"
        # tipo fijo por variable: una variable anulada conserva su tipo y las partes se pueden leer juntas
        if v["tipo_dato"] == "DECIMAL":   # se calcula en doble y se guarda redondeado en simple
            expr = f"round(({expr})::DOUBLE, {v['decimales']})"
        expr = f"({expr})::{TIPO_SQL[v['tipo_dato']]}"
        (capa2 if v.get("capa") == 2 else capa1).append(f"({expr}) AS {v['nombre']}")
    return ",\n        ".join(capa1), ",\n        ".join(capa2)


def construir_variables(dir_base: str | Path, obs: int, dst: str | Path, catalogo: str | Path,
                        desempeno: int = 6, n_buckets: int = N_BUCKETS, buckets: list[int] | None = None,
                        retomar: bool = False, pct_muestra: int | None = None, **kw) -> Path:
    """Variables v2 a la fecha ``obs`` para todos los CUITs presentes en ``obs``.

    Escribe ``dst/periodo_obs=AAAAMM/parte_BB.parquet`` y ``_muestra.json``. Columnas: CUIT, PERIODO_OBS,
    EN_POBLACION, target (nulo si el desempeño no está completo) y las variables del catálogo.

    ``pct_muestra``: sólo los CUITs con ``hash(CUIT) % 100 < pct_muestra`` (modo desarrollo). La muestra queda
    registrada en ``_muestra.json``; no se pueden mezclar en una carpeta partes con distinto porcentaje, y
    ``cendeu subir`` rechaza las muestras (en S3 va sólo la población completa).
    """
    if pct_muestra is not None and not 1 <= pct_muestra <= 100:
        raise ValueError(f"pct_muestra debe estar entre 1 y 100: {pct_muestra}")
    pct = None if pct_muestra in (None, 100) else pct_muestra
    fm = filtro_muestra(pct)
    cat = cat_mod.cargar(catalogo)
    if cat.errores:
        raise ValueError("Catálogo con errores:\n" + "\n".join(cat.errores))
    base, out = Path(dir_base), Path(dst) / f"periodo_obs={obs}"
    out.mkdir(parents=True, exist_ok=True)
    meta_muestra = out / ARCHIVO_MUESTRA
    previo = json.loads(meta_muestra.read_text())["pct_muestra"] if meta_muestra.exists() else (
        100 if any(out.glob("parte_*.parquet")) else None)
    if previo is not None and previo != (pct or 100):
        raise ValueError(f"{out} ya tiene partes con muestra del {previo}%; usar otra carpeta para el {pct or 100}%")
    meta_muestra.write_text(json.dumps({"pct_muestra": pct or 100,
                                        "criterio": f"hash(CUIT) % 100 < {pct}" if pct else "población completa"}))
    p_ini, p_fin = periodo_mas(obs, -(LARGO - 1)), periodo_mas(obs, desempeno)
    fin_mes = f"last_day(DATE '{obs // 100}-{obs % 100:02d}-01')"
    with conexion(**kw) as con:
        con.execute("SET preserve_insertion_order = false;")
        con.execute(MACROS)
        con.execute(f"CREATE VIEW base AS SELECT * FROM read_parquet('{_q(base / 'deudores')}/*/*/*.parquet', hive_partitioning = true)")
        ultimo = con.execute("SELECT max(periodo) FROM base").fetchone()[0]
        primero = con.execute("SELECT min(periodo) FROM base").fetchone()[0]
        mensual_dir = base / "mensual" / f"periodo={obs}"
        hay_mensual = mensual_dir.exists()
        cheques_dir = base / "cheques" / f"periodo={obs}"
        hay_cheques = cheques_dir.exists()
        capa1, capa2 = _variables_sql(cat, obs, hay_mensual, primero, hay_cheques)
        target = cat.meta["targets"][0]["nombre"]
        for k in buckets if buckets is not None else range(n_buckets):
            parte = out / f"parte_{k:02d}.parquet"
            if retomar and parte.exists():
                continue
            tmp_parte = out / f".parte_{k:02d}.tmp"
            t0 = time.time()
            con.execute(f"""
                CREATE OR REPLACE TEMP TABLE b AS
                SELECT cuit, entidad, situacion, monto, pj, reemplazado, tipo, recupero,
                       ((periodo // 100) * 12 + periodo % 100) - {mi(obs)} + 13 AS i
                FROM base WHERE bucket = {k} AND periodo BETWEEN {p_ini} AND {obs}{fm};
            """)
            con.execute(_series_sql(obs))
            con.execute(f"""
                CREATE OR REPLACE TEMP TABLE hist AS   -- último mes en situación 2+ en toda la historia hasta t
                SELECT cuit, max(periodo) AS ult_sit2p FROM base
                WHERE bucket = {k} AND periodo <= {obs} AND situacion >= 2{fm} GROUP BY cuit;
                CREATE OR REPLACE TEMP TABLE tgt AS
                SELECT cuit, max(situacion) AS sit_max_desemp FROM base
                WHERE bucket = {k} AND periodo > {obs} AND periodo <= {p_fin}{fm} GROUP BY cuit;
                CREATE OR REPLACE TEMP TABLE per AS
                SELECT cuit,
                  CASE WHEN tipo_persona(cuit) = 'HUMANA' AND date_diff('year', fecha_nac, {fin_mes}) BETWEEN 0 AND 110 THEN date_diff('year', fecha_nac, {fin_mes}) END AS EDAD,
                  CASE WHEN tipo_persona(cuit) = 'JURIDICA' THEN date_diff('year', fecha_nac, {fin_mes}) END AS ANTIG_EMPRESA,
                  CASE WHEN en_padron = 1 THEN (actividad > 0)::INT END AS ACTIVIDAD_FLG,
                  CASE WHEN actividad > 0 THEN lpad(actividad::VARCHAR, 6, '0')[1:2] END AS SECTOR,
                  lpad(provincia::VARCHAR, 2, '0') AS PROVINCIA,
                  coalesce((fecha_fallecimiento <= {fin_mes})::INT, 0) AS FALLECIDO,
                  coalesce(sexo, 'SIN_REGISTRAR') AS SEXO, desde_sit1
                FROM read_parquet('{_q(base / 'persona')}/*/*.parquet', hive_partitioning = true) WHERE bucket = {k}{fm};
            """)
            if hay_mensual:
                con.execute(f"CREATE OR REPLACE TEMP TABLE mm AS SELECT * FROM read_parquet('{_q(mensual_dir)}/*/*.parquet', hive_partitioning = true) WHERE bucket = {k}{fm}")
            else:
                con.execute("CREATE OR REPLACE TEMP TABLE mm AS SELECT NULL::BIGINT cuit WHERE false")
            if hay_cheques:   # CUITs sin cheques en la foto: sin fila (las variables los llevan a 0)
                con.execute(f"CREATE OR REPLACE TEMP TABLE chq AS SELECT * FROM read_parquet('{_q(cheques_dir)}/*/*.parquet', hive_partitioning = true) WHERE bucket = {k}{fm}")
            else:
                con.execute("CREATE OR REPLACE TEMP TABLE chq AS SELECT NULL::BIGINT cuit WHERE false")
            # Meses en situación 1 ininterrumpida (1DSF + 24DSF); se agrega a x antes de evaluar el catálogo
            con.execute(f"""
                CREATE OR REPLACE TEMP TABLE x2 AS
                SELECT x.*, coalesce(emp.ENT_EMPEORA_3, 0) AS ENT_EMPEORA_3,
                  CASE WHEN ult(s.SIT) >= 2 THEN 0
                       WHEN h.ult_sit2p IS NOT NULL THEN {mi(obs)} - ((h.ult_sit2p // 100) * 12 + h.ult_sit2p % 100)
                       WHEN per.desde_sit1 IS NOT NULL AND per.desde_sit1 <= {obs}
                            THEN {mi(obs)} - ((per.desde_sit1 // 100) * 12 + per.desde_sit1 % 100)
                       ELSE {mi(obs) - mi(primero) + 1} END AS MESES_SIT1_ININT
                FROM x JOIN s USING (cuit) LEFT JOIN emp USING (cuit) LEFT JOIN hist h USING (cuit) LEFT JOIN per USING (cuit);
            """)
            desemp_ok = p_fin <= ultimo
            con.execute(f"""
                COPY (
                  WITH v1 AS (
                    SELECT s.cuit AS CUIT, {obs} AS PERIODO_OBS,
                      (ult(s.SIT) <= 2 AND coalesce(per.FALLECIDO, 0) = 0 AND tipo_persona(s.cuit) <> 'OTRO')::INT AS EN_POBLACION,
                      {f"(coalesce(tgt.sit_max_desemp, 0) >= 3)::INT" if desemp_ok else "NULL::INT"} AS {target},
                      {capa1}
                    FROM s JOIN x2 x USING (cuit) LEFT JOIN per USING (cuit) LEFT JOIN mm USING (cuit) LEFT JOIN chq USING (cuit) LEFT JOIN tgt USING (cuit))
                  SELECT *{(", " + capa2) if capa2 else ""} FROM v1
                ) TO '{_q(tmp_parte)}' (FORMAT parquet, {ZSTD})
            """)
            tmp_parte.rename(parte)  # una parte sólo existe si se escribió completa
            n = con.execute("SELECT count(*) FROM s").fetchone()[0]
            print(f"obs {obs} bucket {k:02d}: {n:,} CUITs, {time.time() - t0:.0f}s", flush=True)
    return out

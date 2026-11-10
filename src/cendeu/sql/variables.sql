-- Variables de comportamiento CENDEU a nivel CUIT, a una fecha de observación.
--
-- Variables de entrada (SET VARIABLE antes de correr):
--   pq     : glob del Parquet largo  (…/deudores_24m/**/*.parquet)
--   ipc    : CSV periodo,ipc,imputado
--   obs    : período de observación AAAAMM (INTEGER)
--   h      : meses de historia hacia atrás incluyendo obs (12)
--   perf   : meses de desempeño hacia adelante para el target (0 = sin target)
--   dst    : archivo parquet de salida
--
-- mb = "months back": 0 = obs, 1 = mes anterior, …, h-1.
-- Montos en miles de pesos constantes del mes de observación (deflactados por IPC INDEC).

SET preserve_insertion_order = false;

CREATE OR REPLACE MACRO mi(p) AS (p // 100) * 12 + (p % 100);

CREATE OR REPLACE TEMP TABLE ipc AS
SELECT periodo::INTEGER AS periodo, ipc::DOUBLE AS ipc FROM read_csv(getvariable('ipc'), header = true);

-- 1) Nivel CUIT x entidad x mes (dentro de la historia), duplicados agregados, monto real
CREATE OR REPLACE TEMP TABLE e AS
SELECT d.cuit, d.entidad,
       mi(getvariable('obs')) - mi(d.periodo)                    AS mb,
       max(CASE WHEN d.situacion = 11 THEN 1 ELSE d.situacion END) AS sit,  -- 11 = cubierta 100% garantía "A" -> riesgo normal
       sum(d.monto)::DOUBLE * any_value(io.ipc) / any_value(ip.ipc) AS monto
FROM read_parquet(getvariable('pq'), hive_partitioning = true) d
JOIN ipc ip ON ip.periodo = d.periodo
JOIN ipc io ON io.periodo = getvariable('obs')
WHERE mi(getvariable('obs')) - mi(d.periodo) BETWEEN 0 AND getvariable('h') - 1
GROUP BY d.cuit, d.entidad, d.periodo;

-- 2) Nivel CUIT x mes
CREATE OR REPLACE TEMP TABLE m AS
SELECT cuit, mb,
       max(sit)                                   AS sit,
       sum(monto)                                 AS deuda,
       sum(monto) FILTER (sit >= 2 AND sit <> 11) AS deuda_2p,
       sum(sit * monto) / nullif(sum(monto), 0)   AS sit_pond,
       count(*)                                   AS n_ent,
       count(*) FILTER (entidad < 1000)           AS n_bancos
FROM e GROUP BY cuit, mb;

-- 3) Rachas y cambios de situación
CREATE OR REPLACE TEMP TABLE rachas AS
WITH o AS (
  SELECT cuit, mb, sit,
         (mb = row_number() OVER w - 1 AND sit = 1) AS ok,
         lag(mb)  OVER w AS mb_prev,
         lag(sit) OVER w AS sit_prev
  FROM m WINDOW w AS (PARTITION BY cuit ORDER BY mb)
), o2 AS (
  SELECT *, bool_and(ok) OVER (PARTITION BY cuit ORDER BY mb ROWS UNBOUNDED PRECEDING) AS ok_pref FROM o
)
SELECT cuit,
       count(*) FILTER (ok_pref)                                      AS racha_sit1_actual,
       count(*) FILTER (mb_prev = mb - 1 AND sit_prev <> sit)         AS cambios_sit_hist
FROM o2 GROUP BY cuit;

-- 4) Entidades: altas y bajas dentro de la ventana
CREATE OR REPLACE TEMP TABLE ents AS
WITH x AS (SELECT cuit, entidad, min(mb) AS mb_min, max(mb) AS mb_max FROM e GROUP BY ALL)
SELECT cuit,
       count(*) FILTER (mb_max < 3)                   AS ent_nuevas_3,
       count(*) FILTER (mb_max < 6)                   AS ent_nuevas_6,
       count(*) FILTER (mb_min BETWEEN 1 AND 5)       AS ent_canceladas_6,
       count(*) FILTER (mb_min BETWEEN 1 AND getvariable('h') - 1) AS ent_canceladas_hist,
       count(DISTINCT entidad)                        AS n_ent_hist
FROM x GROUP BY cuit;

-- 5) Variables finales
CREATE OR REPLACE TEMP TABLE vars AS
SELECT
  m.cuit,
  getvariable('obs')                                         AS periodo_obs,
  CASE WHEN m.cuit // 1000000000 IN (20, 23, 24, 27) THEN 'humana'
       WHEN m.cuit // 1000000000 IN (30, 33, 34)     THEN 'juridica' ELSE 'otro' END AS tipo_persona,
  -- situación
  max(sit) FILTER (mb = 0)                                   AS sit_actual,
  max(sit_pond) FILTER (mb = 0)                              AS sit_pond_actual,
  coalesce(max(sit) FILTER (mb < 3), 0)                      AS peor_sit_3,
  coalesce(max(sit) FILTER (mb < 6), 0)                      AS peor_sit_6,
  max(sit)                                                   AS peor_sit_hist,
  max(sit) FILTER (mb BETWEEN 1 AND 3)                       AS peor_sit_prev3,
  count(*) FILTER (mb < 6 AND sit BETWEEN 2 AND 5)           AS meses_sit2p_6,
  count(*) FILTER (sit BETWEEN 2 AND 5)                      AS meses_sit2p_hist,
  count(*) FILTER (mb < 6 AND sit BETWEEN 3 AND 5)           AS meses_sit3p_6,
  count(*) FILTER (sit BETWEEN 3 AND 5)                      AS meses_sit3p_hist,
  min(mb) FILTER (sit BETWEEN 2 AND 5)                       AS meses_desde_sit2p,
  min(mb) FILTER (sit BETWEEN 3 AND 5)                       AS meses_desde_sit3p,
  (count(*) FILTER (sit = 5) > 0)                            AS tuvo_sit5_hist,
  -- endeudamiento
  coalesce(sum(deuda) FILTER (mb = 0), 0)                    AS deuda_actual,
  coalesce(sum(deuda) FILTER (mb < 3), 0) / 3                AS deuda_prom_3,
  coalesce(sum(deuda) FILTER (mb < 6), 0) / 6                AS deuda_prom_6,
  max(deuda)                                                 AS deuda_max_hist,
  sum(deuda) FILTER (mb = 3)                                 AS deuda_mb3,
  sum(deuda) FILTER (mb = 6)                                 AS deuda_mb6,
  sum(deuda) FILTER (mb = getvariable('h') - 1)              AS deuda_mb_ini,
  coalesce(sum(deuda_2p) FILTER (mb = 0), 0)                 AS deuda_2p_actual,
  -- relación con el sistema
  coalesce(max(n_ent) FILTER (mb = 0), 0)                    AS n_ent_actual,
  max(n_ent)                                                 AS n_ent_max_hist,
  coalesce(max(n_bancos) FILTER (mb = 0), 0)                 AS n_bancos_actual,
  max(mb) + 1                                                AS antiguedad_meses,
  count(*)                                                   AS meses_con_deuda_hist
FROM m GROUP BY m.cuit;

-- 6) Ensamble + derivadas
CREATE OR REPLACE TEMP TABLE final AS
SELECT v.*,
  deuda_actual / nullif(deuda_mb3, 0) - 1                    AS crec_deuda_3,
  deuda_actual / nullif(deuda_mb6, 0) - 1                    AS crec_deuda_6,
  deuda_actual / nullif(deuda_mb_ini, 0) - 1                 AS crec_deuda_hist,
  deuda_2p_actual / nullif(deuda_actual, 0)                  AS prop_deuda_sit2p,
  deuda_actual / nullif(deuda_max_hist, 0)                   AS ratio_deuda_vs_max,
  n_ent_actual - n_bancos_actual                             AS n_no_bancos_actual,
  (n_ent_actual > 0 AND n_bancos_actual = 0)                 AS solo_no_bancario,
  (peor_sit_hist >= 3 AND sit_actual = 1)                    AS recuperado,
  (sit_actual > peor_sit_prev3)                              AS empeoro_3,  -- NULL si no tenía deuda en mb 1..3
  r.racha_sit1_actual, r.cambios_sit_hist,
  x.ent_nuevas_3, x.ent_nuevas_6, x.ent_canceladas_6, x.ent_canceladas_hist, x.n_ent_hist
FROM vars v
JOIN rachas r USING (cuit)
JOIN ents   x USING (cuit);

-- 7) Target (solo si perf > 0): peor situación en los perf meses posteriores a obs
CREATE OR REPLACE TEMP TABLE fut AS
SELECT cuit,
       max(CASE WHEN situacion = 11 THEN 1 ELSE situacion END) AS peor_sit_fut,
       count(DISTINCT periodo)                                AS meses_obs_fut
FROM read_parquet(getvariable('pq'), hive_partitioning = true)
WHERE getvariable('perf') > 0
  AND mi(periodo) - mi(getvariable('obs')) BETWEEN 1 AND getvariable('perf')
GROUP BY cuit;

COPY (
  SELECT f.*,
         CASE WHEN getvariable('perf') > 0 THEN coalesce(u.peor_sit_fut, 0) END AS peor_sit_fut,
         CASE WHEN getvariable('perf') > 0 THEN coalesce(u.peor_sit_fut, 0) >= 3 END AS target_sit3p
  FROM final f LEFT JOIN fut u USING (cuit)
) TO (getvariable('dst')) (FORMAT parquet, COMPRESSION zstd);

-- IV y Gini univariados de las variables CENDEU contra target_sit3p.
-- Variables: src (parquet de variables con target), dst_resumen, dst_bins (CSV).
-- Población: personas humanas en situación 1 a la fecha de observación.
-- Binning: variables con <=20 valores distintos -> un bin por valor; resto -> deciles.
-- NULL es un bin propio. Gini = 2*AUC-1 del score WoE (bins ordenados por tasa de malos),
-- o sea el Gini "de scorecard": no penaliza relaciones no monótonas.

SET preserve_insertion_order = false;

CREATE OR REPLACE TEMP TABLE pop AS
SELECT target_sit3p::INT AS y,
       COLUMNS(* EXCLUDE (cuit, periodo_obs, tipo_persona, sit_actual, sit_pond_actual,
                          peor_sit_fut, target_sit3p, deuda_mb3, deuda_mb6, deuda_mb_ini))::DOUBLE
FROM read_parquet(getvariable('src'))
WHERE tipo_persona = 'humana' AND sit_actual = 1;

CREATE OR REPLACE TEMP TABLE larga AS
SELECT * FROM pop UNPIVOT INCLUDE NULLS (val FOR var IN (COLUMNS(* EXCLUDE (y))));

CREATE OR REPLACE TEMP TABLE cortes AS
SELECT var,
       approx_count_distinct(val) AS n_dist,
       list_distinct(list_sort(approx_quantile(val, [0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9]))) AS cuts
FROM larga GROUP BY var;

CREATE OR REPLACE TEMP TABLE bins AS
SELECT l.var,
       CASE WHEN l.val IS NULL THEN 'NULL'
            WHEN c.n_dist <= 20 THEN CAST(l.val AS VARCHAR)
            ELSE 'd' || lpad(CAST(len(list_filter(c.cuts, lambda x: x < l.val)) AS VARCHAR), 2, '0') END AS bin,
       min(l.val) AS desde, max(l.val) AS hasta,
       count(*) AS n, sum(l.y) AS malos
FROM larga l JOIN cortes c USING (var)
GROUP BY ALL;

CREATE OR REPLACE TEMP TABLE bins2 AS
WITH t AS (SELECT var, sum(malos) B, sum(n - malos) G FROM bins GROUP BY var)
SELECT b.*, n - malos AS buenos, malos / n AS tasa_malos,
       ln(((n - malos) + 0.5) / (t.G + 0.5) / ((malos + 0.5) / (t.B + 0.5))) AS woe,
       ((n - malos) / t.G - malos / t.B) *
       ln(((n - malos) + 0.5) / (t.G + 0.5) / ((malos + 0.5) / (t.B + 0.5))) AS iv_bin,
       t.B, t.G
FROM bins b JOIN t USING (var);

CREATE OR REPLACE TEMP TABLE resumen AS
WITH o AS (
  SELECT *, coalesce(sum(buenos) OVER (PARTITION BY var ORDER BY tasa_malos
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS g_antes
  FROM bins2
)
SELECT var,
       round(sum(iv_bin), 4)                                       AS iv,
       round(2 * sum(malos * (g_antes + 0.5 * buenos)) / (any_value(B) * any_value(G)) - 1, 4) AS gini,
       count(*)                                                    AS n_bins,
       round(100.0 * sum(n) FILTER (bin = 'NULL') / sum(n), 1)     AS pct_null,
       CASE WHEN sum(iv_bin) < 0.02 THEN 'inútil' WHEN sum(iv_bin) < 0.1 THEN 'débil'
            WHEN sum(iv_bin) < 0.3 THEN 'media' WHEN sum(iv_bin) < 0.5 THEN 'fuerte'
            ELSE 'muy fuerte (revisar)' END                          AS poder
FROM o GROUP BY var;

COPY (SELECT * FROM resumen ORDER BY iv DESC) TO (getvariable('dst_resumen')) (HEADER);
COPY (SELECT var, bin, desde, hasta, n, malos, round(tasa_malos, 4) tasa_malos, round(woe, 4) woe, round(iv_bin, 4) iv_bin
      FROM bins2 ORDER BY var, desde NULLS FIRST) TO (getvariable('dst_bins')) (HEADER);
SELECT * FROM resumen ORDER BY iv DESC;

-- Convierte el bianual de la Central de Deudores BCRA (24DSFAAAAMM, ancho fijo 378 chars)
-- a Parquet en formato largo: una fila por CUIT x entidad x período con deuda (situación > 0).
--
-- Layout validado contra 24DSF_control.txt (corte 202609):
--   1-5   entidad (alineado a izquierda)   6-7 tipo_id   8-18 CUIT
--   19 + 15*(k-1): bloque mes k  -> situación(2) + monto miles $ con coma(12) + flag(1)
--   k=1 es el mes de corte (más reciente), k=24 es 23 meses antes.
--
-- Uso: cendeu convertir <24DSF.txt> <salida> --corte AAAAMM   (ver cendeu/pipeline.py)

SET preserve_insertion_order = false;

COPY (
  WITH raw AS (
    SELECT l
    FROM read_csv(getvariable('src'),
                  columns = {'l': 'VARCHAR'}, delim = '|', quote = '', escape = '',
                  header = false, auto_detect = false)
  ),
  largo AS (
    SELECT
      TRY_CAST(trim(substr(l, 1, 5)) AS INTEGER)                              AS entidad,
      TRY_CAST(substr(l, 6, 2) AS UTINYINT)                                   AS tipo_id,
      TRY_CAST(substr(l, 8, 11) AS BIGINT)                                    AS cuit,
      CAST(strftime(getvariable('corte') - to_months(k - 1), '%Y%m') AS INTEGER) AS periodo,
      TRY_CAST(trim(substr(l, 19 + (k - 1) * 15, 2)) AS UTINYINT)             AS situacion,
      TRY_CAST(replace(trim(substr(l, 21 + (k - 1) * 15, 12)), ',', '.') AS DECIMAL(14,1)) AS monto,
      substr(l, 33 + (k - 1) * 15, 1)                                         AS flag
    FROM raw, range(1, 25) t(k)
  )
  SELECT * FROM largo WHERE situacion > 0
) TO (getvariable('dst'))
  (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (periodo), OVERWRITE_OR_IGNORE true);
